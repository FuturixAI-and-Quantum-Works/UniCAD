import json
import os
import subprocess
import typing
import sys
from itertools import combinations
from pathlib import Path

import pymupdf
import pytest

from unicad.catalog.store import load_catalog
from unicad.domain.catalog import ProtectionSpec
from unicad.domain.network import SIGNAL_PORTS, CableBand, ElectricalNetwork, NodeKind
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sld import SldOptions, annotate, layout_sld, plan_pages, render_sld
from unicad.drafting.sld.model import Box, WireKind
from unicad.drafting.sld.outline import inside as in_outline
from unicad.drafting.sld.sheet import dxf_text
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parent.parent
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
FIXTURES = ("rooftop_500kwp", "utility_50mwac")
EPS = 1e-6


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def profile():
    return CompanyProfile.load(ROOT / "profiles" / "default.json")


@pytest.fixture(scope="module")
def projects(catalog):
    return {f: parse_project((ROOT / "fixtures" / "projects" / f"{f}.json").read_text(encoding="utf-8"), catalog) for f in FIXTURES}


@pytest.fixture(scope="module")
def layouts(projects, catalog):
    out = {}
    for f, p in projects.items():
        out[f] = layout_sld(p.network, catalog)
        out[f + ":compact"] = layout_sld(p.network, catalog, SldOptions().compact())
    return out


def _on_bus(layout, node_id, p):
    return any(b.node_id == node_id and abs(b.y - p[1]) < EPS and b.x0 - EPS <= p[0] <= b.x1 + EPS for b in layout.busbars)


def test_wires_are_orthogonal_and_end_exactly_on_pins(layouts):
    for name, lay in layouts.items():
        pins = {(p.owner, p.pin): p.at for s in lay.symbols for p in s.pins}
        assert lay.wires, name
        for w in lay.wires:
            assert len(w.points) >= 2
            for a, b in zip(w.points, w.points[1:]):
                assert abs(a[0] - b[0]) < EPS or abs(a[1] - b[1]) < EPS, (name, w.edge_id, a, b)
            assert w.end is not None and w.end.at == w.points[-1]
            assert pins.get((w.end.owner, w.end.pin)) == w.end.at or _on_bus(lay, w.end.owner, w.end.at), (name, w.edge_id)
            if w.kind is WireKind.EARTH:
                continue
            assert w.start is not None and w.start.at == w.points[0]
            assert pins.get((w.start.owner, w.start.pin)) == w.start.at or _on_bus(lay, w.start.owner, w.start.at), (name, w.edge_id)


def test_every_edge_of_the_network_is_drawn(layouts, projects):
    for f, p in projects.items():
        drawn = {w.edge_id for w in layouts[f].wires}
        wanted = {e.id for e in p.network.edges if e.service.value != "earth"}
        assert wanted <= drawn, wanted - drawn


def _boxes(lay):
    items = [(f"sym:{s.id}", s.body.grow(-0.05)) for s in lay.symbols]
    items += [(f"txt:{t.text}@{t.at}", t.box) for t in lay.texts()]
    items += [(f"bus:{b.node_id}", Box(b.x0, b.y - 0.05, b.x1, b.y + 0.05)) for b in lay.busbars]
    return items


def test_no_symbol_or_text_overlaps(layouts):
    for name, lay in layouts.items():
        clashes = [(a, b) for (a, ba), (b, bb) in combinations(_boxes(lay), 2) if ba.overlaps(bb)]
        assert not clashes, (name, clashes[:5])


def _crosses(box: Box, a, b) -> bool:
    if abs(a[0] - b[0]) < EPS:
        return box.x0 + EPS < a[0] < box.x1 - EPS and min(a[1], b[1]) < box.y1 - EPS and box.y0 + EPS < max(a[1], b[1])
    return box.y0 + EPS < a[1] < box.y1 - EPS and min(a[0], b[0]) < box.x1 - EPS and box.x0 + EPS < max(a[0], b[0])


def test_no_wire_runs_through_text(layouts):
    for name, lay in layouts.items():
        texts = lay.texts()
        hits = [(w.edge_id, t.text) for w in lay.wires for a, b in zip(w.points, w.points[1:]) for t in texts if _crosses(t.box, a, b)]
        assert not hits, (name, hits[:5])


def test_every_power_node_is_drawn_exactly_once_and_groups_are_compressed(layouts, projects):
    for f, p in projects.items():
        lay = layouts[f]
        ids = [n for n, _ in lay.represented]
        assert len(ids) == len(set(ids))
        power = {n.id for n in p.network.nodes if any(port not in SIGNAL_PORTS for port in n.port_names) and n.kind is not NodeKind.EARTH_ELECTRODE}
        control = {n.id for n in p.network.nodes if n.ports is not None and all(port in SIGNAL_PORTS for port in n.ports) or n.kind is NodeKind.RELAY}
        assert set(ids) == power | control
        assert sum(1 for s in lay.symbols if s.role == "node") + len(lay.busbars) == len(ids)
        assert len(ids) < len(p.network.expand().nodes)
    util = layouts["utility_50mwac"]
    titles = {f.group_id: " ".join(lb.text for lb in f.labels) for f in util.frames}
    assert set(titles) == {"block", "inv_sec", "scb_sec", "mvsb"}
    mvsb = next(f for f in util.frames if f.group_id == "mvsb")
    assert mvsb.outline and all(len(loop) >= 4 for loop in mvsb.outline)
    assert all(a[0] == b[0] or a[1] == b[1] for loop in mvsb.outline for a, b in zip(loop, loop[1:] + loop[:1]))
    assert "33 kV MAIN HT SWITCHBOARD PANEL" in titles["mvsb"]
    assert "BLOCK-01..BLOCK-06  (x 6)" in titles["block"]
    assert "(x 15)" in titles["scb_sec"]
    roof = layouts["rooftop_500kwp"]
    assert [f.multiplicity for f in roof.frames] == [5]


def test_poi_is_on_top_and_generation_at_the_bottom(layouts, projects):
    for f, p in projects.items():
        lay = layouts[f]
        y = {s.node_id: s.body.y1 for s in lay.symbols if s.role == "node"}
        y |= {b.node_id: b.y for b in lay.busbars}
        net = p.network
        poi = net.by_kind(NodeKind.POI_GRID)[0].id
        assert y[poi] == max(y.values())
        for s in net.by_kind(NodeKind.PV_STRING):
            path = net.path_to_poi(s.id)
            heights = [y[n] for n in path]
            assert heights == sorted(heights), (f, path, heights)


def test_frames_enclose_their_members_and_nothing_else(layouts, projects):
    for f, p in projects.items():
        lay = layouts[f]
        net = p.network
        for fr in lay.frames:
            def inside(gid):
                while gid is not None:
                    if gid == fr.group_id:
                        return True
                    gid = net.group_by_id[gid].within
                return False

            for s in lay.symbols:
                if s.node_id is None:
                    continue
                node = net.node(s.node_id)
                member = inside(node.housed_in or node.group)
                b = s.body
                if fr.outline:
                    within = all(in_outline(fr.outline, p) for p in ((b.x0, b.y0), (b.x1, b.y0), (b.x1, b.y1), (b.x0, b.y1)))
                else:
                    within = fr.box.x0 <= b.x0 and b.x1 <= fr.box.x1 and fr.box.y0 <= b.y0 and b.y1 <= fr.box.y1
                assert member == within, (fr.group_id, s.node_id)


def test_annotations_come_from_the_catalog_and_project(layouts, projects, catalog):
    roof = layouts["rooftop_500kwp"]
    vals = {s.node_id: dict(s.values) for s in roof.symbols if s.node_id}
    inv = catalog.get(projects["rooftop_500kwp"].network.node("inv").spec)
    assert vals["inv"]["RATING"] == f"{inv.ac_rated_kva:g} kVA, {inv.ac_voltage:g} V AC, {inv.phases} PH"
    assert vals["inv"]["TAG"] == "INV-01"
    net = projects["rooftop_500kwp"].network
    mccb = catalog.get(net.node("inv_mccb").spec)
    assert vals["inv_mccb"]["RATING"] == f"{mccb.rated_current:g} A, {mccb.poles}P, {mccb.breaking_capacity_ka:g} kA, {mccb.rated_voltage:g} V"
    module = catalog.get(net.node("str").spec)
    n = net.node("str").modules_in_series
    assert vals["str"]["RATING"] == f"{n} x {module.pmax:g} Wp = {n * module.pmax / 1000:g} kWp"
    assert vals["str"]["COUNT"] == "x 10 NOS"
    callouts = {c.edge_id: [lb.text for lb in c.labels] for c in roof.callouts}
    assert callouts["e_acb_ct"][0] == "3R x 3 x 1C x 400 sq.mm Al XLPE ARM"
    assert "ON TRAY" in callouts["e_acb_ct"][1]
    util = layouts["utility_50mwac"]
    uvals = {s.node_id: dict(s.values) for s in util.symbols if s.node_id}
    assert uvals["aux_tr"]["VECTOR"] == "Dyn11"
    assert uvals["idt"]["VECTOR"] == "YNd11d11"
    assert uvals["blk_aux"]["VECTOR"] == "Dyn11"
    assert {lb.text for b in util.busbars for lb in b.labels} >= {"33 kV BUS"}


def test_sheet_carries_legend_of_used_symbols_and_system_parameters(projects, catalog, profile):
    doc, res = render_sld(projects["rooftop_500kwp"], "sld", profile, catalog)
    used = set(res.layout.legend_keys())
    assert {slot.key for slot in res.sheet.legend_slots} <= used
    texts = {e.dxf.text for e in doc.modelspace().query("TEXT")}
    assert "499.50 kWp" in texts
    assert "500.00 kVA" in texts
    assert "900 x 555 Wp" in texts


@pytest.mark.parametrize("fixture, paper", [("rooftop_500kwp", "A2"), ("rooftop_500kwp", "A1"), ("utility_50mwac", "A0"), ("utility_50mwac", "A1")])
def test_sheet_is_readable_and_inside_the_drawing_area(projects, catalog, profile, fixture, paper):
    p = projects[fixture]
    p = p.model_copy(update={"sheets": tuple(s.model_copy(update={"paper": paper}) if s.id == "sld" else s for s in p.sheets)})
    _, res = render_sld(p, "sld", profile, catalog)
    assert res.placement.readable, res.placement.scale
    assert len(res.pages) == 1
    area = res.sheet.drawing_area
    ext = res.layout.extent
    lo = res.placement.to_sheet((ext.x0, ext.y0))
    hi = res.placement.to_sheet((ext.x1, ext.y1))
    assert area.x0 - EPS <= lo[0] and hi[0] <= area.x1 + EPS and area.y0 - EPS <= lo[1] and hi[1] <= area.y1 + EPS


def test_dxf_is_byte_identical_across_runs(projects, catalog, profile):
    for f in FIXTURES:
        a = dxf_text(render_sld(projects[f], "sld", profile, catalog)[0])
        b = dxf_text(render_sld(projects[f], "sld", profile, catalog)[0])
        assert a == b


def test_split_plan_covers_every_node_once_when_the_sheet_is_too_small(projects, catalog):
    p = projects["utility_50mwac"]
    a4 = metrics_for(PaperSize.A4).drawing_area
    pages = plan_pages(p.network, catalog, a4.width / 2, a4.height / 2)
    assert len(pages) > 1
    seen = [n for page in pages for n in page.node_ids]
    full = layout_sld(p.network, catalog)
    assert sorted(seen) == sorted(n for n, _ in full.represented)
    cut = {e for page in pages for e in page.cut_edges}
    assert cut
    for e in cut:
        edge = p.network.edge_by_id[e]
        homes = {i for i, page in enumerate(pages) for n in page.node_ids if n in (edge.src_node, edge.dst_node)}
        assert len(homes) == 2


def test_split_plan_is_one_page_when_it_fits(projects, catalog):
    a0 = metrics_for(PaperSize.A0).drawing_area
    pages = plan_pages(projects["rooftop_500kwp"].network, catalog, a0.width, a0.height)
    assert len(pages) == 1 and not pages[0].cut_edges


@pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")
def test_librecad_exports_the_sld_to_pdf(projects, catalog, profile, tmp_path):
    from unicad.drafting.sld import save_deterministic

    doc, _ = render_sld(projects["rooftop_500kwp"], "sld", profile, catalog)
    dxf, pdf = tmp_path / "sld.dxf", tmp_path / "sld.pdf"
    save_deterministic(doc, dxf)
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    done = subprocess.run([str(LIBRECAD), "dxf2pdf", "-s", "1", "-c", "-p", "420x297", "-o", str(pdf), str(dxf)],
                          env=env, capture_output=True, timeout=180)
    assert done.returncode == 0, done.stderr.decode()
    page = pymupdf.open(pdf)[0]
    assert page.rect.width == pytest.approx(420 / 25.4 * 72, abs=2)
    assert len(page.get_drawings()) > 500


def test_scoped_sheet_marks_every_cut_edge_with_an_off_sheet_marker(projects, catalog, profile):
    p = projects["utility_50mwac"]
    _, res = render_sld(p, "sld_blk", profile, catalog)
    lay = res.layout
    drawn = {n for n, _ in lay.represented}
    assert {"idt", "inv", "scb", "str", "icog_la"} <= drawn
    assert not drawn & {"mv_bus", "poi", "icog_vcb", "blk_aux_vcb", "blk_aux"}
    marked = {o.edge_id for o in lay.offsheets}
    assert marked == {"e_idt_vcb", "e_idt_auxvcb"}
    markers = {s.id: s for s in lay.symbols if s.role == "offsheet"}
    assert {dict(s.values)["DESC"] for s in markers.values()} == {"TO ICOG-VCB-01", "TO ICOG-AUX-VCB-01"}
    ends = {w.edge_id: (w.start.owner, w.end.owner) for w in lay.wires if w.kind is not WireKind.EARTH}
    assert ends["e_idt_vcb"] == ("idt", "off.e_idt_vcb")
    assert "sw.vcb" not in lay.legend_keys() and "ann.offsheet" not in lay.legend_keys()


def test_dxf_bytes_do_not_depend_on_the_hash_seed(tmp_path):
    script = (
        "import hashlib, sys; from pathlib import Path\n"
        "from unicad.catalog.store import load_catalog\n"
        "from unicad.drafting.sheets.model import CompanyProfile\n"
        "from unicad.drafting.sld.sheet import dxf_text, render_sld\n"
        "from unicad.store import parse_project\n"
        f"root = Path({str(ROOT)!r}); cat = load_catalog()\n"
        "p = parse_project((root / 'fixtures/projects/utility_50mwac.json').read_text(), cat)\n"
        "doc, _ = render_sld(p, 'sld', CompanyProfile.load(root / 'profiles/default.json'), cat)\n"
        "print(hashlib.sha256(dxf_text(doc).encode()).hexdigest())\n"
    )
    digests = set()
    for seed in ("1", "2"):
        done = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr
        digests.add(done.stdout.strip())
    assert len(digests) == 1


def test_busbar_carries_its_rating_from_node_attributes(catalog):
    import json

    data = json.loads((ROOT / "fixtures" / "projects" / "utility_50mwac.json").read_text(encoding="utf-8"))
    bus = next(n for n in data["network"]["nodes"] if n["id"] == "mv_bus")
    bus["attributes"] = {"rated_current_a": 1250, "short_time_ka": 25, "short_time_s": 1, "material": "Al"}
    p = parse_project(json.dumps(data), catalog)
    lay = layout_sld(p.network, catalog)
    labels = [lb.text for b in lay.busbars if b.node_id == "mv_bus" for lb in b.labels]
    assert labels[1] == "33 kV, 1250 A, 25 kA 1 s, AL BUSBAR"


def test_inverter_duty_transformer_uses_one_lv_pin_per_winding(layouts):
    util = layouts["utility_50mwac"]
    idt = next(s for s in util.symbols if s.node_id == "idt")
    assert idt.key == "tx.idt_2lv"
    assert {p.pin for p in idt.pins} == {"hv", "lv1", "lv2"}
    ends = {(w.end.owner, w.end.pin) for w in util.wires if w.end} | {(w.start.owner, w.start.pin) for w in util.wires if w.start}
    assert ("idt", "lv1") in ends and ("idt", "hv") in ends
    spare = [st for st in util.stubs if st.node_id == "idt"]
    assert [st.pin for st in spare] == ["lv2"] and spare[0].labels[0].text == "LV2 TO PCU-01-2"


def test_turned_symbols_keep_tag_on_top_and_text_on_the_right(layouts):
    for name, lay in layouts.items():
        for s in lay.symbols:
            if s.role != "node" or round(s.rotation) % 360 != 180 or len(s.labels) < 2:
                continue
            tag = dict(s.values)["TAG"]
            assert s.labels[0].text.startswith(tag), (name, s.id)
            assert s.labels[0].at[1] > s.labels[-1].at[1], (name, s.id)
            assert all(lb.align == "left" and lb.box.x0 >= s.body.x1 for lb in s.labels), (name, s.id)


def test_notes_are_engineer_worded(projects, catalog, profile):
    doc, _ = render_sld(projects["rooftop_500kwp"], "sld", profile, catalog)
    joined = " ".join(e.dxf.text for e in doc.modelspace().query("TEXT"))
    assert "_" not in joined
    assert "MINIMUM AMBIENT TEMPERATURE 10 %%dC" in joined
    assert "DC VOLTAGE DROP LIMIT 2 %" in joined
    roof = layout_sld(projects["rooftop_500kwp"].network, catalog)
    callouts = {c.edge_id: " ".join(lb.text for lb in c.labels) for c in roof.callouts}
    assert "SEE NOTE 3" in callouts["e_str"] and roof.notes[0].startswith("DERATING 1.0")


def _utility_with(catalog, edit) -> object:
    data = json.loads((ROOT / "fixtures" / "projects" / "utility_50mwac.json").read_text(encoding="utf-8"))
    edit(data["network"])
    return parse_project(json.dumps(data), catalog)


def _values(lay, node_id) -> dict[str, str]:
    return next(dict(s.values) for s in lay.symbols if s.node_id == node_id)


def test_every_protection_device_has_a_text_formatter():
    from unicad.drafting.sld.annotate import PROTECTION_TEXT

    devices = set(typing.get_args(ProtectionSpec.model_fields["device"].annotation))
    assert set(PROTECTION_TEXT) == devices


def test_instrument_transformers_arresters_and_idt_show_their_spec_ratings(layouts):
    util = layouts["utility_50mwac"]
    assert _values(util, "mv_pt")["RATING"] == "33 kV/\u221a3 / 110 V/\u221a3 - 110 V/\u221a3, CL 0.5 / 3P, 100 VA"
    assert _values(util, "out_la")["RATING"] == "30 kV, 20 kA, WITH SURGE COUNTER"
    assert next(s.key for s in util.symbols if s.node_id == "out_la") == "prot.la_counter"
    assert _values(util, "icog_la")["RATING"] == "30 kV, 10 kA"
    idt = _values(util, "idt")
    assert idt["RATING"] == "8.8 MVA, 33/0.66-0.66 kV"
    assert idt["VECTOR"] == "YNd11d11"
    assert idt["DESC"] == "Z = 7 % ON 4.4 MVA, OCTC +/-10 % IN 2.5 % STEPS"


def test_ct_text_follows_ratio_class_and_burden(catalog):
    base = catalog.get("generic-ct-33kv-200-100-50-1-1a")
    ct = base.model_copy(update={"ratio": "250/1-1 A", "accuracy_class": "5P20 (15 VA, protection) + 0.2S (15 VA, metering)", "burden_va": None})
    assert annotate._instrument_rating(ct) == "250/1-1 A, CL 5P20 / 0.2S, 15 VA"
    assert annotate._instrument_rating(base) == "200-100-50/1-1 A, CL 0.5S / 5P20, 15/30 VA"


def test_pt_secondary_wire_stays_clear_of_group_outlines(layouts):
    from unicad.drafting.sld.layout import _edges_of, _seg_touches

    util = layouts["utility_50mwac"]
    wire = next(w for w in util.wires if w.edge_id == "c_pt_abt")
    edges = [e for f in util.frames for e in _edges_of(f)]
    inner = list(zip(wire.points[1:-2], wire.points[2:-1]))
    assert inner
    for p, q in inner:
        seg = Box(min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))
        assert not [e for e in edges if _seg_touches(seg, e)], (p, q)


@pytest.mark.parametrize("ansi, key, circles", [("50/51, 50N/51N", "prot.relay_group", ["50", "51", "50N", "51N"]), ("87T", "prot.relay", ["87T"])])
def test_relay_circles_carry_the_ansi_numbers_from_attributes(catalog, ansi, key, circles):
    def edit(net):
        relay = next(n for n in net["nodes"] if n["id"] == "out_relay")
        relay["attributes"] = {"ansi": ansi}

    lay = layout_sld(_utility_with(catalog, edit).network, catalog)
    sym = next(s for s in lay.symbols if s.node_id == "out_relay")
    assert sym.key == key
    values = dict(sym.values)
    if key == "prot.relay":
        assert values["ANSI"] == circles[0]
    else:
        assert [values[f"ANSI{i + 1}"] for i in range(len(circles))] == circles
        assert values["ANSI5"] == ""


def _pin(lay, owner, pin):
    return next(p.at for s in lay.symbols for p in s.pins if (p.owner, p.pin) == (owner, pin))


def test_hv_side_branch_taps_the_conductor_above_the_transformer(layouts):
    for name in ("utility_50mwac", "utility_50mwac:compact"):
        lay = layouts[name]
        sym = {s.node_id: s for s in lay.symbols if s.node_id}
        idt, aux, inv = sym["idt"], sym["blk_aux_vcb"], sym["inv"]
        assert idt.body.y0 < aux.body.y1 and aux.body.y0 < idt.body.y1, name
        assert aux.body.y0 > inv.body.y1, name
        hv = _pin(lay, "idt", "hv")
        wire = next(w for w in lay.wires if w.edge_id == "e_idt_auxvcb")
        tap = [p for p in wire.points if abs(p[0] - hv[0]) < EPS and p[1] > hv[1] + EPS]
        assert tap and tap[0] in lay.junctions, (name, wire.points)
        feed = next(w for w in lay.wires if w.edge_id == "e_idt_vcb")
        assert any(abs(a[0] - hv[0]) < EPS and min(a[1], b[1]) - EPS <= tap[0][1] <= max(a[1], b[1]) + EPS
                   for a, b in zip(feed.points, feed.points[1:]) if abs(a[0] - b[0]) < EPS), name


def test_group_titles_do_not_repeat_themselves(layouts):
    for name, lay in layouts.items():
        for f in lay.frames:
            texts = [lb.text for lb in f.labels]
            for a, b in combinations(texts, 2):
                assert not (a in b or b in a), (name, f.group_id, texts)
    mvsb = next(f for f in layouts["utility_50mwac"].frames if f.group_id == "mvsb")
    assert [lb.text for lb in mvsb.labels] == ["33 kV MAIN HT SWITCHBOARD PANEL"]


def _segments(f):
    from unicad.drafting.sld.layout import _edges_of

    return _edges_of(f)


def test_group_outlines_are_one_clean_loop_clear_of_text_and_other_frames(layouts):
    for name, lay in layouts.items():
        texts = lay.texts()
        for f in lay.frames:
            loops = f.outline or ()
            assert len(loops) <= 1, (name, f.group_id)
            for e in _segments(f):
                a, b = (e.x0, e.y0), (e.x1, e.y1)
                hits = [t.text for t in texts if _crosses(t.box, a, b)]
                assert not hits, (name, f.group_id, hits)
            if loops:
                assert len(loops[0]) <= 14, (name, f.group_id, loops[0])  # the switchboard frame steps round the feeder cable callouts below its feeder panels
        for f, g in combinations(lay.frames, 2):
            for e in _segments(f):
                for d in _segments(g):
                    crossing = (e.x0 == e.x1) != (d.x0 == d.x1) and (
                        (e.x0 == e.x1 and d.x0 + EPS < e.x0 < d.x1 - EPS and e.y0 + EPS < d.y0 < e.y1 - EPS)
                        or (d.x0 == d.x1 and e.x0 + EPS < d.x0 < e.x1 - EPS and d.y0 + EPS < e.y0 < d.y1 - EPS)
                    )
                    assert not crossing, (name, f.group_id, g.group_id, e, d)


@pytest.fixture(scope="module")
def central_two_scb_types(catalog):
    """utility_central on the Rajasthan layout: each inverter collects two SCB types, and its DC SPD hangs off both SCB outputs."""
    from test_design_p5 import GROUND_MODULE, ground_build
    from unicad.arrays import PlacementSpec, place_array
    from unicad.ingest.site_import import import_site

    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry
    spec = PlacementSpec(module=GROUND_MODULE, table_rows=2, table_cols=27, tilt_deg=25, n_blocks=2, modules_per_string=27, road_every_rows=12, target_dc_kwp=2 * 144 * 27 * 0.58 * 1.1)
    return ground_build((site, place_array(site, spec, catalog.get(GROUND_MODULE)))).project


def test_utility_central_with_two_scb_types_per_inverter_lays_out(central_two_scb_types, catalog):
    """close-e2e-1 B2: KeyError 3 in _subtree (MCP text '3') for an SC 4000 UP design whose DC SPD branches off an SCB output."""
    net = central_two_scb_types.network
    spd_edges = {e.id for e in net.edges if e.id.startswith("e_scb_spd_")}
    assert {"scb_sec_b01aa", "scb_sec_b01ab"} <= {g.id for g in net.groups} and len(spd_edges) >= 2
    lay = layout_sld(net, catalog)
    assert {e.id for e in net.edges if e.service.value != "earth"} <= {w.edge_id for w in lay.wires}
    assert {"inv_dcspd_b01a", "inv_dcspd_b02a"} <= {n for n, _ in lay.represented}


def test_a_node_left_without_a_catalog_spec_is_drawn_with_its_default_symbol_and_an_open_rating(catalog):
    """close-e2e-1 B3: the block ACB of a 20 x SG350HX block (~6350 A, above every catalog ACB) had no spec, so no symbol, and the SLD failed.
    Block buses are now capped so the ACB gets a spec; the test strips it to keep the spec-less path covered."""
    from test_design import blank
    from unicad.design import autobuild

    r = autobuild(blank("utility_50mwac"), catalog, {"template": "utility_string", "ac_kw": 7040, "module": "waaree-bin-21-635", "inverter": "sungrow-sg350hx",
                                                     "modules_per_string": 28, "inverters_per_block": 20})  # fmt: skip
    net = r.project.network
    acdb = net.by_kind(NodeKind.ACDB)[0]
    acb = next(n for n in net.by_kind(NodeKind.BREAKER) if n.group == acdb.group and n.tag.startswith("ACB-"))
    spd = next(n for n in net.by_kind(NodeKind.AC_SPD) if n.group == acdb.group)
    assert spd.spec == "dehn-dg-m-we-600" and not any(f"{spd.tag} (ac_spd)" in b for b in r.blockers)
    net = ElectricalNetwork(groups=net.groups, nodes=tuple(n.model_copy(update={"spec": None}) if n.id in (acb.id, spd.id) else n for n in net.nodes), edges=net.edges)
    lay = layout_sld(net, catalog)
    sym = {s.node_id: s for s in lay.symbols if s.role == "node"}
    assert sym[acb.id].key == "sw.acb" and sym[spd.id].key == "prot.spd_ac"
    for nid in (acb.id, spd.id):
        assert dict(sym[nid].values)["RATING"].endswith("RATING TO BE SPECIFIED"), nid
        assert any(w.startswith(f"node {nid!r}") and "has no catalog spec" in w for w in lay.warnings), lay.warnings
    bare = ElectricalNetwork(groups=net.groups, nodes=tuple(n.model_copy(update={"tag": "Q-01"}) if n.id == acb.id else n for n in net.nodes), edges=net.edges)
    assert next(s for s in layout_sld(bare, catalog).symbols if s.node_id == acb.id).key == "sw.mccb"


def test_a_split_main_page_also_carries_the_first_feeders_it_has_room_for(catalog):
    """close-e2e-1 m23: the main page of a split overall SLD held only the MV switchboard, a strip across ~30 % of an A0."""
    from test_design import blank
    from test_design_p5 import GROUND_MODULE
    from unicad.arrays import PlacementSpec, place_array
    from unicad.design import autobuild
    from unicad.ingest.site_import import import_site

    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry
    spec = PlacementSpec(module=GROUND_MODULE, table_rows=2, table_cols=27, tilt_deg=25, n_blocks=4, modules_per_string=27, road_every_rows=12, target_dc_kwp=4 * 144 * 27 * 0.58 * 1.1)
    base = blank("utility_50mwac")
    base = base.model_copy(update={"site": base.site.model_copy(update={"geometry": site})})
    intent = {"template": "utility_string", "module": GROUND_MODULE, "inverter": "sungrow-sg350hx", "modules_per_string": 27, "inverters_per_block": 7}
    net = autobuild(base, catalog, intent, layout=place_array(site, spec, catalog.get(GROUND_MODULE))).project.network
    area = metrics_for(PaperSize("A0")).drawing_area
    pages = plan_pages(net, catalog, area.width, area.height, SldOptions())
    assert len(pages) > 1 and all(p.scale >= SldOptions().min_scale for p in pages)
    main = set(pages[0].node_ids)
    assert {"mv_bus", "idt_b01", "acdb_b01"} <= main, sorted(main)
    owned = [n for p in pages for n in p.node_ids]
    assert len(owned) == len(set(owned))


def test_general_notes_come_first_and_cable_notes_keep_their_numbers(projects, catalog):
    """close-e2e-1 m20: general note 1 on the rooftop SLD was a cable remark."""
    from unicad.drafting.sld.sheet import NOTES, sheet_spec

    p = projects["rooftop_500kwp"]
    lay = layout_sld(p.network, catalog)
    notes = sheet_spec(p, p.sheets[0], lay, catalog).notes
    assert notes[: len(NOTES)] == NOTES and notes[len(NOTES)] == lay.notes[0]
    callouts = {c.edge_id: " ".join(lb.text for lb in c.labels) for c in lay.callouts}
    assert f"SEE NOTE {len(NOTES) + 1}" in callouts["e_str"]


def test_a_fuseless_dc_junction_box_draws_terminals_and_the_legend_names_no_string_fuse(catalog, profile):
    """close-e2e-1 m20: the pass-through DC junction box drew the gPV fuse symbol and the legend listed GPV STRING FUSE."""
    from test_design import ROOF_MOD, blank
    from unicad.design import autobuild
    from unicad.sets.sld import plan_sld_set

    p = autobuild(blank("rooftop_500kwp", segment="residential"), catalog, {"template": "residential", "ac_kw": 5, "module": ROOF_MOD, "grid_voltage_v": 230}).project
    jb = p.network.by_kind(NodeKind.DC_FUSE_BOX)[0]
    sheets = plan_sld_set(p, profile, catalog)
    doc, res = render_sld(p, sheets[0].id, profile, catalog, sheets=sheets)
    assert next(s for s in res.layout.symbols if s.node_id == jb.id).key == "dc.jb"
    legend = {slot.key for slot in res.sheet.legend_slots}
    assert "dc.jb" in legend and "dc.fuse" not in legend
    texts = {e.dxf.text for e in doc.modelspace().query("TEXT")}
    assert "DC JUNCTION BOX" in " ".join(texts) and not any("STRING FUSE" in t for t in texts)
    agent = jb.model_copy(update={"symbol": None})
    assert agent.symbol_key(catalog) == "dc.fuse"
    assert agent.model_copy(update={"attributes": {"fused": False}}).symbol_key(catalog) == "dc.jb"


def test_breaker_type_is_shown_once_and_follows_the_device_class(projects, catalog, layouts):
    """n19 / R2: the tag names the breaker type (MCCB-01, ACB-01) and the text beside it gives make and model, not the type again; the
    tag prefix, the symbol and the catalog device class agree."""
    net = projects["rooftop_500kwp"].network
    by_node = {s.node_id: s for s in layouts["rooftop_500kwp"].symbols if s.node_id}
    seen = set()
    for n in net.by_kind(NodeKind.BREAKER):
        spec = catalog.get(n.spec)
        assert isinstance(spec, ProtectionSpec) and spec.device in ("mccb", "acb")
        sym = by_node[n.id]
        values = dict(sym.values)
        assert sym.key == f"sw.{spec.device}" and n.tag.startswith(spec.device.upper())
        assert values["DESC"] == f"{spec.manufacturer} {spec.model}".upper() and spec.device.upper() not in values["DESC"].split()
        seen.add(spec.device)
    assert seen == {"mccb", "acb"}


def test_single_core_ac_runs_name_their_cables_per_run_as_the_schedule_and_bill_do(projects, catalog, layouts):
    """A4: 3 runs of single-core 400 sq.mm on a three-phase circuit read '3R x 3 x 1C x 400 sq.mm', the schedule's cable column says
    '3 x 1C x 400 sq.mm' with 3 runs, and the bill's metres are runs x 3 x length; multicore and DC callouts are unchanged."""
    from unicad.schedules import build_schedule

    p = projects["rooftop_500kwp"]
    text = {c.edge_id: " ".join(lb.text for lb in c.labels) for c in layouts["rooftop_500kwp"].callouts}
    assert text["e_acb_ct"].startswith("3R x 3 x 1C x 400 sq.mm Al XLPE ARM")
    assert text["e_inv_mccb"].startswith("1R x 4C x 95 sq.mm") and text["e_dcfb_iso"].startswith("10R x 1C x 6 sq.mm")
    row = next(r for r in build_schedule(p, catalog).rows if r.edge == "e_acb_ct")
    assert row.callout.startswith("3 x 1C x 400 sq.mm") and row.runs == 3 and row.cables_per_run == 3
    assert row.cable_metres == pytest.approx(3 * 3 * row.length_pos_m)


def _block_order_problems(doc) -> list[str]:
    """Blocks inserted (with attributes) inside another block but defined after it: LibreDWG drops their contents on DWG write (C5)."""
    order = {b.name: i for i, b in enumerate(doc.blocks)}
    out = []
    for b in doc.blocks:
        for e in b.query("INSERT"):
            if e.attribs and order.get(e.dxf.name, -1) > order[b.name]:
                out.append(f"{e.dxf.name} after {b.name}")
    return out


def test_symbols_turned_upright_are_defined_before_the_diagram_block_that_inserts_them(tmp_path):
    """C5: the BESS SLD lost its turned battery symbol on DWG read-back (ATTDEF 23->20, LINE 175->167, SOLID 32->29): the turned copy was
    created while the diagram block was being written, so it came after it. Every symbol block now precedes the diagram block."""
    from unicad.drafting.sld.model import PlacedSymbol
    from unicad.drafting.sld.write import write_body
    from unicad.render.dwg import DwgConverter, available_converter, entity_counts

    import ezdxf

    sym = PlacedSymbol(id="b", node_id="bat", key="bat.bess", at=(0.0, 0.0), rotation=180.0, values=(("TAG", "BAT-01"),), labels=(), body=Box(-40, -30, 0, 0), pins=())
    layout = typing.cast(typing.Any, type("L", (), {"frames": (), "wires": (), "busbars": (), "symbols": (sym,), "callouts": (), "stubs": (), "junctions": ()}))
    doc = ezdxf.new("R2018", setup=["linetypes"])
    write_body(doc, layout, "UC_SLD_TEST")
    doc.modelspace().add_blockref("UC_SLD_TEST", (0, 0))
    assert any(b.name.endswith("_T180") for b in doc.blocks) and _block_order_problems(doc) == []
    if available_converter() is None:
        return
    dxf = tmp_path / "s.dxf"
    doc.saveas(dxf)
    result = DwgConverter().to_dwg(dxf, tmp_path / "s.dwg", strict=False)
    assert {k: v for k, v in result.mismatches.items() if k != "ATTRIB"} == {}


def test_sld_sheets_define_every_nested_symbol_before_its_diagram_block(projects, catalog, profile):
    for name, p in projects.items():
        doc, _ = render_sld(p, "sld", profile, catalog)
        assert _block_order_problems(doc) == [], name


def test_block_feeder_panels_are_drawn_in_the_main_switchboard_frame_with_their_tag_range(layouts, projects):
    """k1 follow-up: VCB-BF-nn, CT-BF-nn and REL-BF-nn repeat with the block but are housed in the main HT switchboard (node attribute
    housed_in), so they sit in its frame, not the block frame, and are tagged with the range of their instances."""
    net = projects["utility_50mwac"].network
    for name in ("utility_50mwac", "utility_50mwac:compact"):
        lay = layouts[name]
        frames = {f.group_id: f for f in lay.frames}
        sym = {s.node_id: s for s in lay.symbols if s.node_id}
        for nid in ("bf_vcb", "bf_ct", "bf_relay"):
            assert net.node(nid).group == "block" and net.node(nid).housed_in == "mvsb"
            b = sym[nid].body
            corners = ((b.x0, b.y0), (b.x1, b.y0), (b.x1, b.y1), (b.x0, b.y1))
            assert all(in_outline(frames["mvsb"].outline, p) for p in corners), (name, nid)
            blk = frames["block"].box
            assert not (blk.x0 <= b.x0 and b.x1 <= blk.x1 and blk.y0 <= b.y0 and b.y1 <= blk.y1), (name, nid)
        assert dict(sym["bf_vcb"].values)["TAG"] == "VCB-BF-01..VCB-BF-06"
    with pytest.raises(ValueError, match="housed in unknown group 'nope'"):
        ElectricalNetwork.model_validate({**net.model_dump(), "nodes": [n.model_dump() | ({"attributes": {"housed_in": "nope"}} if n.id == "bf_ct" else {}) for n in net.nodes]})


def test_m3_a_three_phase_run_of_single_core_cables_reads_three_cables_per_run_in_every_callout(projects, catalog):
    """close-e2e-4 M3/m1: an inverter AC cable whose length bands mix 4C and 1C cables printed '1R x 1C x 400' for a 3-phase circuit; the
    schedule and the bill count 3 x 1C per run."""
    p = projects["rooftop_500kwp"]
    net = p.network
    e = next(e for e in net.edges if e.service.value == "lv_ac" and net.node(e.src_node).kind is NodeKind.INVERTER)
    bands = (CableBand(upto_m=20, cable="polycab-lt-xlpe-al-4c-95-arm"), CableBand(upto_m=60, cable="polycab-lt-xlpe-al-4c-240-arm", runs=2))
    banded = e.model_copy(update={"cable": "polycab-lt-xlpe-al-1c-400-arm", "runs": 1, "bands": bands})
    for edge in (banded, banded.model_copy(update={"bands": ()})):
        text = " ".join(annotate.cable_callout(edge, catalog, net=net))
        assert "1R x 3 x 1C x 400" in text and "1R x 1C x 400" not in text, text
        assert "3 x 4C" not in text
    assert "2R x 4C x 240" in annotate.cable_text(banded, catalog, net)

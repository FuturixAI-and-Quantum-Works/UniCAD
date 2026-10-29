import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import ezdxf
import pytest
from ezdxf import bbox
from ezdxf.path import make_path

from unicad.drafting.symbols import GRID, REGISTRY, Category, PinKind, Side, busbar, insert, install, pin_at
from unicad.drafting.symbols.catalogue import build_catalogue, manifest
from unicad.drafting.symbols.install import reads_upside_down

ROOT = Path(__file__).resolve().parents[1]
LIBRECAD = os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun")
ALLOWED = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "TEXT", "SOLID"}
TOL = 1e-6
NO_ATTRS = {"layout.module", "ann.junction", "ann.north"}
SHORT_ATTRS = {"REV": 1, "NUM": 2, "ID": 1}
TWO_AC_PINS_ON_A_SIDE = {"sw.changeover", "sw.rmu", "tx.idt_2lv", "tx.idt_3lv", "tx.idt_4lv"}
CHAR_W = 0.8

ALL = list(REGISTRY.values())
ids = [s.key for s in ALL]


@pytest.fixture(scope="module")
def full_doc():
    doc = ezdxf.new("R2018", setup=False)
    install(doc)
    return doc


def geometry(doc, sym):
    return [e for e in doc.blocks[sym.block_name] if e.dxftype() != "ATTDEF"]


def polylines(entities):
    out = []
    for e in entities:
        if e.dxftype() == "TEXT":
            box = bbox.extents([e])
            lo, hi = box.extmin, box.extmax
            out.append([(lo.x, lo.y), (hi.x, lo.y), (hi.x, hi.y), (lo.x, hi.y), (lo.x, lo.y)])
        else:
            for sub in make_path(e).sub_paths():
                out.append([(v.x, v.y) for v in sub.flattening(0.01, segments=8)])
    return out


def segments(entities):
    for pts in polylines(entities):
        yield from zip(pts, pts[1:])


def seg_hits_rect(a, b, x0, y0, x1, y1):
    def inside(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    if max(a[0], b[0]) < x0 or min(a[0], b[0]) > x1 or max(a[1], b[1]) < y0 or min(a[1], b[1]) > y1:
        return False

    if inside(a) or inside(b):
        return True
    edges = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    return any(_cross(a, b, c, d) for c, d in edges)


def _cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return orient(a, b, c) * orient(a, b, d) <= 0 and orient(c, d, a) * orient(c, d, b) <= 0


def dist_point_segment(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / n))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def on_grid(v):
    return abs(v / GRID - round(v / GRID)) < 1e-9


def test_keys_and_block_names_unique():
    assert len({s.key for s in ALL}) == len(ALL)
    assert len({s.block_name for s in ALL}) == len(ALL)
    assert all(s.key.count(".") == 1 for s in ALL)


def test_every_category_has_symbols():
    assert {s.category for s in ALL} == set(Category)


def test_required_symbols_exist():
    required = """
    pv.module pv.string pv.array dc.fuse dc.isolator dc.spd dc.mcb dc.diode dc.scb
    inv.string inv.central inv.micro inv.pcs inv.optimiser bat.battery bat.bess
    sw.mcb sw.mccb sw.acb sw.rccb sw.hrc_fuse sw.switch_fuse sw.isolator sw.isolator_es sw.lbs
    sw.contactor sw.vcb sw.sf6 sw.draw_out sw.changeover sw.rmu prot.spd_ac prot.lightning_arrester
    prot.relay tx.power_2w tx.power_3w tx.aux tx.ct tx.pt inst.kwh inst.net_meter inst.abt
    inst.ammeter inst.voltmeter inst.mfm inst.datalogger inst.weather_station grid.utility
    grid.oh_line grid.cable_end grid.dg grid.load earth.earth earth.pe earth.electrode earth.chassis
    ann.junction ann.offsheet ann.cable_tag ann.revision ann.north ann.note_marker
    ann.section_marker layout.module layout.inverter layout.earth_pit layout.ese_la
    layout.transformer layout.scb
    prot.relay_group prot.la_counter tx.ct_2core tx.ct_3core tx.idt_2lv tx.idt_3lv tx.idt_4lv
    tx.earthing earth.ngr inst.lamp_ryb inst.annunciator inst.metering_cubicle inst.wms
    inst.scada_box inst.cctv grid.cable_term_kit dc.fuse_switch dc.scb_monitored
    """.split()
    assert not [k for k in required if k not in REGISTRY]


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_pins_are_on_grid_and_on_their_edge(sym):
    assert len({p.name for p in sym.pins}) == len(sym.pins)
    for p in sym.pins:
        assert on_grid(p.x) and on_grid(p.y), p
        edge = {Side.N: p.y - sym.height, Side.S: p.y, Side.E: p.x - sym.width, Side.W: p.x}[p.side]
        assert abs(edge) < TOL, f"{p.name} is not on the {p.side} edge"
        along = {Side.N: p.x, Side.S: p.x, Side.E: p.y, Side.W: p.y}[p.side]
        assert 0 <= along <= (sym.width if p.side in (Side.N, Side.S) else sym.height)


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_each_pin_has_conductor_geometry_ending_on_it(sym, full_doc):
    segs = list(segments(geometry(full_doc, sym)))
    for p in sym.pins:
        assert min(dist_point_segment((p.x, p.y), a, b) for a, b in segs) < 1e-4, f"nothing is drawn at pin {p.name}"


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_geometry_stays_inside_declared_box(sym, full_doc):
    box = bbox.extents(geometry(full_doc, sym))
    assert box.has_data
    tol = 0.05
    assert box.extmin.x >= -tol and box.extmin.y >= -tol, box
    assert box.extmax.x <= sym.width + tol and box.extmax.y <= sym.height + tol, box


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_only_allowed_entities_on_symbol_layer(sym, full_doc):
    for e in geometry(full_doc, sym):
        assert e.dxftype() in ALLOWED, e.dxftype()
        assert e.dxf.layer == "G-SYMBOL"


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_ac_pins_one_per_side(sym):
    per_side = {}
    for p in sym.pins:
        if p.kind is PinKind.AC:
            per_side[p.side] = per_side.get(p.side, 0) + 1
    if sym.key not in TWO_AC_PINS_ON_A_SIDE:
        assert all(n == 1 for n in per_side.values())


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_attributes_are_unique_and_complete(sym, full_doc):
    tags = [a.tag for a in sym.attrs]
    assert len(tags) == len(set(tags))
    if sym.key not in NO_ATTRS and sym.category is not Category.ANNOTATION:
        assert {"TAG", "RATING"} <= set(tags)
    attdefs = [e for e in full_doc.blocks[sym.block_name] if e.dxftype() == "ATTDEF"]
    assert sorted(e.dxf.tag for e in attdefs) == sorted(tags)
    assert all(e.dxf.layer == "G-SYMBOL" for e in attdefs)


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_attribute_text_clears_geometry(sym, full_doc):
    segs = list(segments(geometry(full_doc, sym)))
    for a in sym.attrs:
        if a.centred:
            half = a.max_chars * CHAR_W * a.height / 2
            x0, x1 = a.x - half, a.x + half
        else:
            n = SHORT_ATTRS.get(a.tag, max(10, len(a.default)))
            x0, x1 = a.x, a.x + n * CHAR_W * a.height
        y0, y1 = a.y - a.height / 2, a.y + a.height / 2
        hit = [s for s in segs if seg_hits_rect(*s, x0, y0, x1, y1)]
        assert not hit, f"{a.tag} text box ({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f}) overlaps geometry"


def test_attribute_text_of_different_attrs_does_not_overlap():
    for sym in ALL:
        rects = [(a.tag, a.y - a.height / 2, a.y + a.height / 2, a.x, a.x + 8 * CHAR_W * a.height) for a in sym.attrs]
        for i, r in enumerate(rects):
            for q in rects[i + 1 :]:
                same_column = abs(r[3] - q[3]) < 1e-6
                if same_column:
                    assert r[2] <= q[1] + 1e-9 or q[2] <= r[1] + 1e-9, f"{sym.key}: {r[0]} overlaps {q[0]}"


def test_install_is_idempotent_and_selective():
    doc = ezdxf.new("R2018", setup=False)
    install(doc, ["sw.mcb"])
    assert "UC_SW_MCB" in doc.blocks and "UC_SW_MCCB" not in doc.blocks
    first = [e.dxftype() for e in doc.blocks["UC_SW_MCB"]]
    install(doc, ["sw.mcb", "sw.mccb"])
    install(doc)
    assert [e.dxftype() for e in doc.blocks["UC_SW_MCB"]] == first
    assert sum(1 for b in doc.blocks if b.name.startswith("UC_")) == len(REGISTRY)


def test_install_unknown_key_raises():
    with pytest.raises(KeyError):
        install(ezdxf.new("R2018"), ["nope.nothing"])


def test_insert_rejects_unknown_tag():
    doc = ezdxf.new("R2018")
    with pytest.raises(ValueError, match="BOGUS"):
        insert(doc.modelspace(), "sw.mcb", (0, 0), values={"TAG": "Q1", "BOGUS": "x"})


def test_insert_writes_values_defaults_and_visible_text():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    ref = insert(msp, "tx.power_2w", (100, 50), values={"TAG": "TR-1", "RATING": "2.5 MVA"})
    attribs = {a.dxf.tag: a for a in ref.attribs}
    assert attribs["TAG"].dxf.text == "TR-1"
    assert attribs["VECTOR"].dxf.text == "Dyn11"
    assert attribs["DESC"].dxf.text == ""
    texts = {t.dxf.text for t in msp.query("TEXT")}
    assert {"TR-1", "2.5 MVA", "Dyn11"} <= texts
    assert "" not in texts
    for a in ref.attribs:
        assert a.dxf.flags & 1


def test_insert_without_visible_text_keeps_attribs_visible():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    ref = insert(msp, "sw.mcb", (0, 0), values={"TAG": "Q1"}, visible_text=False)
    assert not msp.query("TEXT")
    assert all(not (a.dxf.flags & 1) for a in ref.attribs)


def test_insert_places_block_at_point_and_rotation():
    doc = ezdxf.new("R2018")
    ref = insert(doc.modelspace(), "sw.mcb", (12.5, 7.5), rotation=90)
    assert tuple(ref.dxf.insert)[:2] == (12.5, 7.5)
    assert ref.dxf.rotation == 90
    assert ref.dxf.name == "UC_SW_MCB"


def test_insert_text_is_right_aligned_when_flipped_west():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    insert(msp, "sw.mcb", (0, 0), rotation=180, values={"TAG": "Q1"})
    text = next(iter(msp.query("TEXT")))
    assert text.dxf.insert.x < 0
    assert text.dxf.halign == 2


def test_upright_insert_keeps_the_label_stack_right_of_a_turned_symbol():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    sym = REGISTRY["inv.string"]
    insert(msp, "inv.string", (0, 0), rotation=180, values={"TAG": "INV-01", "RATING": "100 kVA"}, upright=True)
    texts = {t.dxf.text: t for t in msp.query("TEXT")}
    assert all(t.dxf.halign == 0 and t.dxf.insert.x > 0 for t in texts.values())
    assert texts["INV-01"].dxf.insert.y > texts["100 kVA"].dxf.insert.y
    tag = next(a for a in sym.attrs if a.tag == "TAG")
    assert texts["INV-01"].dxf.insert.x == pytest.approx(tag.x - sym.width)

@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pin_at_matches_cad_insert_transform(rotation):
    doc = ezdxf.new("R2018")
    for key in ("sw.changeover", "tx.power_3w", "inv.micro"):
        sym = REGISTRY[key]
        at = (40.0, -20.0)
        ref = insert(doc.modelspace(), key, at, rotation=rotation)
        m = ref.matrix44()
        for p in sym.pins:
            expect = m.transform((p.x, p.y, 0))
            got = pin_at(key, p.name, at, rotation)
            assert got == pytest.approx((expect.x, expect.y), abs=1e-6)


def test_pin_at_rotation_semantics():
    assert pin_at("sw.mcb", "in", (0, 0)) == (5.0, 20.0)
    assert pin_at("sw.mcb", "out", (0, 0), 90) == (0.0, 5.0)
    assert pin_at("sw.mcb", "in", (0, 0), 90) == (-20.0, 5.0)
    assert pin_at("sw.mcb", "in", (10, 10), 180) == (5.0, -10.0)
    with pytest.raises(KeyError):
        pin_at("sw.mcb", "nope", (0, 0))


def test_manifest_matches_registry_and_committed_file():
    m = manifest()
    assert [e["key"] for e in m] == list(REGISTRY)
    committed = ROOT / "library" / "unicad-symbols.json"
    if committed.exists():
        assert json.loads(committed.read_text(encoding="utf-8")) == m, "run tools/build_symbol_library.py"


@pytest.fixture(scope="module")
def catalogue_dxf(tmp_path_factory):
    doc = build_catalogue()
    path = tmp_path_factory.mktemp("lib") / "unicad-symbols.dxf"
    doc.saveas(path)
    return doc, path


def test_catalogue_audits_clean_and_round_trips(catalogue_dxf):
    doc, path = catalogue_dxf
    audit = doc.audit()
    assert not audit.errors, [e.message for e in audit.errors]
    reloaded = ezdxf.readfile(path)
    assert not reloaded.audit().errors
    assert sum(1 for b in reloaded.blocks if b.name.startswith("UC_")) == len(REGISTRY)
    assert len(list(reloaded.modelspace().query("INSERT"))) == len(REGISTRY)


def test_catalogue_pins_are_marked_on_their_own_layer(catalogue_dxf):
    doc, _ = catalogue_dxf
    pins = sum(len(s.pins) for s in ALL)
    assert len(list(doc.modelspace().query("CIRCLE[layer=='G-PIN']"))) == pins


@pytest.mark.skipif(not (Path(LIBRECAD).exists() or shutil.which(LIBRECAD)), reason="LibreCAD not installed")
def test_librecad_renders_catalogue_to_pdf(catalogue_dxf, tmp_path):
    _, path = catalogue_dxf
    out = tmp_path / "out.pdf"
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    proc = subprocess.run(
        [LIBRECAD, "dxf2pdf", "-a", "-p", "841x594", "-o", str(out), str(path)],
        env=env,
        capture_output=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr.decode()[-500:]
    assert out.exists() and out.stat().st_size > 10_000
    assert out.read_bytes()[:5] == b"%PDF-"


def _texts(doc, sym):
    return [e.dxf.text for e in geometry(doc, sym) if e.dxftype() == "TEXT"]


def _circles(doc, sym):
    return [e for e in geometry(doc, sym) if e.dxftype() == "CIRCLE"]


def test_relay_is_a_circle_with_a_centred_ansi_attribute(full_doc):
    sym = REGISTRY["prot.relay"]
    (circle,) = _circles(full_doc, sym)
    ansi = next(a for a in sym.attrs if a.tag == "ANSI")
    assert ansi.centred and ansi.max_chars >= 4
    assert (ansi.x, ansi.y) == pytest.approx((circle.dxf.center.x, circle.dxf.center.y))
    assert circle.dxf.radius * 2 > ansi.max_chars * CHAR_W * ansi.height
    assert "IEEE C37.2" in sym.standard and "IEC 60617" in sym.standard


def test_relay_group_has_six_touching_circles_with_one_ansi_attribute_each(full_doc):
    sym = REGISTRY["prot.relay_group"]
    tags = [a.tag for a in sym.attrs if a.tag.startswith("ANSI")]
    assert tags == [f"ANSI{i}" for i in range(1, 7)]
    circles = sorted(_circles(full_doc, sym), key=lambda c: c.dxf.center.x)
    assert len(circles) == 6
    for left, right in zip(circles, circles[1:]):
        assert right.dxf.center.x - left.dxf.center.x == pytest.approx(left.dxf.radius + right.dxf.radius)
    for attr, circle in zip((a for a in sym.attrs if a.tag.startswith("ANSI")), circles):
        assert attr.centred
        assert (attr.x, attr.y) == pytest.approx((circle.dxf.center.x, circle.dxf.center.y))


def test_relay_group_values_are_written_centred_in_each_circle():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    values = {"ANSI1": "50", "ANSI2": "51", "ANSI3": "50N"}
    ref = insert(msp, "prot.relay_group", (100, 40), values=values)
    placed = {t.dxf.text: t for t in msp.query("TEXT") if t.dxf.text in values.values()}
    assert set(placed) == set(values.values())
    for t in placed.values():
        assert t.dxf.halign == 1 and t.dxf.valign == 2
    assert {a.dxf.tag: a.dxf.text for a in ref.attribs}["ANSI4"] == ""


def test_relay_ansi_text_stays_centred_when_symbol_is_flipped():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    insert(msp, "prot.relay", (0, 0), rotation=180, values={"ANSI": "87T"})
    text = next(t for t in msp.query("TEXT") if t.dxf.text == "87T")
    assert text.dxf.halign == 1


def test_breaker_symbols_carry_no_type_text_beside_them(full_doc):
    """close-e2e-4 n19: the type reads from the tag (MCCB-01, ACB-01) and the legend, not from a label drawn beside the symbol."""
    for key in ("sw.mcb", "sw.mccb", "sw.acb"):
        assert _texts(full_doc, REGISTRY[key]) == [], key


def _band(msp):
    (solid,) = msp.query("SOLID")
    xs = [v.x for v in solid.vertices()]
    ys = [v.y for v in solid.vertices()]
    return min(xs), max(xs), min(ys), max(ys)


def test_busbar_is_a_solid_band_on_the_symbol_layer_with_its_label():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    band = busbar(msp, 10.0, 210.0, 150.0, "33 kV, 1200 A, 25 kA FOR 1 SEC, AL. BUSBAR")
    assert band.dxf.layer == "G-SYMBOL"
    x0, x1, y0, y1 = _band(msp)
    assert (x0, x1) == (10.0, 210.0)
    assert y0 < 150.0 < y1 and (y1 - y0) == pytest.approx(1.0)
    assert (y0 + y1) / 2 == pytest.approx(150.0)
    (label,) = msp.query("TEXT")
    assert label.dxf.text == "33 kV, 1200 A, 25 kA FOR 1 SEC, AL. BUSBAR"
    assert label.dxf.layer == "G-SYMBOL-TXT"
    assert label.dxf.insert.x == pytest.approx(10.0) and label.dxf.insert.y > y1
    assert not doc.audit().errors


def test_busbar_without_label_writes_no_text_and_rejects_empty_span():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    busbar(msp, 0.0, 50.0, 0.0)
    assert not msp.query("TEXT")
    with pytest.raises(ValueError):
        busbar(msp, 50.0, 50.0, 0.0)
    with pytest.raises(ValueError):
        busbar(msp, 60.0, 50.0, 0.0)


@pytest.mark.parametrize("lv", [2, 3, 4])
def test_inverter_duty_transformer_has_one_hv_and_n_lv_pins(lv, full_doc):
    sym = REGISTRY[f"tx.idt_{lv}lv"]
    hv = [p for p in sym.pins if p.name == "hv"]
    lvs = [p for p in sym.pins if p.name.startswith("lv")]
    assert len(sym.pins) == 1 + lv and len(hv) == 1
    assert [p.name for p in lvs] == [f"lv{i}" for i in range(1, lv + 1)]
    assert hv[0].side is Side.N and all(p.side is Side.S for p in lvs)
    xs = [p.x for p in lvs]
    assert xs == sorted(xs) and len(set(xs)) == lv
    assert hv[0].x == pytest.approx(sum(xs) / lv)
    assert len(_circles(full_doc, sym)) == 1 + lv


def test_inverter_duty_transformer_windings_interlock(full_doc):
    circles = _circles(full_doc, REGISTRY["tx.idt_4lv"])
    hv = max(circles, key=lambda c: c.dxf.center.y)
    lvs = sorted((c for c in circles if c is not hv), key=lambda c: c.dxf.center.x)

    def overlap(c, d):
        return math.dist(tuple(c.dxf.center)[:2], tuple(d.dxf.center)[:2]) < c.dxf.radius + d.dxf.radius

    assert all(overlap(a, b) for a, b in zip(lvs, lvs[1:]))
    assert sum(overlap(hv, lv) for lv in lvs) >= 2


@pytest.mark.parametrize("cores", [2, 3])
def test_multi_core_ct_has_a_ratio_attribute_and_secondary_pin_per_core(cores, full_doc):
    sym = REGISTRY[f"tx.ct_{cores}core"]
    ratios = [a for a in sym.attrs if a.tag.startswith("RATIO")]
    secs = [p for p in sym.pins if p.name.startswith("sec")]
    assert [a.tag for a in ratios] == [f"RATIO{i}" for i in range(1, cores + 1)]
    assert [p.name for p in secs] == [f"sec{i}" for i in range(1, cores + 1)]
    assert len(_circles(full_doc, sym)) == cores
    for attr, pin in zip(ratios, secs):
        assert attr.y == pytest.approx(pin.y)
    assert all(p.side is Side.E and p.kind is PinKind.SIGNAL for p in secs)


def test_multi_core_ct_ratio_values_are_written_per_core():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    insert(msp, "tx.ct_3core", (0, 0), values={"RATIO1": "CL 0.2S 15 VA", "RATIO2": "CL 5P20 15 VA", "RATIO3": "CL PS"})
    assert {"CL 0.2S 15 VA", "CL 5P20 15 VA", "CL PS"} <= {t.dxf.text for t in msp.query("TEXT")}


def test_every_symbol_with_a_tag_attribute_has_a_tag_prefix():
    for sym in ALL:
        has_tag = any(a.tag == "TAG" for a in sym.attrs)
        assert bool(sym.tag_prefix) == has_tag, f"{sym.key}: tag_prefix {sym.tag_prefix!r}, TAG attribute {has_tag}"
        if has_tag:
            assert sym.tag_prefix == sym.tag_prefix.upper() and sym.tag_prefix.isalnum()


@pytest.mark.parametrize(
    "key,prefix",
    [
        ("inv.string", "INV"), ("dc.scb", "SCB"), ("tx.idt_4lv", "IDT"), ("tx.power_2w", "TX"), ("sw.vcb", "VCB"),
        ("sw.acb", "ACB"), ("sw.mccb", "MCCB"), ("sw.mcb", "MCB"), ("sw.isolator", "ISO"), ("prot.spd_ac", "SPD"),
        ("prot.lightning_arrester", "LA"), ("tx.ct", "CT"), ("tx.pt", "PT"), ("inst.kwh", "EM"),
        ("inst.net_meter", "NM"), ("inst.abt", "ABT"), ("prot.relay", "RLY"), ("bat.bess", "BESS"),
        ("inv.pcs", "PCS"), ("grid.dg", "DG"), ("sw.rmu", "RMU"), ("sw.lbs", "LBS"), ("sw.hrc_fuse", "FU"),
    ],
)
def test_indian_tag_prefixes(key, prefix):
    assert REGISTRY[key].tag_prefix == prefix


def test_catalogue_sample_tags_use_the_tag_prefix():
    doc = build_catalogue()
    texts = {t.dxf.text for t in doc.modelspace().query("TEXT")}
    for sym in ALL:
        if sym.tag_prefix:
            assert f"{sym.tag_prefix}-01" in texts, sym.key


IEC_60617_IDS = {
    "S00050": "Cable sealing end (multi-core cable)",
    "S00051": "Cable sealing end (one-core cables)",
    "S00200": "Earth, general symbol",
    "S00284": "Contactor; Main make contact of a contactor",
    "S00287": "Circuit breaker",
    "S00288": "Disconnector; Isolator",
    "S00289": "Two-way disconnector; Two-way isolator",
    "S00290": "Switch-disconnector; On-load isolating switch",
    "S00327": "Measuring relay; Device related to a measuring relay",
    "S00362": "Fuse, general symbol",
    "S00370": "Fuse switch-disconnector; On-load isolating fuse switch",
    "S00373": "Surge diverter; Lightning arrester",
    "S00409": "Overhead line",
    "S00641": "Semiconductor diode, general symbol",
    "S00841": "Transformer with two windings, general symbol",
    "S00850": "Current transformer, general symbol",
    "S00866": "Three-phase transformer, connection star-zigzag with the neutral brought out",
    "S00878": "Voltage transformer",
    "S00896": "Inverter",
    "S00908": "Photovoltaic generator",
    "S00910": "Indicating instrument, general symbol",
    "S00913": "Voltmeter",
    "S00933": "Watt-hour meter",
    "S00937": "Watt-hour meter, counting in both energy flow directions",
    "S00965": "Lamp, general symbol",
    "S00967": "Indicator, electromechanical; annunciator element",
    "S01342": "Battery of primary or secondary cells",
    "S01848": "Combined disconnector and earthing switch",
}


def test_iec_60617_identifiers_are_confirmed_ones():
    """IEC_60617_IDS is copied from the IEC 60617 database listing (std.iec.ch, IEC webstore preview of 2026-03-02)."""
    import re

    for sym in ALL:
        for ident in re.findall(r"S\d{5}", sym.standard):
            assert ident in IEC_60617_IDS, f"{sym.key}: {ident} is not in the confirmed list"
        assert not re.search(r"IEC 60617-\d", sym.standard), f"{sym.key}: part numbers are not confirmed; name the S-number"


def misread_texts(doc) -> list[str]:
    """Every drawn TEXT of the modelspace (inside blocks too, as placed) that reads upside down, top to bottom, or mirrored."""
    out: list[str] = []

    def visit(entities, mirrored: bool):
        for e in entities:
            if e.dxftype() == "INSERT":
                flip = mirrored != (e.dxf.get("xscale", 1.0) * e.dxf.get("yscale", 1.0) < 0)
                visit(e.virtual_entities(), flip)
            elif e.dxftype() == "TEXT" and e.dxf.text.strip():
                r = e.dxf.get("rotation", 0.0)
                if mirrored or reads_upside_down(r) or e.dxf.get("text_generation_flag", 0) & 6 or e.dxf.get("width", 1.0) < 0:
                    out.append(f"{e.dxf.text!r} at {r:g} deg")

    visit(doc.modelspace(), False)
    return out


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_symbol_texts_read_upright_at_every_rotation(rotation):
    doc = ezdxf.new("R2018", setup=False)
    for i, sym in enumerate(ALL):
        insert(doc.modelspace(), sym.key, (i * 100.0, 0.0), rotation, upright=True)
    assert misread_texts(doc) == []

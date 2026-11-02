"""Drafting follow-ups after fix wave 7 on the RVUNL LT plant as the brief gives it (720 Wp Avaada modules, 8 Solis 150 kW inverters with 7 MPPTs,
inverter 8 with 18- and 19-module strings, an ACCB into the client's 415 V panel with CT, PT, relay, meter and an RS-485 data logger)."""

from pathlib import Path

import pytest
from test_design_rvunl import BRIEF, CATALOG, RELAY, heerapura

from unicad.design import autobuild
from unicad.domain.network import SpdRating
from unicad.domain.project import Project
from unicad.drafting.sld.annotate import bus_texts, node_texts
from unicad.drafting.string_diagram import parameters_for, plan_types
from unicad.rules.pv import cell_temp_min, string_voltage

MODULE = "avaada-electro-avn66g12g-720"
INVERTER = "solis-s6-gc3p150k07-nv-nd"


@pytest.fixture(scope="module")
def lt():
    return autobuild(heerapura(), CATALOG, {"module": MODULE, "inverter": INVERTER, "inverter_strings": BRIEF, "relay": RELAY, "data_logger": True}).project


def test_string_diagram_groups_strings_on_their_recorded_mppt(lt):
    types, _ = plan_types(lt, CATALOG)
    inv8 = next(t for t in types if t.representative.tag == "INV-08")
    by_mppt: dict[int, list[str]] = {}
    for ln in inv8.lines:
        by_mppt.setdefault(ln.mppt, []).append(ln.tag)
    assert by_mppt == {k: [f"STR18-08-{k:02d}", f"STR19-08-{k:02d}"] for k in range(1, 7)} | {7: ["STR18-08-07"]}
    assert [ln.mppt for ln in inv8.lines] == sorted(ln.mppt for ln in inv8.lines), "one MPPT's strings sit together"
    assert [ln.ip for ln in inv8.lines] == [f"IP{i:02d}" for i in range(1, 14)]
    assert inv8.mppt.used == 7 and inv8.mppt.mppt_count == 7 and inv8.mppt.inputs_per_mppt == 2
    assert any("as the network records them" in n for n in inv8.notes) and not any("spread evenly" in n for n in inv8.notes)
    inv5 = next(t for t in types if t.representative.tag == "INV-05")
    assert [ln.mppt for ln in inv5.lines] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7]


def test_string_diagram_prints_each_family_voc(lt):
    types, _ = plan_types(lt, CATALOG)
    inv8 = next(t for t in types if t.representative.tag == "INV-08")
    module = CATALOG.get(MODULE)
    cold = cell_temp_min(lt.design_basis)
    voc = {n: round(string_voltage(module, n, "voc", cold).value, 1) for n in (18, 19)}
    assert {ln.modules: ln.voc_cold_v for ln in inv8.lines} == voc and voc[19] > voc[18]
    params = dict(parameters_for(inv8))
    assert params["MODULES IN SERIES"] == "18 / 19"
    assert params[f"VOC AT {inv8.tmin_c:g} DEG C"] == f"{voc[18]:.1f} V (18) / {voc[19]:.1f} V (19) (LIMIT {inv8.voc_limit_v:g} V)"


# ---------------- typed node ratings on the SLD (N2) ----------------

DATA = Path(__file__).resolve().parent / "data"


def bench2() -> Project:
    """The RVUNL plant as bench run 2 built it over MCP (utility_lt, Avaada 720 Wp, Solis 150 kW, data logger, relay) with the brief's typed ratings."""
    return Project.model_validate_json((DATA / "rvunl_heerapura_bench2.json").read_text(encoding="utf-8"))


def texts_of(project: Project, nid: str) -> dict[str, str]:
    return node_texts(project.network, project.network.node(nid), CATALOG)


def test_typed_busbar_prints_as_a_rating_not_a_field_dump():
    p = bench2()
    lines = bus_texts(p.network, p.network.node("acdb"))
    assert lines[1] == "415 V, 2500 A, 31.5 kA 3 s, AL BUSBAR, 3P+N"
    assert not any("=" in x or "MATERIAL" in x for x in lines), lines


def test_typed_ct_pt_relay_spd_and_ir_print_beside_their_symbols():
    p = bench2()
    ct = texts_of(p, "solar_ct")["RATING"]
    assert ct == "415 V, 2500/1 A, C1 CL 0.2S 15 VA METERING, C2 CL PS PROTECTION, C3 CL 0.2S 15 VA METERING", ct
    pt = texts_of(p, "solar_pt")["RATING"]
    assert pt == "415 V, 0.415 kV/\u221a3 / 110 V/\u221a3, C1 CL 0.2 15 VA METERING, C2 CL 3P 15 VA PROTECTION", pt
    relay = texts_of(p, "relay")
    assert [relay[f"ANSI{i}"] for i in range(1, 7)] == ["50", "51", "67", "67N", "27", "59"]
    assert relay["RATING"] == "50N, 51N, IEC 61850", relay
    spd = texts_of(p, "acdb_spd")["RATING"]
    assert spd == "4P, TYPE 2, 275 V, In/Imax 20/40 kA", spd
    acb = texts_of(p, "acdb_brk")["RATING"]
    assert acb.startswith("2500 A, Ir = 2400 A, 4P"), acb
    assert "Ir" not in texts_of(p, "inv_brka")["RATING"]


def test_typed_spd_imax_wins_over_the_catalog():
    p = bench2()
    net = p.network
    spd = net.node("acdb_spd").model_copy(update={"spd": SpdRating(spd_type="2", imax_ka=65)})
    text = node_texts(net, spd, CATALOG)["RATING"]
    assert text == "4P, TYPE 2, 275 V, In/Imax 20/65 kA", text


def test_attribute_fallbacks_still_print():
    p = bench2()
    net = p.network
    ct = net.node("solar_ct").model_copy(update={"ct": None, "attributes": {"ratio": "2500/1 A", "cores": ["0.2S 15 VA", "PS"]}})
    assert node_texts(net, ct, CATALOG)["RATING"] == "415 V, RATIO 2500/1 A, C1 0.2S 15 VA, C2 PS"
    bus = net.node("acdb").model_copy(update={"busbar": None, "attributes": {"rating_a": 2500, "fault_ka": 31.5, "fault_duration_s": 3, "material": "al"}})
    assert bus_texts(net, bus)[1] == "415 V, 2500 A, 31.5 kA 3 s, AL BUSBAR"


# ---------------- data logger and meter on a stacked SLD (N6) ----------------


def sld_sheets(project: Project):
    """Every SLD sheet of the set with its layout and warnings."""
    from unicad.drafting.sld.sheet import render_sld
    from unicad.drafting.sld.xref import partition
    from unicad.sets.render import plan_set
    from unicad.sets.sld import _drawn, _shown
    from unicad.tenants.store import drawing_profile

    profile = drawing_profile(project.company_profile)
    sheets = dict(plan_set(project, ["sld"], profile, CATALOG))["sld"]
    out = []
    for s, part in zip(sheets, partition(project.network, sheets), strict=True):
        _, res = render_sld(_shown(project, s, CATALOG), s.id, profile, CATALOG, sheets=[s], nodes=_drawn(project.network, s, part.nodes), xrefs=part.xrefs)
        out.append((s, res.layout, res.warnings))
    return out


@pytest.fixture(scope="module")
def bench_sld():
    return sld_sheets(bench2())


def test_logger_and_meter_are_drawn_once_with_no_missing_host(bench_sld):
    symbols = [x for _, lay, _ in bench_sld for x in lay.symbols]
    assert sum(x.node_id == "dlog" for x in symbols) == 1
    assert sum(x.node_id == "solar_meter" for x in symbols) == 1
    from collections import Counter

    twice = {n: c for n, c in Counter(x.node_id for x in symbols if x.role == "node").items() if c > 1}
    assert not twice, twice
    warnings = [w for _, _, ws in bench_sld for w in ws]
    assert not [w for w in warnings if "no host" in w or "missing pin" in w], warnings


def test_every_inverter_rs485_link_is_drawn_or_continued(bench_sld):
    from unicad.drafting.sld.model import WireKind

    links = {e.id for e in bench2().network.edges if e.dst_node == "dlog"}
    drawn = {w.edge_id for _, lay, _ in bench_sld for w in lay.wires if w.kind is WireKind.COMMS}
    continued = {st.pin for _, lay, _ in bench_sld for st in lay.stubs if st.kind is WireKind.COMMS}
    assert links <= drawn | continued, links - drawn - continued
    stubs = [lb.text for _, lay, _ in bench_sld for st in lay.stubs if st.kind is WireKind.COMMS for lb in st.labels]
    at_inverters = [t for t in stubs if "DL-01" in t]
    assert at_inverters and all("SEE ROW 1" in t for t in at_inverters), stubs


def test_no_symbol_sits_on_a_row_marker_or_the_poi_text(bench_sld):
    for _, lay, _ in bench_sld:
        markers = [x for x in lay.symbols if x.role == "offsheet"]
        poi = [x for x in lay.symbols if x.node_id == "poi"]
        for x in lay.symbols:
            if x.role == "offsheet" or x.node_id == "poi":
                continue
            boxes = [x.body, *(lb.box for lb in x.labels)]
            for m in markers + poi:
                others = [m.body, *(lb.box for lb in m.labels)]
                assert not any(a.overlaps(b) for a in boxes for b in others), (x.node_id, m.id)


def test_rs485_links_share_one_note_naming_every_inverter(bench_sld):
    from unicad.drafting.sld.sheet import sheet_spec

    p = bench2()
    s, lay, _ = bench_sld[0]
    notes = [n for n in sheet_spec(p, s, lay, CATALOG).notes if n.startswith("COMMUNICATION LINK")]
    assert notes == ["COMMUNICATION LINKS INV-01..INV-04, INV-05, INV-06, INV-07, INV-08 TO DL-01: RS-485 DAISY CHAIN THROUGH THE INVERTERS.",
                     "COMMUNICATION LINK SM-01 TO DL-01: RS-485."], notes


def test_a_daisy_chain_the_edges_hold_is_drawn_inverter_to_inverter_to_logger():
    from unicad.domain.network import ElectricalNetwork
    from unicad.drafting.sld.model import WireKind

    p = bench2()
    net = p.network
    chain = ["inva", "invx05", "invx06", "invx07", "invx08", "dlog"]
    links = [e for e in net.edges if e.dst_node == "dlog" and e.src_node.startswith("inv")]
    others = [e for e in net.edges if e not in links]
    daisy = tuple(links[0].model_copy(update={"id": f"c_rs485_{a}", "src": f"{a}:sig", "dst": f"{b}:sig"}) for a, b in zip(chain, chain[1:]))
    p = p.model_copy(update={"network": ElectricalNetwork(groups=net.groups, nodes=net.nodes, edges=(*others, *daisy))})
    sheets = sld_sheets(p)
    drawn = {w.edge_id for _, lay, _ in sheets for w in lay.wires if w.kind is WireKind.COMMS}
    drawn |= {st.pin for _, lay, _ in sheets for st in lay.stubs if st.kind is WireKind.COMMS}
    assert {e.id for e in daisy} <= drawn, {e.id for e in daisy} - drawn
    warnings = [w for _, _, ws in sheets for w in ws]
    assert not [w for w in warnings if "no host" in w or "missing pin" in w], warnings
    assert sum(x.node_id == "dlog" for _, lay, _ in sheets for x in lay.symbols) == 1


# ---------------- stale layout equipment (item 7) ----------------


def test_layout_and_earthing_sheets_skip_equipment_whose_node_was_removed(tmp_path):
    """network_remove leaves the node's layout.equipment entry behind; the plan sheets draw only what the network still holds."""
    import ezdxf

    from unicad.domain.layout import PlacedEquipment, Point
    from unicad.sets import render_set

    p = bench2()
    lay = p.array_layout
    stale = PlacedEquipment(node="inv_removed", tag="INV-99", kind="inverter", at=Point(x=60.0, y=40.0), basis="left by network_remove")
    p = p.model_copy(update={"array_layout": lay.model_copy(update={"equipment": (*lay.equipment, stale)})})
    index = render_set(p, tmp_path, kinds=["layout", "earthing"], formats=("dxf",), catalog=CATALOG)
    for s in index.as_dict(absolute=True)["sheets"]:
        doc = ezdxf.readfile(s["files"]["dxf"])
        text = " ".join(e.dxf.text for e in doc.modelspace().query("TEXT"))
        assert "INV-99" not in text and "ACCB-01" in text, s["kind"]


# ---------------- register: location named once, degree sign kept (N8) ----------------


def test_register_names_the_state_once_and_prints_the_degree_sign(tmp_path):
    import ezdxf

    from unicad.sets import render_set

    p = bench2()
    site = p.site.model_copy(update={"name": "Heerapura (26°53'13.41\"N 75°43'10.36\"E)", "address": "Heerapura, Jaipur, Rajasthan 302026", "state": "Rajasthan"})
    p = p.model_copy(update={"site": site})
    index = render_set(p, tmp_path, kinds=["register"], formats=("dxf",), catalog=CATALOG)
    doc = ezdxf.readfile(index.as_dict(absolute=True)["sheets"][0]["files"]["dxf"])
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    starts = [i for i, t in enumerate(texts) if t.startswith("HEERAPURA (")]
    assert len(starts) == 2, "the register table and the title block"
    for i in starts:
        row = " ".join(texts[i:i + 4])
        row = row[:row.index(" E", row.index("LONG")) + 2]
        assert row.count("RAJASTHAN") == 1 and ("26%%d53'" in row or "26°53'" in row) and "DEG" not in row, row


def test_layout_notes_give_the_site_point_as_the_client_gave_it():
    from unicad.sets.plans import layout_notes

    p = bench2()
    note = next(n for n in layout_notes(p, None) if n.startswith("SITE LOCATION AS GIVEN"))
    x, y = p.site.geometry.lonlat_to_local(p.site.longitude, p.site.latitude)
    assert "LAT 26.887058 N LONG 75.719544 E" in note and f"X {x:.1f} m, Y {y:.1f} m" in note and 0 < x < 230 and 0 < y < 140, note


# ---------------- register: every assumption listed, over more pages if need be (N10) ----------------


def test_register_lists_every_assumption_in_its_table_and_drops_none(tmp_path):
    import ezdxf

    from unicad.drafting.assumptions import set_assumptions
    from unicad.sets import render_set

    p = bench2()
    many = tuple(f"Assumed test input {i:02d}: placeholder text long enough to take a line of the register" for i in range(40))
    p = p.model_copy(update={"design_basis": p.design_basis.model_copy(update={"assumptions": p.design_basis.assumptions + many})})
    index = render_set(p, tmp_path, kinds=["register", "sld"], formats=("dxf",), catalog=CATALOG).as_dict(absolute=True)
    registers = [s for s in index["sheets"] if s["kind"] == "register"]
    assert len(registers) >= 2, "the register continues on a second page"
    assert not [w for s in registers for w in s["warnings"] if "did not fit" in w], [s["warnings"] for s in registers]
    text = " ".join(e.dxf.text for s in registers for e in ezdxf.readfile(s["files"]["dxf"]).modelspace().query("TEXT"))
    for i in range(40):
        assert f"ASSUMED TEST INPUT {i:02d}" in text, i
    assert len(set_assumptions(p)) >= 40 and "DRIVES: SLD" in text


def test_derived_equipment_positions_are_assumptions_on_the_plan_sheets():
    from unicad.drafting.assumptions import sheet_assumptions

    p = bench2()
    for kind in ("layout", "earthing"):
        got = sheet_assumptions(p, kind)
        assert any(a.startswith("ACCB-01 POSITION NOT GIVEN") for a in got) and any(a.startswith("POI-01 POSITION NOT GIVEN") for a in got), got
        assert not any("PASS SWITCHYARD" in a for a in got)
    assert not any("POSITION NOT GIVEN" in a for a in sheet_assumptions(p, "sld"))


# ---------------- RVUNL layout: block labels off the tables (item 5) ----------------


def test_rvunl_block_labels_stand_clear_of_the_tables(tmp_path):
    import ezdxf
    from ezdxf import bbox
    from shapely.geometry import box as sbox

    from unicad.sets import render_set

    p = Project.model_validate_json((DATA / "rvunl_heerapura.json").read_text(encoding="utf-8"))
    index = render_set(p, tmp_path, kinds=["layout"], formats=("dxf",), catalog=CATALOG).as_dict(absolute=True)
    msp = ezdxf.readfile(index["sheets"][0]["files"]["dxf"]).modelspace()
    tables = [sbox(b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y) for e in msp.query("INSERT[layer=='L-ARRAY']") if (b := bbox.extents([e])).has_data]
    names = [t for t in msp.query("TEXT") if t.dxf.text.startswith("BLOCK-")]
    boxes = []
    for pl in msp.query("LWPOLYLINE[layer=='L-STATION']"):
        xs, ys = zip(*((x, y) for x, y, *_ in pl.get_points()))
        b = sbox(min(xs), min(ys), max(xs), max(ys))
        if any(b.contains(sbox(*t.dxf.insert.vec2, *t.dxf.insert.vec2)) for t in names):
            boxes.append(b)
    assert len(boxes) == 12
    covered = [sum(b.intersection(t).area for t in tables) / b.area for b in boxes]
    assert max(covered) < 0.15, [round(c, 2) for c in covered]


# ---------------- hybrid earthing: BESS tag stack leaders do not cross (item 6) ----------------


def test_hybrid_earthing_bess_tag_stack_leaders_do_not_cross(tmp_path):
    import gzip
    from collections import defaultdict

    import ezdxf
    from shapely.geometry import LineString

    from unicad.sets import render_set
    from unicad.store import parse_project

    root = Path(__file__).resolve().parents[1]
    p = parse_project(gzip.decompress((root / "fixtures" / "projects" / "kutch_hybrid.json.gz").read_bytes()).decode("utf-8"), CATALOG)
    index = render_set(p, tmp_path, kinds=["earthing"], formats=("dxf",), catalog=CATALOG).as_dict(absolute=True)
    msp = ezdxf.readfile(index["sheets"][0]["files"]["dxf"]).modelspace()
    stacks = defaultdict(list)
    for e in msp.query("LINE[layer=='E-PIT-TAG']"):
        stacks[round(e.dxf.end.x, 2)].append(LineString([e.dxf.start.vec2, e.dxf.end.vec2]))
    stacks = {x: legs for x, legs in stacks.items() if len(legs) >= 3}
    assert stacks, "the BESS units are tagged in a stack"
    crossing = [(x, i, j) for x, legs in stacks.items() for i in range(len(legs)) for j in range(i + 1, len(legs)) if legs[i].crosses(legs[j])]
    assert not crossing, crossing
    assert not [w for w in index["sheets"][0]["warnings"] if "found no room" in w]

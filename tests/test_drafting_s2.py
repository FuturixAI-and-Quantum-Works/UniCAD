"""Drafting defects of the RVUNL Heerapura bench run (RV-*) and verifier run 6 (R6-*), reproduced on the RVUNL project as the run left it."""

import json
import re
from pathlib import Path

import pytest

from unicad.catalog.store import load_catalog
from unicad.domain.project import Party, Project
from unicad.sets import render_set
from unicad.sets.render import plan_set
from unicad.sets.registry import builder
from unicad.tenants.store import drawing_profile, load_profile

DATA = Path(__file__).resolve().parent / "data"
CATALOG = load_catalog()


def rvunl(**changes) -> Project:
    p = Project.model_validate_json((DATA / "rvunl_heerapura.json").read_text(encoding="utf-8"))
    return p.model_copy(update=changes) if changes else p


def built(project: Project, kind: str, profile=None):
    profile = profile or drawing_profile(project.company_profile)
    planned = dict(plan_set(project, [kind], profile, CATALOG))[kind]
    return builder(kind).build(project, planned, planned, profile, CATALOG)


def words(doc) -> str:
    """Every TEXT on the sheet (modelspace and the blocks it draws), joined with spaces, so a phrase wrapped over lines still reads as one."""
    spaces = [doc.modelspace(), *(b for b in doc.blocks if not b.name.startswith("*"))]
    return re.sub(r"\s+", " ", " ".join(e.dxf.text for space in spaces for e in space.query("TEXT")))


@pytest.fixture(scope="module")
def project():
    return rvunl()


@pytest.fixture(scope="module")
def sheets(project, tmp_path_factory):
    out = tmp_path_factory.mktemp("rvunl")
    render_set(project, out, kinds=["register", "sld", "layout", "earthing", "lps", "strings", "cable_schedule", "bom"], formats=("dxf",), catalog=CATALOG)
    import ezdxf

    index = json.loads((out / "index.json").read_text())
    return {s["number"]: (s["kind"], words(ezdxf.readfile(out / s["files"]["dxf"]))) for s in index["sheets"]}


# ---------------- RV-M2: no EPC profile prints a neutral title block ----------------


def test_rv_m2_a_project_without_an_epc_profile_names_no_demo_company(sheets):
    for number, (_, text) in sheets.items():
        assert "UniCAD Demo EPC" not in text and "Pune" not in text and "example-epc" not in text, number
        assert "EPC COMPANY: NOT SET" in text, number


def test_rv_m2_an_assigned_profile_still_prints_its_company():
    p = rvunl(company_profile="acme")
    text = words(built(p, "sld", load_profile("acme"))[0].doc)
    assert "EPC COMPANY: NOT SET" not in text and load_profile("acme").name.upper()[:10] in text.upper()


def test_rv_m2_the_server_asks_for_the_epc_profile_up_front():
    from unicad.mcp.instructions import INSTRUCTIONS

    step0 = INSTRUCTIONS.split("\n0. ", 1)[1].split("\n1. ", 1)[0]
    assert "ask" in step0.lower() and "EPC COMPANY: NOT SET" in step0


# ---------------- RV-30 / RV-31: developer and site coordinates ----------------


class WithDeveloper(Project):
    developer: Party | None = None


def test_rv_30_the_developer_is_in_the_title_block_and_the_register():
    p = WithDeveloper.model_validate({**rvunl().model_dump(mode="json"), "developer": {"name": "Truere Knight Pvt. Ltd."}})
    for kind in ("sld", "register", "layout", "bom"):
        text = words(built(p, kind)[0].doc)
        assert "DEVELOPER" in text and "TRUERE KNIGHT PVT. LTD." in text, kind


def test_rv_31_site_coordinates_print_in_the_title_block_location(sheets):
    for number, (_, text) in sheets.items():
        assert "LAT 26.887058 N LONG 75.719544 E" in text, number


# ---------------- RV-M1: assumptions on the sheets they drive, all of them on the register ----------------


def test_rv_m1_each_sheet_prints_the_assumptions_that_drive_it(sheets):
    by_kind = {}
    for _, (kind, text) in sheets.items():
        by_kind.setdefault(kind, text)
    assert "ASSUMED INPUTS" in by_kind["earthing"] and "SOIL RESISTIVITY 100 OHM-M" in by_kind["earthing"]
    assert "LIGHTNING PROTECTION LEVEL III" in by_kind["lps"]
    assert "HEERAPURA PLOT ASSUMED 180X120" in by_kind["layout"]
    assert "FAULT LEVEL 31.5 kA / 3 s AT 415 V" in by_kind["sld"]
    assert "LENGTH ASSUMED" in by_kind["cable_schedule"]
    assert "SOIL RESISTIVITY" not in by_kind["sld"] and "FAULT LEVEL" not in by_kind["lps"]


def test_rv_m1_the_register_lists_every_assumption_of_the_set(sheets):
    register = next(t for k, t in sheets.values() if k == "register")
    for phrase in ("SOIL RESISTIVITY 100 OHM-M", "LIGHTNING PROTECTION LEVEL III", "HEERAPURA PLOT ASSUMED 180X120", "FAULT LEVEL 31.5 kA",
                   "LENGTH ASSUMED", "CELL TEMPERATURE USES THE MODULE NOCT"):  # fmt: skip
        assert phrase in register, phrase


# ---------------- RV-m3: long revision remarks wrap ----------------


def test_rv_m3_a_long_revision_remark_wraps_instead_of_truncating(sheets, project):
    long = next(r.remarks for r in project.revisions if len(r.remarks) > 55)
    assert "SINGLE-LINE DETAILS ADDED" in long.upper()
    sld = next(t for k, t in sheets.values() if k == "sld")
    assert "SINGLE-LINE DETAILS ADDED" in sld
    warnings = [w for b in built(project, "sld") for w in b.warnings]
    assert not any("truncated" in w for w in warnings), warnings


# ---------------- RV-m4: the BOM states the client-scope equipment it leaves out ----------------


def test_rv_m4_the_bom_names_client_scope_items_left_out(sheets):
    bom = " ".join(t for k, t in sheets.values() if k == "bom")
    assert "NOT IN THIS BOM" in bom and "POI-01 CLIENT MAIN BOARD PANEL" in bom


# ---------------- 11.6: the company layer map on every sheet kind, the schedule and BOM exports included ----------------


def test_the_layer_map_applies_to_exported_schedule_and_bom_pages():
    from unicad.drafting.sheets.model import LayerSpec
    from unicad.schedules import LengthMode, build_bom, build_schedule
    from unicad.schedules.dxf import render_bom, render_schedule

    p = rvunl()
    profile = load_profile("default").model_copy(update={"version": 2, "layer_map": {"G-TEXT": LayerSpec(name="CO-TABLE-TEXT")}})
    schedule = build_schedule(p, CATALOG, LengthMode.AUTO)
    docs = [*render_schedule(p, schedule, profile)[0].docs, *render_bom(p, build_bom(p, CATALOG, schedule), profile).docs]
    for doc in docs:
        layers = {e.dxf.layer for e in doc.modelspace()}
        assert "G-TEXT" not in layers and "CO-TABLE-TEXT" in layers


# ---------------- RV-M5: standard node attributes drawn on the SLD; the relay drawn with its ANSI numbers ----------------

LOGGED_ATTRIBUTES = {  # the network_update calls of the bench run (log.jsonl) before the agent moved the data into titles
    "acdb": ("ACCB: 12 IN / 1 OUT, IP65 OUTDOOR", {"busbar": "3P+N, 50 Hz, 2500 A Al", "fault_level": "31.5 kA / 3 s", "enclosure": "IP65 outdoor"}),
    "solar_ct": ("CT 2500/1 A", {"core_1": "cl 0.2s 15 VA", "core_2": "PS", "core_3": "cl 0.2s 15 VA"}),
    "pt": (None, {"core_1": "cl 0.2 15 VA", "core_2": "cl 3P 15 VA"}),
    "relay": ("NUMERICAL RELAY 50/51/67/67N/27/59/50N/51N", {"protocol": "IEC 61850"}),
}


def rvunl_with_logged_attributes() -> Project:
    data = rvunl().model_dump(mode="json")
    for n in data["network"]["nodes"]:
        if n["id"] in LOGGED_ATTRIBUTES:
            title, attrs = LOGGED_ATTRIBUTES[n["id"]]
            n["attributes"] = attrs
            if title:
                n["title"] = title
            else:
                n.pop("title", None)
    return Project.model_validate(data)


def test_rv_m5_node_attributes_are_drawn_on_the_sld():
    text = words(built(rvunl_with_logged_attributes(), "sld")[0].doc)
    for phrase in ("BUSBAR 3P+N, 50 Hz, 2500 A AL", "FAULT LEVEL 31.5 kA / 3 S", "C1 CL 0.2S 15 VA", "C2 PS", "C3 CL 0.2S 15 VA", "C1 CL 0.2 15 VA",
                   "C2 CL 3P 15 VA", "IEC 61850"):  # fmt: skip
        assert phrase in text, phrase


def test_rv_m5_the_relay_symbol_carries_its_ansi_numbers():
    doc = built(rvunl_with_logged_attributes(), "sld")[0].doc
    relay = [i for i in doc.modelspace().query("INSERT") if i.dxf.name.startswith("UC_SLD")]
    block = doc.blocks.get(relay[0].dxf.name)
    names = {e.dxf.name for e in block.query("INSERT")}
    assert any("RELAY_GROUP" in n.upper() for n in names), sorted(names)
    texts = {e.dxf.text for e in block.query("TEXT")} | {a.dxf.text for i in block.query("INSERT") for a in i.attribs}
    assert {"50", "51", "67", "67N", "27", "59"} <= texts, texts


def test_rv_m5_typed_fields_read_before_attributes():
    from unicad.domain.network import Node
    from unicad.drafting.sld.annotate import standard_attributes

    class CT(Node):
        ratio: str | None = None

    ct = CT(id="ct", tag="CT-01", kind="ct", ratio="2500/1 A", attributes={"ratio": "100/1 A", "core_1": "cl 0.2s 15 VA"})
    assert standard_attributes(ct) == ("RATIO 2500/1 A", "C1 CL 0.2S 15 VA")


# ---------------- RV-M4: RS-485 links drawn in their own line style with a legend entry ----------------


def test_rv_m4_the_inverter_to_logger_link_is_drawn_as_a_communication_line():
    b = built(rvunl(), "sld")[0]
    assert not any("c_inv_dlog" in w for w in b.warnings), b.warnings
    block = next(blk for blk in b.doc.blocks if blk.name.startswith("UC_SLD"))
    comm = [e for e in block if e.dxf.layer == "E-SLD-COMM"]
    assert len(comm) >= 2, "the inverter and the meter links to the logger"
    assert b.doc.layers.get("E-SLD-COMM").dxf.linetype != b.doc.layers.get("E-SLD-CTRL").dxf.linetype
    legend_lines = [e for e in b.doc.modelspace().query("LINE") if e.dxf.layer == "E-SLD-COMM"]
    text = words(b.doc)
    assert legend_lines and "COMMUNICATION LINK (DATA CABLE)" in text
    assert "RS-485 DAISY CHAIN THROUGH ALL 12 INVERTERS TO THE DATA LOGGER" in text


# ---------------- RV-M3: the LT plant layout shows the ACCB, the client panel and their cable route, and no stations ----------------


def test_rv_m3_a_stationless_lt_plant_draws_no_indicative_stations_and_shows_the_accb():
    doc = built(rvunl(), "layout")[0].doc
    names = {i.dxf.name for i in doc.modelspace().query("INSERT")}
    text = words(doc)
    assert not any("INVERTER" in n.upper() and "LAYOUT" in n.upper() for n in names), names
    assert "INDICATIVE" not in text and "INVERTER STATION" not in text
    assert "ACCB-01" in text and "BLOCK-07" in text


def test_rv_m3_panels_the_layout_places_are_drawn_with_their_cable_route(monkeypatch):
    import unicad.routing
    from types import SimpleNamespace
    from unicad.domain.layout import PlacedEquipment, Point

    p = rvunl()
    layout = p.array_layout
    accb = PlacedEquipment(node="acdb", tag="ACCB-01", kind="acdb", at=Point(x=12, y=60), basis="test")
    mbp = PlacedEquipment(node="poi", tag="POI-01", kind="poi_grid", at=Point(x=8, y=20), basis="test")
    p = p.model_copy(update={"array_layout": layout.model_copy(update={"equipment": (*layout.equipment, accb, mbp)})})
    route = SimpleNamespace(edge="e_ct_poi", from_tag="ACCB-01", to_tag="POI-01", points=((12, 60), (12, 20), (8, 20)), length_m=44.0)
    monkeypatch.setattr(unicad.routing, "panel_routes", lambda project: [route], raising=False)
    doc = built(p, "layout")[0].doc
    text = words(doc)
    assert "ACCB-01" in text and "POI-01" in text
    assert "ACCB-01 TO POI-01: 9R x 3.5C x 300 sq.mm" in text and "CABLE ROUTE BETWEEN PANELS" in text
    assert [e for e in doc.modelspace() if e.dxf.layer == "L-CABLE-ROUTE" and e.dxftype() == "LWPOLYLINE"]


def test_rv_m3_a_plant_with_stations_keeps_them():
    from test_fence_clearance import load

    text = words(built(load("thar_45mw"), "layout")[0].doc)
    assert "INVERTER STATION (INDICATIVE LOCATION)" in text

"""Drafting defects of verifier run 6 (R6-m9, R6-m3, R5-m4, R6-m2, R6-m5, R6-m7, n21, m5)."""

import ezdxf
import json
import pytest
import re
from pathlib import Path
from shapely.geometry import LineString
from shapely.geometry import box as shapely_box
from test_design import fixture
from test_sets_e2e2 import scenario
from unicad.catalog.store import load_catalog
from unicad.domain.network import NodeKind
from unicad.domain.network import NodeKind as NK
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import PaperSize
from unicad.drafting.sheets.text import text_width
from unicad.drafting.sld import annotate
from unicad.drafting.sld.annotate import system_parameters
from unicad.drafting.string_diagram import plan_types
from unicad.drafting.tables.draw import ROW_PAD_Y
from unicad.drafting.tables.plan import plan_table
from unicad.schedules import LengthMode, build_schedule
from unicad.schedules.dxf import schedule_tables
from unicad.sets.registry import builder
from unicad.sets.render import load_profile
from unicad.sets.render import plan_set
from unicad.sets.schedules import ScheduleBuilder, table_pages
from unicad.store import parse_project



ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_catalog()


def rooftop_with_isolator_ways(ways: int, spec: str = "abb-otdc100gv22"):
    data = json.loads((ROOT / "fixtures" / "projects" / "rooftop_500kwp.json").read_text(encoding="utf-8"))
    for n in data["network"]["nodes"]:
        if n["id"] == "dciso":
            n["spec"] = spec
            n["attributes"] = {"isolator_ways": ways}
    return parse_project(json.dumps(data), CATALOG)


def test_r6_m9_sld_shows_every_four_pole_isolator_the_bom_counts():
    p = rooftop_with_isolator_ways(10)
    node = p.network.node("dciso")
    texts = annotate.node_texts(p.network, node, CATALOG)
    assert texts["RATING"].startswith("5 x ") and "4P" in texts["RATING"]


def test_r6_m9_a_single_circuit_isolator_keeps_its_plain_rating():
    p = rooftop_with_isolator_ways(1, "abb-otdc100gv11")
    texts = annotate.node_texts(p.network, p.network.node("dciso"), CATALOG)
    assert " x " not in texts["RATING"].split(",")[0]


def test_r6_m9_string_diagram_lists_the_isolator_devices():
    p = rooftop_with_isolator_ways(10)
    types, _ = plan_types(p, CATALOG)
    devices = [d for t in types for d in t.devices]
    assert any("DC-ISO" in d and "5 x 4P" in d for d in devices), devices


# ---------------- R6-m3: BESS units tagged one by one, inside the fence, not on the PV tables ----------------


@pytest.fixture(scope="module")
def hybrid():
    return scenario("kutch-hybrid")


utility = fixture("utility_50mwac")


def build_kind(project, profile, kind):
    planned = dict(plan_set(project, [kind], profile, CATALOG))[kind]
    return builder(kind).build(project, planned, planned, profile, CATALOG)


def bess_tags(project) -> list[str]:
    return [e.tag for e in project.array_layout.equipment if e.kind != "inverter"]


def text_entities(doc, tag):
    return [t for t in doc.modelspace().query("TEXT") if t.dxf.text.split(" (")[0] == tag]


@pytest.mark.parametrize("kind", ["layout", "earthing"])
def test_r6_m3_each_bess_unit_has_its_own_tag_and_no_comma_list_label(hybrid, kind):
    project, profile = hybrid
    built = build_kind(project, profile, kind)
    assert len(built) == 1
    doc = built[0].doc
    tags = bess_tags(project)
    assert len(tags) >= 4
    for tag in tags:
        assert len(text_entities(doc, tag)) == 1, tag
    assert not [t.dxf.text for t in doc.modelspace().query("TEXT") if sum(x in t.dxf.text for x in tags) > 1]
    assert not [w for w in built[0].warnings if "found no room" in w]


@pytest.mark.parametrize("kind", ["layout", "earthing"])
def test_r6_m3_bess_tags_stay_on_the_plant_side_of_the_fence(hybrid, kind):
    project, profile = hybrid
    doc = build_kind(project, profile, kind)[0].doc
    fence = [LineString([(x, y) for x, y, *_ in e.get_points()]) for e in doc.modelspace().query("LWPOLYLINE") if e.dxf.layer == "L-BOUNDARY" and len(e) >= 2]
    assert fence
    boxes = {}
    for tag in bess_tags(project):
        t = text_entities(doc, tag)[0]
        x, y = t.dxf.insert.x, t.dxf.insert.y
        boxes[tag] = shapely_box(x, y, x + text_width(t.dxf.text, t.dxf.height), y + t.dxf.height)
    centre = (sum(b.centroid.x for b in boxes.values()) / len(boxes), sum(b.centroid.y for b in boxes.values()) / len(boxes))
    for tag, b in boxes.items():
        assert not any(f.intersects(b) for f in fence), tag
        assert not any(f.intersects(LineString([centre, (b.centroid.x, b.centroid.y)])) for f in fence), tag


# ---------------- R5-m4: SLD system parameters show the BESS as built ----------------


def built_bess(project):
    net = project.network
    kva = sum(CATALOG.get(n.spec).rated_kva * net.instance_count(n.id) for n in net.by_kind(NK.PCS))
    kwh = sum(CATALOG.get(n.spec).energy_kwh * net.instance_count(n.id) for n in net.by_kind(NK.BATTERY))
    return kva, kwh


def test_r5_m4_system_parameters_state_the_bess_power_and_energy_as_built(hybrid):
    project, _ = hybrid
    kva, kwh = built_bess(project)
    rows = dict(system_parameters(project, CATALOG))
    assert rows["BESS POWER AS BUILT (PCS RATED)"] == f"{kva / 1000:,.3f} MVA"
    assert rows["BESS ENERGY AS BUILT"] == f"{kwh / 1000:,.3f} MWh"


def test_r5_m4_the_request_is_shown_beside_the_build_when_whole_units_differ(hybrid):
    project, _ = hybrid
    data = json.loads(project.model_dump_json())
    for n in data["network"]["nodes"]:
        if n["kind"] == "pcs":
            n["attributes"] = {**n.get("attributes", {}), "bess_kw_requested": 9000.0, "bess_kwh_requested": 18000.0}
    asked = parse_project(json.dumps(data), CATALOG)
    rows = dict(system_parameters(asked, CATALOG))
    assert rows["BESS AS REQUESTED"] == "9.000 MW / 18.000 MWh"
    assert rows["BESS ENERGY AS BUILT"] != "18.000 MWh"


def test_r5_m4_inverter_count_excludes_the_bess_pcs(hybrid):
    project, _ = hybrid
    net = project.network
    pv = sum(net.instance_count(n.id) for n in net.by_kind(NK.INVERTER))
    pcs = sum(net.instance_count(n.id) for n in net.by_kind(NK.PCS))
    rows = dict(system_parameters(project, CATALOG))
    assert rows["INVERTERS"].split(" x ")[0] == str(pv) and pcs and rows["BESS PCS"].split(" x ")[0] == str(pcs)


# ---------------- R6-m2: DC cable schedule pages are filled and 'PART k OF n' is the title block's 'k of n' ----------------


def test_r6_m2_dc_schedule_pages_are_filled():
    schedule = build_schedule(utility, CATALOG, LengthMode.AUTO)
    (_, dc), _ = schedule_tables(schedule)
    m = metrics_for(PaperSize.A3)
    plan = plan_table(dc, m.drawing_area, m.th, m.pad_x, ROW_PAD_Y * m.th)
    rows = [sum(1 for r in page if hasattr(r, "cells")) for page in plan.pages]
    assert len(rows) >= 2 and max(rows) >= 30
    assert all(n >= 0.8 * max(rows) for n in rows[:-1]), rows
    assert not any(s.page_break_before for s in dc.sections)
    assert len(rows) <= -(-sum(rows) // max(rows)) + 1


def test_r6_m2_part_numbers_in_titles_are_the_title_block_numbers():
    profile = load_profile(utility.company_profile)
    built = build_kind(utility, profile, "cable_schedule")
    assert len(built) > 3 and any("PART" in b.sheet.title for b in built)
    for b in built:
        texts = {e.dxf.text for e in b.doc.modelspace().query("TEXT")}
        found = re.search(r"\(PART (\d+) OF (\d+)\)$", b.sheet.title)
        k, n = found.groups() if found else ("1", "1")
        assert f"{k} of {n}" in texts, (b.sheet.title, sorted(t for t in texts if " of " in t))

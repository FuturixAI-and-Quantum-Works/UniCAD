"""Drafting defects of verifier run 6: R6-m9 isolators, R6-m3 BESS tags, R5-m4 BESS as built, R6-m2 schedule pages, R6-m7 unreadable SLDs."""

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
from unicad.domain.network import NodeKind as NK
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import PaperSize
from unicad.drafting.sheets.text import text_width
from unicad.drafting.sld import SldOptions
from unicad.drafting.sld import annotate
from unicad.drafting.sld.annotate import system_parameters
from unicad.drafting.string_diagram import plan_types
from unicad.drafting.tables.draw import ROW_PAD_Y
from unicad.drafting.tables.plan import plan_table
from unicad.schedules import LengthMode, build_schedule
from unicad.schedules.dxf import schedule_tables
from unicad.sets import render_set
from unicad.sets.numbering import Numberer
from unicad.sets.registry import BUILDERS
from unicad.sets.registry import builder
from unicad.sets.render import load_profile, plan_set
from unicad.sets.sld import SldBuilder
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
    for i, b in enumerate(built, start=1):
        texts = {e.dxf.text for e in b.doc.modelspace().query("TEXT")}
        assert f"{i} of {len(built)}" in texts, (b.sheet.title, sorted(t for t in texts if " of " in t))
        if found := re.search(r"\(PART (\d+) OF (\d+)\)$", b.sheet.title):
            assert found.groups() == (str(i), str(len(built))), b.sheet.title


# ---------------- R6-m7: a diagram drawn below the readable scale is flagged on the sheet and in the set ----------------


def test_r6_m7_an_unreadable_sld_says_so_on_the_sheet_and_in_the_set_warnings(monkeypatch, tmp_path):
    monkeypatch.setitem(BUILDERS, "sld", SldBuilder(SldOptions(min_scale=2.0)))
    rooftop = fixture("rooftop_500kwp")
    index = render_set(rooftop, tmp_path, kinds=("sld",), formats=("dxf",), catalog=CATALOG)
    flagged = [e for e in index.sheets if e["readable"] is False]
    assert flagged
    for e in flagged:
        doc = ezdxf.readfile(tmp_path / e["files"]["dxf"])
        assert any(t.dxf.text.startswith("WARNING: THIS DIAGRAM IS DRAWN AT SCALE") for t in doc.modelspace().query("TEXT")), e["number"]
        assert any(w.startswith(f"{e['number']}: NOT READABLE") for w in index.warnings)


def test_r6_m7_a_readable_sld_carries_no_warning(tmp_path):
    index = render_set(fixture("rooftop_500kwp"), tmp_path, kinds=("sld",), formats=("dxf",), catalog=CATALOG)
    assert all(e["readable"] for e in index.sheets)
    assert not [w for w in index.warnings if "NOT READABLE" in w]
    doc = ezdxf.readfile(tmp_path / index.sheets[0]["files"]["dxf"])
    assert not any(t.dxf.text.startswith("WARNING: THIS DIAGRAM") for t in doc.modelspace().query("TEXT"))


def test_r6_m7_a_page_the_split_plan_calls_readable_but_the_sheet_is_not_is_planned_again_with_a_margin(monkeypatch):
    """B SLD 001: plan_pages found one readable page (0.911) but the sheet, which also draws the off-sheet markers, came to 0.84 on A0 and was
    issued as it was. The planner now asks plan_pages again with a minimum raised by the shortfall."""
    import unicad.sets.sld as sets_sld

    seen = []
    real = sets_sld.plan_pages

    def spy(network, catalog, width, height, options, scope=None):
        seen.append(options.min_scale)
        pages = real(network, catalog, width, height, options, scope)
        return pages[:1] if options.min_scale <= 3.0 else pages

    monkeypatch.setattr(sets_sld, "plan_pages", spy)
    profile = load_profile(utility.company_profile)
    planned = SldBuilder(SldOptions(min_scale=3.0)).plan(utility, profile, CATALOG, Numberer.for_project(utility, profile))
    assert planned and len(seen) >= 2
    assert seen[0] == 3.0 and seen[1] > seen[0]

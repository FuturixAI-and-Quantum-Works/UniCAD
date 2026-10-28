"""Earthing and lightning completeness (close-e2e-3: L1, n21, n26, n16, n18, C4, C3 earthing side, A3 labels), on the replayed scenarios and the
Rajasthan and Pune samples."""

import re

import ezdxf
import pytest
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from test_earthing import CATALOG, ROOT, _spec
from test_sets_e2e2 import scenario

from unicad.arrays.geometry import table_corners
from unicad.domain.layout import Point as SitePoint
from unicad.domain.project import EarthingBasis
from unicad.domain.site import FeatureKind, SiteFeature
from unicad.drafting.earthing_plan import earthing_notes, render_earthing_sheet, render_lps_sheet
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.earthing import ConductorRole, PitRole, components, design_earthing
from unicad.earthing.design import conductor_mass_kg
from unicad.earthing.layout import FIELD_BOXES
from unicad.earthing.samples import pune, rajasthan, with_basis
from unicad.earthing.sizing import FLAT_BY_NAME
from unicad.lps import design_lps, equipment_footprint
from unicad.lps.design import MAST_CLEAR_M, class_coverage, placed_equipment, targets
from unicad.lps.site import switchyard_footprint
from unicad.rules import Severity, run_rules

PROFILE = CompanyProfile.load(ROOT / "profiles" / "default.json")


def _tables(p):
    return unary_union([Polygon(table_corners(t)) for t in p.array_layout.table_by_id.values()])


def _texts(doc):
    return [t.dxf.text for t in doc.modelspace().query("TEXT")]


# --- L1: lightning cover for every structure and equipment area -------------------------------


def test_l1_the_hybrid_plant_is_covered_class_by_class_including_switchyard_and_bess():
    p, _ = scenario("kutch-hybrid")
    d = design_lps(p)
    cov = {c.kind: c for c in d.coverage()}
    assert {"table", "station", "switchyard", "bess", "field_inverter"} <= set(cov)
    assert all(c.covered == c.total > 0 for c in cov.values()), cov
    assert cov["bess"].total == sum(1 for e in p.array_layout.equipment if e.kind != "inverter")
    assert cov["table"].total == len(p.array_layout.table_by_id)
    f = [x for x in run_rules(p, CATALOG, ["lps"]).by_rule("lps.coverage") if x.severity is Severity.INFO]
    assert len(f) == 1 and all(f"{c.label} {c.total}/{c.total}" in f[0].message for c in cov.values())


def test_l1_every_placed_footprint_and_the_switchyard_lie_wholly_inside_the_union_of_protection_circles():
    p, _ = scenario("kutch-hybrid")
    d = design_lps(p)
    zones = unary_union([Point(t.at.x, t.at.y).buffer(d.radius_m, 64) for t in d.terminals])
    yard = switchyard_footprint(p)
    assert yard is not None
    shapes = [box(*yard)] + [equipment_footprint(kind, at) for _, _, kind, at in placed_equipment(p)]
    shapes += [equipment_footprint("station", (b.station.x, b.station.y)) for b in p.array_layout.blocks if b.station is not None]
    assert len(shapes) > 60
    assert [s.bounds for s in shapes if s.difference(zones).area > 1e-6] == []


def test_l1_the_coverage_is_reported_by_mcp_with_covered_over_total_per_class():
    p, _ = scenario("kutch-hybrid")
    from unicad.mcp.tools.earthing import _lps_json

    out = _lps_json(design_lps(p))
    by = {c["class"]: c for c in out["coverage"]}
    assert by["MV switchyard"] == {"class": "MV switchyard", "covered": 1, "total": 1}
    assert by["BESS units"]["covered"] == by["BESS units"]["total"] > 0


def test_l1_a_bess_unit_outside_every_circle_is_counted_uncovered():
    p, _ = scenario("kutch-hybrid")
    d = design_lps(p)
    far = [e.model_copy(update={"at": e.at.model_copy(update={"x": e.at.x + 5000})}) if e.kind == "battery" else e for e in p.array_layout.equipment]
    moved = p.model_copy(update={"array_layout": p.array_layout.model_copy(update={"equipment": tuple(far)})})
    cov = {c.kind: c for c in class_coverage(targets(moved), [(t.at.x, t.at.y) for t in d.terminals], d.radius_m)}
    assert cov["bess"].covered < cov["bess"].total


def test_l1_a_building_on_a_ground_plant_is_a_target_class_and_covered():
    p, _ = rajasthan()
    site = p.site.geometry
    ring = ((350, 150), (362, 150), (362, 158), (350, 158))
    hut = SiteFeature(id="control-room", kind=FeatureKind.BUILDING, polygon=tuple(SitePoint(x=x, y=y) for x, y in ring))
    q = p.model_copy(update={"site": p.site.model_copy(update={"geometry": site.with_features([*site.features, hut])})})
    cov = {c.kind: c for c in design_lps(q).coverage()}
    assert cov["building"].total == 1 and cov["building"].covered == 1
    assert cov["switchyard"].total == 1 and cov["switchyard"].covered == 1


# --- n21: masts stand in the gaps, never on or against a table --------------------------------


@pytest.mark.parametrize("scenario_id", ["thar-40mw", "kutch-hybrid"])
def test_n21_ground_masts_and_their_lightning_pits_stand_clear_of_every_table(scenario_id):
    p, _ = scenario(scenario_id)
    d = design_earthing(p, catalog=CATALOG)
    tables = _tables(p)
    assert len(d.lps.terminals) > 5
    for t in d.lps.terminals:
        assert tables.distance(Point(t.at.x, t.at.y)) >= MAST_CLEAR_M - 0.01, t.tag
    for pit in d.layout.pits:
        if pit.role is PitRole.LE:
            assert not tables.intersects(Point(pit.at.x, pit.at.y)), pit.tag


def test_n21_the_sample_ground_plant_masts_are_clear_and_still_cover_everything():
    p, _ = rajasthan()
    d = design_lps(p)
    assert d.covered_all and min(_tables(p).distance(Point(t.at.x, t.at.y)) for t in d.terminals) >= MAST_CLEAR_M - 0.01


# --- n26: array conductors are sized for the fault that can flow in the array ------------------


def test_n26_table_rows_are_sized_for_the_inverter_fault_while_the_main_keeps_the_plant_fault():
    p, _ = scenario("thar-40mw")
    d = design_earthing(p, catalog=CATALOG)
    assert d.sizes.main == "75x6" and d.sizes.array == "25x6"
    assert 0 < d.array_fault.value < d.basis.fault_current_ka * 1000
    assert d.array_fault.source and "IEA PVPS" in d.array_fault.source
    assert FLAT_BY_NAME[d.sizes.array].area_mm2 >= next(s for s in d.steps if s.name == "array_conductor_area").value
    kinds = {q.item for q in d.quantities}
    assert "flat.25x6.array" in kinds and not any(k.startswith("flat.75x6.array") for k in kinds)
    all_main = sum(r.length_m * FLAT_BY_NAME[d.sizes.main].area_mm2 * 1e-6 * 7850 for r in d.layout.runs)
    assert conductor_mass_kg(d.layout) < 0.6 * all_main
    assert any("inverter fault contribution" in a for a in d.assumptions)


def test_n26_the_conductor_rule_checks_each_role_against_its_own_fault():
    p, _ = scenario("thar-40mw")
    f = {x.subjects[0]: x for x in run_rules(p, CATALOG, ["earthing"]).by_rule("earthing.conductor_size")}
    assert f["conductor.main"].measured > f["conductor.array"].measured
    assert f["conductor.array"].severity is Severity.INFO and "inverter fault contribution" in f["conductor.array"].message
    assert f["conductor.main"].severity is Severity.INFO


def test_n26_risers_and_station_rings_stay_at_the_main_conductor_size():
    p, _ = scenario("thar-40mw")
    d = design_earthing(p, catalog=CATALOG)
    assert {r.size for r in d.layout.runs if r.role is ConductorRole.MAIN} == {d.sizes.main}
    assert {r.size for r in d.layout.runs if r.role is ConductorRole.ARRAY} == {d.sizes.array}


def test_n26_a_plant_without_inverters_sizes_the_rows_for_the_plant_fault():
    p, _ = rajasthan()
    bare = p.model_copy(update={"network": p.network.model_copy(update={"nodes": (), "edges": (), "groups": ()})})
    d = design_earthing(bare, catalog=CATALOG)
    assert d.sizes.array == d.sizes.main and d.array_fault is None
    assert any("no inverter" in a for a in d.assumptions)


# --- n16: the pit count says what governs it ---------------------------------------------------


def test_n16_the_calculation_table_shows_the_governing_criterion_and_no_zero_current_density_line():
    p, _ = scenario("thar-40mw")
    d = design_earthing(p, catalog=CATALOG)
    assert d.pits_by_resistance == d.pits_by_current == 0 and d.pits_by_practice == d.pit_count
    assert "practice" in d.pits_governing
    doc = ezdxf.new("R2018", setup=True)
    render_earthing_sheet(doc, p, CATALOG, d, _spec(PaperSize.A0, earthing_notes(d)), PROFILE)
    texts = _texts(doc)
    assert not [t for t in texts if "NEEDED BY CURRENT DENSITY" in t]
    row = next(t for t in texts if t.startswith("PITS PROVIDED / REQUIRED BY"))
    assert "EQUIPMENT / ROW PRACTICE" in row and f"{d.pit_count} / {d.pit_count}" in texts


def test_n16_resistance_governs_when_the_target_needs_more_pits_than_practice():
    p, _ = pune()
    bad = with_basis(p, EarthingBasis(soil_resistivity_ohm_m=400.0, fault_current_ka=10.0, fault_clearing_s=0.5, target_resistance_ohm=0.5), None)
    d = design_earthing(bad, catalog=CATALOG)
    assert d.pits_by_resistance > d.pits_by_practice and d.pits_governing == "earth resistance target"


# --- n18: Detail A shows only equipment at the station -----------------------------------------


def test_n18_dc_junction_boxes_are_field_equipment_not_station_equipment():
    p, _ = scenario("sgt-chakan")
    d = design_earthing(p, catalog=CATALOG)
    kinds = {it.id: it.kind for it in d.layout.items}
    assert any(k == "dc_fuse_box" for k in kinds.values())
    stations = [g for g in d.layout.stations if g.kind == "station"]
    assert stations and all(kinds[i] not in {k.value for k in FIELD_BOXES} for g in stations for i in g.items)
    doc = ezdxf.new("R2018", setup=True)
    render_earthing_sheet(doc, p, CATALOG, d, _spec(PaperSize.A2, earthing_notes(d)), PROFILE)
    assert not [t for t in _texts(doc) if "DC FUSE BOX" in t and "UNLABELLED" in t]


# --- C4: the mesh quoted is the mesh laid and checked ------------------------------------------


def _laid_mesh(d):
    g = [r for r in d.layout.runs if r.role is ConductorRole.GRID]
    xs = sorted({round(r.points[0].x, 3) for r in g if r.points[0].x == r.points[1].x})
    ys = sorted({round(r.points[0].y, 3) for r in g if r.points[0].y == r.points[1].y})
    return max(max(b - a for a, b in zip(xs, xs[1:])), max(b - a for a, b in zip(ys, ys[1:])))


@pytest.mark.parametrize("surface", [True, False])
def test_c4_touch_step_quotes_the_mesh_the_design_lays(surface):
    p, _ = scenario("kutch-hybrid")
    b = p.design_basis.earthing.model_dump() | ({} if surface else {"surface_resistivity_ohm_m": None, "surface_layer_m": None})
    q = p.model_copy(update={"design_basis": p.design_basis.model_copy(update={"earthing": EarthingBasis(**b)})})
    d = design_earthing(q, catalog=CATALOG)
    assert d.grid_spacing_m == pytest.approx(_laid_mesh(d), abs=1e-3)
    assert d.layout.switchyard.spacing == pytest.approx(d.grid_spacing_m, abs=1e-3)
    msgs = [f.message for f in run_rules(q, CATALOG, ["earthing"]).by_rule("earthing.touch_step")]
    assert msgs and all(f"{d.grid_spacing_m:g} m mesh" in m for m in msgs)
    doc = ezdxf.new("R2018", setup=True)
    render_earthing_sheet(doc, q, CATALOG, d, _spec(PaperSize.A0, earthing_notes(d)), PROFILE)
    assert f"{d.grid_spacing_m:g} m MESH" in _texts(doc)


# --- C3 earthing side: BESS units earth at their stored positions outside the mat, bonded to it -


def _bess_outside_the_mat(p):
    sy = design_earthing(p, catalog=CATALOG).layout.switchyard
    moved, k = [], 0
    for e in p.array_layout.equipment:
        if e.kind == "inverter":
            moved.append(e)
            continue
        moved.append(e.model_copy(update={"at": e.at.model_copy(update={"x": sy.x1 + 40 + 10 * (k % 5), "y": sy.y0 + 12 * (k // 5)})}))
        k += 1
    return p.model_copy(update={"array_layout": p.array_layout.model_copy(update={"equipment": tuple(moved)})})


def test_c3_bess_units_have_their_own_ring_pits_and_a_bond_to_the_switchyard_mat():
    p, _ = scenario("kutch-hybrid")
    q = _bess_outside_the_mat(p)
    d = design_earthing(q, catalog=CATALOG)
    sy = d.layout.switchyard
    mat = box(sy.x0, sy.y0, sy.x1, sy.y1)
    bess = {e.node: e for e in q.array_layout.equipment if e.kind != "inverter"}
    groups = [g for g in d.layout.stations if g.kind == "bess"]
    assert groups and sum(len(g.items) for g in groups) == len(bess)
    items = {it.id: it for it in d.layout.items}
    for iid, e in bess.items():
        assert items[iid].at.x == pytest.approx(e.at.x, abs=1e-3) and items[iid].at.y == pytest.approx(e.at.y, abs=1e-3) and items[iid].placed
        assert not mat.contains(Point(e.at.x, e.at.y))
        assert d.bonded[iid] >= items[iid].connections
    assert d.unbonded == () and len(components(d.layout)) == 1
    assert not [w for w in d.warnings if "inside the switchyard earth mat" in w]
    for g in groups:
        pits = [x for x in d.layout.pits if x.tag in g.pits]
        assert sum(1 for x in pits if x.role is PitRole.EPB) >= 2 * sum(1 for i in g.items if items[i].kind == "transformer")
        assert not mat.intersects(box(*g.ring))
    links = [r for r in d.layout.runs if "switchyard" in r.links and r.links[0].startswith("bess-area")]
    assert len(links) == len(groups)
    assert all(LineString([(x.x, x.y) for x in r.points]).distance(mat.exterior) < 2e-3 for r in links)


def test_c3_a_bess_unit_standing_inside_the_mat_is_flagged():
    p, _ = scenario("kutch-hybrid")
    d = design_earthing(p, catalog=CATALOG)
    sy = d.layout.switchyard
    inside = [e for e in p.array_layout.equipment if e.kind != "inverter" and sy.x0 <= e.at.x <= sy.x1 and sy.y0 <= e.at.y <= sy.y1]
    assert len([w for w in d.warnings if "inside the switchyard earth mat" in w]) == len(inside)


def test_c3_the_switchyard_mat_stays_at_the_layout_location_and_says_when_bess_units_stand_on_it():
    """M1 (close-e2e-4): the earthing mat never moves off the layout's one switchyard location (where routing measures the MV feeders to);
    BESS units moved onto it are named in a warning with the fix, and flagged as inside the mat."""
    p, _ = scenario("kutch-hybrid")
    sy = design_earthing(p, catalog=CATALOG).layout.switchyard
    cx, cy = (sy.x0 + sy.x1) / 2, (sy.y0 + sy.y1) / 2
    assert (cx, cy) == pytest.approx((p.array_layout.switchyard.at.x, p.array_layout.switchyard.at.y), abs=1e-6)
    moved, k = [], 0
    for e in p.array_layout.equipment:
        if e.kind != "inverter":
            e = e.model_copy(update={"at": e.at.model_copy(update={"x": cx - 20 + 10 * (k % 5), "y": cy - 5 + 10 * (k // 5)})})
            k += 1
        moved.append(e)
    q = p.model_copy(update={"array_layout": p.array_layout.model_copy(update={"equipment": tuple(moved)})})
    d = design_earthing(q, catalog=CATALOG)
    assert ((d.layout.switchyard.x0 + d.layout.switchyard.x1) / 2, (d.layout.switchyard.y0 + d.layout.switchyard.y1) / 2) == pytest.approx((cx, cy), abs=1e-6)
    assert k and any("overlaps BESS units" in w and "re-run layout_place" in w for w in d.warnings), d.warnings
    assert [w for w in d.warnings if "inside the switchyard earth mat" in w]


# --- A3: placed inverters carry their tags on the earthing sheet -------------------------------


def test_a3_field_inverters_are_labelled_with_their_tags_and_their_pits():
    p, _ = scenario("kutch-hybrid")
    d = design_earthing(p, catalog=CATALOG)
    doc = ezdxf.new("R2018", setup=True)
    sheet = render_earthing_sheet(doc, p, CATALOG, d, _spec(PaperSize.A0, earthing_notes(d)), PROFILE)
    shown = " ".join(_texts(doc))
    inverters = [e for e in p.array_layout.equipment if e.kind == "inverter"]
    assert len(inverters) > 50
    assert [e.tag for e in inverters if e.tag not in shown] == []
    labelled = re.findall(r"INV-[0-9A-Z-]+ \((EP\d+)\)", shown)
    assert len(labelled) > 0.8 * len(inverters)
    assert not [w for w in sheet.warnings if "inverter" in w.lower()]


def test_a3_rooftop_inverters_are_labelled_too():
    p, _ = scenario("sgt-chakan")
    d = design_earthing(p, catalog=CATALOG)
    doc = ezdxf.new("R2018", setup=True)
    render_earthing_sheet(doc, p, CATALOG, d, _spec(PaperSize.A2, earthing_notes(d)), PROFILE)
    shown = " ".join(_texts(doc))
    assert [e.tag for e in p.array_layout.equipment if e.tag not in shown] == []


def test_l1_the_lps_sheet_lists_each_class_with_covered_of_total():
    p, _ = scenario("kutch-hybrid")
    d = design_lps(p)
    doc = ezdxf.new("R2018", setup=True)
    render_lps_sheet(doc, p, CATALOG, d, _spec(PaperSize.A0, ()), PROFILE)
    texts = _texts(doc)
    for c in d.coverage():
        assert f"{c.label.upper()} COVERED" in texts and f"{c.covered} OF {c.total}" in texts
    assert "ALL TARGETS COVERED" in texts

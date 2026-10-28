"""Earthing: worked sizing examples against published hand calculations, the Rajasthan and Pune designs, rules, sheets and MCP tools."""

import math
import os
import re
import subprocess
from datetime import date
from pathlib import Path

import ezdxf
import pytest
from test_mcp import CREATE, session_test

from unicad.catalog.store import load_catalog
from unicad.domain.project import EarthingBasis
from unicad.drafting.earthing_plan import earthing_notes, render_earthing_sheet
from unicad.drafting import dxfrules
from unicad.drafting.earthing_plan import render_lps_sheet
from unicad.drafting.earthing_plan.sheet import ROLE_STYLE, clip_circle, expand_ranges, tag_ranges
from unicad.drafting.labels import overlapping
from unicad.drafting.sheets.canvas import Region
from unicad.drafting.site_plan.sheet import FILL_FRACTION, SCALES
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, PaperSize, SheetSpec, TitleBlockData
from unicad.earthing import PitRole, bonding, components, design_earthing, select_flat
from unicad.earthing.samples import pune, rajasthan, with_basis
from unicad.earthing.sizing import Grid, conductor_area, electrode_current, grid_check, ieee80_area, pipe_resistance, sverak, tolerable
from unicad.rules import Severity, run_rules

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog()
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
SITES = ROOT / "fixtures" / "sites"
LIBRECAD_LINETYPES = {"CONTINUOUS", "CENTER", "CENTER2", "DASHED", "DASHED2", "DASHEDX2", "DASHDOT", "DASHDOT2", "DOT", "DIVIDE", "HIDDEN", "BYLAYER"}


# --- worked examples -------------------------------------------------------------------------


def test_is3043_conductor_area_matches_the_published_example():
    # J. Parmar, EEP part 2: GI, 50 kA for 1 s, k = 80 -> 625 mm2.
    assert conductor_area(50, 1).value == 625.0
    assert conductor_area(50, 1, corrosion_allowance=0.2).value == 750.0
    assert select_flat(625).name == "75x10" and select_flat(156.25).name == "50x6" and select_flat(75).name == "25x3"


def test_ieee80_kf_cross_check():
    # A_kcmil = I Kf sqrt(t) with Kf 28.96 for zinc-coated steel; 1.974 kcmil per mm2.
    assert ieee80_area(10, 0.2).value == pytest.approx(10 * 28.96 * math.sqrt(0.2) / 1.974, abs=0.05)
    assert ieee80_area(12.5, 1).value == pytest.approx(183.4, abs=0.05)


def test_is3043_pipe_resistance_and_current_density():
    # 100 mm x 3 m pipe in 72.44 ohm m soil (EEP part 1, with the natural log as corrected in its comments): 18.4 ohm.
    assert pipe_resistance(72.44, 3.0, 100.0).value == pytest.approx(18.40, abs=0.01)
    # 7.57 x 1000 / sqrt(72.44 x 1) = 889.4 A/m2 over pi x 0.1 x 3 m2: 838 A per pipe.
    assert electrode_current(72.44, 1.0, 3.0, 100.0).value == pytest.approx(838.3, abs=0.5)


def test_ieee80_grid_reproduces_the_iec_calc_hand_check():
    # IECCalc 'Earthing grid: touch voltage decides': 60 x 40 m, 10 m mesh, h 0.6 m, d 12 mm, 12 rods x 3 m, rho 80 (uniform),
    # 100 mm rock at 2500 ohm m, ts 0.5 s, 70 kg, IG 6280 A.
    cs, et, es = tolerable(80, 2500, 0.1, 0.5, body_kg=70)
    assert (round(cs, 4), round(et, 1), round(es, 1)) == (0.6996, 804.5, 2552.0)
    g = Grid(60, 40, 10, 0.6, 0.012, rods=12, rod_length=3)
    assert g.lc == 580 and sverak(80, g) == pytest.approx(0.8412, abs=1e-4)
    c = grid_check(80, g, 6280, 0.5, 2500, 0.1, body_kg=70)
    assert c.km == pytest.approx(0.91635, abs=1e-4) and c.ki == pytest.approx(1.5112, abs=1e-4) and c.ks == pytest.approx(0.32493, abs=1e-4)
    assert c.lm == pytest.approx(637.6, abs=0.1) and c.ls == pytest.approx(465.6, abs=0.1)
    assert c.e_mesh == pytest.approx(1091, abs=1) and c.e_s == pytest.approx(530, abs=1)
    assert not c.safe


# --- designs ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ground():
    p, _ = rajasthan()
    return p, design_earthing(p)


@pytest.fixture(scope="module")
def roof():
    p, _ = pune()
    return p, design_earthing(p)


@pytest.mark.parametrize("which", ["ground", "roof"])
def test_every_table_and_item_is_bonded_to_a_pit(which, request):
    p, d = request.getfixturevalue(which)
    assert set(d.layout.tables) == set(p.array_layout.table_by_id)
    assert d.unbonded == ()
    assert all(d.bonded[t] >= 1 for t in p.array_layout.table_by_id)
    assert all(d.bonded[it.id] >= it.connections for it in d.layout.items)
    assert len(components(d.layout)) == 1, "the earth network is one connected conductor system"
    assert bonding(d.layout) == d.bonded


def test_ground_design_bonds_the_network_equipment(ground):
    p, d = ground
    kinds = {it.kind for it in d.layout.items}
    assert {"inverter", "scb", "transformer", "lightning_arrester", "vcb", "ese"} <= kinds
    n_scb = len(p.network.instance_ids("scb"))
    assert sum(1 for it in d.layout.items if it.kind == "scb") == n_scb == 180
    transformers = [it for it in d.layout.items if it.kind == "transformer"]
    assert all(it.connections == 2 and d.bonded[it.id] >= 2 for it in transformers)
    roles = {r: sum(1 for x in d.layout.pits if x.role is r) for r in PitRole}
    assert roles[PitRole.EPN] == roles[PitRole.EPB] == 2 * 6 + 1 * 7  # 6 IDTs, 6 block + 1 plant auxiliary transformers
    assert roles[PitRole.LE] == len(d.lps.terminals)
    assert d.layout.switchyard is not None and d.grid is not None and d.grid.safe


def test_ground_sizing_and_counts(ground):
    _, d = ground
    assert d.sizes.main == "50x6"  # 12.5 kA 1 s: IS 3043 156.25 mm2, IEEE 80 183.4 mm2
    cap = next(s for s in d.steps if s.name == "strip_current")
    assert d.pits_by_current == max(0, math.ceil((12500 - cap.value) / electrode_current(50, 1, 3, 40).value))
    assert d.pit_count >= d.pits_by_current and d.resistance_ohm <= d.target_ohm
    assert d.pit_count >= math.ceil(len(d.layout.tables) / d.basis.tables_per_pit)
    q = {x.item: x.quantity for x in d.quantities}
    assert q["pit.EP"] + q["pit.EPN"] + q["pit.EPB"] + q["pit.EPT"] + q["pit.ELA"] + q["pit.LE"] == d.pit_count
    assert sum(v for k, v in q.items() if k.startswith("flat.")) >= sum(r.length_m for r in d.layout.runs)
    assert q["lps.terminal"] == len(d.lps.terminals)


def test_pits_keep_two_electrode_lengths_apart_in_the_array(ground):
    _, d = ground
    array = [p for p in d.layout.pits if p.serves == ("array",)]
    assert len(array) > 100
    pts = [(p.at.x, p.at.y) for p in array]
    gap = min(math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1 :])
    assert gap >= 2 * d.basis.electrode_length_m - 1e-6


def test_rooftop_design(roof):
    p, d = roof
    assert d.rooftop and d.sizes.main == "25x6"
    assert all(not r.buried for r in d.layout.runs if r.role.value == "array")
    assert all(x.on_ground for x in d.layout.pits)
    roofs = [f for f in p.site.geometry.features if f.kind.value == "roof"]
    from shapely.geometry import Point, Polygon

    shapes = [Polygon([(q.x, q.y) for q in f.polygon]) for f in roofs]
    assert not any(s.contains(Point(x.at.x, x.at.y)) for s in shapes for x in d.layout.pits), "pits stand on the ground, not on a roof"
    assert d.resistance_ohm <= d.target_ohm == 2.0
    assert any("2 ohm" in a for a in d.assumptions)


def test_more_fault_current_needs_more_electrode_surface_and_a_bigger_conductor():
    p, _ = pune()
    small = design_earthing(p)
    big = design_earthing(with_basis(p, EarthingBasis(soil_resistivity_ohm_m=50.0, fault_current_ka=25.0, fault_clearing_s=1.0), p.design_basis.lps))
    assert big.sizes.main == "75x6" and big.pit_count >= small.pit_count >= small.pits_by_current

    def capacity(d):
        per, strip = (next(s for s in d.steps if s.name == n).value for n in ("electrode_current", "strip_current"))
        return per * sum(1 for x in d.layout.pits if x.on_ground) + strip

    assert capacity(big) >= 25000 and capacity(small) >= 10000
    assert sum(r.length_m for r in big.layout.runs if r.buried) >= sum(r.length_m for r in small.layout.runs if r.buried)


def test_no_fault_data_reports_the_size_as_unverified():
    p, _ = pune()
    d = design_earthing(with_basis(p, EarthingBasis(soil_resistivity_ohm_m=50.0), None))
    assert d.sizes.main == "50x6" and any("not verified" in a for a in d.assumptions)
    q = with_basis(p, EarthingBasis(soil_resistivity_ohm_m=50.0), None)
    f = run_rules(q, CATALOG, ["earthing"]).by_rule("earthing.conductor_size")
    assert [x.severity for x in f] == [Severity.WARNING]


# --- rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize("which", [rajasthan, pune])
def test_sample_designs_pass_the_earthing_and_lps_rules(which):
    p, _ = which()
    report = run_rules(p, CATALOG, ["earthing", "lps"])
    assert report.counts["error"] == 0, report.render()
    assert {f.rule_id for f in report.findings} >= {"earthing.conductor_size", "earthing.electrode_resistance", "earthing.every_equipment_bonded", "lps.coverage"}


def test_rules_flag_a_high_resistance_and_an_undersized_conductor():
    p, _ = pune()
    bad = with_basis(p, EarthingBasis(soil_resistivity_ohm_m=2000.0, fault_current_ka=10.0, fault_clearing_s=0.5, target_resistance_ohm=0.05), None)
    report = run_rules(bad, CATALOG, ["earthing"])
    errs = report.by_rule("earthing.electrode_resistance")
    assert any(f.severity is Severity.ERROR and f.limit == 0.05 for f in errs)
    assert all(f.formula for f in errs if f.measured is not None and f.unit == "ohm")


def test_fixture_projects_without_an_earthing_basis_are_not_checked():
    import json

    from unicad.domain.project import Project

    data = json.loads((ROOT / "fixtures" / "projects" / "rooftop_500kwp.json").read_text())
    report = run_rules(Project.model_validate(data, context={"catalog": CATALOG}), CATALOG, ["earthing", "lps"])
    assert report.findings == ()


# --- sheets ----------------------------------------------------------------------------------


def _spec(paper: PaperSize, notes) -> SheetSpec:
    tb = TitleBlockData(client="C", project_title="P", location="L", drawing_title="EARTHING LAYOUT", drawing_number="X-301", paper=paper,
                        drawn_date=date(2026, 10, 27), issued_for=IssuedFor.INFORMATION)  # fmt: skip
    return SheetSpec(paper=paper, title_block=tb, notes=notes)


def test_earthing_sheet_follows_the_writer_rules(tmp_path, roof):
    p, d = roof
    doc = ezdxf.new("R2018", setup=True)
    profile = CompanyProfile.load(ROOT / "profiles" / "default.json")
    sheet = render_earthing_sheet(doc, p, CATALOG, d, _spec(PaperSize.A2, earthing_notes(d)), profile)
    assert not [w for w in sheet.warnings if "truncated" not in w]
    assert doc.header["$INSUNITS"] == 4
    for layer in doc.layers:
        assert layer.dxf.lineweight >= 0 or layer.dxf.name in ("0", "Defpoints"), layer.dxf.name
        assert layer.dxf.linetype.upper() in LIBRECAD_LINETYPES, layer.dxf.linetype
    msp = doc.modelspace()
    texts = [t.dxf.text for t in msp.query("TEXT")]
    assert "EARTHING BOQ" in texts and "EARTHING DESIGN CALCULATION (SUMMARY)" in texts
    shown = set(texts) | {t for s in texts for t in re.findall(r"\(([A-Z]+\d+)\)", s)} | {t for s in texts for t in expand_ranges(s)}
    assert {x.tag for x in d.layout.pits} <= shown
    assert dxfrules.violations(doc) == []
    used = {e.dxf.layer for e in msp.query("LINE LWPOLYLINE")}
    assert {f"E-COND-{r.size}-{r.role.value.upper()}" for r in d.layout.runs} <= used
    assert all(h.dxf.pattern_name in ("SOLID", "ANSI31") for h in msp.query("HATCH"))
    assert len({(s.colour, s.linetype) for s in ROLE_STYLE.values()}) == len(ROLE_STYLE)
    assert not msp.query("MTEXT MULTILEADER IMAGE XLINE")
    dxf = tmp_path / "e.dxf"
    doc.saveas(dxf)
    if not LIBRECAD.exists():
        pytest.skip("LibreCAD not available")
    pdf = tmp_path / "e.pdf"
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run([str(LIBRECAD), "dxf2pdf", "-s", "1", "-c", "-p", "594x420", "-o", str(pdf), str(dxf)], check=True, capture_output=True, env=env, timeout=300)
    import pymupdf

    page = pymupdf.open(pdf)[0]
    assert abs(page.rect.width - 594 / 25.4 * 72) < 2 and abs(page.rect.height - 420 / 25.4 * 72) < 2
    assert len(page.get_drawings()) > 500


@pytest.fixture(scope="module")
def sample_sheets(ground, roof):
    profile = CompanyProfile.load(ROOT / "profiles" / "default.json")
    out = {}
    for name, (p, d), paper in (("rajasthan", ground, PaperSize.A1), ("pune", roof, PaperSize.A2)):
        doc = ezdxf.new("R2018", setup=True)
        out[f"{name}-earthing"] = (doc, render_earthing_sheet(doc, p, CATALOG, d, _spec(paper, earthing_notes(d)), profile), p, d)
    for name, (p, d), paper in (("rajasthan", ground, PaperSize.A1), ("pune", roof, PaperSize.A3)):
        doc = ezdxf.new("R2018", setup=True)
        out[f"{name}-lps"] = (doc, render_lps_sheet(doc, p, CATALOG, d.lps, _spec(paper, ()), profile), p, d)
    return out


@pytest.mark.parametrize("name", ["rajasthan-earthing", "pune-earthing", "rajasthan-lps", "pune-lps"])
def test_sample_sheet_labels_never_overlap_and_all_find_room(sample_sheets, name):
    doc, sheet, _, _ = sample_sheets[name]
    assert len(sheet.labels) > 3
    assert overlapping(sheet.labels) == []
    assert not [w for w in sheet.warnings if "no room" in w]
    assert dxfrules.violations(doc) == []


@pytest.mark.parametrize("name", ["pune-earthing", "pune-lps", "rajasthan-earthing"])
def test_plans_use_the_largest_standard_scale_that_fits(sample_sheets, name):
    _, sheet, _, _ = sample_sheets[name]
    x0, y0, x1, y1 = sheet.extent

    def fits(s: int) -> bool:
        return (x1 - x0) * 1000 / s <= sheet.room.width * FILL_FRACTION and (y1 - y0) * 1000 / s <= sheet.room.height * FILL_FRACTION

    assert fits(sheet.scale)
    assert not any(fits(s) for s in SCALES if s < sheet.scale)
    assert 750 in SCALES and sheet.scale <= {"pune-earthing": 1000, "pune-lps": 1500, "rajasthan-earthing": 2500}[name]


@pytest.mark.parametrize("name", ["rajasthan-lps", "pune-lps"])
def test_lps_circles_are_drawn_whole_inside_the_plan(sample_sheets, name):
    """close-e2e-4 n14: protection circles were cut at the plan window and read as clipped by the sheet frame; the plan extent now takes in
    every circle, so each is drawn whole inside the plan room."""
    doc, sheet, _, d = sample_sheets[name]
    zone = [e for e in doc.modelspace().query("CIRCLE ARC") if e.dxf.layer == "E-LPS-ZONE"]
    assert zone and all(e.dxftype() == "CIRCLE" for e in zone)
    room = sheet.room
    for e in zone:
        (cx, cy, _), r = e.dxf.center, e.dxf.radius
        assert room.x0 - 1e-6 <= cx - r and cx + r <= room.x1 + 1e-6 and room.y0 - 1e-6 <= cy - r and cy + r <= room.y1 + 1e-6


def test_clip_circle_keeps_only_the_arcs_inside():
    r = Region(0, 0, 10, 10)
    assert clip_circle(5, 5, 2, r) == [(0.0, 360.0)]
    assert clip_circle(50, 50, 2, r) == []
    arcs = clip_circle(10, 5, 2, r)
    assert len(arcs) == 1 and arcs[0] == pytest.approx((90.0, 270.0))
    corner = clip_circle(0, 0, 3, r)
    assert corner == [pytest.approx((0.0, 90.0))]


def test_crowded_stations_are_one_tag_group_and_detail_a(sample_sheets):
    doc, sheet, p, d = sample_sheets["rajasthan-earthing"]
    texts = [t.dxf.text for t in doc.modelspace().query("TEXT")]
    groups = [s for s in texts if s.endswith("STATION, SEE DETAIL A")]
    assert len(groups) == sum(1 for b in p.array_layout.blocks if b.station is not None)
    assert any(s.startswith("DETAIL A: TYPICAL STATION EARTHING") for s in texts)
    station_pits = {t for g in d.layout.stations for t in g.pits}
    listed = set().union(*(expand_ranges(s) for s in texts))
    assert station_pits <= listed
    assert tag_ranges(["EP3", "EP4", "EPN1", "EPN2", "EPN3", "EP9"]) == "EP3-4, EP9, EPN1-3"


# --- MCP -------------------------------------------------------------------------------------


@session_test
async def test_earthing_and_lps_tools(c):
    pid = (await c.call("project_create", **CREATE))["project_id"]
    await c.call("site_import", project_id=pid, file_path=str(SITES / "rooftop_midc_pune.kml"))
    await c.call("site_set_setbacks", project_id=pid, roof_edge_m=1.2, obstacle_m=0.6)
    await c.call("layout_place", project_id=pid, module="waaree-bi-55-540", mounting="rooftop_flush", table_cols=12, half_cols=6, pitch_method="gap", row_gap_m=0.6, table_gap_m=0.6)
    assert "no earthing or lightning design" in await c.fail("earthing_summary", project_id=pid)
    lps = await c.call("lps_design", project_id=pid, method="ese", level="II", ese_delta_t_us=30, mast_height_m=5)
    assert lps["changed"] and lps["lps"]["uncovered_count"] == 0 and lps["lps"]["tables_covered"] == lps["lps"]["tables"] > 0
    assert lps["lps"]["radius_m"] == pytest.approx(54.54, abs=0.01)
    e = await c.call("earthing_design", project_id=pid, soil_resistivity=50, fault_ka=10, clearing_s=0.5)
    assert e["changed"] and e["earthing"]["unbonded"] == [] and e["earthing"]["conductor"]["main"] == "25x6"
    assert e["earthing"]["pits_by_role"]["LE"] >= 2 * sum(1 for t in lps["lps"]["terminals"] if t["on_roof"])
    s = await c.call("earthing_summary", project_id=pid)
    assert s["earthing"]["resistance_ohm"] <= s["earthing"]["target_ohm"] and s["lps"]["terminal_count"] == lps["lps"]["terminal_count"]
    assert any("NF C 17-102" in a for a in s["lps"]["assumptions"])
    r = await c.call("validate", project_id=pid, families=["earthing", "lps"])
    assert r["counts"]["error"] == 0

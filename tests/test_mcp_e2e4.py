"""Regressions from the fourth end-to-end verifier run (close-e2e-4), replayed from its logged MCP tool arguments (stdio)."""

import asyncio
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Point as SPoint
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from test_mcp import connect

from unicad.arrays.geometry import table_corners
from unicad.domain.site import FeatureKind, shape_of
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import PaperSize
from unicad.mcp import Config
from unicad.mcp.workspace import Workspace
from unicad.routing.locate import manhattan
from unicad.schedules.build import build_schedule

ROOT = Path(__file__).resolve().parents[1]
PUNE = ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml"
RAJ = ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml"
ACME = ROOT / "profiles" / "samples" / "acme"
KUTCH = ROOT / "fixtures" / "sites" / "site_plan_kutch.dxf"

A, B, C = "sgt-chakan", "thar-25mw", "kutch-hybrid"
A_MOD, A_INV = "vikram-solar-hypersol-m10r-144-600-wp", "sungrow-sg110cx-p2"
B_MOD, B_INV = "waaree-bin-08-590", "huawei-sun2000-330ktl-h1"
C_MOD, C_INV = "adani-solar-elan-shine-topcon-g2g-gen-ii-580-wp", "sungrow-sg350hx"
A_CALLS = [
    ("company_onboard", {"dxf_path": str(ACME / "acme-company.dxf"), "answers": str(ACME / "answers.yaml")}),
    ("project_create", {
        "name": "1 MWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "Shree Ganesh Textiles factory roof, MIDC Chakan",
        "grid_voltage_v": 415, "state": "Maharashtra", "address": "Plot, MIDC Chakan, Pune, Maharashtra", "utility": "MSEDCL", "client_code": "SGT", "project_id": A,
    }),
    ("company_assign", {"project_id": A, "profile_id": "acme"}),
    ("project_design_basis", {
        "project_id": A, "ambient_min_c": 6, "ambient_max_c": 44, "altitude_m": 640, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.15,
        "power_factor": 0.99, "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
        "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5},
    }),
    ("site_import", {"project_id": A, "file_path": str(PUNE)}),
    ("site_set_setbacks", {"project_id": A, "roof_edge_m": 1.0, "obstacle_m": 0.6}),
    ("layout_place", {
        "project_id": A, "module": A_MOD, "mounting": "rooftop_flush", "modules_per_string": 20, "strings_per_inverter": 12, "target_dc_kwp": 1008, "n_blocks": 1,
    }),
    ("network_autobuild", {
        "project_id": A, "template": "ci_rooftop", "ac_kw": 770, "module": A_MOD, "inverter": A_INV, "modules_per_string": 20, "strings_per_inverter": 12, "connection": "lt_panel",
    }),
    ("lps_design", {"project_id": A, "method": "ese", "level": "II", "ese_delta_t_us": 60, "mast_height_m": 5}),
    ("earthing_design", {"project_id": A, "soil_resistivity": 100, "fault_ka": 25, "clearing_s": 1}),
    ("network_update", {"project_id": A, "id": "e_ct_poi", "fields": {"cable": "polycab-lt-xlpe-al-1c-400-arm", "runs": 3, "length_m": 15, "laying": "tray"}}),
    ("network_update", {"project_id": A, "id": "e_brk_ct", "fields": {"length_m": 35}}),
    ("project_issue", {"project_id": A, "issued_for": "approval", "rev": "0", "drawn": "R. Deshmukh", "checked": "S. Kulkarni", "approved": "P. Joshi", "date": "2026-10-28"}),
]  # fmt: skip
B_CALLS = [
    ("project_create", {
        "name": "25 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Phalodi",
        "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPN", "client_code": "TRL", "project_id": B, "address": "Phalodi, Jodhpur district, Rajasthan",
    }),
    ("project_design_basis", {
        "project_id": B, "ambient_min_c": 3, "ambient_max_c": 49, "altitude_m": 240, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.25,
        "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPN 33 kV fault level (brief)"}],
        "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
    }),
    ("site_import", {"project_id": B, "file_path": str(RAJ)}),
    ("site_set_setbacks", {"project_id": B, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {
        "project_id": B, "module": B_MOD, "mounting": "fixed_tilt", "tilt_deg": 25, "table_rows": 2, "table_cols": 26, "road_every_rows": 12, "modules_per_string": 26,
        "strings_per_inverter": 24, "n_blocks": 7, "target_dc_kwp": 30925.44,
    }),
    ("network_autobuild", {"project_id": B, "template": "utility_string", "module": B_MOD, "inverter": B_INV, "modules_per_string": 26, "strings_per_inverter": 24}),
    ("lps_design", {"project_id": B, "method": "ese", "level": "III", "ese_delta_t_us": 60, "mast_height_m": 6}),
    ("earthing_design", {"project_id": B, "soil_resistivity": 150, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45]}),
    ("earthing_design", {"project_id": B, "surface_resistivity": 3000, "surface_layer_m": 0.1}),
]  # fmt: skip
C_BUILD = {
    "project_id": C, "template": "hybrid", "module": C_MOD, "inverter": C_INV, "modules_per_string": 27, "strings_per_inverter": 24, "bess_kw": 8000, "bess_kwh": 16000,
    "pcs": "sungrow-st5015kwh-2500kw-2h", "battery": "sungrow-st5015kwh", "export_limit_kw": 15000,
}  # fmt: skip
C_CALLS = [
    ("project_create", {
        "name": "Kutch Hybrid 15 MWac PV + 8 MW 16 MWh BESS", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch hybrid park",
        "grid_voltage_v": 33000, "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": C, "address": "Kutch district, Gujarat",
    }),
    ("project_design_basis", {
        "project_id": C, "ambient_min_c": 5, "ambient_max_c": 47, "altitude_m": 20, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.2,
        "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
        "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
    }),
    ("site_import", {"project_id": C, "file_path": str(KUTCH), "origin_lon": 69.8, "origin_lat": 23.45}),
    ("site_set_setbacks", {"project_id": C, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {
        "project_id": C, "module": C_MOD, "mounting": "fixed_tilt", "tilt_deg": 23, "table_rows": 2, "table_cols": 27, "road_every_rows": 12, "modules_per_string": 27,
        "strings_per_inverter": 24, "n_blocks": 3, "target_dc_kwp": 16161.12,
    }),
    ("network_autobuild", C_BUILD),
    ("earthing_design", {"project_id": C, "soil_resistivity": 80, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45], "surface_resistivity": 3000, "surface_layer_m": 0.1}),
    ("network_autobuild", C_BUILD),
    ("lps_design", {"project_id": C, "method": "ese", "level": "III", "ese_delta_t_us": 60, "mast_height_m": 6}),
    ("earthing_design", {"project_id": C}),
]  # fmt: skip


def replay(ws: Path, calls) -> dict[str, list[dict]]:
    async def go():
        out: dict[str, list[dict]] = {}
        async with connect(ws) as c:
            for tool, args in calls:
                out.setdefault(tool, []).append(await c.call(tool, **args))
            pid = next(a["project_id"] for _, a in calls if "project_id" in a)
            out["earthing_summary"] = [await c.call("earthing_summary", project_id=pid)]
            out["layout_summary"] = [await c.call("layout_summary", project_id=pid)]
            out["drawn"] = [await c.call("drawings_render", project_id=pid, kinds=["sld", "earthing", "lps"] if pid == A else ["earthing", "lps"], formats=["dxf"])]
        return out

    return asyncio.run(asyncio.wait_for(go(), 600))


def _workspace(tmp_path_factory, pid, calls):
    ws = tmp_path_factory.mktemp(pid) / "ws"
    results = replay(ws, calls)
    workspace = Workspace(Config(workspace=ws))
    return results, workspace.load(pid)[0], workspace


@pytest.fixture(scope="module")
def rooftop(tmp_path_factory):
    return _workspace(tmp_path_factory, A, A_CALLS)


@pytest.fixture(scope="module", params=[(B, B_CALLS), (C, C_CALLS)], ids=["B", "C"])
def plant(request, tmp_path_factory):
    pid, calls = request.param
    return (pid, *_workspace(tmp_path_factory, pid, calls))


def test_m1_the_switchyard_mat_stands_where_routing_measures_the_mv_feeders_to(plant):
    _, results, project, workspace = plant
    yard = results["layout_summary"][0]["switchyard"]
    grid = results["earthing_summary"][0]["earthing"]["switchyard_grid"]["extent_m"]
    centre = ((grid["x0"] + grid["x1"]) / 2, (grid["y0"] + grid["y1"]) / 2)
    assert abs(centre[0] - yard["x"]) < 0.5 and abs(centre[1] - yard["y"]) < 0.5, (centre, yard)
    mat = box(grid["x0"], grid["y0"], grid["x1"], grid["y1"])
    assert not mat.intersects(_tables(project)), "the switchyard mat stands on tables"
    for f in _keep_off(project):
        assert mat.distance(f) >= project.site.geometry.setbacks.exclusion_m - 0.01, "the switchyard mat stands inside an exclusion setback"
    stations = {b.id: b.station for b in project.array_layout.blocks}
    azimuth = project.array_layout.blocks[0].tables[0].azimuth_deg
    feeders = [r for r in build_schedule(project, workspace.catalog).rows if r.length_basis.startswith("station ") and " to switchyard" in r.length_basis]
    assert feeders
    for r in feeders:
        s = stations[r.length_basis.split()[1]]
        expected = manhattan((s.x, s.y), centre, azimuth)
        assert abs(r.length_pos_m - expected) < 10.0, (r.from_tag, r.length_pos_m, expected)


def _tables(project):
    return unary_union([Polygon(table_corners(t)) for t in project.array_layout.table_by_id.values()])


def _keep_off(project):
    """Exclusions, water and line corridors: what the exclusion setback keeps ground equipment away from."""
    out = []
    for f in project.site.geometry.features:
        if f.kind in (FeatureKind.EXCLUSION, FeatureKind.WATER, FeatureKind.TRANSMISSION_LINE) and not (f.polyline and f.width_m is None):
            g = shape_of(f)
            out.append(g.buffer(f.width_m / 2, cap_style="flat") if f.polyline else g)
    return out


def test_m3_lps_terminals_replied_are_the_ones_stored_and_drawn(plant):
    _, results, _, _ = plant
    final = results["earthing_summary"][0]["lps"]
    replied = results["lps_design"][-1]["lps"]
    if replied["terminal_count"] != final["terminal_count"]:
        moved = [p for r in results["earthing_design"] for p in r.get("problems", ()) if p["subject"] == "lps"]
        assert moved and f"now has {final['terminal_count']} terminals (lps_design gave {replied['terminal_count']})" in moved[0]["message"], moved
    elif [(t["x"], t["y"]) for t in replied["terminals"]] != [(t["x"], t["y"]) for t in final["terminals"]]:
        moved = [p for r in results["earthing_design"] for p in r.get("problems", ()) if p["subject"] == "lps"]
        assert moved and f"moved (still {final['terminal_count']} terminals)" in moved[0]["message"], moved
    assert results["earthing_design"][-1]["lps_terminal_count"] == final["terminal_count"]


def test_block_balance_blocks_hold_equal_whole_inverters_when_the_count_divides(plant):
    pid, results, _, _ = plant
    placed = results["layout_place"][-1]
    invs = [b["modules"] / (26 if pid == B else 27) / 24 for b in placed["blocks"]]
    assert all(i == int(i) for i in invs), invs
    if pid == B:
        assert invs == [12] * 7 and placed["metrics"]["block_balance"] == 0, invs
    else:
        assert sum(invs) == 43 and max(invs) - min(invs) <= 1, invs


def test_m5_field_inverters_stations_and_bess_keep_the_exclusion_setback(plant):
    _, _, project, _ = plant
    setback = project.site.geometry.setbacks.exclusion_m
    lay = project.array_layout
    points = [(e.tag, e.at) for e in lay.equipment] + [(b.id, b.station) for b in lay.blocks if b.station is not None]
    assert any(k == "inverter" for k in (e.kind for e in lay.equipment))
    near = [(tag, round(min(f.distance(SPoint(p.x, p.y)) for f in _keep_off(project)), 2)) for tag, p in points]
    assert [n for n in near if n[1] < setback - 0.01] == []


def _sheet(results, kind):
    return next(s for s in results["drawn"][0]["sheets"] if s["kind"] == kind)


def test_m2_the_earthing_sheet_keeps_every_general_note(rooftop):
    """close-e2e-4 m2: the issued rooftop earthing sheet dropped a general note ('1 notes rows did not fit and were omitted'): the lightning
    design's assumptions were repeated among the earthing notes; they belong on the LPS sheet."""
    results, project, workspace = rooftop
    sheet = _sheet(results, "earthing")
    assert not [w for w in sheet["warnings"] if "did not fit" in w], sheet["warnings"]
    lps = _sheet(results, "lps")
    assert not [w for w in lps["warnings"] if "did not fit" in w], lps["warnings"]
    texts = " ".join(e.dxf.text for e in ezdxf.readfile(sheet["files"]["dxf"]).modelspace().query("TEXT"))
    assert "NF C 17-102, A FRENCH STANDARD" not in texts


def test_n16_no_zero_over_zero_pit_line(rooftop):
    results, _, _ = rooftop
    texts = [e.dxf.text for e in ezdxf.readfile(_sheet(results, "earthing")["files"]["dxf"]).modelspace().query("TEXT")]
    assert "0 / 0" not in texts
    assert any(t.startswith("PITS FOR RESISTANCE") for t in texts) and "NONE: BURIED FLAT SUFFICES" in texts


def test_n14_every_protection_circle_is_drawn_whole_inside_the_plan(rooftop, plant):
    for results in (rooftop[0], plant[1]):
        sheet = _sheet(results, "lps")
        doc = ezdxf.readfile(sheet["files"]["dxf"])
        zone = [e for e in doc.modelspace() if e.dxf.layer == "E-LPS-ZONE"]
        circles = [e for e in zone if e.dxftype() == "CIRCLE"]
        assert circles and not [e for e in zone if e.dxftype() == "ARC"], "a protection circle is cut"
        area = metrics_for(PaperSize(sheet["paper"])).drawing_area
        x0, y0, x1, y1 = area.x0, area.y0, area.x1, area.y1
        for c in circles:
            (cx, cy, _), r = c.dxf.center, c.dxf.radius
            assert x0 < cx - r and cx + r < x1 and y0 < cy - r and cy + r < y1, "a protection circle crosses the sheet frame"


def test_n19_breakers_carry_no_type_text_beside_the_symbol(rooftop):
    results, _, _ = rooftop
    doc = ezdxf.readfile(_sheet(results, "sld")["files"]["dxf"])
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    texts += [e.dxf.text for b in doc.blocks for e in b if e.dxftype() == "TEXT"]
    assert "MCCB" not in texts and "ACB" not in texts


def test_m3_the_inverter_cable_callout_reads_three_single_core_cables_per_run(rooftop):
    results, _, _ = rooftop
    doc = ezdxf.readfile(_sheet(results, "sld")["files"]["dxf"])
    texts = " ".join(e.dxf.text for b in doc.blocks for e in b if e.dxftype() == "TEXT")
    assert "1R x 1C x 400" not in texts and "3 x 1C x 400" in texts


def test_n25_water_is_drawn_as_on_the_layout_on_the_earthing_and_lps_sheets(plant):
    pid, results, project, _ = plant
    if not project.site.geometry.of_kind(FeatureKind.WATER):
        pytest.skip("no water on this site")
    for kind in ("earthing", "lps"):
        doc = ezdxf.readfile(_sheet(results, kind)["files"]["dxf"])
        assert doc.layers.get("L-WATER-FILL").color == 131 and doc.layers.get("L-WATER").color == 4
        assert doc.modelspace().query('HATCH[layer=="L-WATER-FILL"]')

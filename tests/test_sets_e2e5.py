"""Regressions from the fifth end-to-end verifier run (close-e2e-5), replayed over MCP stdio from its logged tool arguments: the overall SLD in
stacked rows on one sheet, sheet numbering, BESS siting, station drawing positions, lightning masts and the drawing details the run flagged."""

import asyncio
from pathlib import Path

import pytest
from test_mcp import connect

from unicad.catalog import load_catalog
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import PaperSize
from unicad.drafting.sld.sheet import fit_layout
from unicad.sets.numbering import Numberer
from unicad.sets.render import load_profile
from unicad.sets.sld import SldBuilder, _drawn, _shown, partition
from unicad.store import ProjectStore

ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / "fixtures" / "sites"
CATALOG = load_catalog()
A, B, C = "sgt-chakan", "thar-35mw", "kutch-hybrid"

A_CALLS = [
    ("project_create", {"name": "450 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "Shree Ganesh Textiles factory roof, MIDC Chakan",
                        "grid_voltage_v": 415, "state": "Maharashtra", "address": "MIDC Chakan, Pune", "utility": "MSEDCL", "client_code": "SGT", "project_id": A}),
    ("project_design_basis", {"project_id": A, "ambient_min_c": 6, "ambient_max_c": 44, "altitude_m": 640, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.12,
                              "power_factor": 0.99, "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5}}),
    ("site_import", {"project_id": A, "file_path": str(SITES / "rooftop_midc_pune.kml")}),
    ("site_set_setbacks", {"project_id": A, "roof_edge_m": 1.0, "obstacle_m": 0.6}),
    ("layout_place", {"project_id": A, "module": "tata-power-solar-tp600lg10tb-series-590-wp", "mounting": "rooftop_flush", "target_dc_kwp": 448.4, "modules_per_string": 19,
                      "strings_per_inverter": 10}),
    ("network_autobuild", {"project_id": A, "template": "ci_rooftop", "ac_kw": 400, "module": "tata-power-solar-tp600lg10tb-series-590-wp", "inverter": "solis-s5-gc100k",
                           "modules_per_string": 19, "strings_per_inverter": 10, "lengths": {"acdb_meter": 25}}),
    ("earthing_design", {"project_id": A}),
    ("lps_design", {"project_id": A, "method": "ese", "level": "II", "ese_delta_t_us": 60, "mast_height_m": 5}),
]  # fmt: skip
B_CALLS = [
    ("project_create", {"name": "35 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Rajasthan",
                        "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPNL", "client_code": "TRL", "project_id": B}),
    ("project_design_basis", {"project_id": B, "ambient_min_c": 2, "ambient_max_c": 48, "altitude_m": 250, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.25,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPNL 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": B, "file_path": str(SITES / "ground_mount_rajasthan.kml")}),
    ("site_set_setbacks", {"project_id": B, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": B, "module": "waaree-bin-21-630", "tilt_deg": 25, "road_every_rows": 10, "target_dc_kwp": 45500, "n_blocks": 4, "modules_per_string": 28,
                      "strings_per_inverter": 312}),
    ("network_autobuild", {"project_id": B, "template": "utility_central", "ac_kw": 35200, "module": "waaree-bin-21-630", "inverter": "sma-sc-4400-up", "modules_per_string": 28,
                           "strings_per_inverter": 312, "inverters_per_block": 2}),
    ("earthing_design", {"project_id": B, "soil_resistivity": 150, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45], "surface_resistivity": 3000, "surface_layer_m": 0.1}),
    ("lps_design", {"project_id": B, "method": "ese", "level": "III", "ese_delta_t_us": 60, "mast_height_m": 6}),
]  # fmt: skip
C_BUILD = {
    "project_id": C, "template": "hybrid", "ac_kw": 12000, "module": "waaree-bi-55-545", "inverter": "huawei-sun2000-330ktl-h1", "modules_per_string": 28, "strings_per_inverter": 24,
    "inverters_per_block": 10, "bess_kw": 7560, "bess_kwh": 15045, "pcs": "sungrow-st5015kwh-2500kw-2h", "battery": "sungrow-st5015kwh", "export_limit_kw": 12000,
}  # fmt: skip
C_CALLS = [
    ("project_create", {"name": "12 MWac Solar PV + 6 MW / 12 MWh BESS Hybrid Plant", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch hybrid site, Gujarat",
                        "grid_voltage_v": 33000, "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": C}),
    ("project_design_basis", {"project_id": C, "ambient_min_c": 5, "ambient_max_c": 46, "altitude_m": 20, "vdrop_dc_max": 0.02, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.2,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": C, "file_path": str(SITES / "site_plan_kutch.dxf"), "origin_lon": 69.8, "origin_lat": 23.45}),
    ("site_set_setbacks", {"project_id": C, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": C, "module": "waaree-bi-55-545", "tilt_deg": 22, "road_every_rows": 10, "target_dc_kwp": 14649.6, "n_blocks": 4, "modules_per_string": 28,
                      "strings_per_inverter": 24}),
    ("network_autobuild", C_BUILD),
    ("earthing_design", {"project_id": C, "soil_resistivity": 80, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45], "surface_resistivity": 3000, "surface_layer_m": 0.1}),
    ("network_autobuild", C_BUILD),
    ("earthing_design", {"project_id": C}),
    ("lps_design", {"project_id": C, "method": "ese", "level": "III", "ese_delta_t_us": 60, "mast_height_m": 6}),
]  # fmt: skip


@pytest.fixture(scope="module")
def ws(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("e2e5") / "ws"

    async def go():
        async with connect(root) as c:
            for tool, args in (*A_CALLS, *B_CALLS, *C_CALLS):
                await c.call(tool, **args)

    asyncio.run(asyncio.wait_for(go(), 900))
    return root


@pytest.fixture(scope="module")
def projects(ws):
    store = ProjectStore(ws)
    return {pid: store.load(pid, catalog=CATALOG) for pid in (A, B, C)}


def _sld_sheets(p):
    profile = load_profile(p.company_profile)
    sheets = SldBuilder().plan(p, profile, CATALOG, Numberer.for_project(p, profile))
    out = []
    for s, part in zip(sheets, partition(p.network, sheets)):
        lay, opts, scale = fit_layout(_shown(p, s, CATALOG), CATALOG, s.paper, _drawn(p.network, s, part.nodes), xrefs=part.xrefs)
        area = metrics_for(PaperSize(s.paper)).drawing_area
        fill = lay.extent.width * scale * lay.extent.height * scale / (area.width * area.height)
        out.append((s, lay, opts, scale, fill))
    return out


@pytest.mark.parametrize("pid", [B, C])
def test_m23_utility_overall_sld_fills_its_a0_or_a1_sheet_in_stacked_rows_at_the_text_floor(projects, pid):
    """Run 5: the B overall SLD was two A0 sheets at about 45 % fill (C the same); the feeders now stack in rows that share one scale on a sheet,
    each conductor cut between rows ending in a marker naming the row it continues in."""
    overall = [x for x in _sld_sheets(projects[pid]) if x[0].title.startswith("OVERALL")]
    if pid == B:
        assert len(overall) == 1 and overall[0][0].title == "OVERALL SINGLE LINE DIAGRAM"
    for s, lay, opts, scale, fill in overall:
        assert scale >= opts.min_scale, (s.title, scale)
        if s.paper in ("A0", "A1"):
            assert fill >= 0.40, (s.title, s.paper, fill)
    s, lay, *_ = overall[0]
    rows = [h.text for h in lay.headings]
    assert rows[0] == f"ROW 1 OF {len(rows)}" and len(rows) >= 2
    refs = [dict(sym.values).get("SHEET", "") for sym in lay.symbols if sym.role == "offsheet"]
    assert any(r.startswith("SEE ROW ") for r in refs), refs


def test_n17_sheet_x_of_y_counts_the_parts_of_the_same_drawing(projects):
    """Run 5: B SLD 001 read 'PART 1 OF 2' in its title and '1 of 3' in the sheet box (the overall parts and the typical block counted together)."""
    p = projects[C]
    profile = load_profile(p.company_profile)
    sheets = SldBuilder().plan(p, profile, CATALOG, Numberer.for_project(p, profile))
    built = SldBuilder().build(p, sheets, sheets, profile, CATALOG)
    for b in built:
        texts = {e.dxf.text for e in b.doc.modelspace().query("TEXT")}
        title = b.sheet.title
        if " (PART " in title:
            k, n = title.rsplit("(PART ", 1)[1].rstrip(")").split(" OF ")
            assert f"{k} of {n}" in texts, (title, sorted(t for t in texts if " of " in t))
        else:
            assert "1 of 1" in texts, title

"""Regressions from the third end-to-end verifier run (close-e2e-3), replayed from its logged MCP tool arguments (stdio)."""

import asyncio
from pathlib import Path

from test_mcp import connect

ROOT = Path(__file__).resolve().parents[1]
PUNE = ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml"
RAJ = ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml"
KUTCH = ROOT / "fixtures" / "sites" / "site_plan_kutch.dxf"
ACME = ROOT / "profiles" / "samples" / "acme"

A, B, C = "sgt-chakan", "thar-30mw", "kutch-hybrid"
A_MOD, A_INV = "tata-power-solar-tp600lg10tb-series-590-wp", "solis-s5-gc100k"
B_MOD, B_INV = "adani-solar-elan-shine-topcon-g2g-gen-ii-580-wp", "sungrow-sg3125hv-30"
C_MOD = "waaree-bin-21-620"
A_CREATE = {
    "name": "600 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "Shree Ganesh Textiles factory roof, MIDC Chakan",
    "grid_voltage_v": 415, "state": "Maharashtra", "address": "Plot, MIDC Chakan, Pune, Maharashtra", "utility": "MSEDCL", "client_code": "SGT", "project_id": A,
}  # fmt: skip
A_BASIS = {
    "project_id": A, "ambient_min_c": 6, "ambient_max_c": 44, "altitude_m": 640, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.15,
    "power_factor": 0.99, "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
    "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5},
}  # fmt: skip
A_PLACE = {"project_id": A, "module": A_MOD, "mounting": "rooftop_flush", "modules_per_string": 19, "strings_per_inverter": 11, "target_dc_kwp": 616.55, "n_blocks": 1}
A_BUILD = {
    "project_id": A, "template": "ci_rooftop", "ac_kw": 500, "module": A_MOD, "inverter": A_INV, "modules_per_string": 19, "strings_per_inverter": 11, "connection": "lt_panel",
}  # fmt: skip
B_CREATE = {
    "name": "30 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Phalodi", "grid_voltage_v": 33000,
    "state": "Rajasthan", "utility": "RVPN", "client_code": "TRL", "project_id": B, "address": "Phalodi, Jodhpur district, Rajasthan",
}  # fmt: skip
B_BASIS = {
    "project_id": B, "ambient_min_c": 3, "ambient_max_c": 49, "altitude_m": 240, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.25, "power_factor": 0.95,
    "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPN 33 kV fault level (brief)"}],
    "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
}  # fmt: skip
B_PLACE = {
    "project_id": B, "module": B_MOD, "mounting": "fixed_tilt", "tilt_deg": 25, "table_rows": 2, "table_cols": 27, "road_every_rows": 12, "modules_per_string": 27,
    "strings_per_inverter": 274, "n_blocks": 9, "target_dc_kwp": 38617.56,
}  # fmt: skip
B_BUILD = {
    "project_id": B, "template": "utility_central", "module": B_MOD, "inverter": B_INV, "modules_per_string": 27, "strings_per_inverter": 274, "inverters_per_block": 1,
}  # fmt: skip
C_CREATE = {
    "name": "Kutch Hybrid 20 MWac PV + 10 MW 20 MWh BESS", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch hybrid park", "grid_voltage_v": 33000,
    "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": C, "address": "Kutch district, Gujarat",
}  # fmt: skip
C_BASIS = {
    "project_id": C, "ambient_min_c": 5, "ambient_max_c": 47, "altitude_m": 20, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.15, "power_factor": 0.95,
    "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
    "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
}  # fmt: skip
C_PLACE = {
    "project_id": C, "module": C_MOD, "mounting": "fixed_tilt", "tilt_deg": 23, "table_rows": 2, "table_cols": 28, "road_every_rows": 12, "modules_per_string": 28,
    "strings_per_inverter": 22, "n_blocks": 4, "target_dc_kwp": 22300,
}  # fmt: skip
C_BESS = {"bess_kw": 10000, "bess_kwh": 20000, "pcs": "sungrow-st5015kwh-2500kw-2h", "battery": "sungrow-st5015kwh", "export_limit_kw": 30000}
C_BUILD = {
    "project_id": C, "template": "hybrid", "module": C_MOD, "inverter": "sungrow-sg350hx", "modules_per_string": 28, "strings_per_inverter": 22, "inverters_per_block": 17, **C_BESS,
}  # fmt: skip
C_HUAWEI = {
    "project_id": C, "template": "hybrid", "ac_kw": 20400, "module": C_MOD, "inverter": "huawei-sun2000-330ktl-h1", "modules_per_string": 28, "strings_per_inverter": 19,
    "inverters_per_block": 18, **C_BESS,
}  # fmt: skip
C_HUAWEI_PLACE = C_PLACE | {"strings_per_inverter": 19, "target_dc_kwp": 22600}


def run(coro, timeout=600):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def site_a(c):
    await c.call("company_onboard", dxf_path=str(ACME / "acme-company.dxf"), answers=str(ACME / "answers.yaml"))
    await c.call("project_create", **A_CREATE)
    await c.call("company_assign", project_id=A, profile_id="acme")
    await c.call("project_design_basis", **A_BASIS)
    await c.call("site_import", project_id=A, file_path=str(PUNE))
    await c.call("site_set_setbacks", project_id=A, roof_edge_m=1.0, obstacle_m=0.6)


async def site_b(c):
    await c.call("project_create", **B_CREATE)
    await c.call("project_design_basis", **B_BASIS)
    await c.call("site_import", project_id=B, file_path=str(RAJ))
    await c.call("site_set_setbacks", project_id=B, boundary_m=10, exclusion_m=10)


async def site_c(c):
    await c.call("project_create", **C_CREATE)
    await c.call("project_design_basis", **C_BASIS)
    await c.call("site_import", project_id=C, file_path=str(KUTCH), origin_lon=69.8, origin_lat=23.45)
    await c.call("site_set_setbacks", project_id=C, boundary_m=10, exclusion_m=10)


async def project(c, pid):
    return (await c.call("project_get", project_id=pid, full=True))["project"]


def _inverters(placed, spi):
    return [round(b["modules"] / spi / placed["metrics"]["modules"] * placed["metrics"]["strings"]) for b in placed["blocks"]]


def test_a1_odd_strings_per_inverter_on_two_string_tables_reach_the_target_by_mixing_table_sizes(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_a(c)
            placed = await c.call("layout_place", **A_PLACE)
            assert placed["target_reached"] is True
            assert placed["metrics"]["strings"] == 55 and placed["metrics"]["dc_kwp"] == 616.55, placed["metrics"]
            assert any("mixed table sizes" in p["message"] for p in placed["problems"]), placed["problems"]
            layout = (await project(c, A))["array_layout"]
            assert sorted({t["rows"] for b in layout["blocks"] for t in b["tables"]}) == [1, 2]

    run(go())


def test_b5_n_blocks_at_the_exact_target_gives_every_block_and_the_whole_target(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_b(c)
            placed = await c.call("layout_place", **B_PLACE)
            assert placed["target_reached"] is True and len(placed["blocks"]) == 9, placed["blocks"]
            assert placed["metrics"]["dc_kwp"] == 38617.56

    run(go())


def test_a1_a_target_that_is_not_whole_inverters_says_so_with_the_shortfall(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            placed = await c.call("layout_place", **C_PLACE)
            assert placed["target_reached"] is False
            short = [p["message"] for p in placed["problems"] if "not reached with whole inverters" in p["message"]]
            assert short and "148.64 kWp short" in short[0] and "22151.36 (58 inverters) or 22533.28 (59)" in short[0], placed["problems"]
            assert placed["metrics"]["dc_kwp"] == 22151.36

    run(go())


def test_c2_blocks_balance_to_one_inverter_and_block_dc_kwp_is_an_upper_bound(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            spi = C_PLACE["strings_per_inverter"]
            placed = await c.call("layout_place", **C_PLACE)
            invs = [b["modules"] // (spi * 28) for b in placed["blocks"]]
            assert all(b["modules"] % (spi * 28) == 0 for b in placed["blocks"])
            assert max(invs) - min(invs) <= 1 and sum(invs) == 58, invs
            capped = await c.call("layout_place", **(C_PLACE | {"n_blocks": None, "block_dc_kwp": 5729, "target_dc_kwp": 22916}))
            assert all(b["dc_kwp"] <= 5729 for b in capped["blocks"]), capped["blocks"]
            invs = [b["modules"] // (spi * 28) for b in capped["blocks"]]
            assert max(invs) - min(invs) <= 1 and capped["target_reached"] is True, invs

    run(go())


def test_b9_default_fill_grows_from_the_switchyard_side(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_b(c)

            def mean_feeder(placed):
                y = placed["switchyard"]
                d = [abs(b["station"]["x"] - y["x"]) + abs(b["station"]["y"] - y["y"]) for b in placed["blocks"]]
                return sum(d) / len(d)

            rows = await c.call("layout_place", **(B_PLACE | {"fill": "rows"}))
            near = await c.call("layout_place", **B_PLACE)
            assert near["target_reached"] and any("filled nearest the transmission line" in a for a in near["assumptions"]), near["assumptions"]
            assert mean_feeder(near) < 0.75 * mean_feeder(rows), (mean_feeder(near), mean_feeder(rows))

    run(go())

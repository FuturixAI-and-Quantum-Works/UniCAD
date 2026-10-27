"""Regressions from the second end-to-end verifier run (close-e2e-2), replayed from its logged MCP tool arguments (stdio)."""

import asyncio
from pathlib import Path

from test_mcp import connect

ROOT = Path(__file__).resolve().parents[1]
PUNE = ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml"
RAJ = ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml"
KUTCH = ROOT / "fixtures" / "sites" / "site_plan_kutch.dxf"
ACME = ROOT / "profiles" / "samples" / "acme"

A_MOD, A_INV = "adani-solar-elan-shine-topcon-g2g-gen-ii-560-wp", "huawei-sun2000-100ktl-m2"
A_CREATE = {
    "name": "Shree Ganesh Textiles 800 kWp Rooftop", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "MIDC Chakan, Pune",
    "grid_voltage_v": 415, "project_id": "sgt-chakan", "state": "Maharashtra", "utility": "MSEDCL", "client_code": "SGT",
    "address": "Plot B-12, MIDC Chakan Phase II, Pune 410501",
}  # fmt: skip
A_BASIS = {
    "project_id": "sgt-chakan", "ambient_min_c": 8, "ambient_max_c": 42, "altitude_m": 640, "vdrop_dc_max": 0.02, "vdrop_ac_max": 0.02,
    "dc_ac_ratio_target": 1.2, "power_factor": 0.99,
    "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "user input: MSEDCL LT fault level 25 kA 1 s"}],
    "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1},
    "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5},
}  # fmt: skip
A_PLACE = {
    "project_id": "sgt-chakan", "module": A_MOD, "mounting": "rooftop_flush", "pitch_method": "gap", "row_gap_m": 0.6, "table_gap_m": 0.6,
    "table_rows": 2, "target_dc_kwp": 806.4, "modules_per_string": 20, "strings_per_inverter": 12,
}  # fmt: skip
A_AUTOBUILD = {
    "project_id": "sgt-chakan", "template": "ci_rooftop", "dc_kwp": 806.4, "module": A_MOD, "inverter": A_INV, "modules_per_string": 20,
    "strings_per_inverter": 12, "connection": "lt_panel",
}  # fmt: skip


def run(coro, timeout=300):
    asyncio.run(asyncio.wait_for(coro, timeout))


async def scenario_a(c, place=None):
    await c.call("company_onboard", dxf_path=str(ACME / "acme-company.dxf"), answers=str(ACME / "answers.yaml"))
    await c.call("project_create", **A_CREATE)
    await c.call("company_assign", project_id="sgt-chakan", profile_id="acme")
    await c.call("project_design_basis", **A_BASIS)
    await c.call("site_import", project_id="sgt-chakan", file_path=str(PUNE))
    await c.call("site_set_setbacks", project_id="sgt-chakan", roof_edge_m=1.0, obstacle_m=0.6)
    placed = await c.call("layout_place", **(A_PLACE | (place or {})))
    built = await c.call("network_autobuild", **A_AUTOBUILD)
    return placed, built


def test_n10_breaker_protects_the_cable_beyond_its_isolator(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            _, built = await scenario_a(c)
            assert not [f for f in built["findings"] if f["rule_id"] == "cables.protection"], built["findings"]
            p = (await c.call("project_get", project_id="sgt-chakan", full=True))["project"]
            edge = next(e for e in p["network"]["edges"] if e["id"] == "e_iso_ltp")
            assert (edge["cable"], edge["runs"]) != ("polycab-lt-xlpe-al-1c-300-arm", 2), edge
            await c.call("network_update", project_id="sgt-chakan", id="e_iso_ltp", fields={"cable": "polycab-lt-xlpe-al-1c-300-arm", "runs": 2})
            v = await c.call("validate", project_id="sgt-chakan", families=["cables"])
            hits = [f for f in v["findings"] if f["rule_id"] == "cables.protection"]
            assert len(hits) == 1 and hits[0]["subjects"] == ["acdb_brk", "e_iso_ltp"] and hits[0]["measured"] == 1250, hits

    run(go())


def test_n12_autobuild_reads_the_company_thresholds_validate_reads(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            _, built = await scenario_a(c)
            v = await c.call("validate", project_id="sgt-chakan")
            ratio = lambda fs: [f for f in fs if f["rule_id"].startswith("ratio.")]  # noqa: E731
            assert ratio(built["findings"]) == ratio(v["findings"]) == [], (built["findings"], v["findings"])
            assert built["company_thresholds"] == v["company_thresholds"] and built["company_thresholds"]["values"]["dc_ac_band"] == 0.15
            assert built["rules"] == v["counts"]

    run(go())

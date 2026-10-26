"""network_autobuild and design_strings over MCP stdio."""

import asyncio
from pathlib import Path

from test_mcp import connect

GROUND = Path(__file__).resolve().parents[1] / "fixtures" / "sites" / "ground_mount_rajasthan.kml"
UTIL_MOD = "vikram-solar-hypersol-m10r-144-580-wp"

MODULE = "adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp"
INVERTER = "huawei-sun2000-100ktl-m2"
CREATE = {"name": "500 kWp Auto Roof", "client": "Demo", "segment": "ci_rooftop", "site": "Factory roof", "grid_voltage_v": 415, "dc_ac_ratio_target": 1.0}


def test_project_create_autobuild_validate_over_stdio(tmp_path):
    async def run():
        async with connect(tmp_path / "ws") as c:
            pid = (await c.call("project_create", **CREATE))["project_id"]
            d = await c.call("design_strings", project_id=pid, module=MODULE, inverter=INVERTER)
            assert d["modules_per_string"] == d["modules_per_string_max"] and d["binding"]["modules_per_string_max"] == "voc_max"

            r = await c.call("network_autobuild", project_id=pid, template="ci_rooftop", ac_kw=500, module=MODULE, inverter=INVERTER)
            assert r["changed"] and r["revision"] == 2 and r["rules"]["error"] == 0
            assert r["design"]["instances"]["inverter"] == 5 and r["strings_design"]["modules_per_string"] == d["modules_per_string"]
            assert all(f["severity"] != "error" for f in r["findings"]) and r["assumptions"]

            v = await c.call("validate", project_id=pid)
            assert v["revision"] == 2 and v["counts"]["error"] == 0 and v["gates"]["approval"]["allowed"]

            again = await c.call("network_autobuild", project_id=pid, template="ci_rooftop", ac_kw=500, module=MODULE, inverter=INVERTER)
            assert not again["changed"] and again["revision"] == 2

            hv = await c.call("network_autobuild", project_id=pid, template="utility_string", ac_kw=20000, inverter="sungrow-sg350hx", grid_voltage_v=33000)
            assert hv["rules"]["error"] == 0 and any("0.8-ynd11-project" in a and "BHEL PS 439-1304" in a for a in hv["assumptions"])

    asyncio.run(asyncio.wait_for(run(), 120))


def test_layout_place_then_autobuild_links_every_string_over_stdio(tmp_path):
    async def run():
        async with connect(tmp_path / "ws") as c:
            pid = (await c.call("project_create", name="Rajasthan 22 MWp", client="Demo", segment="utility", site="Rajasthan", grid_voltage_v=33000, dc_ac_ratio_target=1.3))["project_id"]
            await c.call("site_import", project_id=pid, file_path=str(GROUND))
            await c.call("site_set_setbacks", project_id=pid, boundary_m=10, exclusion_m=5)
            placed = await c.call("layout_place", project_id=pid, module=UTIL_MOD, tilt_deg=25, road_every_rows=12, target_dc_kwp=22000, block_dc_kwp=11000, modules_per_string=28)
            r = await c.call("network_autobuild", project_id=pid, template="utility_central", module=UTIL_MOD, inverter="sma-sc-4400-up")
            assert r["rules"]["error"] == 0 and r["design"]["strings_linked"] == placed["strings_planned"]
            s = await c.call("layout_summary", project_id=pid)
            assert s["strings_linked"] == placed["strings_planned"]
            v = await c.call("validate", project_id=pid)
            assert v["counts"]["error"] == 0

    asyncio.run(asyncio.wait_for(run(), 120))

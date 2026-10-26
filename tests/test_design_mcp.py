"""network_autobuild and design_strings over MCP stdio."""

import asyncio

from test_mcp import connect

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

            assert "800 V" in await c.fail("network_autobuild", project_id=pid, template="utility_string", ac_kw=20000, inverter="sungrow-sg350hx", grid_voltage_v=33000)

    asyncio.run(asyncio.wait_for(run(), 120))

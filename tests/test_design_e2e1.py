"""Regressions from the first end-to-end verifier run, replayed from its logged MCP tool arguments (stdio)."""

import asyncio
from collections import Counter
from pathlib import Path

from test_mcp import connect

ROOT = Path(__file__).resolve().parents[1]
PUNE = ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml"
RAJ = ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml"

A_CREATE = {
    "name": "800 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop",
    "site": "Shree Ganesh Textiles factory roof, MIDC Chakan, Pune", "grid_voltage_v": 415, "state": "Maharashtra",
    "address": "MIDC Chakan, Pune, Maharashtra", "utility": "MSEDCL", "client_code": "SGT", "project_id": "sgt-chakan",
    "ambient_min_c": 5, "ambient_max_c": 45, "altitude_m": 650, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.2, "power_factor": 0.99,
}  # fmt: skip
A_MOD, A_INV = "waaree-bin-08-580", "sungrow-sg110cx"
A_AUTOBUILD = {"project_id": "sgt-chakan", "template": "ci_rooftop", "ac_kw": 660, "module": A_MOD, "inverter": A_INV, "modules_per_string": 19, "strings_per_inverter": 12, "connection": "lt_panel"}
A_PLACE = {"project_id": "sgt-chakan", "module": A_MOD, "mounting": "rooftop_flush", "modules_per_string": 19, "strings_per_inverter": 12, "target_dc_kwp": 793.44}

B_CREATE = {
    "name": "40 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Phalodi, Rajasthan",
    "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPN", "client_code": "TRL", "project_id": "thar-40mw", "ambient_min_c": 2, "ambient_max_c": 48,
    "altitude_m": 230, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.01, "dc_ac_ratio_target": 1.2, "power_factor": 0.95,
}  # fmt: skip
B_MOD, B_INV = "waaree-bin-21-635", "sungrow-sg350hx"
B_PLACE = {
    "project_id": "thar-40mw", "module": B_MOD, "mounting": "fixed_tilt", "tilt_deg": 25, "table_rows": 2, "table_cols": 28, "road_every_rows": 12,
    "target_dc_kwp": 48646.08, "n_blocks": 6, "modules_per_string": 28, "strings_per_inverter": 24,
}  # fmt: skip
FAULT_33 = [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPN 33 kV fault level (brief)"}]


def run(coro, timeout=240):
    asyncio.run(asyncio.wait_for(coro, timeout))


async def scenario_a(c, **place):
    await c.call("project_create", **A_CREATE)
    await c.call("site_import", project_id="sgt-chakan", file_path=str(PUNE))
    await c.call("site_set_setbacks", project_id="sgt-chakan", roof_edge_m=1.0, obstacle_m=0.6)
    return await c.call("layout_place", **(A_PLACE | place))


def test_b4_ci_rooftop_autobuild_on_a_six_block_layout_numbers_inverters_once(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await scenario_a(c, table_rows=1, table_cols=19, half_tables=False, n_blocks=6)
            r = await c.call("network_autobuild", **A_AUTOBUILD)
            # the roof holds 6 x 12 strings without row walkways; with the maintenance walkway every 2 rows (close-e2e-3 R1) it holds whole inverters only
            inv = r["design"]["instances"]["inverter"]
            # the roof holds 661 of the brief's 793 kWp, so layout.shortfall (RV-M6) is the one error
            assert [f["rule_id"] for f in r["findings"] if f["severity"] == "error"] == ["layout.shortfall"] and inv >= 5 and r["design"]["strings_linked"] == 12 * inv
            net = await c.call("project_get", project_id="sgt-chakan", full=True)
            tags = Counter(n["tag"] for n in net["project"]["network"]["nodes"])
            assert all(v == 1 for v in tags.values()), tags
            assert not any("MV switchyard" in a for a in r["assumptions"]), r["assumptions"]

    run(go())


def test_rooftop_default_tables_hold_whole_strings_and_unstringable_tables_are_reported(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            placed = await scenario_a(c, table_rows=2, table_cols=19, orientation="portrait", n_blocks=6)
            assert placed["metrics"]["unstrung_modules"] == 0 and placed["metrics"]["tables_half"] == 0
            assert any("half tables" in a and "19" in a for a in placed["assumptions"]), placed["assumptions"]
            default = await c.call("layout_place", **A_PLACE)
            assert default["full_table"]["cols"] == 19 and default["metrics"]["unstrung_modules"] == 0
            bad = await c.call("layout_place", **(A_PLACE | {"table_rows": 1, "table_cols": 9, "half_tables": False}))
            assert any(p["severity"] == "error" and "cannot hold a string of 19" in p["message"] for p in bad["problems"]), bad["problems"]

    run(go())


def test_m18_rooftop_inverter_station_stands_on_a_roof_or_where_stated(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            placed = await scenario_a(c, table_rows=1, table_cols=19, half_tables=False, n_blocks=1)
            p = (await c.call("project_get", project_id="sgt-chakan", full=True))["project"]
            from shapely.geometry import Point, Polygon

            roofs = [Polygon([(q["x"], q["y"]) for q in f["polygon"]]) for f in p["site"]["geometry"]["features"] if f["kind"] == "roof"]
            st = placed["blocks"][0]["station"]
            assert any(r.covers(Point(st["x"], st["y"])) for r in roofs), (st, [r.bounds for r in roofs])
            stated = await c.call("layout_place", **(A_PLACE | {"table_rows": 1, "table_cols": 19, "half_tables": False, "n_blocks": 1, "station_x": 5.0, "station_y": 5.0}))
            assert stated["blocks"][0]["station"] == {"x": 5.0, "y": 5.0}

    run(go())


async def scenario_b(c):
    await c.call("project_create", **B_CREATE)
    await c.call("site_import", project_id="thar-40mw", file_path=str(RAJ))
    await c.call("site_set_setbacks", project_id="thar-40mw", boundary_m=10, exclusion_m=10)
    return await c.call("layout_place", **B_PLACE)


def test_m10_b3_layout_blocks_hold_whole_inverters_and_every_block_breaker_has_a_spec(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            placed = await scenario_b(c)
            blocks = await c.call("layout_blocks", project_id="thar-40mw")
            assert placed["strings_planned"] == 2736
            assert len(blocks["blocks"]) == 6
            for b in blocks["blocks"]:
                d = await c.call("layout_blocks", project_id="thar-40mw", block=b["id"])
                n = d["strings_truncated"] + len(d["strings"]) if d["strings"] else None
                assert n is None or n % 24 == 0, (b["id"], n)
            await c.call("project_design_basis", project_id="thar-40mw", fault_levels=FAULT_33)
            r = await c.call("network_autobuild", project_id="thar-40mw", template="utility_string", module=B_MOD, inverter=B_INV, modules_per_string=28, strings_per_inverter=24)
            inst = r["design"]["instances"]
            assert inst["inverter"] == 114, inst
            assert not [b for b in r["blockers"] if "BREAKER" in b.upper() or "acb" in b.lower()], r["blockers"]
            s = await c.call("network_summary", project_id="thar-40mw")
            assert not [i for i in s["nodes_without_spec"]["ids"] if "brk" in i], s["nodes_without_spec"]
            nodes = (await c.call("project_get", project_id="thar-40mw", full=True))["project"]["network"]["nodes"]
            from unicad.catalog.store import load_catalog

            cat = load_catalog()
            assert all(n["tag"].split("-")[0] == cat.get(n["spec"]).device.upper() for n in nodes if n["kind"] == "breaker"), [n["tag"] for n in nodes if n["kind"] == "breaker"][:8]
            v = await c.call("validate", project_id="thar-40mw")
            assert not [f for f in v["findings"] if f["severity"] == "error" and f["rule_id"].startswith("protection")], v["findings"]

    run(go(), 600)


def test_m12_design_basis_values_can_be_set_after_create_and_drop_their_placeholder(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await c.call("project_create", name="Placeholder Roof", client="Demo", segment="ci_rooftop", site="Roof", grid_voltage_v=415)
            pid = "placeholder-roof"
            assert any(a.startswith("ambient_min_c") for a in (await c.call("project_get", project_id=pid))["assumptions"])
            r = await c.call("project_design_basis", project_id=pid, ambient_min_c=5, ambient_max_c=45, altitude_m=650, vdrop_dc_max=0.015, vdrop_ac_max=0.015, power_factor=0.99, dc_ac_ratio_target=1.25)
            db = r["design_basis"]
            assert (db["ambient_min_c"], db["ambient_max_c"], db["altitude_m"], db["vdrop_dc_max"], db["power_factor"], db["dc_ac_ratio_target"]) == (5, 45, 650, 0.015, 0.99, 1.25)
            left = (await c.call("project_get", project_id=pid))["assumptions"]
            assert not any(a.split(" ")[0] in {"ambient_min_c", "ambient_max_c", "altitude_m", "vdrop_dc_max", "vdrop_ac_max", "power_factor", "dc_ac_ratio_target"} for a in left), left
            bad = await c.fail("project_design_basis", project_id=pid, ambient_min_c=50)
            assert "ambient_min_c" in bad

    run(go())


def test_m13_fault_levels_added_after_autobuild_ask_for_a_rebuild_and_the_rebuild_sizes_for_them(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await scenario_a(c, table_rows=1, table_cols=19, half_tables=False, n_blocks=1)
            await c.call("network_autobuild", **A_AUTOBUILD)
            fl = await c.call("project_design_basis", project_id="sgt-chakan", fault_levels=[{"voltage_v": 415, "fault_ka": 50, "clearing_s": 1, "source": "MSEDCL LT fault level (brief, raised to 50 kA so the placeholder breaking capacities fall short)"}])
            assert any("network_autobuild" in p["message"] for p in fl["problems"]), fl["problems"]
            v = await c.call("validate", project_id="sgt-chakan")
            errs = [f for f in v["findings"] if f["severity"] == "error"]
            assert errs and "network_autobuild" in v.get("next", ""), v.get("next")
            again = await c.call("network_autobuild", **A_AUTOBUILD)
            assert [f["rule_id"] for f in again["findings"] if f["severity"] == "error"] == ["layout.shortfall"], again["findings"]

    run(go())


def test_m25_catalog_search_rows_carry_almm_listed(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            rows = (await c.call("catalog_search", kind="module", limit=50))["rows"]
            assert rows and all("almm_listed" in r["key"] for r in rows)

    run(go())

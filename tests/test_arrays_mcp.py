from pathlib import Path

import pytest
from test_mcp import CREATE, session_test

SITES = Path(__file__).resolve().parents[1] / "fixtures" / "sites"
GROUND = str(SITES / "ground_mount_rajasthan.kml")
ROOFTOP = str(SITES / "rooftop_midc_pune.kml")
MODULE = "waaree-bi-55-540"


async def ground_project(c) -> str:
    pid = (await c.call("project_create", **{**CREATE, "segment": "utility", "grid_voltage_v": 33000}))["project_id"]
    await c.call("site_import", project_id=pid, file_path=GROUND)
    await c.call("site_set_setbacks", project_id=pid, boundary_m=10, exclusion_m=5)
    return pid


@session_test
async def test_layout_tools_are_listed_and_documented(c):
    tools = {t.name for t in (await c.session.list_tools()).tools}
    assert {"layout_place", "layout_summary", "layout_blocks"} <= tools
    docs = await c.session.read_resource("unicad://docs/tools")
    assert "### `layout_place`" in docs.contents[0].text


@session_test
async def test_place_ground_array_and_read_it_back(c):
    pid = await ground_project(c)
    res = await c.call("layout_place", project_id=pid, module=MODULE, tilt_deg=25, road_every_rows=12, target_dc_kwp=30000, n_blocks=2)
    assert res["changed"] and res["target_reached"]
    m = res["metrics"]
    assert abs(m["dc_kwp"] - 30000) / 30000 < 0.01 and m["block_balance"] < 0.05
    assert res["pitch"]["method"] == "solstice" and "winter solstice" in res["pitch"]["note"]
    assert res["strings_planned"] == m["strings"] and res["strings_linked"] == 0
    assert res["string_id_example"] == "B01-I1-S01"
    assert any("gap between tables" in a for a in res["assumptions"])

    summary = await c.call("layout_summary", project_id=pid)
    assert summary["dc_kwp"] == pytest.approx(m["dc_kwp"], abs=0.01)
    assert summary["tables_full"] == m["tables_full"] and summary["tables_half"] == m["tables_half"]
    assert summary["blocks"] == 2 and summary["table"] == "2P x 28"
    assert summary["row_pitch_m"] == pytest.approx(m["pitch_m"], abs=0.01)
    assert summary["gcr"] == pytest.approx(m["gcr"], abs=0.002)

    blocks = await c.call("layout_blocks", project_id=pid, block="B02")
    assert [b["id"] for b in blocks["blocks"]] == ["B01", "B02"]
    assert blocks["blocks"][1]["station"] is not None and "extent_m" in blocks["blocks"][1]
    n = blocks["blocks"][1]["tables_full"] + blocks["blocks"][1]["tables_half"]
    assert len(blocks["tables"]) == min(n, 200) and blocks["tables_truncated"] == max(0, n - 200)

    again = await c.call("layout_place", project_id=pid, module=MODULE, tilt_deg=25, road_every_rows=12, target_dc_kwp=30000, n_blocks=2)
    assert not again["changed"]


@session_test
async def test_network_strings_are_linked_to_planned_slots(c):
    pid = (await c.call("project_create", **CREATE))["project_id"]
    await c.call("site_import", project_id=pid, file_path=ROOFTOP)
    await c.call("network_add_group", project_id=pid, id="inv_sec", count=2, title="INVERTER SECTION")
    await c.call("network_add_node", project_id=pid, id="str", kind="pv_string", spec=MODULE, count=5, group="inv_sec", modules_in_series=24)
    res = await c.call("layout_place", project_id=pid, module=MODULE, mounting="rooftop_flush", table_cols=12, pitch_method="gap", row_gap_m=0.6, half_tables=False,
                       target_dc_kwp=10 * 24 * 0.54)  # fmt: skip
    assert res["strings_planned"] == 10 and res["strings_linked"] == 10
    blocks = await c.call("layout_blocks", project_id=pid, block="B01")
    assert {s["string"] for s in blocks["strings"]} == {f"str@{i}.{j}" for i in (1, 2) for j in range(1, 6)}
    assert all(sum(sp["n"] for sp in s["spans"]) == 24 for s in blocks["strings"])
    assert (await c.call("layout_summary", project_id=pid))["strings_linked"] == 10


@session_test
async def test_layout_errors_are_clear(c):
    pid = (await c.call("project_create", **CREATE))["project_id"]
    assert "no site geometry" in await c.fail("layout_place", project_id=pid, module=MODULE)
    assert "no array layout" in await c.fail("layout_summary", project_id=pid)
    await c.call("site_import", project_id=pid, file_path=GROUND)
    assert "no catalog entry" in await c.fail("layout_place", project_id=pid, module="nope")
    assert "needs pitch_m" in await c.fail("layout_place", project_id=pid, module=MODULE, pitch_method="explicit")

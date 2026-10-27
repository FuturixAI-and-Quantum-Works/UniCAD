import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

from unicad.mcp import Config, build_server
from unicad.mcp.docs import render_tools_markdown

ROOT = Path(__file__).resolve().parents[1]
MODULE = "adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp"
INVERTER = "growatt-max-100ktl3-x-lv"
CREATE = {"name": "100 kWp Test Roof", "client": "Test Client", "segment": "ci_rooftop", "site": "Test roof", "grid_voltage_v": 415}


class Client:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def call(self, tool: str, /, **args):
        res = await self.session.call_tool(tool, args)
        assert not res.is_error, res.content[0].text
        return res.structured_content

    async def fail(self, tool: str, /, **args) -> str:
        res = await self.session.call_tool(tool, args)
        assert res.is_error, f"{tool} unexpectedly succeeded: {res.structured_content}"
        return res.content[0].text


@asynccontextmanager
async def connect(workspace: Path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "unicad"], env={"UNICAD_WORKSPACE": str(workspace)})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield Client(session)


def session_test(fn):
    def wrapper(tmp_path):
        async def run():
            async with connect(tmp_path / "ws") as c:
                await fn(c)

        asyncio.run(asyncio.wait_for(run(), 120))

    wrapper.__name__ = fn.__name__
    return wrapper


async def build_roof(c: Client) -> str:
    pid = (await c.call("project_create", **CREATE))["project_id"]
    await c.call("network_add_group", project_id=pid, id="inv_sec", count=2, title="100 kW INVERTER SECTION")
    await c.call("network_add_node", project_id=pid, id="str", kind="pv_string", spec=MODULE, count=10, group="inv_sec", modules_in_series=18)
    await c.call("network_add_node", project_id=pid, id="inv", kind="inverter", spec=INVERTER, group="inv_sec")
    await c.call("network_add_node", project_id=pid, id="lt", kind="lt_panel", voltage_v=415)
    await c.call("network_add_node", project_id=pid, id="poi", kind="poi_grid")
    await c.call("network_connect", project_id=pid, src="str:out", dst="inv:dc")
    await c.call("network_connect", project_id=pid, src="inv:ac", dst="lt:in")
    await c.call("network_connect", project_id=pid, src="lt:out", dst="poi:in")
    return pid


@session_test
async def test_lists_tools_resources_prompts(c):
    tools = {t.name for t in (await c.session.list_tools()).tools}
    assert {
        "project_create", "project_get", "project_list", "project_revisions", "project_snapshot", "project_diff",
        "catalog_search", "catalog_get",
        "network_add_group", "network_add_node", "network_connect", "network_update", "network_remove", "network_summary",
    } <= tools  # fmt: skip
    templates = {t.uri_template for t in (await c.session.list_resource_templates()).resource_templates}
    assert {"unicad://catalog/{kind}", "unicad://project/{id}"} <= templates
    docs = await c.session.read_resource("unicad://docs/tools")
    assert "### `network_connect`" in docs.contents[0].text
    prompts = {p.name for p in (await c.session.list_prompts()).prompts}
    assert prompts == {"new_rooftop_project", "new_utility_project", "new_bess_project", "revise_after_comments"}
    plan = await c.session.get_prompt("new_rooftop_project", {"name": "Acme roof"})
    assert "Acme roof" in plan.messages[0].content.text and "network_autobuild" in plan.messages[0].content.text


@session_test
async def test_build_project_and_summarise(c):
    created = await c.call("project_create", **CREATE)
    assert created["project_id"] == "100-kwp-test-roof" and created["revision"] == 1 and created["changed"]
    assert any("ambient_min_c" in a for a in created["assumptions"])
    pid = await build_roof(c)

    s = await c.call("network_summary", project_id=pid)
    assert s["node_kinds"]["pv_string"] == {"templates": 1, "instances": 20}
    assert s["node_kinds"]["inverter"] == {"templates": 1, "instances": 2}
    assert s["node_kinds"]["lt_panel"]["instances"] == 1
    assert s["total_instances"] == 24
    assert s["edges_by_service"] == {"dc": 1, "lv_ac": 2}
    assert s["unconnected_ports"] == {"total": 0, "ports": []}
    assert s["nodes_without_spec"]["total"] == 0
    assert 415.0 in {z["voltage_v"] for z in s["voltage_zones"]}

    got = await c.call("project_get", project_id=pid, full=True)
    assert got["counts"] == {"groups": 1, "nodes": 4, "edges": 3}
    assert got["project"]["network"]["nodes"][0]["id"] == "str"
    assert (await c.call("project_list"))["projects"][0]["project_id"] == pid
    rows = json.loads((await c.session.read_resource(f"unicad://project/{pid}")).contents[0].text)
    assert rows["id"] == pid


@session_test
async def test_invalid_connect_names_the_bad_node(c):
    pid = await build_roof(c)
    before = (await c.call("project_get", project_id=pid))["revision"]
    msg = await c.fail("network_connect", project_id=pid, src="inv:ac", dst="ghost_panel:in")
    assert "ghost_panel" in msg and "Traceback" not in msg
    msg = await c.fail("network_connect", project_id=pid, src="inv:nope", dst="lt:in", service="lv_ac", edge_id="bad")
    assert "inv:nope" in msg and "Traceback" not in msg
    msg = await c.fail("network_add_node", project_id=pid, id="bad", kind="breaker", spec=INVERTER)
    assert INVERTER in msg and "breaker" in msg
    assert (await c.call("project_get", project_id=pid))["revision"] == before


@session_test
async def test_revisions_increase_only_on_real_change(c):
    pid = (await c.call("project_create", **CREATE))["project_id"]
    again = await c.call("project_create", **CREATE)
    assert again["revision"] == 1 and not again["changed"]
    assert "already exists" in await c.fail("project_create", **{**CREATE, "grid_voltage_v": 33000})

    first = await c.call("network_add_node", project_id=pid, id="lt", kind="lt_panel", voltage_v=415)
    assert first["revision"] == 2 and first["changed"] and first["added"] == ["lt"]
    repeat = await c.call("network_add_node", project_id=pid, id="lt", kind="lt_panel", voltage_v=415)
    assert repeat["revision"] == 2 and not repeat["changed"]
    assert "already exists" in await c.fail("network_add_node", project_id=pid, id="lt", kind="lt_panel", voltage_v=690)

    same = await c.call("network_update", project_id=pid, id="lt", fields={"voltage_v": 415})
    assert same["revision"] == 2 and not same["changed"]
    moved = await c.call("network_update", project_id=pid, id="lt", fields={"voltage_v": 400, "attributes": {"rating_a": 250}})
    assert moved["revision"] == 3 and moved["changed"]
    revs = await c.call("project_revisions", project_id=pid)
    assert [r["revision"] for r in revs["revisions"]] == [1, 2, 3] and revs["head"] == 3


@session_test
async def test_diff_between_revisions_shows_added_nodes(c):
    pid = (await c.call("project_create", **CREATE))["project_id"]
    await c.call("project_snapshot", project_id=pid, label="empty")
    await build_roof(c)
    d = await c.call("project_diff", project_id=pid, from_revision="empty")
    assert d["from_revision"] == 1 and not d["empty"]
    assert d["nodes"]["added"] == ["str", "inv", "lt", "poi"]
    assert d["edges"]["added"] == ["e_str_inv", "e_inv_lt", "e_lt_poi"]
    assert d["groups"]["added"] == ["inv_sec"]
    assert (await c.call("project_diff", project_id=pid, from_revision=1, to_revision=1))["empty"]
    snap = await c.call("project_snapshot", project_id=pid, label="empty", revision=1)
    assert snap["revision"] == 1
    assert "already names revision 1" in await c.fail("project_snapshot", project_id=pid, label="empty", revision=2)


@session_test
async def test_remove_cascades_edges_and_reports(c):
    pid = await build_roof(c)
    msg = await c.fail("network_remove", project_id=pid, ids=["inv_sec"])
    assert "str" in msg and "inv" in msg and "include_members" in msg
    r = await c.call("network_remove", project_id=pid, ids=["inv"])
    assert r["removed"]["nodes"] == ["inv"] and r["cascaded_edges"] == ["e_inv_lt", "e_str_inv"] and r["summary"]["edges"] == 1
    r = await c.call("network_remove", project_id=pid, ids=["inv_sec"], include_members=True)
    assert r["removed"]["nodes"] == ["str"] and r["removed"]["groups"] == ["inv_sec"]
    assert "no node" in await c.fail("network_remove", project_id=pid, ids=["inv"])


@session_test
async def test_advisories_and_update_errors(c):
    pid = (await c.call("project_create", **CREATE))["project_id"]
    r = await c.call("network_add_node", project_id=pid, id="inv", kind="inverter")
    assert r["problems"] and r["problems"][0]["subject"] == "inv" and "catalog" in r["problems"][0]["message"]
    msg = await c.fail("network_update", project_id=pid, id="inv", fields={"colour": "red"})
    assert "colour" in msg
    msg = await c.fail("network_update", project_id=pid, id="inv", fields={"spec": "no-such-part"})
    assert "no-such-part" in msg
    r = await c.call("network_update", project_id=pid, id="inv", fields={"spec": INVERTER})
    assert r["problems"] == []


@session_test
async def test_catalog_tools(c):
    r = await c.call("catalog_search", kind="inverter", min_power=90, max_power=110, limit=5)
    assert r["total"] >= 1 and r["shown"] <= 5
    assert all(row["kind"] == "inverter" and 90 <= row["power"] <= 110 and row["power_unit"] == "kVA" for row in r["rows"])
    r = await c.call("catalog_search", text="growatt max 100ktl3", kind="inverter")
    assert INVERTER in {row["id"] for row in r["rows"]}
    spec = await c.call("catalog_get", id=INVERTER)
    assert spec["id"] == INVERTER and spec["kind"] == "inverter" and "source" in spec
    assert "no catalog entry" in await c.fail("catalog_get", id="growatt-max-100")
    assert "Input should be 'module', 'inverter'" in await c.fail("catalog_search", kind="widget")
    res = json.loads((await c.session.read_resource("unicad://catalog/meter")).contents[0].text)
    assert res["kind"] == "meter" and res["count"] == len(res["rows"]) > 0


@session_test
async def test_connect_infers_service_or_asks(c):
    pid = await build_roof(c)
    await c.call("network_add_node", project_id=pid, id="x", kind="breaker")
    await c.call("network_add_node", project_id=pid, id="y", kind="breaker")
    assert "pass service" in await c.fail("network_connect", project_id=pid, src="x:out", dst="y:in")
    r = await c.call("network_connect", project_id=pid, src="x:out", dst="y:in", service="lv_ac")
    assert r["service_inferred"] is False and r["edge_id"] == "e_x_y"


def test_tool_docs_are_current():
    server = build_server(Config(workspace=Path("/nonexistent")))
    expected = asyncio.run(render_tools_markdown(server))
    actual = (ROOT / "docs" / "mcp-tools.md").read_text(encoding="utf-8")
    assert actual == expected, "docs/mcp-tools.md is stale: run `uv run unicad-mcp --write-docs docs/mcp-tools.md`"


def test_cli_help():
    out = subprocess.run([sys.executable, "-m", "unicad", "--help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "UNICAD_WORKSPACE" in out.stdout

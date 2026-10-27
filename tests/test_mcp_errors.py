"""Tool errors reach the agent with something to act on: the domain's message, or for a crash its type, message and where it was
raised. close-e2e-1 saw bare '3' (KeyError) and an empty 'Error executing tool drawings_render' (RuntimeError)."""

import asyncio
import re
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from test_design_mcp import UTIL_MOD
from test_mcp import connect
from unicad.mcp import Config, build_server
from unicad.mcp.errors import guarded


def _raise(exc: BaseException):
    @guarded
    def tool() -> None:
        raise exc

    with pytest.raises(ToolError) as info:
        tool()
    return str(info.value)


def test_a_crash_carries_its_type_message_and_where_it_was_raised():
    text = _raise(RuntimeError("planned 2 register pages, drew 1"))
    assert re.fullmatch(r"RuntimeError: planned 2 register pages, drew 1 \(at test_mcp_errors\.py:\d+ in tool\)", text), text
    assert _raise(KeyError(3)).startswith("KeyError: 3 (at ")
    assert _raise(KeyError("acdb")).startswith("KeyError: 'acdb' (at ")
    assert _raise(ValueError()).startswith("ValueError: no message (at ")
    assert _raise(AssertionError()).startswith("AssertionError: no message (at ")


def test_anticipated_failures_keep_their_own_wording():
    assert _raise(KeyError("no project 'x' in /ws; project_list shows what exists")) == "no project 'x' in /ws; project_list shows what exists"
    assert _raise(ValueError("layout block B01 has 465 strings")) == "layout block B01 has 465 strings"


def test_every_registered_tool_goes_through_the_error_wrapper():
    server = build_server(Config(workspace=Path("/nonexistent")))
    tools = server._tool_manager.list_tools()
    assert len(tools) > 30
    assert [t.name for t in tools if not getattr(t.fn, "__unicad_guarded__", False)] == []


def test_utility_string_block_with_an_unmatched_acb_renders_its_sld_over_stdio(tmp_path):
    """close-e2e-1 B3 from the logged tool calls: 20 x SG350HX per block leaves the block ACB without a catalog match."""

    async def run():
        async with connect(tmp_path / "ws") as c:
            pid = (await c.call("project_create", name="Thar 7 MWac", client="Thar Renewables Ltd", segment="utility", site="Phalodi, Rajasthan", grid_voltage_v=33000))["project_id"]
            built = await c.call("network_autobuild", project_id=pid, template="utility_string", ac_kw=7040, module="waaree-bin-21-635", inverter="sungrow-sg350hx",
                                 modules_per_string=28, inverters_per_block=20)  # fmt: skip
            assert built["changed"]
            r = await c.call("drawings_render", project_id=pid, kinds=["sld"], formats=["dxf"])
            warnings = [w for s in r["sheets"] for w in s["warnings"]]
            assert any("(breaker) has no catalog spec" in w and "RATING TO BE SPECIFIED" in w for w in warnings), warnings

    asyncio.run(asyncio.wait_for(run(), 120))


def test_utility_central_with_two_scb_types_renders_its_sld_over_stdio(tmp_path):
    """close-e2e-1 B2: utility_central on a placed layout whose inverters collect two SCB types failed with the bare text '3'."""
    ground = Path(__file__).resolve().parents[1] / "fixtures" / "sites" / "ground_mount_rajasthan.kml"

    async def run():
        async with connect(tmp_path / "ws") as c:
            pid = (await c.call("project_create", name="Rajasthan 9 MWp", client="Demo", segment="utility", site="Rajasthan", grid_voltage_v=33000))["project_id"]
            await c.call("site_import", project_id=pid, file_path=str(ground))
            await c.call("layout_place", project_id=pid, module=UTIL_MOD, table_rows=2, table_cols=27, tilt_deg=25, n_blocks=2, modules_per_string=27,
                         road_every_rows=12, target_dc_kwp=2 * 144 * 27 * 0.58 * 1.1)  # fmt: skip
            await c.call("network_autobuild", project_id=pid, template="utility_central", module=UTIL_MOD, inverter="sma-sc-4400-up", modules_per_string=27)
            r = await c.call("drawings_render", project_id=pid, kinds=["sld"], formats=["dxf"])
            assert r["sheet_count"] >= 2

    asyncio.run(asyncio.wait_for(run(), 180))

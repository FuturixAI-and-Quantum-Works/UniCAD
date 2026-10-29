"""Tool modules are found by scanning unicad.mcp.tools; instructions and prompts only name tools that exist."""

import asyncio
import re
import sys
import textwrap
from pathlib import Path

import pytest

import unicad.mcp.registry as registry
import unicad.mcp.tools as tools_package
from unicad.mcp import Config, build_server
from unicad.mcp.instructions import INSTRUCTIONS
from unicad.mcp.prompts import BESS, REVISE, ROOFTOP, UTILITY

PROBE = textwrap.dedent(
    '''
    from mcp.server.mcpserver import MCPServer

    def register(server: MCPServer, ws) -> None:
        @server.tool()
        def zz_probe_ping() -> str:
            """Probe tool."""
            return "pong"
    '''
)


def tool_names(server) -> list[str]:
    return [t.name for t in asyncio.run(server.list_tools())]


def build():
    return build_server(Config(workspace=Path("/nonexistent")))


@pytest.fixture
def extra_tools_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_package, "__path__", [*tools_package.__path__, str(tmp_path)])
    yield tmp_path
    for name in [m for m in sys.modules if m.startswith("unicad.mcp.tools.zz_")]:
        del sys.modules[name]


def test_every_tools_module_with_register_is_loaded_in_sorted_order():
    modules = registry.tool_modules()
    assert list(modules) == sorted(modules)
    assert {"unicad.mcp.tools.site", "unicad.mcp.tools.layout", "unicad.mcp.tools.project", "unicad.mcp.tools.earthing"} <= set(modules)
    assert "unicad.mcp.tools.params" not in modules
    assert not set(modules) & set(registry.MODULES)


def test_site_and_layout_tools_live_under_tools():
    root = Path(tools_package.__file__).parent.parent
    assert not (root / "tools_site.py").exists() and not (root / "tools_layout.py").exists()
    names = set(tool_names(build()))
    assert {"site_import", "layout_place", "layout_blocks"} <= names


def test_a_new_module_registers_without_editing_the_registry(extra_tools_dir):
    assert "zz_probe_ping" not in tool_names(build())
    (extra_tools_dir / "zz_probe.py").write_text(PROBE, encoding="utf-8")
    server = build()
    assert tool_names(server).count("zz_probe_ping") == 1
    assert "unicad.mcp.tools.zz_probe" in registry.module_names()


def test_a_module_without_register_is_ignored(extra_tools_dir):
    (extra_tools_dir / "zz_helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert "unicad.mcp.tools.zz_helpers" not in registry.tool_modules()
    assert tool_names(build()) == tool_names(build())


def test_an_explicit_modules_entry_for_a_tools_module_is_not_registered_twice(monkeypatch):
    baseline = tool_names(build())
    monkeypatch.setattr(registry, "MODULES", ("unicad.mcp.tools.site", *registry.MODULES))
    assert tool_names(build()) == baseline
    assert len(registry.module_names()) == len(set(registry.module_names()))


def test_explicit_entries_outside_tools_still_register(monkeypatch, extra_tools_dir):
    (extra_tools_dir / "zz_probe.py").write_text(PROBE, encoding="utf-8")
    monkeypatch.setattr(registry, "MODULES", (*registry.MODULES, "unicad.mcp.tools.zz_probe"))
    assert tool_names(build()).count("zz_probe_ping") == 1


def referenced_tools(text: str, server) -> set[str]:
    tools = set(tool_names(server))
    params = {p for t in asyncio.run(server.list_tools()) for p in t.input_schema.get("properties", {})}
    prefixes = {t.split("_", 1)[0] for t in tools}
    words = set(re.findall(r"\b[a-z]+(?:_[a-z]+)*\b", text))
    return {w for w in words if (w in tools or w.split("_", 1)[0] in prefixes and "_" in w) and w not in params}


def test_instructions_and_prompts_name_only_real_tools():
    server = build()
    tools = set(tool_names(server))
    pending = {"drawings_render"} - tools
    for text in (INSTRUCTIONS, ROOFTOP, UTILITY, BESS, REVISE):
        unknown = referenced_tools(text, server) - tools - pending
        assert not unknown, unknown


def test_instructions_walk_the_whole_workflow_in_order():
    order = ["project_create", "project_design_basis", "site_import", "layout_place", "network_autobuild", "validate", "earthing_design", "lps_design", "cable_schedule", "bom", "project_snapshot"]
    positions = [INSTRUCTIONS.index(name) for name in order]
    assert positions == sorted(positions)
    for name in ("drawings_render", "explain_finding", "site_set_setbacks", "design_strings", "project_diff", "new_bess_project", "revise_after_comments"):
        assert name in INSTRUCTIONS


@pytest.mark.parametrize("text", [ROOFTOP, UTILITY], ids=["rooftop", "utility"])
def test_pv_prompts_set_the_design_basis_before_the_design_as_the_instructions_do(text):
    """close-e2e-1 M13: autobuild ran before the fault levels were set, so cables were not sized for the short circuit."""
    order = ["project_create", "project_design_basis", "site_import", "layout_place", "network_autobuild", "validate", "earthing_design", "lps_design", "project_snapshot", "project_issue", "drawings_render"]
    positions = [text.index(name) for name in order]
    assert positions == sorted(positions)
    basis = text[text.index("project_design_basis"):text.index("site_import")]
    assert all(w in basis for w in ("fault_levels", "earthing", "lps", "ambient_min_c"))


@pytest.mark.parametrize("text", [INSTRUCTIONS, ROOFTOP, UTILITY, BESS, REVISE], ids=["instructions", "rooftop", "utility", "bess", "revise"])
def test_the_guidance_ends_by_issuing_then_rendering_the_set(text):
    """close-e2e-4: the instructions and every prompt ended at project_snapshot, so an agent following them never set the revision, names
    and date; each now ends with project_issue then drawings_render, and nothing is rendered before the issue."""
    server = build()
    tools = set(tool_names(server))
    last = sorted((text.rindex(t), t) for t in referenced_tools(text, server) & tools)
    assert [t for _, t in last[-2:]] == ["project_issue", "drawings_render"], last[-4:]
    assert text.index("drawings_render") > text.index("project_issue")


def test_prompts_cover_each_project_kind_and_the_review_loop():
    async def go():
        server = build()
        names = {p.name for p in await server.list_prompts()}
        rooftop = await server.get_prompt("new_rooftop_project", {"name": "Acme roof"})
        revise = await server.get_prompt("revise_after_comments", {"project_id": "acme", "comments": "1. Move the ACDB"})
        bess = await server.get_prompt("new_bess_project", {})
        return names, rooftop, revise, bess

    names, rooftop, revise, bess = asyncio.run(go())
    assert names == {"new_rooftop_project", "new_utility_project", "new_bess_project", "revise_after_comments"}
    text = rooftop.messages[0].content.text
    assert "Acme roof" in text and all(t in text for t in ("site_import", "layout_place", "network_autobuild", "validate", "earthing_design", "lps_design", "cable_schedule", "bom"))
    text = revise.messages[0].content.text
    assert "acme" in text and "Move the ACDB" in text and "project_diff" in text and "project_snapshot" in text
    assert "bess_ac" in bess.messages[0].content.text

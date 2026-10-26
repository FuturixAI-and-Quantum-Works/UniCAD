from typing import Any

from mcp.server.mcpserver import MCPServer

HEADER = """# UniCAD MCP tools

Generated from the registered tools by `uv run unicad-mcp --write-docs docs/mcp-tools.md`; `tests/test_mcp.py` fails when this file is out of date. Do not edit by hand.

Mutating tools return `{project_id, revision, changed, summary, problems}`. `changed` is false and `revision` stays put when the edit leaves the model identical. Failures come back as tool errors carrying the domain's message.
"""


def _type(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in prop:
        return _type(defs[prop["$ref"].rsplit("/", 1)[1]], defs)
    if "enum" in prop:
        return " | ".join(f"`{v}`" for v in prop["enum"])
    for key in ("anyOf", "oneOf"):
        if key in prop:
            parts = [_type(o, defs) for o in prop[key] if o.get("type") != "null"]
            return " or ".join(dict.fromkeys(parts))
    t = prop.get("type", "any")
    if t == "array":
        return f"list of {_type(prop.get('items', {}), defs)}"
    return str(t)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _tool_section(tool: Any) -> list[str]:
    schema = tool.input_schema
    required = set(schema.get("required", []))
    defs = schema.get("$defs", {})
    lines = [f"### `{tool.name}`", "", (tool.description or "").strip(), ""]
    if tool.annotations and tool.annotations.read_only_hint:
        lines += ["Read-only.", ""]
    props = schema.get("properties", {})
    if props:
        lines += ["| Parameter | Type | Required | Description |", "| --- | --- | --- | --- |"]
        for name, prop in props.items():
            default = "" if name in required or "default" not in prop or prop["default"] in (None, "") else f"Default: `{prop['default']}`."
            lines.append(f"| `{name}` | {_cell(_type(prop, defs))} | {'yes' if name in required else 'no'} | {_cell(prop.get('description', '') + ' ' + default)} |")
        lines.append("")
    return lines


async def render_tools_markdown(server: MCPServer) -> str:
    tools = await server.list_tools()
    lines = [HEADER]
    group = None
    for tool in tools:
        prefix = tool.name.split("_", 1)[0]
        if prefix != group:
            group = prefix
            lines += [f"## {prefix.capitalize()} tools", ""]
        lines += _tool_section(tool)
    resources = [(r.uri_template, r.description) for r in await server.list_resource_templates()]
    resources += [(r.uri, r.description) for r in await server.list_resources()]
    if resources:
        lines += ["## Resources", ""]
        lines += [f"- `{uri}`: {(desc or '').strip()}" for uri, desc in resources]
        lines.append("")
    prompts = await server.list_prompts()
    if prompts:
        lines += ["## Prompts", ""]
        lines += [f"- `{p.name}`: {(p.description or '').strip()}" for p in prompts]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

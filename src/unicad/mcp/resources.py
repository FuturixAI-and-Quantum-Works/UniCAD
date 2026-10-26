import json

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError

from unicad.mcp.docs import render_tools_markdown
from unicad.mcp.tools.catalog import KINDS, row
from unicad.mcp.workspace import Workspace


def register(server: MCPServer, ws: Workspace) -> None:
    @server.resource("unicad://catalog/{kind}", mime_type="application/json")
    def catalog_kind(kind: str) -> str:
        """Every catalog entry of one kind as compact rows."""
        if kind not in KINDS:
            raise ResourceError(f"unknown catalog kind {kind!r}; use one of {', '.join(KINDS)}")
        rows = [row(e) for e in sorted(ws.catalog.filter(kind), key=lambda e: e.id)]
        return json.dumps({"kind": kind, "count": len(rows), "rows": rows}, indent=1)

    @server.resource("unicad://project/{id}", mime_type="application/json")
    def project_json(id: str) -> str:
        """The complete project model JSON at its latest revision."""
        try:
            project, _ = ws.load(id)
        except (KeyError, ValueError) as e:
            raise ResourceError(str(e.args[0]) if isinstance(e, KeyError) else str(e)) from None
        return json.dumps(project.model_dump(mode="json", exclude_defaults=True), indent=1)

    @server.resource("unicad://docs/tools", mime_type="text/markdown")
    async def tools_doc() -> str:
        """Reference for every tool, resource and prompt this server exposes."""
        return await render_tools_markdown(server)

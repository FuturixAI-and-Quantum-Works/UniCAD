"""Tool modules, in registration order. A module exposes `register(server, workspace)`.

Add a line here to plug in a new group of tools, resources or prompts (rules engine, drawing, ingest).
"""

import importlib
from collections.abc import Callable
from typing import cast

from mcp.server.mcpserver import MCPServer

from unicad.mcp.config import Config
from unicad.mcp.workspace import Workspace

MODULES = (
    "unicad.mcp.tools.project",
    "unicad.mcp.tools.catalog",
    "unicad.mcp.tools.network",
    "unicad.mcp.tools_site",
    "unicad.mcp.tools.rules",
    "unicad.mcp.resources",
    "unicad.mcp.prompts",
)

INSTRUCTIONS = (
    "UniCAD builds Indian solar EPC drawing sets from a typed project model. "
    "State intent through the tools (project_create, then network_* to build the electrical network); never give coordinates. "
    "Use catalog_search for equipment ids. Each edit returns the new revision and any problems."
)


def build_server(config: Config | None = None) -> MCPServer:
    ws = Workspace(config or Config.from_env())
    server = MCPServer("unicad", instructions=INSTRUCTIONS)
    for name in MODULES:
        cast(Callable[[MCPServer, Workspace], None], importlib.import_module(name).register)(server, ws)
    return server

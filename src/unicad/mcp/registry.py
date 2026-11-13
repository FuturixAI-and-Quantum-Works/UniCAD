"""Server assembly.

Every module in `unicad.mcp.tools` that defines `register(server, workspace)` is loaded in sorted name order; dropping a file there
is all a new tool group needs. `MODULES` lists the modules outside that package (resources, prompts) and may repeat a tools module.
"""

import importlib
import pkgutil
from collections.abc import Callable
from importlib import metadata
from typing import cast

from mcp.server.mcpserver import MCPServer

import unicad.mcp.tools
from unicad.mcp.config import Config
from unicad.mcp.instructions import INSTRUCTIONS
from unicad.mcp.workspace import Workspace

MODULES = (
    "unicad.mcp.resources",
    "unicad.mcp.prompts",
)

Register = Callable[[MCPServer, Workspace], None]


def tool_modules() -> tuple[str, ...]:
    names = sorted(m.name for m in pkgutil.iter_modules(unicad.mcp.tools.__path__, "unicad.mcp.tools."))
    return tuple(n for n in names if hasattr(importlib.import_module(n), "register"))


def module_names() -> tuple[str, ...]:
    return tuple(dict.fromkeys(tool_modules() + MODULES))


def _version() -> str:
    try:
        return metadata.version("unicad")
    except metadata.PackageNotFoundError:
        return ""


def build_server(config: Config | None = None) -> MCPServer:
    ws = Workspace(config or Config.from_env())
    server = MCPServer("unicad", instructions=INSTRUCTIONS, version=_version())
    for name in module_names():
        cast(Register, importlib.import_module(name).register)(server, ws)
    return server

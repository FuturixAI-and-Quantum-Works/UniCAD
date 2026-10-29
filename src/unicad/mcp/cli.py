import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from unicad.mcp.config import BUNDLED_DATA, DEFAULT_WORKSPACE, Config
from unicad.mcp.docs import render_tools_markdown
from unicad.mcp.registry import build_server

EPILOG = f"""Environment:
  UNICAD_WORKSPACE  project directory (default {DEFAULT_WORKSPACE})
  UNICAD_CATALOG    catalog directory override
  UNICAD_PROFILES   extra company-profile directories, separated by '{os.pathsep}'
  UNICAD_TOKEN      bearer token required by --http (unless --no-auth)
  LIBRECAD          LibreCAD executable (librecad.exe on Windows) or AppRun used to render drawings
  LIBREDWG_BIN      directory holding LibreDWG's dxf2dwg and dwg2dxf
  DISPLAY           Linux only: an X display for LibreCAD; run under xvfb-run on a headless host
"""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="unicad-mcp",
        description="UniCAD MCP server. Speaks the Model Context Protocol on stdin/stdout, or over Streamable HTTP with --http.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--workspace", type=Path, help="project workspace directory (overrides UNICAD_WORKSPACE)")
    p.add_argument("--catalog", type=Path, help="catalog directory (overrides UNICAD_CATALOG)")
    p.add_argument("--http", action="store_true", help="serve Streamable HTTP at /mcp instead of stdio")
    p.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default %(default)s)")
    p.add_argument("--port", type=int, default=8765, help="HTTP port (default %(default)s)")
    p.add_argument("--tenants", action="store_true", help="HTTP: give each X-UniCAD-Tenant its own workspace subdirectory; the header becomes required")
    p.add_argument("--stateless", action="store_true", help="HTTP: no server-side sessions; every request stands alone")
    p.add_argument("--no-auth", action="store_true", help="HTTP: serve without a bearer token (otherwise UNICAD_TOKEN is required)")
    p.add_argument("--print-docs", action="store_true", help="print the tool reference as Markdown and exit")
    p.add_argument("--write-docs", type=Path, metavar="PATH", help="write the tool reference to PATH and exit")
    p.add_argument("--write-workflow", type=Path, metavar="PATH", help="run the worked example against a temporary workspace, write the agent workflow guide to PATH and exit")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.getLogger("ezdxf").setLevel(logging.WARNING)
    profiles = BUNDLED_DATA / "profiles"
    if profiles.is_dir():
        os.environ.setdefault("UNICAD_PROFILES", str(profiles))
    config = Config.from_env()
    config = Config(
        workspace=args.workspace.resolve() if args.workspace else config.workspace,
        catalog=args.catalog.resolve() if args.catalog else config.catalog,
    )
    if args.write_workflow:
        from unicad.mcp.workflow_doc import render_workflow_markdown

        args.write_workflow.write_text(asyncio.run(render_workflow_markdown()), encoding="utf-8")
        return
    server = build_server(config)
    if args.print_docs or args.write_docs:
        text = asyncio.run(render_tools_markdown(server))
        if args.write_docs:
            args.write_docs.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return
    if args.http:
        from unicad.mcp.http import build_http_app, serve

        token = os.environ.get("UNICAD_TOKEN") or None
        if token is None and not args.no_auth:
            sys.exit("unicad-mcp --http needs a bearer token: set UNICAD_TOKEN, or pass --no-auth to serve without one")
        serve(build_http_app(server, None if args.no_auth else token, args.tenants, args.stateless, args.host), args.host, args.port)
        return
    server.run("stdio")

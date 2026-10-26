import argparse
import asyncio
import sys
from pathlib import Path

from unicad.mcp.config import DEFAULT_WORKSPACE, Config
from unicad.mcp.docs import render_tools_markdown
from unicad.mcp.registry import build_server


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="unicad-mcp",
        description="UniCAD MCP server (stdio). Speaks the Model Context Protocol on stdin/stdout.",
        epilog=f"Environment: UNICAD_WORKSPACE (project directory, default {DEFAULT_WORKSPACE}), UNICAD_CATALOG (catalog directory override).",
    )
    p.add_argument("--workspace", type=Path, help="project workspace directory (overrides UNICAD_WORKSPACE)")
    p.add_argument("--catalog", type=Path, help="catalog directory (overrides UNICAD_CATALOG)")
    p.add_argument("--print-docs", action="store_true", help="print the tool reference as Markdown and exit")
    p.add_argument("--write-docs", type=Path, metavar="PATH", help="write the tool reference to PATH and exit")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = Config.from_env()
    config = Config(
        workspace=args.workspace.resolve() if args.workspace else config.workspace,
        catalog=args.catalog.resolve() if args.catalog else config.catalog,
    )
    server = build_server(config)
    if args.print_docs or args.write_docs:
        text = asyncio.run(render_tools_markdown(server))
        if args.write_docs:
            args.write_docs.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return
    server.run("stdio")

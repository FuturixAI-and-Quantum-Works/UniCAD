"""Drive an installed unicad-mcp over stdio with the official MCP client: build a small rooftop project
through the tools, render its SLD and layout to DXF, PDF and DWG, and check every file.

Usage: python smoke.py [--server PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pymupdf
from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml"
MODULE = "adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp"
FORWARDED = ("LIBRECAD", "LIBREDWG_BIN", "DISPLAY", "PATH", "LOCALAPPDATA", "ProgramFiles", "USERPROFILE", "HOME")


async def call(session: ClientSession, tool: str, **args) -> dict:
    res = await session.call_tool(tool, args)
    if res.is_error:
        raise SystemExit(f"{tool} failed: {res.content[0].text}")
    print(f"ok  {tool}")
    return res.structured_content


async def smoke(server: str, workspace: Path) -> dict:
    env = {k: os.environ[k] for k in FORWARDED if k in os.environ} | {"UNICAD_WORKSPACE": str(workspace)}
    params = StdioServerParameters(command=server, args=[], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        pid = (await call(s, "project_create", name="Windows smoke rooftop", client="Demo Industries", segment="ci_rooftop",
                          site="Plot B-12, MIDC Pune", grid_voltage_v=415, state="Maharashtra"))["project_id"]
        await call(s, "site_import", project_id=pid, file_path=str(SITE))
        await call(s, "site_set_setbacks", project_id=pid, roof_edge_m=1.0)
        await call(s, "project_design_basis", project_id=pid,
                   fault_levels=[{"voltage_v": 415, "fault_ka": 10, "clearing_s": 1, "source": "smoke test input"}])
        await call(s, "layout_place", project_id=pid, module=MODULE, mounting="rooftop_flush", table_cols=10, pitch_method="gap",
                   row_gap_m=0.6, half_tables=False, target_dc_kwp=244.2, modules_per_string=20)
        await call(s, "network_autobuild", project_id=pid, template="ci_rooftop", dc_kwp=244.2, module=MODULE, inverter="huawei-sun2000-100ktl-m2")
        return await call(s, "drawings_render", project_id=pid, kinds=["sld", "layout"], formats=["dxf", "pdf", "dwg"])


def check(result: dict, out: Path) -> list[str]:
    problems: list[str] = []
    kinds = {s["kind"] for s in result["sheets"]}
    if not {"sld", "layout"} <= kinds:
        problems.append(f"expected sld and layout sheets, got {sorted(kinds)}")
    out.mkdir(parents=True, exist_ok=True)
    for sheet in result["sheets"]:
        for fmt in ("dxf", "pdf", "dwg"):
            path = sheet["files"].get(fmt)
            if not path or not Path(path).is_file() or Path(path).stat().st_size == 0:
                problems.append(f"{sheet['number']}: no {fmt} ({path}); warnings: {sheet.get('warnings')}")
                continue
            if fmt == "pdf":
                with pymupdf.open(path) as doc:
                    if len(doc) != 1:
                        problems.append(f"{path} has {len(doc)} pages, expected 1")
                shutil.copy2(path, out / Path(path).name)
    set_pdf = result.get("set_pdf")
    if not set_pdf or not Path(set_pdf).is_file():
        problems.append(f"no set PDF ({set_pdf})")
    else:
        with pymupdf.open(set_pdf) as doc:
            if len(doc) != len(result["sheets"]):
                problems.append(f"set PDF has {len(doc)} pages for {len(result['sheets'])} sheets")
        shutil.copy2(set_pdf, out / Path(set_pdf).name)
    for sheet in result["sheets"]:
        print(f"    {sheet['number']}  {sheet['paper']}  {', '.join(f'{k}={Path(v).stat().st_size}' for k, v in sheet['files'].items() if v)}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=shutil.which("unicad-mcp"), help="unicad-mcp executable (default: the one on PATH)")
    ap.add_argument("--out", type=Path, default=Path("smoke-pdfs"), help="directory the rendered PDFs are copied to")
    args = ap.parse_args()
    if not args.server:
        sys.exit("unicad-mcp is not on PATH; pass --server")
    with tempfile.TemporaryDirectory(prefix="unicad-smoke-") as tmp:
        result = asyncio.run(asyncio.wait_for(smoke(args.server, Path(tmp) / "ws"), 900))
        (args.out).mkdir(parents=True, exist_ok=True)
        (args.out / "drawings_render.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        problems = check(result, args.out)
    if problems:
        sys.exit("smoke failed:\n  " + "\n  ".join(problems))
    print(f"smoke passed: {len(result['sheets'])} sheets, PDFs in {args.out}")


if __name__ == "__main__":
    main()

"""Build the full drawing-set samples in library/sets/ by driving the MCP server over stdio the way an agent does, from the stored project alone:

  rooftop_500kwp   the 500 kWp rooftop fixture + the Pune roof site + layout_place + earthing and lightning inputs
  utility_string   project_create + the Rajasthan site + layout_place + network_autobuild (string inverters) + earthing and lightning inputs
  rooftop_acme     the rooftop fixture without its issued sheets, as a new project of the bundled Acme Solar EPC profile (company_assign:
                   its title block, numbers, layers, symbols), then the same flow as rooftop_500kwp

then drawings_render(kinds=["all"]). Per sample, library/sets/<name>/ holds every sheet as DXF and PDF, set.pdf, index.json and a PNG of
the register and of the first sheet of each kind (preview/). Earthing, lightning and fault-level inputs are illustrative sample values,
not site data. Set LIBRECAD to the AppRun/binary."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "library" / "sets"
SITES = ROOT / "fixtures" / "sites"
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
ROOF_MODULE = "waaree-bi-55-540"
GROUND_MODULE = "vikram-solar-hypersol-m10r-144-580-wp"
STRING_INVERTER = "sungrow-sg350hx"
SAMPLE = "SAMPLE VALUE, NOT SITE DATA"

ROOF_BASIS = {
    "earthing": {"soil_resistivity_ohm_m": 50.0, "fault_current_ka": 10.0, "fault_clearing_s": 0.5},
    "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 30.0, "mast_height_m": 5.0},
    "fault_levels": [{"voltage_v": 415, "fault_ka": 25.0, "clearing_s": 0.5, "source": SAMPLE}],
}
GROUND_BASIS = {
    "earthing": {"soil_resistivity_ohm_m": 50.0, "fault_current_ka": 12.5, "fault_clearing_s": 1.0, "surface_resistivity_ohm_m": 3000.0, "surface_layer_m": 0.15},
    "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60.0, "mast_height_m": 6.0},
    "fault_levels": [{"voltage_v": 33000, "fault_ka": 25.0, "clearing_s": 1.0, "source": SAMPLE}],
}


class Client:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def call(self, tool: str, /, **args):
        res = await self.session.call_tool(tool, args)
        if res.is_error:
            raise RuntimeError(f"{tool}: {res.content[0].text}")
        return res.structured_content


def server_params(workspace: Path) -> StdioServerParameters:
    env = {**os.environ, "UNICAD_WORKSPACE": str(workspace), "DISPLAY": os.environ.get("DISPLAY", ":0"), "LIBRECAD": str(LIBRECAD)}
    return StdioServerParameters(command=sys.executable, args=["-m", "unicad"], env=env)


def store_fixture(workspace: Path, name: str, **update) -> str:
    from unicad.catalog.store import load_catalog
    from unicad.store import ProjectStore, parse_project

    p = parse_project((ROOT / "fixtures" / "projects" / f"{name}.json").read_text(encoding="utf-8"), load_catalog()).model_copy(update=update)
    ProjectStore(workspace).save(p)
    return p.id


async def rooftop(c: Client, pid: str) -> str:
    """The stored rooftop fixture gets its site, setbacks, an array layout and the earthing and lightning inputs."""
    await c.call("site_import", project_id=pid, file_path=str(SITES / "rooftop_midc_pune.kml"))
    await c.call("site_set_setbacks", project_id=pid, roof_edge_m=1.2, obstacle_m=0.6)
    await c.call("layout_place", project_id=pid, module=ROOF_MODULE, mounting="rooftop_flush", table_cols=12, half_cols=6, pitch_method="gap",
                 row_gap_m=0.6, table_gap_m=0.6, walkway_every_tables=4, walkway_width_m=1.2, target_dc_kwp=500)  # fmt: skip
    await c.call("project_design_basis", project_id=pid, **ROOF_BASIS)
    return pid


async def utility(c: Client) -> str:
    """A ground-mount string-inverter plant built from scratch on the Rajasthan site."""
    created = await c.call("project_create", name="6 MWp Ground Mount Solar PV Plant", client="Demo Renewables Ltd", client_code="DRL", segment="utility",
                           site="Phalodi solar park (synthetic)", state="Rajasthan", grid_voltage_v=33000, project_id="ground-6mwp")  # fmt: skip
    pid = created["project_id"]
    await c.call("site_import", project_id=pid, file_path=str(SITES / "ground_mount_rajasthan.kml"))
    await c.call("site_set_setbacks", project_id=pid, boundary_m=10, exclusion_m=5)
    await c.call("layout_place", project_id=pid, module=GROUND_MODULE, tilt_deg=25, table_cols=28, road_every_rows=12, block_dc_kwp=1560,
                 target_dc_kwp=6240, modules_per_string=28, strings_per_inverter=24)  # fmt: skip
    await c.call("network_autobuild", project_id=pid, template="utility_string", module=GROUND_MODULE, inverter=STRING_INVERTER)
    await c.call("project_design_basis", project_id=pid, **GROUND_BASIS)
    return pid


async def rooftop_acme(c: Client, pid: str) -> str:
    await c.call("company_assign", project_id=pid, profile_id="acme")
    return await rooftop(c, pid)


async def render_all(c: Client, pid: str, formats=("dxf", "pdf")) -> dict:
    return await c.call("drawings_render", project_id=pid, kinds=["all"], formats=list(formats))


async def build(workspace: Path, name: str, flow) -> dict:
    async with stdio_client(server_params(workspace)) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        c = Client(session)
        pid = await flow(c)
        r = await render_all(c, pid)
        shots = {}
        for s in r["sheets"]:
            if s["kind"] not in shots:
                shots[s["kind"]] = (await c.call("drawings_preview", project_id=pid, sheet=s["id"], px=2400))["png"]
        return r | {"previews": shots}


def main() -> None:
    if not LIBRECAD.exists():
        sys.exit(f"LibreCAD not found at {LIBRECAD}; the sets need it for the PDFs")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="unicad-sets-") as tmp:
        ws = Path(tmp)
        roof_id = store_fixture(ws, "rooftop_500kwp")
        acme_id = store_fixture(ws, "rooftop_500kwp", id="rooftop-500kwp-acme", sheets=())
        samples = {"rooftop_500kwp": lambda c: rooftop(c, roof_id), "utility_string": utility, "rooftop_acme": lambda c: rooftop_acme(c, acme_id)}
        for name, flow in samples.items():
            r = asyncio.run(build(ws, name, flow))
            dest = OUT / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(r["out_dir"], dest, ignore=shutil.ignore_patterns("pdf"))
            pages = subprocess.run(["pdfinfo", str(dest / "set.pdf")], capture_output=True, text=True).stdout
            print(f"{name}: {r['sheet_count']} sheets, set.pdf {next((x.split()[-1] for x in pages.splitlines() if x.startswith('Pages')), '?')} pages")
            for s in r["sheets"]:
                print(f"  {s['number']:<26} {s['kind']:<15} {s['paper']} {s['drawing_scale']:<7} {s['kind_sheet_of']:<8} {s['title']}"
                      + (f"  [{len(s['warnings'])} warnings]" if s["warnings"] else ""))  # fmt: skip
            for w in r["warnings"]:
                print(f"  warning: {w}")
            previews = {k: str(Path("preview") / Path(v).name) for k, v in r["previews"].items()}
            summary = {"project_id": r["project_id"], "revision": r["revision"], "sheet_count": r["sheet_count"], "warnings": r["warnings"], "previews": previews,
                       "sheets": [{k: s[k] for k in ("number", "kind", "title", "paper", "drawing_scale", "sheet_of", "kind_sheet_of", "warnings")} for s in r["sheets"]]}  # fmt: skip
            (dest / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()

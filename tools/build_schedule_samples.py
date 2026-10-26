"""Build the cable schedule and BOM samples in library/schedules/ from the project fixtures.

Per sample this writes
  <name>-cable-schedule.xlsx         one row per cable
  <name>-cable-schedule-pNN.dxf      company-sheet pages
  <name>-cable-schedule.pdf          the pages printed through LibreCAD
  <name>-cable-schedule-pNN.png      the PDF pages rasterised with pdftoppm (first, second and last page of long sets)
  <name>-bom.xlsx, <name>-bom-pNN.dxf, <name>-bom.pdf, <name>-bom-pNN.png

Set LIBRECAD to the AppRun/binary; defaults to /tmp/opencode/proto/squashfs-root/AppRun.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from unicad.arrays import PlacementSpec, place_array
from unicad.catalog.store import Catalog, load_catalog
from unicad.domain.project import Project
from unicad.drafting.sheets.model import CompanyProfile
from unicad.drafting.sld.sheet import save_deterministic
from unicad.drafting.tables import TableRender
from unicad.ingest.site_import import import_site
from unicad.render import LibreCadRenderer, Sheet
from unicad.schedules import LengthMode, build_bom, build_schedule, write_bom_xlsx, write_schedule_xlsx
from unicad.schedules.dxf import render_bom, render_schedule
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "library" / "schedules"
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
PNG_DPI = {"A0": 110, "A1": 150, "A2": 200, "A3": 130, "A4": 200}
GROUND_MODULE = "vikram-solar-hypersol-m10r-144-580-wp"
MAX_PNG_PAGES = 6


def ground_demo(catalog: Catalog) -> Project:
    """The utility fixture scaled to 2 blocks x 2 PCUs x 3 SCBs x 12 strings and laid out on the Rajasthan site, so the schedule can be estimated from geometry."""
    data = json.loads((ROOT / "fixtures" / "projects" / "utility_50mwac.json").read_text(encoding="utf-8"))
    for g in data["network"]["groups"]:
        g["count"] = {"block": 2, "scb_sec": 3}.get(g["id"], g.get("count", 1))
    next(n for n in data["network"]["nodes"] if n["id"] == "str")["count"] = 12
    data["id"], data["name"] = "ground-demo-2mwac", "2 MWac ground-mounted solar PV plant (scaled utility fixture on the Rajasthan site)"
    for e in data["network"]["edges"]:
        if e["id"] in ("e_str", "e_scb_inv", "e_icog_bus"):
            e.pop("length_m")
    base = Project.model_validate(data, context={"catalog": catalog})
    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry
    spec = PlacementSpec(
        module=GROUND_MODULE, table_rows=2, table_cols=27, tilt_deg=25, n_blocks=2, modules_per_string=27, road_every_rows=12, target_dc_kwp=144 * 27 * 0.58 * 1.1
    )
    placed = place_array(site, spec, catalog.get(GROUND_MODULE))
    strings = tuple(a.model_copy(update={"string": sid}) for a, sid in zip(placed.layout.strings, base.network.instance_ids("str"), strict=False))
    data = base.model_dump(mode="json")
    data["array_layout"] = placed.layout.model_copy(update={"strings": strings}).model_dump(mode="json")
    data["site"]["geometry"] = site.model_dump(mode="json")
    return Project.model_validate(data, context={"catalog": catalog})


def print_pages(render: TableRender, stem: str, titles: list[str]) -> list[Path]:
    dxfs = []
    for i, doc in enumerate(render.docs, start=1):
        path = OUT / f"{stem}-p{i:02d}.dxf"
        save_deterministic(doc, path)
        dxfs.append(path)
    if not LIBRECAD.exists():
        return dxfs
    sheets = [Sheet(p, paper.value, titles[part]) for p, part, paper in zip(dxfs, render.parts, render.papers, strict=True)]
    pdf = LibreCadRenderer(binary=LIBRECAD).build_set(sheets, OUT / f"{stem}.pdf").pdf
    if shutil.which("pdftoppm"):
        n = len(dxfs)
        keep = range(1, n + 1) if n <= MAX_PNG_PAGES else (1, 2, n)
        for page in keep:
            dpi = PNG_DPI[render.papers[page - 1].value]
            subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(page), "-l", str(page), "-singlefile", str(pdf), str(OUT / f"{stem}-p{page:02d}")], check=True)
    return dxfs


def build(project: Project, name: str, catalog: Catalog, profile: CompanyProfile, mode: LengthMode) -> None:
    schedule = build_schedule(project, catalog, mode)
    bom = build_bom(project, catalog, schedule)
    write_schedule_xlsx(schedule, OUT / f"{name}-cable-schedule.xlsx")
    write_bom_xlsx(bom, OUT / f"{name}-bom.xlsx")
    render, titles = render_schedule(project, schedule, profile)
    print_pages(render, f"{name}-cable-schedule", titles)
    bom_render = render_bom(project, bom, profile)
    print_pages(bom_render, f"{name}-bom", ["BILL OF MATERIALS"])
    totals = schedule.totals()
    sources = {s: sum(1 for r in schedule.rows if r.length_source == s) for s in ("network", "estimated", "none")}
    print(f"{name} ({mode.value} lengths): {len(schedule.rows)} cables, {totals['cable_m']:,.0f} m, loss {totals['loss_w'] / 1000:,.1f} kW, sources {sources}")
    print(f"  schedule {render.pages} pages, BOM {bom_render.pages} page(s); modules {bom.summary()['modules']:,.0f}")
    for line in schedule.vd_summary:
        if line[0].endswith("WORST PATH"):
            print(f"  {line[0]}: {line[1]}")
    for w in (*render.warnings, *bom_render.warnings, *schedule.warnings):
        print(f"  warning: {w}")
    for g in schedule.gaps:
        print(f"  gap: {g}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*"):
        if old.is_file():
            old.unlink()
    catalog = load_catalog()
    profile = CompanyProfile.load(ROOT / "profiles" / "default.json")
    fixtures = ROOT / "fixtures" / "projects"
    rooftop = parse_project((fixtures / "rooftop_500kwp.json").read_text(encoding="utf-8"), catalog)
    utility = parse_project((fixtures / "utility_50mwac.json").read_text(encoding="utf-8"), catalog)
    build(rooftop, "rooftop_500kwp", catalog, profile, LengthMode.ESTIMATED)
    build(utility, "utility_50mwac", catalog, profile, LengthMode.AUTO)
    build(ground_demo(catalog), "ground_demo_2mwac", catalog, profile, LengthMode.AUTO)


if __name__ == "__main__":
    main()

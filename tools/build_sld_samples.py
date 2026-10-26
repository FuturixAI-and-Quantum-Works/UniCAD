"""Build the SLD samples in library/sld/ from the project fixtures: DXF, LibreCAD PDF and a PNG preview.

Set LIBRECAD to the AppRun/binary; defaults to /tmp/opencode/proto/squashfs-root/AppRun.
"""

import os
import shutil
import subprocess
from pathlib import Path

from unicad.catalog.store import load_catalog
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sld import render_sld, save_deterministic
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "library" / "sld"
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
PNG_DPI = {"A0": 110, "A1": 150, "A2": 200, "A3": 250, "A4": 300}
JOBS = (
    ("rooftop_500kwp", "sld", None),
    ("rooftop_500kwp", "sld", "A2"),
    ("utility_50mwac", "sld", None),
    ("utility_50mwac", "sld", "A1"),
    ("utility_50mwac", "sld_blk", None),
)


def librecad(*args: str) -> None:
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run([str(LIBRECAD), *args], check=True, capture_output=True, env=env, timeout=300)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog()
    profile = CompanyProfile.load(ROOT / "profiles" / "default.json")
    for fixture, sheet_id, paper in JOBS:
        project = parse_project((ROOT / "fixtures" / "projects" / f"{fixture}.json").read_text(encoding="utf-8"), catalog)
        if paper is not None:
            sheets = tuple(s.model_copy(update={"paper": paper}) if s.id == sheet_id else s for s in project.sheets)
            project = project.model_copy(update={"sheets": sheets})
        sheet = next(s for s in project.sheets if s.id == sheet_id)
        doc, result = render_sld(project, sheet_id, profile, catalog)
        dxf = OUT / f"{fixture}-{sheet_id}-{sheet.paper}.dxf"
        save_deterministic(doc, dxf)
        print(f"{dxf.name}: scale {result.placement.scale:.2f}, {len(result.pages)} page(s)")
        for w in result.warnings:
            print(f"  warning: {w}")
        if not LIBRECAD.exists():
            continue
        w, h = PaperSize(sheet.paper).size_mm
        pdf = dxf.with_suffix(".pdf")
        librecad("dxf2pdf", "-s", "1", "-c", "-p", f"{w:g}x{h:g}", "-o", str(pdf), str(dxf))
        if shutil.which("pdftoppm"):
            subprocess.run(["pdftoppm", "-png", "-r", str(PNG_DPI[sheet.paper]), "-singlefile", str(pdf), str(dxf.with_suffix(""))],
                           check=True)


if __name__ == "__main__":
    main()

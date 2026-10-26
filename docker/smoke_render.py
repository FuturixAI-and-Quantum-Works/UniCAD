"""Runs inside the image: render the fixture rooftop SLD to DXF, PDF and DWG and rasterise the PDF."""

import subprocess
import sys
from pathlib import Path

import pymupdf

from unicad.catalog.store import load_catalog
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sld import render_sld, save_deterministic
from unicad.mcp.config import BUNDLED_DATA
from unicad.render import DwgConverter, LibreCadRenderer
from unicad.store import parse_project

FIXTURES = Path("/fixtures")
OUT = Path("/out")


def main() -> int:
    catalog = load_catalog(BUNDLED_DATA / "catalog")
    profile = CompanyProfile.load(BUNDLED_DATA / "profiles" / "default.json")
    project = parse_project((FIXTURES / "projects" / "rooftop_500kwp.json").read_text(encoding="utf-8"), catalog)
    sheet = next(s for s in project.sheets if s.id == "sld")
    doc, result = render_sld(project, sheet.id, profile, catalog)
    dxf = OUT / "rooftop_500kwp-sld.dxf"
    save_deterministic(doc, dxf)
    print(f"dxf: {dxf.stat().st_size} bytes, {len(result.pages)} page(s), scale {result.placement.scale:.2f}")

    pdf = OUT / "rooftop_500kwp-sld.pdf"
    LibreCadRenderer().to_pdf(dxf, pdf, PaperSize(sheet.paper).value)
    with pymupdf.open(pdf) as pages:
        text = pages[0].get_text()
        print(f"pdf: {pdf.stat().st_size} bytes, {pages.page_count} page(s), {len(pages[0].get_drawings())} paths")
        if pages.page_count < 1 or not pages[0].get_drawings():
            print("PDF is blank", file=sys.stderr)
            return 1

    dwg = DwgConverter().to_dwg(dxf, OUT / "rooftop_500kwp-sld.dwg", strict=False)
    print(f"dwg: {dwg.dwg.stat().st_size} bytes via {dwg.converter}, verified={dwg.verified} mismatches={dwg.mismatches}")

    png = OUT / "rooftop_500kwp-sld"
    subprocess.run(["pdftoppm", "-png", "-r", "60", "-singlefile", str(pdf), str(png)], check=True)
    print(f"png: {png.with_suffix('.png').stat().st_size} bytes")
    return 0 if text is not None else 1


if __name__ == "__main__":
    sys.exit(main())

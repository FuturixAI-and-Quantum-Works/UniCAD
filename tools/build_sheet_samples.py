"""Build the sample sheets in library/sheets/: DXF, plus PDF and PNG through LibreCAD.

Per sample this writes
  sample-A3.dxf          the sheet
  sample-A3.pdf          LibreCAD dxf2pdf
  sample-A3.png          the PDF rasterised with PyMuPDF (LibreCAD's own dxf2png draws text as boxes)
  sample-A3.dxf2png.png  LibreCAD dxf2png as asked for; text shows as extent boxes
  sample-A3.strokes.png  ezdxf render with TEXT expanded to the .lff glyph strokes (tools/preview.py)

Set LIBRECAD to the AppRun/binary; defaults to /tmp/opencode/proto/squashfs-root/AppRun.
"""

import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from preview import render as render_strokes  # noqa: E402

from unicad.drafting.legend import legend_drawer  # noqa: E402
from unicad.drafting.sheets import samples  # noqa: E402
from unicad.drafting.sheets.model import CompanyProfile, PaperSize, SheetSpec  # noqa: E402
from unicad.drafting.sheets.render import render_sheet  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "library" / "sheets"
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
PNG_WIDTH_PX = {PaperSize.A0: 7200, PaperSize.A1: 5400, PaperSize.A2: 4500, PaperSize.A3: 3600, PaperSize.A4: 3600}


def build(spec: SheetSpec, profile: CompanyProfile, dxf: Path) -> None:
    doc = ezdxf.new("R2018", setup=["linetypes"])
    result = render_sheet(doc, spec, profile, legend_drawer(doc.modelspace()))
    for w in result.warnings:
        print(f"  warning: {w}")
    doc.saveas(dxf)


def librecad(*args: str) -> None:
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run([str(LIBRECAD), *args], check=True, capture_output=True, env=env)


def export(dxf: Path, paper: PaperSize) -> None:
    w, h = paper.size_mm
    pdf = dxf.with_suffix(".pdf")
    librecad("dxf2pdf", "-a", "-p", f"{w:g}x{h:g}", "-o", str(pdf), str(dxf))
    page = pymupdf.open(pdf)[0]
    dpi = round(PNG_WIDTH_PX[paper] / (w / 25.4))
    page.get_pixmap(dpi=dpi).save(dxf.with_suffix(".png"))
    librecad("dxf2png", "-r", "3600x2550", "-o", str(dxf.with_suffix(".dxf2png.png")), str(dxf))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    profile = CompanyProfile.load(ROOT / "profiles" / "default.json")
    jobs = [
        (PaperSize.A3, samples.rooftop_500kwp(PaperSize.A3)),
        (PaperSize.A1, samples.utility_100mwac(PaperSize.A1)),
        (PaperSize.A0, samples.utility_100mwac(PaperSize.A0)),
    ]
    have_librecad = LIBRECAD.exists()
    if not have_librecad:
        print(f"LibreCAD not found at {LIBRECAD}; writing DXF and stroke previews only")
    for paper, spec in jobs:
        dxf = OUT / f"sample-{paper.value}.dxf"
        print(dxf.name)
        build(spec, profile, dxf)
        render_strokes(dxf, dxf.with_suffix(".strokes.png"), dpi=100 if paper == PaperSize.A0 else 150)
        if have_librecad:
            export(dxf, paper)


if __name__ == "__main__":
    main()

"""Build library/unicad-symbols.{dxf,json,pdf,png} and per-category previews."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unicad.drafting.symbols import Category  # noqa: E402
from unicad.drafting.symbols.catalogue import build_catalogue, manifest  # noqa: E402

LIB = ROOT / "library"
DEFAULT_LIBRECAD = "/tmp/opencode/proto/squashfs-root/AppRun"


def librecad() -> list[str]:
    path = os.environ.get("LIBRECAD", DEFAULT_LIBRECAD)
    if not Path(path).exists() and not shutil.which(path):
        raise SystemExit(f"LibreCAD not found at {path!r}; set LIBRECAD")
    return [path]


def run(cmd: list[str]) -> None:
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run(cmd, check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)


def dxf2pdf(dxf: Path, pdf: Path, paper: str = "841x594") -> None:
    run([*librecad(), "dxf2pdf", "-a", "-p", paper, "-o", str(pdf), str(dxf)])


def dxf2png(dxf: Path, png: Path, res: str = "4000x2800") -> None:
    run([*librecad(), "dxf2png", "-r", res, "-o", str(png), str(dxf)])


def pdf_to_png(pdf: Path, png: Path, width_px: int) -> None:
    """Rasterise a LibreCAD PDF with poppler.

    LibreCAD writes these PDFs with a page 1000x too large and hairline strokes, which
    MuPDF renders blank. Poppler draws hairlines at 1 px; we thicken them afterwards.
    """
    from PIL import Image, ImageFilter

    if not shutil.which("pdftoppm"):
        print("pdftoppm missing; skipping", png.name)
        return
    probe = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    page_w_pt = float(next(line for line in probe.splitlines() if line.startswith("Page size")).split()[2])
    oversample = 3
    dpi = width_px * oversample * 72 / page_w_pt
    with tempfile.TemporaryDirectory() as tmp:
        stem = Path(tmp) / "r"
        subprocess.run(
            ["pdftoppm", "-png", "-r", f"{dpi:.5f}", "-singlefile", str(pdf), str(stem)],
            check=True,
            stderr=subprocess.DEVNULL,
        )
        img = Image.open(f"{stem}.png").convert("L")
    img = img.point(lambda v: 0 if v < 235 else 255).filter(ImageFilter.MinFilter(3))
    img.resize((width_px, round(img.height * width_px / img.width)), Image.LANCZOS).save(png)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-render", action="store_true", help="write DXF and JSON only")
    args = ap.parse_args()

    LIB.mkdir(exist_ok=True)
    (LIB / "preview").mkdir(exist_ok=True)

    doc = build_catalogue()
    dxf = LIB / "unicad-symbols.dxf"
    doc.saveas(dxf)
    (LIB / "unicad-symbols.json").write_text(json.dumps(manifest(), indent=2) + "\n")
    print("wrote", dxf, "and manifest:", len(manifest()), "symbols")
    if args.no_render:
        return

    dxf2pdf(dxf, LIB / "unicad-symbols.pdf")
    dxf2png(dxf, LIB / "unicad-symbols.png")
    pdf_to_png(LIB / "unicad-symbols.pdf", LIB / "preview" / "all.png", 3600)

    with tempfile.TemporaryDirectory() as tmp:
        for cat in Category:
            d = build_catalogue([cat])
            cat_dxf = Path(tmp) / f"{cat.value}.dxf"
            d.saveas(cat_dxf)
            cat_pdf = Path(tmp) / f"{cat.value}.pdf"
            dxf2pdf(cat_dxf, cat_pdf, "420x297")
            pdf_to_png(cat_pdf, LIB / "preview" / f"{cat.value}.png", 2600)
            print("preview", cat.value)


if __name__ == "__main__":
    main()

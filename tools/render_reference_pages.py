"""Rasterise every reference PDF under references/samples/raw into references/samples/pages."""

import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1] / "references" / "samples"


def main(dpi: int = 110) -> None:
    for pdf in sorted((ROOT / "raw").rglob("*.pdf")):
        out = ROOT / "pages" / pdf.parent.name / pdf.stem
        out.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(pdf) as doc:
            for page in doc:
                target = out / f"p{page.number + 1:03d}.png"
                if not target.exists():
                    page.get_pixmap(dpi=dpi).save(target)
            print(f"{pdf.relative_to(ROOT)}: {doc.page_count} pages")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))

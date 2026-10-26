"""Agent-readable PNG previews cropped from the LibreCAD PDF.

Previews are rasterised from the same PDF that goes into the issued set, so what an agent
sees is what gets printed. The PDF must have been made with `ScaleMode.ACTUAL`: drawing mm
(0, 0) at the page's lower-left corner and 1 drawing mm = 1 paper mm. Text in that PDF is
strokes, so the crop is legible at any zoom.
"""

from __future__ import annotations

import atexit
import hashlib
import io
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import pymupdf
from ezdxf import bbox
from PIL import Image, ImageDraw, ImageFont

from .backend import PT_PER_MM, PaperSpec, Renderer, ScaleMode, paper_size_mm, smallest_paper_for

Region = tuple[float, float, float, float]

DEFAULT_PX = 1600
MIN_TEXT_PX = 9
TYPICAL_TEXT_MM = 2.5
_GRID_STEPS_MM = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500)
_CACHE_LIMIT = 32

_cache_dir: Path | None = None
_cache: dict[str, Path] = {}


@dataclass(frozen=True)
class Preview:
    png: bytes
    region: Region
    width_px: int
    height_px: int
    px_per_mm: float
    path: Path | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def preview(
    source: Path,
    region: Region | None = None,
    px: int = DEFAULT_PX,
    *,
    paper: PaperSpec | None = None,
    page: int = 0,
    grid: bool = False,
    out: Path | None = None,
    renderer: Renderer | None = None,
) -> Preview:
    """PNG of `region` (x0, y0, x1, y1 in drawing mm, y up) or the whole sheet, `px` on its long side.

    `source` is a DXF (rendered through LibreCAD, cached by content) or an ACTUAL-mode PDF.
    `paper` defaults to the smallest ISO sheet that holds the DXF's extents.
    """
    source = Path(source)
    warnings: list[str] = []
    if source.suffix.lower() == ".pdf":
        pdf = source
    else:
        pdf = _cached_pdf(source, paper, renderer, warnings)
    png, used, scale = _rasterise(pdf, region, px, page, grid)
    px_per_mm = scale * PT_PER_MM
    if px_per_mm * TYPICAL_TEXT_MM < MIN_TEXT_PX:
        warnings.append(
            f"{TYPICAL_TEXT_MM:g} mm text is {px_per_mm * TYPICAL_TEXT_MM:.1f} px tall at this zoom; "
            f"pass a region of at most {px / (MIN_TEXT_PX / TYPICAL_TEXT_MM):.0f} mm on its long side to read it"
        )
    width, height = Image.open(io.BytesIO(png)).size
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
    return Preview(png, used, width, height, px_per_mm, out, tuple(warnings))


def pdf_to_png(pdf: Path, region: Region | None = None, px: int = DEFAULT_PX, page: int = 0, grid: bool = False) -> bytes:
    return _rasterise(Path(pdf), region, px, page, grid)[0]


def _rasterise(pdf: Path, region: Region | None, px: int, page_index: int, grid: bool) -> tuple[bytes, Region, float]:
    if px < 16:
        raise ValueError(f"px must be at least 16, got {px}")
    with pymupdf.open(pdf) as doc:
        if not 0 <= page_index < len(doc):
            raise ValueError(f"page {page_index} out of range for {pdf.name} with {len(doc)} pages")
        page = doc[page_index]
        height_pt = page.rect.height
        if region is None:
            used = (0.0, 0.0, page.rect.width / PT_PER_MM, height_pt / PT_PER_MM)
        else:
            x0, y0, x1, y1 = region
            if not (x1 > x0 and y1 > y0):
                raise ValueError(f"region must have x1 > x0 and y1 > y0, got {region}")
            used = (x0, y0, x1, y1)
        clip = pymupdf.Rect(
            used[0] * PT_PER_MM, height_pt - used[3] * PT_PER_MM, used[2] * PT_PER_MM, height_pt - used[1] * PT_PER_MM
        ) & page.rect
        if clip.is_empty:
            raise ValueError(f"region {region} lies outside the {page.rect.width / PT_PER_MM:.0f}x{height_pt / PT_PER_MM:.0f} mm page")
        used = (
            clip.x0 / PT_PER_MM,
            (height_pt - clip.y1) / PT_PER_MM,
            clip.x1 / PT_PER_MM,
            (height_pt - clip.y0) / PT_PER_MM,
        )
        scale = px / max(clip.width, clip.height)
        png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False).tobytes("png")
    if grid:
        png = _draw_grid(png, used, scale * PT_PER_MM)
    return png, used, scale


def _draw_grid(png: bytes, region: Region, px_per_mm: float) -> bytes:
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    x0, y0, x1, y1 = region
    long_side = max(x1 - x0, y1 - y0)
    step = next((s for s in _GRID_STEPS_MM if long_side / s <= 10), _GRID_STEPS_MM[-1])
    font = ImageFont.load_default(size=max(11, round(img.width / 110)))
    line, label = (220, 40, 40, 110), (200, 20, 20, 255)

    def label_at(xy: tuple[float, float], text: str) -> None:
        box = draw.textbbox(xy, text, font=font)
        draw.rectangle((box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1), fill=(255, 255, 255, 200))
        draw.text(xy, text, fill=label, font=font)

    gx = (int(x0 // step) + 1) * step
    while gx < x1:
        x = (gx - x0) * px_per_mm
        draw.line((x, 0, x, img.height), fill=line, width=1)
        label_at((x + 3, 2), f"{gx:g}")
        gx += step
    gy = (int(y0 // step) + 1) * step
    while gy < y1:
        y = (y1 - gy) * px_per_mm
        draw.line((0, y, img.width, y), fill=line, width=1)
        label_at((3, y + 2), f"{gy:g}")
        gy += step
    out = io.BytesIO()
    Image.alpha_composite(img, overlay).convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _cached_pdf(dxf: Path, paper: PaperSpec | None, renderer: Renderer | None, warnings: list[str]) -> Path:
    global _cache_dir
    dxf = dxf.resolve()
    if paper is None:
        paper = _infer_paper(dxf, warnings)
    w, h = paper_size_mm(paper)
    digest = hashlib.sha256(dxf.read_bytes() + f"|{w:g}x{h:g}".encode()).hexdigest()[:24]
    hit = _cache.get(digest)
    if hit is not None and hit.is_file():
        return hit
    if _cache_dir is None:
        _cache_dir = Path(tempfile.mkdtemp(prefix="unicad-preview-"))
        atexit.register(shutil.rmtree, _cache_dir, True)
    if renderer is None:
        from .librecad import LibreCadRenderer

        renderer = LibreCadRenderer()
    while len(_cache) >= _CACHE_LIMIT:
        _cache.pop(next(iter(_cache))).unlink(missing_ok=True)
    pdf = renderer.to_pdf(dxf, _cache_dir / f"{digest}.pdf", (w, h), ScaleMode.ACTUAL)
    _cache[digest] = pdf
    return pdf


def _infer_paper(dxf: Path, warnings: list[str]) -> tuple[float, float]:
    extents = bbox.extents(ezdxf.readfile(dxf).modelspace())
    if not extents.has_data:
        return paper_size_mm("A4")
    if extents.extmin.x < -1 or extents.extmin.y < -1:
        warnings.append(
            f"drawing extends to ({extents.extmin.x:.0f}, {extents.extmin.y:.0f}) mm, left/below the sheet origin; "
            "that part is not printed"
        )
    return smallest_paper_for(extents.extmax.x, extents.extmax.y)

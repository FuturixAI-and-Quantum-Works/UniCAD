"""Renderer interface: DXF in, PDF/SVG/PNG out, plus assembly of a multi-sheet set.

Every generator draws one sheet per DXF at paper millimetres with the origin at the sheet's
lower-left corner. `ScaleMode.ACTUAL` keeps that registration in the PDF (drawing mm
(x, y) lands x mm right of and y mm above the page's lower-left corner), which is what
region previews rely on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

import pymupdf

PaperSpec = str | tuple[float, float]

_ISO_LANDSCAPE_MM: dict[str, tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}

PT_PER_MM = 72.0 / 25.4


class RenderError(Exception):
    """Base class for every failure raised by a renderer."""


class BackendUnavailable(RenderError):
    """The converter binary or the display it needs is missing."""


class InvalidDrawing(RenderError):
    """The input DXF cannot be rendered safely."""


class RenderFailed(RenderError):
    """The converter ran but did not produce usable output."""


class RenderTimeout(RenderError):
    """The converter did not finish in time and was killed."""


class ScaleMode(StrEnum):
    ACTUAL = "actual"
    CENTERED = "centered"
    FIT = "fit"


def paper_size_mm(paper: PaperSpec) -> tuple[float, float]:
    if isinstance(paper, str):
        key = paper.strip().upper()
        if key not in _ISO_LANDSCAPE_MM:
            raise ValueError(f"unknown paper {paper!r}; use one of {sorted(_ISO_LANDSCAPE_MM)} or (width_mm, height_mm)")
        return _ISO_LANDSCAPE_MM[key]
    w, h = paper
    if w <= 0 or h <= 0:
        raise ValueError(f"paper size must be positive, got {paper!r}")
    return float(w), float(h)


def smallest_paper_for(width_mm: float, height_mm: float) -> tuple[float, float]:
    for size in reversed(list(_ISO_LANDSCAPE_MM.values())):
        if width_mm <= size[0] and height_mm <= size[1]:
            return size
    return width_mm, height_mm


@dataclass(frozen=True)
class Sheet:
    dxf: Path
    paper: PaperSpec
    title: str | None = None


@dataclass(frozen=True)
class PageInfo:
    sheet: Sheet
    pdf: Path
    width_mm: float
    height_mm: float


@dataclass(frozen=True)
class SetResult:
    pdf: Path
    pages: tuple[PageInfo, ...]


@runtime_checkable
class Renderer(Protocol):
    def to_pdf(self, dxf: Path, pdf: Path, paper: PaperSpec, scale_mode: ScaleMode = ScaleMode.ACTUAL) -> Path: ...

    def to_svg(self, dxf: Path, svg: Path) -> Path: ...

    def to_png(
        self, dxf: Path, png: Path, paper: PaperSpec, px: int = 3600, scale_mode: ScaleMode = ScaleMode.ACTUAL
    ) -> Path: ...

    def build_set(
        self,
        sheets: Sequence[Sheet],
        out_pdf: Path,
        scale_mode: ScaleMode = ScaleMode.ACTUAL,
        work_dir: Path | None = None,
    ) -> SetResult: ...


def merge_pdfs(pdfs: Sequence[Path], titles: Sequence[str | None], out_pdf: Path) -> None:
    merged = pymupdf.open()
    toc: list[list] = []
    try:
        for pdf, title in zip(pdfs, titles, strict=True):
            with pymupdf.open(pdf) as part:
                first = len(merged) + 1
                merged.insert_pdf(part)
            if title:
                toc.append([1, title, first])
        if toc:
            merged.set_toc(toc)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        merged.save(out_pdf, garbage=3, deflate=True)
    finally:
        merged.close()

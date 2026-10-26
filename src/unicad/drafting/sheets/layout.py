from dataclasses import dataclass

from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.model import PaperSize

_TEXT_HEIGHT = {
    PaperSize.A4: 2.0,
    PaperSize.A3: 2.5,
    PaperSize.A2: 3.0,
    PaperSize.A1: 3.5,
    PaperSize.A0: 4.2,
}
_MARGIN = {PaperSize.A4: 5.0, PaperSize.A3: 5.0, PaperSize.A2: 10.0, PaperSize.A1: 10.0, PaperSize.A0: 10.0}
_ZONES = {
    PaperSize.A4: (4, 2),
    PaperSize.A3: (6, 4),
    PaperSize.A2: (8, 6),
    PaperSize.A1: (8, 6),
    PaperSize.A0: (8, 6),
}
COLUMN_WIDTH_IN_TH = 46.0


@dataclass(frozen=True)
class SheetMetrics:
    """All sheet dimensions derive from `th`, the body text height, so a sheet looks the
    same at every paper size and only the amount of room changes."""

    paper: PaperSize
    th: float
    outer: Region
    inner: Region
    column: Region
    drawing_area: Region
    zones_x: int
    zones_y: int

    @property
    def lbl(self) -> float:
        return 0.7 * self.th

    @property
    def pad_x(self) -> float:
        return 0.6 * self.th

    @property
    def pad_y(self) -> float:
        return 0.4 * self.th


def metrics_for(paper: PaperSize) -> SheetMetrics:
    w, h = paper.size_mm
    th = _TEXT_HEIGHT[paper]
    m = _MARGIN[paper]
    strip = 2.4 * th
    outer = Region(m, m, w - m, h - m)
    inner = outer.inset(strip)
    column = Region(inner.x1 - COLUMN_WIDTH_IN_TH * th, inner.y0, inner.x1, inner.y1)
    area = Region(inner.x0 + th, inner.y0 + th, column.x0 - th, inner.y1 - th)
    nx, ny = _ZONES[paper]
    return SheetMetrics(paper, th, outer, inner, column, area, nx, ny)

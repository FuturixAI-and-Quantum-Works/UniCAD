from dataclasses import dataclass
from typing import Literal

from ezdxf.document import Drawing
from ezdxf.layouts import Modelspace

from unicad.drafting.sheets.model import CompanyProfile, LayerRole
from unicad.drafting.sheets.text import DESCENDER, LINE_PITCH, Fitted, fit, text_width

EPS = 1e-6
HEAVY = 50
Align = Literal["left", "center", "right"]
VAlign = Literal["top", "middle"]


class LayoutOverflow(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def inset(self, dx: float, dy: float | None = None) -> "Region":
        dy = dx if dy is None else dy
        return Region(self.x0 + dx, self.y0 + dy, self.x1 - dx, self.y1 - dy)

    def contains(self, other: "Region", tol: float = EPS) -> bool:
        return (
            other.x0 >= self.x0 - tol
            and other.y0 >= self.y0 - tol
            and other.x1 <= self.x1 + tol
            and other.y1 <= self.y1 + tol
        )

    def overlaps(self, other: "Region", tol: float = EPS) -> bool:
        return (
            self.x0 < other.x1 - tol
            and other.x0 < self.x1 - tol
            and self.y0 < other.y1 - tol
            and other.y0 < self.y1 - tol
        )


@dataclass(frozen=True)
class TextCell:
    handle: str
    cell: Region


class Canvas:
    def __init__(self, doc: Drawing, profile: CompanyProfile):
        self.msp: Modelspace = doc.modelspace()
        self.profile = profile
        self.text_cells: list[TextCell] = []
        self.warnings: list[str] = []

    def _attribs(self, role: LayerRole, heavy: bool) -> dict:
        attribs: dict = {"layer": self.profile.layer(role).name}
        if heavy:
            attribs["lineweight"] = HEAVY
        return attribs

    def line(self, role: LayerRole, p: tuple[float, float], q: tuple[float, float], heavy: bool = False) -> None:
        self.msp.add_line(p, q, dxfattribs=self._attribs(role, heavy))

    def rect(self, role: LayerRole, r: Region, heavy: bool = False) -> None:
        a = self._attribs(role, heavy)
        pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        for i in range(4):
            self.msp.add_line(pts[i], pts[(i + 1) % 4], dxfattribs=a)

    def hline(self, role: LayerRole, y: float, x0: float, x1: float, heavy: bool = False) -> None:
        self.line(role, (x0, y), (x1, y), heavy)

    def vline(self, role: LayerRole, x: float, y0: float, y1: float, heavy: bool = False) -> None:
        self.line(role, (x, y0), (x, y1), heavy)

    def text(
        self,
        role: LayerRole,
        s: str,
        x: float,
        baseline: float,
        h: float,
        wf: float,
        cell: Region,
        align: Align = "left",
    ) -> None:
        if not s:
            return
        w = text_width(s, h, wf)
        left = {"left": x, "center": x - w / 2, "right": x - w}[align]
        box = Region(left, baseline - DESCENDER * h, left + w, baseline + h)
        if not cell.contains(box):
            raise LayoutOverflow(f"text {s!r} ({box}) overflows its cell {cell}")
        attribs = self._attribs(role, False)
        attribs["style"] = self.profile.text_style
        attribs["height"] = h
        if abs(wf - 1.0) > EPS:
            attribs["width"] = wf
        attribs["insert"] = (left, baseline)
        entity = self.msp.add_text(s, dxfattribs=attribs)
        self.text_cells.append(TextCell(entity.dxf.handle, cell))

    def put(
        self,
        role: LayerRole,
        cell: Region,
        s: str,
        h: float,
        *,
        align: Align = "left",
        valign: VAlign = "top",
        max_lines: int = 1,
        min_h: float | None = None,
        pad_x: float = 0.0,
        pad_y: float = 0.0,
    ) -> Fitted:
        inner = cell.inset(pad_x, pad_y)
        fitted = fit(s, inner.width, h, max_lines=max_lines, max_h=inner.height, min_h=min_h)
        if fitted.truncated:
            self.warnings.append(f"text truncated to fit its cell: {s[:60]!r}")
        self.draw_fitted(role, cell, inner, fitted, align, valign)
        return fitted

    def draw_fitted(
        self, role: LayerRole, cell: Region, inner: Region, fitted: Fitted, align: Align, valign: VAlign
    ) -> None:
        hh = fitted.height
        top = inner.y1 if valign == "top" else inner.centre[1] + fitted.block_height / 2
        x = {"left": inner.x0, "center": inner.centre[0], "right": inner.x1}[align]
        for i, line in enumerate(fitted.lines):
            baseline = top - hh - i * hh * LINE_PITCH
            self.text(role, line, x, baseline, hh, fitted.width_factor, cell, align)

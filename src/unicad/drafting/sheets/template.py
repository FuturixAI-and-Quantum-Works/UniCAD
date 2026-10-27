"""A company's own title block, drawn in its DXF with named placeholders, parsed and filled in.

A placeholder is a TEXT, MTEXT or ATTDEF whose text is `{NAME}` (an ATTDEF may instead carry the name as its tag). The placeholder's
box is the closed cell of lines around its position, cut off below any label text that sits above it in the same cell. Everything
else in the DXF is the title block's static drawing and is copied to the sheet, scaled uniformly so its width is the sheet's right
column. Values are fitted inside the box with the sheet's usual wrap, condense and shrink steps.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import ezdxf
from ezdxf import bbox
from ezdxf.document import Drawing
from ezdxf.entities import DXFGraphic
from ezdxf.math import Matrix44, Vec3

from unicad.drafting.dxfrules import HATCH_PATTERNS, LIBRECAD_FONTS, MIN_LINEWEIGHT, plain
from unicad.drafting.sheets.canvas import EPS, Canvas, Region
from unicad.drafting.sheets.layout import SheetMetrics
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, LayerRole, TemplateRef, TitleBlockData
from unicad.drafting.sheets.text import DESCENDER, LINE_PITCH, fit, text_width
from unicad.drafting.sheets.titleblock import compose_location, place_logo

T = LayerRole.TITLE
Align = Literal["left", "center", "right"]
_PLACEHOLDER = re.compile(r"^\{\s*([A-Za-z_]+)\s*\}$")
_AXIS_TOL = 1e-3
_LINE_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE"})
_TEXT_TYPES = frozenset({"TEXT", "MTEXT", "ATTDEF"})
_COPYABLE = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SOLID", "HATCH", "TEXT"})
_MIN_SCALE, _MAX_SCALE = 0.4, 2.5
_MAX_HEIGHT_SHARE = 0.6


def _fmt(d, fmt: str) -> str:
    return d.strftime(fmt) if d else ""


def _values(d: TitleBlockData, p: CompanyProfile) -> dict[str, Callable[[], str]]:
    issued = {f"ISSUED_{o.name}": (lambda o=o: "X" if o == d.issued_for else "") for o in IssuedFor}
    return {
        "OWNER": lambda: d.client,
        "CLIENT": lambda: d.client,
        "CONSULTANT": lambda: d.consultant or "",
        "PROJECT_TITLE": lambda: d.project_title,
        "LOCATION": lambda: compose_location(d.location),
        "LOA_NO": lambda: d.loa_no or "",
        "TITLE": lambda: d.drawing_title,
        "DRAWING_TITLE": lambda: d.drawing_title,
        "DRAWING_NO": lambda: d.drawing_number,
        "REV": lambda: d.revision,
        "SCALE": lambda: d.scale,
        "SHEET": lambda: f"{d.sheet_no} of {d.sheet_count}",
        "SHEET_NO": lambda: str(d.sheet_no),
        "SHEET_COUNT": lambda: str(d.sheet_count),
        "PAPER": lambda: d.paper.value,
        "DRAWN": lambda: d.drawn_by,
        "CHECKED": lambda: d.checked_by,
        "APPROVED": lambda: d.approved_by,
        "DRAWN_DATE": lambda: _fmt(d.drawn_date, p.date_format),
        "CHECKED_DATE": lambda: _fmt(d.checked_date, p.date_format),
        "APPROVED_DATE": lambda: _fmt(d.approved_date, p.date_format),
        "DATE": lambda: _fmt(d.drawn_date, p.date_format),
        "ISSUED_FOR": lambda: d.issued_for.value,
        "COMPANY": lambda: p.name,
        "ADDRESS": lambda: p.address,
        "CONFIDENTIALITY": lambda: p.confidentiality_note or "",
        "SHORT_CODE": lambda: p.short_code,
        **issued,
    }


_ISSUED_NAMES = frozenset(f"ISSUED_{o.name}" for o in IssuedFor)
_WRAPPED = frozenset({"LOCATION"})
"""Placeholders whose value wraps onto smaller lines to fit its cell rather than being cut."""
KNOWN_PLACEHOLDERS = frozenset(
    "OWNER CLIENT CONSULTANT PROJECT_TITLE LOCATION LOA_NO TITLE DRAWING_TITLE DRAWING_NO REV SCALE SHEET SHEET_NO SHEET_COUNT "
    "PAPER DRAWN CHECKED APPROVED DRAWN_DATE CHECKED_DATE APPROVED_DATE DATE ISSUED_FOR COMPANY ADDRESS CONFIDENTIALITY "
    "SHORT_CODE LOGO".split()
) | _ISSUED_NAMES
REQUIRED_PLACEHOLDERS: tuple[tuple[str, ...], ...] = (("DRAWING_NO",), ("TITLE", "DRAWING_TITLE"), ("REV",), ("SHEET", "SHEET_NO"))
REPORTED_WHEN_MISSING = ("OWNER", "PROJECT_TITLE", "LOCATION", "DRAWN", "CHECKED", "APPROVED", "SCALE", "PAPER")


@dataclass(frozen=True)
class Placeholder:
    name: str
    box: Region
    height: float
    align: Align
    x: float


@dataclass(frozen=True)
class Template:
    ref: TemplateRef
    extents: Region
    fields: tuple[Placeholder, ...]
    entities: tuple[DXFGraphic, ...] = field(repr=False)
    layers: dict[str, tuple[int, str, int]] = field(default_factory=dict, repr=False)
    problems: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)

    @property
    def missing_required(self) -> tuple[str, ...]:
        return tuple("/".join(group) for group in REQUIRED_PLACEHOLDERS if not set(group) & self.names)


def _is_placeholder(e: DXFGraphic) -> str | None:
    t = e.dxftype()
    if t not in _TEXT_TYPES:
        return None
    text = e.plain_text() if t == "MTEXT" else e.dxf.text
    if m := _PLACEHOLDER.match(text.strip()):
        return m.group(1).upper()
    if t == "ATTDEF" and (tag := e.dxf.tag.strip().upper()) in KNOWN_PLACEHOLDERS:
        return tag
    return None


def _text_anchor(e: DXFGraphic) -> tuple[float, float, float, Align]:
    """(x, y at mid-height, text height, horizontal alignment) of a text entity."""
    if e.dxftype() == "MTEXT":
        h = e.dxf.char_height
        ap = e.dxf.get("attachment_point", 1)
        x, y = e.dxf.insert.x, e.dxf.insert.y
        row = (ap - 1) // 3
        return x, y - h / 2 - row * h / 2, h, ("left", "center", "right")[(ap - 1) % 3]
    h = e.dxf.height
    halign, valign = e.dxf.get("halign", 0), e.dxf.get("valign", 0)
    p = e.dxf.align_point if (halign or valign) and e.dxf.hasattr("align_point") else e.dxf.insert
    mid = {0: p.y + h / 2, 1: p.y + h / 2, 2: p.y, 3: p.y - h / 2}[valign]
    return p.x, mid, h, {0: "left", 1: "center", 2: "right", 3: "left", 4: "center", 5: "left"}[halign]


def _segments(entities: list[DXFGraphic]) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """Axis-parallel segments: (y, x0, x1) horizontal and (x, y0, y1) vertical."""
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []

    def add(p: Vec3, q: Vec3) -> None:
        if abs(p.y - q.y) <= _AXIS_TOL and abs(p.x - q.x) > _AXIS_TOL:
            horizontal.append((p.y, min(p.x, q.x), max(p.x, q.x)))
        elif abs(p.x - q.x) <= _AXIS_TOL and abs(p.y - q.y) > _AXIS_TOL:
            vertical.append((p.x, min(p.y, q.y), max(p.y, q.y)))

    for e in entities:
        t = e.dxftype()
        if t == "LINE":
            add(e.dxf.start, e.dxf.end)
        elif t in ("LWPOLYLINE", "POLYLINE"):
            pts = [Vec3(x, y) for x, y in (e.get_points("xy") if t == "LWPOLYLINE" else [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices])]
            closed = e.closed if t == "LWPOLYLINE" else e.is_closed
            for a, b in zip(pts, pts[1:] + (pts[:1] if closed else []), strict=False):
                add(a, b)
    return horizontal, vertical


def _cell(px: float, py: float, hor, ver, bounds: Region) -> Region:
    x0 = max((x for x, a, b in ver if x < px - EPS and a - _AXIS_TOL <= py <= b + _AXIS_TOL), default=bounds.x0)
    x1 = min((x for x, a, b in ver if x > px + EPS and a - _AXIS_TOL <= py <= b + _AXIS_TOL), default=bounds.x1)
    y0 = max((y for y, a, b in hor if y < py - EPS and a - _AXIS_TOL <= px <= b + _AXIS_TOL), default=bounds.y0)
    y1 = min((y for y, a, b in hor if y > py + EPS and a - _AXIS_TOL <= px <= b + _AXIS_TOL), default=bounds.y1)
    return Region(x0, y0, x1, y1)


def _flatten(entities, skipped: Counter, depth: int = 0) -> list[DXFGraphic]:
    out: list[DXFGraphic] = []
    for e in entities:
        t = e.dxftype()
        if t == "INSERT" and depth < 6:
            out += _flatten(list(e.virtual_entities()), skipped, depth + 1)
        elif t == "HATCH" and e.dxf.pattern_name.upper() not in HATCH_PATTERNS:
            skipped[f"HATCH {e.dxf.pattern_name}"] += 1
        elif t in _COPYABLE | {"MTEXT", "ATTDEF"}:
            out.append(e)
        elif t not in ("ATTRIB", "VIEWPORT"):
            skipped[t] += 1
    return out


def _source(ref: TemplateRef, skipped: Counter) -> tuple[Drawing, list[DXFGraphic]]:
    doc = ezdxf.readfile(ref.dxf)
    if ref.block is not None:
        if ref.block not in doc.blocks:
            raise ValueError(f"{ref.dxf.name} has no block {ref.block!r}; blocks: {sorted(b.name for b in doc.blocks if not b.name.startswith('*'))}")
        space = list(doc.blocks.get(ref.block))
    else:
        space = [e for e in doc.modelspace() if ref.layer is None or e.dxf.layer == ref.layer]
    if not space:
        raise ValueError(f"{ref.dxf.name} holds no entities for the title block" + (f" on layer {ref.layer!r}" if ref.layer else ""))
    return doc, _flatten(space, skipped)


def parse_template(ref: TemplateRef) -> Template:
    """Read a title block DXF. Problems are collected, not raised, so an importer can report all of them."""
    skipped: Counter = Counter()
    src, entities = _source(ref, skipped)
    problems: list[str] = []
    placeholders = [(name, e) for e in entities if (name := _is_placeholder(e))]
    static = [e for e in entities if _is_placeholder(e) is None and e.dxftype() != "ATTDEF"]
    shapes = [e for e in static if e.dxftype() not in _TEXT_TYPES]
    box = bbox.extents(shapes or static)
    if not box.has_data:
        raise ValueError(f"{ref.dxf.name}: the title block has no geometry")
    extents = Region(box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)
    hor, ver = _segments(static)
    labels = [(e, *_text_anchor(e)) for e in static if e.dxftype() in ("TEXT", "MTEXT")]
    fields: list[Placeholder] = []
    for name, e in placeholders:
        if name not in KNOWN_PLACEHOLDERS:
            problems.append(f"unknown placeholder {{{name}}}; known: {', '.join(sorted(KNOWN_PLACEHOLDERS))}")
            continue
        x, y, h, align = _text_anchor(e)
        nudge = {"left": 0.1 * h, "center": 0.0, "right": -0.1 * h}[align]
        cell = _cell(x + nudge, y, hor, ver, extents)
        ceiling = cell.y1
        for lab, lx, ly, lh, lalign in labels:
            lw = text_width(lab.dxf.text if lab.dxftype() == "TEXT" else lab.plain_text(), lh)
            left = {"left": lx, "center": lx - lw / 2, "right": lx - lw}[lalign]
            bottom = ly - lh / 2 - DESCENDER * lh
            if ly > y + EPS and bottom >= y and cell.x0 - EPS <= left and left + lw <= cell.x1 + EPS and cell.y0 <= ly <= cell.y1:
                ceiling = min(ceiling, bottom)
        fields.append(Placeholder(name, Region(cell.x0, cell.y0, cell.x1, ceiling), h, align, x))
    names = {f.name for f in fields}
    for group in REQUIRED_PLACEHOLDERS:
        if not set(group) & names:
            problems.append(f"missing required placeholder {{{group[0]}}}" + (f" (or {{{group[1]}}})" if len(group) > 1 else ""))
    seen: set[str] = set()
    for f in fields:
        if f.name in seen and f.name != "LOGO":
            problems.append(f"placeholder {{{f.name}}} appears more than once")
        seen.add(f.name)
    for f in fields:
        if f.box.width < 2 * f.height * 0.5 or f.box.height < f.height:
            problems.append(f"placeholder {{{f.name}}} sits in a cell of {f.box.width:.1f} x {f.box.height:.1f} mm, too small for its {f.height:g} mm text")
    layers = {}
    for name in sorted({e.dxf.get("layer", "0") for e in static} - {"0"}):
        lay = src.layers.get(name) if name in src.layers else None
        lw = lay.dxf.get("lineweight", -3) if lay is not None else -3
        layers[name] = (abs(lay.dxf.get("color", 7)) or 7 if lay is not None else 7, lay.dxf.get("linetype", "CONTINUOUS") if lay is not None else "CONTINUOUS", lw if lw >= MIN_LINEWEIGHT else 25)
    return Template(ref, extents, tuple(fields), tuple(static), layers, tuple(problems), tuple(f"{n} x {k}" for k, n in sorted(skipped.items())))


@lru_cache(maxsize=32)
def _cached(ref: TemplateRef, mtime: float) -> Template:
    return parse_template(ref)


def load_template(ref: TemplateRef) -> Template:
    """`parse_template`, cached until the file changes; raises ValueError when the template is unusable."""
    t = _cached(ref, ref.dxf.stat().st_mtime)
    if t.problems:
        raise ValueError(f"title block {ref.dxf.name}: " + "; ".join(t.problems))
    return t


class ImportedTitleBlock:
    """The company's title block drawn at the bottom of the right column, same interface as `TitleBlock`."""

    def __init__(self, cv: Canvas, m: SheetMetrics, doc: Drawing, data: TitleBlockData, profile: CompanyProfile, x0: float, x1: float, template: Template):
        self.cv, self.m, self.doc, self.data, self.profile, self.template = cv, m, doc, data, profile, template
        self.x0, self.x1 = x0, x1
        self.scale = (x1 - x0) / template.extents.width
        self.height = template.extents.height * self.scale
        if not _MIN_SCALE <= self.scale <= _MAX_SCALE:
            cv.warnings.append(f"title block {template.ref.dxf.name} is drawn {self.scale:.2f}x to fill the {x1 - x0:.0f} mm column; its text will look wrong")
        if self.height > _MAX_HEIGHT_SHARE * m.column.height:
            cv.warnings.append(f"title block is {self.height:.0f} mm tall, more than {_MAX_HEIGHT_SHARE:.0%} of the {m.column.height:.0f} mm column")

    def _to_sheet(self, y_bottom: float) -> Matrix44:
        e = self.template.extents
        s = self.scale
        return Matrix44.translate(-e.x0, -e.y0, 0) @ Matrix44.scale(s, s, s) @ Matrix44.translate(self.x0, y_bottom, 0)

    def _region(self, r: Region, y_bottom: float) -> Region:
        e, s = self.template.extents, self.scale
        return Region(self.x0 + (r.x0 - e.x0) * s, y_bottom + (r.y0 - e.y0) * s, self.x0 + (r.x1 - e.x0) * s, y_bottom + (r.y1 - e.y0) * s)

    def _define_layers(self) -> None:
        for name, (colour, linetype, lineweight) in self.template.layers.items():
            if name not in self.doc.layers:
                self.doc.layers.add(name, color=colour, linetype=linetype if linetype in self.doc.linetypes else "CONTINUOUS", lineweight=lineweight)

    def _copy_static(self, y_bottom: float) -> None:
        self._define_layers()
        m44 = self._to_sheet(y_bottom)
        msp, doc = self.doc.modelspace(), self.doc
        for e in self.template.entities:
            t = e.dxftype()
            layer = e.dxf.get("layer", "0")
            if layer == "0":
                layer = self.profile.layer(T).name
            if t == "MTEXT":
                self._copy_mtext(e, m44, layer)
                continue
            c = e.copy()
            c.transform(m44)
            c.dxf.layer = layer
            lt = c.dxf.get("linetype", "BYLAYER")
            if lt.upper() not in ("BYLAYER", "BYBLOCK") and lt not in doc.linetypes:
                c.dxf.linetype = "BYLAYER"
            if c.dxf.hasattr("lineweight") and 0 <= c.dxf.lineweight < MIN_LINEWEIGHT:
                c.dxf.discard("lineweight")
            if t == "TEXT":
                c.dxf.text = plain(c.dxf.text)
                style = c.dxf.get("style", "Standard").lower()
                c.dxf.style = style if style in LIBRECAD_FONTS and style in doc.styles else self.profile.text_style
            msp.add_entity(c)

    def _copy_mtext(self, e: DXFGraphic, m44: Matrix44, layer: str) -> None:
        h = e.dxf.char_height * self.scale
        ap = e.dxf.get("attachment_point", 1)
        p = m44.transform(Vec3(e.dxf.insert))
        align = {0: "left", 1: "center", 2: "right"}[(ap - 1) % 3]
        for i, line in enumerate(e.plain_text(split=True)):
            if not line.strip():
                continue
            y = p.y - h - i * h * LINE_PITCH
            w = text_width(line, h)
            left = {"left": p.x, "center": p.x - w / 2, "right": p.x - w}[align]
            self.doc.modelspace().add_text(plain(line), dxfattribs={"layer": layer, "style": self.profile.text_style, "height": h, "insert": (left, y)})

    def _fill(self, f: Placeholder, text: str, y_bottom: float) -> None:
        cv = self.cv
        cell = self._region(f.box, y_bottom)
        if f.name == "LOGO":
            inner = cell.inset(0.06 * min(cell.width, cell.height))
            if not place_logo(cv, self.doc, self.profile, inner):
                cv.put(T, inner, self.profile.short_code, 0.5 * inner.height, align="center", valign="middle", pad_x=0.05 * inner.width, pad_y=0.05 * inner.height)
            return
        if f.name in _ISSUED_NAMES:
            if text:
                side = 0.6 * min(cell.width, cell.height)
                cx, cy = cell.centre
                a = Region(cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
                cv.line(T, (a.x0, a.y0), (a.x1, a.y1), heavy=True)
                cv.line(T, (a.x0, a.y1), (a.x1, a.y0), heavy=True)
            return
        if not text:
            return
        h = f.height * self.scale
        pad_x, pad_y = 0.25 * h, 0.3 * h
        inner_h = cell.height - 2 * pad_y
        h = min(h, inner_h / (1 + DESCENDER))
        lines = max(1, int((inner_h / h - (1 + DESCENDER)) / LINE_PITCH) + 1)
        ax = self.x0 + (f.x - self.template.extents.x0) * self.scale
        span = {
            "left": Region(ax - pad_x, cell.y0, cell.x1, cell.y1),
            "right": Region(cell.x0, cell.y0, ax + pad_x, cell.y1),
            "center": Region(ax - (half := min(ax - cell.x0, cell.x1 - ax)), cell.y0, ax + half, cell.y1),
        }[f.align]
        if f.name in _WRAPPED:
            inner = span.inset(pad_x, pad_y)
            for n in range(lines, lines + 8):
                fitted = fit(text, inner.width, h, max_lines=n, max_h=inner.height)
                if not fitted.truncated:
                    cv.draw_fitted(T, span, inner, fitted, f.align, "middle" if len(fitted.lines) == 1 else "top")
                    return
        cv.put(T, span, text, h, align=f.align, valign="middle" if lines == 1 else "top", max_lines=lines, pad_x=pad_x, pad_y=pad_y)

    def draw(self, y_bottom: float) -> Region:
        self._copy_static(y_bottom)
        values = _values(self.data, self.profile)
        for f in self.template.fields:
            self._fill(f, values[f.name]() if f.name != "LOGO" else "", y_bottom)
        names = self.template.names
        unmapped = [n for n in REPORTED_WHEN_MISSING if n not in names and not (n == "OWNER" and "CLIENT" in names) and values[n]()]
        if self.data.consultant and "CONSULTANT" not in names:
            unmapped.append("CONSULTANT")
        if self.data.loa_no and "LOA_NO" not in names:
            unmapped.append("LOA_NO")
        if unmapped:
            self.cv.warnings.append(f"title block {self.template.ref.dxf.name} has no placeholder for {', '.join(unmapped)}; those values are not shown")
        return Region(self.x0, y_bottom, self.x1, y_bottom + self.height)

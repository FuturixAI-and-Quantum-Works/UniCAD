import math
from dataclasses import dataclass

from ezdxf.document import Drawing

from unicad.drafting import dxfrules
from unicad.drafting.sheets.canvas import EPS, Canvas, LayoutOverflow, Region, TextCell
from unicad.drafting.sheets.layout import SheetMetrics, metrics_for
from unicad.drafting.sheets.model import CompanyProfile, LayerRole, SheetSpec
from unicad.drafting.sheets.tables import LegendSlot, Section, SymbolDrawer, Tables
from unicad.drafting.sheets.symbol_override import apply_symbol_overrides
from unicad.drafting.sheets.template import ImportedTitleBlock, load_template
from unicad.drafting.sheets.titleblock import TitleBlock

__all__ = ["LayoutOverflow", "LegendSlot", "Region", "RenderedSheet", "TextCell", "render_sheet"]

F = LayerRole.FRAME
_SCALE_STEPS = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6)
_MIN_REV_ROWS = 3
_TWO_COLUMN_BELOW = 0.8
_MAX_REV_SHARE = 0.3
ASSUMPTIONS_TITLE = "ASSUMED INPUTS (TO BE CONFIRMED)"


@dataclass(frozen=True)
class RenderedSheet:
    drawing_area: Region
    legend_slots: tuple[LegendSlot, ...]
    column: Region
    frame: Region
    warnings: tuple[str, ...]
    text_cells: tuple[TextCell, ...]


def _prepare(doc: Drawing, profile: CompanyProfile) -> list[str]:
    dxfrules.prepare(doc)
    font = profile.text_font if "." in profile.text_font else f"{profile.text_font}.shx"
    if profile.text_style in doc.styles:
        doc.styles.get(profile.text_style).dxf.font = font
    else:
        doc.styles.add(profile.text_style, font=font)
    for role in LayerRole:
        spec = profile.layer(role)
        if spec.linetype not in doc.linetypes:
            raise ValueError(f"linetype {spec.linetype!r} of layer {spec.name!r} is not defined in the document")
        if spec.name in doc.layers:
            continue
        doc.layers.add(spec.name, color=spec.colour, linetype=spec.linetype, lineweight=spec.lineweight)
    return apply_symbol_overrides(doc, profile) if profile.symbol_overrides else []


def _title_block(cv: Canvas, m: SheetMetrics, doc: Drawing, spec: SheetSpec, profile: CompanyProfile, x0: float, x1: float) -> TitleBlock | ImportedTitleBlock:
    choice = profile.title_block
    if choice.kind == "imported":
        nearest = min(choice.templates, key=lambda p: abs(math.log(p.width * p.height / (spec.paper.width * spec.paper.height))))
        try:
            return ImportedTitleBlock(cv, m, doc, spec.title_block, profile, x0, x1, load_template(choice.templates[nearest]))
        except (ValueError, OSError) as exc:
            cv.warnings.append(f"imported title block not used, built-in one drawn: {exc}")
    return TitleBlock(cv, m, doc, spec.title_block, profile, x0, x1)


def _draw_frame(cv: Canvas, m: SheetMetrics) -> None:
    cv.rect(F, m.outer)
    cv.rect(F, m.inner, heavy=True)
    cv.vline(F, m.column.x0, m.column.y0, m.column.y1, heavy=True)
    zw = m.inner.width / m.zones_x
    zh = m.inner.height / m.zones_y
    label_h = 0.9 * m.th
    for i in range(m.zones_x + 1):
        x = m.inner.x0 + i * zw
        if 0 < i < m.zones_x:
            cv.vline(F, x, m.outer.y0, m.inner.y0)
            cv.vline(F, x, m.inner.y1, m.outer.y1)
        if i < m.zones_x:
            label = str(i + 1)
            for y0, y1 in ((m.outer.y0, m.inner.y0), (m.inner.y1, m.outer.y1)):
                cv.put(F, Region(x, y0, x + zw, y1), label, label_h, align="center", valign="middle",
                       pad_x=0.2 * m.th, pad_y=0.2 * m.th)
    for j in range(m.zones_y + 1):
        y = m.inner.y0 + j * zh
        if 0 < j < m.zones_y:
            cv.hline(F, y, m.outer.x0, m.inner.x0)
            cv.hline(F, y, m.inner.x1, m.outer.x1)
        if j < m.zones_y:
            label = chr(ord("A") + j)
            for x0, x1 in ((m.outer.x0, m.inner.x0), (m.inner.x1, m.outer.x1)):
                cv.put(F, Region(x0, y, x1, y + zh), label, label_h, align="center", valign="middle",
                       pad_x=0.2 * m.th, pad_y=0.2 * m.th)


def _water_fill(natural: dict[str, float], room: float) -> dict[str, float]:
    """Give every section its natural height if the equal share covers it, else the share."""
    budgets: dict[str, float] = {}
    left = room
    pending = sorted(natural, key=natural.__getitem__)
    for i, name in enumerate(pending):
        share = left / (len(pending) - i)
        budgets[name] = min(natural[name], share)
        left -= budgets[name]
    return budgets


def _plan_top_sections(t: Tables, spec: SheetSpec, room: float) -> list[Section]:
    """Largest text scale at which everything fits; a two-column legend before shrinking further."""
    columns = 1

    def build(f: float, budgets: dict[str, float] | None) -> list[Section]:
        def b(name: str) -> float | None:
            return budgets[name] if budgets else None

        sections: list[Section] = []
        if spec.legend:
            sections.append(t.legend(spec.legend, f, b("legend"), columns))
        if spec.parameters:
            sections.append(t.parameters(spec.parameters, f, b("parameters")))
        if spec.notes:
            sections.append(t.notes(spec.notes, f, b("notes")))
        if spec.assumptions:
            sections.append(t.notes(spec.assumptions, f, b("assumptions"), ASSUMPTIONS_TITLE, "assumptions"))
        return sections

    for f in _SCALE_STEPS:
        sections = build(f, None)
        if sum(s.height for s in sections) <= room:
            return sections
        if f <= _TWO_COLUMN_BELOW and columns == 1 and len(spec.legend) > 1:
            columns = 2
            sections = build(f, None)
            if sum(s.height for s in sections) <= room:
                return sections
    f = _SCALE_STEPS[-1]
    natural = build(f, None)
    budgets = _water_fill({s.name: s.height for s in natural}, room)
    for _ in range(5):
        sections = build(f, budgets)
        spare = room - sum(s.height for s in sections)
        short = [s for s in sections if s.omitted]
        if spare <= EPS or not short:
            break
        for s in short:
            budgets[s.name] = s.height + spare / len(short)
    return sections


def render_sheet(
    doc: Drawing, spec: SheetSpec, profile: CompanyProfile, draw_symbol: SymbolDrawer | None = None
) -> RenderedSheet:
    """Draw the border, zone grid, right-hand column and title block into modelspace.

    Modelspace at 1:1 paper millimetres, origin at the sheet's lower-left corner. LibreCAD
    draws no viewports and prints paperspace entities on top of modelspace in the same
    coordinates (docs/compat.md), so nothing goes into paperspace and every generator works
    in paper millimetres (symbols included).
    """
    notes = _prepare(doc, profile)
    m = metrics_for(spec.paper)
    cv = Canvas(doc, profile)
    cv.warnings.extend(notes)
    _draw_frame(cv, m)

    col = m.column
    tb = _title_block(cv, m, doc, spec, profile, col.x0, col.x1)
    tb.draw(col.y0)
    tables = Tables(cv, m, col.x0, col.x1, draw_symbol)

    rev_top = col.y0 + tb.height
    rev_rows = max(_MIN_REV_ROWS, len(spec.revisions))
    row_h = 3.0 * m.th
    cap = max(1, int((col.height - tb.height) * _MAX_REV_SHARE // row_h))
    rev_rows = min(rev_rows, cap)
    revisions = tables.revisions(spec.revisions, rev_rows, profile.date_format, max(cap * row_h, 2 * row_h) + 2.0 * m.th)
    revisions.draw(rev_top + revisions.height)
    if revisions.omitted:
        cv.warnings.append(f"{revisions.omitted} earliest revisions did not fit and were omitted")

    top_floor = rev_top + revisions.height
    room = col.y1 - top_floor
    sections = _plan_top_sections(tables, spec, room) if room > 4 * m.th else []
    if sections and sections[-1].name in ("notes", "assumptions"):
        used_above = sum(s.height for s in sections[:-1])
        sections[-1].with_height(room - used_above)
    y = col.y1
    slots: list[LegendSlot] = []
    for s in sections:
        s.draw(y)
        y -= s.height
        slots.extend(s.slots)
        if s.omitted:
            cv.warnings.append(f"{s.omitted} {s.name} rows did not fit and were omitted")
    wanted = (bool(spec.legend), bool(spec.parameters), bool(spec.notes), bool(spec.assumptions))
    if sum(wanted) and not sections:
        cv.warnings.append("no room for legend, parameters or notes")

    return RenderedSheet(
        drawing_area=m.drawing_area,
        legend_slots=tuple(slots),
        column=col,
        frame=m.inner,
        warnings=tuple(cv.warnings),
        text_cells=tuple(cv.text_cells),
    )

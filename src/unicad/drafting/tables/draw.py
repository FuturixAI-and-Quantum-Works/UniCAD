"""Draw planned table pages onto company sheets with LINE, TEXT and SOLID only."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import ezdxf
from ezdxf.colors import rgb2int
from ezdxf.document import Drawing

from unicad.drafting.sheets.canvas import Canvas, Region
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import CompanyProfile, LayerRole, PaperSize, SheetSpec
from unicad.drafting.sheets.render import render_sheet
from unicad.drafting.sheets.text import fit
from unicad.drafting.tables.model import TableSpec, ascii_safe
from unicad.drafting.tables.plan import BandItem, RowItem, TablePlan, plan_table

TXT = LayerRole.TEXT
HEADER_FILL = rgb2int((221, 235, 247))
BAND_FILL = rgb2int((248, 203, 173))
DIM_HEADER = {"$DIMTXT": 2.5, "$DIMASZ": 2.5, "$DIMEXE": 1.25, "$DIMEXO": 0.625, "$DIMGAP": 0.625, "$DIMDEC": 2}
ROW_PAD_Y = 0.3


@dataclass(frozen=True)
class TableRender:
    """`docs` holds the pages of all tables in order; `parts[i]` is the part each page belongs to."""

    docs: tuple[Drawing, ...]
    parts: tuple[int, ...]
    plans: tuple[TablePlan, ...]
    warnings: tuple[str, ...]

    @property
    def pages(self) -> int:
        return len(self.docs)


def new_document() -> Drawing:
    doc = ezdxf.new("R2018", setup=["linetypes"])
    for key, value in DIM_HEADER.items():
        doc.header[key] = value
    return doc


def _fill(cv: Canvas, region: Region, colour: int) -> None:
    pts = [(region.x0, region.y0), (region.x1, region.y0), (region.x0, region.y1), (region.x1, region.y1)]
    cv.msp.add_solid(pts, dxfattribs={"layer": cv.profile.layer(TXT).name, "true_color": colour})


def _draw_preamble(cv: Canvas, plan: TablePlan, top: float) -> float:
    pre = plan.preamble
    assert pre is not None
    a = plan.area
    y = top
    for row in pre.rows:
        label = Region(a.x0, y - row.height, a.x0 + pre.label_width, y)
        value = Region(label.x1, y - row.height, a.x1, y)
        cv.draw_fitted(TXT, label, label.inset(plan.pad_x, plan.pad_y), row.label, "left", "middle")
        cv.draw_fitted(TXT, value, value.inset(plan.pad_x, plan.pad_y), row.value, "left", "middle")
        y -= row.height
        cv.hline(TXT, y, a.x0, a.x1)
    cv.vline(TXT, a.x0 + pre.label_width, y, top)
    cv.rect(TXT, Region(a.x0, y, a.x1, top), heavy=True)
    return y


def _draw_header(cv: Canvas, spec: TableSpec, plan: TablePlan, top: float) -> float:
    xs, a = plan.edges, plan.area
    g, c = plan.group_tier_h, plan.column_tier_h
    bottom = top - g - c
    _fill(cv, Region(a.x0, bottom, a.x1, top), HEADER_FILL)
    cols = spec.columns
    i = 0
    while i < len(cols):
        group = cols[i].group
        j = i
        while group and j + 1 < len(cols) and cols[j + 1].group == group:
            j += 1
        if group:
            cell = Region(xs[i], top - g, xs[j + 1], top)
            label = fit(ascii_safe(group), cell.width - 2 * plan.pad_x, plan.text_h, max_lines=1, min_h=plan.text_h)
            cv.draw_fitted(TXT, cell, cell.inset(plan.pad_x, plan.pad_y), label, "center", "middle")
            cv.hline(TXT, top - g, xs[i], xs[j + 1])
        for k in range(i, j + 1):
            cell = Region(xs[k], bottom, xs[k + 1], top - g if group else top)
            cv.draw_fitted(TXT, cell, cell.inset(plan.pad_x, plan.pad_y), plan.titles[k], "center", "middle")
        i = j + 1
    for k in range(1, len(cols)):
        under_one_group = cols[k].group is not None and cols[k].group == cols[k - 1].group
        cv.vline(TXT, xs[k], bottom, top - g if under_one_group else top)
    cv.hline(TXT, bottom, a.x0, a.x1)
    return bottom


def _draw_items(cv: Canvas, spec: TableSpec, plan: TablePlan, items: tuple[RowItem | BandItem, ...], top: float) -> float:
    xs, a = plan.edges, plan.area
    y = top
    for item in items:
        cell = Region(a.x0, y - item.height, a.x1, y)
        if isinstance(item, BandItem):
            _fill(cv, cell, BAND_FILL)
            cv.draw_fitted(TXT, cell, cell.inset(plan.pad_x, plan.pad_y), item.fitted, "center", "middle")
        else:
            for k, f in enumerate(item.cells):
                box = Region(xs[k], cell.y0, xs[k + 1], y)
                cv.draw_fitted(TXT, box, box.inset(plan.pad_x, plan.pad_y), f, spec.columns[k].align, "middle")
            for x in xs[1:-1]:
                cv.vline(TXT, x, cell.y0, y)
        y -= item.height
        cv.hline(TXT, y, a.x0, a.x1)
    return y


def draw_page(cv: Canvas, spec: TableSpec, plan: TablePlan, page: int) -> None:
    top = plan.area.y1
    if page == 0 and plan.preamble:
        top = _draw_preamble(cv, plan, top) - 2 * plan.text_h
    body_top = _draw_header(cv, spec, plan, top)
    bottom = _draw_items(cv, spec, plan, plan.pages[page], body_top)
    cv.rect(TXT, Region(plan.area.x0, bottom, plan.area.x1, top), heavy=True)


def render_tables(
    parts: Sequence[tuple[TableSpec, PaperSize]], profile: CompanyProfile, sheet_for_page: Callable[[int, int, int], SheetSpec]
) -> TableRender:
    """One DXF per page, each a company sheet whose drawing area holds that page of a table.

    Pages of all `parts` are numbered together: `sheet_for_page(page, pages, part)` supplies the sheet spec of page `page`
    (1-based) of `pages` for table number `part`, so the title blocks read "x of y" across the whole set.
    """
    plans = []
    for spec, paper in parts:
        m = metrics_for(paper)
        plans.append(plan_table(spec, m.drawing_area, m.th, m.pad_x, ROW_PAD_Y * m.th))
    total = sum(len(p.pages) for p in plans)
    docs: list[Drawing] = []
    owner: list[int] = []
    warnings = [w for p in plans for w in p.warnings]
    for part, ((spec, paper), plan) in enumerate(zip(parts, plans, strict=True)):
        for page in range(len(plan.pages)):
            doc = new_document()
            sheet = render_sheet(doc, sheet_for_page(len(docs) + 1, total, part), profile)
            if sheet.drawing_area != plan.area:
                raise ValueError("the sheet's drawing area differs from the paper's metrics")
            cv = Canvas(doc, profile)
            draw_page(cv, spec, plan, page)
            warnings += [*sheet.warnings, *cv.warnings]
            docs.append(doc)
            owner.append(part)
    return TableRender(tuple(docs), tuple(owner), tuple(plans), tuple(dict.fromkeys(warnings)))

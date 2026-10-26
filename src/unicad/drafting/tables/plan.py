"""Fit a table to a sheet's drawing area: column widths from content, row heights, and page breaks with repeated headings."""

from dataclasses import dataclass

from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.text import Fitted, block_height, fit, text_width
from unicad.drafting.tables.model import Column, TableSpec, ascii_safe

TEXT_SCALE = 0.9
SCALE_STEPS = (1.0, 0.9, 0.8, 0.72, 0.65)
WRAP_CAP_HEIGHTS = 30.0
WRAP_FLOOR_HEIGHTS = 9.0
MAX_PREAMBLE_LINES = 4


@dataclass(frozen=True)
class RowItem:
    cells: tuple[Fitted, ...]
    height: float


@dataclass(frozen=True)
class BandItem:
    title: str
    fitted: Fitted
    height: float


@dataclass(frozen=True)
class PreambleRow:
    label: Fitted
    value: Fitted
    height: float


@dataclass(frozen=True)
class Preamble:
    label_width: float
    rows: tuple[PreambleRow, ...]

    @property
    def height(self) -> float:
        return sum(r.height for r in self.rows)


@dataclass(frozen=True)
class TablePlan:
    area: Region
    text_h: float
    pad_x: float
    pad_y: float
    widths: tuple[float, ...]
    titles: tuple[Fitted, ...]
    group_tier_h: float
    column_tier_h: float
    preamble: Preamble | None
    pages: tuple[tuple[RowItem | BandItem, ...], ...]
    warnings: tuple[str, ...]

    @property
    def header_h(self) -> float:
        return self.group_tier_h + self.column_tier_h

    @property
    def edges(self) -> tuple[float, ...]:
        xs = [self.area.x0]
        for w in self.widths:
            xs.append(xs[-1] + w)
        return tuple(xs)


def _cell_lines(c: Column, text: str, width: float, h: float, pad_x: float) -> Fitted:
    return fit(ascii_safe(text), width - 2 * pad_x, h, max_lines=c.max_lines if c.wrap else 1, min_h=h)


def _natural_widths(spec: TableSpec, h: float, pad_x: float) -> list[float]:
    widths = []
    for i, c in enumerate(spec.columns):
        head = max((text_width(line, h) for line in ascii_safe(c.title).split("\n")), default=0.0)
        body = max((text_width(ascii_safe(row[i]), h) for s in spec.sections for row in s.rows), default=0.0)
        if c.wrap:
            body = min(body, WRAP_CAP_HEIGHTS * h)
        widths.append(max(head, body) + 2 * pad_x)
    return widths


def _group_width_needs(spec: TableSpec, widths: list[float], h: float, pad_x: float) -> list[float]:
    """Widen the columns under a group heading when the heading is wider than they are together."""
    out = list(widths)
    for g in dict.fromkeys(c.group for c in spec.columns if c.group):
        idx = [i for i, c in enumerate(spec.columns) if c.group == g]
        need = text_width(ascii_safe(g), h) + 2 * pad_x
        short = need - sum(out[i] for i in idx)
        if short > 0:
            for i in idx:
                out[i] += short / len(idx)
    return out


def _squeeze_wrapping(spec: TableSpec, widths: list[float], excess: float, h: float, pad_x: float) -> float:
    """Narrow wrapping columns towards their floor to remove `excess` mm; returns what is still too wide."""
    floor = WRAP_FLOOR_HEIGHTS * h + 2 * pad_x
    wrappable = [i for i, c in enumerate(spec.columns) if c.wrap]
    room = sum(max(0.0, widths[i] - floor) for i in wrappable)
    if room > 0:
        take = min(1.0, excess / room)
        for i in wrappable:
            widths[i] -= max(0.0, widths[i] - floor) * take
    return max(0.0, excess - room)


def _fit_widths(spec: TableSpec, avail: float, base_h: float, pad_x: float) -> tuple[float, list[float], list[str]]:
    """Largest text height at which the columns fit (wrapping columns may narrow), with the leftover width shared in proportion."""
    for step in SCALE_STEPS:
        h = base_h * step
        widths = _group_width_needs(spec, _natural_widths(spec, h, pad_x), h, pad_x)
        left = _squeeze_wrapping(spec, widths, max(0.0, sum(widths) - avail), h, pad_x)
        if left <= 1e-6:
            return h, [w * avail / sum(widths) for w in widths], []
    warning = f"columns need {left:.0f} mm more than the sheet offers at the smallest text height; the widest cells are condensed or truncated"
    return h, [w * avail / sum(widths) for w in widths], [warning]


def plan_table(spec: TableSpec, area: Region, th: float, pad_x: float, pad_y: float) -> TablePlan:
    h, widths, warnings = _fit_widths(spec, area.width, TEXT_SCALE * th, pad_x)
    cols = spec.columns
    titles = tuple(fit(ascii_safe(c.title), w - 2 * pad_x, h, max_lines=3, min_h=h) for c, w in zip(cols, widths, strict=True))
    col_tier = max(block_height(len(t.lines), h) for t in titles) + 2 * pad_y
    group_tier = block_height(1, h) + 2 * pad_y if any(c.group for c in cols) else 0.0

    preamble = None
    if spec.preamble:
        label_w = min(0.24 * area.width, max(text_width(ascii_safe(label), h) for label, _ in spec.preamble) + 2 * pad_x)
        rows = []
        for label, value in spec.preamble:
            lf = fit(ascii_safe(label), label_w - 2 * pad_x, h, max_lines=2, min_h=h)
            vf = fit(ascii_safe(value), area.width - label_w - 2 * pad_x, h, max_lines=MAX_PREAMBLE_LINES, min_h=h)
            rows.append(PreambleRow(lf, vf, max(lf.block_height, vf.block_height) + 2 * pad_y))
        preamble = Preamble(label_w, tuple(rows))

    band_h = block_height(1, h) + 2 * pad_y
    header_h = group_tier + col_tier
    first_cap = area.height - header_h - ((preamble.height + th) if preamble else 0.0)
    cap = area.height - header_h
    pages: list[tuple[RowItem | BandItem, ...]] = []
    cur: list[RowItem | BandItem] = []
    used, limit = 0.0, first_cap

    def flush() -> None:
        nonlocal cur, used, limit
        pages.append(tuple(cur))
        cur, used, limit = [], 0.0, cap

    def band(title: str) -> BandItem:
        return BandItem(title, fit(ascii_safe(title), area.width - 2 * pad_x, h, max_lines=1, min_h=h), band_h)

    for sec in spec.sections:
        if sec.page_break_before and cur:
            flush()
        planned = []
        for row in sec.rows:
            cells = tuple(_cell_lines(c, text, w, h, pad_x) for c, text, w in zip(cols, row, widths, strict=True))
            planned.append(RowItem(cells, max(f.block_height for f in cells) + 2 * pad_y))
        pending = band(sec.title) if sec.title else None
        for i, row in enumerate(planned):
            keep = sum(r.height for r in planned[i : i + 2]) if pending else row.height
            extra = pending.height if pending else 0.0
            if cur and used + extra + keep > limit:
                flush()
                if pending is None and sec.title:
                    pending = band(f"{sec.title} (CONT.)")
                extra = pending.height if pending else 0.0
            if not cur and extra + row.height > limit:
                warnings.append(f"a row is taller than the page: {sec.title or 'table'}")
            if pending:
                cur.append(pending)
                used += pending.height
                pending = None
            cur.append(row)
            used += row.height
    if cur or not pages:
        pages.append(tuple(cur))
    return TablePlan(area, h, pad_x, pad_y, tuple(widths), titles, group_tier, col_tier, preamble, tuple(pages), tuple(dict.fromkeys(warnings)))

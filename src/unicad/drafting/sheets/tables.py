from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from unicad.drafting.sheets.canvas import Canvas, Region
from unicad.drafting.sheets.layout import SheetMetrics
from unicad.drafting.sheets.model import LayerRole, Revision
from unicad.drafting.sheets.text import block_height, fit

TXT = LayerRole.TEXT
TTL = LayerRole.TITLE


@dataclass(frozen=True)
class LegendSlot:
    key: str
    cell: Region

    @property
    def centre(self) -> tuple[float, float]:
        return self.cell.centre


@dataclass
class Section:
    name: str
    height: float
    draw: Callable[[float], None]
    omitted: int = 0
    slots: list[LegendSlot] = field(default_factory=list)

    def with_height(self, height: float) -> "Section":
        self.height = max(self.height, height)
        return self


class Tables:
    def __init__(self, cv: Canvas, m: SheetMetrics, x0: float, x1: float):
        self.cv, self.m, self.x0, self.x1 = cv, m, x0, x1

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    def _header(self, title: str, f: float) -> float:
        m = self.m
        h = m.th * f
        return block_height(1, h) + 2 * m.pad_y * f + 0.2 * m.th

    def _draw_header(self, title: str, top: float, f: float, height: float) -> None:
        cell = Region(self.x0, top - height, self.x1, top)
        self.cv.put(TXT, cell, title, self.m.th * f, align="center", valign="middle",
                    pad_x=self.m.pad_x, pad_y=self.m.pad_y * f)
        self.cv.hline(TXT, cell.y0, self.x0, self.x1)

    def _frame(self, top: float, height: float) -> None:
        self.cv.rect(TXT, Region(self.x0, top - height, self.x1, top), heavy=True)

    def legend(self, entries: Sequence[tuple[str, str]], f: float, budget: float | None) -> Section:
        m, th = self.m, self.m.th
        sl_w, sym_w = 4 * th, 9 * th
        desc_w = self.width - sl_w - sym_w
        head = self._header("LEGEND", f)
        row_min = 4.6 * th * f
        rows: list[tuple[float, object]] = []
        used = head
        for key, text in entries:
            d = fit(text, desc_w - 2 * m.pad_x, 0.9 * th * f, max_lines=3, min_h=0.65 * th)
            rh = max(row_min, d.block_height + 2 * m.pad_y * f)
            if budget is not None and used + rh > budget:
                break
            rows.append((rh, d))
            used += rh
        section = Section("legend", used, lambda top: None, omitted=len(entries) - len(rows))

        def draw(top: float) -> None:
            cv = self.cv
            self._draw_header("LEGEND", top, f, head)
            y = top - head
            for i, ((rh, d), (key, _)) in enumerate(zip(rows, entries, strict=False)):
                cv.put(TXT, Region(self.x0, y - rh, self.x0 + sl_w, y), str(i + 1), 0.9 * th * f,
                       align="center", valign="middle", pad_x=m.pad_x / 2)
                sym = Region(self.x0 + sl_w, y - rh, self.x0 + sl_w + sym_w, y)
                box = sym.inset(0.6 * th * f, m.pad_y * f)
                cv.rect(TXT, box)
                cv.put(TXT, box, key, 0.7 * th * f, align="center", valign="middle",
                       pad_x=0.15 * th, pad_y=0.1 * th, min_h=0.3 * th)
                section.slots.append(LegendSlot(key, box))
                desc = Region(sym.x1, y - rh, self.x1, y)
                cv.draw_fitted(TXT, desc, desc.inset(m.pad_x, m.pad_y * f), d, "left", "middle")
                y -= rh
                cv.hline(TXT, y, self.x0, self.x1)
            if rows:
                cv.vline(TXT, self.x0 + sl_w, top - head, y)
                cv.vline(TXT, self.x0 + sl_w + sym_w, top - head, y)
            self._frame(top, section.height)

        section.draw = draw
        return section

    def parameters(self, rows_in: Sequence[tuple[str, str]], f: float, budget: float | None) -> Section:
        m, th = self.m, self.m.th
        label_w = self.width * 0.58
        head = self._header("SYSTEM PARAMETERS", f)
        planned: list[tuple[float, object, object]] = []
        used = head
        for label, value in rows_in:
            lf = fit(label, label_w - 2 * m.pad_x, 0.9 * th * f, max_lines=2, min_h=0.65 * th)
            vf = fit(value, self.width - label_w - 2 * m.pad_x, 0.9 * th * f, max_lines=2, min_h=0.65 * th)
            rh = max(lf.block_height, vf.block_height) + 2 * m.pad_y * f
            if budget is not None and used + rh > budget:
                break
            planned.append((rh, lf, vf))
            used += rh
        section = Section("parameters", used, lambda top: None, omitted=len(rows_in) - len(planned))

        def draw(top: float) -> None:
            cv = self.cv
            self._draw_header("SYSTEM PARAMETERS", top, f, head)
            y = top - head
            for rh, lf, vf in planned:
                left = Region(self.x0, y - rh, self.x0 + label_w, y)
                right = Region(left.x1, y - rh, self.x1, y)
                cv.draw_fitted(TXT, left, left.inset(m.pad_x, m.pad_y * f), lf, "left", "middle")
                cv.draw_fitted(TXT, right, right.inset(m.pad_x, m.pad_y * f), vf, "left", "middle")
                y -= rh
                cv.hline(TXT, y, self.x0, self.x1)
            if planned:
                cv.vline(TXT, self.x0 + label_w, top - head, y)
            self._frame(top, section.height)

        section.draw = draw
        return section

    def notes(self, items: Sequence[str], f: float, budget: float | None) -> Section:
        m, th = self.m, self.m.th
        num_w = 2.8 * th * f
        head = self._header("GENERAL NOTES", f)
        planned: list[tuple[float, object]] = []
        used = head + m.pad_y * f
        for text in items:
            nf = fit(text, self.width - num_w - 2 * m.pad_x, 0.9 * th * f, max_lines=8, min_h=0.65 * th)
            rh = nf.block_height + 0.6 * m.pad_y * f
            if budget is not None and used + rh > budget:
                break
            planned.append((rh, nf))
            used += rh
        used += m.pad_y * f
        section = Section("notes", used, lambda top: None, omitted=len(items) - len(planned))

        def draw(top: float) -> None:
            cv = self.cv
            self._draw_header("GENERAL NOTES", top, f, head)
            y = top - head - m.pad_y * f
            for i, (rh, nf) in enumerate(planned):
                num = Region(self.x0, y - rh, self.x0 + num_w, y)
                cv.put(TXT, num, f"{i + 1}.", nf.height, align="right", pad_x=m.pad_x / 2)
                body = Region(num.x1, y - rh, self.x1, y)
                cv.draw_fitted(TXT, body, body.inset(m.pad_x / 2, 0), nf, "left", "top")
                y -= rh
            self._frame(top, section.height)

        section.draw = draw
        return section

    def revisions(self, revs: Sequence[Revision], n_rows: int, date_format: str) -> Section:
        m, th = self.m, self.m.th
        head = 2.0 * th
        row_h = 2.8 * th
        widths = [4.0 * th, 7.5 * th, None, 4.0 * th, 4.0 * th, 4.0 * th]
        fixed = sum(w for w in widths if w)
        widths[2] = self.width - fixed
        edges = [self.x0]
        for w in widths:
            edges.append(edges[-1] + w)
        titles = ["REV", "DATE", "DESCRIPTION", "BY", "CHKD", "APPD"]
        height = head + n_rows * row_h
        shown = list(revs)[-n_rows:] if n_rows else []
        section = Section("revisions", height, lambda top: None, omitted=len(revs) - len(shown))

        def draw(top: float) -> None:
            cv = self.cv
            bottom = top - height
            for r in range(n_rows):
                y = bottom + head + r * row_h
                cv.hline(TTL, y, self.x0, self.x1)
                if r < len(shown):
                    rev = shown[r]
                    cells = [rev.rev, rev.date.strftime(date_format), rev.description, rev.by,
                             rev.checked, rev.approved]
                    for c, text in enumerate(cells):
                        cell = Region(edges[c], y, edges[c + 1], y + row_h)
                        cv.put(TTL, cell, text, 0.9 * th, max_lines=2 if c == 2 else 1,
                               align="left" if c == 2 else "center", valign="middle",
                               pad_x=m.pad_x / 2, pad_y=m.pad_y, min_h=0.6 * th)
            for c, title in enumerate(titles):
                cell = Region(edges[c], bottom, edges[c + 1], bottom + head)
                cv.put(TTL, cell, title, m.lbl * 1.1, align="center", valign="middle",
                       pad_x=m.pad_x / 2, pad_y=m.pad_y / 2)
            for x in edges[1:-1]:
                cv.vline(TTL, x, bottom, top)
            cv.rect(TTL, Region(self.x0, bottom, self.x1, top), heavy=True)

        section.draw = draw
        return section

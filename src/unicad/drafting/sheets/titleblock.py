from collections.abc import Callable
from dataclasses import dataclass

import ezdxf
from ezdxf import bbox
from ezdxf.addons import Importer
from ezdxf.document import Drawing

from unicad.drafting.sheets.canvas import Canvas, Region
from unicad.drafting.sheets.layout import SheetMetrics
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, LayerRole, TitleBlockData
from unicad.drafting.sheets.text import fit

T = LayerRole.TITLE


LOCATION_LINES = 6


def compose_location(*parts: str | None) -> str:
    """Comma-separated place parts with each place named once, in first-seen order and ignoring case."""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for piece in (part or "").split(","):
            piece = piece.strip()
            if piece and piece.casefold() not in seen:
                seen.add(piece.casefold())
                out.append(piece)
    return ", ".join(out)


@dataclass(frozen=True)
class Row:
    height: float
    draw: Callable[[float], None]


def import_logo(doc: Drawing, profile: CompanyProfile) -> tuple[str, Region] | None:
    if profile.logo is None or not profile.logo.is_file():
        return None
    name = f"LOGO_{profile.short_code}"
    if name not in doc.blocks:
        src = ezdxf.readfile(profile.logo)
        block = doc.blocks.new(name)
        importer = Importer(src, doc)
        importer.import_entities(list(src.modelspace()), block)
        importer.finalize()
    box = bbox.extents(doc.blocks.get(name))
    if not box.has_data:
        return None
    return name, Region(box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)


def place_logo(cv: Canvas, doc: Drawing, profile: CompanyProfile, cell: Region) -> bool:
    """Draw the profile's logo block centred in `cell`, as large as fits; False when the profile has no usable logo."""
    logo = import_logo(doc, profile)
    if logo is None:
        return False
    name, box = logo
    scale = min(cell.width / box.width, cell.height / box.height)
    cx, cy = cell.centre
    bx, by = box.centre
    cv.msp.add_blockref(
        name,
        (cx - bx * scale, cy - by * scale),
        dxfattribs={"layer": profile.layer(T).name, "xscale": scale, "yscale": scale, "zscale": scale},
    )
    return True


class TitleBlock:
    def __init__(
        self,
        cv: Canvas,
        m: SheetMetrics,
        doc: Drawing,
        data: TitleBlockData,
        profile: CompanyProfile,
        x0: float,
        x1: float,
    ):
        self.cv, self.m, self.doc, self.data, self.profile = cv, m, doc, data, profile
        self.x0, self.x1 = x0, x1
        self.rows = self._plan()
        self.height = sum(r.height for r in self.rows)

    @property
    def _lp(self) -> float:
        return 0.6 * self.m.pad_y

    @property
    def _label_strip(self) -> float:
        return 1.3 * self.m.lbl + 2 * self._lp

    def _fmt_date(self, d) -> str:
        return d.strftime(self.profile.date_format) if d else ""

    def _value_block(self, text: str, width: float, h: float, max_lines: int):
        return fit(text, width - 2 * self.m.pad_x, h, max_lines=max_lines)

    def _labelled(self, cell: Region, label: str, value: str, vh: float, max_lines: int = 1,
                  align: str = "left") -> None:
        m, cv = self.m, self.cv
        cv.put(T, Region(cell.x0, cell.y1 - self._label_strip, cell.x1, cell.y1), label, m.lbl,
               pad_x=m.pad_x, pad_y=self._lp)
        body = Region(cell.x0, cell.y0, cell.x1, cell.y1 - self._label_strip + self._lp)
        cv.put(T, body, value, vh, max_lines=max_lines, pad_x=m.pad_x, pad_y=m.pad_y, align=align)

    def _labelled_height(self, width: float, value: str, vh: float, max_lines: int) -> float:
        f = self._value_block(value, width, vh, max_lines)
        return self._label_strip - self._lp + f.block_height + 2 * self.m.pad_y

    def _plan(self) -> list[Row]:
        m, d, th = self.m, self.data, self.m.th
        w = self.x1 - self.x0
        rows: list[Row] = [self._brand_row(w)]
        kv_w = 10 * th
        rows.append(self._kv_row("OWNER", d.client, 2, kv_w))
        if d.developer:
            rows.append(self._kv_row("DEVELOPER", d.developer, 2, kv_w))
        if d.consultant:
            rows.append(self._kv_row("CONSULTANT", d.consultant, 2, kv_w))
        rows.append(self._kv_row("PROJECT TITLE", d.project_title, 3, kv_w))
        rows.append(self._kv_row("LOCATION", compose_location(d.location), 2, kv_w, LOCATION_LINES))
        if d.loa_no:
            rows.append(self._kv_row("LOA / PO NO.", d.loa_no, 2, kv_w))
        rows.append(self._single_row("DRAWING TITLE", d.drawing_title, 1.2 * th, 3))
        rows.append(self._single_row("DRAWING NO.", d.drawing_number, 1.3 * th, 1))
        rows.append(self._signoff_row(w))
        rows.append(self._meta_row(w))
        rows.append(self._issued_row(w))
        if self.profile.confidentiality_note:
            rows.append(self._note_row(w))
        return rows

    def _brand_row(self, w: float) -> Row:
        m, th = self.m, self.m.th
        height = 7.5 * th

        def draw(top: float) -> None:
            cv = self.cv
            logo_cell = Region(self.x0, top - height, self.x0 + height, top)
            cv.vline(T, logo_cell.x1, logo_cell.y0, logo_cell.y1)
            self._draw_logo(logo_cell)
            right = Region(logo_cell.x1, top - height, self.x1, top)
            name_cell = Region(right.x0, top - 3.2 * th, right.x1, top)
            f = cv.put(T, name_cell, self.profile.name, 1.25 * th, max_lines=2, pad_x=m.pad_x,
                       pad_y=m.pad_y, valign="middle")
            addr_cell = Region(right.x0, top - height, right.x1, top - 3.2 * th)
            cv.put(T, addr_cell, self.profile.address, 0.7 * th, max_lines=4, pad_x=m.pad_x,
                   pad_y=m.pad_y * 0.5)
            del f

        return Row(height, draw)

    def _draw_logo(self, cell: Region) -> None:
        m, cv = self.m, self.cv
        inner = cell.inset(0.8 * m.th)
        if not place_logo(cv, self.doc, self.profile, inner):
            cv.rect(T, inner)
            if self.profile.company_set:
                cv.put(T, inner, self.profile.short_code, 1.4 * m.th, align="center", valign="middle",
                       pad_x=0.3 * m.th, pad_y=0.3 * m.th)

    def _kv_row(self, label: str, value: str, max_lines: int, kv_w: float, overflow_lines: int | None = None) -> Row:
        """`overflow_lines` lets a value that cannot be fitted in `max_lines` even condensed and shrunk wrap onto more lines rather than be cut."""
        m = self.m
        w = self.x1 - self.x0
        f = fit(value, w - kv_w - 2 * m.pad_x, m.th, max_lines=max_lines)
        if f.truncated and overflow_lines:
            f = fit(value, w - kv_w - 2 * m.pad_x, m.th, max_lines=overflow_lines)
        height = f.block_height + 2 * m.pad_y

        def draw(top: float) -> None:
            cv = self.cv
            label_cell = Region(self.x0, top - height, self.x0 + kv_w, top)
            value_cell = Region(label_cell.x1, top - height, self.x1, top)
            cv.vline(T, label_cell.x1, label_cell.y0, label_cell.y1)
            first_baseline = top - m.pad_y - f.height
            cv.text(T, label, label_cell.x0 + m.pad_x, first_baseline, m.lbl, 1.0, label_cell)
            inner = value_cell.inset(m.pad_x, m.pad_y)
            cv.draw_fitted(T, value_cell, inner, f, "left", "top")

        return Row(height, draw)

    def _single_row(self, label: str, value: str, vh: float, max_lines: int) -> Row:
        w = self.x1 - self.x0
        height = self._labelled_height(w, value, vh, max_lines)

        def draw(top: float) -> None:
            self._labelled(Region(self.x0, top - height, self.x1, top), label, value, vh, max_lines)

        return Row(height, draw)

    def _columns(self, weights: list[float]) -> list[tuple[float, float]]:
        total = sum(weights)
        edges = [self.x0]
        for wt in weights:
            edges.append(edges[-1] + (self.x1 - self.x0) * wt / total)
        edges[-1] = self.x1
        return list(zip(edges, edges[1:], strict=False))

    def _signoff_row(self, w: float) -> Row:
        m, d, th, roles = self.m, self.data, self.m.th, self.profile.roles
        cols = self._columns([1, 1, 1, 0.85])
        people = [
            (roles.drawn, d.drawn_by, None),
            (roles.checked, d.checked_by, d.checked_date if d.checked_date != d.drawn_date else None),
            (roles.approved, d.approved_by, d.approved_date if d.approved_date != d.drawn_date else None),
            ("DATE", self._fmt_date(d.drawn_date), None),
        ]
        date_h = 0.85 * th if any(dt for _, _, dt in people) else 0.0
        height = self._label_strip - self._lp + (1.3 * th) + (1.3 * date_h) + 0.2 * th + 2 * m.pad_y

        def draw(top: float) -> None:
            cv = self.cv
            for i, ((x0, x1), (role, name, dt)) in enumerate(zip(cols, people, strict=True)):
                cell = Region(x0, top - height, x1, top)
                if i:
                    cv.vline(T, x0, cell.y0, cell.y1)
                cv.put(T, Region(x0, top - self._label_strip, x1, top), role, m.lbl,
                       pad_x=m.pad_x, pad_y=self._lp)
                body_top = top - self._label_strip + self._lp
                name_cell = Region(x0, body_top - 1.3 * th - m.pad_y, x1, body_top)
                cv.put(T, name_cell, name, th, pad_x=m.pad_x, pad_y=m.pad_y / 2)
                if dt is not None:
                    cv.put(T, Region(x0, cell.y0, x1, name_cell.y0), self._fmt_date(dt), date_h, pad_x=m.pad_x, pad_y=m.pad_y / 2)

        return Row(height, draw)

    def _meta_row(self, w: float) -> Row:
        m, d, th = self.m, self.data, self.m.th
        cols = self._columns([1.25, 1.1, 0.9, 0.8])
        cells = [
            ("SCALE", d.scale),
            ("SHEET NO.", f"{d.sheet_no} of {d.sheet_count}"),
            ("PAPER SIZE", d.paper.value),
            ("REV", d.revision),
        ]
        height = self._label_strip - self._lp + 1.3 * th + 2 * m.pad_y

        def draw(top: float) -> None:
            for i, ((x0, x1), (label, value)) in enumerate(zip(cols, cells, strict=True)):
                if i:
                    self.cv.vline(T, x0, top - height, top)
                self._labelled(Region(x0, top - height, x1, top), label, value, th)

        return Row(height, draw)

    def _issued_row(self, w: float) -> Row:
        m, th, d = self.m, self.m.th, self.data
        options = list(IssuedFor)
        box = 1.1 * th
        height = self._label_strip + box + 2 * m.pad_y

        def draw(top: float) -> None:
            cv = self.cv
            cv.put(T, Region(self.x0, top - self._label_strip, self.x1, top), "DRAWING ISSUED FOR",
                   m.lbl, pad_x=m.pad_x, pad_y=self._lp)
            cols = self._columns([1] * len(options))
            y1 = top - self._label_strip
            for (x0, x1), opt in zip(cols, options, strict=True):
                cell = Region(x0, top - height, x1, y1)
                bx0 = x0 + m.pad_x
                cy = (cell.y0 + y1) / 2
                b = Region(bx0, cy - box / 2, bx0 + box, cy + box / 2)
                cv.rect(T, b)
                if opt == d.issued_for:
                    cv.line(T, (b.x0 + 0.2 * box, b.y0 + 0.5 * box), (b.x0 + 0.42 * box, b.y0 + 0.2 * box),
                            heavy=True)
                    cv.line(T, (b.x0 + 0.42 * box, b.y0 + 0.2 * box), (b.x0 + 0.85 * box, b.y0 + 0.85 * box),
                            heavy=True)
                label_cell = Region(b.x1 + 0.5 * th, cell.y0, x1 - 0.2 * th, y1)
                f = fit(opt.value, label_cell.width, m.lbl, max_lines=1)
                cv.text(T, opt.value, label_cell.x0, cy - f.height / 2, f.height, f.width_factor, label_cell)

        return Row(height, draw)

    def _note_row(self, w: float) -> Row:
        m, th = self.m, self.m.th
        h = 0.6 * th
        f = fit(self.profile.confidentiality_note or "", w - 2 * m.pad_x, h, max_lines=3, min_h=h)
        height = f.block_height + 2 * m.pad_y

        def draw(top: float) -> None:
            cell = Region(self.x0, top - height, self.x1, top)
            self.cv.draw_fitted(T, cell, cell.inset(m.pad_x, m.pad_y), f, "left", "top")

        return Row(height, draw)

    def draw(self, y_bottom: float) -> Region:
        y = y_bottom + self.height
        for i, row in enumerate(self.rows):
            row.draw(y)
            y -= row.height
            if i < len(self.rows) - 1:
                self.cv.hline(T, y, self.x0, self.x1)
        region = Region(self.x0, y_bottom, self.x1, y_bottom + self.height)
        self.cv.rect(T, region, heavy=True)
        return region

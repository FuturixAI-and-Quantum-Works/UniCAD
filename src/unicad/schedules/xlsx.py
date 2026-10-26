"""XLSX export of the cable schedule and the bill of materials."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from unicad.schedules.bom import Bom
from unicad.schedules.model import CableRow, Schedule

HEADER_FILL = PatternFill("solid", start_color="DDEBF7")
THIN = Side(style="thin", color="808080")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


@dataclass(frozen=True)
class Column[T]:
    header: str
    unit: str
    value: Callable[[T], object]
    width: float = 0
    number_format: str | None = None


def _section_of(schedule: Schedule) -> dict[str, str]:
    return {r.instance: s.title for s in schedule.sections for r in s.rows}


def schedule_columns(schedule: Schedule) -> list[Column[CableRow]]:
    section = _section_of(schedule)
    return [
        Column("SL.NO", "", lambda r: r.sl),
        Column("CABLE IDENTIFICATION NO.", "", lambda r: r.cable_id),
        Column("SECTION", "", lambda r: section[r.instance]),
        Column("SERVICE", "", lambda r: r.service.value),
        Column("FROM", "", lambda r: r.from_tag),
        Column("TO", "", lambda r: r.to_tag),
        Column("CABLE", "", lambda r: r.callout),
        Column("CATALOG ID", "", lambda r: r.cable_ref),
        Column("CORES", "no.", lambda r: r.cores),
        Column("SIZE", "sq.mm", lambda r: r.size_mm2, number_format="0.##"),
        Column("CONDUCTOR", "", lambda r: r.conductor),
        Column("NO. OF RUNS", "no.", lambda r: r.runs),
        Column("LENGTH POSITIVE POLARITY", "m", lambda r: r.length_pos_m, number_format="0.00"),
        Column("LENGTH NEGATIVE POLARITY", "m", lambda r: r.length_neg_m, number_format="0.00"),
        Column("LENGTH SOURCE", "", lambda r: r.length_source),
        Column("LAYING", "", lambda r: r.laying),
        Column("DESIGN CURRENT Ib", "A", lambda r: r.ib_a, number_format="0.0"),
        Column("DERATED AMPACITY Iz", "A", lambda r: r.iz_a, number_format="0.0"),
        Column("VOLTAGE DROP", "%", lambda r: r.vd_pct, number_format="0.000"),
        Column("POWER LOSS", "W", lambda r: r.loss_w, number_format="0.0"),
        Column("CABLE TO SUPPLY", "m", lambda r: r.cable_metres, number_format="0.0"),
        Column("TYPE OF MODULE", "", lambda r: r.module),
        Column("MODULE WATTAGE", "Wp", lambda r: r.module_wp, number_format="0.##"),
        Column("NETWORK EDGE", "", lambda r: r.edge),
        Column("LENGTH BASIS", "", lambda r: r.length_basis),
        Column("REMARKS", "", lambda r: r.remarks),
    ]


def bom_columns() -> list[Column[tuple[int, object]]]:
    return [
        Column("SL.NO", "", lambda t: t[0]),
        Column("CATEGORY", "", lambda t: t[1].category),
        Column("ITEM", "", lambda t: t[1].item),
        Column("CATALOG ID", "", lambda t: t[1].catalog_id),
        Column("QUANTITY", "", lambda t: t[1].qty, number_format="#,##0"),
        Column("UNIT", "", lambda t: t[1].unit),
        Column("REMARKS", "", lambda t: t[1].remarks),
    ]


def _write_table[T](ws: Worksheet, columns: Sequence[Column[T]], rows: Sequence[T]) -> None:
    """Header on row 1, units on row 2, data from row 3; the first two rows stay on screen."""
    for c, col in enumerate(columns, start=1):
        for r, text in ((1, col.header), (2, col.unit)):
            cell = ws.cell(row=r, column=c, value=text or None)
            cell.font = Font(bold=r == 1, italic=r == 2)
            cell.fill = HEADER_FILL
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, row in enumerate(rows, start=3):
        for c, col in enumerate(columns, start=1):
            cell = ws.cell(row=i, column=c, value=col.value(row))
            cell.border = BORDER
            if col.number_format:
                cell.number_format = col.number_format
    for c, col in enumerate(columns, start=1):
        sample = (str(col.value(r) or "") for r in rows[:300])
        width = col.width or min(60, max([len(col.header) * 0.6 + 2, *(len(s) + 2 for s in sample)]))
        ws.column_dimensions[get_column_letter(c)].width = max(width, 8)
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(columns))}{max(2, len(rows) + 2)}"


def _write_notes(ws: Worksheet, blocks: Sequence[tuple[str, Sequence[Sequence[object]]]]) -> None:
    row = 1
    for title, lines in blocks:
        if not lines:
            continue
        ws.cell(row=row, column=1, value=title).font = Font(bold=True)
        row += 1
        for line in lines:
            for c, value in enumerate(line, start=1):
                cell = ws.cell(row=row, column=c, value=value)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            row += 1
        row += 1
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 110
    for c in "CDEF":
        ws.column_dimensions[c].width = 14


def write_schedule_xlsx(schedule: Schedule, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Cable schedule"
    _write_table(ws, schedule_columns(schedule), schedule.rows)
    totals = schedule.totals()
    notes = wb.create_sheet("Basis")
    _write_notes(
        notes,
        [
            ("PROJECT", [("Project", f"{schedule.project_name} ({schedule.project_id})")]),
            ("TOTALS", [(k, v) for k, v in totals.items()]),
            ("VOLTAGE DROP BASIS", list(schedule.basis)),
            ("VOLTAGE DROP SUMMARY", list(schedule.vd_summary)),
            ("ASSUMPTIONS", [("", a) for a in schedule.assumptions]),
            ("WARNINGS", [("", w) for w in schedule.warnings]),
            ("INCOMPLETE", [("", g) for g in schedule.gaps]),
        ],
    )
    if schedule.template_lengths:
        t = wb.create_sheet("Template lengths")
        heads = ("EDGE", "INSTANCES", "MIN (m)", "MEAN (m)", "MAX (m)", "NOTE")
        for c, h in enumerate(heads, start=1):
            cell = t.cell(row=1, column=c, value=h)
            cell.font, cell.fill, cell.border = Font(bold=True), HEADER_FILL, BORDER
        for i, tl in enumerate(schedule.template_lengths.values(), start=2):
            for c, v in enumerate((tl.edge, tl.count, round(tl.minimum_m, 2), round(tl.mean_m, 2), round(tl.maximum_m, 2), tl.note), start=1):
                t.cell(row=i, column=c, value=v).border = BORDER
        t.freeze_panes = "A2"
        for c, w in zip("ABCDEF", (16, 12, 10, 10, 10, 100), strict=True):
            t.column_dimensions[c].width = w
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_bom_xlsx(bom: Bom, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    _write_table(ws, bom_columns(), list(enumerate(bom.lines, start=1)))
    notes = wb.create_sheet("Basis")
    _write_notes(
        notes,
        [
            ("PROJECT", [("Project", f"{bom.project_name} ({bom.project_id})")]),
            ("TOTALS", list(bom.summary().items())),
            ("ASSUMPTIONS", [("", a) for a in bom.assumptions]),
            ("WARNINGS", [("", w) for w in bom.warnings]),
        ],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path

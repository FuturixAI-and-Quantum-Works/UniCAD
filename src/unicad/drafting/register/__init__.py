"""Drawing register (document list) sheet: one row per sheet of the set with its number, title, paper, scale, revision, purpose of
issue and date, the project details above the table, set totals in the parameters panel and transmittal notes."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from unicad.drafting.tables import Column, TableSection, TableSpec

KIND_TITLES: dict[str, str] = {
    "register": "DRAWING REGISTER",
    "sld": "SINGLE LINE DIAGRAMS",
    "layout": "ARRAY LAYOUT",
    "earthing": "EARTHING LAYOUT",
    "lps": "LIGHTNING PROTECTION LAYOUT",
    "strings": "STRING WIRING DIAGRAMS",
    "cable_schedule": "CABLE SCHEDULE",
    "bom": "BILL OF MATERIALS",
}

COLUMNS = (
    Column("SL.NO"),
    Column("DRAWING NO.", "left"),
    Column("DRAWING TITLE", "left", wrap=True),
    Column("SIZE"),
    Column("SCALE"),
    Column("REV."),
    Column("ISSUED FOR"),
    Column("DATE"),
)

NOTES = (
    "THIS REGISTER LISTS EVERY SHEET ISSUED WITH THIS SET AND ITS CURRENT REVISION; A SHEET NOT LISTED IS NOT PART OF THIS ISSUE.",
    "SCALES ARE TRUE ON THE PAPER SIZE LISTED; NTS = NOT TO SCALE.",
    "PLEASE ACKNOWLEDGE RECEIPT AND RETURN COMMENTS QUOTING THE DRAWING NUMBER AND REVISION.",
    "DRAWINGS ARE GENERATED FROM THE PROJECT MODEL; VALUES MARKED AS ASSUMPTIONS ON A SHEET ARE TO BE CONFIRMED BEFORE CONSTRUCTION.",
)


@dataclass(frozen=True)
class RegisterRow:
    kind: str
    number: str
    title: str
    paper: str
    scale: str
    rev: str
    issued_for: str
    date: str


def register_table(rows: Sequence[RegisterRow], preamble: Sequence[tuple[str, str]] = ()) -> TableSpec:
    """Rows grouped by drawing kind in set order, numbered continuously."""
    sections: list[TableSection] = []
    sl = 0
    for kind in dict.fromkeys(r.kind for r in rows):
        body = []
        for r in (r for r in rows if r.kind == kind):
            sl += 1
            body.append((str(sl), r.number, r.title, r.paper, r.scale, r.rev, r.issued_for.upper(), r.date))
        sections.append(TableSection(KIND_TITLES.get(kind, kind.replace("_", " ").upper()), tuple(body)))
    return TableSpec(COLUMNS, tuple(sections), tuple(preamble))


def set_totals(rows: Sequence[RegisterRow]) -> tuple[tuple[str, str], ...]:
    """(label, value) rows: sheets per kind and in the whole set, and sheets per paper size."""
    kinds = Counter(r.kind for r in rows)
    papers = Counter(r.paper for r in rows)
    out = [(KIND_TITLES.get(k, k.upper()), str(n)) for k, n in kinds.items()]
    out.append(("TOTAL SHEETS IN SET", str(len(rows))))
    out.append(("SHEETS BY SIZE", ", ".join(f"{p} x {papers[p]}" for p in sorted(papers))))
    return tuple(out)


__all__ = ["COLUMNS", "KIND_TITLES", "NOTES", "RegisterRow", "register_table", "set_totals"]

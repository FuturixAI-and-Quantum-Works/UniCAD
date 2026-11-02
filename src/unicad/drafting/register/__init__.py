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


ASSUMED_TITLE = "ASSUMED INPUTS (TO BE CONFIRMED)"
ASSUMED_POINTER = "EVERY ASSUMED INPUT OF THE SET IS LISTED IN THE REGISTER UNDER ASSUMED INPUTS, WITH THE DRAWINGS IT DRIVES."
"""What the register's side panel says in place of the list, which the table holds (it continues over as many pages as it needs)."""
KIND_SHORT: dict[str, str] = {"sld": "SLD", "layout": "LAYOUT", "earthing": "EARTHING", "lps": "LPS", "strings": "STRING DIAGRAMS",
                              "cable_schedule": "CABLE SCHEDULE", "bom": "BOM"}  # fmt: skip


def register_table(rows: Sequence[RegisterRow], preamble: Sequence[tuple[str, str]] = (),
                   assumed: Sequence[tuple[frozenset[str], str]] = ()) -> TableSpec:  # fmt: skip
    """Rows grouped by drawing kind in set order, numbered continuously, then the set's assumed inputs (A1, A2...), each naming the
    drawings of the set it drives."""
    sections: list[TableSection] = []
    sl = 0
    for kind in dict.fromkeys(r.kind for r in rows):
        body = []
        for r in (r for r in rows if r.kind == kind):
            sl += 1
            body.append((str(sl), r.number, r.title, r.paper, r.scale, r.rev, r.issued_for.upper(), r.date))
        sections.append(TableSection(KIND_TITLES.get(kind, kind.replace("_", " ").upper()), tuple(body)))
    listed = list(dict.fromkeys(r.kind for r in rows))
    body = []
    for i, (kinds, text) in enumerate(assumed, start=1):
        on = [KIND_SHORT.get(k, k.upper()) for k in listed if k in kinds]
        body.append((f"A{i}", "", f"{text}" + (f" (DRIVES: {', '.join(on)})" if on else ""), "", "", "", "", ""))
    if body:
        sections.append(TableSection(ASSUMED_TITLE, tuple(body)))
    return TableSpec(COLUMNS, tuple(sections), tuple(preamble))


def set_totals(rows: Sequence[RegisterRow]) -> tuple[tuple[str, str], ...]:
    """(label, value) rows: sheets per kind and in the whole set, and sheets per paper size."""
    kinds = Counter(r.kind for r in rows)
    papers = Counter(r.paper for r in rows)
    out = [(KIND_TITLES.get(k, k.upper()), str(n)) for k, n in kinds.items()]
    out.append(("TOTAL SHEETS IN SET", str(len(rows))))
    out.append(("SHEETS BY SIZE", ", ".join(f"{p} x {papers[p]}" for p in sorted(papers))))
    return tuple(out)


__all__ = ["ASSUMED_POINTER", "ASSUMED_TITLE", "COLUMNS", "KIND_TITLES", "NOTES", "RegisterRow", "register_table", "set_totals"]

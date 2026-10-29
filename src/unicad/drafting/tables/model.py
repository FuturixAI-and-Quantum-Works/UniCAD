"""Table content for multi-page table sheets: columns, sections of rows, and a preamble of label/value lines."""

from dataclasses import dataclass
from typing import Literal

Align = Literal["left", "center", "right"]

_ASCII = {"°": "%%d", "Ω": "ohm", "²": "2", "³": "3", "×": "x", "–": "-", "—": "-", "±": "%%p", "√": "sqrt", "·": ".", "µ": "u", "’": "'", "‘": "'", "“": '"', "”": '"'}


def ascii_safe(text: str) -> str:
    """The sheet font covers printable ASCII only: map common symbols (the degree and plus-minus signs to their %%d and %%p codes, which LibreCAD prints as the glyphs) and replace the rest with "?"."""
    mapped = "".join(_ASCII.get(c, c) for c in text)
    return "".join(c if c == "\n" or " " <= c <= "~" else "?" for c in mapped)


@dataclass(frozen=True)
class Column:
    """`title` may hold '\\n' for a two-line heading. Consecutive columns sharing `group` sit under one spanning heading."""

    title: str
    align: Align = "center"
    group: str | None = None
    wrap: bool = False
    max_lines: int = 3


@dataclass(frozen=True)
class TableSection:
    """A titled run of rows. `page_break_before` starts the section on a new page."""

    title: str | None
    rows: tuple[tuple[str, ...], ...]
    page_break_before: bool = False


@dataclass(frozen=True)
class TableSpec:
    columns: tuple[Column, ...]
    sections: tuple[TableSection, ...]
    preamble: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        n = len(self.columns)
        for s in self.sections:
            for r in s.rows:
                if len(r) != n:
                    raise ValueError(f"row {r[:2]!r} has {len(r)} cells, the table has {n} columns")
        groups = [c.group for c in self.columns]
        for g in {g for g in groups if g}:
            idx = [i for i, x in enumerate(groups) if x == g]
            if idx != list(range(idx[0], idx[-1] + 1)):
                raise ValueError(f"columns of group {g!r} are not adjacent")

    @property
    def row_count(self) -> int:
        return sum(len(s.rows) for s in self.sections)

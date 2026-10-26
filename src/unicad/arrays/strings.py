"""Assign modules in series to contiguous slots of each table."""

from collections.abc import Sequence

from unicad.domain.layout import LayoutBlock, SlotSpan, StringAssignment, Table


def serpentine(t: Table) -> list[tuple[int, int]]:
    """Slots in wiring order: row 0 left to right, row 1 right to left, and so on, skipping unused slots."""
    unused = set(t.unused)
    out = []
    for r in range(t.rows):
        cols = range(t.cols) if r % 2 == 0 else range(t.cols - 1, -1, -1)
        out += [(r, c) for c in cols if (r, c) not in unused]
    return out


def _spans(table_id: str, slots: Sequence[tuple[int, int]]) -> tuple[SlotSpan, ...]:
    spans: list[SlotSpan] = []
    for r, c in slots:
        if spans and spans[-1].row == r and c in (spans[-1].col - 1, spans[-1].col + spans[-1].n):
            last = spans[-1]
            spans[-1] = SlotSpan(table=table_id, row=r, col=min(c, last.col), n=last.n + 1)
        else:
            spans.append(SlotSpan(table=table_id, row=r, col=c, n=1))
    return tuple(spans)


def string_id(block_id: str, inverter: int, string: int) -> str:
    return f"{block_id}-I{inverter}-S{string:02d}"


def assign_strings(blocks: Sequence[LayoutBlock], modules_per_string: int, strings_per_inverter: int | None) -> tuple[list[StringAssignment], int]:
    """Strings of `modules_per_string` consecutive slots, never crossing tables; ids `B01-I1-S02` (block, inverter, string).

    Strings are numbered per inverter in table order; with `strings_per_inverter` unset each block has one inverter.
    Returns the assignments and the count of slots left without a string.
    """
    out: list[StringAssignment] = []
    left = 0
    for b in blocks:
        k = 0
        for t in b.tables:
            slots = serpentine(t)
            whole = len(slots) // modules_per_string
            left += len(slots) - whole * modules_per_string
            for s in range(whole):
                chunk = slots[s * modules_per_string : (s + 1) * modules_per_string]
                inv, n = (k // strings_per_inverter + 1, k % strings_per_inverter + 1) if strings_per_inverter else (1, k + 1)
                out.append(StringAssignment(string=string_id(b.id, inv, n), spans=_spans(t.id, chunk)))
                k += 1
    return out, left

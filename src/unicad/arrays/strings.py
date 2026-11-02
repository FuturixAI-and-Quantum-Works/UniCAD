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


def table_string_length(t: Table, modules_per_string: int, lengths: frozenset[int] | None) -> int:
    """Modules per string on `t`: with several string lengths, a table as long as one of them holds one string of that length per row."""
    return t.cols if lengths and t.cols in lengths else modules_per_string


def assign_strings(
    blocks: Sequence[LayoutBlock], modules_per_string: int, strings_per_inverter: int | None, lengths: frozenset[int] | None = None
) -> tuple[list[StringAssignment], int]:
    """Strings of `modules_per_string` consecutive slots, never crossing tables. Each gets a layout key `B01-I1-S02` (layout block,
    inverter, string) that only links the layout to the network; drawings and schedules show the network tags instead (STR-01-01-02,
    cable ids built from the inverter and combiner tags, see `unicad.schedules.ids.SCHEME`).

    Strings are numbered per inverter in table order; with `strings_per_inverter` unset each block has one inverter. With `lengths` (several
    string lengths on the plant) a table as long as one of them holds strings of that length instead of `modules_per_string`.
    Returns the assignments and the count of slots left without a string.
    """
    out: list[StringAssignment] = []
    left = 0
    for b in blocks:
        k = 0
        for t in b.tables:
            slots = serpentine(t)
            mps = table_string_length(t, modules_per_string, lengths)
            whole = len(slots) // mps
            left += len(slots) - whole * mps
            for s in range(whole):
                chunk = slots[s * mps : (s + 1) * mps]
                inv, n = (k // strings_per_inverter + 1, k % strings_per_inverter + 1) if strings_per_inverter else (1, k + 1)
                out.append(StringAssignment(string=string_id(b.id, inv, n), spans=_spans(t.id, chunk)))
                k += 1
    return out, left

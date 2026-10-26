"""Array layout types: blocks of tables or trackers, each a grid of module slots in site metres.

The placement algorithm lives elsewhere; this module only fixes the shape it fills.
Slot (row, col) indices are 0-based, row 0 nearest the table origin.
"""

from collections import defaultdict
from enum import StrEnum
from functools import cached_property
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

LayoutId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
StringId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*(@[0-9]+(\.[0-9]+)*)?$")]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Point(_Frozen):
    x: float
    y: float


class Mounting(StrEnum):
    FIXED_TILT = "fixed_tilt"
    SINGLE_AXIS_TRACKER = "single_axis_tracker"
    ROOF_FLUSH = "roof_flush"
    CARPORT = "carport"
    FLOATING = "floating"


class Orientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class Table(_Frozen):
    """A table or tracker: `rows` x `cols` module slots laid out from `origin` along `azimuth_deg`.

    `azimuth_deg` is the direction the modules face, clockwise from grid north. `slot_width_m`
    runs along a row and `slot_height_m` across rows, both on the sloped plane and including the
    gap to the next slot.
    """

    id: LayoutId
    mounting: Mounting
    origin: Point
    azimuth_deg: float = Field(ge=0, lt=360)
    tilt_deg: float = Field(ge=0, le=90)
    rows: int = Field(gt=0)
    cols: int = Field(gt=0)
    orientation: Orientation
    module: str
    slot_width_m: float = Field(gt=0)
    slot_height_m: float = Field(gt=0)
    unused: tuple[tuple[int, int], ...] = ()

    @model_validator(mode="after")
    def _unused_in_grid(self) -> Self:
        bad = [s for s in self.unused if not (0 <= s[0] < self.rows and 0 <= s[1] < self.cols)]
        if bad:
            raise ValueError(f"table {self.id!r}: unused slots {bad} lie outside its {self.rows}x{self.cols} grid")
        return self

    @property
    def slot_count(self) -> int:
        return self.rows * self.cols - len(set(self.unused))


class SlotSpan(_Frozen):
    """`n` consecutive slots in one row of a table, starting at column `col`."""

    table: LayoutId
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    n: int = Field(default=1, gt=0)

    def slots(self) -> tuple[tuple[str, int, int], ...]:
        return tuple((self.table, self.row, c) for c in range(self.col, self.col + self.n))


class StringAssignment(_Frozen):
    """Which slots hold the modules of one string. `string` is a pv_string node id or an expanded instance id."""

    string: StringId
    spans: tuple[SlotSpan, ...] = Field(min_length=1)

    def slots(self) -> tuple[tuple[str, int, int], ...]:
        return tuple(s for span in self.spans for s in span.slots())


class LayoutBlock(_Frozen):
    """`station` is where the block's inverter station stands, in site metres."""

    id: LayoutId
    name: str
    boundary: tuple[Point, ...] = ()
    tables: tuple[Table, ...] = ()
    station: Point | None = None

    @model_validator(mode="after")
    def _boundary_is_polygon(self) -> Self:
        if self.boundary and len(self.boundary) < 3:
            raise ValueError(f"block {self.id!r}: boundary needs at least 3 points")
        return self


class ArrayLayout(_Frozen):
    blocks: tuple[LayoutBlock, ...] = ()
    strings: tuple[StringAssignment, ...] = ()

    @cached_property
    def table_by_id(self) -> dict[str, Table]:
        return {t.id: t for b in self.blocks for t in b.tables}

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems = []
        ids = [b.id for b in self.blocks] + [t.id for b in self.blocks for t in b.tables]
        problems += [f"duplicate layout id {i!r}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        owner: dict[tuple[str, int, int], str] = {}
        seen_strings = defaultdict(int)
        for a in self.strings:
            seen_strings[a.string] += 1
            for table_id, row, col in a.slots():
                t = self.table_by_id.get(table_id)
                if t is None:
                    problems.append(f"string {a.string!r} uses unknown table {table_id!r}")
                    break
                if not (row < t.rows and col < t.cols):
                    problems.append(f"string {a.string!r} slot ({row},{col}) is outside table {table_id!r} ({t.rows}x{t.cols})")
                elif (row, col) in t.unused:
                    problems.append(f"string {a.string!r} uses unused slot ({row},{col}) of table {table_id!r}")
                elif (prev := owner.get((table_id, row, col))) is not None:
                    problems.append(f"slot ({row},{col}) of table {table_id!r} is assigned to both {prev!r} and {a.string!r}")
                else:
                    owner[(table_id, row, col)] = a.string
        problems += [f"string {s!r} is assigned {n} times" for s, n in seen_strings.items() if n > 1]
        if problems:
            raise ValueError("invalid array layout: " + "; ".join(problems))
        return self

    def string_slots(self, string_id: str) -> tuple[tuple[str, int, int], ...]:
        for a in self.strings:
            if a.string == string_id:
                return a.slots()
        raise KeyError(f"no layout assignment for string {string_id!r}")

"""Earthing design types: calculation steps, pits, conductor runs, bonded equipment and quantities. Coordinates are site metres."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from unicad.domain.layout import Point


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Value(_Frozen):
    name: str
    value: float | int | str
    unit: str | None = None
    source: str | None = None

    def __init__(self, name: str, value: float | str, unit: str | None = None, source: str | None = None, **kw) -> None:
        super().__init__(name=name, value=value, unit=unit, source=source, **kw)


class Step(_Frozen):
    """One calculated value with its formula, inputs and the source the formula was taken from."""

    name: str
    value: float
    unit: str
    formula: str
    inputs: tuple[Value, ...] = ()
    source: str | None = None
    assumptions: tuple[str, ...] = ()


class PitRole(StrEnum):
    """Pit tags as on the CIL/Refex earthing layout: EP periphery/general (treated), EPN transformer neutral, EPB transformer body
    (treated), EPT potential transformer and ELA lightning (surge) arrester (untreated), LE lightning protection system (untreated)."""

    EP = "EP"
    EPN = "EPN"
    EPB = "EPB"
    EPT = "EPT"
    ELA = "ELA"
    LE = "LE"

    @property
    def treated(self) -> bool:
        return self in (PitRole.EP, PitRole.EPN, PitRole.EPB)


class ConductorRole(StrEnum):
    MAIN = "main"
    ARRAY = "array"
    EQUIPMENT = "equipment"
    GRID = "grid"
    DOWN = "down"


class Pit(_Frozen):
    tag: str
    role: PitRole
    at: Point
    serves: tuple[str, ...] = ()
    on_ground: bool = True


class Run(_Frozen):
    """A conductor run: a polyline of one size and role. `links` names the items or pits it bonds."""

    id: str
    role: ConductorRole
    size: str
    points: tuple[Point, ...]
    links: tuple[str, ...] = ()
    buried: bool = True

    @property
    def length_m(self) -> float:
        return sum(((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5 for a, b in zip(self.points, self.points[1:]))


class Item(_Frozen):
    """Equipment or structure to bond. `kind` is a network node kind, 'table', 'switchyard' or 'ese'. `connections` is how many
    distinct earth connections it needs. `placed` marks equipment the layout stands in the field, at its stored position."""

    id: str
    kind: str
    tag: str
    at: Point
    connections: int = 1
    on_roof: bool = False
    placed: bool = False


class Quantity(_Frozen):
    item: str
    description: str
    quantity: float
    unit: str

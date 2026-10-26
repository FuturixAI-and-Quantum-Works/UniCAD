from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ezdxf.layouts import BlockLayout

# Symbols are drawn in paper millimetres at 1:1. Pins sit on this grid so
# conductors drawn between pins stay orthogonal.
GRID = 2.5


class Side(StrEnum):
    N = "N"
    S = "S"
    E = "E"
    W = "W"


class PinKind(StrEnum):
    DC_POS = "dc+"
    DC_NEG = "dc-"
    DC = "dc"
    AC = "ac"
    EARTH = "pe"
    SIGNAL = "sig"


class Category(StrEnum):
    PV = "pv"
    DC_PROTECTION = "dc_protection"
    CONVERSION = "conversion"
    SWITCHGEAR = "switchgear"
    PROTECTION = "protection"
    TRANSFORMER = "transformer"
    INSTRUMENT = "instrument"
    STORAGE = "storage"
    EARTHING = "earthing"
    GRID = "grid"
    ANNOTATION = "annotation"
    LAYOUT = "layout"


@dataclass(frozen=True)
class Pin:
    name: str
    x: float
    y: float
    side: Side
    kind: PinKind


@dataclass(frozen=True)
class Attr:
    tag: str
    x: float
    y: float
    height: float = 2.0
    default: str = ""
    visible: bool = True


@dataclass(frozen=True)
class SymbolDef:
    key: str
    title: str
    category: Category
    standard: str
    width: float
    height: float
    pins: tuple[Pin, ...]
    attrs: tuple[Attr, ...]
    draw: Callable[[BlockLayout], None]

    @property
    def block_name(self) -> str:
        return "UC_" + self.key.upper().replace(".", "_")

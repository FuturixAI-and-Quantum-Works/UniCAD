"""Where network node instances stand on the site, derived from the array layout."""

import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from unicad.arrays.geometry import frame, to_site
from unicad.domain.layout import ArrayLayout, Table
from unicad.domain.network import ElectricalNetwork, NodeKind
from unicad.routing.model import RouteAssumptions

COMBINERS = frozenset({NodeKind.SCB, NodeKind.DC_FUSE_BOX})
GENERATORS = frozenset({NodeKind.INVERTER, NodeKind.PCS})
CO_LOCATED_M = 1.0


@dataclass(frozen=True)
class Place:
    x: float
    y: float
    azimuth_deg: float
    where: str

    @property
    def xy(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True)
class StringPoints:
    plus: tuple[float, float]
    minus: tuple[float, float]
    centre: tuple[float, float]
    block: str
    azimuth_deg: float


def manhattan(a: tuple[float, float], b: tuple[float, float], azimuth_deg: float) -> float:
    """Distance from `a` to `b` measured along the axes of the array frame (rows, then across them)."""
    (ux, uy), (vx, vy) = frame(azimuth_deg)
    dx, dy = b[0] - a[0], b[1] - a[1]
    return abs(dx * ux + dy * uy) + abs(dx * vx + dy * vy)


def straight(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def slot_centre(t: Table, row: int, col: int) -> tuple[float, float]:
    plan_row = t.slot_height_m * math.cos(math.radians(t.tilt_deg))
    return to_site(t.origin, t.azimuth_deg, (col + 0.5) * t.slot_width_m, (row + 0.5) * plan_row)


class Locator:
    def __init__(self, layout: ArrayLayout, expanded: ElectricalNetwork, assumptions: RouteAssumptions):
        self.layout, self.net, self.assumptions = layout, expanded, assumptions
        self.block_of_table = {t.id: b.id for b in layout.blocks for t in b.tables}
        self.block_frame = {b.id: b.tables[0].azimuth_deg for b in layout.blocks if b.tables}
        self.stations = {b.id: b.station for b in layout.blocks if b.station is not None}
        self.strings = self._string_points()
        self._preds: dict[str, list[str]] = {n.id: [] for n in expanded.nodes}
        for e in expanded.edges:
            if e.is_power:
                self._preds[e.dst_node].append(e.src_node)
        self.notes: list[str] = []
        for name in ("strings_below", "generators_below", "switchyard", "location"):
            setattr(self, name, lru_cache(maxsize=None)(getattr(self, name)))

    def _string_points(self) -> dict[str, StringPoints]:
        out = {}
        for a in self.layout.strings:
            slots = a.slots()
            pts = [slot_centre(self.layout.table_by_id[t], r, c) for t, r, c in slots]
            block = self.block_of_table[slots[0][0]]
            centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            out[a.string] = StringPoints(pts[0], pts[-1], centre, block, self.layout.table_by_id[slots[0][0]].azimuth_deg)
        return out

    def strings_below(self, node_id: str) -> tuple[str, ...]:
        """Located string instances that feed `node_id`, itself included."""
        if self.net.node(node_id).kind is NodeKind.PV_STRING:
            return (node_id,) if node_id in self.strings else ()
        found: list[str] = []
        for p in self._preds[node_id]:
            found += self.strings_below(p)
        return tuple(dict.fromkeys(found))

    def generators_below(self, node_id: str) -> tuple[str, ...]:
        found = [node_id] if self.net.node(node_id).kind in GENERATORS else []
        for p in self._preds[node_id]:
            found += self.generators_below(p)
        return tuple(dict.fromkeys(found))

    def block_of_generator(self, generator: str) -> str | None:
        blocks = Counter(self.strings[s].block for s in self.strings_below(generator))
        return min(blocks, key=lambda b: (-blocks[b], b)) if blocks else None

    def switchyard(self) -> Place | None:
        a = self.assumptions
        azimuth = next(iter(self.block_frame.values()), 180.0)
        if a.switchyard is not None:
            return Place(*a.switchyard, azimuth, "switchyard")
        if not self.stations:
            return None
        pts = list(self.stations.values())
        x, y = sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)
        self.notes.append(
            f"The MV switchyard is not located in the project; it is assumed at the centroid of the {len(pts)} block station(s), local metres ({x:.1f}, {y:.1f}). Pass the real position to replace it."
        )
        return Place(x, y, azimuth, "switchyard")

    def station(self, block: str) -> Place | None:
        s = self.stations.get(block)
        return None if s is None else Place(s.x, s.y, self.block_frame.get(block, 180.0), f"station {block}")

    def location(self, node_id: str) -> Place | None:
        kind = self.net.node(node_id).kind
        below = self.strings_below(node_id)
        if kind is NodeKind.PV_STRING:
            p = self.strings.get(node_id)
            return None if p is None else Place(*p.centre, p.azimuth_deg, "string")
        if not below:
            return None
        if kind in COMBINERS:
            pts = [self.strings[s] for s in below]
            x, y = sum(p.centre[0] for p in pts) / len(pts), sum(p.centre[1] for p in pts) / len(pts)
            return Place(x, y, pts[0].azimuth_deg, f"{self.net.node(node_id).tag} (centroid of {len(pts)} strings)")
        gens = self.generators_below(node_id)
        blocks = {self.block_of_generator(g) for g in gens} - {None}
        if len(blocks) == 1:
            return self.station(next(iter(blocks)))
        if len(blocks) > 1 or not gens:
            return self.switchyard()
        return None

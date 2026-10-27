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
BUSES = frozenset({NodeKind.BUSBAR, NodeKind.HT_PANEL, NodeKind.LT_PANEL, NodeKind.ACDB, NodeKind.POI_GRID})
CO_LOCATED_M = 1.0
PANEL_KINDS = frozenset({NodeKind.VCB, NodeKind.CT, NodeKind.BREAKER, NodeKind.RMU, NodeKind.ISOLATOR, NodeKind.FUSE})


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
        self.placed = layout.equipment_by_node
        self._succs: dict[str, list[str]] = {n.id: [] for n in expanded.nodes}
        self.strings = self._string_points()
        self._preds: dict[str, list[str]] = {n.id: [] for n in expanded.nodes}
        self._wired: dict[str, list[str]] = {n.id: [] for n in expanded.nodes}
        for e in expanded.edges:
            if e.is_power:
                self._preds[e.dst_node].append(e.src_node)
                self._succs[e.src_node].append(e.dst_node)
                if e.cable is None and e.laying is None:
                    self._wired[e.src_node].append(e.dst_node)
        self.notes: list[str] = []
        for name in ("strings_below", "generators_below", "switchyard", "location", "placed_near", "fed_generator", "panel_bus"):
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
        if self.layout.switchyard is not None:
            at = self.layout.switchyard.at
            return Place(at.x, at.y, azimuth, "switchyard")
        if not self.stations:
            return None
        pts = list(self.stations.values())
        x, y = sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)
        mv = any(n.kind in (NodeKind.TRANSFORMER, NodeKind.VCB, NodeKind.RMU) for n in self.net.nodes)
        what = "MV switchyard" if mv else "main LT panel collecting the inverters"
        self.notes.append(
            f"The {what} is not located in the project; it is assumed at the centroid of the {len(pts)} block station(s), local metres ({x:.1f}, {y:.1f}). Re-run layout_place (with switchyard_x and switchyard_y for the real position) to store one location that routing and earthing share."
        )
        return Place(x, y, azimuth, "switchyard")

    def _block_near(self, at) -> str | None:
        return min(self.stations, key=lambda b: math.hypot(self.stations[b].x - at.x, self.stations[b].y - at.y), default=None)

    def station(self, block: str) -> Place | None:
        s = self.stations.get(block)
        return None if s is None else Place(s.x, s.y, self.block_frame.get(block, 180.0), f"station {block}")

    def fed_generator(self, node_id: str) -> str | None:
        """The generator a DC-side node feeds (an isolator or SPD between strings and its inverter), following power flow."""
        seen, todo = {node_id}, list(self._succs[node_id])
        while todo:
            n = todo.pop(0)
            if n in seen:
                continue
            seen.add(n)
            if self.net.node(n).kind in GENERATORS:
                return n
            if self.net.node(n).kind in COMBINERS:
                return None
            todo += self._succs[n]
        return None

    def placed_near(self, node_id: str) -> str | None:
        """For equipment with no strings below it (BESS switchgear): the nearest placed node it feeds, else the nearest placed node feeding it,
        never through a busbar or panel."""
        if not self.placed or self.net.node(node_id).kind in BUSES:
            return None
        for step in (self._succs, self._preds):
            seen, todo = {node_id}, list(step[node_id])
            while todo:
                n = todo.pop(0)
                if n in seen or self.net.node(n).kind in BUSES:
                    continue
                seen.add(n)
                if n in self.placed:
                    return n
                todo += step[n]
        return None

    def panel_bus(self, node_id: str) -> str | None:
        """The busbar a switchgear node feeds through bus wiring alone (no cable): the switchboard panel it is part of, so it stands with that bus."""
        if self.net.node(node_id).kind not in PANEL_KINDS:
            return None
        for n in self._wired[node_id]:
            if self.net.node(n).kind is NodeKind.BUSBAR:
                return n
            if (bus := self.panel_bus(n)) is not None:
                return bus
        return None

    def location(self, node_id: str) -> Place | None:
        kind = self.net.node(node_id).kind
        if node_id in self.placed:
            p = self.placed[node_id]
            return Place(p.at.x, p.at.y, self.block_frame.get(self._block_near(p.at), 180.0), f"{p.tag} ({p.basis})")
        if (bus := self.panel_bus(node_id)) is not None:
            return self.location(bus)
        below = self.strings_below(node_id)
        if kind is NodeKind.PV_STRING:
            p = self.strings.get(node_id)
            return None if p is None else Place(*p.centre, p.azimuth_deg, "string")
        if not below:
            near = self.placed_near(node_id)
            return None if near is None else self.location(near)
        if kind in COMBINERS:
            pts = [self.strings[s] for s in below]
            x, y = sum(p.centre[0] for p in pts) / len(pts), sum(p.centre[1] for p in pts) / len(pts)
            return Place(x, y, pts[0].azimuth_deg, f"{self.net.node(node_id).tag} (centroid of {len(pts)} strings)")
        gens = self.generators_below(node_id)
        if not gens and (fed := self.fed_generator(node_id)) is not None:
            return self.location(fed)
        blocks = {self.block_of_generator(g) for g in gens} - {None}
        if len(blocks) == 1:
            return self.station(next(iter(blocks)))
        if len(blocks) > 1 or not gens:
            return self.switchyard()
        return None

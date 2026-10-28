"""Earthing layout geometry: where the pits go and how conductors bond every table row, station, panel, switchyard and lightning
terminal into one earth network. Pure function of the project, the design basis and the chosen conductor sizes.

Locations are indicative, as on the reference earthing layouts: stations get a local ring with the equipment bonded to it twice,
table rows get a conductor along their centre line with risers between neighbouring rows, and separate parts are joined by the
shortest links (a minimum spanning tree over the parts).
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.geometry import Point as SPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from unicad.arrays.geometry import frame, plan_depth, plan_length, table_corners, to_site
from unicad.arrays.siting import free_spot
from unicad.domain.layout import LayoutBlock, Point, Table
from unicad.domain.network import NodeKind, Service
from unicad.domain.project import Project
from unicad.earthing.model import ConductorRole, Item, Pit, PitRole, Run
from unicad.lps.design import LpsDesign
from unicad.lps.site import blocked, boundary, designated_switchyard, is_rooftop, place_switchyard, roofs

ROW_GAP_SPLIT_M = 8.0
STATION_PITCH_M = 3.0
TOL = 1e-3

# Distinct earth connections per item (CIL/Refex note: every equipment and structure by two distinct connections).
CONNECTIONS: dict[NodeKind, int] = {
    NodeKind.INVERTER: 2, NodeKind.PCS: 2, NodeKind.BATTERY: 2, NodeKind.TRANSFORMER: 2, NodeKind.AUX_TRANSFORMER: 2,
    NodeKind.ACDB: 2, NodeKind.LT_PANEL: 2, NodeKind.HT_PANEL: 2, NodeKind.RMU: 2, NodeKind.VCB: 2, NodeKind.DG: 2,
    NodeKind.SCB: 2, NodeKind.DC_FUSE_BOX: 1, NodeKind.LIGHTNING_ARRESTER: 1, NodeKind.PT: 1, NodeKind.CT: 1, NodeKind.POI_GRID: 2,
}  # fmt: skip
PLACED_ONLY_KINDS = frozenset({NodeKind.POI_GRID})
BONDED_KINDS = frozenset(CONNECTIONS) - PLACED_ONLY_KINDS
LT_PANEL_KINDS = frozenset({NodeKind.ACDB, NodeKind.LT_PANEL, NodeKind.POI_GRID})
# Dedicated pits per equipment item (transformers 2 body + 2 neutral, panels 2: practice quoted by J. Parmar, EEP part 1; LA and PT
# untreated pits as on the CIL/Refex layout).
PITS: dict[NodeKind, tuple[PitRole, ...]] = {
    NodeKind.TRANSFORMER: (PitRole.EPN, PitRole.EPN, PitRole.EPB, PitRole.EPB),
    NodeKind.AUX_TRANSFORMER: (PitRole.EPN, PitRole.EPB),
    NodeKind.LIGHTNING_ARRESTER: (PitRole.ELA,),
    NodeKind.PT: (PitRole.EPT,),
    NodeKind.SCB: (PitRole.EP,),
}
GROUP_PITS = 2
BESS_LINK_M = 25.0
FIELD_BOXES = frozenset({NodeKind.SCB, NodeKind.DC_FUSE_BOX})
MV_KINDS = frozenset({NodeKind.VCB, NodeKind.RMU, NodeKind.HT_PANEL, NodeKind.PT, NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER, NodeKind.LIGHTNING_ARRESTER, NodeKind.CT})
KIND_ORDER = {k: i for i, k in enumerate((NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER, NodeKind.INVERTER, NodeKind.PCS, NodeKind.BATTERY,
              NodeKind.HT_PANEL, NodeKind.RMU, NodeKind.VCB, NodeKind.ACDB, NodeKind.LT_PANEL, NodeKind.DG, NodeKind.PT, NodeKind.CT,
              NodeKind.LIGHTNING_ARRESTER, NodeKind.DC_FUSE_BOX, NodeKind.SCB))}  # fmt: skip


@dataclass(frozen=True)
class Sizes:
    main: str
    array: str
    equipment: str
    grid: str
    down: str


@dataclass(frozen=True)
class Switchyard:
    x0: float
    y0: float
    x1: float
    y1: float
    spacing: float

    @property
    def polygon(self) -> Polygon:
        return box(self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class StationGroup:
    """One equipment area (an inverter station, the plant panel area or a BESS area, by `kind`): its ring and the pits and items placed with it."""

    name: str
    ring: tuple[float, float, float, float]
    pits: tuple[str, ...]
    items: tuple[str, ...]
    kind: str = "station"


@dataclass
class EarthingLayout:
    items: list[Item] = field(default_factory=list)
    pits: list[Pit] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)
    tables: dict[str, Table] = field(default_factory=dict)
    switchyard: Switchyard | None = None
    stations: list[StationGroup] = field(default_factory=list)
    rooftop: bool = False
    warnings: list[str] = field(default_factory=list)

    def run(self, role: ConductorRole, size: str, pts, links=(), buried: bool = True, prefix: str = "R") -> Run:
        r = Run(id=f"{prefix}{len(self.runs) + 1}", role=role, size=size, points=tuple(Point(x=round(x, 3), y=round(y, 3)) for x, y in pts), links=tuple(links), buried=buried)
        self.runs.append(r)
        return r

    def pit(self, role: PitRole, at: tuple[float, float], serves=(), on_ground: bool = True) -> Pit:
        n = sum(1 for p in self.pits if p.role is role) + 1
        p = Pit(tag=f"{role.value}{n}", role=role, at=Point(x=round(at[0], 3), y=round(at[1], 3)), serves=tuple(serves), on_ground=on_ground)
        self.pits.append(p)
        return p


def _xy(p: Point) -> tuple[float, float]:
    return (p.x, p.y)


# --- equipment from the network ----------------------------------------------------------------


STATION_KINDS = frozenset({NodeKind.INVERTER, NodeKind.PCS, NodeKind.TRANSFORMER})
Instance = tuple[str, str, NodeKind]


def _instances(project: Project):
    """(instance id, tag, kind, unit) for every node instance; the unit is the outermost group instance the node sits in."""
    net = project.network
    for n in net.nodes:
        chain = net._chain(n.group)
        top = chain[0] if chain else None
        for path in net._paths(n):
            unit = None if top is None else (top.id, path[0] if top.count > 1 else 0)
            yield net._instance_id(n.id, path), n.tag.format(*path), n.kind, unit


def _unit_blocks(project: Project, insts) -> dict[tuple[str, int], int]:
    """Layout block index of each top-level group instance holding station equipment: by the blocks its strings are laid in, else in
    order when the network has exactly one such instance per block (a group repeated once per block, or one group per block)."""
    blocks = project.array_layout.blocks
    units = list(dict.fromkeys(u for _, _, k, u in insts if u is not None and k in STATION_KINDS))
    table_block = {t.id: i for i, b in enumerate(blocks) for t in b.tables}
    string_unit = {iid: u for iid, _, k, u in insts if k is NodeKind.PV_STRING and u is not None}
    votes: dict[tuple[str, int], Counter] = defaultdict(Counter)
    for a in project.array_layout.strings:
        u = string_unit.get(a.string)
        slots = a.slots()
        if u is not None and slots and slots[0][0] in table_block:
            votes[u][table_block[slots[0][0]]] += 1
    out = {u: min(votes[u], key=lambda b: (-votes[u][b], b)) for u in units if votes[u]}
    if not out and len(units) == len(blocks):
        out = {u: i for i, u in enumerate(units)}
    return out


def _acdb_assembly(net) -> frozenset[str]:
    """The AC combiner and what is joined to it without a cable (its breakers, SPD): one assembly at the station. Equipment a cable away
    (the solar meter, the isolator, the main LT panel) stands in the LT room."""
    out = {n.id for n in net.nodes if n.kind is NodeKind.ACDB}
    grown = True
    while grown:
        grown = False
        for e in net.edges:
            if e.cable is None and e.service is not Service.EARTH and (e.src_node in out) != (e.dst_node in out):
                out |= {e.src_node, e.dst_node}
                grown = True
    return frozenset(out)


def _housed_away(net) -> frozenset[str]:
    """Nodes that repeat with a block but are housed in a panel outside it (a block's feeder VCB and CT in the main HT switchboard at the
    switchyard): they stand with that panel, not at the block's station."""
    out = set()
    for n in net.nodes:
        chain = net._chain(n.group)
        if n.housed_in is None or not chain or n.housed_in not in net.group_by_id:
            continue
        housing = net._chain(n.housed_in)
        if chain[0].id not in {g.id for g in housing}:
            out.add(n.id)
    return frozenset(out)


def equipment(project: Project) -> tuple[dict[int, list[Instance]], list[Instance], list[str]]:
    """Bonded equipment instances (id, tag, kind) per layout block index and the plant-level rest, plus assumptions."""
    layout = project.array_layout
    n_blocks = len(layout.blocks) if layout is not None else 0
    per_block: dict[int, list[Instance]] = defaultdict(list)
    plant: list[Instance] = []
    notes: list[str] = []
    if not project.network.nodes:
        return per_block, plant, notes
    insts = list(_instances(project))
    unit_block = _unit_blocks(project, insts) if n_blocks > 1 else {}
    away = _housed_away(project.network)
    placed = layout.equipment_by_node if layout is not None else {}
    for iid, tag, kind, unit in insts:
        if kind not in BONDED_KINDS and not (kind in PLACED_ONLY_KINDS and iid in placed):
            continue
        if iid.split("@")[0] in away:
            plant.append((iid, tag, kind))
        elif unit is not None and n_blocks == 1:
            per_block[0].append((iid, tag, kind))
        elif unit in unit_block:
            per_block[unit_block[unit]].append((iid, tag, kind))
        else:
            plant.append((iid, tag, kind))
    if n_blocks > 1 and not unit_block:
        notes.append(f"the network's block groups do not match the {n_blocks} layout blocks; all equipment is placed at the plant equipment area")
    return per_block, plant, notes


# --- small geometry helpers --------------------------------------------------------------------


def _ring(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def _cluster(centre: tuple[float, float], n: int, pitch: float) -> list[tuple[float, float]]:
    cols = min(n, max(1, math.ceil(math.sqrt(n * 2))))
    rows = math.ceil(n / cols)
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        out.append((centre[0] + (c - (cols - 1) / 2) * pitch, centre[1] + ((rows - 1) / 2 - r) * pitch))
    return out


def _cluster_box(centre: tuple[float, float], n: int) -> Polygon:
    pos = _cluster(centre, max(1, n), STATION_PITCH_M)
    xs, ys = [p[0] for p in pos], [p[1] for p in pos]
    return box(min(xs) - 2.0, min(ys) - 2.0, max(xs) + 2.0, max(ys) + 2.0)


def _free_spot(centre: tuple[float, float], n: int, avoid, site: Polygon | None, reach: float = 40.0) -> tuple[float, float]:
    """The centre nearest `centre` whose ground equipment cluster of `n` items keeps clear of `avoid` (roofs, other stations) and
    inside the plot; `centre` itself when nothing nearby is free."""
    if not _cluster_box(centre, n).intersects(avoid):
        return centre
    near = nearest_points(avoid, SPoint(centre))[0]
    away = (centre[0] - near.x, centre[1] - near.y)
    if math.hypot(*away) < TOL:
        away = (centre[0] - avoid.centroid.x, centre[1] - avoid.centroid.y)
    a0 = math.atan2(away[1], away[0])
    angles = sorted((a0 + math.radians(22.5 * k) for k in range(16)), key=lambda a: abs(math.remainder(a - a0, 2 * math.pi)))
    for d in np.arange(0.5, reach, 0.5):
        for a in angles:
            c = (centre[0] + d * math.cos(a), centre[1] + d * math.sin(a))
            b = _cluster_box(c, n)
            if not b.intersects(avoid) and (site is None or site.contains(b)):
                return c
    return centre


def _ring_points(ring: list[tuple[float, float]], k: int) -> list[tuple[float, float]]:
    line = LineString(ring)
    return [tuple(line.interpolate((i + 0.5) / k, normalized=True).coords[0]) for i in range(k)]


def _two_connections(p: tuple[float, float], ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Nearest points on the two ring sides closest to `p`."""
    sides = [LineString([a, b]) for a, b in zip(ring, ring[1:])]
    sp = SPoint(p)
    ranked = sorted(sides, key=lambda s: s.distance(sp))[:2]
    return [tuple(nearest_points(s, sp)[0].coords[0]) for s in ranked]


def _station_area(lay: EarthingLayout, centre: tuple[float, float], items: list[tuple[str, str, NodeKind]], label: str, sizes: Sizes,
                  name: str | None = None, avoid: Polygon | None = None, roof: Polygon | None = None, site: Polygon | None = None,
                  electrode_m: float = 3.0, area: str = "station") -> None:  # fmt: skip
    """Equipment cluster with a local ring and each item bonded to the ring by its connections. On the ground its pits stand on or
    beside the ring (off any `avoid` area, e.g. a roof the ring overlaps). On a `roof` the ring is bonded by down conductors to pits
    on the ground at the nearest building edge."""
    items = sorted(items, key=lambda t: (KIND_ORDER.get(t[2], 99), t[0]))
    on_roof = roof is not None
    pos = _cluster(centre, max(1, len(items)), STATION_PITCH_M / 2 if on_roof else STATION_PITCH_M)
    if on_roof:
        pos = _inside_roof(pos, roof, centre)
    xs, ys = [p[0] for p in pos], [p[1] for p in pos]
    m = 1.0 if on_roof else 2.0
    ring = _ring(min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
    if on_roof:
        clipped = box(*ring[0], *ring[2]).intersection(roof)
        if isinstance(clipped, Polygon) and not clipped.is_empty:
            ring = [(x, y) for x, y in clipped.exterior.coords]
    lay.run(ConductorRole.MAIN, sizes.main, ring, (label,), buried=not on_roof)
    roles: list[tuple[PitRole, str]] = []
    for (iid, tag, kind), p in zip(items, pos):
        lay.items.append(Item(id=iid, kind=kind.value, tag=tag, at=Point(x=round(p[0], 3), y=round(p[1], 3)), connections=CONNECTIONS[kind], on_roof=on_roof))
        for q in _two_connections(p, ring)[: CONNECTIONS[kind]]:
            lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, q], (iid,), buried=not on_roof)
        roles += [(r, iid) for r in PITS.get(kind, ())]
    if not any(r is PitRole.EP for r, _ in roles):
        roles = [(PitRole.EP, label)] * GROUP_PITS + roles
    if on_roof:
        pts = _roof_edge_pits(lay, roof, centre, len(roles), electrode_m, site)
        ring_line = LineString(ring)
        for k, ((role, serves), q) in enumerate(zip(roles, pts)):
            if k < 2:
                edge = nearest_points(roof.exterior, SPoint(q))[0]
                top = nearest_points(ring_line, edge)[0]
                lay.run(ConductorRole.DOWN, sizes.down, [(top.x, top.y), (edge.x, edge.y), q], (label,), buried=False)
            else:
                lay.run(ConductorRole.MAIN, sizes.main, [pts[k - 1], q])
        pits = [lay.pit(role, q, (serves,)) for (role, serves), q in zip(roles, pts)]
    else:
        pts = _ring_points(ring, len(roles))
        if avoid is not None and any(avoid.contains(SPoint(q)) for q in pts):
            free = [q for q in _ring_points(ring, 8 * len(roles)) if not avoid.contains(SPoint(q))]
            if len(free) >= len(roles):
                pts = [free[round(i * len(free) / len(roles))] for i in range(len(roles))]
        pits = [lay.pit(role, q, (serves,)) for (role, serves), q in zip(roles, pts)]
    bounds = LineString(ring).bounds
    lay.stations.append(StationGroup(name or label.upper(), bounds, tuple(p.tag for p in pits), tuple(i[0] for i in items), kind=area))


def _inside_roof(pos: list[tuple[float, float]], roof: Polygon, station: tuple[float, float], clear: float = 0.4) -> list[tuple[float, float]]:
    """Slides a rooftop equipment cluster inward from the station point, the cluster's edge-side midpoint, until every item is `clear` m
    inside the roof edge."""
    edge = nearest_points(roof.exterior, SPoint(station))[0]
    vx, vy = station[0] - edge.x, station[1] - edge.y
    if math.hypot(vx, vy) < TOL:
        vx, vy = roof.centroid.x - edge.x, roof.centroid.y - edge.y
    ln = math.hypot(vx, vy) or 1.0
    vx, vy = vx / ln, vy / ln
    inner = roof.buffer(-clear)
    if inner.is_empty:
        return pos
    for k in range(41):
        moved = [(x + vx * 0.25 * k, y + vy * 0.25 * k) for x, y in pos]
        if all(inner.contains(SPoint(q)) for q in moved):
            return moved
    return pos


def _roof_edge_pits(lay: EarthingLayout, roof: Polygon, toward: tuple[float, float], n: int, electrode_m: float, site: Polygon | None) -> list[tuple[float, float]]:
    """`n` ground points 1.5 m outside the roof edge nearest `toward`, in a line along the edge one pit spacing apart and inside the plot."""
    edge, g = _outside_roof(roof, toward, site)
    nx, ny = g[0] - edge[0], g[1] - edge[1]
    ln = math.hypot(nx, ny) or 1.0
    tx, ty = -ny / ln, nx / ln
    step = 2 * electrode_m
    for sign in (1.0, -1.0):
        pts = [(g[0] + sign * tx * (k - (n - 1) / 2) * step, g[1] + sign * ty * (k - (n - 1) / 2) * step) for k in range(n)]
        if site is None or all(site.contains(SPoint(q)) for q in pts):
            return pts
    shift = [(g[0] + tx * k * step, g[1] + ty * k * step) for k in range(n)]
    if site is None or all(site.contains(SPoint(q)) for q in shift):
        return shift
    lay.warnings.append(f"the {n} pits of the station at ({toward[0]:.1f}, {toward[1]:.1f}) do not all fit {step:g} m apart at the building edge inside the plot")
    return pts


def _rows(tables: list[Table]) -> list[list[Table]]:
    _, (vx, vy) = frame(tables[0].azimuth_deg)
    by_v: dict[float, list[Table]] = defaultdict(list)
    for t in tables:
        by_v[round(t.origin.x * vx + t.origin.y * vy, 1)].append(t)
    return [by_v[k] for k in sorted(by_v)]


def _row_runs(lay: EarthingLayout, tables: list[Table], size: str, avoid, buried: bool) -> list[tuple[float, float, float, Run]]:
    """One conductor per run of adjacent tables along a row, through every table's centre line; (v, u0, u1, run) per segment."""
    if not tables:
        return []
    (ux, uy), (vx, vy) = frame(tables[0].azimuth_deg)
    segs: list[tuple[float, float, float, Run]] = []
    for row in _rows(tables):
        row.sort(key=lambda t: t.origin.x * ux + t.origin.y * uy)
        groups: list[list[Table]] = [[row[0]]]
        for a, b in zip(row, row[1:]):
            ua = a.origin.x * ux + a.origin.y * uy + plan_length(a)
            ub = b.origin.x * ux + b.origin.y * uy
            gap = LineString([to_site(a.origin, a.azimuth_deg, plan_length(a), plan_depth(a) / 2), to_site(b.origin, b.azimuth_deg, 0, plan_depth(b) / 2)])
            if ub - ua > ROW_GAP_SPLIT_M or (avoid is not None and gap.intersects(avoid)):
                groups.append([b])
            else:
                groups[-1].append(b)
        for g in groups:
            first, last = g[0], g[-1]
            p0 = to_site(first.origin, first.azimuth_deg, 0.3, plan_depth(first) / 2)
            p1 = to_site(last.origin, last.azimuth_deg, plan_length(last) - 0.3, plan_depth(last) / 2)
            r = lay.run(ConductorRole.ARRAY, size, [p0, p1], tuple(t.id for t in g), buried=buried)
            v = p0[0] * vx + p0[1] * vy
            segs.append((v, p0[0] * ux + p0[1] * uy, p1[0] * ux + p1[1] * uy, r))
    return segs


def _risers(lay: EarthingLayout, segs: list[tuple[float, float, float, Run]], azimuth: float, size: str, max_step: float, buried: bool) -> None:
    """Link each row segment's ends to the segment in the next row that spans the same position."""
    (ux, uy), (vx, vy) = frame(azimuth)
    vs = sorted({round(s[0], 2) for s in segs})
    nxt = {a: b for a, b in zip(vs, vs[1:]) if b - a <= max_step}
    by_v = defaultdict(list)
    for s in segs:
        by_v[round(s[0], 2)].append(s)
    for v, u0, u1, _ in segs:
        nv = nxt.get(round(v, 2))
        if nv is None:
            continue
        for u in (u0, u1):
            for v2, a0, a1, _ in by_v[nv]:
                if a0 - 0.5 <= u <= a1 + 0.5:
                    uu = min(max(u, a0), a1)
                    lay.run(ConductorRole.MAIN, size, [(uu * ux + v * vx, uu * uy + v * vy), (uu * ux + v2 * vx, uu * uy + v2 * vy)], buried=buried)
                    break


def _scb_positions(block: LayoutBlock, n: int) -> list[tuple[float, float]]:
    tables = sorted(block.tables, key=lambda t: (round(t.origin.y, 1), t.origin.x))
    if not tables or n <= 0:
        return []
    chunks = np.array_split(np.arange(len(tables)), n)
    out = []
    for c in chunks:
        if not len(c):
            continue
        t = tables[int(c[len(c) // 2])]
        out.append(to_site(t.origin, t.azimuth_deg, plan_length(t) / 2, plan_depth(t) + 0.8))
    return out


# --- switchyard --------------------------------------------------------------------------------




def _switchyard(lay: EarthingLayout, rect: tuple[float, float, float, float], spacing: float, items, sizes: Sizes, perimeter_pits: int) -> None:
    x0, y0, x1, y1 = rect
    nx = max(2, round((x1 - x0) / spacing) + 1)
    ny = max(2, round((y1 - y0) / spacing) + 1)
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    for x in xs:
        lay.run(ConductorRole.GRID, sizes.grid, [(x, y0), (x, y1)], ("switchyard",))
    for y in ys:
        lay.run(ConductorRole.GRID, sizes.grid, [(x0, y), (x1, y)], ("switchyard",))
    lay.switchyard = Switchyard(x0, y0, x1, y1, float(max(xs[1] - xs[0], ys[1] - ys[0])))
    items = sorted(items, key=lambda t: (KIND_ORDER.get(t[2], 99), t[0]))
    cells = [((a + b) / 2, (c + d) / 2) for c, d in zip(ys[::-1][:-1], ys[::-1][1:]) for a, b in zip(xs[:-1], xs[1:])]
    if len(items) > len(cells):
        lay.warnings.append(f"{len(items)} switchyard items share {len(cells)} grid meshes")
    roles: list[tuple[PitRole, str]] = []
    pick = np.linspace(0, len(cells) - 1, len(items)).round().astype(int) if len(items) <= len(cells) else np.arange(len(items)) % len(cells)
    for i, (iid, tag, kind) in enumerate(items):
        cx, cy = cells[int(pick[i])]
        k = i // len(cells)
        p = (cx + 0.6 * k, cy - 0.6 * k)
        lay.items.append(Item(id=iid, kind=kind.value, tag=tag, at=Point(x=round(p[0], 3), y=round(p[1], 3)), connections=CONNECTIONS[kind]))
        ix = int(np.argmin(np.abs(xs - p[0])))
        iy = int(np.argmin(np.abs(ys - p[1])))
        ends = [(float(xs[ix]), p[1]), (p[0], float(ys[iy]))][: CONNECTIONS[kind]]
        for q in ends:
            lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, q], (iid,))
        roles += [(r, iid) for r in PITS.get(kind, ())]
    ring = _ring(x0, y0, x1, y1)
    perimeter = [(PitRole.EP, "switchyard")] * perimeter_pits
    for (role, serves), q in zip(perimeter + roles, _ring_points(ring, perimeter_pits + len(roles))):
        lay.pit(role, q, (serves,))


# --- connectivity ------------------------------------------------------------------------------


def _union_find(n: int):
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    return parent, find


def components(lay: EarthingLayout) -> list[list[int]]:
    """Groups of runs that touch each other (indices into lay.runs)."""
    lines = [LineString([_xy(p) for p in r.points]) for r in lay.runs]
    tree = STRtree(lines)
    parent, find = _union_find(len(lines))
    for i, g in enumerate(lines):
        for j in tree.query(g.buffer(TOL)):
            j = int(j)
            if j != i and lines[j].distance(g) <= TOL:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(lines)):
        groups[find(i)].append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def connect_all(lay: EarthingLayout, size: str) -> None:
    """Join separate parts with the shortest links (Kruskal over the parts' nearest points)."""
    comps = components(lay)
    if len(comps) < 2:
        return
    geoms = [MultiLineString([[_xy(p) for p in lay.runs[i].points] for i in c]) for c in comps]
    buried = [any(lay.runs[i].buried for i in c) for c in comps]
    cents = np.array([[g.centroid.x, g.centroid.y] for g in geoms])
    pairs = []
    k = min(len(comps) - 1, 8)
    for i in range(len(comps)):
        d = np.hypot(*(cents - cents[i]).T)
        near = set(np.argsort(d)[1 : k + 1].tolist())
        for j in near:
            if j > i or i not in set(np.argsort(np.hypot(*(cents - cents[j]).T))[1 : k + 1].tolist()):
                a, b = nearest_points(geoms[i], geoms[j])
                pairs.append((a.distance(b), min(i, j), max(i, j), (a.x, a.y), (b.x, b.y)))
    pairs.sort()
    parent, find = _union_find(len(comps))
    for dist, i, j, a, b in pairs:
        if find(i) != find(j):
            parent[find(i)] = find(j)
            lay.run(ConductorRole.MAIN, size, _orthogonal(a, b), buried=buried[i] or buried[j], prefix="L")
    if len({find(i) for i in range(len(comps))}) > 1:
        roots = sorted({find(i) for i in range(len(comps))})
        for r in roots[1:]:
            a, b = nearest_points(geoms[r], geoms[roots[0]])
            lay.run(ConductorRole.MAIN, size, _orthogonal((a.x, a.y), (b.x, b.y)), prefix="L")


def _orthogonal(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
    """A link drawn along the site axes (one bend), as conductors are laid; straight when already nearly axis-aligned."""
    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
    if min(dx, dy) < 0.5:
        return [a, b]
    return [a, (b[0], a[1]), b]


def bonding(lay: EarthingLayout) -> dict[str, int]:
    """Earth connections that reach a pit, per item and table: equipment counts conductor ends at its position, tables count row
    conductors crossing their footprint. 0 means not bonded to any pit."""
    comps = components(lay)
    comp_of = {i: k for k, c in enumerate(comps) for i in c}
    lines = [LineString([_xy(p) for p in r.points]) for r in lay.runs]
    tree = STRtree(lines)
    earthed = set()
    for p in lay.pits:
        sp = SPoint(_xy(p.at))
        for j in tree.query(sp.buffer(TOL)):
            if lines[int(j)].distance(sp) <= TOL:
                earthed.add(comp_of[int(j)])
    out: dict[str, int] = {}
    for it in lay.items:
        sp = SPoint(_xy(it.at))
        n = 0
        for j in tree.query(sp.buffer(TOL)):
            j = int(j)
            ends = (lines[j].coords[0], lines[j].coords[-1])
            if comp_of[j] in earthed and any(math.dist(e, (it.at.x, it.at.y)) <= TOL for e in ends):
                n += 1
        out[it.id] = n
    for tid, t in lay.tables.items():
        poly = Polygon(table_corners(t))
        out[tid] = sum(1 for j in tree.query(poly) if comp_of[int(j)] in earthed and lines[int(j)].intersects(poly))
    return out


# --- the layout --------------------------------------------------------------------------------


def _farthest(cands: np.ndarray, fixed: np.ndarray, k: int, min_spacing: float) -> list[tuple[float, float]]:
    if k <= 0 or not len(cands):
        return []
    d = np.full(len(cands), np.inf) if not len(fixed) else np.min(np.hypot(cands[:, None, 0] - fixed[None, :, 0], cands[:, None, 1] - fixed[None, :, 1]), axis=1)
    out = []
    for _ in range(k):
        i = int(np.argmax(d))
        if d[i] < min_spacing:
            break
        out.append((float(cands[i, 0]), float(cands[i, 1])))
        d = np.minimum(d, np.hypot(cands[:, 0] - cands[i, 0], cands[:, 1] - cands[i, 1]))
    return out


def _along(runs: list[Run], step: float) -> np.ndarray:
    pts = []
    for r in runs:
        line = LineString([_xy(p) for p in r.points])
        n = max(1, int(line.length // step))
        pts += [line.interpolate(i / n, normalized=True).coords[0] for i in range(n + 1)]
    return np.asarray(pts, dtype=float).reshape(-1, 2)


def _outside_roof(poly: Polygon, toward: tuple[float, float], site: Polygon | None, offset: float = 1.5) -> tuple[tuple[float, float], tuple[float, float]]:
    """(point on the roof edge nearest `toward`, a ground point `offset` m outside it)."""
    edge = nearest_points(poly.exterior, SPoint(toward))[0]
    c = poly.centroid
    dx, dy = edge.x - c.x, edge.y - c.y
    sides = [LineString([a, b]) for a, b in zip(poly.exterior.coords, poly.exterior.coords[1:])]
    side = min(sides, key=lambda s: s.distance(edge))
    (ax, ay), (bx, by) = side.coords
    nx, ny = by - ay, -(bx - ax)
    ln = math.hypot(nx, ny) or 1.0
    nx, ny = nx / ln, ny / ln
    if nx * dx + ny * dy < 0:
        nx, ny = -nx, -ny
    g = (edge.x + nx * offset, edge.y + ny * offset)
    if site is not None and not site.contains(SPoint(g)):
        g = (edge.x - nx * offset, edge.y - ny * offset)
    return (edge.x, edge.y), g


def build_layout(project: Project, sizes: Sizes, array_pits: int, tables_per_pit: int, electrode_m: float, lps: LpsDesign | None,
                 switchyard: tuple[tuple[float, float, float, float] | None, float] | None, perimeter_pits: int = 8,
                 ring: bool = False) -> EarthingLayout:  # fmt: skip
    """`ring` lays a buried ring earth conductor around each roof carrying an array (rooftop projects); `array_pits` extra pits go on
    the buried conductors, on rooftops along those rings at the building perimeter."""
    layout = project.array_layout
    lay = EarthingLayout(rooftop=is_rooftop(project))
    per_block, plant, notes = equipment(project)
    lay.warnings += notes
    site = boundary(project)
    avoid = blocked(project)
    avoid = None if avoid.is_empty else avoid
    tables = list(layout.table_by_id.values()) if layout is not None else []
    lay.tables = {t.id: t for t in tables}
    blocks = layout.blocks if layout is not None else ()
    roof_polys = roofs(project) if lay.rooftop else []
    roof_union = unary_union([q for _, q in roof_polys]) if roof_polys else None
    pitch = _row_pitch(tables)
    array_runs: list[Run] = []
    if lay.rooftop:
        for rid, poly in roof_polys:
            on = [t for t in tables if poly.buffer(0.5).contains(Polygon(table_corners(t)).centroid)]
            if not on:
                continue
            segs = _row_runs(lay, on, sizes.array, None, buried=False)
            array_runs += [s[3] for s in segs]
            _risers(lay, segs, on[0].azimuth_deg, sizes.main, 1.6 * (pitch or 10), buried=False)
            envelope = unary_union([Polygon(table_corners(t)) for t in on])
            x0, y0, x1, y1 = envelope.bounds
            for corner in ((x0, y0), (x1, y1)):
                edge, g = _outside_roof(poly, corner, site)
                start = nearest_points(MultiLineString([[_xy(p) for p in r.points] for r in array_runs if r.links and r.links[0] in {t.id for t in on}]), SPoint(edge))[0]
                lay.run(ConductorRole.DOWN, sizes.down, [(start.x, start.y), edge], (rid,), buried=False)
                lay.run(ConductorRole.DOWN, sizes.down, [edge, g], (rid,), buried=False)
                lay.pit(PitRole.EP, g, (f"{rid} array",))
            if ring:
                _perimeter_ring(lay, poly, site, sizes)
    else:
        segs = _row_runs(lay, tables, sizes.array, avoid, buried=True)
        array_runs += [s[3] for s in segs]
        if tables:
            _risers(lay, segs, tables[0].azimuth_deg, sizes.main, 1.6 * (pitch or 10), buried=True)
    placed = layout.equipment_by_node if layout is not None else {}
    field_items = [it for it in plant if it[0] in placed] + [it for items in per_block.values() for it in items if it[0] in placed]
    in_field = {i for i, items in per_block.items() if any(it[0] in placed for it in items)}
    plant = [it for it in plant if it[0] not in placed]
    per_block = {i: [it for it in items if it[0] not in placed] for i, items in per_block.items()}
    yard_items = [it for it in plant if it[2] in MV_KINDS] if switchyard is not None and switchyard[0] is not None else []
    panel_items = [it for it in plant if it not in yard_items]
    host = next((i for i, b in enumerate(blocks) if b.station is not None), None) if lay.rooftop else None
    if host is not None and layout.switchyard is not None:
        at_acdb = _acdb_assembly(project.network)
        hosted = [it for it in panel_items if it[0].split("@")[0] in at_acdb]
        panel_items = [it for it in panel_items if it not in hosted]
    else:
        hosted = panel_items if host is not None else []
        panel_items = [] if hosted else panel_items
    for i, b in enumerate(blocks):
        items = per_block.get(i, [])
        if b.station is None:
            if items:
                lay.warnings.append(f"block {b.id} has no station; its {len(items)} equipment items are not placed")
            continue
        scbs = [it for it in items if it[2] in FIELD_BOXES]
        rest = [it for it in items if it[2] not in FIELD_BOXES] + (hosted if i == host else [])
        if not rest:
            if i in in_field:
                continue
            rest = [(f"{b.id}~station", f"{b.name} STATION", NodeKind.INVERTER)]
        at = _xy(b.station)
        roof = next((p for _, p in roof_polys if p.contains(SPoint(*at))), None)
        if roof_union is not None and roof is None and _cluster_box(at, len(rest)).intersects(roof_union):
            lay.warnings.append(f"the ground station of block {b.id} at ({at[0]:.1f}, {at[1]:.1f}) stands within 2 m of a roof; its earthing is drawn at the station")
        _station_area(lay, at, rest, b.id, sizes, name=f"{b.name} STATION", avoid=roof_union, roof=roof, site=site, electrode_m=electrode_m)
        for (iid, tag, kind), p in zip(scbs, _scb_positions(b, len(scbs))):
            lay.items.append(Item(id=iid, kind=kind.value, tag=tag, at=Point(x=round(p[0], 3), y=round(p[1], 3)), connections=CONNECTIONS[kind], on_roof=lay.rooftop))
            near = sorted(array_runs, key=lambda r: LineString([_xy(q) for q in r.points]).distance(SPoint(p)))[: CONNECTIONS[kind]]
            for r in near:
                q = nearest_points(LineString([_xy(x) for x in r.points]), SPoint(p))[0]
                lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, (q.x, q.y)], (iid,), buried=not lay.rooftop)
            if not lay.rooftop:
                lay.pit(PitRole.EP, (p[0] + 1.0, p[1] + 0.6), (iid,))
                lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, (p[0] + 1.0, p[1] + 0.6)], (iid,))
    bess_items = [it for it in field_items if it[2] is not NodeKind.INVERTER and it[2] not in LT_PANEL_KINDS]
    for iid, tag, kind in field_items:
        if kind is NodeKind.INVERTER or kind in LT_PANEL_KINDS:
            _field_item(lay, iid, tag, kind, _xy(placed[iid].at), array_runs, sizes)
    if yard_items:
        _switchyard(lay, switchyard[0], switchyard[1], yard_items, sizes, perimeter_pits)
    yard_rect = switchyard[0] if yard_items else None
    for k, group in enumerate(_bess_groups(bess_items, placed), 1):
        _bess_area(lay, group, placed, sizes, f"BESS AREA {k}", yard_rect, electrode_m)
    if panel_items:
        at = _panel_area(project, lay, site, roof_polys)
        if roof_union is not None:
            at = _free_spot(at, len(panel_items), unary_union([roof_union, *(box(*g.ring).buffer(2.0) for g in lay.stations)]), site)
        _station_area(lay, at, panel_items, "plant", sizes, name="PLANT EQUIPMENT AREA", avoid=roof_union, area="plant")
    if lps is not None:
        _lps(lay, lps, sizes, site, dict(roof_polys), unary_union([Polygon(table_corners(t)) for t in tables]) if tables else None)
    existing = np.asarray([_xy(p.at) for p in lay.pits], dtype=float).reshape(-1, 2)
    buried_array = [r for r in array_runs if r.buried]
    want = (math.ceil(len(tables) / tables_per_pit) + array_pits) if buried_array else 0
    for q in _farthest(_along(buried_array, max(2.0, electrode_m)), existing, want, 2 * electrode_m):
        lay.pit(PitRole.EP, q, ("array",))
    if lay.rooftop and array_pits > 0:
        rings = [r for r in lay.runs if r.links == ("perimeter ring",)]
        base = _along(rings, 1.0) if rings else _along([r for r in lay.runs if r.buried], 1.0)
        extra = _farthest(base, existing, array_pits, 2 * electrode_m)
        for q in extra:
            lay.pit(PitRole.EP, q, ("additional",))
        if len(extra) < array_pits:
            lay.warnings.append(f"only {len(extra)} of {array_pits} additional pits fit {2 * electrode_m:g} m apart at the building perimeter inside the plot")
    _pit_spurs(lay, sizes)
    connect_all(lay, sizes.main)
    return lay


def _field_item(lay: EarthingLayout, iid: str, tag: str, kind: NodeKind, p: tuple[float, float], array_runs: list[Run], sizes: Sizes) -> None:
    """Equipment the layout places in the field (string inverters at their rows, BESS units): bonded where it stands, to the nearest array
    conductors, and on the ground to its own pit beside it."""
    on_roof = lay.rooftop and any(r.links and not r.buried for r in array_runs)
    lay.items.append(Item(id=iid, kind=kind.value, tag=tag, at=Point(x=round(p[0], 3), y=round(p[1], 3)), connections=CONNECTIONS[kind], on_roof=on_roof, placed=True))
    near = sorted(array_runs, key=lambda r: LineString([_xy(q) for q in r.points]).distance(SPoint(p)))[: CONNECTIONS[kind]]
    for r in near:
        q = nearest_points(LineString([_xy(x) for x in r.points]), SPoint(p))[0]
        if math.dist(p, (q.x, q.y)) > TOL:
            lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, (q.x, q.y)], (iid,), buried=not on_roof)
    if not on_roof:
        pit = (p[0] + 1.0, p[1] + 0.6)
        lay.pit(PitRole.EP, pit, (iid,))
        lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, pit], (iid,))


def _bess_groups(items: list[Instance], placed) -> list[list[Instance]]:
    """BESS units that stand within BESS_LINK_M of each other, one group per BESS area, in stored order."""
    groups: list[list[Instance]] = []
    for it in sorted(items, key=lambda t: (placed[t[0]].at.x, placed[t[0]].at.y, t[0])):
        at = _xy(placed[it[0]].at)
        near = [g for g in groups if any(math.dist(at, _xy(placed[o[0]].at)) <= BESS_LINK_M for o in g)]
        merged = [o for g in near for o in g] + [it]
        groups = [g for g in groups if g not in near] + [merged]
    return groups


def _bess_area(lay: EarthingLayout, group: list[Instance], placed, sizes: Sizes, name: str, yard: tuple[float, float, float, float] | None, electrode_m: float) -> None:
    """Earthing of a BESS area, outside the switchyard mat: a buried ring round its units, each unit bonded to the ring at its stored position by
    its connections, the area's pits on the ring, and the ring bonded to the switchyard grid by the shortest link."""
    group = sorted(group, key=lambda t: (KIND_ORDER.get(t[2], 99), t[0]))
    pos = {iid: _xy(placed[iid].at) for iid, _, _ in group}
    xs, ys = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
    ring = _ring(min(xs) - 3.0, min(ys) - 3.0, max(xs) + 3.0, max(ys) + 3.0)
    label = name.lower().replace(" ", "-")
    lay.run(ConductorRole.MAIN, sizes.main, ring, (label,))
    roles: list[tuple[PitRole, str]] = [(PitRole.EP, label)] * GROUP_PITS
    for iid, tag, kind in group:
        p = pos[iid]
        lay.items.append(Item(id=iid, kind=kind.value, tag=tag, at=Point(x=round(p[0], 3), y=round(p[1], 3)), connections=CONNECTIONS[kind], placed=True))
        for q in _two_connections(p, ring)[: CONNECTIONS[kind]]:
            lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, q], (iid,))
        roles += [(r, iid) for r in PITS.get(kind, ())]
        if yard is not None and yard[0] <= p[0] <= yard[2] and yard[1] <= p[1] <= yard[3]:
            lay.warnings.append(f"{tag} stands inside the switchyard earth mat; BESS units belong outside it, bonded to it")
    pits = [lay.pit(role, q, (serves,)) for (role, serves), q in zip(roles, _ring_points(ring, len(roles)))]
    if yard is not None:
        a, b = nearest_points(LineString(ring), box(*yard))
        if a.distance(b) > TOL:
            lay.run(ConductorRole.MAIN, sizes.main, _orthogonal((a.x, a.y), (b.x, b.y)), (label, "switchyard"))
    lay.stations.append(StationGroup(name, LineString(ring).bounds, tuple(p.tag for p in pits), tuple(i[0] for i in group), kind="bess"))


def _perimeter_ring(lay: EarthingLayout, poly: Polygon, site: Polygon | None, sizes: Sizes, offset: float = 1.5) -> None:
    """A buried ring earth conductor `offset` m outside the building, kept inside the plot."""
    ring = poly.buffer(offset, join_style="mitre").exterior
    path = ring if site is None else ring.intersection(site.buffer(-0.5))
    for part in getattr(path, "geoms", [path]):
        if isinstance(part, LineString) and part.length > 1.0:
            lay.run(ConductorRole.MAIN, sizes.main, list(part.coords), ("perimeter ring",))


def _pit_spurs(lay: EarthingLayout, sizes: Sizes) -> None:
    """Pits not on a conductor get a short spur to the nearest one."""
    lines = [LineString([_xy(p) for p in r.points]) for r in lay.runs]
    if not lines:
        return
    tree = STRtree(lines)
    for p in lay.pits:
        sp = SPoint(_xy(p.at))
        j = int(tree.nearest(sp))
        if lines[j].distance(sp) > TOL:
            q = nearest_points(lines[j], sp)[0]
            lay.run(ConductorRole.MAIN if p.on_ground else ConductorRole.DOWN, sizes.main, _orthogonal(_xy(p.at), (q.x, q.y)))


def _row_pitch(tables: list[Table]) -> float | None:
    if len(tables) < 2:
        return None
    _, (vx, vy) = frame(tables[0].azimuth_deg)
    vs = sorted({round(t.origin.x * vx + t.origin.y * vy, 2) for t in tables})
    steps = [b - a for a, b in zip(vs, vs[1:]) if b - a > 0.5]
    return float(np.median(steps)) if steps else None


def _panel_area(project: Project, lay: EarthingLayout, site: Polygon | None, roof_polys) -> tuple[float, float]:
    """Plant-level panels: at the layout's switchyard location (main LT panel) on rooftop projects or when there is no switchyard grid, else at the
    free spot nearest it clear of the grid, tables and roads; without a stored location, beside the first roof edge nearest the station on rooftop
    projects, else beside the station nearest the site centroid."""
    layout = project.array_layout
    if layout is not None and layout.switchyard is not None:
        at = _xy(layout.switchyard.at)
        geo = project.site.geometry
        if lay.rooftop or geo is None:
            return at
        taken = [Polygon(table_corners(t)) for t in layout.table_by_id.values()]
        if lay.switchyard is not None:
            y = lay.switchyard
            taken.append(box(y.x0, y.y0, y.x1, y.y1))
        spot = free_spot(geo, at, unary_union(taken))
        return _xy(spot) if spot is not None else at
    stations = [_xy(b.station) for b in layout.blocks if b.station is not None] if layout is not None else []
    if lay.rooftop and roof_polys:
        ref = stations[0] if stations else roof_polys[0][1].centroid.coords[0]
        poly = min((p for _, p in roof_polys), key=lambda p: p.distance(SPoint(ref)))
        _, g = _outside_roof(poly, ref, site, offset=6.0)
        return g
    if stations:
        c = site.centroid if site is not None else SPoint(stations[0])
        s = min(stations, key=lambda p: math.dist(p, (c.x, c.y)))
        return (s[0] + 12.0, s[1])
    return (site.centroid.x, site.centroid.y) if site is not None else (0.0, 0.0)


def _lps(lay: EarthingLayout, lps: LpsDesign, sizes: Sizes, site: Polygon | None, roof_by_id: dict[str, Polygon], tables: BaseGeometry | None = None) -> None:
    for t in lps.terminals:
        at = _xy(t.at)
        lay.items.append(Item(id=t.tag, kind="ese" if lps.method == "ese" else "air_terminal", tag=t.tag, at=t.at, connections=1 if not t.on_roof else 2, on_roof=t.on_roof))
        if t.on_roof and t.roof in roof_by_id:
            poly = roof_by_id[t.roof]
            sides = sorted((LineString([a, b]) for a, b in zip(poly.exterior.coords, poly.exterior.coords[1:])), key=lambda s: s.distance(SPoint(at)))[:2]
            for s in sides:
                e = nearest_points(s, SPoint(at))[0]
                edge, g = _outside_roof(poly, (e.x, e.y), site)
                lay.run(ConductorRole.DOWN, sizes.down, [at, edge], (t.tag,), buried=False, prefix="D")
                lay.run(ConductorRole.DOWN, sizes.down, [edge, g], (t.tag,), buried=False, prefix="D")
                lay.pit(PitRole.LE, g, (t.tag,))
        else:
            g = _mast_pit(at, site, tables)
            lay.run(ConductorRole.DOWN, sizes.down, [at, g], (t.tag,), prefix="D")
            lay.pit(PitRole.LE, g, (t.tag,))


def _mast_pit(at: tuple[float, float], site: Polygon | None, tables: BaseGeometry | None, offset: float = 1.5) -> tuple[float, float]:
    """The lightning earth pit beside a ground mast: `offset` m east if that is off the tables and inside the plot, else the first of west, north
    and south, else a shorter offset the same way."""
    for d in (offset, 0.75 * offset, 0.5 * offset):
        for dx, dy in ((d, 0.0), (-d, 0.0), (0.0, d), (0.0, -d)):
            g = (at[0] + dx, at[1] + dy)
            if (tables is None or not tables.intersects(SPoint(g))) and (site is None or site.contains(SPoint(g))):
                return g
    return (at[0] + offset, at[1])

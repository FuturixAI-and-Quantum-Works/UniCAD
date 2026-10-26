"""Earthing layout geometry: where the pits go and how conductors bond every table row, station, panel, switchyard and lightning
terminal into one earth network. Pure function of the project, the design basis and the chosen conductor sizes.

Locations are indicative, as on the reference earthing layouts: stations get a local ring with the equipment bonded to it twice,
table rows get a conductor along their centre line with risers between neighbouring rows, and separate parts are joined by the
shortest links (a minimum spanning tree over the parts).
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.geometry import Point as SPoint
from shapely.ops import nearest_points, unary_union

from unicad.arrays.geometry import frame, plan_depth, plan_length, table_corners, to_site
from unicad.domain.layout import LayoutBlock, Point, Table
from unicad.domain.network import NodeKind
from unicad.domain.project import Project
from unicad.domain.site import shape_of
from unicad.earthing.model import ConductorRole, Item, Pit, PitRole, Run
from unicad.lps.design import LpsDesign, blocked, boundary, is_rooftop, roofs

ROW_GAP_SPLIT_M = 8.0
STATION_PITCH_M = 3.0
TOL = 1e-3

# Distinct earth connections per item (CIL/Refex note: every equipment and structure by two distinct connections).
CONNECTIONS: dict[NodeKind, int] = {
    NodeKind.INVERTER: 2, NodeKind.PCS: 2, NodeKind.BATTERY: 2, NodeKind.TRANSFORMER: 2, NodeKind.AUX_TRANSFORMER: 2,
    NodeKind.ACDB: 2, NodeKind.LT_PANEL: 2, NodeKind.HT_PANEL: 2, NodeKind.RMU: 2, NodeKind.VCB: 2, NodeKind.DG: 2,
    NodeKind.SCB: 2, NodeKind.DC_FUSE_BOX: 1, NodeKind.LIGHTNING_ARRESTER: 1, NodeKind.PT: 1, NodeKind.CT: 1,
}  # fmt: skip
BONDED_KINDS = frozenset(CONNECTIONS)
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


@dataclass
class EarthingLayout:
    items: list[Item] = field(default_factory=list)
    pits: list[Pit] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)
    tables: dict[str, Table] = field(default_factory=dict)
    switchyard: Switchyard | None = None
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


def _block_group(project: Project, n_blocks: int) -> str | None:
    net = project.network
    for g in net.groups:
        if g.within is None and g.count == n_blocks and n_blocks > 1:
            return g.id
    return None


def equipment(project: Project) -> tuple[dict[int, list[tuple[str, str, NodeKind]]], list[tuple[str, str, NodeKind]], list[str]]:
    """Bonded equipment instances (id, tag, kind) per layout block index and the plant-level rest, plus assumptions."""
    net = project.network
    layout = project.array_layout
    n_blocks = len(layout.blocks) if layout is not None else 0
    gid = _block_group(project, n_blocks)
    per_block: dict[int, list[tuple[str, str, NodeKind]]] = defaultdict(list)
    plant: list[tuple[str, str, NodeKind]] = []
    notes: list[str] = []
    if not net.nodes:
        return per_block, plant, notes
    expanded = net.expand()
    for n in expanded.nodes:
        if n.kind not in BONDED_KINDS:
            continue
        tpl = net.node(n.template_id)
        chain = [g.id for g in net._chain(tpl.group)]
        if gid is not None and gid in chain:
            per_block[int(n.id.split("@", 1)[1].split(".")[0]) - 1].append((n.id, n.tag, n.kind))
        elif gid is None and n_blocks == 1 and tpl.group is not None:
            per_block[0].append((n.id, n.tag, n.kind))
        else:
            plant.append((n.id, n.tag, n.kind))
    if n_blocks > 1 and gid is None:
        notes.append(f"no network group repeats once per layout block ({n_blocks} blocks); all equipment is placed at the plant equipment area")
    return per_block, plant, notes


# --- small geometry helpers --------------------------------------------------------------------


def _ring(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def _cluster(centre: tuple[float, float], n: int, pitch: float) -> list[tuple[float, float]]:
    cols = max(1, math.ceil(math.sqrt(n * 2)))
    rows = math.ceil(n / cols)
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        out.append((centre[0] + (c - (cols - 1) / 2) * pitch, centre[1] + ((rows - 1) / 2 - r) * pitch))
    return out


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
                  on_roof: bool = False, pit_at: tuple[float, float] | None = None) -> None:  # fmt: skip
    """Equipment cluster with a local ring, each item bonded to the ring by its connections, and its pits on or beside the ring."""
    items = sorted(items, key=lambda t: (KIND_ORDER.get(t[2], 99), t[0]))
    pos = _cluster(centre, max(1, len(items)), STATION_PITCH_M / 2 if on_roof else STATION_PITCH_M)
    xs, ys = [p[0] for p in pos], [p[1] for p in pos]
    m = 1.0 if on_roof else 2.0
    ring = _ring(min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
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
        return
    for (role, serves), q in zip(roles, _ring_points(ring, len(roles))):
        lay.pit(role, q, (serves,))


# --- array rows --------------------------------------------------------------------------------


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
                    lay.run(ConductorRole.ARRAY, size, [(uu * ux + v * vx, uu * uy + v * vy), (uu * ux + v2 * vx, uu * uy + v2 * vy)], buried=buried)
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


SWITCHYARD_NAME = re.compile(r"switch ?yard|sub ?station|pooling", re.IGNORECASE)


def designated_switchyard(project: Project) -> Polygon | None:
    """A site area marked for the switchyard: attribute use=switchyard, or a name or id that says switchyard, substation or pooling."""
    geo = project.site.geometry
    if geo is None:
        return None
    for f in geo.features:
        if f.polygon and (f.attributes.get("use") == "switchyard" or SWITCHYARD_NAME.search(f.name or "") or SWITCHYARD_NAME.search(f.id)):
            return shape_of(f)
    return None


def place_switchyard(project: Project, size: tuple[float, float], near: tuple[float, float] | None) -> tuple[tuple[float, float, float, float] | None, str | None]:
    """The switchyard grid rectangle: inside a designated switchyard area if the site has one, else the free area nearest `near`."""
    marked = designated_switchyard(project)
    if marked is not None:
        x0, y0, x1, y1 = marked.buffer(-2.0).bounds
        return (x0, y0, x1, y1), None
    site = boundary(project)
    if site is None:
        return None, "no site boundary: the switchyard grid is not placed"
    layout = project.array_layout
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()]) if layout is not None else Polygon()
    free = site.buffer(-3.0).difference(tables.buffer(5.0)).difference(blocked(project).buffer(3.0))
    lx, ly = size
    room = free.buffer(-math.hypot(lx, ly) / 2)
    target = SPoint(near) if near is not None else site.centroid
    if room.is_empty:
        return None, f"no free area of {lx:g} x {ly:g} m inside the site for the switchyard earth grid; it is not drawn"
    c = nearest_points(room, target)[0]
    return (c.x - lx / 2, c.y - ly / 2, c.x + lx / 2, c.y + ly / 2), None


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
    lay.switchyard = Switchyard(x0, y0, x1, y1, float(xs[1] - xs[0]))
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
                 switchyard: tuple[tuple[float, float, float, float] | None, float] | None, perimeter_pits: int = 8) -> EarthingLayout:  # fmt: skip
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
    pitch = _row_pitch(tables)
    array_runs: list[Run] = []
    if lay.rooftop:
        for rid, poly in roof_polys:
            on = [t for t in tables if poly.buffer(0.5).contains(Polygon(table_corners(t)).centroid)]
            if not on:
                continue
            segs = _row_runs(lay, on, sizes.array, None, buried=False)
            array_runs += [s[3] for s in segs]
            _risers(lay, segs, on[0].azimuth_deg, sizes.array, 1.6 * (pitch or 10), buried=False)
            envelope = unary_union([Polygon(table_corners(t)) for t in on])
            x0, y0, x1, y1 = envelope.bounds
            for corner in ((x0, y0), (x1, y1)):
                edge, g = _outside_roof(poly, corner, site)
                start = nearest_points(MultiLineString([[_xy(p) for p in r.points] for r in array_runs if r.links and r.links[0] in {t.id for t in on}]), SPoint(edge))[0]
                lay.run(ConductorRole.DOWN, sizes.down, [(start.x, start.y), edge], (rid,), buried=False)
                lay.run(ConductorRole.DOWN, sizes.down, [edge, g], (rid,), buried=False)
                lay.pit(PitRole.EP, g, (f"{rid} array",))
    else:
        segs = _row_runs(lay, tables, sizes.array, avoid, buried=True)
        array_runs += [s[3] for s in segs]
        if tables:
            _risers(lay, segs, tables[0].azimuth_deg, sizes.array, 1.6 * (pitch or 10), buried=True)
    for i, b in enumerate(blocks):
        items = per_block.get(i, [])
        if b.station is None:
            if items:
                lay.warnings.append(f"block {b.id} has no station; its {len(items)} equipment items are not placed")
            continue
        scbs = [it for it in items if it[2] is NodeKind.SCB]
        rest = [it for it in items if it[2] is not NodeKind.SCB]
        if not rest:
            rest = [(f"{b.id}~station", f"{b.name} STATION", NodeKind.INVERTER)]
        on_roof = lay.rooftop and any(p.contains(SPoint(b.station.x, b.station.y)) for _, p in roof_polys)
        _station_area(lay, _xy(b.station), rest, b.id, sizes, on_roof=on_roof)
        if on_roof:
            poly = next(p for _, p in roof_polys if p.contains(SPoint(b.station.x, b.station.y)))
            edge, g = _outside_roof(poly, _xy(b.station), site)
            lay.run(ConductorRole.DOWN, sizes.down, [_xy(b.station), edge], (b.id,), buried=False)
            lay.run(ConductorRole.DOWN, sizes.down, [edge, g], (b.id,), buried=False)
            for k in range(GROUP_PITS):
                q = (g[0] + (k - 0.5) * 2 * electrode_m, g[1])
                lay.run(ConductorRole.MAIN, sizes.main, [g, q])
                lay.pit(PitRole.EP, q, (f"{b.id} station",))
        for (iid, tag, kind), p in zip(scbs, _scb_positions(b, len(scbs))):
            lay.items.append(Item(id=iid, kind=kind.value, tag=tag, at=Point(x=round(p[0], 3), y=round(p[1], 3)), connections=CONNECTIONS[kind], on_roof=lay.rooftop))
            near = sorted(array_runs, key=lambda r: LineString([_xy(q) for q in r.points]).distance(SPoint(p)))[: CONNECTIONS[kind]]
            for r in near:
                q = nearest_points(LineString([_xy(x) for x in r.points]), SPoint(p))[0]
                lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, (q.x, q.y)], (iid,), buried=not lay.rooftop)
            if not lay.rooftop:
                lay.pit(PitRole.EP, (p[0] + 1.0, p[1] + 0.6), (iid,))
                lay.run(ConductorRole.EQUIPMENT, sizes.equipment, [p, (p[0] + 1.0, p[1] + 0.6)], (iid,))
    yard_items = [it for it in plant if it[2] in MV_KINDS] if switchyard is not None and switchyard[0] is not None else []
    panel_items = [it for it in plant if it not in yard_items]
    if yard_items:
        _switchyard(lay, switchyard[0], switchyard[1], yard_items, sizes, perimeter_pits)
    if panel_items:
        _station_area(lay, _panel_area(project, lay, site, roof_polys), panel_items, "plant", sizes)
    if lps is not None:
        _lps(lay, lps, sizes, site, dict(roof_polys))
    existing = np.asarray([_xy(p.at) for p in lay.pits], dtype=float).reshape(-1, 2)
    buried_array = [r for r in array_runs if r.buried]
    want = (math.ceil(len(tables) / tables_per_pit) + array_pits) if buried_array else 0
    for q in _farthest(_along(buried_array, max(2.0, electrode_m)), existing, want, 2 * electrode_m):
        lay.pit(PitRole.EP, q, ("array",))
    if lay.rooftop and array_pits > 0:
        ground = [r for r in lay.runs if r.buried]
        base = _along(ground, 1.0) if ground else existing
        extra = _farthest(base, existing, array_pits, 2 * electrode_m)
        for q in extra:
            lay.pit(PitRole.EP, q, ("additional",))
        if len(extra) < array_pits and len(existing):
            g0 = (float(existing[0][0]), float(existing[0][1]))
            sign = 1.0 if site is None or site.contains(SPoint(g0[0] + 2 * electrode_m * (array_pits - len(extra)), g0[1])) else -1.0
            prev = g0
            for k in range(array_pits - len(extra)):
                q = (g0[0] + sign * 2 * electrode_m * (k + 1), g0[1])
                lay.run(ConductorRole.MAIN, sizes.main, [prev, q])
                lay.pit(PitRole.EP, q, ("additional",))
                prev = q
    _pit_spurs(lay, sizes)
    connect_all(lay, sizes.main)
    return lay


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
    """Plant-level panels: beside the first roof edge nearest the station on rooftop projects, else beside the station nearest the site centroid."""
    layout = project.array_layout
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


def _lps(lay: EarthingLayout, lps: LpsDesign, sizes: Sizes, site: Polygon | None, roof_by_id: dict[str, Polygon]) -> None:
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
            g = (at[0] + 1.5, at[1])
            lay.run(ConductorRole.DOWN, sizes.down, [at, g], (t.tag,), prefix="D")
            lay.pit(PitRole.LE, g, (t.tag,))

"""Partition tables into inverter blocks of near-equal DC capacity, and site each block's inverter station."""

import math
from collections.abc import Sequence

from shapely import STRtree, concave_hull
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Polygon
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from unicad.arrays.geometry import frame, table_centre, table_corners
from unicad.arrays.siting import free_spot, occupied, roof_spot
from unicad.arrays.spec import RoadLine
from unicad.domain.layout import LayoutBlock, Point, Table
from unicad.domain.site import FeatureKind, SiteGeometry, shape_of

CONCAVE_RATIO = 0.25
CUT_SLACK = 0.02
WIDE_GAP_RATIO = 2.5
REGION_BALANCE = 0.10
MAX_GROWTH = 1.5
SWAP_WINDOW = 40
COMPACT_REACH = 3.0
COMPACT_NEIGHBOURS = 12
COMPACT_PASSES = 50


def block_count(total_kwp: float, n_blocks: int | None, block_dc_kwp: float | None, n_tables: int) -> int:
    if n_blocks is not None:
        n = n_blocks
    elif block_dc_kwp is not None:
        n = math.ceil(total_kwp / block_dc_kwp - 1e-9)
    else:
        n = 1
    return max(1, min(n, n_tables))


def _median(values: list[float]) -> float:
    v = sorted(values)
    return v[len(v) // 2] if v else 0.0


def partition(
    tables: Sequence[Table], weights: Sequence[float], n: int, azimuth_deg: float, cap: float | None = None,
    units: Sequence[int] | None = None, quantum: int | None = None,
) -> list[list[int]]:  # fmt: skip
    """Recursive capacity-weighted bisection of table centres in the array frame.

    Each cut may move off the exact capacity share by up to CUT_SLACK of one block, and within that window takes the widest
    gap between tables, so blocks split along corridors, ponds and roads rather than through rows. The cut runs across the
    group's longer side unless the other axis offers a gap much wider than its usual table spacing. With `cap`, the slack
    never takes either side's mean block capacity above it.

    With `units` (strings per table) and `quantum` (strings per inverter) every cut gives each side its even share of whole inverters
    (blocks differ by at most one inverter) where the tables allow, keeping under `cap`; a side may carry a remainder of fewer strings
    than one inverter, which trimming removes.
    """
    (ux, uy), (vx, vy) = frame(azimuth_deg)
    pts = []
    for t in tables:
        x, y = table_centre(t)
        pts.append((x * ux + y * uy, x * vx + y * vy))

    def best_cut(idx: list[int], k: int, axis: int) -> tuple[float, float, int, list[int]]:
        ordered = sorted(idx, key=lambda i: (round(pts[i][axis], 6), round(pts[i][1 - axis], 6), tables[i].id))
        k_lo = k // 2
        total = sum(weights[i] for i in ordered)
        want = total * k_lo / k
        slack = CUT_SLACK * total / k
        coords = [pts[i][axis] for i in ordered]
        steps = [b - a for a, b in zip(coords, coords[1:]) if b - a > 1e-6]
        typical = _median(steps)
        acc, best = 0.0, (-1.0, math.inf, 1)
        lo, hi = k_lo, len(ordered) - (k - k_lo)
        whole = units is not None and quantum
        if whole:
            total_u = sum(units[i] for i in ordered)
            invs = total_u // quantum
            want_inv = invs // k * k_lo + min(invs % k, k_lo)
            acc_u, near = 0, None
        for pos in range(1, len(ordered)):
            acc += weights[ordered[pos - 1]]
            if whole:
                acc_u += units[ordered[pos - 1]]
            if not lo <= pos <= hi:
                continue
            miss = abs(acc - want)
            within_cap = cap is None or (acc <= cap * k_lo + 1e-9 and total - acc <= cap * (k - k_lo) + 1e-9)
            if whole:
                key = (not within_cap, abs(acc_u // quantum - want_inv) + abs((total_u - acc_u) // quantum - (invs - want_inv)), acc_u % quantum, -(coords[pos] - coords[pos - 1]))
                if near is None or key < near[0]:
                    near = (key, miss, pos)
                continue
            gap = coords[pos] - coords[pos - 1] if miss <= slack and within_cap else -1.0
            if (gap, -miss) > (best[0], -best[1]):
                best = (gap, miss, pos)
        if whole and near is not None:
            best = (max(0.0, -near[0][3]), near[1], near[2])
        ratio = best[0] / typical if typical > 0 and best[0] > 0 else 0.0
        return ratio, best[1], best[2], ordered

    def exact(lo_side: list[int], hi_side: list[int], k: int, axis: int) -> tuple[list[int], list[int]]:
        """With whole inverters: when the cut leaves the low side a few strings off its share of whole inverters, move one table, or swap a
        pair, among the SWAP_WINDOW tables either side of the cut so both sides hold exactly whole inverters (where such tables exist)."""
        k_lo = k // 2
        total_u = sum(units[i] for i in lo_side + hi_side)
        invs = total_u // quantum
        want = (invs // k * k_lo + min(invs % k, k_lo)) * quantum
        d = sum(units[i] for i in lo_side) - want
        if not d or abs(d) >= quantum:
            return lo_side, hi_side
        edge = (pts[lo_side[-1]][axis] + pts[hi_side[0]][axis]) / 2
        near_lo, near_hi = lo_side[-SWAP_WINDOW:], hi_side[:SWAP_WINDOW]
        moves: list[tuple[float, tuple[int, ...], tuple[int, ...]]] = []
        moves += [(abs(pts[a][axis] - edge), (a,), ()) for a in near_lo if units[a] == d]
        moves += [(abs(pts[b][axis] - edge), (), (b,)) for b in near_hi if units[b] == -d]
        moves += [(abs(pts[a][axis] - edge) + abs(pts[b][axis] - edge), (a,), (b,)) for a in near_lo for b in near_hi if units[a] - units[b] == d]
        if not moves:
            return lo_side, hi_side
        _, out, into = min(moves, key=lambda m: (m[0], m[1], m[2]))
        return [i for i in lo_side if i not in out] + list(into), list(out) + [i for i in hi_side if i not in into]

    def split(idx: list[int], k: int) -> list[list[int]]:
        if k == 1 or len(idx) <= 1:
            return [idx]
        xs = [pts[i][0] for i in idx]
        ys = [pts[i][1] for i in idx]
        longer = 0 if max(xs) - min(xs) >= max(ys) - min(ys) else 1
        cuts = {axis: best_cut(idx, k, axis) for axis in (0, 1)}
        other = 1 - longer
        axis = other if cuts[other][0] >= WIDE_GAP_RATIO and cuts[other][0] > cuts[longer][0] else longer
        _, _, cut, ordered = cuts[axis]
        k_lo = k // 2
        lo_side, hi_side = ordered[:cut], ordered[cut:]
        if units is not None and quantum:
            lo_side, hi_side = exact(lo_side, hi_side, k, axis)
        return split(lo_side, k_lo) + split(hi_side, k - k_lo)

    groups = [g for g in split(list(range(len(tables))), n) if g]
    classes = [(units[i] if units else 0, round(weights[i], 6)) for i in range(len(tables))]
    return compact(groups, pts, classes)


def compact(groups: list[list[int]], pts: Sequence[tuple[float, float]], classes: Sequence[object]) -> list[list[int]]:
    """Swap tables of equal class (same strings and capacity) between blocks while that lowers the sum of squared distances of the tables to
    their block centres. The cuts of `partition` run through rows in table order, which leaves staircase edges and, where the placed set is
    ragged, blocks in two pieces; swaps keep every block's capacity and strings exactly and pull each block into one compact patch."""
    if len(groups) < 2:
        return groups
    owner = {i: g for g, idx in enumerate(groups) for i in idx}
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    spacing = math.sqrt(max((max(xs) - min(xs)) * (max(ys) - min(ys)), 1e-9) / len(pts))

    def grid(size: float) -> dict[tuple[int, int], list[int]]:
        cells: dict[tuple[int, int], list[int]] = {}
        for i in sorted(owner):
            cells.setdefault((int(pts[i][0] // size), int(pts[i][1] // size)), []).append(i)
        return cells

    def within(cells: dict[tuple[int, int], list[int]], size: float, i: int) -> list[int]:
        cx, cy = int(pts[i][0] // size), int(pts[i][1] // size)
        return [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in cells.get((cx + dx, cy + dy), ()) if j != i]

    coarse = 2.0 * spacing
    cells = grid(coarse)
    nearest = [d for i in owner if (d := min((math.dist(pts[i], pts[j]) for j in within(cells, coarse, i)), default=0.0)) > 0]
    if not nearest:
        return groups
    reach = COMPACT_REACH * _median(nearest)
    cells = grid(reach)
    around = {
        i: sorted((j for j in within(cells, reach, i) if classes[j] == classes[i] and math.dist(pts[i], pts[j]) <= reach), key=lambda j: (math.dist(pts[i], pts[j]), j))[:COMPACT_NEIGHBOURS]
        for i in owner
    }
    size = [len(g) for g in groups]
    sx = [sum(pts[i][0] for i in g) for g in groups]
    sy = [sum(pts[i][1] for i in g) for g in groups]
    sq = [sum(pts[i][0] ** 2 + pts[i][1] ** 2 for i in g) for g in groups]

    def spread(g: int, dx: float, dy: float, dq: float) -> float:
        return sq[g] + dq - ((sx[g] + dx) ** 2 + (sy[g] + dy) ** 2) / size[g]

    for _ in range(COMPACT_PASSES):
        moved = False
        for i in sorted(owner):
            a = owner[i]
            best = None
            for j in around[i]:
                b = owner[j]
                if b == a:
                    continue
                dx, dy = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
                dq = pts[j][0] ** 2 + pts[j][1] ** 2 - pts[i][0] ** 2 - pts[i][1] ** 2
                gain = spread(a, 0, 0, 0) + spread(b, 0, 0, 0) - spread(a, dx, dy, dq) - spread(b, -dx, -dy, -dq)
                if gain > 1e-6 and (best is None or gain > best[0] + 1e-9):
                    best = (gain, j, dx, dy, dq)
            if best is not None:
                _, j, dx, dy, dq = best
                b = owner[j]
                sx[a] += dx; sy[a] += dy; sq[a] += dq
                sx[b] -= dx; sy[b] -= dy; sq[b] -= dq
                owner[i], owner[j] = b, a
                moved = True
        if not moved:
            break
    out: list[list[int]] = [[] for _ in groups]
    for i in sorted(owner):
        out[owner[i]].append(i)
    return out


def _shed(t: Table, mps: int) -> int:
    """Strings one top row of `t` holds when every row holds whole strings, else 0: such a table can shed rows to drop single strings."""
    return t.cols // mps if t.rows > 1 and not t.unused and t.cols % mps == 0 else 0


def _trim(
    group: list[int], tables: Sequence[Table], kwp: Sequence[float], units: Sequence[int], quantum: int, notes: list[str], bi: int, mps: int,
) -> tuple[list[int], dict[int, int]]:  # fmt: skip
    """Drop the tables farthest from the block's capacity centre until its strings make whole inverters of `quantum` strings. Where whole
    tables cannot make up the remainder (two-string tables and an odd remainder), the farthest table that holds one string per row sheds its
    top rows instead, so the block mixes table sizes rather than losing a whole inverter. Returns the kept tables and {table: rows left}."""
    have = sum(units[i] for i in group)
    r = have % quantum
    if not r:
        return group, {}
    total = sum(kwp[i] for i in group) or 1.0
    cx = sum(table_centre(tables[i])[0] * kwp[i] for i in group) / total
    cy = sum(table_centre(tables[i])[1] * kwp[i] for i in group) / total
    far = sorted(group, key=lambda i: (-math.hypot(table_centre(tables[i])[0] - cx, table_centre(tables[i])[1] - cy), tables[i].id))
    for drop in range(r, have + 1, quantum):
        left, out, shrunk = drop, set(), {}
        for i in far:
            if 0 < units[i] <= left:
                out.add(i)
                left -= units[i]
            if not left:
                break
        for i in far:
            if not left:
                break
            per_row = _shed(tables[i], mps)
            if i in out or not per_row or left % per_row:
                continue
            rows = min(tables[i].rows - 1, left // per_row)
            shrunk[i] = tables[i].rows - rows
            left -= rows * per_row
        if not left:
            kw = sum(kwp[i] for i in out) + sum(kwp[i] * (1 - shrunk[i] / tables[i].rows) for i in shrunk)
            what = [f"{len(out)} table(s) left out"] if out else []
            what += [f"{len(shrunk)} table(s) cut to {', '.join(sorted({f'{shrunk[i]} x {tables[i].cols}' for i in shrunk}))} modules (mixed table sizes)"] if shrunk else []
            notes.append(f"BLOCK-{bi:02d}: {drop} strings ({kw:.1f} kWp) dropped, {' and '.join(what)}, so the block holds whole inverters of {quantum} strings; pass whole_inverters=false to keep them")
            return [i for i in group if i not in out], shrunk
    return group, {}


def outline(tables: Sequence[Table], closing_m: float) -> Polygon:
    """Tables merged across row gaps and roads up to `closing_m` wide; a concave hull when that still leaves pieces."""
    merged = unary_union([Polygon(table_corners(t)) for t in tables]).buffer(closing_m / 2, join_style="mitre").buffer(-closing_m / 2, join_style="mitre")
    if isinstance(merged, Polygon) and not merged.is_empty:
        return Polygon(merged.exterior)
    corners = [c for t in tables for c in table_corners(t)]
    hull = concave_hull(MultiPoint(corners), ratio=CONCAVE_RATIO)
    if not isinstance(hull, Polygon) or hull.is_empty:
        hull = MultiPoint(corners).convex_hull
    if not isinstance(hull, Polygon):
        hull = hull.buffer(0.5)
    return hull


def site_roads(site: SiteGeometry) -> list[BaseGeometry]:
    out = []
    for f in site.of_kind(FeatureKind.ROAD):
        g = shape_of(f)
        out.append(g if isinstance(g, LineString) else g.exterior)
    return out


def station_point(tables: Sequence[Table], weights: Sequence[float], hull: Polygon, roads: Sequence[BaseGeometry]) -> Point:
    """Capacity-weighted centroid, moved to the nearest road centreline that passes within reach of the block, else to the
    nearest point on the block outline so that the station stands clear of the tables."""
    total = sum(weights)
    cx = sum(table_centre(t)[0] * w for t, w in zip(tables, weights)) / total
    cy = sum(table_centre(t)[1] * w for t, w in zip(tables, weights)) / total
    reach = hull.buffer(max(10.0, math.sqrt(hull.area) * 0.15))
    centre = ShapelyPoint(cx, cy)
    best = None
    for road in roads:
        if not road.intersects(reach):
            continue
        part = road.intersection(reach)
        if part.is_empty:
            continue
        _, q = nearest_points(centre, part)
        d = centre.distance(q)
        if best is None or d < best[0]:
            best = (d, q)
    if best is None:
        _, q = nearest_points(centre, hull.exterior)
        best = (0.0, q)
    x, y = best[1].x, best[1].y
    return Point(x=round(x, 3), y=round(y, 3))


ROOF_STATION_INSET_M = 0.5


def roof_station(tables: Sequence[Table], weights: Sequence[float], site: SiteGeometry, others: Sequence[Table] = ()) -> Point | None:
    """On the roof that carries most of the block's capacity: the point of its edge nearest the capacity-weighted centre of the block's
    tables on that roof, ROOF_STATION_INSET_M inside the edge so the station stands on the roof, not between roofs, moved to the nearest
    spot of that roof clear of every table (`others`, the whole array) when the edge point lies over one."""
    roofs = [(f.id, shape_of(f)) for f in site.of_kind(FeatureKind.ROOF) if f.polygon]
    if not roofs:
        return None
    load: dict[str, list[tuple[Table, float]]] = {}
    for t, w in zip(tables, weights):
        c = ShapelyPoint(*table_centre(t))
        rid = next((rid for rid, g in roofs if g.covers(c)), None)
        if rid is not None:
            load.setdefault(rid, []).append((t, w))
    if not load:
        return None
    rid = max(load, key=lambda k: (sum(w for _, w in load[k]), k))
    roof = dict(roofs)[rid]
    total = sum(w for _, w in load[rid])
    cx = sum(table_centre(t)[0] * w for t, w in load[rid]) / total
    cy = sum(table_centre(t)[1] * w for t, w in load[rid]) / total
    inner = roof.buffer(-ROOF_STATION_INSET_M)
    ring = (inner if not inner.is_empty and isinstance(inner, Polygon) else roof).exterior
    _, q = nearest_points(ShapelyPoint(cx, cy), ring)
    on_roof = [t for t in (others or tables) if roof.intersects(Polygon(table_corners(t)))]
    clear = roof_spot(roof, (q.x, q.y), on_roof, ROOF_STATION_INSET_M)
    return clear or Point(x=round(q.x, 3), y=round(q.y, 3))


def corridors(site: SiteGeometry, keep_out_only: bool = False) -> list[tuple[str, BaseGeometry]]:
    """Linear corridors that a block should not straddle: transmission line corridors and roads with a width, by feature id. A transmission
    line corridor is a keep-out (cable is not run under the line); a road can be crossed, so `keep_out_only` leaves roads out."""
    out = []
    for f in site.features:
        if f.kind is FeatureKind.TRANSMISSION_LINE or (not keep_out_only and f.kind is FeatureKind.ROAD and (f.polygon or f.width_m)):
            g = shape_of(f)
            out.append((f.id, g if f.polygon else g.buffer((f.width_m or 1.0) / 2, cap_style="flat")))
    return out


def _extended(site: SiteGeometry, area: BaseGeometry, walls: list[tuple[str, BaseGeometry]]) -> list[LineString]:
    """Dead ends of corridor centrelines inside the area, carried on along their last segment to the area edge or another corridor,
    so that a road that stops short still divides the area into two sides."""
    out = []
    reach = math.hypot(area.bounds[2] - area.bounds[0], area.bounds[3] - area.bounds[1])
    inner = area.buffer(-1.0)
    for f in site.features:
        if not f.polyline or f.id not in {i for i, _ in walls}:
            continue
        stops = unary_union([area.boundary] + [g.boundary for i, g in walls if i != f.id])
        cs = list(shape_of(f).coords)
        for end, prev in ((cs[-1], cs[-2]), (cs[0], cs[1])):
            if not inner.contains(ShapelyPoint(end)):
                continue
            dx, dy = end[0] - prev[0], end[1] - prev[1]
            n = math.hypot(dx, dy) or 1.0
            ray = LineString([end, (end[0] + dx / n * reach, end[1] + dy / n * reach)])
            hit = ray.intersection(stops)
            ds = [ShapelyPoint(end).distance(p) for p in getattr(hit, "geoms", [hit]) if not p.is_empty]
            ds = [d for d in ds if d > 0.5]
            if ds:
                d = min(ds)
                out.append(LineString([end, (end[0] + dx / n * d, end[1] + dy / n * d)]))
    return out


def regions(tables: Sequence[Table], site: SiteGeometry, keep_out_only: bool = False) -> list[list[int]]:
    """Tables grouped by the part of the site they stand in once the corridors cut it (indices into `tables`); with `keep_out_only`, once only
    the keep-out corridors (transmission lines) cut it."""
    walls = corridors(site, keep_out_only)
    if not walls or len(tables) < 2:
        return [list(range(len(tables)))]
    bounds = [shape_of(f) for f in site.features if f.kind is FeatureKind.BOUNDARY and f.polygon]
    area = unary_union(bounds) if bounds else MultiPoint([c for t in tables for c in table_corners(t)]).convex_hull.buffer(5.0)
    area = area.union(unary_union([Polygon(table_corners(t)) for t in tables]).buffer(1.0))
    cut = unary_union([g for _, g in walls] + [e.buffer(0.05) for e in _extended(site, area, walls)])
    parts = area.difference(cut)
    polys = [p for p in (parts.geoms if isinstance(parts, MultiPolygon) else [parts]) if not p.is_empty]
    if len(polys) < 2:
        return [list(range(len(tables)))]
    tree = STRtree(polys)
    groups: dict[int, list[int]] = {}
    for i, t in enumerate(tables):
        c = ShapelyPoint(*table_centre(t))
        hit = [int(j) for j in tree.query(c) if polys[int(j)].contains(c)]
        k = hit[0] if hit else int(tree.nearest(c))
        groups.setdefault(k, []).append(i)
    return [groups[k] for k in sorted(groups, key=lambda k: min(groups[k]))]


def allocate(caps: Sequence[float], n: int, cap: float | None) -> list[int]:
    """Blocks per region: one each, then each further block to the region with the largest mean block (D'Hondt), so the largest
    block is as small as it can be. With `cap`, each region gets at least enough blocks to keep under it."""
    k = [1] * len(caps)
    if cap is not None:
        k = [max(1, math.ceil(c / cap - 1e-9)) for c in caps]
    while sum(k) < n:
        i = max(range(len(caps)), key=lambda i: (caps[i] / k[i], -i))
        k[i] += 1
    return k


def _spread(caps: Sequence[float], k: Sequence[int]) -> float:
    means = [c / n for c, n in zip(caps, k)]
    total = sum(caps) / sum(k)
    return (max(means) - min(means)) / total if total else 0.0


def _by_inverter(idx: list[int], tables: Sequence[Table], kwp: Sequence[float], azimuth_deg: float, units: Sequence[int] | None, quantum: int | None) -> list[int]:
    """The block's tables in string order: with strings per inverter, grouped into compact patches of one inverter each (the same
    whole-inverter bisection as the blocks), so that each inverter's strings stand together rather than along a strip of rows."""
    if not units or not quantum:
        return idx
    n = sum(units[i] for i in idx) // quantum
    if n < 2:
        return idx
    patches = partition([tables[i] for i in idx], [kwp[i] for i in idx], n, azimuth_deg, None, [units[i] for i in idx], quantum)
    return [idx[j] for patch in patches for j in sorted(patch)]


def _inverter_spread(invs: Sequence[int], k: Sequence[int]) -> int:
    """Largest minus smallest inverter count per block when each region shares its whole inverters evenly over its blocks."""
    sizes = [size for i, n in zip(invs, k) for size in {i // n, -(-i // n)}]
    return max(sizes) - min(sizes) if sizes else 0


def build_blocks(
    tables: Sequence[Table], kwp: Sequence[float], n: int, azimuth_deg: float, site: SiteGeometry, internal: Sequence[RoadLine], closing_m: float,
    cap: float | None = None, warnings: list[str] | None = None, flexible: bool = False, on_roofs: bool = False, station: Point | None = None,
    units: Sequence[int] | None = None, quantum: int | None = None, trim: bool = False, mps: int | None = None,
) -> list[LayoutBlock]:  # fmt: skip
    """Blocks that keep to one side of every corridor. Each region between corridors gets its share of the blocks; when that leaves
    region means more than REGION_BALANCE apart, a `flexible` count (from block_dc_kwp) may grow until they agree, else the
    smallest region joins its nearest neighbour and those blocks may cross the corridor between them. The count grows to at most
    MAX_GROWTH times `n`. With whole inverters (`units`, `quantum`, `trim`) a region counts only the capacity of the whole inverters its
    strings make, and a region too small for one inverter is left out."""
    notes = warnings if warnings is not None else []
    parts = regions(tables, site)
    whole = bool(units and quantum and trim)
    if whole and len(parts) > 1:
        small = [g for g in parts if sum(units[i] for i in g) < quantum]
        if small and len(small) < len(parts):
            notes.append(f"{sum(len(g) for g in small)} tables in {len(small)} part(s) of the site cut off by roads or line corridors are left out: each holds fewer strings than one inverter of {quantum}")
            parts = [g for g in parts if sum(units[i] for i in g) >= quantum]

    def usable(g: list[int]) -> float:
        kw = sum(kwp[i] for i in g)
        if not whole:
            return kw
        u = sum(units[i] for i in g)
        return kw * (u // quantum * quantum) / u if u else 0.0

    while True:
        caps = [usable(g) for g in parts]
        if len(parts) == 1:
            k = [n]
            break
        tries = range(n, math.ceil(MAX_GROWTH * n) + 1) if flexible else (n,)
        if whole:
            invs = [sum(units[i] for i in g) // quantum for g in parts]
            balanced = lambda kk: _inverter_spread(invs, kk) <= 1  # noqa: E731
        else:
            balanced = lambda kk: _spread(caps, kk) <= REGION_BALANCE  # noqa: E731
        k = next((kk for m in tries if sum(kk := allocate(caps, m, cap)) == m and balanced(kk)), None)
        if k is not None:
            if sum(k) != n:
                notes.append(f"{sum(k)} blocks instead of {n} so that no block crosses a road or line corridor and the blocks stay balanced")
            break
        small = min(range(len(parts)), key=lambda i: (caps[i], i))
        cx = [sum(table_centre(tables[i])[0] for i in g) / len(g) for g in parts]
        cy = [sum(table_centre(tables[i])[1] for i in g) / len(g) for g in parts]
        near = min((j for j in range(len(parts)) if j != small), key=lambda j: (math.hypot(cx[j] - cx[small], cy[j] - cy[small]), j))
        parts[near] = sorted(parts[near] + parts[small])
        del parts[small]
        notes.append(
            "some blocks cross a road or line corridor: keeping every block to one side would leave blocks more than one inverter apart"
            if whole
            else f"some blocks cross a road or line corridor: keeping every block to one side would unbalance the blocks by more than {REGION_BALANCE:.0%}"
        )
    groups: list[list[int]] = []
    for g, kk in zip(parts, k):
        sub = partition([tables[i] for i in g], [kwp[i] for i in g], kk, azimuth_deg, cap, [units[i] for i in g] if units else None, quantum)
        groups += [[g[i] for i in s] for s in sub]
    shrunk: dict[int, int] = {}
    if units and quantum and trim:
        kept = []
        for bi, g in enumerate(groups, start=1):
            g, cut = _trim(g, tables, kwp, units, quantum, notes, bi, mps or 1)
            shrunk |= cut
            kept.append(g)
        groups = [g for g in kept if g]
    if corridors(site, keep_out_only=True):
        island = {i: k for k, g in enumerate(regions(tables, site, keep_out_only=True)) for i in g}
        across = [f"BLOCK-{bi:02d}" for bi, g in enumerate(groups, start=1) if len({island[i] for i in g}) > 1]
        if across:
            notes.append(
                f"{', '.join(across)} stand on both sides of a transmission line corridor: equal whole inverters per block outweigh a block per side here, so each of "
                "these blocks' feeders crosses under the line once; network_autobuild keeps every inverter's strings on one side wherever the strings divide, and says where they do not"
            )
    inner = [LineString([(p.x, p.y) for p in r.points]) for r in internal]
    roads = site_roads(site) + inner
    taken = None
    blocks = []
    for bi, idx in enumerate(groups, start=1):
        bid = f"B{bi:02d}"
        members = _by_inverter(sorted(idx), tables, kwp, azimuth_deg, units, quantum)
        renamed = [tables[i].model_copy(update={"id": f"{bid}-T{k:03d}", **({"rows": shrunk[i]} if i in shrunk else {})}) for k, i in enumerate(members, start=1)]
        w = [kwp[i] * (shrunk[i] / tables[i].rows if i in shrunk else 1.0) for i in members]
        hull = outline(renamed, closing_m)
        at = station or (roof_station(renamed, w, site, tables) if on_roofs else None)
        if at is None:
            at = station_point(renamed, w, hull, roads)
            if taken is None:
                taken = occupied(site, tables, inner, max((r.width_m for r in internal), default=0.0))
            at = free_spot(site, (at.x, at.y), taken) or at
        ring = tuple(Point(x=round(x, 3), y=round(y, 3)) for x, y in list(hull.exterior.coords)[:-1])
        blocks.append(LayoutBlock(id=bid, name=f"BLOCK-{bi:02d}", boundary=ring, tables=tuple(renamed), station=at))
    return blocks

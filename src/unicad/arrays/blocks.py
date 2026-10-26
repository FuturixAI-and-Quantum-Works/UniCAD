"""Partition tables into inverter blocks of near-equal DC capacity, and site each block's inverter station."""

import math
from collections.abc import Sequence

from shapely import STRtree, concave_hull
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Polygon
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from unicad.arrays.geometry import frame, table_centre, table_corners
from unicad.arrays.spec import RoadLine
from unicad.domain.layout import LayoutBlock, Point, Table
from unicad.domain.site import FeatureKind, SiteGeometry, shape_of

CONCAVE_RATIO = 0.25
CUT_SLACK = 0.02
WIDE_GAP_RATIO = 2.5
REGION_BALANCE = 0.10
MAX_GROWTH = 1.5


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


def partition(tables: Sequence[Table], weights: Sequence[float], n: int, azimuth_deg: float, cap: float | None = None) -> list[list[int]]:
    """Recursive capacity-weighted bisection of table centres in the array frame.

    Each cut may move off the exact capacity share by up to CUT_SLACK of one block, and within that window takes the widest
    gap between tables, so blocks split along corridors, ponds and roads rather than through rows. The cut runs across the
    group's longer side unless the other axis offers a gap much wider than its usual table spacing. With `cap`, the slack
    never takes either side's mean block capacity above it.
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
        for pos in range(1, len(ordered)):
            acc += weights[ordered[pos - 1]]
            if not lo <= pos <= hi:
                continue
            miss = abs(acc - want)
            within_cap = cap is None or (acc <= cap * k_lo + 1e-9 and total - acc <= cap * (k - k_lo) + 1e-9)
            gap = coords[pos] - coords[pos - 1] if miss <= slack and within_cap else -1.0
            if (gap, -miss) > (best[0], -best[1]):
                best = (gap, miss, pos)
        ratio = best[0] / typical if typical > 0 and best[0] > 0 else 0.0
        return ratio, best[1], best[2], ordered

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
        return split(ordered[:cut], k_lo) + split(ordered[cut:], k - k_lo)

    return [g for g in split(list(range(len(tables))), n) if g]


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


def corridors(site: SiteGeometry) -> list[tuple[str, BaseGeometry]]:
    """Linear corridors that a block should not straddle: transmission line corridors and roads with a width, by feature id."""
    out = []
    for f in site.features:
        if f.kind is FeatureKind.TRANSMISSION_LINE or (f.kind is FeatureKind.ROAD and (f.polygon or f.width_m)):
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


def regions(tables: Sequence[Table], site: SiteGeometry) -> list[list[int]]:
    """Tables grouped by the part of the site they stand in once the corridors cut it (indices into `tables`)."""
    walls = corridors(site)
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


def build_blocks(
    tables: Sequence[Table], kwp: Sequence[float], n: int, azimuth_deg: float, site: SiteGeometry, internal: Sequence[RoadLine], closing_m: float,
    cap: float | None = None, warnings: list[str] | None = None, flexible: bool = False,
) -> list[LayoutBlock]:  # fmt: skip
    """Blocks that keep to one side of every corridor. Each region between corridors gets its share of the blocks; when that leaves
    region means more than REGION_BALANCE apart, a `flexible` count (from block_dc_kwp) may grow until they agree, else the
    smallest region joins its nearest neighbour and those blocks may cross the corridor between them. The count grows to at most
    MAX_GROWTH times `n`."""
    notes = warnings if warnings is not None else []
    parts = regions(tables, site)
    while True:
        caps = [sum(kwp[i] for i in g) for g in parts]
        if len(parts) == 1:
            k = [n]
            break
        tries = range(n, math.ceil(MAX_GROWTH * n) + 1) if flexible else (n,)
        k = next((kk for m in tries if sum(kk := allocate(caps, m, cap)) == m and _spread(caps, kk) <= REGION_BALANCE), None)
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
        notes.append(f"some blocks cross a road or line corridor: keeping every block to one side would unbalance the blocks by more than {REGION_BALANCE:.0%}")
    groups: list[list[int]] = []
    for g, kk in zip(parts, k):
        sub = partition([tables[i] for i in g], [kwp[i] for i in g], kk, azimuth_deg, cap)
        groups += [[g[i] for i in s] for s in sub]
    roads = site_roads(site) + [LineString([(p.x, p.y) for p in r.points]) for r in internal]
    blocks = []
    for bi, idx in enumerate(groups, start=1):
        bid = f"B{bi:02d}"
        members = sorted(idx)
        renamed = [tables[i].model_copy(update={"id": f"{bid}-T{k:03d}"}) for k, i in enumerate(members, start=1)]
        w = [kwp[i] for i in members]
        hull = outline(renamed, closing_m)
        station = station_point(renamed, w, hull, roads)
        ring = tuple(Point(x=round(x, 3), y=round(y, 3)) for x, y in list(hull.exterior.coords)[:-1])
        blocks.append(LayoutBlock(id=bid, name=f"BLOCK-{bi:02d}", boundary=ring, tables=tuple(renamed), station=station))
    return blocks

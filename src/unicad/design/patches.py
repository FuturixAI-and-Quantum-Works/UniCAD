"""Compact patches of layout strings for inverters and combiners: capacitated clustering in the array frame."""

from unicad.arrays.geometry import frame
from unicad.domain.layout import ArrayLayout, StringAssignment
from unicad.routing.locate import slot_centre

XY = tuple[float, float]
Item = tuple[XY, StringAssignment]
LLOYD_ROUNDS = 12

PATCHES = (
    "Each layout block's strings are split between its inverters as compact patches of neighbouring tables: capacitated clustering in the array frame "
    "(recursive bisection along the longer extent, then rounds of moving each patch to its centroid and re-assigning strings at least squared distance "
    "within the inverter string counts), so every inverter collects one contiguous patch and stands beside it."
)
SCB_PATCHES = (
    "Each central inverter's strings are grouped onto its SCBs the same way (compact patches within the SCB sizes), so every SCB combines a patch of "
    "neighbouring strings and stands at their centroid."
)


def centre(layout: ArrayLayout, a: StringAssignment) -> XY:
    """The string's module centroid in the array frame of its first table (u along the rows, v across them)."""
    pts = [slot_centre(layout.table_by_id[t], r, c) for t, r, c in a.slots()]
    t = layout.table_by_id[a.spans[0].table]
    (ux, uy), (vx, vy) = frame(t.azimuth_deg)
    x, y = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    return x * ux + y * uy, x * vx + y * vy


def reach(points: list[XY]) -> float:
    """The farthest of `points` from the point nearest all of them, measured along the array axes (half the larger diagonal extent)."""
    p = [x + y for x, y in points]
    q = [x - y for x, y in points]
    return max(max(p) - min(p), max(q) - min(q)) / 2


def _bisect(items: list[tuple[int, XY]], sizes: list[int]) -> list[list[int]]:
    if len(sizes) <= 1:
        return [[i for i, _ in items]]
    k = len(sizes) // 2
    first = sum(sizes[:k])
    spans = [max(p[a] for _, p in items) - min(p[a] for _, p in items) for a in (0, 1)]
    axis = 0 if spans[0] >= spans[1] else 1
    ordered = sorted(items, key=lambda it: (it[1][axis], it[1][1 - axis], it[0]))
    return _bisect(ordered[:first], sizes[:k]) + _bisect(ordered[first:], sizes[k:])


def _cost(p: XY, c: XY) -> float:
    return (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2


def _negative_cycle(w: list[list[float]]) -> list[int] | None:
    """A cycle of clusters with negative total weight in the complete digraph `w` (Bellman-Ford from a virtual source), else None."""
    k = len(w)
    dist, pred = [0.0] * k, [-1] * k
    last = -1
    for _ in range(k):
        last = -1
        for a in range(k):
            da = dist[a]
            row = w[a]
            for b in range(k):
                if a != b and da + row[b] < dist[b] - 1e-9:
                    dist[b], pred[b], last = da + row[b], a, b
        if last < 0:
            return None
    for _ in range(k):
        last = pred[last]
    cycle, at = [last], pred[last]
    while at != last:
        cycle.append(at)
        at = pred[at]
    return cycle[::-1]


def _assign(points: list[XY], cents: list[XY], where: list[int]) -> list[int]:
    """The least-total-squared-distance assignment of `points` to `cents` that keeps every cluster's size in `where`: cancel negative exchange cycles
    (each moves one point along the cycle, so sizes hold) until none is left, which is optimal for the transportation problem."""
    k = len(cents)
    where = list(where)
    cost = [[_cost(p, c) for c in cents] for p in points]
    for _ in range(50 * len(points)):
        w = [[0.0] * k for _ in range(k)]
        arg = [[-1] * k for _ in range(k)]
        best = [[float("inf")] * k for _ in range(k)]
        for i, row in enumerate(cost):
            a = where[i]
            here, ba, ga = row[a], best[a], arg[a]
            for b in range(k):
                d = row[b] - here
                if d < ba[b]:
                    ba[b], ga[b] = d, i
        for a in range(k):
            for b in range(k):
                w[a][b] = best[a][b]
        cycle = _negative_cycle(w)
        if cycle is None:
            break
        moves = [(arg[a][b], b) for a, b in zip(cycle, cycle[1:] + cycle[:1], strict=True)]
        for i, b in moves:
            where[i] = b
    return where


def _radius(points: list[XY]) -> float:
    cx, cy = sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
    return max(abs(x - cx) + abs(y - cy) for x, y in points)


def _score(points: list[XY], groups: list[list[int]], at_centroid: bool) -> tuple[float, float]:
    measure = _radius if at_centroid else reach
    rs = [measure([points[i] for i in g]) for g in groups if g]
    return max(rs), sum(rs)


def _seeded(points: list[XY], sizes: list[int]) -> list[list[int]]:
    """Each group starts at the point farthest from the centre of those left and takes its nearest neighbours (along the array axes)."""
    left, out = list(range(len(points))), []
    for size in sizes:
        cx, cy = sum(points[i][0] for i in left) / len(left), sum(points[i][1] for i in left) / len(left)
        seed = max(left, key=lambda i: (abs(points[i][0] - cx) + abs(points[i][1] - cy), -i))
        near = sorted(left, key=lambda i: (abs(points[i][0] - points[seed][0]) + abs(points[i][1] - points[seed][1]), i))[:size]
        taken = set(near)
        left = [i for i in left if i not in taken]
        out.append(near)
    return out


def _lloyd(points: list[XY], groups: list[list[int]], sizes: list[int], at_centroid: bool):
    best, best_score = groups, _score(points, groups, at_centroid)
    where = [0] * len(points)
    for k, g in enumerate(groups):
        for i in g:
            where[i] = k
    for _ in range(LLOYD_ROUNDS):
        cents = [(sum(points[i][0] for i in g) / len(g), sum(points[i][1] for i in g) / len(g)) if g else (0.0, 0.0) for g in groups]
        moved = _assign(points, cents, where)
        if moved == where:
            break
        where = moved
        groups = [[i for i in range(len(points)) if where[i] == k] for k in range(len(sizes))]
        score = _score(points, groups, at_centroid)
        if score < best_score:
            best, best_score = groups, score
    return best, best_score


def patches(items: list[Item], sizes: list[int], at_centroid: bool = False) -> list[list[StringAssignment]]:
    """`items` split into groups of exactly `sizes` (in that order) that are compact in the array frame: recursive bisection and seeded groups, each
    refined by capacitated Lloyd rounds (centroids, then the optimal size-keeping reassignment). The grouping whose worst patch is least wins: measured
    from the point nearest all its strings along the array axes, or from its centroid with `at_centroid` (a combiner that stands at the centroid)."""
    if len(sizes) <= 1:
        return [[a for _, a in items]]
    if sum(sizes) != len(items):
        raise ValueError(f"patch sizes {sizes} do not add up to {len(items)} strings")
    points = [p for p, _ in items]
    starts = (_bisect(list(enumerate(points)), sizes), _seeded(points, sizes))
    best = min((_lloyd(points, g, sizes, at_centroid) for g in starts), key=lambda gs: gs[1])[0]
    return [[items[i][1] for i in g] for g in best]

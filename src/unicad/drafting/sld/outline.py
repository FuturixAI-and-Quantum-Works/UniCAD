"""Rectilinear outline of a union of boxes, for groups whose members do not form one subtree."""

from collections import defaultdict

from unicad.drafting.sld.model import Box, Point


def union_outline(boxes: list[Box]) -> tuple[tuple[Point, ...], ...]:
    """Closed counter-clockwise loops tracing the union of `boxes`, collinear vertices merged."""
    xs = sorted({v for b in boxes for v in (b.x0, b.x1)})
    ys = sorted({v for b in boxes for v in (b.y0, b.y1)})
    nx, ny = len(xs) - 1, len(ys) - 1
    covered = [[False] * ny for _ in range(nx)]
    for i in range(nx):
        cx = (xs[i] + xs[i + 1]) / 2
        for j in range(ny):
            cy = (ys[j] + ys[j + 1]) / 2
            covered[i][j] = any(b.x0 < cx < b.x1 and b.y0 < cy < b.y1 for b in boxes)

    def on(i: int, j: int) -> bool:
        return 0 <= i < nx and 0 <= j < ny and covered[i][j]

    edges: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for i in range(nx):
        for j in range(ny):
            if not covered[i][j]:
                continue
            if not on(i, j - 1):
                edges[(i, j)].append((i + 1, j))
            if not on(i + 1, j):
                edges[(i + 1, j)].append((i + 1, j + 1))
            if not on(i, j + 1):
                edges[(i + 1, j + 1)].append((i, j + 1))
            if not on(i - 1, j):
                edges[(i, j + 1)].append((i, j))
    loops: list[tuple[Point, ...]] = []
    def take(v: tuple[int, int]) -> tuple[int, int]:
        nxt = edges[v].pop(0)
        if not edges[v]:
            del edges[v]
        return nxt

    while edges:
        start = min(edges)
        loop = [start]
        cur = take(start)
        while cur != start:
            loop.append(cur)
            cur = take(cur)
        pts = [(xs[i], ys[j]) for i, j in loop]
        merged = [p for k, p in enumerate(pts) if not _collinear(pts[k - 1], p, pts[(k + 1) % len(pts)])]
        loops.append(tuple(merged))
    return tuple(loops)


def _collinear(a: Point, b: Point, c: Point) -> bool:
    return (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1])


def inside(loops: tuple[tuple[Point, ...], ...], p: Point) -> bool:
    """Even-odd point-in-polygon over all loops."""
    hit = False
    for loop in loops:
        for a, b in zip(loop, loop[1:] + loop[:1]):
            if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
                hit = not hit
    return hit

"""Collision-free label placement for plan sheets.

Each label is a box of known size tied to an anchor (a pit, a terminal, a station). The placer tries the eight positions around
the anchor first, then rings of positions further out joined back by a leader line, and takes the first ring that has a box clear
of every placed label, every blocked area (symbols) and the sheet area's edge; within a ring the optional cost decides. A label
that finds no place within the maximum leader length is returned as overflow for the caller's callout list. Labels are placed in
the order given, so the result is deterministic. An optional `allow(box, anchor)` vetoes boxes (and the leader to the anchor) the caller
keeps labels out of, such as the far side of a fence.
"""

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from unicad.drafting.sheets.canvas import Region

ADJACENT = ((1, 0), (1, 1), (1, -1), (-1, 0), (-1, 1), (-1, -1), (0, 1), (0, -1))
RING_DIRECTIONS = 16


@dataclass(frozen=True)
class Label:
    key: str
    anchor: tuple[float, float]
    width: float
    height: float
    clear: float = 0.0


@dataclass(frozen=True)
class Placement:
    label: Label
    box: Region
    leader: tuple[tuple[float, float], tuple[float, float]] | None = None


def _box_at(anchor: tuple[float, float], w: float, h: float, dx: float, dy: float, d: float) -> Region:
    """The w x h box beside `anchor` in direction (dx, dy), its nearest edge or corner `d` away."""
    x, y = anchor
    if dx > 0:
        x0 = x + d * (1 if dy == 0 else 0.7071)
    elif dx < 0:
        x0 = x - d * (1 if dy == 0 else 0.7071) - w
    else:
        x0 = x - w / 2
    if dy > 0:
        y0 = y + d * (1 if dx == 0 else 0.7071)
    elif dy < 0:
        y0 = y - d * (1 if dx == 0 else 0.7071) - h
    else:
        y0 = y - h / 2
    return Region(x0, y0, x0 + w, y0 + h)


def _ring_box(anchor: tuple[float, float], w: float, h: float, ang: float, d: float) -> Region:
    """The box whose nearest point to `anchor` lies `d` away along the direction `ang`."""
    cx, cy = anchor[0] + d * math.cos(ang), anchor[1] + d * math.sin(ang)
    ox = 0.0 if abs(math.cos(ang)) < 0.2 else (w / 2 if math.cos(ang) > 0 else -w / 2)
    oy = 0.0 if abs(math.sin(ang)) < 0.2 else (h / 2 if math.sin(ang) > 0 else -h / 2)
    cx, cy = cx + ox, cy + oy
    return Region(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def nearest_on(box: Region, p: tuple[float, float]) -> tuple[float, float]:
    return (min(max(p[0], box.x0), box.x1), min(max(p[1], box.y0), box.y1))


def _segment_hits(box: Region, a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Whether segment ab passes through the interior of `box` (Liang-Barsky)."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - box.x0), (dx, box.x1 - a[0]), (-dy, a[1] - box.y0), (dy, box.y1 - a[1])):
        if p == 0:
            if q <= 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return False
    return t1 - t0 > 1e-9


class LabelPlacer:
    def __init__(self, bounds: Region, max_leader: float, gap: float = 0.0, cell: float | None = None):
        self.bounds, self.max_leader, self.gap = bounds, max_leader, gap
        self.cell = cell or max(1.0, max_leader)
        self.grid: dict[tuple[int, int], list[Region]] = defaultdict(list)
        self.labels: list[Placement] = []
        self.overflow: list[Label] = []

    def _cells(self, r: Region) -> Iterable[tuple[int, int]]:
        c = self.cell
        for i in range(math.floor(r.x0 / c), math.floor(r.x1 / c) + 1):
            for j in range(math.floor(r.y0 / c), math.floor(r.y1 / c) + 1):
                yield i, j

    def _free(self, r: Region) -> bool:
        if not self.bounds.contains(r):
            return False
        g = r.inset(-self.gap)
        return not any(g.overlaps(q) for k in self._cells(g) for q in self.grid.get(k, ()))

    def _add(self, r: Region) -> None:
        for k in self._cells(r):
            self.grid[k].append(r)

    def block(self, r: Region) -> None:
        """Keep labels off `r` (a symbol or a fixed piece of text)."""
        self._add(r)

    def block_path(self, points: Sequence[tuple[float, float]], closed: bool = False, thickness: float = 0.0, step: float | None = None) -> None:
        """Keep labels off a polyline (a feature outline): straight axis-aligned runs are one thin box, slanted ones a chain of small boxes."""
        pts = list(points) + ([points[0]] if closed and points else [])
        half = thickness / 2
        step = step or max(thickness * 4, 1e-3)
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            n = 1 if abs(x1 - x0) < 1e-9 or abs(y1 - y0) < 1e-9 else max(1, math.ceil(max(abs(x1 - x0), abs(y1 - y0)) / step))
            for i in range(n):
                ax, ay = x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n
                bx, by = x0 + (x1 - x0) * (i + 1) / n, y0 + (y1 - y0) * (i + 1) / n
                self._add(Region(min(ax, bx) - half, min(ay, by) - half, max(ax, bx) + half, max(ay, by) + half))

    def _leader_clear(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        seg = Region(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        return not any(_segment_hits(p.box, a, b) for p in self.labels if p.box.overlaps(seg.inset(-1e-6)))

    def place(self, label: Label, cost: Callable[[Region], float] | None = None, allow: Callable[[Region, tuple[float, float]], bool] | None = None) -> Placement | None:
        w, h, a = label.width, label.height, label.anchor
        d0 = label.clear + max(self.gap, 0.15 * h)
        rings: list[list[Region]] = [[_box_at(a, w, h, dx, dy, d0) for dx, dy in ADJACENT]]
        step = max(h, 0.5 * self.max_leader / 6)
        d = d0 + step
        while d <= self.max_leader + 1e-9:
            rings.append([_ring_box(a, w, h, 2 * math.pi * i / RING_DIRECTIONS, d) for i in range(RING_DIRECTIONS)])
            d += step
        for n, ring in enumerate(rings):
            ok = [(i, r) for i, r in enumerate(ring) if self._free(r) and (allow is None or allow(r, label.anchor))]
            if n:
                ok = [(i, r) for i, r in ok if self._leader_clear(a, nearest_on(r, a))]
            if not ok:
                continue
            _, box = min(ok, key=lambda t: (round(cost(t[1]), 6) if cost else 0.0, t[0]))
            leader = None
            if n:
                tip = nearest_on(box, a)
                ln = math.dist(a, tip) or 1.0
                start = (a[0] + (tip[0] - a[0]) * label.clear / ln, a[1] + (tip[1] - a[1]) * label.clear / ln)
                leader = (start, tip)
            p = Placement(label, box, leader)
            self._add(box)
            self.labels.append(p)
            return p
        self.overflow.append(label)
        return None

    def fixed(self, label: Label, box: Region) -> Placement | None:
        """Place `label` exactly at `box` if that is clear (feature names centred on their feature), else nothing."""
        if not self._free(box):
            return None
        p = Placement(label, box)
        self._add(box)
        self.labels.append(p)
        return p

    def place_all(self, labels: Sequence[Label]) -> list[Placement | None]:
        return [self.place(lb) for lb in labels]


def overlapping(boxes: Sequence[Region], tol: float = 1e-6) -> list[tuple[int, int]]:
    """Index pairs of boxes that overlap (for checks and tests)."""
    order = sorted(range(len(boxes)), key=lambda i: boxes[i].x0)
    out = []
    for n, i in enumerate(order):
        for j in order[n + 1 :]:
            if boxes[j].x0 >= boxes[i].x1 - tol:
                break
            if boxes[i].overlaps(boxes[j], tol):
                out.append((min(i, j), max(i, j)))
    return sorted(out)


__all__ = ["Label", "LabelPlacer", "Placement", "nearest_on", "overlapping"]

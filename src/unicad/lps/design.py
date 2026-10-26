"""Lightning protection design for a project: which method and radius, where the terminals stand, and the proof that every
table, station and roof is inside a protection zone."""

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point as SPoint
from shapely.geometry import Polygon
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep

from unicad.arrays.geometry import table_centre, table_corners
from unicad.domain.layout import Mounting, Point
from unicad.domain.project import LpsBasis, Project, Segment
from unicad.domain.site import FeatureKind, shape_of
from unicad.earthing.model import Step
from unicad.lps.zones import CoverageError, ese_radius, greedy_cover, rod_radius, sphere_radius

ROOFTOP_SEGMENTS = frozenset({Segment.RESIDENTIAL, Segment.CI_ROOFTOP})
ROD_HEIGHT_ASSUMED_M = 10.0
GROUND_PROTECTED_ASSUMED_M = 3.0
ROOF_PROTECTED_ASSUMED_M = 1.0


@dataclass(frozen=True)
class Target:
    id: str
    kind: str
    at: tuple[float, float]


@dataclass(frozen=True)
class Terminal:
    tag: str
    at: Point
    on_roof: bool
    roof: str | None = None


@dataclass(frozen=True)
class LpsDesign:
    method: str
    level: str
    radius_m: float
    steps: tuple[Step, ...]
    terminals: tuple[Terminal, ...]
    targets: tuple[Target, ...]
    uncovered: tuple[Target, ...]
    mast_height_m: float | None
    assumptions: tuple[str, ...] = field(default=())

    @property
    def covered_all(self) -> bool:
        return not self.uncovered


def is_rooftop(project: Project) -> bool:
    layout = project.array_layout
    if layout is not None and any(t.mounting is Mounting.ROOF_FLUSH for t in layout.table_by_id.values()):
        return True
    return project.segment in ROOFTOP_SEGMENTS


def roofs(project: Project) -> list[tuple[str, Polygon]]:
    geo = project.site.geometry
    if geo is None:
        return []
    return [(f.id, shape_of(f)) for f in geo.features if f.kind in (FeatureKind.ROOF, FeatureKind.BUILDING) and f.polygon]


def boundary(project: Project) -> Polygon | None:
    geo = project.site.geometry
    if geo is None:
        return None
    parts = [shape_of(f) for f in geo.features if f.kind is FeatureKind.BOUNDARY and f.polygon]
    return unary_union(parts) if parts else None


def blocked(project: Project) -> Polygon:
    geo = project.site.geometry
    if geo is None:
        return Polygon()
    shapes = []
    for f in geo.features:
        if f.kind in (FeatureKind.WATER, FeatureKind.EXCLUSION, FeatureKind.EXISTING_STRUCTURE, FeatureKind.OBSTACLE, FeatureKind.TRANSMISSION_LINE):
            g = shape_of(f)
            shapes.append(g if f.polygon else g.buffer((f.width_m or 1.0) / 2))
        elif f.kind is FeatureKind.ROAD and f.polyline:
            shapes.append(shape_of(f).buffer((f.width_m or 1.0) / 2))
    return unary_union(shapes) if shapes else Polygon()


def targets(project: Project, extra: tuple[Target, ...] = ()) -> tuple[Target, ...]:
    out: list[Target] = []
    layout = project.array_layout
    if layout is not None:
        for t in layout.table_by_id.values():
            out.append(Target(t.id, "table", table_centre(t)))
            out += [Target(f"{t.id}#{i}", "table_corner", c) for i, c in enumerate(table_corners(t))]
        out += [Target(f"{b.id}~station", "station", (b.station.x, b.station.y)) for b in layout.blocks if b.station is not None]
    if is_rooftop(project):
        for rid, poly in roofs(project):
            out += [Target(f"{rid}#{i}", "roof", (x, y)) for i, (x, y) in enumerate(poly.exterior.coords[:-1])]
    return tuple(out) + extra


def _grid_points(region, spacing: float) -> np.ndarray:
    if region.is_empty:
        return np.zeros((0, 2))
    x0, y0, x1, y1 = region.bounds
    xs = np.arange(x0 + spacing / 2, x1, spacing)
    ys = np.arange(y0 + spacing / 2, y1, spacing)
    pr = prep(region)
    pts = [(x, y) for y in ys for x in xs if pr.contains(SPoint(x, y))]
    return np.asarray(pts, dtype=float).reshape(-1, 2)


def _snapped(free, spacing: float) -> np.ndarray:
    """Grid points moved onto the nearest free ground (row gaps, roads) when it is within half a grid step."""
    if free.is_empty:
        return np.zeros((0, 2))
    x0, y0, x1, y1 = free.bounds
    pr = prep(free)
    out = []
    for y in np.arange(y0 + spacing / 2, y1, spacing):
        for x in np.arange(x0 + spacing / 2, x1, spacing):
            p = SPoint(x, y)
            if pr.contains(p):
                out.append((x, y))
                continue
            q = nearest_points(free, p)[0]
            if q.distance(p) <= spacing / 2:
                out.append((round(q.x, 3), round(q.y, 3)))
    return np.unique(np.asarray(out, dtype=float).reshape(-1, 2), axis=0)


def candidates(project: Project, radius: float) -> tuple[np.ndarray, list[str | None]]:
    """Mast positions: free ground inside the boundary away from tables and carved features; on rooftop projects, also free roof area."""
    layout = project.array_layout
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()]) if layout is not None else Polygon()
    pts, where = [], []
    spacing = max(3.0, min(radius / 4, 25.0))
    if is_rooftop(project):
        for rid, poly in roofs(project):
            free = poly.buffer(-0.5).difference(tables.buffer(0.3))
            g = _grid_points(free, max(2.0, min(spacing, 6.0)))
            pts.append(g)
            where += [rid] * len(g)
    site = boundary(project)
    if site is not None:
        roof_union = unary_union([p for _, p in roofs(project)]) if roofs(project) else Polygon()
        free = site.buffer(-2.0).difference(tables.buffer(0.5)).difference(blocked(project)).difference(roof_union.buffer(1.0))
        g = _snapped(free, spacing)
        pts.append(g)
        where += [None] * len(g)
    arr = np.concatenate(pts) if pts else np.zeros((0, 2))
    return arr, where


def design_lps(project: Project, basis: LpsBasis | None = None) -> LpsDesign:
    basis = basis or project.design_basis.lps
    if basis is None:
        raise CoverageError("no lightning protection basis: set design_basis.lps (lps_design does)")
    rooftop = is_rooftop(project)
    steps: list[Step] = [sphere_radius(basis.level)]
    assumptions: list[str] = []
    mast = basis.mast_height_m
    if basis.method == "ese":
        if basis.ese_radius_m is not None:
            radius = basis.ese_radius_m
            assumptions.append(f"ESE protection radius {radius:g} m as given (user or terminal datasheet), not recomputed")
        elif basis.ese_delta_t_us is not None:
            if mast is None:
                mast = 5.0
                assumptions.append("ESE tip taken 5 m above the protected plane (mast_height_m not given; assumption)")
            step = ese_radius(basis.level, basis.ese_delta_t_us, mast)
            steps.append(step)
            radius = step.value
        else:
            raise CoverageError("ESE design needs ese_radius_m (from the terminal datasheet) or ese_delta_t_us and mast_height_m")
    else:
        if mast is None:
            mast = ROD_HEIGHT_ASSUMED_M
            assumptions.append(f"air terminal height {mast:g} m (mast_height_m not given; assumption)")
        hx = basis.protected_height_m
        if hx is None:
            hx = ROOF_PROTECTED_ASSUMED_M if rooftop else GROUND_PROTECTED_ASSUMED_M
            assumptions.append(f"protected height {hx:g} m above {'the roof' if rooftop else 'ground'} (protected_height_m not given; assumption)")
        step = rod_radius(basis.level, mast, hx)
        steps.append(step)
        radius = step.value
    tg = targets(project)
    pts = np.asarray([t.at for t in tg], dtype=float).reshape(-1, 2)
    cand, where = candidates(project, radius)
    if not len(cand) and len(pts):
        raise CoverageError("no free position for a lightning terminal inside the site")
    cover = greedy_cover(pts, cand, radius) if len(pts) else None
    terms: list[Terminal] = []
    if cover is not None:
        index = {(float(c[0]), float(c[1])): i for i, c in enumerate(cand)}
        prefix = "ESE" if basis.method == "ese" else "LA"
        for i, (x, y) in enumerate(sorted(cover.terminals, key=lambda p: (-round(p[1], 1), p[0]))):
            roof = where[index[(x, y)]]
            terms.append(Terminal(f"{prefix}-{i + 1:02d}", Point(x=round(x, 3), y=round(y, 3)), roof is not None, roof))
    uncovered = tuple(tg[i] for i in cover.uncovered) if cover is not None else ()
    if basis.method == "ese":
        assumptions.append("ESE design to NF C 17-102, a French standard used in Indian EPC practice; IS/IEC 62305 does not recognise an enlarged ESE radius")
    return LpsDesign(basis.method, basis.level, radius, tuple(steps), tuple(terms), tg, uncovered, mast, tuple(assumptions))


def coverage_points(design: LpsDesign, kinds: tuple[str, ...] = ("table",)) -> tuple[int, int]:
    """(covered, total) over targets of `kinds`, recomputed from the terminal positions."""
    pts = [t.at for t in design.targets if t.kind in kinds]
    if not pts:
        return 0, 0
    tp = [(t.at.x, t.at.y) for t in design.terminals]
    ok = sum(1 for p in pts if any(math.dist(p, q) <= design.radius_m + 1e-6 for q in tp))
    return ok, len(pts)


__all__ = ["LpsDesign", "Target", "Terminal", "coverage_points", "design_lps", "is_rooftop"]

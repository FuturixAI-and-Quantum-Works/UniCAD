"""Lightning protection design for a project: which method and radius, where the terminals stand, and the proof that every
table, station and roof is inside a protection zone."""

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import MultiPoint
from shapely.geometry import Point as SPoint
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep

from unicad.arrays.geometry import frame, table_centre, table_corners
from unicad.arrays.sun import sun_path
from unicad.arrays.siting import FENCE_CLEAR_MIN_M, equipment_ground
from unicad.domain.layout import Point
from unicad.domain.project import LpsBasis, Project
from unicad.domain.site import FeatureKind
from unicad.earthing.model import Step
from unicad.lps.site import blocked, boundary, is_rooftop, roofs, switchyard_footprint
from unicad.lps.zones import CoverageError, covered, ese_radius, greedy_cover, rod_radius, sphere_radius

ROD_HEIGHT_ASSUMED_M = 10.0
ESE_TIP_ASSUMED_M = 5.0
GROUND_PROTECTED_ASSUMED_M = 3.0
ROOF_PROTECTED_ASSUMED_M = 1.0
MAST_CLEAR_M = 1.5
MAST_CLEAR_MIN_M = 0.5
SAMPLE_PITCH_M = 4.0
SHADE_HOURS = (9.0, 15.0)
RELOCATE_SPACING_M = 2.0
RELOCATE_EVALUATIONS = 120
RELOCATE_INSET_M = 0.002
ENVELOPE_M = {"inverter": 2.0, "acdb": 2.0, "lt_panel": 2.0, "poi_grid": 2.0}
ENVELOPE_DEFAULT_M = 8.0
CLASS_LABEL = {
    "table": "tables", "station": "inverter stations", "switchyard": "MV switchyard", "bess": "BESS units", "field_inverter": "string inverters", "lt_panel": "LT panels",
    "roof": "roofs", "building": "buildings",
}
ENVELOPE_ASSUMPTION = (
    f"equipment footprints are not in the catalog: each station and each BESS unit (container, PCS, transformer) is protected as a {ENVELOPE_DEFAULT_M:g} m square "
    f"centred on its stored position and each string inverter and LT panel as a {ENVELOPE_M['inverter']:g} m square (assumed envelopes, not datasheet sizes)"
)
MAST_ASSUMPTION = (
    f"terminals keep {MAST_CLEAR_M:g} m clear of every table (room for the mast foundation and its lightning earth pit; assumed clearance, not a cited figure), "
    f"keep the setback of exclusions, water and line corridors as the tables do, stand {FENCE_CLEAR_MIN_M:g} m inside the boundary fence (assumed clearance), "
    "and stand off roads, stations, the switchyard grid and BESS units"
)


@dataclass(frozen=True)
class Target:
    """A point that must lie inside a protection zone. Points sampled from one structure share the part of `id` before '#', its `group`."""

    id: str
    kind: str
    at: tuple[float, float]

    @property
    def group(self) -> str:
        return self.id.split("#")[0]


@dataclass(frozen=True)
class Terminal:
    tag: str
    at: Point
    on_roof: bool
    roof: str | None = None
    shade_m2: float = 0.0


@dataclass(frozen=True)
class ClassCoverage:
    kind: str
    covered: int
    total: int

    @property
    def label(self) -> str:
        return CLASS_LABEL.get(self.kind, self.kind)


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

    def coverage(self) -> tuple[ClassCoverage, ...]:
        """Per target class, the structures (tables, stations, BESS units, ...) all of whose points lie inside a terminal's radius, recomputed
        from the terminal positions, over the structures of the class."""
        return class_coverage(self.targets, [(t.at.x, t.at.y) for t in self.terminals], self.radius_m)


def class_coverage(targets: tuple[Target, ...], terminals: list[tuple[float, float]], radius: float) -> tuple[ClassCoverage, ...]:
    pts = np.asarray([t.at for t in targets], dtype=float).reshape(-1, 2)
    ok = covered(pts, terminals, radius) if len(pts) else np.zeros(0, dtype=bool)
    groups: dict[tuple[str, str], bool] = {}
    for t, hit in zip(targets, ok):
        k = (t.kind.removesuffix("_corner"), t.group)
        groups[k] = groups.get(k, True) and bool(hit)
    out: dict[str, list[int]] = {}
    for (kind, _), hit in groups.items():
        c = out.setdefault(kind, [0, 0])
        c[0] += hit
        c[1] += 1
    order = list(CLASS_LABEL)
    return tuple(ClassCoverage(k, c[0], c[1]) for k, c in sorted(out.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99, kv[0])))


def equipment_footprint(kind: str, at: tuple[float, float]) -> Polygon:
    """The assumed protected envelope of one piece of placed equipment or a station standing at `at`."""
    h = ENVELOPE_M.get(kind, ENVELOPE_DEFAULT_M) / 2
    return box(at[0] - h, at[1] - h, at[0] + h, at[1] + h)


def _samples(poly: Polygon, pitch: float = SAMPLE_PITCH_M) -> list[tuple[float, float]]:
    """Corners, points along the edges at most `pitch` apart and a grid inside at that pitch: a footprint inside the radius of one terminal at
    all of them is covered."""
    out: list[tuple[float, float]] = []
    ring = list(poly.exterior.coords)
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        n = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / pitch))
        out += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
    x0, y0, x1, y1 = poly.bounds
    inside = prep(poly.buffer(-1e-6))
    out += [(float(x), float(y)) for x in np.arange(x0 + pitch / 2, x1, pitch) for y in np.arange(y0 + pitch / 2, y1, pitch) if inside.contains(SPoint(x, y))]
    out.append((poly.centroid.x, poly.centroid.y))
    return out


def _footprint_targets(gid: str, kind: str, poly: Polygon) -> list[Target]:
    return [Target(f"{gid}#{i}", kind, (round(x, 3), round(y, 3))) for i, (x, y) in enumerate(_samples(poly))]


def _equipment_class(kind: str) -> str:
    return "field_inverter" if kind == "inverter" else "lt_panel" if kind in ENVELOPE_M else "bess"


def placed_equipment(project: Project) -> list[tuple[str, str, str, tuple[float, float]]]:
    """(class, id, kind, position) of the equipment the layout places in the field: string inverters and BESS units."""
    layout = project.array_layout
    if layout is None:
        return []
    nodes = {n.id for n in project.network.nodes}
    return [(_equipment_class(e.kind), e.node, e.kind, (e.at.x, e.at.y)) for e in layout.equipment if not project.network.nodes or e.node.split("@")[0] in nodes]


def targets(project: Project, extra: tuple[Target, ...] = ()) -> tuple[Target, ...]:
    out: list[Target] = []
    layout = project.array_layout
    if layout is not None:
        for t in layout.table_by_id.values():
            out.append(Target(t.id, "table", table_centre(t)))
            out += [Target(f"{t.id}#{i}", "table_corner", c) for i, c in enumerate(table_corners(t))]
        from unicad.design.placement import stations_exist

        has_stations = stations_exist(project)
        for b in layout.blocks:
            if b.station is not None and has_stations:
                out += _footprint_targets(f"{b.id}~station", "station", equipment_footprint("station", (b.station.x, b.station.y)))
        for cls, node, kind, at in placed_equipment(project):
            out += _footprint_targets(node, cls, equipment_footprint(kind, at))
    panels = any(t.kind == "lt_panel" for t in out)
    yard = switchyard_footprint(project)
    if yard is not None:
        out += _footprint_targets("switchyard", "switchyard", box(*yard))
    elif layout is not None and layout.switchyard is not None and not is_rooftop(project) and not panels:
        out.append(Target("switchyard", "switchyard", (layout.switchyard.at.x, layout.switchyard.at.y)))
    kind = "roof" if is_rooftop(project) else "building"
    for rid, poly in roofs(project):
        out += [Target(f"{rid}#{i}", kind, (x, y)) for i, (x, y) in enumerate(poly.exterior.coords[:-1])]
    return tuple(out) + extra


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


def free_ground(project: Project, clear: float = MAST_CLEAR_M) -> list[tuple[str | None, BaseGeometry]]:
    """Where a mast may stand, by roof id (None for ground): free ground inside the boundary `clear` m away from tables, off roads and keeping the
    setback of every exclusion, water body and line corridor as other ground equipment does (`siting.equipment_ground`), clear of stations, the
    switchyard grid and placed equipment; on rooftop projects, also free roof area."""
    layout = project.array_layout
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()]) if layout is not None else Polygon()
    stations = unary_union([SPoint(b.station.x, b.station.y).buffer(6.0) for b in layout.blocks if b.station is not None]) if layout is not None else Polygon()
    standing = [equipment_footprint(kind, at).buffer(1.0) for _, _, kind, at in placed_equipment(project)]
    yard = switchyard_footprint(project)
    if yard is not None:
        standing.append(box(*yard).buffer(1.0))
    stations = unary_union([stations, *standing])
    out: list[tuple[str | None, BaseGeometry]] = []
    if is_rooftop(project):
        for rid, poly in roofs(project):
            out.append((rid, poly.buffer(-0.5).difference(tables.buffer(0.3)).difference(stations)))
    site = boundary(project)
    if site is not None:
        roof_union = unary_union([p for _, p in roofs(project)]) if roofs(project) else Polygon()
        free = site.buffer(-FENCE_CLEAR_MIN_M).intersection(equipment_ground(project.site.geometry, FENCE_CLEAR_MIN_M)).difference(tables.buffer(clear)).difference(blocked(project))
        out.append((None, free.difference(roof_union.buffer(1.0)).difference(stations)))
    return out


def candidates(project: Project, radius: float, clear: float = MAST_CLEAR_M) -> tuple[np.ndarray, list[str | None]]:
    """Mast positions on a grid over `free_ground`, moved onto the nearest free ground within half a grid step."""
    spacing = max(3.0, min(radius / 8, 12.0))
    pts, where = [], []
    for rid, free in free_ground(project, clear):
        g = _snapped(free, max(2.0, min(spacing, 6.0)) if rid is not None else spacing)
        pts.append(g)
        where += [rid] * len(g)
    arr = np.concatenate(pts) if pts else np.zeros((0, 2))
    return arr, where


def sweep(latitude_deg: float, height_m: float) -> Polygon:
    """Plan area swept by the shadow of a mast of `height_m` from its foot over SHADE_HOURS on the winter solstice (the window and day that set the
    row pitch): the hull of the foot and the shadow of the tip at every sun position."""
    pts = [(0.0, 0.0)]
    for p in sun_path(latitude_deg, *SHADE_HOURS):
        length = height_m / math.tan(math.radians(p.elevation_deg))
        away = math.radians(p.azimuth_deg + 180.0)
        pts.append((length * math.sin(away), length * math.cos(away)))
    return MultiPoint(pts).convex_hull


def _hull_vertices(pts: np.ndarray) -> np.ndarray:
    return np.unique(shapely.get_coordinates(MultiPoint([tuple(p) for p in pts]).convex_hull), axis=0)


def array_area(project: Project) -> BaseGeometry:
    """The ground the array covers: its tables merged across row and table gaps."""
    layout = project.array_layout
    if layout is None or not layout.blocks:
        return Polygon()
    tables = list(layout.table_by_id.values())
    _, (vx, vy) = frame(tables[0].azimuth_deg)
    rows = sorted({round(t.origin.x * vx + t.origin.y * vy, 2) for t in tables})
    steps = [b - a for a, b in zip(rows, rows[1:]) if b - a > 0.5]
    close = (float(np.median(steps)) if steps else 10.0) / 2 + 1.0
    return unary_union([Polygon(table_corners(t)) for t in tables]).buffer(close, join_style="mitre").buffer(-close, join_style="mitre")


def reposition(project: Project, terminals: list[tuple[float, float, str | None]], pts: np.ndarray, radius: float, height_m: float, clear: float,
               keep_out: BaseGeometry | None = None) -> list[tuple[float, float, str | None, float]]:  # fmt: skip
    """Move each terminal, one at a time, to the free spot that keeps every point it alone covers inside its radius and puts the least table
    area under the swept shadow of its mast, then the most room round it. Returns (x, y, roof, shaded m2) per terminal."""
    layout = project.array_layout
    geo = project.site.geometry
    if layout is None or geo is None or not terminals:
        return [(x, y, r, 0.0) for x, y, r in terminals]
    polys = [Polygon(table_corners(t)) for t in layout.table_by_id.values()]
    tree = STRtree(polys)
    shape = sweep(geo.origin.lat, height_m)
    reach = max(math.hypot(x, y) for x, y in shape.exterior.coords)
    ground = [(rid, free.buffer(-RELOCATE_INSET_M, join_style="mitre")) for rid, free in free_ground(project, clear)]
    if keep_out is not None and not keep_out.is_empty:
        ground = [(rid, free.difference(keep_out)) for rid, free in ground]

    def shaded(x: float, y: float) -> float:
        moved = shapely.transform(shape, lambda c: c + (x, y))
        return float(sum(polys[i].intersection(moved).area for i in tree.query(moved, predicate="intersects")))

    def fine(rid: str | None, free: BaseGeometry, need: np.ndarray) -> np.ndarray:
        if free.is_empty or not len(need):
            return np.zeros((0, 2))
        lo = need.max(axis=0) - radius
        hi = need.min(axis=0) + radius
        fx0, fy0, fx1, fy1 = free.bounds
        x0, y0, x1, y1 = max(fx0, lo[0]), max(fy0, lo[1]), min(fx1, hi[0]), min(fy1, hi[1])
        if x0 > x1 or y0 > y1:
            return np.zeros((0, 2))
        local = shapely.clip_by_rect(free, x0, y0, x1, y1)
        if local.is_empty:
            return np.zeros((0, 2))
        gx, gy = np.meshgrid(np.arange(x0, x1 + RELOCATE_SPACING_M, RELOCATE_SPACING_M), np.arange(y0, y1 + RELOCATE_SPACING_M, RELOCATE_SPACING_M))
        grid = np.column_stack([gx.ravel(), gy.ravel()])
        rim = shapely.get_coordinates(shapely.segmentize(shapely.boundary(local), RELOCATE_SPACING_M))
        both = np.concatenate([grid, rim]) if len(rim) else grid
        both = both[shapely.contains_xy(shapely.buffer(local, 1e-6), both[:, 0], both[:, 1])]
        return np.unique(np.round(both, 3), axis=0)

    placed = [(x, y, r) for x, y, r in terminals]
    out = []
    for i, (x, y, rid) in enumerate(placed):
        others = [(a, b) for j, (a, b, _) in enumerate(placed) if j != i]
        need = pts[covered(pts, [(x, y)], radius) & ~covered(pts, others, radius)]
        best = (shaded(x, y), 0.0, x, y, rid)
        if len(need):
            hull = _hull_vertices(need)
            for r, free in ground:
                if rid is not None and r != rid:
                    continue
                cand = fine(r, free, need)
                if not len(cand):
                    continue
                ok = (((hull[:, None, :] - cand[None, :, :]) ** 2).sum(axis=2) <= radius * radius).all(axis=0)
                cand = cand[ok]
                if not len(cand):
                    continue
                room = tree.query_nearest(shapely.points(cand), return_distance=True, all_matches=False)[1]
                near = np.hypot(cand[:, 0] - x, cand[:, 1] - y)
                roomy = np.lexsort((cand[:, 1], cand[:, 0], -room))[:RELOCATE_EVALUATIONS]
                closest = np.lexsort((cand[:, 1], cand[:, 0], near))[:RELOCATE_EVALUATIONS]
                for k in dict.fromkeys([*roomy.tolist(), *closest.tolist()]):
                    cx, cy = float(cand[k][0]), float(cand[k][1])
                    area = shaded(cx, cy) if room[k] < reach else 0.0
                    key = (round(area, 3), round(math.dist((cx, cy), (x, y)), 1), cx, cy)
                    if key < (round(best[0], 3), best[1], best[2], best[3]):
                        best = (area, key[1], cx, cy, r)
        placed[i] = (best[2], best[3], best[4])
        out.append(best)
    return [(b[2], b[3], b[4], round(b[0], 3)) for b in out]


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
    clear = MAST_CLEAR_M
    cand, where = candidates(project, radius, clear)
    cover = greedy_cover(pts, cand, radius) if len(pts) and len(cand) else None
    if len(pts) and (cover is None or cover.uncovered) and clear > MAST_CLEAR_MIN_M:
        clear = MAST_CLEAR_MIN_M
        cand, where = candidates(project, radius, clear)
        cover = greedy_cover(pts, cand, radius) if len(cand) else None
        assumptions.append(f"no cover at {MAST_CLEAR_M:g} m from the tables: terminals may stand {clear:g} m from a table edge")
    if not len(cand) and len(pts):
        raise CoverageError("no free position for a lightning terminal inside the site")
    keep_out = None
    if not rooftop and basis.placement != "array" and len(pts):
        inside = array_area(project).buffer(clear)
        out_mask = ~shapely.contains_xy(inside, cand[:, 0], cand[:, 1]) if not inside.is_empty else np.ones(len(cand), dtype=bool)
        outer = greedy_cover(pts, cand[out_mask], radius) if out_mask.any() else None
        inner_n = len(cover.terminals) if cover is not None else 0
        if outer is not None and (not outer.uncovered or basis.placement == "perimeter"):
            assumptions.append(
                f"terminals on the perimeter, outside the array (lps placement {basis.placement}): {len(outer.terminals)} mast(s) on the boundary strip and roads "
                f"against {inner_n} standing in the array's row gaps, so no mast shades the tables"
                + (f"; {len(outer.uncovered)} target(s) stay uncovered from the perimeter" if outer.uncovered else "")
            )
            cand, where = cand[out_mask], [w for w, m in zip(where, out_mask) if m]
            cover, keep_out = outer, inside
        elif basis.placement == "perimeter":
            assumptions.append("no free ground outside the array holds a terminal: they stand in the array's row gaps")
        elif outer is not None:
            assumptions.append(f"perimeter masts outside the array would leave {len(outer.uncovered)} target(s) uncovered over a {radius:g} m radius: terminals stand in the array's row gaps")
    terms: list[Terminal] = []
    shade_height = mast if mast is not None else ESE_TIP_ASSUMED_M
    if cover is not None:
        index = {(float(c[0]), float(c[1])): i for i, c in enumerate(cand)}
        prefix = "ESE" if basis.method == "ese" else "LA"
        start = [(x, y, where[index[(x, y)]]) for x, y in sorted(cover.terminals, key=lambda p: (-round(p[1], 1), p[0]))]
        moved = reposition(project, start, pts, radius, shade_height, clear, keep_out)
        for i, (x, y, roof, area) in enumerate(sorted(moved, key=lambda m: (-round(m[1], 1), m[0]))):
            terms.append(Terminal(f"{prefix}-{i + 1:02d}", Point(x=round(x, 3), y=round(y, 3)), roof is not None, roof, area))
    uncovered = tuple(tg[i] for i in cover.uncovered) if cover is not None else ()
    if basis.method == "ese":
        assumptions.append("ESE design to NF C 17-102, a French standard used in Indian EPC practice; IS/IEC 62305 does not recognise an enlarged ESE radius")
    if any(t.kind in ("station", "bess", "field_inverter", "lt_panel") for t in tg):
        assumptions.append(ENVELOPE_ASSUMPTION)
    if any(t.kind == "switchyard" for t in tg) and switchyard_footprint(project) is None:
        assumptions.append("MV switchyard size unknown (switchyard_m or a marked area): only its location is checked for lightning cover")
    assumptions.append(MAST_ASSUMPTION if clear == MAST_CLEAR_M else f"terminals stand off stations, the switchyard grid and BESS units and at least {clear:g} m from a table edge (assumed clearance)")
    assumptions += terminal_notes(project, terms, radius, shade_height, mast is None)
    return LpsDesign(basis.method, basis.level, radius, tuple(steps), tuple(terms), tg, uncovered, mast, tuple(assumptions))


def terminal_notes(project: Project, terms: list[Terminal], radius: float, height_m: float, height_assumed: bool) -> list[str]:
    """What a reader of the lightning sheet must be told about where the terminals stand: a lone terminal, the shadow of the masts over
    tables, and protection circles that run past the plot boundary."""
    out: list[str] = []
    if not terms:
        return out
    if len(terms) == 1:
        t = terms[0]
        out.append(f"{t.tag} is the only terminal: it covers every target on its own over a {radius:g} m radius, so the whole plant relies on one terminal, one down conductor and one lightning earth pit; the lightning risk assessment decides whether that is accepted")
    shaded = [t for t in terms if t.shade_m2 > 0]
    h = f"{height_m:g} m{' (assumed tip height, mast_height_m not given)' if height_assumed else ''}"
    if shaded:
        out.append(
            f"mast shadow over tables: a {h} mast shades the table area under its swept shadow between {SHADE_HOURS[0]:02.0f}:00 and {SHADE_HOURS[1]:02.0f}:00 apparent solar time on the winter solstice "
            f"(the window that sets the row pitch; foot to tip shadow): {', '.join(f'{t.tag} {t.shade_m2:.0f} m2' for t in shaded)}; each terminal stands where that area is least while "
            "it still covers the targets only it covers (a terminal outside the array cannot reach them), assumed check, not a cited criterion"
        )
    else:
        out.append(f"mast shadow: the swept shadow of a {h} mast, 09:00 to 15:00 on the winter solstice, falls on no table at any terminal")
    plot = boundary(project)
    if plot is not None and not plot.is_empty:
        beyond = []
        for t in terms:
            circle = SPoint(t.at.x, t.at.y).buffer(radius, quad_segs=32)
            share = circle.difference(plot).area / circle.area
            if share > 0.01:
                beyond.append(f"{t.tag} {share:.0%}")
        if beyond:
            out.append(f"protection circle beyond the plot: the share of the circle outside the boundary is {', '.join(beyond)}; only the part inside the plot protects the plant, and the circle is not a cover for land outside the boundary")
    return out


def coverage_points(design: LpsDesign, kinds: tuple[str, ...] = ("table",)) -> tuple[int, int]:
    """(covered, total) over targets of `kinds`, recomputed from the terminal positions."""
    pts = [t.at for t in design.targets if t.kind in kinds]
    if not pts:
        return 0, 0
    tp = [(t.at.x, t.at.y) for t in design.terminals]
    ok = sum(1 for p in pts if any(math.dist(p, q) <= design.radius_m + 1e-6 for q in tp))
    return ok, len(pts)


__all__ = [
    "ClassCoverage", "LpsDesign", "Target", "Terminal", "class_coverage", "coverage_points", "design_lps", "equipment_footprint", "is_rooftop",
    "placed_equipment",
]  # fmt: skip

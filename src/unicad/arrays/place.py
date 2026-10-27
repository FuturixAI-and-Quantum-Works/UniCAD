"""Fill buildable regions with tables on a grid aligned to the array azimuth."""

import math
from bisect import bisect_left
from dataclasses import dataclass

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, box
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from unicad.arrays.geometry import frame
from unicad.arrays.spec import FillOrder, PitchInfo, PitchMethod, PlacementSpec, RoadLine, TableGeometry
from unicad.arrays.sun import fixed_tilt_gap, profile_angle_deg, sun_path, tracker_pitch
from unicad.domain.catalog import ModuleSpec
from unicad.domain.layout import Orientation, Point, Table
from unicad.domain.site import SiteGeometry, buildable_area

EPS = 1e-6
STRIP_EPS = 1e-4
INSIDE_TOL = 1e-3
MIN_ROAD_M = 10.0


class PlacementError(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    key: str
    geom: BaseGeometry


@dataclass(frozen=True)
class Placed:
    tables: list[Table]
    pitch: PitchInfo
    full: TableGeometry
    half: TableGeometry | None
    roads: list[RoadLine]
    buildable_m2: float
    target_reached: bool | None
    warnings: list[str]
    assumptions: tuple[str, ...] = ()


def slot_size(spec: PlacementSpec, module: ModuleSpec) -> tuple[float, float]:
    """(along the row, across it) on the sloped plane, each including one module gap."""
    long_m, short_m = module.length / 1000, module.width / 1000
    w, h = (short_m, long_m) if spec.orientation is Orientation.PORTRAIT else (long_m, short_m)
    return w + spec.module_gap_m, h + spec.module_gap_m


def table_geometry(spec: PlacementSpec, module: ModuleSpec, cols: int) -> TableGeometry:
    sw, sh = slot_size(spec, module)
    slope = spec.table_rows * sh
    return TableGeometry(cols=cols, length_m=cols * sw, depth_m=slope * math.cos(math.radians(spec.tilt_deg)), slope_m=slope)


def site_latitude(site: SiteGeometry, spec: PlacementSpec) -> float:
    return spec.latitude_deg if spec.latitude_deg is not None else site.origin.lat


def row_pitch(spec: PlacementSpec, geom: TableGeometry, latitude_deg: float) -> PitchInfo:
    m = spec.pitch_method
    if m is PitchMethod.EXPLICIT:
        assert spec.pitch_m is not None
        pitch, note = spec.pitch_m, f"explicit pitch {spec.pitch_m} m"
        info = PitchInfo(method=m, pitch_m=pitch, note=note)
    elif m is PitchMethod.GCR:
        assert spec.gcr is not None
        pitch = geom.slope_m / spec.gcr
        info = PitchInfo(method=m, pitch_m=pitch, note=f"pitch = collector width {geom.slope_m:.3f} m / GCR {spec.gcr}")
    elif m is PitchMethod.GAP:
        assert spec.row_gap_m is not None
        pitch = geom.depth_m + spec.row_gap_m
        info = PitchInfo(method=m, pitch_m=pitch, note=f"pitch = plan depth {geom.depth_m:.3f} m + row gap {spec.row_gap_m} m")
    else:
        path = sun_path(latitude_deg, spec.shade_start_hour, spec.shade_end_hour)
        window = f"{_hhmm(spec.shade_start_hour)}-{_hhmm(spec.shade_end_hour)} apparent solar time on the winter solstice at latitude {latitude_deg:.4f}"
        if spec.mounting.is_tracker:
            pitch, at = tracker_pitch(geom.slope_m, spec.axis_azimuth_deg, spec.tracker_max_rotation_deg, path)
            note = f"shade-free tracker pitch {window}, true tracking within +/-{spec.tracker_max_rotation_deg} deg, no backtracking"
            shade = None if at is None else round(at.elevation_deg, 3)
        else:
            gap, at = fixed_tilt_gap(geom.slope_m, spec.tilt_deg, spec.azimuth_deg, path)
            pitch = geom.depth_m + gap
            note = f"shade-free fixed-tilt pitch {window}: plan depth + top-edge height / tan(profile angle)"
            shade = None if at is None else round(profile_angle_deg(at, spec.azimuth_deg), 3)
        info = PitchInfo(method=m, pitch_m=pitch, note=note, shade_angle_deg=shade, shade_hour=None if at is None else at.hour)
    min_pitch = geom.slope_m if spec.mounting.is_tracker else geom.depth_m
    if info.pitch_m < min_pitch - EPS:
        raise PlacementError(f"pitch {info.pitch_m:.3f} m is less than the table depth {min_pitch:.3f} m; rows would overlap")
    return info.model_copy(update={"pitch_m": round(info.pitch_m, 4)})


def _hhmm(h: float) -> str:
    return f"{int(h):02d}:{round((h % 1) * 60):02d}"


def regions_for(site: SiteGeometry, spec: PlacementSpec) -> list[Region]:
    area = buildable_area(site, spec.setbacks)
    use_roofs = spec.regions == "roofs" or (spec.regions == "auto" and spec.mounting.on_roof)
    if use_roofs:
        return [Region(k, g) for k, g in area.roofs.items() if not g.is_empty]
    return [] if area.ground.is_empty else [Region("", area.ground)]


def _bad_intervals(geom: BaseGeometry, x0: float, x1: float, b0: float, b1: float) -> list[tuple[float, float]]:
    strip = box(x0, b0 + STRIP_EPS, x1, b1 - STRIP_EPS)
    outside = strip.difference(geom)
    parts = getattr(outside, "geoms", [outside])
    spans = sorted((p.bounds[0], p.bounds[2]) for p in parts if not p.is_empty and p.area > 0)
    merged: list[tuple[float, float]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def _fits(bad: list[tuple[float, float]], his: list[float], a0: float, a1: float) -> bool:
    i = bisect_left(his, a0 + EPS)
    return i >= len(bad) or bad[i][0] >= a1 - EPS


def _road_gap(spec: PlacementSpec, depth: float, pitch: float) -> float:
    return max(pitch, depth + spec.road_width_m)


ROOF_ROW_WALKWAY_EVERY = 2


def row_walkway(spec: PlacementSpec, depth: float, pitch: float) -> int | None:
    """Rows between maintenance walkways: as given, else on a roof ROOF_ROW_WALKWAY_EVERY when the gap between rows is narrower than a walkway."""
    if spec.row_walkway_every_rows:
        return spec.row_walkway_every_rows
    if spec.mounting.on_roof and not spec.road_every_rows and pitch - depth < spec.row_walkway_m - EPS:
        return ROOF_ROW_WALKWAY_EVERY
    return None


def _row_starts(spec: PlacementSpec, b_min: float, b_max: float, depth: float, pitch: float) -> list[float]:
    out, b, k = [], b_min, 0
    walk = row_walkway(spec, depth, pitch)
    while b + depth <= b_max + EPS:
        out.append(b)
        k += 1
        if spec.road_every_rows and k % spec.road_every_rows == 0:
            b += _road_gap(spec, depth, pitch)
        elif walk and k % walk == 0:
            b += max(pitch, depth + spec.row_walkway_m)
        else:
            b += pitch
    return out


def _col_starts(spec: PlacementSpec, a_min: float, a_max: float, length: float) -> list[float]:
    out, a, j = [], a_min, 0
    while a <= a_max + EPS:
        out.append(a)
        j += 1
        gap = spec.table_gap_m
        if spec.walkway_every_tables and j % spec.walkway_every_tables == 0:
            gap = max(gap, spec.walkway_width_m)
        a += length + gap
    return out


@dataclass(frozen=True)
class _Candidate:
    region: str
    row: int
    col: int
    part: int
    a: float
    b: float
    cols: int


def place_tables(site: SiteGeometry, spec: PlacementSpec, module: ModuleSpec) -> Placed:
    full = table_geometry(spec, module, spec.table_cols)
    half = table_geometry(spec, module, spec.half_table_cols) if spec.half_tables else None
    pitch = row_pitch(spec, full, site_latitude(site, spec))
    regions = regions_for(site, spec)
    warnings: list[str] = []
    if not regions:
        raise PlacementError("no buildable area for this mounting: import a site with a boundary or roofs, or relax the setbacks")
    (ux, uy), (vx, vy) = frame(spec.table_azimuth_deg)
    depth = full.slope_m if spec.mounting.is_tracker else full.depth_m
    candidates: list[_Candidate] = []
    roads: list[RoadLine] = []
    row_starts: dict[str, list[float]] = {}
    for r in regions:
        local = affinity.affine_transform(r.geom, [ux, uy, vx, vy, 0, 0])
        a_min, b_min, a_max, b_max = local.bounds
        rows = _row_starts(spec, b_min, b_max, depth, pitch.pitch_m)
        cols = _col_starts(spec, a_min, a_max, full.length_m)
        for i, b in enumerate(rows):
            bad = _bad_intervals(local, a_min - 1, cols[-1] + full.length_m + 1, b, b + depth)
            his = [hi for _, hi in bad]
            for j, a in enumerate(cols):
                if _fits(bad, his, a, a + full.length_m):
                    candidates.append(_Candidate(r.key, i, j, 0, a, b, full.cols))
                elif half is not None:
                    left_end = None
                    if _fits(bad, his, a, a + half.length_m):
                        candidates.append(_Candidate(r.key, i, j, 1, a, b, half.cols))
                        left_end = a + half.length_m
                    right = a + full.length_m - half.length_m
                    if (left_end is None or right >= left_end + spec.table_gap_m - EPS) and _fits(bad, his, right, a + full.length_m):
                        candidates.append(_Candidate(r.key, i, j, 2, right, b, half.cols))
        row_starts[r.key] = rows

    chosen, reached = _select(candidates, spec, module, (ux, uy), (vx, vy), full, depth)
    for r in regions:
        roads += _internal_roads(spec, r.geom, row_starts[r.key], [c for c in chosen if c.region == r.key], full, depth, (ux, uy), (vx, vy))
    sw, sh = slot_size(spec, module)
    tables = []
    for c in chosen:
        prefix = f"{c.region}-" if c.region else ""
        suffix = f"-{c.part}" if c.part else ""
        ox, oy = c.a * ux + c.b * vx, c.a * uy + c.b * vy
        tables.append(Table(
            id=f"{prefix}R{c.row + 1:03d}C{c.col + 1:03d}{suffix}", mounting=spec.mounting.mounting, origin=Point(x=round(ox, 4), y=round(oy, 4)),
            azimuth_deg=spec.table_azimuth_deg, tilt_deg=spec.tilt_deg, rows=spec.table_rows, cols=c.cols, orientation=spec.orientation,
            module=module.id, slot_width_m=round(sw, 6), slot_height_m=round(sh, 6),
        ))  # fmt: skip
    if spec.target_dc_kwp is not None and not reached:
        got = sum(t.slot_count for t in tables) * module.pmax / 1000
        warnings.append(f"target {spec.target_dc_kwp} kWp not reached: the buildable area holds {got:.1f} kWp")
    buildable = sum(r.geom.area for r in regions)
    notes = ()
    if (walk := row_walkway(spec, depth, pitch.pitch_m)) and not spec.row_walkway_every_rows:
        notes = (
            f"maintenance walkway {spec.row_walkway_m:g} m wide after every {walk} rows: the rows stand {max(0.0, pitch.pitch_m - depth):.2f} m apart, too close to walk between, "
            f"so every row is reached from a walkway on one side (design assumption for cleaning and maintenance access, not a cited figure; set row_walkway_every_rows "
            "and row_walkway_m to change it)",
        )
    return Placed(tables, pitch, full, half, roads, buildable, reached, warnings, notes)


def _select(
    candidates: list[_Candidate], spec: PlacementSpec, module: ModuleSpec, u: tuple[float, float], v: tuple[float, float],
    full: TableGeometry, depth: float,
) -> tuple[list[_Candidate], bool | None]:  # fmt: skip
    if spec.target_dc_kwp is None:
        return candidates, None
    if spec.fill is FillOrder.NEAREST:
        assert spec.fill_from is not None
        fx, fy = spec.fill_from.x, spec.fill_from.y

        def dist(c: _Candidate) -> tuple[float, str, int, int, int]:
            ca, cb = c.a + c.cols * full.length_m / full.cols / 2, c.b + depth / 2
            x, y = ca * u[0] + cb * v[0], ca * u[1] + cb * v[1]
            return (round(math.hypot(x - fx, y - fy), 6), c.region, c.row, c.col, c.part)

        ordered = sorted(candidates, key=dist)
    else:
        ordered = candidates
    kw_per_module = module.pmax / 1000
    target = spec.target_dc_kwp
    total, chosen = 0.0, []
    for c in ordered:
        kw = c.cols * spec.table_rows * kw_per_module
        if total + kw <= target + EPS:
            chosen.append(c)
            total += kw
    order = {id(c): i for i, c in enumerate(candidates)}
    chosen.sort(key=lambda c: order[id(c)])
    smallest = min((c.cols for c in candidates), default=spec.table_cols) * spec.table_rows * kw_per_module
    return chosen, target - total < smallest - EPS


def _internal_roads(
    spec: PlacementSpec, geom: BaseGeometry, rows: list[float], chosen: list[_Candidate], full: TableGeometry, depth: float,
    u: tuple[float, float], v: tuple[float, float],
) -> list[RoadLine]:  # fmt: skip
    """Centrelines of the road gaps left every `road_every_rows` rows, spanning the tables on either side and clipped to the region."""
    if not spec.road_every_rows:
        return []
    by_row: dict[int, list[_Candidate]] = {}
    for c in chosen:
        by_row.setdefault(c.row, []).append(c)
    unit = full.length_m / full.cols
    prepared = prep(geom)
    out = []
    for k in range(spec.road_every_rows, len(rows), spec.road_every_rows):
        beside = by_row.get(k - 1, []) + by_row.get(k, [])
        if not beside:
            continue
        a0 = min(c.a for c in beside) - spec.table_gap_m
        a1 = max(c.a + c.cols * unit for c in beside) + spec.table_gap_m
        b_end = rows[k - 1] + depth
        width = rows[k] - b_end
        b = b_end + width / 2
        line = LineString([(a * u[0] + b * v[0], a * u[1] + b * v[1]) for a in (a0, a1)])
        if not prepared.intersects(line):
            continue
        clipped = geom.intersection(line)
        parts = clipped.geoms if isinstance(clipped, MultiLineString) else [clipped]
        for p in parts:
            if isinstance(p, LineString) and p.length >= MIN_ROAD_M:
                out.append(RoadLine(points=tuple(Point(x=round(x, 3), y=round(y, 3)) for x, y in p.coords), width_m=round(width, 3)))
    return out


def inside_problems(tables: list[Table], regions: list[Region], footprint) -> list[str]:
    """Ids of tables whose footprint is not wholly inside any region (tolerance INSIDE_TOL)."""
    shapes = [prep(r.geom.buffer(INSIDE_TOL)) for r in regions]
    return [t.id for t in tables if not any(s.contains(footprint(t)) for s in shapes)]


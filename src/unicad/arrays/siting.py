"""Where equipment may stand on the site: a free spot off tables, roads, exclusions and water, and the one switchyard location."""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from shapely.geometry import LineString, Polygon
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from unicad.arrays.geometry import table_corners
from unicad.domain.layout import Located, Point, Table
from unicad.domain.site import FeatureKind, SiteFeature, SiteGeometry, shape_of

STATION_CLEAR_M = 1.5
ROOF_CLEAR_M = 0.4
PANEL_OFFSET_M = 6.0
SPOT_ASSUMPTION = (
    f"Ground equipment (inverter stations, switchyard, BESS units) keeps {STATION_CLEAR_M:g} m clear of tables and roads, keeps the exclusion setback from "
    "exclusions, water and line corridors as the tables do, and stands inside the buildable area (assumed clearance for access, not a cited figure)."
)
SWITCHYARD_NAME = re.compile(r"switch ?yard|sub ?station|pooling", re.IGNORECASE)
SWITCHYARD_ASSUMED_M = (60.0, 45.0)
RESERVED_YARD_ID = "mv-yard-reserve"


def _xy(p: Point) -> tuple[float, float]:
    return (p.x, p.y)


def marked_switchyard(site: SiteGeometry | None) -> tuple[str, Polygon] | None:
    """A site area marked for the switchyard: kind 'switchyard', attribute use=switchyard, or a name or id that says switchyard, substation or pooling."""
    if site is None:
        return None
    for f in site.features:
        if f.polygon and (f.kind is FeatureKind.SWITCHYARD or f.attributes.get("use") == "switchyard" or SWITCHYARD_NAME.search(f.name or "") or SWITCHYARD_NAME.search(f.id)):
            return f.name or f.id, shape_of(f)
    return None


def occupied(site: SiteGeometry, tables: Sequence[Table], roads: Sequence[LineString] = (), road_width_m: float = 0.0) -> BaseGeometry:
    """Tables, internal roads (centrelines of `road_width_m`) and site roads or lines with no width (as lines)."""
    parts: list[BaseGeometry] = [Polygon(table_corners(t)) for t in tables]
    parts += [r.buffer(road_width_m / 2, cap_style="flat") if road_width_m else r for r in roads]
    parts += [shape_of(f) for f in site.features if f.polyline and f.width_m is None]
    return unary_union(parts)


ROAD_WIDTH_ASSUMED_M = 6.0
"""Width taken for a site road drawn without one: layout_place's default internal road width, which it also gives such roads when placing tables."""


def keep_off_m(site: SiteGeometry, f: SiteFeature) -> float:
    """The clearance ground equipment keeps from a site feature: its own setback_m, else the site's exclusion setback (obstacle setback for
    obstacles), as tables keep. Roads and roofs keep none: a station may stand in the strip beside a road that tables keep clear of."""
    if f.kind in (FeatureKind.ROAD, FeatureKind.ROOF):
        return 0.0
    if f.setback_m is not None:
        return f.setback_m
    return site.setbacks.obstacle_m if f.kind is FeatureKind.OBSTACLE else site.setbacks.exclusion_m


def equipment_ground(site: SiteGeometry) -> BaseGeometry:
    """Where ground equipment may stand: inside the boundary, off roads (their width, ROAD_WIDTH_ASSUMED_M for a road drawn without one),
    roofs, and exclusions, water, buildings, obstacles and line corridors by their setback (keep_off_m)."""
    inside = unary_union([shape_of(f) for f in site.of_kind(FeatureKind.BOUNDARY)])
    off = []
    for f in site.features:
        if f.kind in (FeatureKind.BOUNDARY, FeatureKind.SETBACK_LINE, FeatureKind.SWITCHYARD):
            continue
        g, clear = shape_of(f), keep_off_m(site, f)
        width = f.width_m or (ROAD_WIDTH_ASSUMED_M if f.kind is FeatureKind.ROAD else None)
        if f.polyline and width:
            g = g.buffer(width / 2 + clear, cap_style="flat", join_style="mitre")
        elif clear:
            g = g.buffer(clear, join_style="mitre")
        off.append(g)
    return inside.difference(unary_union(off)) if off else inside


def room(site: SiteGeometry, taken: BaseGeometry, clear_m: float = STATION_CLEAR_M) -> BaseGeometry:
    """The equipment ground that keeps `clear_m` from `taken`, from roads, exclusions and water, and from the boundary."""
    out = equipment_ground(site).buffer(-clear_m)
    return out if taken.is_empty else out.difference(taken.buffer(clear_m))


def nearest_in(area: BaseGeometry, target: tuple[float, float]) -> Point | None:
    if area.is_empty:
        return None
    q = nearest_points(area, ShapelyPoint(target))[0]
    return Point(x=round(q.x, 3), y=round(q.y, 3))


def free_spot(site: SiteGeometry, target: tuple[float, float], taken: BaseGeometry, clear_m: float = STATION_CLEAR_M) -> Point | None:
    """The point of `room` nearest `target`; None when there is none."""
    return nearest_in(room(site, taken, clear_m), target)


def roof_spot(roof: Polygon, target: tuple[float, float], tables: Sequence[Table], clear_m: float = ROOF_CLEAR_M) -> Point | None:
    """The point of `roof` nearest `target` that keeps `clear_m` from the roof edge and from every table on it."""
    room = roof.buffer(-clear_m)
    on = [Polygon(table_corners(t)) for t in tables]
    if on:
        room = room.difference(unary_union(on).buffer(clear_m))
    if room.is_empty:
        return None
    q = nearest_points(room, ShapelyPoint(target))[0]
    return Point(x=round(q.x, 3), y=round(q.y, 3))


def outside_roof(roof: Polygon, toward: tuple[float, float], offset: float) -> tuple[float, float]:
    """The ground point `offset` m outside the roof edge nearest `toward`, square to that edge."""
    edge = nearest_points(roof.exterior, ShapelyPoint(toward))[0]
    sides = [LineString([a, b]) for a, b in zip(roof.exterior.coords, roof.exterior.coords[1:])]
    side = min(sides, key=lambda s: s.distance(edge))
    (ax, ay), (bx, by) = side.coords
    nx, ny = by - ay, -(bx - ax)
    ln = math.hypot(nx, ny) or 1.0
    nx, ny = nx / ln, ny / ln
    c = roof.centroid
    if nx * (edge.x - c.x) + ny * (edge.y - c.y) < 0:
        nx, ny = -nx, -ny
    return (edge.x + nx * offset, edge.y + ny * offset)


@dataclass(frozen=True)
class ReservedYard:
    """A ground switchyard footprint kept free of tables before they are placed: the carve (an exclusion) and the stored location."""

    feature: SiteFeature
    located: Located


def reserve_switchyard(
    site: SiteGeometry, target: Point, why: str, size_m: tuple[float, float] | None, given: bool = False
) -> ReservedYard | None:  # fmt: skip
    """The switchyard footprint (`size_m`, else SWITCHYARD_ASSUMED_M) centred on the given point, or on the free ground nearest `target` that holds it
    clear of exclusions, water and line corridors by their setback; None when no free ground holds it. Tables are then placed around it, so the
    earth mat, the BESS clearance and the MV feeder routes all use this one location."""
    lx, ly = size_m or SWITCHYARD_ASSUMED_M
    if given:
        at = target
    else:
        at = nearest_in(room(site, Polygon(), math.hypot(lx, ly) / 2), (target.x, target.y))
        if at is None:
            return None
    size = "switchyard_m" if size_m else "assumed size: set switchyard_m and re-run layout_place"
    ring = ((at.x - lx / 2, at.y - ly / 2), (at.x + lx / 2, at.y - ly / 2), (at.x + lx / 2, at.y + ly / 2), (at.x - lx / 2, at.y + ly / 2))
    feature = SiteFeature(id=RESERVED_YARD_ID, kind=FeatureKind.EXCLUSION, polygon=tuple(Point(x=x, y=y) for x, y in ring))
    where = why if given else f"derived: the free ground {why}, clear of exclusions and line corridors by their setback (pass switchyard_x/y for the real position)"
    return ReservedYard(feature, Located(at=at, basis=f"{where}; a {lx:g} x {ly:g} m yard ({size}) is kept free of tables"))


RESERVED_BESS_ID = "bess-area-reserve"


def reserve_bess(site: SiteGeometry, yard: BaseGeometry, size_m: tuple[float, float], gap_m: float, basis: str) -> ReservedYard | None:
    """A BESS area of `size_m` kept free of tables at the free ground nearest the switchyard `yard`, `gap_m` clear of it and clear of roads,
    exclusions, water and line corridors by their setback; None when no free ground holds it. The carve keeps tables far enough from its
    centre that network_autobuild's BESS grid (centred at the free spot nearest the switchyard, half its diagonal clear of tables) lands there."""
    lx, ly = size_m
    reach = math.hypot(lx, ly) / 2
    c = yard.centroid
    at = nearest_in(room(site, yard.buffer(gap_m, join_style="mitre"), reach), (c.x, c.y))
    if at is None:
        return None
    ring = ((at.x - lx / 2, at.y - ly / 2), (at.x + lx / 2, at.y - ly / 2), (at.x + lx / 2, at.y + ly / 2), (at.x - lx / 2, at.y + ly / 2))
    clear = reach - min(lx, ly) / 2 + STATION_CLEAR_M
    feature = SiteFeature(id=RESERVED_BESS_ID, kind=FeatureKind.EXCLUSION, polygon=tuple(Point(x=x, y=y) for x, y in ring), setback_m=round(clear, 3))
    return ReservedYard(feature, Located(at=at, basis=f"a {lx:g} x {ly:g} m BESS area ({basis}) is kept free of tables at the free ground nearest the switchyard, {gap_m:g} m clear of it"))


def switchyard(
    site: SiteGeometry, stations: Sequence[Point], tables: Sequence[Table], on_roofs: bool, given: Point | None = None,
    size_m: tuple[float, float] | None = None, roads: Sequence[LineString] = (), road_width_m: float = 0.0,
) -> Located | None:  # fmt: skip
    """The one location of the MV switchyard (main LT panel on an LT plant): given, else the centre of a site area marked for it, else derived.

    Derived on a rooftop plant: on the ground PANEL_OFFSET_M outside the roof edge nearest the first block station. On the ground: the free
    spot nearest the site's transmission line (where the export line leaves) or, without one, nearest the centroid of the block stations,
    clear of tables and roads by half the switchyard diagonal (switchyard_m) or STATION_CLEAR_M."""
    if given is not None:
        return Located(at=given, basis="given (layout_place switchyard_x, switchyard_y)")
    marked = marked_switchyard(site)
    if marked is not None:
        name, poly = marked
        c = poly.representative_point() if not poly.contains(poly.centroid) else poly.centroid
        return Located(at=Point(x=round(c.x, 3), y=round(c.y, 3)), basis=f"centre of the switchyard area {name!r} marked on the site")
    if not stations:
        return None
    if on_roofs:
        roofs = [shape_of(f) for f in site.of_kind(FeatureKind.ROOF) if f.polygon]
        ref = _xy(stations[0])
        if roofs:
            roof = min(roofs, key=lambda r: r.distance(ShapelyPoint(ref)))
            x, y = outside_roof(roof, ref, PANEL_OFFSET_M)
            return Located(at=Point(x=round(x, 3), y=round(y, 3)), basis=f"derived: on the ground {PANEL_OFFSET_M:g} m outside the roof edge nearest the BLOCK-01 station (assumed electrical room position; pass switchyard_x/y for the real one)")
    cx, cy = sum(p.x for p in stations) / len(stations), sum(p.y for p in stations) / len(stations)
    lines = [shape_of(f) for f in site.of_kind(FeatureKind.TRANSMISSION_LINE)]
    if lines:
        line = min(lines, key=lambda g: g.distance(ShapelyPoint(cx, cy)))
        q = nearest_points(line, ShapelyPoint(cx, cy))[0]
        target, why = (q.x, q.y), "nearest the transmission line on the site (where the export line leaves)"
    else:
        target, why = (cx, cy), f"nearest the centroid of the {len(stations)} block station(s)"
    clear = math.hypot(*size_m) / 2 if size_m else STATION_CLEAR_M
    spot = free_spot(site, target, occupied(site, tables, roads, road_width_m), clear)
    if spot is None:
        spot = Point(x=round(target[0], 3), y=round(target[1], 3))
        why += "; no free area is large enough, so it stands at that point"
    return Located(at=spot, basis=f"derived: the free spot {why}, clear of tables, roads and exclusions by {clear:.1f} m (pass switchyard_x/y for the real position)")

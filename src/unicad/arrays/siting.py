"""Where equipment may stand on the site: a free spot off tables, roads, exclusions and water, and the one switchyard location."""

import math
import re
from collections.abc import Sequence

from shapely.geometry import LineString, Polygon
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from unicad.arrays.geometry import table_corners
from unicad.domain.layout import Located, Point, Table
from unicad.domain.site import FeatureKind, SiteGeometry, shape_of

STATION_CLEAR_M = 1.5
ROOF_CLEAR_M = 0.4
PANEL_OFFSET_M = 6.0
SPOT_ASSUMPTION = (
    f"Ground equipment (inverter stations, switchyard, BESS units) keeps {STATION_CLEAR_M:g} m clear of tables, roads, exclusions and water and stands "
    "inside the buildable area (assumed clearance for access, not a cited figure)."
)
SWITCHYARD_NAME = re.compile(r"switch ?yard|sub ?station|pooling", re.IGNORECASE)


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


def equipment_ground(site: SiteGeometry) -> BaseGeometry:
    """Where ground equipment may stand: inside the boundary, off roads (their width), exclusions, water, buildings, obstacles, roofs and line
    corridors. Array setbacks do not apply: a station may stand in the strip beside a road that tables keep clear of."""
    inside = unary_union([shape_of(f) for f in site.of_kind(FeatureKind.BOUNDARY)])
    off = []
    for f in site.features:
        if f.kind in (FeatureKind.BOUNDARY, FeatureKind.SETBACK_LINE):
            continue
        g = shape_of(f)
        if f.polyline:
            off.append(g.buffer((f.width_m or 0.0) / 2, cap_style="flat") if f.width_m else g)
        elif f.kind is not FeatureKind.SWITCHYARD:
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

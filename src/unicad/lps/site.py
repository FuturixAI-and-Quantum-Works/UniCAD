"""Site facts the lightning and earthing designs share: roofs, boundary, blocked ground and where the MV switchyard stands."""

import math

from shapely.geometry import Point as SPoint
from shapely.geometry import Polygon
from shapely.ops import nearest_points, unary_union

from unicad.arrays.geometry import table_corners
from unicad.arrays.siting import marked_switchyard
from unicad.domain.layout import Mounting
from unicad.domain.project import Project, Segment
from unicad.domain.site import FeatureKind, shape_of

ROOFTOP_SEGMENTS = frozenset({Segment.RESIDENTIAL, Segment.CI_ROOFTOP})
Rect = tuple[float, float, float, float]


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


def designated_switchyard(project: Project) -> Polygon | None:
    """A site area marked for the switchyard: kind 'switchyard', attribute use=switchyard, or a name or id that says switchyard, substation or pooling."""
    marked = marked_switchyard(project.site.geometry)
    return None if marked is None else marked[1]


def place_switchyard(project: Project, size: tuple[float, float], near: tuple[float, float] | None) -> tuple[Rect | None, str | None]:
    """The switchyard grid rectangle: inside a designated switchyard area if the site has one, else the free area nearest `near` (the layout's
    switchyard location, so the grid is drawn where cable routing takes the switchyard to be)."""
    marked = designated_switchyard(project)
    if marked is not None:
        x0, y0, x1, y1 = marked.buffer(-2.0).bounds
        return (x0, y0, x1, y1), None
    site = boundary(project)
    if site is None:
        return None, "no site boundary: the switchyard grid is not placed"
    layout = project.array_layout
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()]) if layout is not None else Polygon()
    free = site.buffer(-3.0).difference(tables.buffer(5.0)).difference(blocked(project).buffer(3.0))
    lx, ly = size
    room = free.buffer(-math.hypot(lx, ly) / 2)
    target = SPoint(near) if near is not None else site.centroid
    if room.is_empty:
        return None, f"no free area of {lx:g} x {ly:g} m inside the site for the switchyard earth grid; it is not drawn"
    c = nearest_points(room, target)[0]
    return (c.x - lx / 2, c.y - ly / 2, c.x + lx / 2, c.y + ly / 2), None


def switchyard_near(project: Project) -> tuple[float, float] | None:
    """Where the switchyard grid is wanted: the layout's one switchyard location, else the centroid of the block stations."""
    layout = project.array_layout
    if layout is None:
        return None
    if layout.switchyard is not None:
        return (layout.switchyard.at.x, layout.switchyard.at.y)
    stations = [(b.station.x, b.station.y) for b in layout.blocks if b.station is not None]
    return (sum(p[0] for p in stations) / len(stations), sum(p[1] for p in stations) / len(stations)) if stations else None


def switchyard_footprint(project: Project) -> Rect | None:
    """The MV switchyard earth grid rectangle of a ground plant whose design basis gives its size or whose site marks its area; else None."""
    if is_rooftop(project) or project.array_layout is None:
        return None
    basis = project.design_basis.earthing
    size = basis.switchyard_m if basis is not None else None
    if size is None and designated_switchyard(project) is None:
        return None
    return place_switchyard(project, size or (0.0, 0.0), switchyard_near(project))[0]

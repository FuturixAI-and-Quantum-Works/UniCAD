"""Site facts the lightning and earthing designs share: roofs, boundary, blocked ground and where the MV switchyard stands."""

from shapely.geometry import Point as SPoint
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

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


def place_switchyard(project: Project, size: tuple[float, float]) -> tuple[Rect | None, str | None]:
    """The switchyard grid rectangle: inside a designated switchyard area if the site has one, else `size` centred on the layout's one switchyard
    location (where cable routing measures the MV feeders to and layout_place kept the yard free of tables). It never moves: when the
    footprint there overlaps tables, exclusions or placed BESS units, the problem says so and how to fix it."""
    from unicad.rules.structure import BESS_PLACED

    marked = designated_switchyard(project)
    if marked is not None:
        x0, y0, x1, y1 = marked.buffer(-2.0).bounds
        return (x0, y0, x1, y1), None
    layout = project.array_layout
    if layout is None or layout.switchyard is None:
        return None, "the layout has no switchyard location: run layout_place (switchyard_x/y, or derived) so the switchyard grid is placed where routing takes it"
    lx, ly = size
    at = layout.switchyard.at
    rect = (at.x - lx / 2, at.y - ly / 2, at.x + lx / 2, at.y + ly / 2)
    mat = box(*rect)
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()])
    hits = []
    if mat.intersects(tables):
        hits.append("tables")
    if mat.intersects(blocked(project)):
        hits.append("an exclusion, road or line corridor")
    if any(mat.contains(SPoint(e.at.x, e.at.y)) for e in layout.equipment if e.kind in BESS_PLACED):
        hits.append("BESS units")
    if hits:
        return rect, (
            f"the {lx:g} x {ly:g} m switchyard grid centred on the layout's switchyard location ({at.x:.1f}, {at.y:.1f}) overlaps {' and '.join(hits)}: "
            "re-run layout_place after setting switchyard_m (it keeps that footprint free of tables), or pass switchyard_x/y for a free spot"
        )
    return rect, None


def switchyard_footprint(project: Project) -> Rect | None:
    """The MV switchyard earth grid rectangle of a ground plant whose design basis gives its size or whose site marks its area; else None."""
    if is_rooftop(project) or project.array_layout is None:
        return None
    basis = project.design_basis.earthing
    size = basis.switchyard_m if basis is not None else None
    if size is None and designated_switchyard(project) is None:
        return None
    return place_switchyard(project, size or (0.0, 0.0))[0]

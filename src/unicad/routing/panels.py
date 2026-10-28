"""Cable routes between the LT panels the layout places on a ground plant: the ACCB and the client panel."""

from dataclasses import dataclass

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from unicad.arrays.geometry import frame, table_corners
from unicad.domain.layout import PlacedEquipment
from unicad.domain.network import NodeKind
from unicad.domain.project import Project
from unicad.routing.locate import CO_LOCATED_M, Locator, manhattan, straight
from unicad.routing.model import RouteAssumptions

PANEL_KINDS = frozenset({NodeKind.ACDB.value, NodeKind.LT_PANEL.value, NodeKind.POI_GRID.value})


@dataclass(frozen=True)
class PanelRoute:
    edge: str
    from_tag: str
    to_tag: str
    points: tuple[tuple[float, float], ...]
    length_m: float


def live_panels(project: Project) -> list[PlacedEquipment]:
    """The LT panels the layout places that are still nodes of the network (a panel removed since the equipment was placed is not one)."""
    layout = project.array_layout
    nodes = {n.id for n in project.network.nodes}
    return [e for e in layout.equipment if e.kind in PANEL_KINDS and e.node.split("@")[0] in nodes] if layout is not None else []


def route_points(a: tuple[float, float], b: tuple[float, float], azimuth_deg: float, tables: Polygon | None = None) -> tuple[tuple[float, float], ...]:
    """A route from `a` to `b` along the array axes: along the table rows then across them, or across then along, whichever crosses the tables less."""
    (ux, uy), (vx, vy) = frame(azimuth_deg)
    dx, dy = b[0] - a[0], b[1] - a[1]
    du, dv = dx * ux + dy * uy, dx * vx + dy * vy
    along = (a[0] + du * ux, a[1] + du * uy)
    across = (a[0] + dv * vx, a[1] + dv * vy)
    options = [(a, along, b), (a, across, b)]
    if tables is not None and not tables.is_empty:
        options.sort(key=lambda o: LineString(o).intersection(tables).length)
    best = options[0]
    pts = [best[0], best[1], best[2]] if min(abs(du), abs(dv)) > 1e-6 else [a, b]
    return tuple((round(x, 3), round(y, 3)) for x, y in pts)


def panel_routes(project: Project) -> list[PanelRoute]:
    """The cabled power edges that join two LT panels placed in the field (or the equipment standing at them), routed outside the tables where
    the axes allow."""
    layout = project.array_layout
    if layout is None or not layout.equipment:
        return []
    panels = live_panels(project)
    if len(panels) < 2:
        return []
    expanded = project.network.expand()
    loc = Locator(layout, expanded, RouteAssumptions())
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()])
    azimuth = next(iter(loc.block_frame.values()), 180.0)

    def panel_at(node: str):
        p = loc.location(node)
        if p is None:
            return None
        return next((e for e in panels if straight(p.xy, (e.at.x, e.at.y)) < CO_LOCATED_M), None)

    out = []
    for e in expanded.edges:
        if not e.is_power or (e.cable is None and e.laying is None):
            continue
        a, b = panel_at(e.src_node), panel_at(e.dst_node)
        if a is None or b is None or a.node == b.node:
            continue
        pa, pb = (a.at.x, a.at.y), (b.at.x, b.at.y)
        out.append(PanelRoute(e.id, a.tag, b.tag, route_points(pa, pb, azimuth, tables), round(manhattan(pa, pb, azimuth), 3)))
    return out

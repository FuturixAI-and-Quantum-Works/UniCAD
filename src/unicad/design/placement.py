"""Where the network's field equipment stands: string inverters at the end of their table rows, AC-coupled BESS units in a stated or free area."""

import math
from collections import defaultdict

from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from unicad.arrays import siting
from unicad.arrays.geometry import table_corners
from unicad.catalog.store import Catalog
from unicad.domain.catalog import InverterSpec
from unicad.domain.layout import ArrayLayout, PlacedEquipment, Point
from unicad.domain.network import ElectricalNetwork, NodeKind
from unicad.domain.project import Project
from unicad.domain.site import ROOF_CARVE_KINDS, FeatureKind, shape_of
from unicad.routing import RouteAssumptions
from unicad.routing.locate import Locator
from unicad.rules.structure import BESS_SEPARATION_M, BESS_SEPARATION_SOURCE

INVERTER_CLEAR_M = 0.5
INVERTER_SPACING_M = 1.0
BESS_PITCH_M = 10.0
FIELD_INVERTERS = (
    f"String inverters stand in the field beside the tables they serve: at the free spot (row or table gap, road side; on a roof, on that roof) nearest the "
    f"centre of their strings, {INVERTER_CLEAR_M:g} m clear of tables, roof edges and obstacles and {INVERTER_SPACING_M:g} m from each other (structure-mounted "
    "practice, which keeps DC string runs short; the clearances are assumptions); their AC cables are routed from there to the block's AC combiner at the station."
)
BESS_SPACING = (
    f"BESS units (battery containers, PCS and BESS transformer of each block) stand on a grid {BESS_PITCH_M:g} m centre to centre, one row per BESS block "
    "(assumed spacing for access and fire separation; not from the datasheets or a cited standard; equipment footprints are not modelled)."
)


def _field_inverters(net: ElectricalNetwork, catalog: Catalog, loc: Locator, layout: ArrayLayout, project: Project) -> list[PlacedEquipment]:
    inverters = []
    for n in net.by_kind(NodeKind.INVERTER):
        spec = catalog.get(n.spec) if isinstance(n.spec, str) else n.spec
        if not isinstance(spec, InverterSpec) or spec.topology != "string":
            continue
        if any(net.node(p).kind is NodeKind.SCB for p in net.upstream(n.id)):
            continue
        if loc.strings_below(n.id):
            inverters.append(n)
    if not inverters:
        return []
    site = project.site.geometry
    taken = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()])
    roofs = _roof_room(site, taken) if site is not None else []
    ground = siting.room(site, taken, INVERTER_CLEAR_M) if site is not None else None
    out = []
    for n in inverters:
        strings = loc.strings_below(n.id)
        centres = [loc.strings[s].centre for s in strings]
        on, mine = None, centres
        if roofs:
            by_roof = [[c for c in centres if r.covers(ShapelyPoint(c))] for r, _ in roofs]
            best = max(range(len(roofs)), key=lambda i: (len(by_roof[i]), -i))
            if by_roof[best]:
                on, mine = roofs[best][1], by_roof[best]
        x = sum(c[0] for c in mine) / len(mine)
        y = sum(c[1] for c in mine) / len(mine)
        free, where = (on, "on the roof that carries most of its strings, beside them") if on is not None else (ground, "beside the centre of the tables it serves")
        if free is not None and out:
            free = free.difference(unary_union([ShapelyPoint(e.at.x, e.at.y).buffer(INVERTER_SPACING_M) for e in out]))
        at = (siting.nearest_in(free, (x, y)) if free is not None else None) or Point(x=round(x, 3), y=round(y, 3))
        out.append(PlacedEquipment(node=n.id, tag=n.tag, kind=n.kind.value, at=at, basis=where))
    return out


def _roof_room(site, taken) -> list[tuple[Polygon, BaseGeometry]]:
    """Per roof that carries tables: the roof area clear of its edge, its obstacles and the tables by INVERTER_CLEAR_M."""
    blocked = unary_union([shape_of(f) for f in site.features if f.kind in ROOF_CARVE_KINDS and f.polygon])
    out = []
    for f in site.of_kind(FeatureKind.ROOF):
        if not f.polygon or not (roof := shape_of(f)).intersects(taken):
            continue
        out.append((roof, roof.buffer(-INVERTER_CLEAR_M).difference(unary_union([taken, blocked]).buffer(INVERTER_CLEAR_M))))
    return out


BESS_KINDS = (NodeKind.TRANSFORMER, NodeKind.PCS, NodeKind.BATTERY)
BESS_SEPARATION = f"BESS units stand outside the MV switchyard earth mat, clear of its edge by {BESS_SEPARATION_SOURCE} Confirm with the BESS vendor's fire layout and the CEIG."
ASSUMED_YARD_M = (60.0, 45.0)
YARD_UNKNOWN = (
    f"The switchyard size is not set (switchyard_m): the BESS clearance is kept from an assumed {ASSUMED_YARD_M[0]:g} x {ASSUMED_YARD_M[1]:g} m yard centred on the "
    "switchyard location (design assumption, not a yard layout). Set switchyard_m (project_design_basis earthing) and re-run network_autobuild."
)


def _yard_size(project: Project) -> tuple[float, float] | None:
    e = project.design_basis.earthing
    return e.switchyard_m if e is not None and e.switchyard_m else None


def switchyard_area(project: Project) -> BaseGeometry | None:
    """The MV switchyard footprint: the area marked on the site, else switchyard_m (or the assumed size) centred on the layout's switchyard."""
    layout = project.array_layout
    site = project.site.geometry
    marked = siting.marked_switchyard(site)
    if marked is not None:
        return marked[1]
    if layout is None or layout.switchyard is None:
        return None
    sm = _yard_size(project) or ASSUMED_YARD_M
    sx, sy = layout.switchyard.at.x, layout.switchyard.at.y
    return box(sx - sm[0] / 2, sy - sm[1] / 2, sx + sm[0] / 2, sy + sm[1] / 2)


def _bess_units(net: ElectricalNetwork) -> list[list]:
    """Per BESS block, its transformer then its PCS and battery instances, in network order."""
    units: dict[str, list] = defaultdict(list)
    for n in net.nodes:
        if n.kind is NodeKind.PCS:
            tr = next((d for d in net.downstream(n.id) if net.node(d).kind is NodeKind.TRANSFORMER), None)
            if tr is None:
                continue
            if not units[tr]:
                units[tr].append(net.node(tr))
            units[tr] += [net.node(b) for b in net.upstream(n.id) if net.node(b).kind is NodeKind.BATTERY] + [n]
    return list(units.values())


def _bess(net: ElectricalNetwork, layout: ArrayLayout, project: Project, at: Point | None, taken_extra) -> tuple[list[PlacedEquipment], list[str]]:
    units = _bess_units(net)
    if not units:
        return [], []
    site = project.site.geometry
    cols = max(len(u) for u in units)
    w, h = cols * BESS_PITCH_M, len(units) * BESS_PITCH_M
    notes = [BESS_SPACING]
    if at is not None:
        centre, basis = (at.x, at.y), "BESS area given (bess_x, bess_y)"
    else:
        if site is None:
            return [], ["BESS units are not placed: the project has no site geometry"]
        anchor = layout.switchyard.at if layout.switchyard is not None else next((b.station for b in layout.blocks if b.station is not None), None)
        if anchor is None:
            return [], ["BESS units are not placed: no switchyard or station to place them near"]
        tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()])
        free = siting.room(site, unary_union([tables, *taken_extra]), math.hypot(w, h) / 2)
        spot = siting.nearest_in(free, (anchor.x, anchor.y))
        if spot is None:
            return [], [f"BESS units are not placed: no free area of {w:g} x {h:g} m on the site; pass bess_x and bess_y"]
        centre, basis = (spot.x, spot.y), f"free area nearest the switchyard, clear of tables, roads, exclusions and water ({w:g} x {h:g} m)"
    out = []
    for r, unit in enumerate(units):
        for c, node in enumerate(unit):
            x = centre[0] - w / 2 + (c + 0.5) * BESS_PITCH_M
            y = centre[1] + h / 2 - (r + 0.5) * BESS_PITCH_M
            out.append(PlacedEquipment(node=node.id, tag=node.tag, kind=node.kind.value, at=Point(x=round(x, 3), y=round(y, 3)), basis=basis))
    notes.append(f"BESS: {len(units)} unit row(s) at local ({centre[0]:.1f}, {centre[1]:.1f}) ({basis}).")
    return out, notes


def place_equipment(project: Project, catalog: Catalog, bess_at: Point | None = None) -> tuple[ArrayLayout | None, list[str]]:
    """The project's array layout with the field equipment of its network placed, and the assumptions made. Needs a layout with string links
    for inverters; BESS units need a site or a stated position."""
    layout = project.array_layout
    if layout is None or not layout.blocks:
        return layout, []
    net = project.network.expand()
    notes: list[str] = []
    placed: list[PlacedEquipment] = []
    if layout.strings:
        loc = Locator(layout, net, RouteAssumptions())
        inv = _field_inverters(net, catalog, loc, layout, project)
        if inv:
            placed += inv
            notes.append(FIELD_INVERTERS)
    extra = []
    yard = switchyard_area(project)
    if yard is not None:
        extra.append(yard.buffer(BESS_SEPARATION_M, join_style="mitre"))
    extra += [box(p.at.x - 0.5, p.at.y - 0.5, p.at.x + 0.5, p.at.y + 0.5) for p in placed]
    bess, more = _bess(net, layout, project, bess_at, extra)
    if bess and yard is not None and bess_at is None:
        more.append(BESS_SEPARATION)
        if _yard_size(project) is None:
            more.append(YARD_UNKNOWN)
    placed += bess
    notes += more
    return layout.model_copy(update={"equipment": tuple(placed)}), notes

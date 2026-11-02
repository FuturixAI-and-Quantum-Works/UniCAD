"""Where the network's field equipment stands: string inverters at the end of their table rows, AC-coupled BESS units in a stated or free area."""

import math
from collections import defaultdict

from shapely import contains_xy
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from unicad.arrays import siting
from unicad.arrays.geometry import frame, table_corners
from unicad.catalog.store import Catalog
from unicad.domain.catalog import InverterSpec
from unicad.domain.layout import ArrayLayout, Located, Mounting, PlacedEquipment, Point
from unicad.domain.network import ElectricalNetwork, NodeKind
from unicad.domain.project import Project, Segment
from unicad.domain.site import ROOF_CARVE_KINDS, FeatureKind, buildable_area, shape_of
from unicad.routing import RouteAssumptions
from unicad.routing.locate import Locator, manhattan
from unicad.rules.structure import BESS_SEPARATION_M, BESS_SEPARATION_SOURCE

INVERTER_CLEAR_M = 0.5
INVERTER_SPACING_M = 1.0
SPOT_MARGIN_M = 10.0
SPOT_STEP_U_M = 2.0
SPOT_STEP_V_M = 0.5
RUN_SLACK_M = 2.0
BESS_PITCH_M = 10.0
FIELD_INVERTERS = (
    f"String inverters stand in the field beside the tables they serve: at the free spot (row or table gap, road side; on a roof, on that roof) whose "
    f"longest string run along the array axes is least (of the spots within {RUN_SLACK_M:g} m of it, the one nearest its station), {INVERTER_CLEAR_M:g} m clear of tables, inside the boundary and roof edge setbacks, clear of obstacles "
    f"by their setback (at least {INVERTER_CLEAR_M:g} m) and {INVERTER_SPACING_M:g} m from each other (structure-mounted "
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
    ground = _ground_room(site, taken) if site is not None else None
    out = []
    for n in inverters:
        strings = loc.strings_below(n.id)
        on, mine = None, list(strings)
        if roofs:
            by_roof = [[s for s in strings if r.covers(ShapelyPoint(loc.strings[s].centre))] for r, _ in roofs]
            best = max(range(len(roofs)), key=lambda i: (len(by_roof[i]), -i))
            if by_roof[best]:
                on, mine = roofs[best][1], by_roof[best]
        ends = [e for s in mine for e in (loc.strings[s].plus, loc.strings[s].minus)]
        free, where = (on, "on the roof that carries most of its strings, beside them") if on is not None else (ground, "beside the tables it serves")
        block = loc.block_of_generator(n.id)
        station = loc.station(block) if block is not None else None
        toward = station.xy if station is not None else None
        at = _spot(free, ends, loc.strings[mine[0]].azimuth_deg, [e.at for e in out], toward) if free is not None else None
        if at is None:
            x, y = sum(p[0] for p in ends) / len(ends), sum(p[1] for p in ends) / len(ends)
            at = Point(x=round(x, 3), y=round(y, 3))
        out.append(PlacedEquipment(node=n.id, tag=n.tag, kind=n.kind.value, at=at, basis=where))
    return out


def string_runs(project: Project) -> dict[str, tuple[float, int]]:
    """Per placed field inverter instance: (its longest string run from either end of a string along the array axes, its string count)."""
    layout = project.array_layout
    if layout is None or not layout.strings or not layout.equipment:
        return {}
    loc = Locator(layout, project.network.expand(), RouteAssumptions())
    out = {}
    for e in layout.equipment:
        if e.kind != NodeKind.INVERTER.value or not (strings := loc.strings_below(e.node)):
            continue
        ends = [q for s in strings for q in (loc.strings[s].plus, loc.strings[s].minus)]
        out[e.node] = (_run(ends, (e.at.x, e.at.y), loc.strings[strings[0]].azimuth_deg), len(strings))
    return out


def _run(ends: list[tuple[float, float]], at: tuple[float, float], azimuth_deg: float) -> float:
    return max(manhattan(e, at, azimuth_deg) for e in ends)


def _spot(free: BaseGeometry, ends: list[tuple[float, float]], azimuth_deg: float, others: list[Point], toward: tuple[float, float] | None) -> Point | None:
    """The free point, INVERTER_SPACING_M from the inverters already placed, whose longest string run (from either end of a string, along the array
    axes) is least: the point nearest all the string ends, else the best of a grid of free points about it. Of the points within RUN_SLACK_M of
    that least run, the one nearest `toward` (the station its AC cable runs to) is taken, so the AC run is no longer than it need be."""
    (ux, uy), (vx, vy) = frame(azimuth_deg)
    uv = [(x * ux + y * uy, x * vx + y * vy) for x, y in ends]
    p, q = [u + v for u, v in uv], [u - v for u, v in uv]
    pc, qc = (max(p) + min(p)) / 2, (max(q) + min(q)) / 2
    us, vs = [u for u, _ in uv], [v for _, v in uv]
    cu, cv = (pc + qc) / 2, (pc - qc) / 2
    cu, cv = min(max(cu, min(us)), max(us)), min(max(cv, min(vs)), max(vs))
    span_u, span_v = (max(us) - min(us)) / 2 + SPOT_MARGIN_M, (max(vs) - min(vs)) / 2 + SPOT_MARGIN_M
    corners = [(cu + a * span_u, cv + b * span_v) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    window = Polygon([(u * ux + v * vx, u * uy + v * vy) for u, v in corners])
    target = (cu * ux + cv * vx, cu * uy + cv * vy)
    if others:
        free = free.difference(unary_union([ShapelyPoint(o.x, o.y).buffer(INVERTER_SPACING_M) for o in others]))
    local = free.intersection(window)
    if local.is_empty:
        return siting.nearest_in(free, target)
    pool = [(q.x, q.y) for q in [siting.nearest_in(local, target)] if q is not None]
    nu, nv = int(2 * span_u / SPOT_STEP_U_M) + 1, int(2 * span_v / SPOT_STEP_V_M) + 1
    grid = [(cu - span_u + i * SPOT_STEP_U_M, cv - span_v + j * SPOT_STEP_V_M) for i in range(nu) for j in range(nv)]
    xs, ys = [u * ux + v * vx for u, v in grid], [u * uy + v * vy for u, v in grid]
    pool += [(x, y) for x, y, inside in zip(xs, ys, contains_xy(local, xs, ys), strict=True) if inside]
    runs = [_run(ends, xy, azimuth_deg) for xy in pool]
    least = min(runs)
    near = toward or target
    x, y = min((xy for xy, r in zip(pool, runs, strict=True) if r <= least + RUN_SLACK_M),
               key=lambda xy: (round(manhattan(xy, near, azimuth_deg), 3), math.hypot(xy[0] - target[0], xy[1] - target[1])))
    return Point(x=round(x, 3), y=round(y, 3))


def _ground_room(site, taken) -> BaseGeometry:
    """The equipment ground INVERTER_CLEAR_M clear of tables, roads and exclusions, inside the boundary setback (where the tables may stand)."""
    inside = buildable_area(site).ground
    return siting.room(site, taken, INVERTER_CLEAR_M).intersection(inside.buffer(-INVERTER_CLEAR_M)) if not inside.is_empty else siting.room(site, taken, INVERTER_CLEAR_M)


def _roof_room(site, taken) -> list[tuple[Polygon, BaseGeometry]]:
    """Per roof that carries tables: the roof area clear of its edge (the roof edge setback, at least INVERTER_CLEAR_M), its obstacles (their setback,
    at least INVERTER_CLEAR_M) and the tables by INVERTER_CLEAR_M."""
    blocked = unary_union([shape_of(f).buffer(max(INVERTER_CLEAR_M, siting.keep_off_m(site, f))) for f in site.features if f.kind in ROOF_CARVE_KINDS and f.polygon])
    edge = max(INVERTER_CLEAR_M, site.setbacks.roof_edge_m)
    out = []
    for f in site.of_kind(FeatureKind.ROOF):
        if not f.polygon or not (roof := shape_of(f)).intersects(taken):
            continue
        clear = max(INVERTER_CLEAR_M, f.setback_m) if f.setback_m is not None else edge
        out.append((roof, roof.buffer(-clear, join_style="mitre").difference(unary_union([taken.buffer(INVERTER_CLEAR_M), blocked]))))
    return out


MV_PLANT_KINDS = frozenset({NodeKind.TRANSFORMER, NodeKind.VCB, NodeKind.RMU, NodeKind.HT_PANEL, NodeKind.AUX_TRANSFORMER})
LT_PANEL_SPACING_M = 10.0
LT_PANEL_CLEAR_M = 2.0
CENTRE_OFFSET_M = 10.0
LT_PANELS = (
    f"On a ground LT plant (no MV equipment) the ACCB stands at the layout's switchyard location, which layout_place keeps free of tables, and the client panel (the point "
    f"of connection, else the LT panel) at the free spot nearest it at least {LT_PANEL_SPACING_M:g} m away, {LT_PANEL_CLEAR_M:g} m clear of tables, roads and exclusions and inside the boundary "
    "(assumed spacing for access and cable termination, not a cited figure; the client's panel position is not given). Their cable runs along the array axes, round the tables."
)


def lt_ground_plant(project: Project) -> bool:
    """A ground-mount plant that connects at LT through an AC combiner: no MV equipment in the network, an ACDB and a tabled ground layout."""
    layout, site = project.array_layout, project.site.geometry
    if layout is None or not layout.blocks or project.segment in (Segment.RESIDENTIAL, Segment.CI_ROOFTOP):
        return False
    if any(t.mounting is Mounting.ROOF_FLUSH for t in layout.table_by_id.values()) or (site is not None and site.of_kind(FeatureKind.ROOF)):
        return False
    kinds = {n.kind for n in project.network.nodes}
    return NodeKind.ACDB in kinds and not kinds & MV_PLANT_KINDS


def _lt_panels(net: ElectricalNetwork, layout: ArrayLayout, project: Project) -> tuple[list[PlacedEquipment], list[str]]:
    """The ACCB at the layout's switchyard and the client panel beside it, outside the tables."""
    site = project.site.geometry
    acdb = [n for n in net.nodes if n.kind is NodeKind.ACDB]
    rest = [n for n in net.nodes if n.kind is NodeKind.POI_GRID] or [n for n in net.nodes if n.kind is NodeKind.LT_PANEL]
    if layout.switchyard is None or site is None or not acdb:
        return [], []
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()])
    at = layout.switchyard.at
    out = [PlacedEquipment(node=n.id, tag=n.tag, kind=n.kind.value, at=at, basis=f"the layout's switchyard location ({layout.switchyard.basis.split(';')[0]})") for n in acdb[:1]]
    taken = [(at.x, at.y)]
    for n in acdb[1:] + rest:
        free = siting.room(site, unary_union([tables, *(ShapelyPoint(q).buffer(LT_PANEL_SPACING_M) for q in taken)]), LT_PANEL_CLEAR_M)
        spot = siting.nearest_in(free, taken[0])
        if spot is None:
            return out, [f"{n.tag} is not placed: no free ground {LT_PANEL_SPACING_M:g} m from the other LT panels outside the tables"]
        out.append(PlacedEquipment(node=n.id, tag=n.tag, kind=n.kind.value, at=spot, basis=f"free ground nearest the ACCB, {LT_PANEL_SPACING_M:g} m or more from it and outside the tables"))
        taken.append((spot.x, spot.y))
    return out, [LT_PANELS]


def stations_exist(project: Project) -> bool:
    """Whether any network equipment stands at a block's inverter station. False when the network has equipment and all of it stands in the field
    or at the LT panels (string inverters in the field, their breakers inside one ACCB): the stations the layout holds for each block are indicative
    only and a drawing must not show them as equipment."""
    layout = project.array_layout
    if layout is None or not layout.blocks or not project.network.nodes:
        return True
    expanded = project.network.expand()
    loc = Locator(layout, expanded, RouteAssumptions())
    return any((p := loc.location(n.id)) is not None and p.where.startswith("station ") for n in expanded.nodes)


BESS_KINDS = (NodeKind.TRANSFORMER, NodeKind.PCS, NodeKind.BATTERY)
BESS_SEPARATION = f"BESS units stand outside the MV switchyard earth mat, clear of its edge by {BESS_SEPARATION_SOURCE} Confirm with the BESS vendor's fire layout and the CEIG."
ASSUMED_YARD_M = (60.0, 45.0)
YARD_UNKNOWN = (
    f"The switchyard size is not set (switchyard_m): the BESS clearance is kept from an assumed {ASSUMED_YARD_M[0]:g} x {ASSUMED_YARD_M[1]:g} m yard centred on the "
    "switchyard location (design assumption, not a yard layout). Set switchyard_m in project_design_basis earthing before layout_place and network_autobuild; if it is set later, re-run both."
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


def electrical_centre(project: Project, inverters: list[PlacedEquipment]) -> Located | None:
    """The free ground (outside the tables, roads and exclusions by LT_PANEL_CLEAR_M) nearest the point of least total inverter cable length along the
    array axes: the median of the field inverters along the rows and across them."""
    layout, site = project.array_layout, project.site.geometry
    if not inverters or layout is None or site is None:
        return None
    t0 = next(iter(layout.table_by_id.values()))
    (ux, uy), (vx, vy) = frame(t0.azimuth_deg)
    us = sorted(e.at.x * ux + e.at.y * uy for e in inverters)
    vs = sorted(e.at.x * vx + e.at.y * vy for e in inverters)
    mu, mv = (us[(len(us) - 1) // 2] + us[len(us) // 2]) / 2, (vs[(len(vs) - 1) // 2] + vs[len(vs) // 2]) / 2
    target = (mu * ux + mv * vx, mu * uy + mv * vy)
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()])
    taken = unary_union([tables, *(ShapelyPoint(e.at.x, e.at.y).buffer(INVERTER_SPACING_M) for e in inverters)])
    spot = siting.nearest_in(siting.room(site, taken, LT_PANEL_CLEAR_M), target)
    if spot is None:
        return None
    off = math.dist((spot.x, spot.y), target)
    away = (f"; the array leaves no free ground there, so the ACCB stands {off:.0f} m off it: layout_place switchyard_x={target[0]:.0f}, switchyard_y={target[1]:.0f} "
            "clears an ACCB pad at the centre" if off > CENTRE_OFFSET_M else "")  # fmt: skip
    return Located(at=spot, basis=(f"electrical centre of the {len(inverters)} field inverters: the free spot nearest the point of least total inverter cable length "
                                   f"along the array axes, ({target[0]:.1f}, {target[1]:.1f}), {LT_PANEL_CLEAR_M:g} m clear of tables (network_autobuild accb_at){away}"))  # fmt: skip


def place_equipment(project: Project, catalog: Catalog, bess_at: Point | None = None, accb_at: str = "layout") -> tuple[ArrayLayout | None, list[str]]:
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
    if lt_ground_plant(project):
        if accb_at == "electrical_centre":
            centre = electrical_centre(project, [e for e in placed if e.kind == NodeKind.INVERTER.value])
            if centre is not None:
                layout = layout.model_copy(update={"switchyard": centre})
                notes.append(f"ACCB at local ({centre.at.x:.1f}, {centre.at.y:.1f}): {centre.basis}.")
            else:
                notes.append("accb_at='electrical_centre' needs field inverters on a sited layout; the ACCB stays at the layout's switchyard location.")
        panels, more = _lt_panels(project.network, layout, project)
        placed += panels
        notes += more
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

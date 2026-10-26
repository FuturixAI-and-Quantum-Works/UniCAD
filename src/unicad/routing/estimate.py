"""Cable length estimation from the array layout."""

from collections import defaultdict
from collections.abc import Collection

from unicad.catalog.store import Catalog
from unicad.domain.network import Edge, NodeKind
from unicad.domain.project import Project
from unicad.routing.locate import CO_LOCATED_M, Locator, manhattan, straight
from unicad.routing.model import InstanceLength, RouteAssumptions, RouteEstimate
from unicad.rules.cables import cable_of

NO_LAYOUT = "The project has no array layout with located strings, so no cable length can be estimated from geometry."


def template_of(instance_id: str) -> str:
    return instance_id.split("@", 1)[0]


def estimable_edges(project: Project, catalog: Catalog) -> list[Edge]:
    return [e for e in project.network.edges if e.is_power and cable_of(catalog, e) is not None]


def estimate_routes(
    project: Project, catalog: Catalog, assumptions: RouteAssumptions = RouteAssumptions(), edges: Collection[str] | None = None
) -> RouteEstimate:
    """Per-instance lengths of the cabled power edges named in `edges` (all when None), measured on the layout.

    An instance whose ends cannot both be placed on the site, or that join two things standing at the same spot,
    is left out and its template is listed in `unresolved` with the reason.
    """
    layout = project.array_layout
    wanted = [e for e in estimable_edges(project, catalog) if edges is None or e.id in edges]
    if layout is None or not layout.strings:
        return RouteEstimate(unresolved={e.id: NO_LAYOUT for e in wanted}, assumptions=assumptions.statements(), warnings=(NO_LAYOUT,))
    expanded = project.network.expand()
    loc = Locator(layout, expanded, assumptions)
    by_template = {e.id: e for e in wanted}
    per_cable = assumptions.per_cable_m
    found: dict[str, InstanceLength] = {}
    reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[str, int] = defaultdict(int)
    for ie in expanded.edges:
        tid = template_of(ie.id)
        if tid not in by_template:
            continue
        totals[tid] += 1
        src_kind = expanded.node(ie.src_node).kind
        if src_kind is NodeKind.PV_STRING and ie.src_node in loc.strings:
            dest = loc.location(ie.dst_node)
            if dest is None:
                reasons[tid]["the string's destination has no position on the layout"] += 1
                continue
            sp = loc.strings[ie.src_node]
            az = sp.azimuth_deg
            pos = manhattan(sp.plus, dest.xy, az)
            neg = manhattan(sp.minus, dest.xy, az)
            found[ie.id] = InstanceLength(
                ie.id, tid, pos + per_cable, neg + per_cable, straight(sp.plus, dest.xy), straight(sp.minus, dest.xy),
                f"first and last module of the string to {dest.where}, along the table rows then across them",
            )  # fmt: skip
            continue
        a, b = loc.location(ie.src_node), loc.location(ie.dst_node)
        if a is None or b is None:
            reasons[tid]["an end has no position on the layout (no inverter station or string below it)"] += 1
            continue
        if straight(a.xy, b.xy) < CO_LOCATED_M:
            reasons[tid]["both ends stand at the same place on the layout, so the length between them is not derivable from it"] += 1
            continue
        length = manhattan(a.xy, b.xy, a.azimuth_deg) + per_cable
        d = straight(a.xy, b.xy)
        found[ie.id] = InstanceLength(ie.id, tid, length, None, d, None, f"{a.where} to {b.where}, along the array axes")
    unresolved = {}
    for tid, why in reasons.items():
        unresolved[tid] = "; ".join(f"{n} of {totals[tid]} instances: {text}" for text, n in why.items())
    return RouteEstimate(found, unresolved, assumptions.statements(), tuple(dict.fromkeys(loc.notes)))


def with_estimated_lengths(project: Project, estimate: RouteEstimate, how: str = "max") -> Project:
    """Copy of `project` in which every cabled edge lacking a `length_m` gets the template length of its estimate.

    Lengths the network states are never replaced. The copy exists to run the rules engine on worst-case estimated
    lengths; it is not stored.
    """
    templates = estimate.templates()
    edges = tuple(
        e.model_copy(update={"length_m": templates[e.id].length(how)}) if e.length_m is None and e.id in templates else e
        for e in project.network.edges
    )
    network = project.network.model_copy(update={"edges": edges})
    return project.model_copy(update={"network": network})

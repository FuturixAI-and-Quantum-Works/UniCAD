"""Replace placeholder cable lengths with lengths measured on the array layout."""

from dataclasses import dataclass, field

from unicad.catalog.store import Catalog
from unicad.domain.project import Project
from unicad.routing import RouteAssumptions, estimate_routes

PLACEHOLDER = "Cable route lengths are design placeholders"


@dataclass
class Routed:
    lengths: dict[str, float] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)
    unresolved: dict[str, str] = field(default_factory=dict)
    instances: dict[str, list[float]] = field(default_factory=dict)
    statements: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def route(sized: Project, catalog: Catalog, edge_roles: dict[str, str], given_roles: frozenset[str]) -> Routed:
    """Worst-case routed length of every sized edge whose role the caller did not fix, measured on the project's layout.

    `sized` must carry cables on the edges (the estimator only measures cabled edges) and the layout with its string links."""
    wanted = [eid for eid, role in edge_roles.items() if role not in given_roles]
    est = estimate_routes(sized, catalog, RouteAssumptions(), wanted)
    templates = est.templates()
    out = Routed(unresolved=dict(est.unresolved), notes=est.warnings)
    for eid, t in templates.items():
        out.lengths[eid] = round(t.maximum_m, 1)
        out.roles[eid] = edge_roles[eid]
    for i in est.instances.values():
        out.instances.setdefault(i.edge, []).append(i.effective_m)
    out.statements = est.assumptions
    return out


def routed_statement(r: Routed) -> str:
    by_role: dict[str, list[float]] = {}
    for eid, length in r.lengths.items():
        by_role.setdefault(r.roles[eid], []).append(length)
    used = ", ".join(f"{role} {max(v):.0f} m" for role, v in sorted(by_role.items()))
    return f"Cable route lengths are routed on the array layout (worst-case instance of each template edge, so every instance is sized for it): {used}."

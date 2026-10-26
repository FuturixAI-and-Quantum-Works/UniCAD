from collections.abc import Iterable

from unicad.domain.network import SPEC_KINDS, NodeKind
from unicad.domain.project import Project
from unicad.mcp.workspace import Problem


def advisories(project: Project, touched: Iterable[str]) -> tuple[Problem, ...]:
    """Gaps in the items an edit touched that are legitimate while a design is in progress but should not be forgotten."""
    net = project.network
    out: list[Problem] = []
    for item_id in dict.fromkeys(touched):
        if node := net.node_by_id.get(item_id):
            if node.kind in SPEC_KINDS and node.spec is None:
                out.append(_p("warning", node.id, f"{node.kind} has no catalog spec; find one with catalog_search and set it with network_update"))
            if node.kind is NodeKind.PV_STRING and node.modules_in_series is None:
                out.append(_p("warning", node.id, "pv_string has no modules_in_series"))
        elif edge := net.edge_by_id.get(item_id):
            if edge.is_power and edge.cable is None:
                out.append(_p("info", edge.id, f"{edge.service} connection has no cable yet"))
    return tuple(out)


def _p(severity: str, subject: str, text: str) -> Problem:
    return {"severity": severity, "subject": subject, "message": text}

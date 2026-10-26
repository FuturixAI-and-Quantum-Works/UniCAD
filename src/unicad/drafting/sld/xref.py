"""Which SLD sheet of a set draws each node, and where every cut conductor continues.

A sheet's scope names its root nodes or groups; a sheet with an empty scope is rooted at the POI.
Each node belongs to the sheet of the nearest root downstream of it (towards the grid), so a
detail sheet takes everything that feeds its roots up to where another, more detailed sheet takes
over. Branches fed sideways (an auxiliary transformer off a transformer's HV side, an arrester or
SPD) go with the node that feeds them, control-only devices with the device they are wired to.
Every power edge between two sheets is then cut on both, and each side's off-sheet marker names
the other sheet.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from unicad.domain.network import SIGNAL_PORTS, ElectricalNetwork, NodeKind, Service
from unicad.domain.project import Sheet


@dataclass(frozen=True)
class SheetPart:
    sheet_id: str
    nodes: frozenset[str]
    cut_edges: tuple[str, ...]
    xrefs: dict[str, str]
    """Cut edge id -> number of the sheet that draws the other end."""


def _in_group(net: ElectricalNetwork, gid: str | None, target: str) -> bool:
    while gid is not None:
        if gid == target:
            return True
        gid = net.group_by_id[gid].within
    return False


def owners(net: ElectricalNetwork, sheets: Sequence[Sheet]) -> dict[str, str]:
    """Node id -> id of the sheet that draws it, for every node except earth electrodes (drawn wherever needed)."""
    if not sheets:
        return {}
    roots: dict[str, str] = {}
    for s in sheets:
        for ref in s.scope.nodes:
            members = [ref] if ref in net.node_by_id else [n.id for n in net.nodes if _in_group(net, n.group, ref)]
            for m in members:
                roots.setdefault(m, s.id)
    default = next((s.id for s in sheets if not s.scope.nodes), sheets[0].id)
    for n in net.by_kind(NodeKind.POI_GRID):
        roots.setdefault(n.id, default)
    feeders: dict[str, list[str]] = {n.id: [] for n in net.nodes}
    for e in net.edges:
        if e.is_power:
            feeders[e.dst_node].append(e.src_node)
    out: dict[str, str] = {}

    def own(nid: str, seen: frozenset[str] = frozenset()) -> str | None:
        if nid in out:
            return out[nid]
        if nid in roots:
            return roots[nid]
        hit = next((roots[m] for m in net.downstream(nid) if m in roots), None)
        if hit is None:
            hit = next((o for f in feeders[nid] if f not in seen and (o := own(f, seen | {nid})) is not None), None)
        return hit

    power = [n for n in net.nodes if n.kind is not NodeKind.EARTH_ELECTRODE and any(p not in SIGNAL_PORTS for p in n.port_names)]
    for n in power:
        out[n.id] = own(n.id) or default
    for n in net.nodes:
        if n.id in out or n.kind is NodeKind.EARTH_ELECTRODE:
            continue
        host = next(
            (o for e in net.edges if e.service is Service.CONTROL and n.id in (e.src_node, e.dst_node)
             and (o := out.get(e.dst_node if e.src_node == n.id else e.src_node)) is not None),
            None,
        )
        out[n.id] = host or default
    return out


def partition(net: ElectricalNetwork, sheets: Sequence[Sheet]) -> tuple[SheetPart, ...]:
    """Each sheet's nodes, the power edges cut at its border, and the sheet number every cut continues on."""
    own = owners(net, sheets)
    number = {s.id: s.number for s in sheets}
    parts = []
    for s in sheets:
        nodes = frozenset(n for n, o in own.items() if o == s.id)
        cut = tuple(e.id for e in net.edges if e.is_power and (e.src_node in nodes) != (e.dst_node in nodes))
        xrefs = {}
        for eid in cut:
            e = net.edge_by_id[eid]
            other = e.dst_node if e.src_node in nodes else e.src_node
            xrefs[eid] = number[own[other]]
        parts.append(SheetPart(s.id, nodes, cut, xrefs))
    return tuple(parts)

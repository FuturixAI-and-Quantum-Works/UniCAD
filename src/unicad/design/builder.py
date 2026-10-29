"""Accumulate groups, nodes and edges for a template, with the sizing hints that travel beside the network."""

from dataclasses import dataclass, field
from typing import Any

from unicad.domain.catalog import TransformerSpec
from unicad.domain.network import ElectricalNetwork, Laying, NodeKind, Service
from unicad.design.select import CableFamily

EARTH = "ep"


@dataclass
class NodeHint:
    """How the sizer picks this node's spec. `role` is one of: lv_breaker, vcb, ac_isolator, dc_isolator, dc_spd, ac_spd; `nominal_a` is
    the current to rate for when the rules have no design current (auxiliary feeders)."""

    role: str
    poles: tuple[int, ...] = (4, 3)
    nominal_a: float | None = None
    voltage_v: float | None = None
    min_breaking_ka: float | None = None


@dataclass
class EdgeHint:
    family: CableFamily
    runs: int | None = None
    nominal_a: float | None = None


@dataclass
class _Group:
    id: str
    prefix: str
    part: str | None
    count: int
    within: str | None
    title: str | None
    tag: str | None = None


@dataclass
class NetBuilder:
    groups: dict[str, _Group] = field(default_factory=dict)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    node_hints: dict[str, NodeHint] = field(default_factory=dict)
    edge_hints: dict[str, EdgeHint] = field(default_factory=dict)
    edge_roles: dict[str, str] = field(default_factory=dict)
    last_role: str | None = None
    assumptions: list[str] = field(default_factory=list)
    earth_title: str = "MAIN EARTH GRID"
    _earthed: set[str] = field(default_factory=set)

    def assume(self, *texts: str) -> None:
        for t in texts:
            if t not in self.assumptions:
                self.assumptions.append(t)

    def _chain(self, group: str | None) -> list[_Group]:
        out = []
        while group is not None:
            g = self.groups[group]
            out.append(g)
            group = g.within
        return list(reversed(out))

    def _tag(self, prefix: str, group: str | None, count: int, own: str | None = None) -> str:
        parts, i = [], 0
        for g in self._chain(group):
            if g.count > 1:
                parts.append(f"{{{i}:02d}}")
                i += 1
            elif g.part:
                parts.append(g.part)
        if count > 1:
            parts.append(f"{{{i}:02d}}")
        elif own:
            parts.append(own)
        if not parts and not prefix[-1].isdigit():
            parts.append("01")
        return "-".join([prefix, *parts])

    def group(self, id: str, prefix: str, count: int = 1, within: str | None = None, part: str | None = None, title: str | None = None,
              tag: str | None = None) -> str:
        self.groups[id] = _Group(id, prefix, part, count, within, title, tag)
        return id

    def group_tag(self, id: str) -> str:
        g = self.groups[id]
        return g.tag or self._tag(g.prefix, g.within, g.count, g.part)

    def node(self, id: str, prefix: str, kind: NodeKind, group: str | None = None, count: int = 1, hint: NodeHint | None = None, **kw: Any) -> str:
        spec = kw.pop("spec", None)
        if isinstance(spec, TransformerSpec):
            spec = spec.model_dump(mode="json")
        self.nodes.append({"id": id, "tag": self._tag(prefix, group, count), "kind": kind.value, "group": group, "count": count, "spec": spec, **kw})
        if hint is not None:
            self.node_hints[id] = hint
        return id

    def edge(
        self,
        id: str,
        src: str,
        dst: str,
        service: Service,
        hint: EdgeHint | None = None,
        length_m: float | None = None,
        laying: Laying | None = None,
        **kw: Any,
    ) -> str:
        self.edges.append({"id": id, "src": src, "dst": dst, "service": service.value, "length_m": length_m, "laying": laying.value if laying else None, **kw})
        if length_m is not None and self.last_role is not None:
            self.edge_roles[id] = self.last_role
        self.last_role = None
        if hint is not None:
            self.edge_hints[id] = hint
        return id

    def earth(self, *node_ids: str) -> None:
        if not any(n["id"] == EARTH for n in self.nodes):
            self.node(EARTH, "EP-MAIN", NodeKind.EARTH_ELECTRODE, title=self.earth_title)
        for n in node_ids:
            if n not in self._earthed:
                self._earthed.add(n)
                self.edge(f"pe_{n}", f"{n}:pe", f"{EARTH}:pe", Service.EARTH)

    def set_spec(self, node_id: str, spec: str | None) -> None:
        next(n for n in self.nodes if n["id"] == node_id)["spec"] = spec

    def network(self) -> ElectricalNetwork:
        groups = []
        for g in self.groups.values():
            groups.append({"id": g.id, "tag": self.group_tag(g.id), "count": g.count, "within": g.within, "title": g.title})
        nodes = [{k: v for k, v in n.items() if v is not None} for n in self.nodes]
        edges = [{k: v for k, v in e.items() if v is not None} for e in self.edges]
        return ElectricalNetwork.model_validate({"groups": groups, "nodes": nodes, "edges": edges})

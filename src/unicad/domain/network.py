"""Electrical network: a directed graph of equipment templates.

Edges point in the export direction (from generation towards the grid or a load).
Identical equipment is modelled once: a node's `count` gives its instances per
instance of its `group`, and groups nest through `within`. `expand()` produces the
per-instance graph when detail is needed.
"""

import string
from collections import defaultdict
from enum import StrEnum
from functools import cached_property
from itertools import product
from typing import TYPE_CHECKING, Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from unicad.domain.catalog import CableSpec

if TYPE_CHECKING:
    from unicad.catalog.store import Catalog

Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*(@[0-9]+(\.[0-9]+)*)?$")]
Terminal = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*(@[0-9]+(\.[0-9]+)*)?:[a-z0-9_]+$")]
AttrValue = str | int | float | bool


class NodeKind(StrEnum):
    PV_STRING = "pv_string"
    PV_ARRAY_BLOCK = "pv_array_block"
    SCB = "scb"
    DC_FUSE_BOX = "dc_fuse_box"
    DC_ISOLATOR = "dc_isolator"
    DC_SPD = "dc_spd"
    INVERTER = "inverter"
    PCS = "pcs"
    BATTERY = "battery"
    ACDB = "acdb"
    LT_PANEL = "lt_panel"
    BREAKER = "breaker"
    ISOLATOR = "isolator"
    FUSE = "fuse"
    AC_SPD = "ac_spd"
    CT = "ct"
    PT = "pt"
    METER = "meter"
    RELAY = "relay"
    TRANSFORMER = "transformer"
    AUX_TRANSFORMER = "aux_transformer"
    HT_PANEL = "ht_panel"
    RMU = "rmu"
    VCB = "vcb"
    BUSBAR = "busbar"
    LIGHTNING_ARRESTER = "lightning_arrester"
    EARTH_ELECTRODE = "earth_electrode"
    POI_GRID = "poi_grid"
    DG = "dg"
    LOAD = "load"


class Service(StrEnum):
    DC = "dc"
    LV_AC = "lv_ac"
    MV_AC = "mv_ac"
    HV_AC = "hv_ac"
    EARTH = "earth"
    CONTROL = "control"


class Laying(StrEnum):
    AIR = "air"
    TRAY = "tray"
    CONDUIT = "conduit"
    TRENCH = "trench"
    BURIED_DIRECT = "buried_direct"
    BURIED_DUCT = "buried_duct"
    OVERHEAD = "overhead"


POWER_SERVICES = frozenset({Service.DC, Service.LV_AC, Service.MV_AC, Service.HV_AC})
EARTH_PORT = "pe"
SIGNAL_PORTS = frozenset({"sig", "sec"})
SOURCE_KINDS = frozenset({NodeKind.PV_STRING, NodeKind.PV_ARRAY_BLOCK, NodeKind.BATTERY, NodeKind.DG, NodeKind.POI_GRID})
CONVERTER_KINDS = frozenset({NodeKind.INVERTER, NodeKind.PCS, NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER})

_INLINE = ("in", "out")
DEFAULT_PORTS: dict[NodeKind, tuple[str, ...]] = {
    NodeKind.PV_STRING: ("out",),
    NodeKind.PV_ARRAY_BLOCK: ("out",),
    NodeKind.BATTERY: ("out",),
    NodeKind.DG: ("out",),
    NodeKind.POI_GRID: ("in",),
    NodeKind.LOAD: ("in",),
    NodeKind.INVERTER: ("dc", "ac"),
    NodeKind.PCS: ("dc", "ac"),
    NodeKind.TRANSFORMER: ("lv", "hv"),
    NodeKind.AUX_TRANSFORMER: ("hv", "lv"),
    NodeKind.BUSBAR: ("bus",),
    NodeKind.CT: ("in", "out", "sec"),
    NodeKind.PT: ("in", "sec"),
    NodeKind.METER: ("in", "out", "sig"),
    NodeKind.RELAY: ("sig",),
    NodeKind.DC_SPD: ("line",),
    NodeKind.AC_SPD: ("line",),
    NodeKind.LIGHTNING_ARRESTER: ("line",),
    NodeKind.EARTH_ELECTRODE: (),
}

# Fallback symbol when neither the node nor its catalog spec names one. Panels and
# busbars have none: the SLD draws them as enclosures and bus lines.
KIND_SYMBOLS: dict[NodeKind, str] = {
    NodeKind.PV_STRING: "pv.string",
    NodeKind.PV_ARRAY_BLOCK: "pv.array",
    NodeKind.SCB: "dc.scb",
    NodeKind.DC_FUSE_BOX: "dc.fuse",
    NodeKind.DC_ISOLATOR: "dc.isolator",
    NodeKind.DC_SPD: "dc.spd",
    NodeKind.INVERTER: "inv.string",
    NodeKind.PCS: "inv.pcs",
    NodeKind.BATTERY: "bat.bess",
    NodeKind.ISOLATOR: "sw.isolator",
    NodeKind.FUSE: "sw.hrc_fuse",
    NodeKind.AC_SPD: "prot.spd_ac",
    NodeKind.CT: "tx.ct",
    NodeKind.PT: "tx.pt",
    NodeKind.METER: "inst.kwh",
    NodeKind.RELAY: "prot.relay",
    NodeKind.TRANSFORMER: "tx.power_2w",
    NodeKind.AUX_TRANSFORMER: "tx.aux",
    NodeKind.RMU: "sw.rmu",
    NodeKind.VCB: "sw.vcb",
    NodeKind.LIGHTNING_ARRESTER: "prot.lightning_arrester",
    NodeKind.EARTH_ELECTRODE: "earth.electrode",
    NodeKind.POI_GRID: "grid.utility",
    NodeKind.DG: "grid.dg",
    NodeKind.LOAD: "grid.load",
}

# Catalog spec kinds a node kind may reference, and for protection specs the devices allowed.
SPEC_KINDS: dict[NodeKind, frozenset[str]] = {
    NodeKind.PV_STRING: frozenset({"module"}),
    NodeKind.PV_ARRAY_BLOCK: frozenset({"module"}),
    NodeKind.INVERTER: frozenset({"inverter"}),
    NodeKind.PCS: frozenset({"pcs", "inverter"}),
    NodeKind.BATTERY: frozenset({"battery"}),
    NodeKind.TRANSFORMER: frozenset({"transformer"}),
    NodeKind.AUX_TRANSFORMER: frozenset({"transformer"}),
    NodeKind.METER: frozenset({"meter"}),
    NodeKind.BREAKER: frozenset({"protection"}),
    NodeKind.VCB: frozenset({"protection"}),
    NodeKind.ISOLATOR: frozenset({"protection"}),
    NodeKind.DC_ISOLATOR: frozenset({"protection"}),
    NodeKind.DC_SPD: frozenset({"protection"}),
    NodeKind.AC_SPD: frozenset({"protection"}),
    NodeKind.FUSE: frozenset({"protection"}),
}
PROTECTION_DEVICES: dict[NodeKind, frozenset[str]] = {
    NodeKind.BREAKER: frozenset({"mcb", "mccb", "acb", "rccb"}),
    NodeKind.VCB: frozenset({"vcb"}),
    NodeKind.ISOLATOR: frozenset({"ac_isolator", "sfu"}),
    NodeKind.DC_ISOLATOR: frozenset({"dc_isolator"}),
    NodeKind.DC_SPD: frozenset({"spd_dc"}),
    NodeKind.AC_SPD: frozenset({"spd_ac"}),
    NodeKind.FUSE: frozenset({"fuse_gpv", "fuse_hrc"}),
}


class NetworkError(ValueError):
    pass


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Group(_Frozen):
    """A repeated set of equipment, drawn as a titled box ("BLOCK-01 .. BLOCK-06")."""

    id: Id
    tag: str
    count: int = Field(default=1, gt=0)
    within: Id | None = None
    title: str | None = None


class Node(_Frozen):
    id: Id
    tag: str
    kind: NodeKind
    spec: str | None = None
    symbol: str | None = None
    count: int = Field(default=1, gt=0)
    group: Id | None = None
    voltage_v: float | None = Field(default=None, gt=0)
    modules_in_series: int | None = Field(default=None, gt=0)
    ports: tuple[str, ...] | None = None
    attributes: dict[str, AttrValue] = Field(default_factory=dict)
    title: str | None = None

    @model_validator(mode="after")
    def _modules_only_on_strings(self) -> Self:
        if self.modules_in_series is not None and self.kind is not NodeKind.PV_STRING:
            raise ValueError(f"node {self.id!r}: modules_in_series is only valid on pv_string, not {self.kind}")
        return self

    @property
    def port_names(self) -> tuple[str, ...]:
        return self.ports if self.ports is not None else DEFAULT_PORTS.get(self.kind, _INLINE)

    @property
    def template_id(self) -> str:
        return self.id.split("@", 1)[0]

    def symbol_key(self, catalog: "Catalog | None" = None) -> str | None:
        if self.symbol:
            return self.symbol
        if self.spec and catalog is not None:
            return catalog.get(self.spec).symbol
        return KIND_SYMBOLS.get(self.kind)


class Edge(_Frozen):
    """A connection or cable run. `runs` is the number of parallel runs; `length_m` is per instance."""

    id: Id
    src: Terminal
    dst: Terminal
    service: Service
    cable: str | CableSpec | None = None
    runs: int = Field(default=1, gt=0)
    length_m: float | None = Field(default=None, gt=0)
    laying: Laying | None = None
    derating: float | None = Field(default=None, gt=0, le=1)
    remarks: str | None = None

    @property
    def src_node(self) -> str:
        return self.src.rsplit(":", 1)[0]

    @property
    def src_port(self) -> str:
        return self.src.rsplit(":", 1)[1]

    @property
    def dst_node(self) -> str:
        return self.dst.rsplit(":", 1)[0]

    @property
    def dst_port(self) -> str:
        return self.dst.rsplit(":", 1)[1]

    @property
    def is_power(self) -> bool:
        return self.service in POWER_SERVICES


class VoltageZone(_Frozen):
    voltage_v: float | None
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]


def _placeholders(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f is not None}


class ElectricalNetwork(_Frozen):
    groups: tuple[Group, ...] = ()
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()

    @model_validator(mode="after")
    def _invariants(self, info: ValidationInfo) -> Self:
        problems = self._structural_problems()
        if not problems:
            problems = self._group_problems()
        if not problems:
            problems = self._edge_problems()
        if not problems:
            problems = self._cycle_problems() + self._zone_problems()
        catalog = (info.context or {}).get("catalog") if info is not None else None
        if not problems and catalog is not None:
            problems = self.catalog_problems(catalog)
        if problems:
            raise NetworkError("invalid network: " + "; ".join(problems))
        return self

    def _structural_problems(self) -> list[str]:
        problems = []
        seen: dict[str, str] = {}
        for what, items in (("group", self.groups), ("node", self.nodes), ("edge", self.edges)):
            for item in items:
                if item.id in seen:
                    problems.append(f"duplicate id {item.id!r} ({seen[item.id]} and {what})")
                seen[item.id] = what
        tags = defaultdict(list)
        for n in self.nodes:
            tags[n.tag].append(n.id)
        problems += [f"nodes {ids} share tag {tag!r}" for tag, ids in tags.items() if len(ids) > 1]
        return problems

    def _group_problems(self) -> list[str]:
        problems = []
        for g in self.groups:
            if g.within is not None and g.within not in self.group_by_id:
                problems.append(f"group {g.id!r} is within unknown group {g.within!r}")
        if problems:
            return problems
        for g in self.groups:
            seen, cur = {g.id}, g.within
            while cur is not None:
                if cur in seen:
                    return [f"group {g.id!r} nests inside itself"]
                seen.add(cur)
                cur = self.group_by_id[cur].within
        for g in self.groups:
            problems += self._tag_problems(f"group {g.id!r}", g.tag, len(self._display_levels(self._chain(g.id))))
        for n in self.nodes:
            if n.group is not None and n.group not in self.group_by_id:
                problems.append(f"node {n.id!r} is in unknown group {n.group!r}")
                continue
            depth = len(self._levels(n)) + (n.count > 1)
            problems += self._tag_problems(f"node {n.id!r}", n.tag, depth)
        return problems

    @staticmethod
    def _tag_problems(who: str, tag: str, depth: int) -> list[str]:
        try:
            tag.format(*([1] * depth))
        except (IndexError, KeyError, ValueError) as e:
            return [f"{who}: tag {tag!r} does not format with {depth} index(es): {e}"]
        used = _placeholders(tag)
        if used != {str(i) for i in range(depth)}:
            return [f"{who}: tag {tag!r} must use exactly the placeholders {{0}}..{{{depth - 1}}}" if depth else f"{who}: tag {tag!r} must not contain placeholders"]
        return []

    def _edge_problems(self) -> list[str]:
        problems = []
        for e in self.edges:
            for end, node_id, port in (("src", e.src_node, e.src_port), ("dst", e.dst_node, e.dst_port)):
                node = self.node_by_id.get(node_id)
                if node is None:
                    problems.append(f"edge {e.id!r} {end} references unknown node {node_id!r}")
                    continue
                if e.service is Service.EARTH:
                    if port != EARTH_PORT:
                        problems.append(f"edge {e.id!r} {end}: earth connections use port 'pe', not {node_id}:{port}")
                elif e.service is Service.CONTROL:
                    if port not in SIGNAL_PORTS or port not in node.port_names:
                        problems.append(f"edge {e.id!r} {end}: control port {node_id}:{port} is not a sig/sec port of {node.kind}")
                elif port not in node.port_names or port in SIGNAL_PORTS:
                    problems.append(f"edge {e.id!r} {end}: dangling port {node_id}:{port}; {node.kind} has power ports {[p for p in node.port_names if p not in SIGNAL_PORTS]}")
            if e.src_node == e.dst_node:
                problems.append(f"edge {e.id!r} connects node {e.src_node!r} to itself")
        if problems:
            return problems
        for e in self.edges:
            try:
                self._pairing(e)
            except NetworkError as err:
                problems.append(str(err))
        return problems

    def _cycle_problems(self) -> list[str]:
        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(n: str) -> list[str] | None:
            state[n] = 1
            stack.append(n)
            for m in self._down[n]:
                if state.get(m) == 1:
                    return stack[stack.index(m) :] + [m]
                if m not in state and (cyc := visit(m)):
                    return cyc
            stack.pop()
            state[n] = 2
            return None

        for n in self.node_by_id:
            if n not in state and (cyc := visit(n)):
                return ["power flow cycle: " + " -> ".join(cyc)]
        return []

    def _zone_problems(self) -> list[str]:
        problems = []
        for z in self._zones:
            declared = sorted({v for n in z[0] if (v := self.node_by_id[n].voltage_v) is not None})
            if len(declared) > 1:
                culprits = sorted(n for n in z[0] if self.node_by_id[n].voltage_v is not None)
                problems.append(f"nodes {culprits} are galvanically connected but declare voltages {declared}")
        return problems

    def catalog_problems(self, catalog: "Catalog") -> list[str]:
        problems = []
        for n in self.nodes:
            if n.spec is None:
                continue
            try:
                spec = catalog.get(n.spec)
            except KeyError:
                problems.append(f"node {n.id!r} references unknown catalog id {n.spec!r}")
                continue
            allowed = SPEC_KINDS.get(n.kind, frozenset())
            if spec.kind not in allowed:
                problems.append(f"node {n.id!r} ({n.kind}) cannot use {spec.kind} spec {n.spec!r}")
            elif spec.kind == "protection" and spec.device not in PROTECTION_DEVICES[n.kind]:
                problems.append(f"node {n.id!r} ({n.kind}) cannot use {spec.device} device {n.spec!r}")
        for e in self.edges:
            if not isinstance(e.cable, str):
                continue
            try:
                spec = catalog.get(e.cable)
            except KeyError:
                problems.append(f"edge {e.id!r} references unknown catalog id {e.cable!r}")
                continue
            if spec.kind != "cable":
                problems.append(f"edge {e.id!r} cable {e.cable!r} is a {spec.kind}, not a cable")
        return problems

    def check_catalog(self, catalog: "Catalog") -> None:
        if problems := self.catalog_problems(catalog):
            raise NetworkError("catalog references: " + "; ".join(problems))

    @cached_property
    def node_by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    @cached_property
    def edge_by_id(self) -> dict[str, Edge]:
        return {e.id: e for e in self.edges}

    @cached_property
    def group_by_id(self) -> dict[str, Group]:
        return {g.id: g for g in self.groups}

    @cached_property
    def _down(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {n: [] for n in self.node_by_id}
        for e in self.edges:
            if e.is_power:
                out[e.src_node].append(e.dst_node)
        return out

    @cached_property
    def _up(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {n: [] for n in self.node_by_id}
        for e in self.edges:
            if e.is_power:
                out[e.dst_node].append(e.src_node)
        return out

    def _chain(self, group_id: str | None) -> tuple[Group, ...]:
        chain = []
        while group_id is not None:
            g = self.group_by_id[group_id]
            chain.append(g)
            group_id = g.within
        return tuple(reversed(chain))

    @staticmethod
    def _display_levels(chain: tuple[Group, ...]) -> tuple[Group, ...]:
        return tuple(g for g in chain if g.count > 1)

    def _levels(self, node: Node) -> tuple[Group, ...]:
        """Enclosing groups that repeat (count > 1), outermost first. Count-1 groups are only enclosures."""
        return self._display_levels(self._chain(node.group))

    def _pairing(self, e: Edge) -> str:
        """How template edge `e` maps onto instances: 'pair', 'fan_in' (many src -> one dst) or 'fan_out'."""
        a, b = self.node_by_id[e.src_node], self.node_by_id[e.dst_node]
        ca, cb = self._levels(a), self._levels(b)
        if ca == cb:
            if a.count == b.count:
                return "pair"
            if b.count == 1:
                return "fan_in"
            if a.count == 1:
                return "fan_out"
            raise NetworkError(f"edge {e.id!r}: {a.id!r} (x{a.count}) and {b.id!r} (x{b.count}) in the same group need equal counts or a count of 1")
        if ca[: len(cb)] == cb:
            if b.count != 1:
                raise NetworkError(f"edge {e.id!r}: {b.id!r} sits in an enclosing group and must have count 1 to receive {a.id!r}")
            return "fan_in"
        if cb[: len(ca)] == ca:
            if a.count != 1:
                raise NetworkError(f"edge {e.id!r}: {a.id!r} sits in an enclosing group and must have count 1 to feed {b.id!r}")
            return "fan_out"
        raise NetworkError(f"edge {e.id!r} joins {a.id!r} (group {a.group}) and {b.id!r} (group {b.group}), which repeat independently")

    @cached_property
    def _zones(self) -> list[tuple[list[str], list[str]]]:
        parent: dict[object, object] = {}

        def find(x: object) -> object:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def key(node_id: str, port: str) -> object:
            return (node_id, port) if self.node_by_id[node_id].kind in CONVERTER_KINDS else node_id

        for n in self.nodes:
            if n.kind not in CONVERTER_KINDS and any(p not in SIGNAL_PORTS for p in n.port_names):
                find(n.id)
        for e in self.edges:
            if e.is_power:
                ka, kb = key(e.src_node, e.src_port), key(e.dst_node, e.dst_port)
                parent[find(ka)] = find(kb)
                find(("edge", e.id))
                parent[find(("edge", e.id))] = find(kb)
        zones: dict[object, tuple[list[str], list[str]]] = {}
        for x in list(parent):
            root = find(x)
            if isinstance(x, str):
                zones.setdefault(root, ([], []))[0].append(x)
            elif x[0] == "edge":
                zones.setdefault(root, ([], []))[1].append(x[1])
            else:
                zones.setdefault(root, ([], []))
        return [(sorted(ns), sorted(es)) for ns, es in zones.values()]

    def node(self, node_id: str) -> Node:
        try:
            return self.node_by_id[node_id]
        except KeyError:
            raise KeyError(f"no node {node_id!r}") from None

    def by_kind(self, *kinds: NodeKind | str) -> tuple[Node, ...]:
        wanted = {NodeKind(k) for k in kinds}
        return tuple(n for n in self.nodes if n.kind in wanted)

    def edges_at(self, node_id: str) -> tuple[Edge, ...]:
        return tuple(e for e in self.edges if node_id in (e.src_node, e.dst_node))

    def downstream(self, node_id: str) -> tuple[str, ...]:
        """Every node power flows to from `node_id`, nearest first (BFS order)."""
        return self._walk(node_id, self._down)

    def upstream(self, node_id: str) -> tuple[str, ...]:
        """Every node that feeds `node_id`, nearest first (BFS order)."""
        return self._walk(node_id, self._up)

    def _walk(self, start: str, adj: dict[str, list[str]]) -> tuple[str, ...]:
        self.node(start)
        seen, order, frontier = {start}, [], [start]
        while frontier:
            nxt = []
            for n in frontier:
                for m in adj[n]:
                    if m not in seen:
                        seen.add(m)
                        order.append(m)
                        nxt.append(m)
            frontier = nxt
        return tuple(order)

    def path_to_poi(self, node_id: str) -> tuple[str, ...]:
        """Shortest downstream node path from `node_id` to a poi_grid node, both ends included."""
        self.node(node_id)
        prev: dict[str, str | None] = {node_id: None}
        frontier = [node_id]
        while frontier:
            nxt = []
            for n in frontier:
                if self.node_by_id[n].kind is NodeKind.POI_GRID:
                    path = [n]
                    while (p := prev[path[-1]]) is not None:
                        path.append(p)
                    return tuple(reversed(path))
                for m in self._down[n]:
                    if m not in prev:
                        prev[m] = n
                        nxt.append(m)
            frontier = nxt
        raise NetworkError(f"node {node_id!r} has no downstream path to a poi_grid")

    def subtree(self, node_id: str) -> "ElectricalNetwork":
        """`node_id` and everything feeding it, with the edges and groups among them. Ports may dangle at the cut."""
        keep = {node_id, *self.upstream(node_id)}
        return self._restrict(keep)

    def _restrict(self, keep: set[str]) -> "ElectricalNetwork":
        nodes = tuple(n for n in self.nodes if n.id in keep)
        edges = tuple(e for e in self.edges if e.src_node in keep and e.dst_node in keep)
        needed = {g.id for n in nodes for g in self._chain(n.group)}
        groups = tuple(g for g in self.groups if g.id in needed)
        return ElectricalNetwork(groups=groups, nodes=nodes, edges=edges)

    def voltage_zones(self) -> tuple[VoltageZone, ...]:
        """Galvanically connected regions of the power graph, split at inverters, PCS and transformers."""
        out = []
        for node_ids, edge_ids in self._zones:
            declared = {v for n in node_ids if (v := self.node_by_id[n].voltage_v) is not None}
            out.append(VoltageZone(voltage_v=declared.pop() if declared else None, node_ids=tuple(node_ids), edge_ids=tuple(edge_ids)))
        return tuple(sorted(out, key=lambda z: (z.voltage_v is None, -(z.voltage_v or 0), z.node_ids)))

    def unconnected_ports(self) -> tuple[str, ...]:
        """Power ports with no edge, as 'node:port'. Not an error while a design is in progress."""
        used = {t for e in self.edges for t in (e.src, e.dst)}
        return tuple(f"{n.id}:{p}" for n in self.nodes for p in n.port_names if p not in SIGNAL_PORTS and f"{n.id}:{p}" not in used)

    def instance_count(self, node_id: str) -> int:
        n = self.node(node_id)
        total = n.count
        for g in self._chain(n.group):
            total *= g.count
        return total

    def _paths(self, node: Node) -> list[tuple[int, ...]]:
        """1-based index path of every instance: one index per repeating group, then the node's own if count > 1."""
        counts = [g.count for g in self._levels(node)] + ([node.count] if node.count > 1 else [])
        return list(product(*(range(1, c + 1) for c in counts)))

    @staticmethod
    def _instance_id(base: str, path: tuple[int, ...]) -> str:
        return f"{base}@{'.'.join(map(str, path))}" if path else base

    def expand(self) -> "ElectricalNetwork":
        """One node per instance and one edge per instance connection. Ids become 'id@i.j', tags are formatted."""
        nodes = [
            n.model_copy(update={"id": self._instance_id(n.id, p), "tag": n.tag.format(*p), "count": 1, "group": None})
            for n in self.nodes
            for p in self._paths(n)
        ]
        edges = []
        for e in self.edges:
            a, b = self.node_by_id[e.src_node], self.node_by_id[e.dst_node]
            mode = self._pairing(e)
            many, other = (b, a) if mode == "fan_out" else (a, b)
            depth = len(self._levels(other))
            for p in self._paths(many):
                q = p if mode == "pair" else p[:depth]
                pa, pb = (q, p) if mode == "fan_out" else (p, q)
                edges.append(
                    e.model_copy(
                        update={
                            "id": self._instance_id(e.id, p),
                            "src": f"{self._instance_id(a.id, pa)}:{e.src_port}",
                            "dst": f"{self._instance_id(b.id, pb)}:{e.dst_port}",
                        }
                    )
                )
        return ElectricalNetwork(nodes=tuple(nodes), edges=tuple(edges))

    def instance_ids(self, node_id: str) -> tuple[str, ...]:
        n = self.node(node_id)
        return tuple(self._instance_id(n.id, p) for p in self._paths(n))


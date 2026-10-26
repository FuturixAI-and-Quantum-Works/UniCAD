"""Graph queries the rule families share: labels, voltage zones, which strings feed which inverter, and design currents.

The network is evaluated at template level: identical instances get identical results, so one
finding covers every instance of a template and its message names the instance range.
"""

import math
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.catalog import InverterSpec, ModuleSpec, PCSSpec
from unicad.domain.network import CONVERTER_KINDS, ElectricalNetwork, NetworkError, Node, NodeKind, Service, VoltageZone
from unicad.domain.project import Project
from unicad.rules.model import Calc, Quantity
from unicad.rules.thresholds import ISC_FACTOR

PASS_THROUGH = frozenset({NodeKind.DC_FUSE_BOX, NodeKind.DC_ISOLATOR, NodeKind.FUSE, NodeKind.BREAKER, NodeKind.ISOLATOR})
COMBINERS = frozenset({NodeKind.SCB})
STRING_FUSE_HOLDERS = frozenset({NodeKind.FUSE, NodeKind.DC_FUSE_BOX})
GENERATORS = frozenset({NodeKind.INVERTER, NodeKind.PCS})
EVEN_MPPT = "Strings (or combiner outputs) are spread evenly over the inverter's MPPTs; the network does not record the MPPT allocation."
PASS_THROUGH_NOTE = "DC fuse boxes, isolators and fuses pass strings through one-to-one; strings are paralleled only in SCBs or at the inverter MPPT."


def instance_tag(net: ElectricalNetwork, instance_id: str) -> str:
    base, _, suffix = instance_id.partition("@")
    path = tuple(int(i) for i in suffix.split(".")) if suffix else ()
    return net.node(base).tag.format(*path)


def label(net: ElectricalNetwork, node_id: str) -> str:
    ids = net.instance_ids(node_id)
    if len(ids) == 1:
        return instance_tag(net, ids[0])
    return f"{instance_tag(net, ids[0])}..{instance_tag(net, ids[-1])} (x{len(ids)})"


def per_instance(net: ElectricalNetwork, many: str, one: str) -> int:
    """Instances of `many` per instance of `one` they connect to."""
    return math.ceil(net.instance_count(many) / net.instance_count(one))


def zone_at(net: ElectricalNetwork, node_id: str, port: str | None = None) -> VoltageZone | None:
    """The voltage zone of a node, or of one port of a converter (inverter, PCS, transformer)."""
    node = net.node(node_id)
    zones = net.voltage_zones()
    if node.kind in CONVERTER_KINDS:
        ids = {e.id for e in net.edges if e.is_power and ((e.src_node, e.src_port) == (node_id, port) or (e.dst_node, e.dst_port) == (node_id, port))}
        return next((z for z in zones if ids & set(z.edge_ids)), None)
    return next((z for z in zones if node_id in z.node_ids), None)


def is_dc(net: ElectricalNetwork, node_id: str) -> bool:
    return any(e.service is Service.DC for e in net.edges_at(node_id))


def exports(net: ElectricalNetwork, node_id: str) -> bool:
    try:
        net.path_to_poi(node_id)
    except NetworkError:
        return False
    return True


def _feeders(net: ElectricalNetwork, node_id: str, port: str | None = None) -> list[str]:
    return [e.src_node for e in net.edges if e.is_power and e.dst_node == node_id and (port is None or e.dst_port == port)]


def nearest_upstream(net: ElectricalNetwork, start: str, port: str | None, stop: frozenset[NodeKind]) -> list[tuple[str, tuple[str, ...]]]:
    """Nearest upstream nodes of a `stop` kind reached through pass-through devices, each with the devices passed (source side first)."""
    out, seen = [], set()
    frontier = [(n, ()) for n in _feeders(net, start, port)]
    while frontier:
        n, path = frontier.pop(0)
        if n in seen:
            continue
        seen.add(n)
        kind = net.node(n).kind
        if kind in stop:
            out.append((n, path))
        elif kind in PASS_THROUGH:
            frontier += [(m, (n, *path)) for m in _feeders(net, n)]
    return out


@dataclass(frozen=True)
class StringFeed:
    """One string template feeding one inverter template."""

    string: Node
    module: ModuleSpec
    n_series: int
    inverter: Node
    spec: InverterSpec
    combiner: Node | None
    path: tuple[str, ...]
    strings_per_inverter: int
    inverter_inputs: int
    strings_per_input: int

    @property
    def inputs_per_mppt(self) -> int:
        return math.ceil(self.inverter_inputs / self.spec.mppt_count)

    @property
    def strings_per_mppt(self) -> int:
        return self.inputs_per_mppt * self.strings_per_input

    def subjects(self) -> tuple[str, ...]:
        return (self.string.id, self.inverter.id)

    def where(self, net: ElectricalNetwork) -> str:
        return f"string {self.string.id} ({label(net, self.string.id)}, {self.n_series} x {self.module.model}) into inverter {self.inverter.id} ({label(net, self.inverter.id)}, {self.spec.model})"

    def layout_inputs(self) -> tuple[Quantity, ...]:
        return (
            Quantity(name="strings_per_inverter", value=self.strings_per_inverter, source="network"),
            Quantity(name="inverter_dc_inputs", value=self.inverter_inputs, source="network"),
            Quantity(name="strings_per_input", value=self.strings_per_input, source="network"),
            Quantity(name="mppt_count", value=self.spec.mppt_count, source=self.spec.id),
            Quantity(name="dc_inputs_per_mppt", value=self.inputs_per_mppt, source=EVEN_MPPT),
            Quantity(name="strings_per_mppt", value=self.strings_per_mppt, source=EVEN_MPPT),
        )


def _module_of(net: ElectricalNetwork, catalog: Catalog, node: Node) -> ModuleSpec | None:
    if node.spec is None or node.modules_in_series is None:
        return None
    spec = catalog.get(node.spec)
    return spec if isinstance(spec, ModuleSpec) else None


def inverter_spec(catalog: Catalog, node: Node) -> InverterSpec | None:
    if node.spec is None:
        return None
    spec = catalog.get(node.spec)
    return spec if isinstance(spec, InverterSpec) else None


def string_feeds(project: Project, catalog: Catalog) -> list[StringFeed]:
    net = project.network
    feeds = []
    for inv in net.by_kind(*GENERATORS):
        spec = inverter_spec(catalog, inv)
        if spec is None:
            continue
        inputs = nearest_upstream(net, inv.id, "dc", frozenset({NodeKind.PV_STRING, NodeKind.PV_ARRAY_BLOCK, *COMBINERS}))
        total_inputs = sum(per_instance(net, u, inv.id) for u, _ in inputs)
        for u, path in inputs:
            unit = net.node(u)
            if unit.kind is NodeKind.PV_STRING:
                strings, combiner, per_input = [(u, path)], None, 1
            elif unit.kind in COMBINERS:
                found = nearest_upstream(net, u, None, frozenset({NodeKind.PV_STRING}))
                strings = [(s, (*p, u, *path)) for s, p in found]
                combiner, per_input = unit, sum(per_instance(net, s, u) for s, _ in found)
            else:
                continue
            for s, spath in strings:
                node = net.node(s)
                module = _module_of(net, catalog, node)
                if module is None:
                    continue
                feeds.append(
                    StringFeed(
                        string=node,
                        module=module,
                        n_series=node.modules_in_series,
                        inverter=inv,
                        spec=spec,
                        combiner=combiner,
                        path=spath,
                        strings_per_inverter=per_instance(net, s, inv.id),
                        inverter_inputs=total_inputs,
                        strings_per_input=per_input,
                    )
                )
    return feeds


def _ac_kva(catalog: Catalog, node: Node) -> tuple[float, int, str] | None:
    """Maximum AC apparent power, phases and the field it came from."""
    if node.spec is None:
        return None
    spec = catalog.get(node.spec)
    if isinstance(spec, InverterSpec):
        return spec.ac_max_kva, spec.phases, "ac_max_kva"
    if isinstance(spec, PCSSpec):
        return spec.rated_kva, 3, "rated_kva"
    return None


def design_current(project: Project, catalog: Catalog, node_id: str) -> Calc | None:
    """Design current Ib through a device on an export path, per instance.

    AC side: the maximum output current of every inverter or PCS it carries towards the POI, at
    the device's zone voltage. DC side: 1.25 x Isc of every string it carries. Protection checks
    In against this; cable sizing reuses it for Iz. None when the device carries no generation
    or its zone voltage is undeclared.
    """
    net = project.network
    if is_dc(net, node_id):
        found = [(s, per_instance(net, s, node_id)) for s in net.upstream(node_id) if net.node(s).kind is NodeKind.PV_STRING]
        mods = [(s, n, _module_of(net, catalog, net.node(s))) for s, n in found]
        mods = [(s, n, m) for s, n, m in mods if m is not None]
        if not mods:
            return None
        inputs = [Quantity(name="k_Isc", value=ISC_FACTOR.value, source=f"threshold.{ISC_FACTOR.name}")]
        for s, n, m in mods:
            inputs += [Quantity(name=f"n[{s}]", value=n, source="network"), Quantity(name=f"Isc[{s}]", value=m.isc, unit="A", source=m.id)]
        return Calc(
            value=sum(n * ISC_FACTOR.value * m.isc for _, n, m in mods),
            unit="A",
            formula="Ib = sum(n_strings x k_Isc x Isc)",
            inputs=tuple(inputs),
            assumptions=(ISC_FACTOR.assumption,),
        )
    if not exports(net, node_id):
        return None
    zone = zone_at(net, node_id)
    if zone is None or zone.voltage_v is None:
        return None
    gens = [(g, per_instance(net, g, node_id), r) for g in net.upstream(node_id) if net.node(g).kind in GENERATORS and (r := _ac_kva(catalog, net.node(g)))]
    if not gens:
        return None
    v = zone.voltage_v
    inputs = [Quantity(name="V", value=v, unit="V", source="network.voltage_zone")]
    total = 0.0
    for g, n, (kva, phases, field) in gens:
        total += n * kva * 1000.0 / ((math.sqrt(3) if phases == 3 else 1.0) * v)
        spec_id = net.node(g).spec
        inputs += [Quantity(name=f"n[{g}]", value=n, source="network"), Quantity(name=f"S_max[{g}]", value=kva, unit="kVA", source=spec_id), Quantity(name=f"phases[{g}]", value=phases, source=spec_id)]
    return Calc(
        value=total,
        unit="A",
        formula="Ib = sum(n x S_max x 1000 / (sqrt(3) x V)), V = zone voltage (V alone for single-phase)",
        inputs=tuple(inputs),
        assumptions=("Inverter maximum AC current derived from maximum apparent power at the zone voltage; the catalog has no datasheet maximum-current field.",),
    )

"""Size protection and cables on a built network with the rules engine's own design currents, then upsize cables until voltage drop fits."""

from dataclasses import dataclass, field

from unicad.catalog.store import Catalog
from unicad.domain.catalog import ProtectionSpec
from unicad.domain.network import Edge, ElectricalNetwork, NetworkError, NodeKind, Service
from unicad.domain.project import Project
from unicad.design.builder import EdgeHint, NodeHint
from unicad.design.select import (
    BREAKING_ASSUMPTION,
    SPD_AC_ASSUMPTION,
    CableOption,
    NoFit,
    cable_options,
    pick_ac_spd,
    pick_by_voltage,
    pick_lv_breaker,
    pick_rated,
)
from unicad.rules.cables import OVERLOAD_DEVICES, needs_cable, phases
from unicad.rules.pv import cell_temp_min, string_voltage
from unicad.rules.thresholds import CONTINUOUS_FACTOR
from unicad.rules.topology import GENERATORS, design_current, edge_current, string_feeds, zone_at
from unicad.rules.vdrop import segment_drop

MAX_VDROP_STEPS = 400
NOMINAL_ASSUMPTION = "Auxiliary feeders carry no export current in the rules; their breakers and cables are rated for the auxiliary transformer's full-load current."


@dataclass
class Sizing:
    project: Project
    assumptions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    chosen: dict[str, CableOption] = field(default_factory=dict)


def _with(project: Project, net: ElectricalNetwork) -> Project:
    return project.model_copy(update={"network": net})


def _rebuild(project: Project, nodes=None, edges=None) -> Project:
    net = project.network
    return _with(project, ElectricalNetwork(groups=net.groups, nodes=tuple(nodes if nodes is not None else net.nodes), edges=tuple(edges if edges is not None else net.edges)))


def max_dc_voc(project: Project, catalog: Catalog, node_id: str) -> float | None:
    zone = zone_at(project.network, node_id)
    if zone is None:
        return None
    vs = [string_voltage(f.module, f.n_series, "voc", cell_temp_min(project.design_basis)).value for f in string_feeds(project, catalog) if f.string.id in zone.node_ids]
    return max(vs) if vs else None


def _zone_v(project: Project, node_id: str) -> float | None:
    z = zone_at(project.network, node_id)
    return z.voltage_v if z else None


def _pick_node(project: Project, catalog: Catalog, node_id: str, hint: NodeHint, s: Sizing) -> str | None:
    ib = design_current(project, catalog, node_id)
    amps = ib.value if ib is not None else hint.nominal_a
    if ib is None and hint.nominal_a is not None:
        s.assumptions.append(NOMINAL_ASSUMPTION)
    v = _zone_v(project, node_id)
    k = CONTINUOUS_FACTOR.value
    role = hint.role
    if role == "dc_spd":
        voc = max(max_dc_voc(project, catalog, node_id) or 0.0, hint.voltage_v or 0.0)
        return pick_by_voltage(catalog, "spd_dc", voc).id
    if role == "ac_spd":
        s.assumptions.append(SPD_AC_ASSUMPTION)
        return pick_ac_spd(catalog, v or 0.0, 1 if hint.poles[0] <= 2 else 3).id
    if amps is None:
        amps = 0.0
    if role == "dc_isolator":
        voc = max(max_dc_voc(project, catalog, node_id) or 0.0, hint.voltage_v or 0.0)
        return pick_rated(catalog, ["dc_isolator"], amps, voc, hint.poles).id
    need = k * amps
    if role == "lv_breaker":
        s.assumptions.append(BREAKING_ASSUMPTION)
        return pick_lv_breaker(catalog, need, v or 0.0, hint.poles).id
    if role == "vcb":
        s.assumptions.append(BREAKING_ASSUMPTION)
        return pick_rated(catalog, ["vcb"], need, v or 0.0, (3,)).id
    if role == "ac_isolator":
        return pick_rated(catalog, ["ac_isolator", "sfu"], need, v or 0.0, hint.poles).id
    raise ValueError(f"unknown node sizing role {role!r}")


def size_protection(project: Project, catalog: Catalog, hints: dict[str, NodeHint], s: Sizing) -> Project:
    nodes = []
    for n in project.network.nodes:
        hint = hints.get(n.id)
        if hint is None:
            nodes.append(n)
            continue
        try:
            spec = _pick_node(project, catalog, n.id, hint, s)
        except NoFit as e:
            s.blockers.append(f"{n.tag} ({n.kind}): {e}; left without a spec")
            spec = None
        nodes.append(n.model_copy(update={"spec": spec}))
    return _rebuild(project, nodes=nodes)


def _device_in(project: Project, catalog: Catalog, node_id: str) -> float:
    n = project.network.node(node_id)
    if n.spec is None or not isinstance(n.spec, str):
        return 0.0
    spec = catalog.get(n.spec)
    if isinstance(spec, ProtectionSpec) and spec.device in OVERLOAD_DEVICES and spec.rated_current is not None:
        return spec.rated_current
    return 0.0


def _need(project: Project, catalog: Catalog, e: Edge, hint: EdgeHint, s: Sizing) -> float:
    ib = edge_current(project, catalog, e)
    amps = ib.value if ib is not None else (CONTINUOUS_FACTOR.value * hint.nominal_a if hint.nominal_a else 0.0)
    if ib is None and hint.nominal_a:
        s.assumptions.append(NOMINAL_ASSUMPTION)
    return max(amps, _device_in(project, catalog, e.src_node), _device_in(project, catalog, e.dst_node))


def _options(project: Project, catalog: Catalog, e: Edge, hint: EdgeHint) -> list[CableOption]:
    ph = phases(project, catalog, e) if e.service is not Service.DC else 1
    return cable_options(catalog, hint.family, e.laying, ph, hint.runs)


def _apply(e: Edge, o: CableOption) -> Edge:
    return e.model_copy(update={"cable": o.cable.id, "runs": o.runs})


def size_cables(project: Project, catalog: Catalog, hints: dict[str, EdgeHint], s: Sizing) -> Project:
    edges = []
    net = project.network
    for e in net.edges:
        hint = hints.get(e.id)
        if hint is None or not e.is_power or not needs_cable(net, e) or e.laying is None:
            edges.append(e)
            continue
        need = _need(project, catalog, e, hint, s)
        fits = [o for o in _options(project, catalog, e, hint) if o.iz >= need - 1e-9]
        if not fits:
            s.blockers.append(f"edge {e.id}: no {hint.family.name} cable option carries {need:.1f} A laid '{e.laying}' within {hint.family.max_runs} runs")
            edges.append(e)
            continue
        s.chosen[e.id] = fits[0]
        edges.append(_apply(e, fits[0]))
    return _rebuild(project, edges=edges)


def _drop(project: Project, catalog: Catalog, e: Edge) -> float:
    d = segment_drop(project, catalog, e)
    return d.value if d is not None else 0.0


def _power_path(net: ElectricalNetwork, start: str) -> list[Edge]:
    try:
        nodes = net.path_to_poi(start)
    except NetworkError:
        return []
    out = []
    for a, b in zip(nodes, nodes[1:]):
        e = next((e for e in net.edges if e.is_power and e.src_node == a and e.dst_node == b), None)
        if e is not None:
            out.append(e)
    return out


def vdrop_paths(project: Project) -> list[tuple[str, list[Edge], float]]:
    """(what, edges, limit %) for each DC string-to-inverter path and each generator-to-POI AC path, as the vdrop rules walk them."""
    net, basis = project.network, project.design_basis
    out = []
    for st in net.by_kind(NodeKind.PV_STRING):
        dc = []
        for e in _power_path(net, st.id):
            if e.service is not Service.DC:
                break
            dc.append(e)
        if dc:
            out.append((f"DC {st.id}", dc, 100 * basis.vdrop_dc_max))
    for g in net.by_kind(*GENERATORS):
        ac = [e for e in _power_path(net, g.id) if e.service is not Service.DC]
        if ac:
            out.append((f"AC {g.id}", ac, 100 * basis.vdrop_ac_max))
    return out


def fit_vdrop(project: Project, catalog: Catalog, hints: dict[str, EdgeHint], s: Sizing) -> Project:
    """Upsize the cable with the largest drop on each path over its limit, one step at a time, until every path fits or no step is left."""
    for _ in range(MAX_VDROP_STEPS):
        over = []
        for what, edges, limit in vdrop_paths(project):
            drops = {e.id: _drop(project, catalog, e) for e in edges}
            if sum(drops.values()) > limit:
                over.append((what, edges, drops, limit))
        if not over:
            return project
        net = project.network
        changed: dict[str, Edge] = {}
        for what, edges, drops, limit in over:
            for eid in sorted(drops, key=lambda k: -drops[k]):
                if eid in changed or eid not in s.chosen or drops[eid] <= 0:
                    continue
                e, cur = net.edge_by_id[eid], s.chosen[eid]
                need = _need(project, catalog, e, hints[eid], s)
                better = [o for o in _options(project, catalog, e, hints[eid]) if o.iz >= need - 1e-9 and o.r_eff < cur.r_eff - 1e-12 and o.cost >= cur.cost]
                if not better:
                    continue
                s.chosen[eid] = better[0]
                changed[eid] = _apply(e, better[0])
                break
            else:
                if any(eid in changed for eid in drops):
                    continue
                total = sum(drops.values())
                msg = f"{what}: voltage drop {total:.2f} % exceeds {limit:g} % and no larger cable option is left"
                if msg not in s.blockers:
                    s.blockers.append(msg)
        if not changed:
            return project
        project = _rebuild(project, edges=[changed.get(e.id, e) for e in net.edges])
    return project


def size_network(project: Project, catalog: Catalog, node_hints: dict[str, NodeHint], edge_hints: dict[str, EdgeHint]) -> Sizing:
    s = Sizing(project)
    p = size_protection(project, catalog, node_hints, s)
    p = size_cables(p, catalog, edge_hints, s)
    p = fit_vdrop(p, catalog, edge_hints, s)
    s.project = p
    s.assumptions = list(dict.fromkeys(s.assumptions))
    return s

"""Size protection and cables on a built network with the rules engine's own design currents, then upsize cables until voltage drop fits."""

import math
from dataclasses import dataclass, field

from unicad.catalog.store import Catalog
from unicad.domain.catalog import ProtectionSpec
from unicad.domain.network import CableBand, Edge, ElectricalNetwork, NetworkError, NodeKind, Service
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
from unicad.rules.cables import K_BY_CABLE, cable_fault, needs_cable, phases, protectors
from unicad.rules.faults import prospective_fault
from unicad.rules.pv import cell_temp_min, string_voltage
from unicad.rules.thresholds import CONTINUOUS_FACTOR
from unicad.rules.topology import AC_SERVICES, GENERATORS, design_current, edge_current, string_feeds, zone_at
from unicad.rules.vdrop import segment_drop

MAX_VDROP_STEPS = 400
NOMINAL_ASSUMPTION = "Auxiliary feeders carry no export current in the rules; their breakers and cables are rated for the auxiliary transformer's full-load current."


@dataclass
class Sizing:
    project: Project
    assumptions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    chosen: dict[str, CableOption] = field(default_factory=dict)
    guards: dict[str, list[tuple[str, ProtectionSpec]]] | None = None


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
    if role == "string_fuse":
        voc = max_dc_voc(project, catalog, node_id) or 0.0
        fuse = pick_rated(catalog, ["fuse_gpv"], amps, voc, hint.poles)
        feeds = [f for f in string_feeds(project, catalog) if node_id in f.path]
        limit = min((f.module.max_series_fuse for f in feeds if f.module.max_series_fuse is not None), default=None)
        if limit is not None and fuse.rated_current > limit:
            raise NoFit(f"the smallest catalog gPV fuse rated >= {amps:.1f} A at {voc:.0f} V is {fuse.rated_current:g} A, above the module max series fuse {limit:g} A")
        return fuse.id
    need = k * amps
    if role in ("lv_breaker", "vcb"):
        fault = prospective_fault(project, catalog, node_id)
        if fault is None:
            s.assumptions.append(BREAKING_ASSUMPTION)
        elif fault.where == "transformer-fed":
            s.assumptions.append(f"Breaking capacity at {fault.voltage_v:g} V is chosen at or above the {fault.ka:g} kA prospective fault the transformer passes to the bus ({fault.formula}).")
            s.assumptions.extend(fault.assumptions)
        else:
            s.assumptions.append(f"Breaking capacity at {fault.voltage_v:g} V is chosen at or above the {fault.ka:g} kA fault level from {fault.where}.")
        if role == "lv_breaker":
            return pick_lv_breaker(catalog, need, v or 0.0, hint.poles, fault.ka if fault else None).id
        return pick_rated(catalog, ["vcb"], need, v or 0.0, (3,), fault.ka if fault else None).id
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


def _need(project: Project, catalog: Catalog, e: Edge, hint: EdgeHint, s: Sizing) -> float:
    """Iz the cable needs: its design current, and at least the In of every overcurrent device protecting it (In <= Iz)."""
    ib = edge_current(project, catalog, e)
    amps = ib.value if ib is not None else (CONTINUOUS_FACTOR.value * hint.nominal_a if hint.nominal_a else 0.0)
    if ib is None and hint.nominal_a:
        s.assumptions.append(NOMINAL_ASSUMPTION)
    if s.guards is None:
        s.guards = protectors(project, catalog)
    return max([amps, *(spec.rated_current for _, spec in s.guards.get(e.id, []))])


def _options(project: Project, catalog: Catalog, e: Edge, hint: EdgeHint, s: Sizing) -> list[CableOption]:
    ph = phases(project, catalog, e) if e.service is not Service.DC else 1
    options = cable_options(catalog, hint.family, e.laying, ph, hint.runs)
    if e.service not in AC_SERVICES:
        return options
    if s.guards is None:
        s.guards = protectors(project, catalog)
    fault = cable_fault(project, catalog, e, s.guards)
    if fault is None:
        return options
    s.assumptions.append(
        f"AC cables in the {fault.voltage_v:g} V zone are sized for conductor area x runs >= I sqrt(t) / k with the {fault.ka:g} kA fault ({fault.where}) cleared in {fault.clearing_s:g} s, "
        f"k from the cited XLPE constants; the fault is taken at the cable's source end and only the conductor is checked."
    )
    s.assumptions.extend(fault.assumptions)

    def withstands(o: CableOption) -> bool:
        k = K_BY_CABLE.get((o.cable.conductor, o.cable.insulation))
        return k is None or o.cable.size_mm2 * o.runs >= fault.ka * 1000 * math.sqrt(fault.clearing_s) / k.value - 1e-9

    return [o for o in options if withstands(o)]


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
        fits = [o for o in _options(project, catalog, e, hint, s) if o.iz >= need - 1e-9]
        if not fits:
            s.blockers.append(f"edge {e.id}: no {hint.family.name} cable option carries {need:.1f} A laid '{e.laying}' within {hint.family.max_runs} runs (and withstands the fault level where design_basis.fault_levels names its voltage)")
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
                better = [o for o in _options(project, catalog, e, hints[eid], s) if o.iz >= need - 1e-9 and o.r_eff < cur.r_eff - 1e-12 and o.cost >= cur.cost]
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


BAND_SERVICES = frozenset({Service.DC, Service.LV_AC})
BANDS = (
    "String and LT cables are sized per length band: each routed instance takes the smallest cable that carries the design current, withstands the fault "
    "and whose voltage drop at its own routed length is at most the drop the template cable has at the worst-case length, so every instance stays within "
    "the path limit the rules check on the worst case; the cable schedule and BOM list each instance with its band's cable."
)


def band_cables(s: Sizing, catalog: Catalog, hints: dict[str, EdgeHint], lengths: dict[str, list[float]]) -> Sizing:
    """Give each multi-instance DC or LT edge with routed instance lengths the cable bands its shorter instances need (see BANDS)."""
    project = s.project
    net = project.network
    edges = []
    banded = 0
    for e in net.edges:
        ls = lengths.get(e.id)
        cur = s.chosen.get(e.id)
        if not ls or len(ls) < 2 or cur is None or e.service not in BAND_SERVICES or e.length_m is None or e.id not in hints:
            edges.append(e)
            continue
        worst = _drop(project, catalog, e)
        need = _need(project, catalog, e, hints[e.id], s)
        cheaper = [o for o in _options(project, catalog, e, hints[e.id], s) if o.iz >= need - 1e-9 and o.cost < cur.cost]
        per_m = {id(o): _drop(project, catalog, _apply(e, o)) / e.length_m for o in cheaper}
        upto: dict[int, tuple[float, CableOption]] = {}
        for length in ls:
            up = math.ceil(length * 10 - 1e-9) / 10
            o = next((o for o in cheaper if per_m[id(o)] * up <= worst + 1e-9), None)
            if o is not None:
                upto[id(o)] = (max(up, upto.get(id(o), (0.0, o))[0]), o)
        bands = tuple(CableBand(upto_m=m, cable=o.cable.id, runs=o.runs) for m, o in sorted(upto.values(), key=lambda v: v[0]))
        if bands:
            banded += 1
            e = e.model_copy(update={"bands": bands})
        edges.append(e)
    if banded:
        s.project = _rebuild(project, edges=edges)
        s.assumptions.append(BANDS)
    return s


def size_network(project: Project, catalog: Catalog, node_hints: dict[str, NodeHint], edge_hints: dict[str, EdgeHint]) -> Sizing:
    s = Sizing(project)
    p = size_protection(project, catalog, node_hints, s)
    p = size_cables(p, catalog, edge_hints, s)
    p = fit_vdrop(p, catalog, edge_hints, s)
    s.project = p
    s.assumptions = list(dict.fromkeys(s.assumptions))
    return s

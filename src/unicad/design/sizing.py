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
from unicad.rules.cables import K_BY_CABLE, cable_fault, edge_label, needs_cable, phases, protectors, terminals
from unicad.rules.faults import prospective_fault
from unicad.rules.protection import ISOLATOR_WAYS, SPD_IT, SPD_TN, covers_fault, spd_uc_need
from unicad.rules.pv import cell_temp_min, string_voltage
from unicad.rules.settings import trip_current
from unicad.rules.thresholds import CONTINUOUS_FACTOR, ISC_FACTOR, STRING_FUSE_MAX, STRING_FUSE_MIN
from unicad.rules.topology import AC_SERVICES, GENERATORS, design_current, edge_current, label, per_instance, string_feeds, zone_at
from unicad.rules.vdrop import segment_drop

MAX_VDROP_STEPS = 400
NOMINAL_ASSUMPTION = "Auxiliary feeders carry no export current in the rules; their breakers and cables are rated for the auxiliary transformer's full-load current."
TERMINALS = (
    "Cables landing on a breaker or an inverter's AC output are chosen, length bands included, only among sizes its terminals take (largest cable and cables per "
    "pole from the catalog entry, which cites the datasheet or labels the value an assumption; cables.terminals)."
)


@dataclass
class Sizing:
    project: Project
    assumptions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    chosen: dict[str, CableOption] = field(default_factory=dict)
    guards: dict[str, list[tuple[str, ProtectionSpec]]] | None = None
    ampacity_runs: dict[str, int] = field(default_factory=dict)
    ampacity: dict[str, CableOption] = field(default_factory=dict)
    reframed: dict[str, str] = field(default_factory=dict)


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
        phases_n = 1 if hint.poles[0] <= 2 else 3
        got = spd_uc_need(project, catalog, node_id)
        if got is None or phases_n == 1:
            s.assumptions.append(SPD_AC_ASSUMPTION)
            return pick_ac_spd(catalog, v or 0.0, phases_n).id
        need, system, _ = got
        s.assumptions.append(f"AC SPDs on {v:g} V {system} buses are chosen with Uc >= {need:.0f} V. {SPD_IT if system == 'IT' else SPD_TN}")
        return pick_ac_spd(catalog, v or 0.0, phases_n, need, f"{system} bus at {v:g} V: {'1.1 x U(L-L)' if system == 'IT' else '1.1 x U0'}").id
    if amps is None:
        amps = 0.0
    if role == "dc_isolator":
        voc = max(max_dc_voc(project, catalog, node_id) or 0.0, hint.voltage_v or 0.0)
        ways = project.network.node(node_id).attributes.get(ISOLATOR_WAYS)
        if not isinstance(ways, int) or ways < 1:
            return pick_rated(catalog, ["dc_isolator"], amps, voc, hint.poles).id
        for poles in hint.poles:
            try:
                return pick_rated(catalog, ["dc_isolator"], amps * min(poles // 2, ways) / ways, voc, (poles,)).id
            except NoFit:
                continue
        raise NoFit(f"no catalog DC isolator switches {ways} string circuits of {amps / ways:.1f} A at {voc:.0f} V")
    if role == "string_fuse":
        voc = max_dc_voc(project, catalog, node_id) or 0.0
        feeds = [f for f in string_feeds(project, catalog) if node_id in f.path]
        net = project.network
        k = max((per_instance(net, f.string.id, node_id) for f in feeds), default=1)
        isc = max((f.module.isc for f in feeds), default=amps / ISC_FACTOR.value if amps else 0.0)
        need = k * STRING_FUSE_MIN.value * isc
        fuse = pick_rated(catalog, ["fuse_gpv"], need + 1e-6, voc, hint.poles)
        limit = min((f.module.max_series_fuse for f in feeds if f.module.max_series_fuse is not None), default=None)
        tops = ([limit - (k - 1) * isc] if limit is not None else []) + ([STRING_FUSE_MAX.value * isc] if k == 1 else [])
        top = min(tops) if tops else None
        if top is not None and fuse.rated_current > top + 1e-9:
            raise NoFit(
                f"the smallest catalog gPV fuse rated above {k} x {STRING_FUSE_MIN.value:g} x {isc:g} A = {need:.1f} A at {voc:.0f} V is {fuse.rated_current:g} A, "
                f"above the upper limit {top:.1f} A (module max series fuse {limit if limit is not None else '-'} A, {STRING_FUSE_MAX.value:g} x Isc)"
            )
        s.assumptions.append(STRING_FUSE_MIN.assumption)
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
            s.blockers.append(f"{label(project.network, n.id)} ({n.kind}): {e}; left without a spec")
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
    return max([amps, *(trip_current(project.network.node(nid), spec) for nid, spec in s.guards.get(e.id, []))])


def _options(project: Project, catalog: Catalog, e: Edge, hint: EdgeHint, s: Sizing) -> list[CableOption]:
    ph = phases(project, catalog, e) if e.service is not Service.DC else 1
    options = cable_options(catalog, hint.family, e.laying, ph, hint.runs, project.design_basis.ambient_max_c)
    t = terminals(project, catalog, e)
    if t is not None:
        options = [o for o in options if t.admits(o.cable, o.runs)]
        s.assumptions.append(TERMINALS)
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
        s.ampacity_runs[e.id] = fits[0].runs
        s.ampacity[e.id] = fits[0]
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


VDROP_MAX_RUNS = 4
VDROP_MATERIAL_PP = 0.05
LV_MAX_V = 1000.0
VDROP_ORDER = (
    "Voltage drop is fitted on each path over its limit one step at a time: a larger conductor, or a parallel run up to "
    f"{VDROP_MAX_RUNS} per circuit or the runs its current already needs (design choice: piling runs only to meet voltage drop is not sensible LT practice), "
    "on the cable whose step removes the most drop per added conductor volume (sq.mm x m over all its instances); more than one cable per pole on an inverter "
    "output whose datasheet does not say its terminal takes them only when nothing else is left. An LT breaker is moved to a larger frame whose terminals take a "
    f"larger conductor only when no such step is left. Only a step that removes at least {VDROP_MATERIAL_PP:g} percentage points of the path's drop, or all of "
    "its excess, counts (design choice: cross-section is spent where the drop is). Afterwards every cable is stepped back down to the cheapest option that "
    "keeps every path through it within its limit. A path that still exceeds its limit is reported as a blocker naming the cable that limits it."
)
VDROP_MV = (
    f"Cables above {LV_MAX_V:g} V are sized by ampacity and short-circuit withstand only, never enlarged for the voltage drop of a path "
    "(design choice); their drop still counts in the path the rules check, and a path their drop keeps over its limit is reported."
)


def _run_cap(s: Sizing, eid: str) -> int:
    return max(VDROP_MAX_RUNS, s.ampacity_runs.get(eid, 1))


def _fittable(project: Project, e: Edge) -> bool:
    if e.service is Service.DC:
        return True
    v = _zone_v(project, e.src_node)
    return v is None or v <= LV_MAX_V


def _volume(net: ElectricalNetwork, e: Edge, o: CableOption) -> float:
    """Conductor volume of one option over every instance of the edge, sq.mm x m."""
    return net.instance_count(e.src_node) * (e.length_m or 0.0) * o.conductors * o.cable.size_mm2 * o.runs


def _upsizes(project: Project, catalog: Catalog, e: Edge, hint: EdgeHint, s: Sizing) -> list[CableOption]:
    """Options of lower resistance than the chosen one that still carry the current, with at most the run cap of parallel runs, cheapest first."""
    cur = s.chosen[e.id]
    need = _need(project, catalog, e, hint, s)
    cap = _run_cap(s, e.id)
    return [
        o for o in _options(project, catalog, e, hint, s)
        if o.iz >= need - 1e-9 and o.r_eff < cur.r_eff - 1e-12 and o.cost >= cur.cost and cur.runs <= o.runs <= cap
    ]  # fmt: skip


def _silent_runs(project: Project, catalog: Catalog, e: Edge, o: CableOption) -> bool:
    """More than one cable per pole on an inverter output whose datasheet does not give how many its terminal takes."""
    t = terminals(project, catalog, e, switchgear=False)
    return o.runs > 1 and t is not None and any(d.inverter and d.per_pole is None for d in t.devices)


def _best_step(project: Project, catalog: Catalog, edges: list[Edge], hints: dict[str, EdgeHint], s: Sizing, need_pp: float) -> tuple[str, CableOption] | None:
    """The material step (see VDROP_ORDER) removing the most drop per added conductor volume: for each edge and run count its cheapest material option."""
    net = project.network
    best, score = None, (True, 0.0)
    for e in edges:
        cur = s.chosen[e.id]
        before = _drop(project, catalog, e)
        seen: set[int] = set()
        for o in _upsizes(project, catalog, e, hints[e.id], s):
            if o.runs in seen:
                continue
            gain = before - _drop(project, catalog, _apply(e, o))
            if gain < need_pp - 1e-9:
                continue
            seen.add(o.runs)
            extra = max(_volume(net, e, o) - _volume(net, e, cur), 1e-9)
            key = (_silent_runs(project, catalog, e, o), -gain / extra)
            if best is None or key < score:
                best, score = (e.id, o), key
    return best


def fit_vdrop(project: Project, catalog: Catalog, hints: dict[str, EdgeHint], s: Sizing, node_hints: dict[str, NodeHint] | None = None) -> Project:
    """Lower the drop on each path over its limit one step at a time (see VDROP_ORDER) until every path fits or no step is left; where the
    terminals of an LT breaker are the limit, move that breaker to a larger frame (REFRAME). Then step every cable back down where the paths allow it."""
    node_hints = node_hints or {}
    for _ in range(MAX_VDROP_STEPS):
        over = []
        for what, edges, limit in vdrop_paths(project):
            drops = {e.id: _drop(project, catalog, e) for e in edges}
            if sum(drops.values()) > limit:
                over.append((what, edges, drops, limit))
        if not over:
            break
        s.assumptions.append(VDROP_ORDER)
        net = project.network
        changed: dict[str, Edge] = {}
        reframed = None
        for what, edges, drops, limit in over:
            if any(eid in changed for eid in drops):
                continue
            if any(eid in s.chosen and drops[eid] > 0 and not _fittable(project, net.edge_by_id[eid]) for eid in drops):
                s.assumptions.append(VDROP_MV)
            cands = [net.edge_by_id[eid] for eid in sorted(drops, key=lambda k: -drops[k]) if eid in s.chosen and drops[eid] > 0 and _fittable(project, net.edge_by_id[eid])]
            need_pp = min(sum(drops.values()) - limit, VDROP_MATERIAL_PP)
            step = _best_step(project, catalog, cands, hints, s, need_pp)
            if step is None:
                reframed = next((p for more in (False, True) for e in cands if (p := _reframe(project, catalog, e, hints[e.id], node_hints, s, more, need_pp)) is not None), None)
                if reframed is not None:
                    break
            if step is None:
                total = sum(drops.values())
                worst = max((e for e in edges if e.id in s.chosen), key=lambda e: drops[e.id], default=None)
                at = f"; the largest drop is {drops[worst.id]:.2f} % on {worst.id} ({s.chosen[worst.id].runs} x {s.chosen[worst.id].cable.callout()})" if worst else ""
                msg = (
                    f"{what}: voltage drop {total:.2f} % exceeds {limit:g} %: no larger conductor or breaker frame is left that materially lowers it and more parallel "
                    f"runs than {VDROP_MAX_RUNS} per circuit is not a sensible design{at}. Shorten the route (lengths or equipment placement) or raise the design basis limit"
                )
                if msg not in s.blockers:
                    s.blockers.append(msg)
                continue
            eid, o = step
            s.chosen[eid] = o
            changed[eid] = _apply(net.edge_by_id[eid], o)
        if reframed is not None:
            project = _rebuild(reframed, edges=[changed.get(e.id) or e for e in reframed.network.edges]) if changed else reframed
            continue
        if not changed:
            break
        project = _rebuild(project, edges=[changed.get(e.id) or e for e in net.edges])
    project = _step_down(project, catalog, hints, node_hints, s)
    return _explain_upsizes(project, catalog, s)


def _paths_fit(project: Project, catalog: Catalog, eid: str, was: dict[str, float]) -> bool:
    """Every path through edge `eid` is within its limit, or no worse than it was when it was already over."""
    for what, edges, limit in vdrop_paths(project):
        if not any(e.id == eid for e in edges):
            continue
        d = sum(_drop(project, catalog, e) for e in edges)
        if d > max(limit, was.get(what, 0.0)) + 1e-9:
            return False
    return True


def _step_down(project: Project, catalog: Catalog, hints: dict[str, EdgeHint], node_hints: dict[str, NodeHint], s: Sizing) -> Project:
    """Give each cable enlarged for voltage drop the cheapest option that still keeps every path through it within its limit (largest conductor
    volume first), then put back any breaker moved to a larger frame whose original frame takes the final cable."""
    was = {what: sum(_drop(project, catalog, e) for e in edges) for what, edges, limit in vdrop_paths(project)}
    net = project.network
    grown = [eid for eid, o in s.chosen.items() if eid in s.ampacity and o != s.ampacity[eid]]
    for eid in sorted(grown, key=lambda k: -_volume(net, net.edge_by_id[k], s.chosen[k])):
        e = project.network.edge_by_id[eid]
        cur = s.chosen[eid]
        need = _need(project, catalog, e, hints[eid], s)
        for o in _options(project, catalog, e, hints[eid], s):
            if o.cost >= cur.cost or o.iz < need - 1e-9 or o.runs > _run_cap(s, eid):
                continue
            trial = _rebuild(project, edges=[_apply(x, o) if x.id == eid else x for x in project.network.edges])
            if _paths_fit(trial, catalog, eid, was):
                project, s.chosen[eid] = trial, o
                break
    for brk_id, old in list(s.reframed.items()):
        n = project.network.node(brk_id)
        spec = catalog.get(old)
        ok = all(
            (spec.terminal_max_mm2 is None or s.chosen[e.id].cable.size_mm2 <= spec.terminal_max_mm2) and (spec.terminal_cables_per_pole is None or s.chosen[e.id].runs <= spec.terminal_cables_per_pole)
            and s.chosen[e.id].iz >= (spec.rated_current or 0.0) - 1e-9
            for e in project.network.edges_at(brk_id) if e.id in s.chosen
        )  # fmt: skip
        if ok and n.spec != old:
            project = _rebuild(project, nodes=[x.model_copy(update={"spec": old}) if x.id == brk_id else x for x in project.network.nodes])
            s.guards = None
            del s.reframed[brk_id]
            s.assumptions.append(f"{label(project.network, brk_id)}: back to {spec.model}; after stepping the cables down its terminals take them.")
    for brk_id, old in s.reframed.items():
        spec, now = catalog.get(old), catalog.get(project.network.node(brk_id).spec)
        lands = [s.chosen[e.id] for e in project.network.edges_at(brk_id) if e.id in s.chosen]
        took = f"{spec.terminal_cables_per_pole or 'any number of'} x {spec.terminal_max_mm2:g} sq.mm per pole" if spec.terminal_max_mm2 else f"{spec.terminal_cables_per_pole} cable(s) per pole"
        s.assumptions.append(
            f"{label(project.network, brk_id)}: {spec.model} takes at most {took}; voltage drop needs {', '.join(f'{o.runs} x {o.cable.callout()}' for o in lands)}, so {now.model} is used."
        )
    return project


def _explain_upsizes(project: Project, catalog: Catalog, s: Sizing) -> Project:
    """Say, on the edge (Edge.sized_for) and in the assumptions, why each cable is larger than ampacity and fault withstand need."""
    net = project.network
    paths = vdrop_paths(project)
    notes: dict[str, str] = {}
    for eid, o in s.chosen.items():
        amp = s.ampacity.get(eid)
        if amp is None or o.cost <= amp.cost:
            continue
        e = net.edge_by_id[eid]
        through = [(what, edges, limit) for what, edges, limit in paths if any(x.id == eid for x in edges)]
        if not through:
            continue
        what, edges, limit = max(through, key=lambda p: sum(_drop(project, catalog, x) for x in p[1]))
        now = sum(_drop(project, catalog, x) for x in edges)
        alone = _drop(project, catalog, _apply(e, amp)) - _drop(project, catalog, e)
        notes[eid] = (
            f"{o.runs} x {o.cable.callout()} instead of {amp.runs} x {amp.cable.callout()} (enough for the current and the fault) for voltage drop: "
            f"the worst path through it ({what}) is {now:.2f} % against the {limit:g} % limit and would be {now + alone:.2f} % with the smaller cable."
        )
        s.assumptions.append(f"{edge_label(net, e)}: {notes[eid]}")
    if not notes:
        return project
    return _rebuild(project, edges=[x.model_copy(update={"sized_for": notes[x.id]}) if x.id in notes else x for x in net.edges])


REFRAME = (
    "Where voltage drop needs a cable larger than an LT breaker's terminals take, the breaker is replaced by the smallest catalog breaker with the same poles, rated for the voltage and the fault, "
    "whose terminals take the cable (a larger frame, rated at least the original); the cable then carries the new rating (In <= Iz) and "
    "protection.oversize reports the larger rating."
)


REFRAME_MAX = 3.0
REFRAME_CAP = (
    f"A breaker is moved to a larger frame for voltage drop only up to {REFRAME_MAX:g} x its design requirement (1.25 x Ib) (design choice: a feeder breaker rated "
    "several times its current to land a larger cable is not sensible); beyond that the drop is met on other cables or reported."
)


def _ib(project: Project, catalog: Catalog, e: Edge) -> float:
    ib = edge_current(project, catalog, e)
    return ib.value if ib is not None else 0.0


def _reframe(project: Project, catalog: Catalog, e: Edge, hint: EdgeHint, node_hints: dict[str, NodeHint], s: Sizing, more_runs: bool = False, need_pp: float = 0.0) -> Project | None:
    """Swap the terminal-limited LT breaker at an end of `e` for a larger frame whose terminals take a lower-resistance cable (see REFRAME): at
    the same number of runs, or with `more_runs` up to the run cap."""
    t = terminals(project, catalog, e, equipment=False)
    fixed = terminals(project, catalog, e, switchgear=False)
    if t is None:
        return None
    net = project.network
    ends = [net.node(n) for n in (e.src_node, e.dst_node) if n in node_hints and node_hints[n].role == "lv_breaker" and net.node(n).spec is not None]
    if not ends:
        return None
    brk = ends[0]
    old = catalog.get(brk.spec)
    cur = s.chosen[e.id]
    ph = phases(project, catalog, e)
    fault = prospective_fault(project, catalog, brk.id)
    v = _zone_v(project, brk.id) or 0.0
    pool = sorted(
        (p for p in catalog.filter("protection") if isinstance(p, ProtectionSpec) and p.device in ("mcb", "mccb", "acb") and p.rated_current is not None
         and p.rated_current >= (old.rated_current or 0) and p.rated_voltage >= v and p.poles == old.poles and p.id != old.id
         and (fault is None or covers_fault(p, fault.ka))),
        key=lambda p: (p.rated_current, p.manufacturer != old.manufacturer, p.breaking_capacity_ka or 0.0, p.id),
    )  # fmt: skip
    for o in cable_options(catalog, hint.family, e.laying, ph, hint.runs, project.design_basis.ambient_max_c):
        if o.r_eff >= cur.r_eff - 1e-12 or t.admits(o.cable, o.runs) or (fixed is not None and not fixed.admits(o.cable, o.runs)):
            continue
        if (o.runs > cur.runs) != more_runs or o.runs > _run_cap(s, e.id):
            continue
        if _drop(project, catalog, e) - _drop(project, catalog, _apply(e, o)) < need_pp - 1e-9:
            continue
        p = next((p for p in pool if (p.terminal_max_mm2 is None or o.cable.size_mm2 <= p.terminal_max_mm2) and (p.terminal_cables_per_pole is None or o.runs <= p.terminal_cables_per_pole)), None)
        need = _need(project, catalog, e, hint, s)
        if p is None or o.iz < max(p.rated_current, need) - 1e-9 or p.rated_current > max(old.rated_current or 0.0, REFRAME_MAX * CONTINUOUS_FACTOR.value * _ib(project, catalog, e)) + 1e-9:
            continue
        nodes = [n.model_copy(update={"spec": p.id}) if n.id == brk.id else n for n in net.nodes]
        edges = [_apply(x, o) if x.id == e.id else x for x in net.edges]
        s.chosen[e.id] = o
        s.guards = None
        s.reframed.setdefault(brk.id, old.id)
        s.assumptions.extend((REFRAME, REFRAME_CAP))
        return _rebuild(project, nodes=nodes, edges=edges)
    return None


BAND_SERVICES = frozenset({Service.DC, Service.LV_AC})
BANDS = (
    "String and LT cables are sized per length band: each routed instance takes the smallest cable that carries the design current, withstands the fault "
    "and whose voltage drop at its own routed length is at most the drop the template cable has at the worst-case length, so every instance stays within "
    "the path limit the rules check on the worst case; the cable schedule and BOM list each instance with its band's cable."
)
MAX_BANDS = 2
BAND_CAP = (
    f"At most {MAX_BANDS} length bands besides the worst-case cable per circuit type (design choice, so a circuit type uses at most {MAX_BANDS + 1} cable sizes): "
    "the shortest instances take the smallest kept band's cable, which is larger than they need."
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
        if len(bands) > MAX_BANDS:
            bands = bands[-MAX_BANDS:]
            s.assumptions.append(BAND_CAP)
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
    p = fit_vdrop(p, catalog, edge_hints, s, node_hints)
    s.project = p
    s.assumptions = list(dict.fromkeys(s.assumptions))
    return s

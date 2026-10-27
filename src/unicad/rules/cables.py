"""Cable presence, current-carrying capacity and overload coordination with protective devices."""

import math
from collections.abc import Iterator
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.catalog import CableSpec, Conductor, ProtectionSpec
from unicad.domain.network import Edge, ElectricalNetwork, Laying, NodeKind, Service
from unicad.domain.project import Project
from unicad.rules.faults import LV_BREAKER_CLEARING, LV_BREAKER_CLEARING_S, default_clearing, fault_at, prospective_in
from unicad.rules.grouping import grouping
from unicad.rules.model import Calc, Finding, Quantity, Severity
from unicad.rules.registry import rule
from unicad.rules.thresholds import K_XLPE_AL, K_XLPE_CU
from unicad.rules.topology import AC_SERVICES, GENERATORS, edge_current, edge_zone, label, zone_at

TAPS = frozenset({NodeKind.DC_SPD, NodeKind.AC_SPD, NodeKind.LIGHTNING_ARRESTER, NodeKind.PT})
PANELS = frozenset({NodeKind.ACDB, NodeKind.LT_PANEL, NodeKind.HT_PANEL, NodeKind.BUSBAR})
PANEL_MOUNTED = frozenset({NodeKind.BREAKER, NodeKind.VCB, NodeKind.ISOLATOR, NodeKind.FUSE, NodeKind.CT, NodeKind.METER, NodeKind.RMU})
OVERLOAD_DEVICES = frozenset({"mcb", "mccb", "acb", "fuse_gpv", "fuse_hrc"})
COLUMN = {
    Laying.AIR: "air",
    Laying.TRAY: "air",
    Laying.OVERHEAD: "air",
    Laying.TRENCH: "air",
    Laying.BURIED_DIRECT: "ground",
    Laying.BURIED_DUCT: "duct",
    Laying.CONDUIT: "duct",
}
COLUMN_NOTES = {
    Laying.TRENCH: "Cables in a trench are rated with the in-air ampacity column; apply trench grouping through the edge derating.",
    Laying.CONDUIT: "Cables in conduit are rated with the in-duct ampacity column.",
    Laying.OVERHEAD: "Overhead cables are rated with the in-air ampacity column.",
}
NO_DERATING = "No derating factor is given on the edge; 1.0 is used, so the ampacity is the catalog base rating (ambient, grouping and depth corrections not applied)."
PAIR_FALLBACK = "Single-core cable without a two-cable rating in the catalog: its catalog column value is used for the DC or single-phase pair."
IS_732 = "IS 732"
OVERLOAD = "IS 732 / IEC 60364-4-43"
K_BY_CABLE = {(Conductor.CU, "xlpe"): K_XLPE_CU, (Conductor.AL, "xlpe"): K_XLPE_AL}


def edge_label(net: ElectricalNetwork, e: Edge) -> str:
    return f"edge {e.id} ({label(net, e.src_node)} -> {label(net, e.dst_node)})"


def cable_of(catalog: Catalog, e: Edge) -> CableSpec | None:
    if e.cable is None:
        return None
    if isinstance(e.cable, CableSpec):
        return e.cable
    spec = catalog.get(e.cable)
    return spec if isinstance(spec, CableSpec) else None


def needs_cable(net: ElectricalNetwork, e: Edge) -> bool:
    """Taps to line devices carry no load current, and links without a laying between switchgear, metering and a panel are taken as busbar connections inside the panel."""
    a, b = net.node(e.src_node).kind, net.node(e.dst_node).kind
    if a in TAPS or b in TAPS:
        return False
    inside = PANELS | PANEL_MOUNTED
    busbar_link = a in inside and b in inside and not (a in PANELS and b in PANELS)
    return not (busbar_link and e.laying is None)


def phases(project: Project, catalog: Catalog, e: Edge) -> int:
    """1 for DC and for AC circuits fed only by single-phase inverters, else 3."""
    if e.service is Service.DC:
        return 1
    net = project.network
    gens = [net.node(g) for g in (e.src_node, *net.upstream(e.src_node)) if net.node(g).kind in GENERATORS and net.node(g).spec]
    ph = {getattr(catalog.get(g.spec), "phases", 3) for g in gens}
    return 1 if ph == {1} else 3


@dataclass(frozen=True)
class Ampacity:
    """Derated current-carrying capacity Iz of one edge instance, all runs together."""

    cable: CableSpec
    base: float
    column: str
    derating: float
    derating_given: bool
    runs: int
    assumptions: tuple[str, ...]
    grouping_source: str | None = None

    @property
    def value(self) -> float:
        return self.base * self.derating * self.runs

    def calc(self) -> Calc:
        return Calc(
            value=self.value,
            unit="A",
            formula="Iz = I_table x k_derating x runs",
            inputs=(
                Quantity(name="I_table", value=self.base, unit="A", source=f"{self.cable.id}.{self.column}"),
                Quantity(name="k_derating", value=self.derating, source="edge.derating" if self.derating_given else self.grouping_source or "default 1.0"),
                Quantity(name="runs", value=self.runs, source="edge.runs"),
            ),
            assumptions=self.assumptions,
        )


def table_ampacity(cable: CableSpec, laying: Laying, n_phases: int) -> tuple[float | None, str, tuple[str, ...]]:
    """Catalog ampacity for a laying method: the field used and its value. Single-core cables use the two-cable rating for DC and single-phase pairs, the three-cable circuit rating for three-phase."""
    col = COLUMN[laying]
    notes = (COLUMN_NOTES[laying],) if laying in COLUMN_NOTES else ()
    if cable.cores == 1 and n_phases == 1:
        pair = getattr(cable.ampacity_2x1c, col, None) if cable.ampacity_2x1c else None
        if pair is not None:
            return pair, f"ampacity_2x1c.{col}", notes
        notes = (*notes, PAIR_FALLBACK)
    return getattr(cable, f"ampacity_{col}"), f"ampacity_{col}", notes


def edge_ampacity(project: Project, catalog: Catalog, e: Edge) -> Ampacity | None:
    cable = cable_of(catalog, e)
    if cable is None or e.laying is None:
        return None
    base, column, notes = table_ampacity(cable, e.laying, phases(project, catalog, e))
    if base is None:
        return None
    given = e.derating is not None
    if given:
        return Ampacity(cable, base, column, e.derating, True, e.runs, notes)
    g = grouping(cable, e.laying, e.runs, e.service is Service.DC)
    if g is not None and g.factor is not None:
        return Ampacity(cable, base, column, g.factor, False, e.runs, (*notes, f"No derating on the edge: the {g.source} is applied; ambient and depth corrections are not."), g.source)
    return Ampacity(cable, base, column, 1.0, False, e.runs, (*notes, NO_DERATING, *((g.source,) if g else ())))


@rule("cables.missing", "Power connections have a cable and a length")
def missing(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Every power edge needs a cable (catalog id or inline spec) and a route length so that ampacity and voltage drop can be checked. Taps to SPDs, lightning arresters and PTs are exempt, and so are links with no laying between switchgear, CTs, meters and a panel or busbar: they are taken as busbar connections inside the panel. Panel-to-panel links, and any link given a laying, need a cable."""
    net = project.network
    for e in net.edges:
        if not e.is_power or not needs_cable(net, e):
            continue
        gaps = [what for what, absent in (("cable", e.cable is None), ("length_m", e.length_m is None)) if absent]
        if gaps:
            yield Finding(
                rule_id="cables.missing",
                severity=Severity.WARNING,
                subjects=(e.id,),
                message=f"{edge_label(net, e)} ({e.service}) has no {' or '.join(gaps)}; cable sizing and voltage drop are not checked for it",
            )


@rule("cables.ampacity", "Cable current-carrying capacity covers the design current", clause=IS_732)
def ampacity(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Design current Ib (DC: 1.25 x Isc of the strings carried; AC: maximum inverter current at the zone voltage) must not exceed Iz = catalog ampacity x derating x runs.

    The ampacity column follows the laying: air, tray, trench and overhead use the in-air rating, buried_direct the in-ground rating, buried_duct and conduit the in-duct rating. Single-core cables use the two-cable rating for DC and single-phase circuits and the three-cable rating for three-phase. The derating is the edge's `derating`; when absent, parallel runs (runs > 1) of AC cables take the group rating factor of the KEI Technical Handbook for the laying and the number of circuits (next tabulated count up), and a single run, or a laying or count the handbook does not tabulate, uses 1.0; an info finding says which. A cable without a rating for its laying, or an edge without a laying, is a warning."""
    net = project.network
    for e in net.edges:
        cable = cable_of(catalog, e)
        if not e.is_power or cable is None:
            continue
        ib = edge_current(project, catalog, e)
        if ib is None:
            continue
        where = f"{edge_label(net, e)} {cable.callout()}"
        if e.laying is None:
            yield Finding(rule_id="cables.ampacity", severity=Severity.WARNING, subjects=(e.id,), message=f"{where}: no laying method; ampacity not checked")
            continue
        iz = edge_ampacity(project, catalog, e)
        if iz is None:
            col = COLUMN[e.laying]
            yield Finding(
                rule_id="cables.ampacity",
                severity=Severity.WARNING,
                subjects=(e.id,),
                message=f"{where}: catalog {cable.id} has no {col} ampacity for laying '{e.laying}'; ampacity not checked",
            )
            continue
        z = iz.calc()
        common = {
            "subjects": (e.id,),
            "measured": round(ib.value, 2),
            "limit": round(z.value, 2),
            "unit": "A",
            "formula": f"Ib <= Iz; {ib.formula}; {z.formula}",
            "inputs": (*ib.inputs, *z.inputs),
            "assumptions": (*ib.assumptions, *z.assumptions),
        }
        working = f"Iz = {iz.base:g} A ({iz.column}) x {iz.derating:g} x {iz.runs} = {z.value:.1f} A"
        if ib.value > z.value:
            yield Finding(rule_id="cables.ampacity", severity=Severity.ERROR, message=f"{where}: Ib {ib.value:.1f} A exceeds {working}", **common)
        elif not iz.derating_given:
            taken = f"group rating factor {iz.derating:g} for {iz.runs} parallel runs" if iz.grouping_source else "taken as 1.0"
            yield Finding(rule_id="cables.ampacity", severity=Severity.INFO, message=f"{where}: derating not specified, {taken}; Ib {ib.value:.1f} A within {working}", **common)


IN_LINE = frozenset({NodeKind.CT, NodeKind.METER, NodeKind.ISOLATOR, NodeKind.DC_ISOLATOR})


def protected_edges(net: ElectricalNetwork, node_id: str) -> tuple[Edge, ...]:
    """Power edges in series with an overcurrent device: those at it and those reached through in-line CTs, meters and isolators, up to the
    next panel, switching device or equipment. An isolator or CT does not interrupt overload current, so the cable beyond it is still the
    device's to protect."""
    out: dict[str, Edge] = {}
    seen, stack = {node_id}, [node_id]
    while stack:
        at = stack.pop()
        for e in net.edges_at(at):
            if not e.is_power or e.id in out:
                continue
            other = e.dst_node if e.src_node == at else e.src_node
            if net.node(other).kind in TAPS:
                continue
            out[e.id] = e
            if other not in seen and net.node(other).kind in IN_LINE:
                seen.add(other)
                stack.append(other)
    return tuple(out.values())


def overload_device(catalog: Catalog, node) -> ProtectionSpec | None:
    if node.spec is None:
        return None
    spec = node.spec if isinstance(node.spec, ProtectionSpec) else catalog.get(node.spec)
    if not isinstance(spec, ProtectionSpec) or spec.device not in OVERLOAD_DEVICES or spec.rated_current is None:
        return None
    return spec


def protectors(project: Project, catalog: Catalog) -> dict[str, list[tuple[str, ProtectionSpec]]]:
    """The overcurrent devices (node id, spec) protecting each edge (see protected_edges), by edge id."""
    net = project.network
    out: dict[str, list[tuple[str, ProtectionSpec]]] = {}
    for n in net.nodes:
        spec = overload_device(catalog, n)
        if spec is None:
            continue
        for e in protected_edges(net, n.id):
            out.setdefault(e.id, []).append((n.id, spec))
    return out


def protecting_in(project: Project, catalog: Catalog) -> dict[str, float]:
    """Largest rated current In of the overcurrent devices protecting each edge, by edge id."""
    return {eid: max(s.rated_current for _, s in devs) for eid, devs in protectors(project, catalog).items()}


LV_MAX_V = 1000.0
LV_BREAKERS = frozenset({"mcb", "mccb", "acb"})


@dataclass(frozen=True)
class CableFault:
    """The fault a cable must withstand: current, clearing time, and where each comes from."""

    voltage_v: float
    ka: float
    clearing_s: float
    where: str
    inputs: tuple[Quantity, ...]
    assumptions: tuple[str, ...]


def cable_fault(project: Project, catalog: Catalog, e: Edge, guards: dict[str, list[tuple[str, ProtectionSpec]]] | None = None) -> CableFault | None:
    """Prospective fault on an AC cable and its clearing time. The current is the level declared for the cable's system voltage or, in an LT zone a
    transformer feeds, the transformer-fed current when larger (as protection.fault_level). The time is the instantaneous trip of the LT breaker
    protecting the cable (LV_BREAKER_CLEARING_S) when one does, else the clearing time of the declared level, else the default chain."""
    net, basis = project.network, project.design_basis
    zone = edge_zone(net, e)
    v = zone.voltage_v if zone else None
    if v is None:
        return None
    declared = fault_at(basis, v)
    fed = prospective_in(project, catalog, zone) if v <= LV_MAX_V else None
    if fed is not None and fed.where != "transformer-fed":
        fed = None
    if declared is None and fed is None:
        return None
    if fed is not None and (declared is None or fed.ka > declared.ka):
        ka, where = fed.ka, f"transformer-fed ({fed.formula})"
        current = (Quantity(name="I_fault", value=fed.ka, unit="kA", source=where), *fed.inputs)
        notes: tuple[str, ...] = fed.assumptions
    else:
        ka, where, current, notes = declared.ka, declared.where, declared.inputs()[:1], ()
    guards = protectors(project, catalog) if guards is None else guards
    breaker = next(((nid, s) for nid, s in guards.get(e.id, []) if s.device in LV_BREAKERS), None) if v <= LV_MAX_V else None
    if breaker is not None:
        nid, spec = breaker
        t = LV_BREAKER_CLEARING_S
        time = Quantity(name="t", value=t, unit="s", source=f"assumption: instantaneous trip of {spec.model} ({spec.device.upper()}), {t:g} s")
        tnote = (f"{label(net, nid)} {spec.model} ({spec.device.upper()}) protects the cable. {LV_BREAKER_CLEARING}",)
    elif (hv := _hv_clearing(project, zone, v)) is not None and (declared is None or fed is not None):
        t, time, tnote = hv
    elif declared is not None:
        t, time, tnote = declared.clearing_s, declared.inputs()[1], declared.assumptions
    else:
        t, why = default_clearing(basis, v)
        time, tnote = Quantity(name="t", value=t, unit="s", source=why), (why,)
    return CableFault(v, ka, t, where, (*current, time), (*notes, *tnote))


HV_PROTECTIVE = frozenset({NodeKind.VCB, NodeKind.RMU, NodeKind.BREAKER, NodeKind.FUSE})


def _hv_clearing(project: Project, zone, v: float) -> tuple[float, Quantity, tuple[str, ...]] | None:
    """Clearing time of an LT cable fault fed by a transformer and not guarded by an LT breaker: the breaker on that transformer's HV side clears it,
    in the clearing time of the HV fault level (its relay and break times are not modelled). None when no such transformer and breaker exist."""
    net, basis = project.network, project.design_basis
    for n in net.by_kind(NodeKind.TRANSFORMER):
        if zone_at(net, n.id, "lv") != zone:
            continue
        hv_zone = zone_at(net, n.id, "hv")
        if hv_zone is None or hv_zone.voltage_v is None:
            continue
        ends = [e.dst_node if e.src_node == n.id else e.src_node for e in net.edges if e.is_power and e.service is not Service.DC and n.id in (e.src_node, e.dst_node) and e.id in hv_zone.edge_ids]
        brk = next((m for m in ends if net.node(m).kind in HV_PROTECTIVE), None)
        if brk is None:
            continue
        level = fault_at(basis, hv_zone.voltage_v)
        if level is not None:
            t, basis_note = level.clearing_s, f"the clearing time of the {hv_zone.voltage_v:g} V fault level ({level.where})"
        else:
            t, basis_note = default_clearing(basis, hv_zone.voltage_v)
        note = (
            f"An LT fault on this cable is fed by {label(net, n.id)} and cleared by {label(net, brk)} on its HV side (no LT breaker guards the cable; an inverter's "
            f"integrated AC breaker clears only the inverter's own contribution). The breaker's relay and break times are not modelled, so {t:g} s is taken: {basis_note} (assumption)."
        )
        return t, Quantity(name="t", value=t, unit="s", source=f"assumption: {label(net, brk)} on the {label(net, n.id)} HV side, {t:g} s"), (note,)
    return None


def _protected_cables(project: Project, catalog: Catalog, node_id: str) -> Iterator[tuple[Edge, Ampacity]]:
    for e in protected_edges(project.network, node_id):
        if (iz := edge_ampacity(project, catalog, e)) is not None:
            yield e, iz


@rule("cables.protection", "Overcurrent device rating does not exceed the cable ampacity", clause=OVERLOAD)
def protection(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Overload coordination Ib <= In <= Iz: the rated current In of a breaker or fuse must not exceed the derated ampacity Iz of each cable it protects, so the device trips before the cable overheats.
    A device protects the cables connected to it and those reached from it through in-line CTs, meters and isolators (which do not interrupt overload current), up to the next panel, switching device
    or equipment. Ib <= In is protection.rated_current. MV breakers are not checked: their trip settings live in relays the network does not model.

    Worked example: an ACB of In 1250 A feeding a CT, an isolator and then a cable of Iz 980 A to the LT panel is an error (1250 A > 980 A), although the cable does not touch the ACB."""
    net = project.network
    for n in net.nodes:
        spec = overload_device(catalog, n)
        if spec is None:
            continue
        for e, iz in _protected_cables(project, catalog, n.id):
            z = iz.calc()
            if spec.rated_current <= z.value:
                continue
            yield Finding(
                rule_id="cables.protection",
                severity=Severity.ERROR,
                subjects=(n.id, e.id),
                message=f"{label(net, n.id)} {spec.model}: In {spec.rated_current:g} A exceeds the ampacity of {edge_label(net, e)} {iz.cable.callout()}: Iz = {iz.base:g} A x {iz.derating:g} x {iz.runs} = {z.value:.1f} A",
                measured=spec.rated_current,
                limit=round(z.value, 2),
                unit="A",
                formula=f"In <= Iz; {z.formula}",
                inputs=(Quantity(name="In", value=spec.rated_current, unit="A", source=spec.id), *z.inputs),
                assumptions=z.assumptions,
            )



@rule("cables.short_circuit", "Cable conductor withstands the prospective fault", clause=OVERLOAD)
def short_circuit(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """The conductor of every AC power cable must carry the prospective fault current for the clearing time without overheating (adiabatic heating): A >= I sqrt(t) / k, where A is the conductor
    area (area x parallel runs), I the three-phase fault current and t its clearing time, and k the conductor and insulation constant.

    I is the level declared for the cable's system voltage (design_basis.fault_levels) or, in an LT zone (up to 1000 V) fed by a transformer, the transformer-fed current of
    protection.fault_level when that is larger. t depends on the voltage level: an LT cable protected by an MCB, MCCB or ACB (directly, or through in-line CTs, meters and
    isolators as for cables.protection) is cleared by the breaker's instantaneous trip, taken as 0.1 s (a labelled assumption: the shortest duration IEC 60364-4-43 applies the
    adiabatic formula to); every other cable uses the clearing time of its level, else design_basis.earthing.fault_clearing_s, else 1 s, and the finding says which.
    k is 143 A s^0.5/mm2 for XLPE copper and 94 for XLPE aluminium (90 degC to 250 degC, from the KEI Technical Handbook short circuit table; the Polycab catalogue agrees). The fault is taken at the
    cable's source end, which is conservative (cable impedance only lowers it), and only the conductor is checked, not the armour or screen. Other insulations have no cited k and are reported as not
    checked. Where no fault current is known for a cable's system voltage a single info finding lists the voltages; DC string cables are not checked because a string cannot deliver more than about 1.25 Isc.

    Worked examples: a 33 kV feeder, fault level 25 kA cleared in 1 s, aluminium XLPE: I sqrt(t) / k = 25000 x 1 / 94 = 266 mm2, so one run of 240 sq.mm is an error
    (it withstands 22.6 kA) and one run of 300 sq.mm passes (28.2 kA). A 415 V cable behind an ACB with a 25 kA level: 25000 x sqrt(0.1) / 94 = 84.1 mm2, so one run of 95 sq.mm passes."""
    net = project.network
    unchecked: dict[str, int] = {}
    guards = protectors(project, catalog)
    for e in net.edges:
        cable = cable_of(catalog, e)
        if not e.is_power or cable is None or e.service not in AC_SERVICES:
            continue
        zone = edge_zone(net, e)
        fault = cable_fault(project, catalog, e, guards)
        if fault is None:
            key = f"{zone.voltage_v:g} V" if zone and zone.voltage_v else "undeclared voltage"
            unchecked[key] = unchecked.get(key, 0) + 1
            continue
        k = K_BY_CABLE.get((cable.conductor, cable.insulation))
        where = f"{edge_label(net, e)} {cable.callout()}"
        if k is None:
            yield Finding(
                rule_id="cables.short_circuit", severity=Severity.INFO, subjects=(e.id,),
                message=f"{where}: no cited short-circuit constant k for {cable.conductor.value.upper()} {cable.insulation.upper()} conductors; withstand not checked",
            )  # fmt: skip
            continue
        have = cable.size_mm2 * e.runs
        need = fault.ka * 1000 * math.sqrt(fault.clearing_s) / k.value
        withstand = k.value * have / math.sqrt(fault.clearing_s) / 1000
        ok = have >= need
        working = f"A = {cable.size_mm2:g} mm2 x {e.runs} run(s) = {have:g} mm2 against I sqrt(t) / k = {fault.ka * 1000:g} x sqrt({fault.clearing_s:g}) / {k.value:g} = {need:.1f} mm2 ({withstand:.1f} kA for {fault.clearing_s:g} s)"
        yield Finding(
            rule_id="cables.short_circuit", severity=Severity.INFO if ok else Severity.ERROR, subjects=(e.id,),
            message=f"{where}: conductor {'withstands' if ok else 'cannot withstand'} the {fault.ka:g} kA fault at {fault.voltage_v:g} V: {working}",
            measured=round(have, 2), limit=round(need, 2), unit="mm2", formula="A >= I sqrt(t) / k",
            inputs=(
                *fault.inputs,
                Quantity(name="k", value=k.value, unit=k.unit, source=f"threshold.{k.name}"),
                Quantity(name="A", value=have, unit="mm2", source=cable.id),
            ),
            assumptions=(*fault.assumptions, k.assumption),
        )  # fmt: skip
    if unchecked:
        listed = ", ".join(f"{v} ({n} cables)" for v, n in sorted(unchecked.items()))
        yield Finding(
            rule_id="cables.short_circuit", severity=Severity.INFO, subjects=(),
            message=f"short-circuit withstand not checked: no fault level in design_basis.fault_levels for {listed}",
        )  # fmt: skip

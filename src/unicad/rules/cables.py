"""Cable presence, current-carrying capacity and overload coordination with protective devices."""

import math
from collections.abc import Iterator
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.catalog import CableSpec, Conductor, InverterSpec, ProtectionSpec
from unicad.domain.network import Edge, ElectricalNetwork, Laying, NodeKind, Service
from unicad.domain.project import Project
from unicad.rules.faults import LV_BREAKER_CLEARING, LV_BREAKER_CLEARING_S, default_clearing, fault_at, prospective_in
from unicad.rules.derating import Derating, derating
from unicad.rules.model import Calc, Finding, Quantity, Severity
from unicad.rules.registry import rule
from unicad.rules.settings import trip_current
from unicad.rules.thresholds import K_XLPE_AL, K_XLPE_CU
from unicad.rules.topology import AC_SERVICES, GENERATORS, edge_current, edge_zone, exports, label, per_instance, zone_at

TAPS = frozenset({NodeKind.DC_SPD, NodeKind.AC_SPD, NodeKind.LIGHTNING_ARRESTER, NodeKind.PT})
PANELS = frozenset({NodeKind.ACDB, NodeKind.LT_PANEL, NodeKind.HT_PANEL, NodeKind.BUSBAR, NodeKind.SCB, NodeKind.DC_FUSE_BOX})
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
    Laying.TRENCH: "Cables in a covered trench are rated with the in-air ampacity column times the KEI covered-trench rating factor for the circuits laid together.",
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
    parts: Derating | None = None

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
    if e.derating is not None:
        return Ampacity(cable, base, column, e.derating, True, e.runs, notes)
    d = derating(cable, e.laying, e.runs, e.service is Service.DC, project.design_basis.ambient_max_c)
    if d.factor != 1.0 or d.source:
        return Ampacity(cable, base, column, d.factor, False, e.runs, (*notes, f"No derating on the edge: {d.source}.", *d.notes), d.source, d)
    return Ampacity(cable, base, column, 1.0, False, e.runs, (*notes, NO_DERATING))


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


def _taken(iz: Ampacity) -> str:
    d = iz.parts
    if d is None:
        return "taken as 1.0"
    parts = [f"temperature factor {d.temperature:g}"] if d.temperature != 1.0 else []
    if d.group != 1.0:
        parts.append(f"group rating factor {d.group:g} for {iz.runs} parallel runs" if iz.runs > 1 else f"covered-trench factor {d.group:g}")
    return f"derating {iz.derating:g} = " + " x ".join(parts) if parts else f"derating {iz.derating:g} ({d.source})"


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
            "assumptions": (*ib.assumptions, *z.assumptions, *((f"Sized above its current: {e.sized_for}",) if e.sized_for else ())),
        }
        working = f"Iz = {iz.base:g} A ({iz.column}) x {iz.derating:g} x {iz.runs} = {z.value:.1f} A"
        if ib.value > z.value:
            yield Finding(rule_id="cables.ampacity", severity=Severity.ERROR, message=f"{where}: Ib {ib.value:.1f} A exceeds {working}", **common)
        elif not iz.derating_given:
            taken = _taken(iz)
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
    """Largest trip current (the long-time setting Ir where one is set, else In) of the overcurrent devices protecting each edge, by edge id."""
    net = project.network
    return {eid: max(trip_current(net.node(nid), s) for nid, s in devs) for eid, devs in protectors(project, catalog).items()}


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
    elif (hv := _hv_clearing(project, catalog, zone, v)) is not None and (declared is None or fed is not None):
        t, time, tnote = hv
    elif declared is not None:
        t, time, tnote = declared.clearing_s, declared.inputs()[1], declared.assumptions
    else:
        t, why = default_clearing(basis, v)
        time, tnote = Quantity(name="t", value=t, unit="s", source=why), (why,)
    return CableFault(v, ka, t, where, (*current, time), (*notes, *tnote))


HV_PROTECTIVE = frozenset({NodeKind.VCB, NodeKind.RMU, NodeKind.BREAKER, NodeKind.FUSE})
REF615 = "references/catalog-sources/raw/datasheets/protection/ABB_REF615_Product_Guide_1MRS756379_D.pdf"
RELAY_START_S = 0.023
RELAY_DELAY_S = 0.020
RELAY_INSTANTANEOUS = (
    f"ABB REF615 feeder relay, instantaneous overcurrent stage PHIPTOC: start time at most {RELAY_START_S * 1000:g} ms at 2 x the start value (Table 24) plus its "
    f"minimum operate delay setting {RELAY_DELAY_S * 1000:g} ms (Table 25), {REF615} p.23-24"
)


def _hv_clearing(project: Project, catalog: Catalog, zone, v: float) -> tuple[float, Quantity, tuple[str, ...]] | None:
    """Clearing time of an LT cable fault fed by a transformer and not guarded by an LT breaker: the breaker on that transformer's HV side clears it.
    When the HV breaker is a VCB with a catalogued total breaking time, the time is the feeder relay's instantaneous stage (RELAY_INSTANTANEOUS) plus that
    breaking time, labelled as the transformer protection set to see the fault; else the clearing time of the HV fault level. None when no such transformer
    and breaker exist."""
    net, basis = project.network, project.design_basis
    for n in net.by_kind(NodeKind.TRANSFORMER):
        if zone_at(net, n.id, "lv") != zone:
            continue
        hv_zone = zone_at(net, n.id, "hv")
        if hv_zone is None or hv_zone.voltage_v is None:
            continue
        ends = [e.dst_node if e.src_node == n.id else e.src_node for e in net.edges if e.is_power and e.service is not Service.DC and n.id in (e.src_node, e.dst_node) and e.id in hv_zone.edge_ids]
        brk = min((m for m in ends if net.node(m).kind in HV_PROTECTIVE), key=lambda m: (not exports(net, m), m), default=None)
        if brk is None:
            continue
        node = net.node(brk)
        spec = (catalog.get(node.spec) if isinstance(node.spec, str) else node.spec) if node.spec is not None else None
        brk_s = getattr(spec, "break_time_s", None) if node.kind is NodeKind.VCB else None
        head = (
            f"An LT fault on this cable is fed by {label(net, n.id)} and cleared by {label(net, brk)} on its HV side (no LT breaker guards the cable; an inverter's "
            "integrated AC breaker clears only the inverter's own contribution)."
        )
        if brk_s is not None:
            t = round(RELAY_START_S + RELAY_DELAY_S + brk_s, 3)
            note = (
                f"{head} Clearing time {t:g} s = relay {RELAY_START_S + RELAY_DELAY_S:g} s ({RELAY_INSTANTANEOUS}) + {spec.model} total breaking time {brk_s:g} s ({spec.id}). "
                f"Assumption: the {label(net, n.id)} protection (instantaneous overcurrent or differential) is set to see a fault on its LT winding cable, which needs no "
                "discrimination with an LT device because each LT winding feeds one inverter; confirm in the relay settings, or declare the LT level with its clearing_s."
            )
            src = f"assumption: IDT protection relay {RELAY_START_S + RELAY_DELAY_S:g} s (REF615) + {spec.model} {brk_s:g} s ({spec.id})"
            return t, Quantity(name="t", value=t, unit="s", source=src), (note,)
        level = fault_at(basis, hv_zone.voltage_v)
        if level is not None:
            t, basis_note = level.clearing_s, f"the clearing time of the {hv_zone.voltage_v:g} V fault level ({level.where})"
        else:
            t, basis_note = default_clearing(basis, hv_zone.voltage_v)
        note = f"{head} The breaker's relay and break times are not modelled, so {t:g} s is taken: {basis_note} (assumption)."
        return t, Quantity(name="t", value=t, unit="s", source=f"assumption: {label(net, brk)} on the {label(net, n.id)} HV side, {t:g} s"), (note,)
    return None


def _protected_cables(project: Project, catalog: Catalog, node_id: str) -> Iterator[tuple[Edge, Ampacity]]:
    for e in protected_edges(project.network, node_id):
        if (iz := edge_ampacity(project, catalog, e)) is not None:
            yield e, iz


@rule("cables.protection", "Overcurrent device rating does not exceed the cable ampacity", clause=OVERLOAD)
def protection(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Overload coordination Ib <= In <= Iz: the rated current In of a breaker or fuse (or the long-time setting Ir of an MCCB or ACB set below its frame, node field long_time_setting_a) must not exceed the derated ampacity Iz of each cable it protects, so the device trips before the cable overheats.
    A device protects the cables connected to it and those reached from it through in-line CTs, meters and isolators (which do not interrupt overload current), up to the next panel, switching device
    or equipment. Ib <= In is protection.rated_current. MV breakers are not checked: their trip settings live in relays the network does not model.

    Worked example: an ACB of In 1250 A feeding a CT, an isolator and then a cable of Iz 980 A to the LT panel is an error (1250 A > 980 A), although the cable does not touch the ACB."""
    net = project.network
    for n in net.nodes:
        spec = overload_device(catalog, n)
        if spec is None:
            continue
        rated = trip_current(n, spec)
        setting = rated != spec.rated_current
        sym = "Ir" if setting else "In"
        for e, iz in _protected_cables(project, catalog, n.id):
            z = iz.calc()
            if rated <= z.value:
                continue
            yield Finding(
                rule_id="cables.protection",
                severity=Severity.ERROR,
                subjects=(n.id, e.id),
                message=f"{label(net, n.id)} {spec.model}: {sym} {rated:g} A{f' (frame {spec.rated_current:g} A)' if setting else ''} exceeds the ampacity of {edge_label(net, e)} {iz.cable.callout()}: Iz = {iz.base:g} A x {iz.derating:g} x {iz.runs} = {z.value:.1f} A",
                measured=rated,
                limit=round(z.value, 2),
                unit="A",
                formula=f"{sym} <= Iz; {z.formula}",
                inputs=(Quantity(name=sym, value=rated, unit="A", source=f"network.{n.id}.long_time_setting_a" if setting else spec.id), *z.inputs),
                assumptions=z.assumptions,
            )


@dataclass(frozen=True)
class TerminalLimit:
    """What one device at an edge's end takes per pole, from its catalog entry."""

    id: str
    model: str
    max_mm2: float | None
    per_pole: int | None
    inverter: bool = False


@dataclass(frozen=True)
class Terminals:
    """The largest cable per pole and the most cables per pole the equipment at an edge's ends takes (the tighter device of the two ends)."""

    max_mm2: float | None
    per_pole: int | None
    devices: tuple[TerminalLimit, ...]

    def admits(self, cable: CableSpec, runs: int) -> bool:
        return (self.max_mm2 is None or cable.size_mm2 <= self.max_mm2 + 1e-9) and (self.per_pole is None or runs <= self.per_pole)

    def text(self) -> str:
        return f"{self.per_pole or 'any number of'} x {self.max_mm2:g} sq.mm per pole" if self.max_mm2 is not None else f"{self.per_pole} cable(s) per pole"


def _limit(spec, port: str) -> TerminalLimit | None:
    if isinstance(spec, ProtectionSpec) and (spec.terminal_max_mm2 is not None or spec.terminal_cables_per_pole is not None):
        return TerminalLimit(spec.id, spec.model, spec.terminal_max_mm2, spec.terminal_cables_per_pole)
    if isinstance(spec, InverterSpec) and port == "ac" and (spec.ac_terminal_max_mm2 is not None or spec.ac_terminal_cables_per_pole is not None):
        return TerminalLimit(spec.id, f"{spec.manufacturer} {spec.model} AC output", spec.ac_terminal_max_mm2, spec.ac_terminal_cables_per_pole, True)
    return None


def terminals(project: Project, catalog: Catalog, e: Edge, switchgear: bool = True, equipment: bool = True) -> Terminals | None:
    """Terminal capacity of the switchgear (catalog terminal_max_mm2 / terminal_cables_per_pole) and of the inverter AC outputs (ac_terminal_max_mm2 /
    ac_terminal_cables_per_pole) an edge lands on, or None when neither end has one."""
    net = project.network
    limits = []
    for nid, port in ((e.src_node, e.src_port), (e.dst_node, e.dst_port)):
        n = net.node(nid)
        spec = (n.spec if not isinstance(n.spec, str) else catalog.get(n.spec)) if n.spec is not None else None
        if (isinstance(spec, ProtectionSpec) and not switchgear) or (isinstance(spec, InverterSpec) and not equipment):
            continue
        if (lim := _limit(spec, port)) is not None:
            limits.append(lim)
    if not limits:
        return None
    sizes = [x.max_mm2 for x in limits if x.max_mm2 is not None]
    counts = [x.per_pole for x in limits if x.per_pole is not None]
    return Terminals(min(sizes) if sizes else None, min(counts) if counts else None, tuple(limits))


@rule("cables.terminals", "Cables fit the terminals of the switchgear and inverter outputs they land on")
def terminal_capacity(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """A cable landing on a breaker or switch must fit its terminals: conductor size per pole at most the device's largest terminal cable and parallel runs at most
    its cables per pole, as the catalog gives them from the device datasheet (or as a labelled assumption in the catalog source where the datasheet is silent).
    Every length band of a template edge is checked too. An error when a cable does not fit. The same holds for an inverter's AC output terminals where its
    datasheet gives the largest cable (and cables per pole); where it gives the size but not the number of cables per pole, more than one cable per pole is a
    warning to confirm with the manufacturer."""
    net = project.network
    for e in net.edges:
        if not e.is_power or e.cable is None:
            continue
        t = terminals(project, catalog, e)
        if t is None:
            continue
        runs = [(cable_of(catalog, e), e.runs, "")] + [(catalog.get(b.cable) if isinstance(b.cable, str) else b.cable, b.runs, f" (band up to {b.upto_m:g} m)") for b in e.bands]
        for cable, n, where in runs:
            if not isinstance(cable, CableSpec) or t.admits(cable, n):
                continue
            dev = ", ".join(dict.fromkeys(d.model for d in t.devices))
            yield Finding(
                rule_id="cables.terminals", severity=Severity.ERROR, subjects=(e.id,),
                message=f"{edge_label(net, e)}{where}: {n} x {cable.callout()} per pole does not fit the terminals of {dev} ({t.text()})",
                measured=cable.size_mm2, limit=t.max_mm2, unit="mm2", formula="size <= terminal_max_mm2; runs <= terminal_cables_per_pole",
                inputs=tuple(Quantity(name="terminal_max_mm2", value=d.max_mm2 or 0.0, unit="mm2", source=d.id) for d in t.devices),
            )  # fmt: skip
        silent = [d for d in t.devices if d.inverter and d.per_pole is None]
        many = max([e.runs, *(b.runs for b in e.bands)])
        if silent and many > 1:
            yield Finding(
                rule_id="cables.terminals", severity=Severity.WARNING, subjects=(e.id,),
                message=f"{edge_label(net, e)}: {many} cables per pole land on the {', '.join(d.model for d in silent)}, whose datasheet gives the largest cable "
                f"({silent[0].max_mm2:g} sq.mm) but not how many cables per pole the terminal takes; confirm with the manufacturer",
                measured=many, unit="runs", inputs=tuple(Quantity(name="terminal_max_mm2", value=d.max_mm2 or 0.0, unit="mm2", source=d.id) for d in silent),
            )  # fmt: skip

BATTERY_PROTECTION = (
    "No DC breaker or fuse is modelled between battery and PCS: the battery container's integrated DC switchgear is taken to protect the cable "
    "(rated at least Ib and breaking the battery short-circuit current); confirm with the battery datasheet."
)


@rule("cables.battery_dc", "Battery DC cables carry the PCS current at the battery's lowest voltage")
def battery_dc(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """A battery-to-PCS (or battery-to-DC-DC converter) cable must carry the battery's maximum continuous current when the catalog gives it, else the converter's rated
    power at the battery's minimum voltage, shared by the batteries in parallel on that converter: Ib = P / V_min / n. Iz below Ib is an error. Where no DC breaker or fuse
    is modelled on the cable, the finding states that the container's integrated DC switchgear is relied on; a modelled device must have In >= Ib (protection.rated_current)."""
    from unicad.domain.catalog import BatterySpec

    net = project.network
    for e in net.edges:
        if e.service is not Service.DC or not e.is_power or net.node(e.src_node).kind is not NodeKind.BATTERY:
            continue
        bat_node, conv = net.node(e.src_node), net.node(e.dst_node)
        bat = catalog.get(bat_node.spec) if bat_node.spec is not None else None
        if not isinstance(bat, BatterySpec):
            continue
        n = max(1, per_instance(net, bat_node.id, conv.id))
        conv_spec = catalog.get(conv.spec) if conv.spec is not None else None
        p_kw = getattr(conv_spec, "rated_kva", None) or conv.attributes.get("rated_kw")
        if bat.max_continuous_current is not None:
            ib, formula, inputs = bat.max_continuous_current, "Ib = battery max continuous current", (Quantity(name="I_bat", value=bat.max_continuous_current, unit="A", source=bat.id),)
        elif isinstance(p_kw, (int, float)) and p_kw:
            ib = p_kw * 1000 / bat.voltage_min / n
            formula = "Ib = P / V_min / n"
            inputs = (Quantity(name="P", value=p_kw, unit="kVA", source=getattr(conv_spec, "id", None) or f"network.{conv.id}.attributes"),
                      Quantity(name="V_min", value=bat.voltage_min, unit="V", source=bat.id), Quantity(name="n", value=n, source="network"))  # fmt: skip
        else:
            continue
        iz = edge_ampacity(project, catalog, e)
        if iz is None:
            continue
        z = iz.calc()
        protected = any(net.node(m).kind in (NodeKind.FUSE, NodeKind.BREAKER, NodeKind.DC_ISOLATOR) for m in (zone_at(net, e.src_node).node_ids if zone_at(net, e.src_node) else ()))
        ok = z.value >= ib - 1e-9
        yield Finding(
            rule_id="cables.battery_dc", severity=Severity.INFO if ok else Severity.ERROR, subjects=(e.id,),
            message=f"{edge_label(net, e)} {iz.cable.callout()} x {e.runs}: Iz {z.value:.0f} A {'carries' if ok else 'is below'} Ib {ib:.0f} A"
            + ("" if protected else "; DC protection: integrated in the battery container (not modelled)"),
            measured=round(z.value, 1), limit=round(ib, 1), unit="A", formula=f"Iz >= Ib; {formula}; {z.formula}", inputs=(*inputs, *z.inputs),
            assumptions=(*z.assumptions, *(() if protected else (BATTERY_PROTECTION,))),
        )  # fmt: skip


@rule("cables.short_circuit", "Cable conductor withstands the prospective fault", clause=OVERLOAD)
def short_circuit(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """The conductor of every AC power cable must carry the prospective fault current for the clearing time without overheating (adiabatic heating): A >= I sqrt(t) / k, where A is the conductor
    area (area x parallel runs), I the three-phase fault current and t its clearing time, and k the conductor and insulation constant.

    I is the level declared for the cable's system voltage (design_basis.fault_levels) or, in an LT zone (up to 1000 V) fed by a transformer, the transformer-fed current of
    protection.fault_level when that is larger. t depends on the voltage level: an LT cable protected by an MCB, MCCB or ACB (directly, or through in-line CTs, meters and
    isolators as for cables.protection) is cleared by the breaker's instantaneous trip, taken as 0.1 s (a labelled assumption: the shortest duration IEC 60364-4-43 applies the
    adiabatic formula to); an LT cable a transformer feeds with no LT breaker on it is cleared by the VCB on that transformer's HV side, in the feeder relay's instantaneous
    stage plus the VCB's catalogued total breaking time (0.023 + 0.020 + 0.060 = 0.103 s for an ABB VD4 behind an ABB REF615, labelled as an assumption on the relay settings);
    every other cable uses the clearing time of its level, else design_basis.earthing.fault_clearing_s, else 1 s, and the finding says which.
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

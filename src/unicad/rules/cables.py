"""Cable presence, current-carrying capacity and overload coordination with protective devices."""

from collections.abc import Iterator
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.catalog import CableSpec, ProtectionSpec
from unicad.domain.network import Edge, ElectricalNetwork, Laying, NodeKind, Service
from unicad.domain.project import Project
from unicad.rules.model import Calc, Finding, Quantity, Severity
from unicad.rules.registry import rule
from unicad.rules.topology import GENERATORS, edge_current, label

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
                Quantity(name="k_derating", value=self.derating, source="edge.derating" if self.derating_given else "default 1.0"),
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
    return Ampacity(cable, base, column, e.derating if given else 1.0, given, e.runs, notes if given else (*notes, NO_DERATING))


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

    The ampacity column follows the laying: air, tray, trench and overhead use the in-air rating, buried_direct the in-ground rating, buried_duct and conduit the in-duct rating. Single-core cables use the two-cable rating for DC and single-phase circuits and the three-cable rating for three-phase. The derating is the edge's `derating`; when absent 1.0 is used and an info finding says so. A cable without a rating for its laying, or an edge without a laying, is a warning."""
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
            yield Finding(rule_id="cables.ampacity", severity=Severity.INFO, message=f"{where}: derating not specified, taken as 1.0; Ib {ib.value:.1f} A within {working}", **common)


def _protected_cables(project: Project, catalog: Catalog, node_id: str) -> Iterator[tuple[Edge, Ampacity]]:
    for e in project.network.edges_at(node_id):
        if e.is_power and (iz := edge_ampacity(project, catalog, e)) is not None:
            yield e, iz


@rule("cables.protection", "Overcurrent device rating does not exceed the cable ampacity", clause=OVERLOAD)
def protection(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Overload coordination Ib <= In <= Iz: the rated current In of a breaker or fuse must not exceed the derated ampacity Iz of each cable connected directly to it, so the device trips before the cable overheats. Ib <= In is protection.rated_current. MV breakers are not checked: their trip settings live in relays the network does not model."""
    net = project.network
    for n in net.nodes:
        if n.spec is None:
            continue
        spec = catalog.get(n.spec)
        if not isinstance(spec, ProtectionSpec) or spec.device not in OVERLOAD_DEVICES or spec.rated_current is None:
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


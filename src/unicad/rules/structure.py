"""Network structure: connectivity, voltage consistency across converters, and missing equipment data."""

from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.catalog import InverterSpec, PCSSpec, TransformerSpec
from unicad.domain.network import SOURCE_KINDS, NodeKind
from unicad.domain.project import Project
from unicad.rules.model import Finding, Quantity, Severity
from unicad.rules.registry import rule
from unicad.rules.thresholds import VOLTAGE_TOLERANCE
from unicad.rules.topology import label, zone_at

SPEC_REQUIRED = frozenset(
    {NodeKind.PV_STRING, NodeKind.PV_ARRAY_BLOCK, NodeKind.INVERTER, NodeKind.PCS, NodeKind.BATTERY, NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER}
)
SPEC_EXPECTED = frozenset(
    {NodeKind.BREAKER, NodeKind.VCB, NodeKind.FUSE, NodeKind.ISOLATOR, NodeKind.DC_ISOLATOR, NodeKind.DC_SPD, NodeKind.AC_SPD, NodeKind.METER}
)


@rule("structure.unconnected_port", "Every power port is connected")
def unconnected_port(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """A power port with no cable is either missing a connection or belongs to equipment that should not be there. Allowed while drafting; blocks issue for approval."""
    net = project.network
    for terminal in net.unconnected_ports():
        node_id, port = terminal.rsplit(":", 1)
        yield Finding(
            rule_id="structure.unconnected_port",
            severity=Severity.ERROR,
            subjects=(node_id,),
            message=f"{label(net, node_id)} ({net.node(node_id).kind}) port '{port}' is not connected",
        )


@rule("structure.path_to_poi", "Every source reaches the point of interconnection")
def path_to_poi(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Each generating source (PV string, array block, battery, DG) must have a downstream power path to a poi_grid node."""
    net = project.network
    pois = net.by_kind(NodeKind.POI_GRID)
    if not pois and net.nodes:
        yield Finding(rule_id="structure.path_to_poi", severity=Severity.ERROR, subjects=(), message="network has no poi_grid node")
        return
    poi_ids = {p.id for p in pois}
    for n in net.nodes:
        if n.kind in SOURCE_KINDS and n.kind is not NodeKind.POI_GRID and not poi_ids & set(net.downstream(n.id)):
            yield Finding(
                rule_id="structure.path_to_poi",
                severity=Severity.ERROR,
                subjects=(n.id,),
                message=f"{label(net, n.id)} ({n.kind}) has no power path to a point of interconnection",
            )


def _voltage_check(rule_id: str, subjects: tuple[str, ...], what: str, nominal: float, nominal_src: str, zone_v: float | None) -> Finding:
    tol = VOLTAGE_TOLERANCE.value
    inputs = (
        Quantity(name="V_equipment", value=nominal, unit="V", source=nominal_src),
        Quantity(name="V_zone", value=zone_v if zone_v is not None else "undeclared", unit="V", source="network.voltage_zone"),
        Quantity(name="tolerance", value=tol, source=f"threshold.{VOLTAGE_TOLERANCE.name}"),
    )
    if zone_v is None:
        return Finding(rule_id=rule_id, severity=Severity.INFO, subjects=subjects, message=f"{what}: not checked, no voltage is declared in the connected zone", inputs=inputs)
    dev = abs(zone_v - nominal) / nominal
    sev = Severity.ERROR if dev > tol else Severity.INFO
    verdict = "outside" if dev > tol else "within"
    return Finding(
        rule_id=rule_id,
        severity=sev,
        subjects=subjects,
        message=f"{what}: rated {nominal:g} V, zone {zone_v:g} V ({dev:.1%} difference, {verdict} {tol:.0%})",
        measured=round(dev, 4),
        limit=tol,
        unit="p.u.",
        formula="|V_zone - V_equipment| / V_equipment <= tolerance",
        inputs=inputs,
        assumptions=(VOLTAGE_TOLERANCE.assumption,),
    )


@rule("structure.inverter_ac_voltage", "Inverter AC voltage matches its AC zone")
def inverter_ac_voltage(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """The rated AC voltage of each inverter or PCS is compared with the declared voltage of the zone its AC port connects to. A difference above the tolerance is an error; any smaller non-zero difference is reported for confirmation."""
    net = project.network
    for n in net.by_kind(NodeKind.INVERTER, NodeKind.PCS):
        if n.spec is None:
            continue
        spec = catalog.get(n.spec)
        if not isinstance(spec, (InverterSpec, PCSSpec)):
            continue
        zone = zone_at(net, n.id, "ac")
        if zone is None:
            continue
        if zone.voltage_v == spec.ac_voltage:
            continue
        yield _voltage_check("structure.inverter_ac_voltage", (n.id,), f"{label(net, n.id)} {spec.model} AC output", spec.ac_voltage, spec.id, zone.voltage_v)


@rule("structure.transformer_voltage", "Transformer windings match their zones")
def transformer_voltage(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Each transformer's HV winding is compared with the zone on its 'hv' port and its LV winding (the nearest, for multi-winding units) with the zone on its 'lv' port."""
    net = project.network
    for n in net.by_kind(NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER):
        if n.spec is None:
            continue
        spec = catalog.get(n.spec)
        if not isinstance(spec, TransformerSpec):
            continue
        for port, windings in (("hv", (spec.hv_kv,)), ("lv", spec.lv_kv)):
            zone = zone_at(net, n.id, port)
            if zone is None:
                continue
            zv = zone.voltage_v
            nominal = min((kv * 1000.0 for kv in windings), key=lambda v: abs(v - zv) if zv is not None else 0)
            if zv == nominal:
                continue
            yield _voltage_check("structure.transformer_voltage", (n.id,), f"{label(net, n.id)} {port.upper()} winding", nominal, spec.id, zv)


@rule("structure.missing_spec", "Rated equipment names a catalog spec")
def missing_spec(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Strings, inverters, PCS, batteries and transformers need a catalog spec (and strings a module count) for the engineering rules to run: missing ones are errors. Protection devices and meters without a spec cannot be rating-checked: warnings."""
    net = project.network
    for n in net.nodes:
        if n.spec is None and n.kind in SPEC_REQUIRED:
            yield Finding(rule_id="structure.missing_spec", severity=Severity.ERROR, subjects=(n.id,), message=f"{label(net, n.id)} ({n.kind}) has no catalog spec; its ratings cannot be checked")
        elif n.spec is None and n.kind in SPEC_EXPECTED:
            yield Finding(rule_id="structure.missing_spec", severity=Severity.WARNING, subjects=(n.id,), message=f"{label(net, n.id)} ({n.kind}) has no catalog spec; its ratings cannot be checked")
        if n.kind is NodeKind.PV_STRING and n.modules_in_series is None:
            yield Finding(rule_id="structure.missing_spec", severity=Severity.ERROR, subjects=(n.id,), message=f"{label(net, n.id)} has no modules_in_series; string voltages cannot be checked")

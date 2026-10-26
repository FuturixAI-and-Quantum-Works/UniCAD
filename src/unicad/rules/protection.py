"""Protection device ratings, surge protection and earthing presence."""

import math
from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.catalog import ProtectionSpec
from unicad.domain.network import Node, NodeKind, Service
from unicad.domain.project import Project
from unicad.rules.faults import prospective_fault
from unicad.rules.model import Finding, Quantity, Severity
from unicad.rules.pv import cell_temp_min, string_voltage
from unicad.rules.registry import rule
from unicad.rules.thresholds import CONTINUOUS_FACTOR, ISC_FACTOR
from unicad.rules.topology import GENERATORS, design_current, is_dc, label, per_instance, string_feeds, zone_at

CURRENT_RATED = frozenset({NodeKind.BREAKER, NodeKind.VCB, NodeKind.FUSE, NodeKind.ISOLATOR, NodeKind.DC_ISOLATOR})
INTERRUPTING = frozenset({"mcb", "mccb", "acb", "vcb", "rccb", "fuse_gpv", "fuse_hrc"})
EARTH_REQUIRED = frozenset({NodeKind.INVERTER, NodeKind.PCS})
EARTH_EXPECTED = frozenset({NodeKind.ACDB, NodeKind.LT_PANEL, NodeKind.HT_PANEL, NodeKind.SCB})
SINGLE_PHASE_POLES = "Devices with one or two poles are taken as phase-to-neutral connected and need only V_zone / sqrt(3)."


def _protection(catalog: Catalog, node: Node) -> ProtectionSpec | None:
    if node.spec is None:
        return None
    spec = catalog.get(node.spec)
    return spec if isinstance(spec, ProtectionSpec) else None


@rule("protection.rated_current", "Device rated current covers the design current", clause="IS 732")
def rated_current(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """A breaker, fuse or switch on an export path must be rated for the current it carries: on the AC side In >= 1.25 x the maximum current of the inverters behind it, on the DC side In >= 1.25 x Isc of the strings behind it. String fuses (gPV) are checked by protection.string_fuse_rating. The upper bound In <= cable ampacity belongs to cable sizing, which reuses the same design current."""
    net = project.network
    for n in net.nodes:
        spec = _protection(catalog, n)
        if n.kind not in CURRENT_RATED or spec is None or spec.rated_current is None or spec.device == "fuse_gpv":
            continue
        ib = design_current(project, catalog, n.id)
        if ib is None:
            continue
        dc = is_dc(net, n.id)
        factor = 1.0 if dc else CONTINUOUS_FACTOR.value
        need = factor * ib.value
        if spec.rated_current >= need:
            continue
        side = "DC" if dc else "AC"
        yield Finding(
            rule_id="protection.rated_current",
            severity=Severity.ERROR,
            subjects=(n.id,),
            message=f"{label(net, n.id)} {spec.model}: rated {spec.rated_current:g} A is below the {side} design requirement {need:.1f} A ({factor:g} x Ib {ib.value:.1f} A)",
            measured=spec.rated_current,
            limit=round(need, 2),
            unit="A",
            formula=f"In >= k x Ib; {ib.formula}" + ("" if dc else "; k = continuous_factor"),
            inputs=(*ib.inputs, Quantity(name="k", value=factor, source=None if dc else f"threshold.{CONTINUOUS_FACTOR.name}"), Quantity(name="In", value=spec.rated_current, unit="A", source=spec.id)),
            assumptions=(*ib.assumptions, *(() if dc else (CONTINUOUS_FACTOR.assumption,))),
        )


@rule("protection.breaking_capacity", "Interrupting devices state a breaking capacity")
def breaking_capacity(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Breakers and fuses must carry a breaking capacity (kA) so it can be shown on the SLD and compared with the fault level. A spec without one is an error."""
    net = project.network
    for n in net.nodes:
        spec = _protection(catalog, n)
        if spec is None or spec.device not in INTERRUPTING or spec.breaking_capacity_ka is not None:
            continue
        yield Finding(rule_id="protection.breaking_capacity", severity=Severity.ERROR, subjects=(n.id,), message=f"{label(net, n.id)} {spec.model} ({spec.device}) has no breaking capacity in its catalog spec")


def covers_fault(spec: ProtectionSpec, fault_ka: float) -> bool:
    """Icu at or above the fault level; for vacuum breakers also the rated short-time withstand current, which is their thermal duty."""
    if spec.breaking_capacity_ka is None or spec.breaking_capacity_ka < fault_ka - 1e-9:
        return False
    return not (spec.device == "vcb" and spec.short_time_withstand_ka is not None and spec.short_time_withstand_ka < fault_ka - 1e-9)


@rule("protection.fault_level", "Interrupting devices break the prospective fault current")
def fault_level(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """A breaker or fuse must have a breaking capacity (Icu; for vacuum breakers also the short-time withstand current Icw) at or above the prospective three-phase fault current at
    its zone. That is the larger of the level declared in design_basis.fault_levels for the zone's system voltage and, where a transformer LV winding feeds the zone, the current the
    transformer passes: I = S / (sqrt(3) V (Z% / 100 + S / S_sc)), with S the kVA base of the winding's impedance, Z% its catalog (or inline) impedance and S_sc = sqrt(3) V_hv I_hv
    the declared fault level on the HV side (an infinite bus when none is declared, which is conservative). Inverter and PCS contributions are not added. An error when the breaking
    capacity is below. Where neither applies, a single info finding lists the voltages and the devices are not checked; autobuild then picks the lowest breaking capacity at or above a
    placeholder minimum and reports that as an assumption. A breaker without a catalog spec is an error of structure.missing_spec.

    Worked examples: a 20 kA VD4 vacuum breaker on a 33 kV bus with a 25 kA fault level is an error (20 kA < 25 kA); the 25 kA VD4 passes. A 7040 kVA, 7 % IDT winding at 800 V
    on an infinite bus passes 7040 / (sqrt(3) x 0.8 x 0.07) = 72.6 kA to its LV bus, so a 12 kA MCCB there is an error."""
    net = project.network
    unchecked: dict[str, int] = {}
    for n in net.nodes:
        spec = _protection(catalog, n)
        if spec is None or spec.device not in INTERRUPTING or spec.breaking_capacity_ka is None:
            continue
        zone = zone_at(net, n.id)
        fault = prospective_fault(project, catalog, n.id)
        if fault is None:
            key = f"{zone.voltage_v:g} V" if zone and zone.voltage_v else "undeclared voltage"
            unchecked[key] = unchecked.get(key, 0) + net.instance_count(n.id)
            continue
        ok = covers_fault(spec, fault.ka)
        icw = f", Icw {spec.short_time_withstand_ka:g} kA" if spec.device == "vcb" and spec.short_time_withstand_ka is not None else ""
        yield Finding(
            rule_id="protection.fault_level", severity=Severity.INFO if ok else Severity.ERROR, subjects=(n.id,),
            message=f"{label(net, n.id)} {spec.model}: Icu {spec.breaking_capacity_ka:g} kA{icw} {'covers' if ok else 'is below'} the {fault.ka:g} kA {'transformer-fed ' if fault.where == 'transformer-fed' else ''}fault level at {fault.voltage_v:g} V",
            measured=spec.breaking_capacity_ka, limit=fault.ka, unit="kA", formula="Icu >= I_fault" + ("; Icw >= I_fault" if spec.device == "vcb" else "") + (f"; {fault.formula}" if fault.formula else ""),
            inputs=(*fault.inputs, Quantity(name="Icu", value=spec.breaking_capacity_ka, unit="kA", source=spec.id)), assumptions=fault.assumptions,
        )  # fmt: skip
    if unchecked:
        listed = ", ".join(f"{v} ({n} devices)" for v, n in sorted(unchecked.items()))
        yield Finding(
            rule_id="protection.fault_level", severity=Severity.INFO, subjects=(),
            message=f"breaking capacities not checked against a fault level: none in design_basis.fault_levels for {listed}",
        )  # fmt: skip


def _max_dc_voc(project: Project, catalog: Catalog, node_id: str) -> tuple[float, tuple[Quantity, ...], tuple[str, ...]] | None:
    zone = zone_at(project.network, node_id)
    if zone is None:
        return None
    best = None
    for f in string_feeds(project, catalog):
        if f.string.id in zone.node_ids:
            v = string_voltage(f.module, f.n_series, "voc", cell_temp_min(project.design_basis))
            if best is None or v.value > best.value:
                best = v
    return (best.value, best.inputs, best.assumptions) if best else None


@rule("protection.rated_voltage", "Device rated voltage covers the system voltage")
def rated_voltage(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """DC devices must be rated for the highest string Voc at the coldest cell temperature in their zone; AC devices for the declared zone voltage (line-to-line for 3- and 4-pole devices). AC surge protective devices are not checked: their rated voltage is a continuous operating voltage with a different basis."""
    net = project.network
    for n in net.nodes:
        spec = _protection(catalog, n)
        if spec is None or spec.device == "spd_ac":
            continue
        if is_dc(net, n.id):
            got = _max_dc_voc(project, catalog, n.id)
            if got is None:
                continue
            need, inputs, assumptions = got
            formula = "rated_voltage >= max(N x Voc x (1 + beta_Voc / 100 x (Tcell_min - 25)))"
        else:
            zone = zone_at(net, n.id)
            if zone is None or zone.voltage_v is None:
                continue
            single = spec.poles <= 2
            need = zone.voltage_v / math.sqrt(3) if single else zone.voltage_v
            inputs = (Quantity(name="V_zone", value=zone.voltage_v, unit="V", source="network.voltage_zone"), Quantity(name="poles", value=spec.poles, source=spec.id))
            assumptions = (SINGLE_PHASE_POLES,) if single else ()
            formula = "rated_voltage >= V_zone" + (" / sqrt(3)" if single else "")
        if spec.rated_voltage >= need:
            continue
        yield Finding(
            rule_id="protection.rated_voltage",
            severity=Severity.ERROR,
            subjects=(n.id,),
            message=f"{label(net, n.id)} {spec.model}: rated {spec.rated_voltage:g} V is below the system voltage {need:.1f} V",
            measured=spec.rated_voltage,
            limit=round(need, 2),
            unit="V",
            formula=formula,
            inputs=(*inputs, Quantity(name="rated_voltage", value=spec.rated_voltage, unit="V", source=spec.id)),
            assumptions=assumptions,
        )


def _spd_in_zone(project: Project, node_id: str, port: str, kinds: frozenset[NodeKind]) -> bool | None:
    net = project.network
    zone = zone_at(net, node_id, port)
    if zone is None:
        return None
    return any(net.node(m).kind in kinds for m in zone.node_ids)


@rule("protection.spd_dc", "Surge protection on the inverter DC side", clause="IEC 62548")
def spd_dc(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Each inverter or PCS DC zone should contain a DC surge protective device. Warning when none is modelled: an SPD built into the inverter or SCB should still be modelled so the SLD shows it."""
    net = project.network
    for n in net.by_kind(*GENERATORS):
        if _spd_in_zone(project, n.id, "dc", frozenset({NodeKind.DC_SPD})) is False:
            yield Finding(rule_id="protection.spd_dc", severity=Severity.WARNING, subjects=(n.id,), message=f"{label(net, n.id)}: no DC SPD in the DC zone of the inverter")


@rule("protection.spd_ac", "Surge protection on the inverter AC side")
def spd_ac(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Each inverter or PCS AC zone should contain an AC SPD or lightning arrester. Warning when none is modelled."""
    net = project.network
    for n in net.by_kind(*GENERATORS):
        if _spd_in_zone(project, n.id, "ac", frozenset({NodeKind.AC_SPD, NodeKind.LIGHTNING_ARRESTER})) is False:
            yield Finding(rule_id="protection.spd_ac", severity=Severity.WARNING, subjects=(n.id,), message=f"{label(net, n.id)}: no AC SPD or lightning arrester in the AC zone of the inverter")


@rule("protection.earthing", "Inverters and panels are earthed", clause="IS 3043")
def earthing(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Every inverter and PCS needs an earth connection (an edge on its 'pe' port): error when missing. Distribution panels and SCBs without one are warnings, since their earthing may be detailed on the earthing drawing instead of the SLD."""
    net = project.network
    for n in net.nodes:
        if n.kind not in EARTH_REQUIRED | EARTH_EXPECTED:
            continue
        if any(e.service is Service.EARTH for e in net.edges_at(n.id)):
            continue
        sev = Severity.ERROR if n.kind in EARTH_REQUIRED else Severity.WARNING
        yield Finding(rule_id="protection.earthing", severity=sev, subjects=(n.id,), message=f"{label(net, n.id)} ({n.kind}) has no earth connection")


@rule("protection.string_fuse_rating", "String fuse rating between string current and module limit", clause="IEC 62548")
def string_fuse_rating(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """A gPV fuse carrying n strings must be rated at least n x 1.25 x Isc, and a fuse protecting a single string must not exceed the module's maximum series fuse rating."""
    net = project.network
    for n in net.by_kind(NodeKind.FUSE):
        spec = _protection(catalog, n)
        if spec is None or spec.device != "fuse_gpv" or spec.rated_current is None:
            continue
        for f in string_feeds(project, catalog):
            if n.id not in f.path:
                continue
            k = per_instance(net, f.string.id, n.id)
            need = k * ISC_FACTOR.value * f.module.isc
            base = (
                Quantity(name="n_strings", value=k, source="network"),
                Quantity(name="k_Isc", value=ISC_FACTOR.value, source=f"threshold.{ISC_FACTOR.name}"),
                Quantity(name="Isc", value=f.module.isc, unit="A", source=f.module.id),
                Quantity(name="In", value=spec.rated_current, unit="A", source=spec.id),
            )
            if spec.rated_current < need:
                yield Finding(
                    rule_id="protection.string_fuse_rating",
                    severity=Severity.ERROR,
                    subjects=(n.id, f.string.id),
                    message=f"{label(net, n.id)} {spec.model}: rated {spec.rated_current:g} A is below {k} x {ISC_FACTOR.value:g} x {f.module.isc:g} A = {need:.1f} A",
                    measured=spec.rated_current,
                    limit=round(need, 2),
                    unit="A",
                    formula="In >= n_strings x k_Isc x Isc",
                    inputs=base,
                    assumptions=(ISC_FACTOR.assumption,),
                )
            if k == 1 and f.module.max_series_fuse is not None and spec.rated_current > f.module.max_series_fuse:
                yield Finding(
                    rule_id="protection.string_fuse_rating",
                    severity=Severity.ERROR,
                    subjects=(n.id, f.string.id),
                    message=f"{label(net, n.id)} {spec.model}: rated {spec.rated_current:g} A exceeds module max series fuse {f.module.max_series_fuse:g} A",
                    measured=spec.rated_current,
                    limit=f.module.max_series_fuse,
                    unit="A",
                    formula="In <= max_series_fuse",
                    inputs=(*base, Quantity(name="max_series_fuse", value=f.module.max_series_fuse, unit="A", source=f.module.id)),
                )

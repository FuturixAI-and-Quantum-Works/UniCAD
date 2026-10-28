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
from unicad.rules.thresholds import CONTINUOUS_FACTOR, STRING_FUSE_MAX, STRING_FUSE_MIN
from unicad.rules.topology import GENERATORS, design_current, exports, is_dc, label, per_instance, string_feeds, zone_at

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


LV_BREAKER_DEVICES = ("mcb", "mccb", "acb")


@rule("protection.oversize", "LT breakers are not rated above what the design current needs without a stated reason")
def oversize(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """An LT breaker (MCB, MCCB, ACB) on an export path rated above the smallest catalog breaker that covers k x Ib at its voltage and poles is a warning, because the
    cable it protects must then carry In, not Ib (cables.protection). The finding says why the larger device is there: no smaller catalog breaker breaks the prospective
    fault at its bus (protection.fault_level), or the smaller frames' terminals do not take the cable it lands (cables.terminals), or neither (a smaller one fits)."""
    from unicad.rules.cables import cable_of

    net = project.network
    pool = [s for s in catalog.filter("protection") if isinstance(s, ProtectionSpec) and s.device in LV_BREAKER_DEVICES and s.rated_current is not None]
    for n in net.by_kind(NodeKind.BREAKER):
        spec = _protection(catalog, n)
        if spec is None or spec.device not in LV_BREAKER_DEVICES or spec.rated_current is None or is_dc(net, n.id):
            continue
        ib = design_current(project, catalog, n.id)
        zone = zone_at(net, n.id)
        if ib is None or zone is None or zone.voltage_v is None:
            continue
        need = CONTINUOUS_FACTOR.value * ib.value
        fits = [s for s in pool if s.rated_current >= need - 1e-9 and s.poles == spec.poles and s.rated_voltage >= zone.voltage_v]
        smallest = min((s.rated_current for s in fits), default=None)
        if smallest is None or spec.rated_current <= smallest + 1e-9:
            continue
        fault = prospective_fault(project, catalog, n.id)
        breaking = [s for s in fits if fault is None or covers_fault(s, fault.ka)]
        smallest_breaking = min(breaking, key=lambda s: (s.rated_current, s.id), default=None)
        if fault is not None and smallest_breaking is not None and smallest_breaking.rated_current >= spec.rated_current - 1e-9:
            why = (f"the catalog forces it: no catalog breaker rated below {spec.rated_current:g} A at {zone.voltage_v:g} V with {spec.poles} poles breaks the "
                   f"{fault.ka:g} kA fault at its bus (the smallest that covers {need:.0f} A, {smallest:g} A, breaks less)")
        else:
            landed = [e for e in net.edges_at(n.id) if e.is_power and e.cable is not None]
            small = smallest_breaking
            blocked = [
                e for e in landed
                if small is not None and (c := cable_of(catalog, e)) is not None
                and ((small.terminal_max_mm2 is not None and c.size_mm2 > small.terminal_max_mm2 + 1e-9) or (small.terminal_cables_per_pole is not None and e.runs > small.terminal_cables_per_pole))
            ]  # fmt: skip
            if blocked and small is not None:
                c = cable_of(catalog, blocked[0])
                cap = f"{small.terminal_cables_per_pole or 1} x {small.terminal_max_mm2:g} sq.mm" if small.terminal_max_mm2 is not None else f"{small.terminal_cables_per_pole} cable(s)"
                why = f"the terminals force it: {small.model} would break the fault but takes at most {cap} per pole, not the {blocked[0].runs} x {c.callout()} voltage drop needs"
            else:
                why = f"a smaller catalog breaker ({small.model if small else f'{smallest:g} A'}) would do"
        yield Finding(
            rule_id="protection.oversize", severity=Severity.WARNING, subjects=(n.id,),
            message=f"{label(net, n.id)} {spec.model}: rated {spec.rated_current:g} A for a design requirement of {need:.1f} A ({CONTINUOUS_FACTOR.value:g} x Ib {ib.value:.1f} A); {why}; its cables are sized for In",
            measured=spec.rated_current, limit=smallest, unit="A", formula=f"In <= smallest catalog In >= k x Ib; {ib.formula}",
            inputs=(*ib.inputs, Quantity(name="In", value=spec.rated_current, unit="A", source=spec.id)), assumptions=(*ib.assumptions, CONTINUOUS_FACTOR.assumption),
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


@rule("protection.spd_ac_poles", "AC SPD has a pole per phase conductor")
def spd_ac_poles(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """An AC surge protective device on a three-phase circuit must protect every phase: a three- or four-pole SPD (3+0, 3+1, 4+0). A single- or two-pole
    catalog SPD there protects one phase only: error. Circuits fed only by single-phase inverters count as single-phase."""
    from unicad.rules.cables import phases

    net = project.network
    for n in net.by_kind(NodeKind.AC_SPD):
        spec = _protection(catalog, n)
        if spec is None or spec.poles >= 3:
            continue
        edge = next((e for e in net.edges_at(n.id) if e.is_power), None)
        if edge is None or phases(project, catalog, edge) != 3:
            continue
        yield Finding(
            rule_id="protection.spd_ac_poles",
            severity=Severity.ERROR,
            subjects=(n.id,),
            message=f"{label(net, n.id)} {spec.model}: a {spec.poles}-pole SPD on a three-phase circuit protects only {spec.poles} conductor(s); use a three- or four-pole SPD",
            measured=spec.poles,
            limit=3,
            unit="poles",
            inputs=(Quantity(name="poles", value=spec.poles, source=spec.id),),
        )


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
    """A gPV fuse protecting one string must be rated 1.5 x Isc < In < 2.4 x Isc and In <= the module's maximum series fuse rating (IMOD_MAX_OCPR); a fuse protecting a
    group of N strings 1.5 x N x Isc < In < IMOD_MAX_OCPR - (N - 1) x Isc. Isc is the module short-circuit current at STC. The factors are the string_fuse_min and
    string_fuse_max thresholds, whose text names the sources the clause was read from."""
    net = project.network
    for n in net.by_kind(NodeKind.FUSE):
        spec = _protection(catalog, n)
        if spec is None or spec.device != "fuse_gpv" or spec.rated_current is None:
            continue
        seen: set[tuple[int, str]] = set()
        for f in string_feeds(project, catalog):
            if n.id not in f.path:
                continue
            k = per_instance(net, f.string.id, n.id)
            if (k, f.module.id) in seen:
                continue
            seen.add((k, f.module.id))
            isc, lo, hi = f.module.isc, STRING_FUSE_MIN.value, STRING_FUSE_MAX.value
            base = (
                Quantity(name="n_strings", value=k, source="network"),
                Quantity(name="Isc", value=isc, unit="A", source=f.module.id),
                Quantity(name="In", value=spec.rated_current, unit="A", source=spec.id),
                Quantity(name="k_min", value=lo, source=f"threshold.{STRING_FUSE_MIN.name}"),
            )
            ocpr = f.module.max_series_fuse
            checks = [(spec.rated_current > k * lo * isc + 1e-9, f"is not above {k} x {lo:g} x {isc:g} A = {k * lo * isc:.1f} A", k * lo * isc, "In > n_strings x k_min x Isc", ())]
            if k == 1:
                checks.append((spec.rated_current < hi * isc - 1e-9, f"is not below {hi:g} x {isc:g} A = {hi * isc:.1f} A", hi * isc, "In < k_max x Isc", (Quantity(name="k_max", value=hi, source=f"threshold.{STRING_FUSE_MAX.name}"),)))
                if ocpr is not None:
                    checks.append((spec.rated_current <= ocpr + 1e-9, f"exceeds module max series fuse {ocpr:g} A", ocpr, "In <= max_series_fuse", (Quantity(name="max_series_fuse", value=ocpr, unit="A", source=f.module.id),)))
            elif ocpr is not None:
                top = ocpr - (k - 1) * isc
                checks.append((spec.rated_current < top - 1e-9, f"is not below max series fuse {ocpr:g} A - ({k} - 1) x {isc:g} A = {top:.1f} A", top, "In < max_series_fuse - (n_strings - 1) x Isc", (Quantity(name="max_series_fuse", value=ocpr, unit="A", source=f.module.id),)))
            for ok, what, limit, formula, extra in checks:
                if ok:
                    continue
                yield Finding(
                    rule_id="protection.string_fuse_rating", severity=Severity.ERROR, subjects=(n.id, f.string.id),
                    message=f"{label(net, n.id)} {spec.model}: rated {spec.rated_current:g} A {what}",
                    measured=spec.rated_current, limit=round(limit, 2), unit="A", formula=formula, inputs=(*base, *extra),
                    assumptions=(STRING_FUSE_MIN.assumption,),
                )  # fmt: skip


MV_PROTECTIVE = frozenset({NodeKind.VCB, NodeKind.RMU, NodeKind.BREAKER, NodeKind.FUSE})
PANEL_THROUGH = frozenset({NodeKind.CT, NodeKind.ISOLATOR})
TRANSFORMERS = frozenset({NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER})
SOURCE_END = (
    "On an export path (generator to grid) the grid end of a cable is its downstream end; on an auxiliary or load feeder it is the upstream end. "
    "Current transformers and isolators in the same panel are passed through; inverter fault contribution is not counted as a source."
)


def _protective(catalog: Catalog, node: Node) -> bool:
    return node.kind in MV_PROTECTIVE or (node.kind is NodeKind.ISOLATOR and (s := _protection(catalog, node)) is not None and s.device == "sfu")


def _protected_end(project: Project, catalog: Catalog, node_id: str, downstream: bool) -> str | None:
    """The protective device at the grid end of a cable that ends at `node_id`: the node itself, or one reached through CTs and isolators
    wired to it inside the same panel (no cable between)."""
    net = project.network
    seen, todo = set(), [node_id]
    while todo:
        n = todo.pop(0)
        if n in seen:
            continue
        seen.add(n)
        node = net.node(n)
        if _protective(catalog, node):
            return n
        if node.kind not in PANEL_THROUGH:
            continue
        for e in net.edges:
            if e.is_power and e.cable is None and e.laying is None and (e.src_node if downstream else e.dst_node) == n:
                todo.append(e.dst_node if downstream else e.src_node)
    return None


def _terminal_guarded(project: Project, catalog: Catalog, node_id: str, edge_id: str) -> bool:
    """A cable tapped off a transformer's HV terminals is supplied through the breaker that feeds those terminals (the block VCB of the transformer)."""
    net = project.network
    if net.node(node_id).kind not in TRANSFORMERS:
        return False
    return any(
        _protective(catalog, net.node(e.dst_node if e.src_node == node_id else e.src_node))
        for e in net.edges
        if e.id != edge_id and e.service is Service.MV_AC and node_id in (e.src_node, e.dst_node)
    )


@rule("protection.mv_cable", "Every MV cable is protected at the end the fault current comes from")
def mv_cable(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """An MV cable must have a circuit breaker, RMU breaker or fuse (switch-fuse) at its grid end, so a cable fault is cleared from the side that feeds it:
    the bus end of a block feeder, the transformer-tap end of an auxiliary transformer feeder. A breaker at the far end only (the block VCB of a feeder that
    tees straight onto the main bus) leaves the cable unprotected: error."""
    net = project.network
    for e in net.edges:
        if e.service is not Service.MV_AC or e.cable is None:
            continue
        export = exports(net, e.dst_node)
        end = e.dst_node if export else e.src_node
        if _protected_end(project, catalog, end, export) is not None or (not export and _terminal_guarded(project, catalog, end, e.id)):
            continue
        yield Finding(
            rule_id="protection.mv_cable",
            severity=Severity.ERROR,
            subjects=(e.id, end),
            message=f"MV cable {e.id} ({label(net, e.src_node)} -> {label(net, e.dst_node)}) has no breaker or fuse at its {'grid (bus)' if export else 'supply'} end {label(net, end)}; "
            "add a feeder panel breaker there",
            assumptions=(SOURCE_END,),
        )


@rule("protection.transformer_feed", "Every transformer tap is switched and protected")
def transformer_feed(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """A transformer fed from another transformer's terminals or from a bus (an auxiliary transformer tapped off a block transformer's HV side, a plant
    auxiliary transformer on the main bus) must be fed through a breaker, RMU breaker or switch-fuse on its supply side: error when the supply reaches it
    through cables and panel wiring alone. Block transformers on an export path are protected by their block VCB or RMU (protection.mv_cable)."""
    net = project.network
    for n in net.by_kind(*TRANSFORMERS):
        if exports(net, n.id):
            continue
        feeders = [e for e in net.edges if e.is_power and e.dst_node == n.id]
        for e in feeders:
            seen, todo, found = set(), [e.src_node], False
            while todo:
                m = todo.pop(0)
                if m in seen:
                    continue
                seen.add(m)
                node = net.node(m)
                if _protective(catalog, node):
                    found = True
                    break
                if node.kind in TRANSFORMERS | {NodeKind.BUSBAR, NodeKind.HT_PANEL, NodeKind.LT_PANEL, NodeKind.ACDB} | GENERATORS:
                    break
                todo += [x.src_node for x in net.edges if x.is_power and x.dst_node == m]
            if found:
                continue
            yield Finding(
                rule_id="protection.transformer_feed",
                severity=Severity.ERROR,
                subjects=(n.id, e.id),
                message=f"{label(net, n.id)} is tapped from {label(net, e.src_node)} with no breaker or switch-fuse on its supply side; add a T-off VCB or fuse-switch",
            )

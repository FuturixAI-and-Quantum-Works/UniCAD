"""Voltage drop per cable and along the path from each source to the point of interconnection."""

import math
from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.network import Edge, ElectricalNetwork, NetworkError, NodeKind, Service
from unicad.domain.project import Project
from unicad.rules.cables import cable_of, edge_label, phases
from unicad.rules.model import Calc, Finding, Quantity, Severity
from unicad.rules.registry import rule
from unicad.rules.topology import GENERATORS, module_of, edge_current, label, per_instance

R_AT = {
    90: "Conductor resistance taken at 90 degC (catalog r_ohm_per_km_90c), the maximum operating temperature of XLPE insulation; conservative at lower loading.",
    70: "Conductor resistance taken at 70 degC (catalog r_ohm_per_km_70c), the maximum operating temperature of PVC insulation; conservative at lower loading.",
}
DC_OPERATING = "DC drop evaluated at STC operating point: current n x Imp and voltage N x Vmp of the shortest string carried."
AC_LOADING = "AC drop evaluated at the maximum inverter current (design current Ib) and the design-basis power factor."
NO_X = "Catalog has no reactance for this cable; X taken as 0."


def _dc_drop(project: Project, catalog: Catalog, e: Edge, length: float, r: float, r_note: str) -> Calc | None:
    net = project.network
    ids = [e.src_node, *net.upstream(e.src_node)]
    mods = [(s, 1 if s == e.src_node else per_instance(net, s, e.src_node), m, net.node(s).modules_in_series) for s in ids if net.node(s).kind is NodeKind.PV_STRING and (m := module_of(net, catalog, net.node(s)))]
    if not mods:
        return None
    current = sum(n * m.imp for _, n, m, _ in mods)
    s_min, _, m_min, n_min = min(mods, key=lambda x: x[3] * x[2].vmp)
    v = n_min * m_min.vmp
    dv = 2 * length * (current / e.runs) * r / 1000.0
    inputs = [Quantity(name="L", value=length, unit="m", source="edge.length_m"), Quantity(name="runs", value=e.runs, source="edge.runs"), Quantity(name="R", value=r, unit="ohm/km", source=_cable_id(e))]
    for s, n, m, _ in mods:
        inputs += [Quantity(name=f"n[{s}]", value=n, source="network"), Quantity(name=f"Imp[{s}]", value=m.imp, unit="A", source=m.id)]
    inputs += [Quantity(name="N", value=n_min, source="network.modules_in_series"), Quantity(name="Vmp", value=m_min.vmp, unit="V", source=m_min.id)]
    return Calc(
        value=100.0 * dv / v,
        unit="%",
        formula="dV% = 100 x 2 x L x (I / runs) x R / 1000 / V, I = sum(n x Imp), V = N x Vmp",
        inputs=tuple(inputs),
        assumptions=(r_note, DC_OPERATING),
    )


def _ac_drop(project: Project, catalog: Catalog, e: Edge, length: float, r: float, r_note: str, x: float | None) -> Calc | None:
    ib = edge_current(project, catalog, e)
    if ib is None:
        return None
    v = float(next(q.value for q in ib.inputs if q.name == "V"))
    pf = project.design_basis.power_factor
    sin = math.sqrt(1 - pf * pf)
    n_ph = phases(project, catalog, e)
    k, k_text = (math.sqrt(3), "sqrt(3)") if n_ph == 3 else (2.0, "2")
    dv = k * length * (ib.value / e.runs) * (r * pf + (x or 0.0) * sin) / 1000.0
    return Calc(
        value=100.0 * dv / v,
        unit="%",
        formula=f"dV% = 100 x {k_text} x L x (Ib / runs) x (R cos phi + X sin phi) / 1000 / V; {ib.formula}",
        inputs=(
            Quantity(name="L", value=length, unit="m", source="edge.length_m"),
            Quantity(name="runs", value=e.runs, source="edge.runs"),
            Quantity(name="R", value=r, unit="ohm/km", source=_cable_id(e)),
            Quantity(name="X", value=x or 0.0, unit="ohm/km", source=_cable_id(e) if x is not None else "assumed 0"),
            Quantity(name="cos_phi", value=pf, source="design_basis.power_factor"),
            *ib.inputs,
        ),
        assumptions=(r_note, AC_LOADING, *ib.assumptions, *(() if x is not None else (NO_X,))),
    )


def _cable_id(e: Edge) -> str:
    return e.cable if isinstance(e.cable, str) else "edge.cable"


def segment_drop(project: Project, catalog: Catalog, e: Edge) -> Calc | None:
    """Voltage drop over one edge in percent of the circuit voltage, or None when the edge has no cable, length, resistance or carried generation."""
    cable = cable_of(catalog, e)
    r = cable.operating_resistance if cable is not None else None
    if r is None or e.length_m is None:
        return None
    if e.service is Service.DC:
        return _dc_drop(project, catalog, e, e.length_m, r[0], R_AT[r[1]])
    return _ac_drop(project, catalog, e, e.length_m, r[0], R_AT[r[1]], cable.x_ohm_per_km)


def missing_resistance(catalog: Catalog, e: Edge) -> str | None:
    """What the catalog lacks for the drop over a cabled edge with a length: the resistance field at the insulation's operating temperature."""
    cable = cable_of(catalog, e)
    if cable is None or e.length_m is None or cable.operating_resistance is not None:
        return None
    field = "r_ohm_per_km_70c" if cable.insulation == "pvc" else "r_ohm_per_km_90c"
    return f"{e.id} ({cable.id}, {cable.callout()}) has no {field} in the catalog"


def _path_edges(net: ElectricalNetwork, nodes: tuple[str, ...]) -> list[Edge]:
    out = []
    for a, b in zip(nodes, nodes[1:], strict=False):
        e = next((e for e in net.edges if e.is_power and e.src_node == a and e.dst_node == b), None)
        if e is not None:
            out.append(e)
    return out


def _segments(project: Project, catalog: Catalog, dc: bool, limit: float, rule_id: str) -> Iterator[Finding]:
    net = project.network
    for e in net.edges:
        if not e.is_power or (e.service is Service.DC) != dc:
            continue
        d = segment_drop(project, catalog, e)
        if d is None:
            continue
        yield Finding(
            rule_id=rule_id,
            severity=Severity.INFO,
            subjects=(e.id,),
            message=f"{edge_label(net, e)}: segment voltage drop {d.value:.2f} %",
            measured=round(d.value, 3),
            limit=round(100 * limit, 3),
            unit="%",
            formula=d.formula,
            inputs=d.inputs,
            assumptions=(*d.assumptions, *((f"Sized above its current: {e.sized_for}",) if e.sized_for else ())),
        )


def _cumulative(project: Project, catalog: Catalog, rule_id: str, start: str, edges: list[Edge], limit: float, limit_field: str, what: str) -> Finding:
    net = project.network
    parts, skipped, assumptions = [], [], []
    for e in edges:
        d = segment_drop(project, catalog, e)
        if d is None:
            skipped.append(e.id)
            continue
        parts.append((e, d))
        assumptions += [a for a in d.assumptions if a not in assumptions]
    end = edges[-1].dst_node if edges else start
    missing = [m for e in edges if (m := missing_resistance(catalog, e))]
    if missing:
        return Finding(
            rule_id=rule_id,
            severity=Severity.ERROR,
            subjects=(start, end, *dict.fromkeys(e.id for e in edges if missing_resistance(catalog, e))),
            message=f"{what} from {label(net, start)} to {label(net, end)} cannot be checked against {limit_field} {100 * limit:g} %: {'; '.join(dict.fromkeys(missing))}; "
                    "use a catalogued cable that has it, or add the value from the cable's cited datasheet to the catalog",
            assumptions=tuple(assumptions),
        )  # fmt: skip
    if not parts:
        return Finding(
            rule_id=rule_id,
            severity=Severity.INFO,
            subjects=(start, end),
            message=f"{what} from {label(net, start)} to {label(net, end)} not evaluated: no segment has a cable with resistance, a length and carried current ({', '.join(skipped)})",
        )
    total = sum(d.value for _, d in parts)
    if skipped:
        assumptions.append(f"Segments without cable, length, resistance or carried current are not counted: {', '.join(skipped)}.")
    over = total > 100 * limit
    working = " + ".join(f"{d.value:.2f}" for _, d in parts)
    return Finding(
        rule_id=rule_id,
        severity=Severity.ERROR if over else Severity.INFO,
        subjects=(start, end),
        message=f"{what} from {label(net, start)} to {label(net, end)}: {working} = {total:.2f} % {'exceeds' if over else 'within'} {limit_field} {100 * limit:g} %",
        measured=round(total, 3),
        limit=round(100 * limit, 3),
        unit="%",
        formula="dV%_total = sum(dV%_segment)",
        inputs=(
            *(Quantity(name=f"dV%[{e.id}]", value=round(d.value, 4), unit="%", source=d.formula) for e, d in parts),
            Quantity(name=limit_field, value=limit, source=f"design_basis.{limit_field}"),
        ),
        assumptions=tuple(assumptions),
    )


def _path(net: ElectricalNetwork, start: str) -> tuple[str, ...] | None:
    try:
        return net.path_to_poi(start)
    except NetworkError:
        return None


@rule("vdrop.dc", "DC voltage drop from string to inverter within the design basis")
def dc(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Per DC cable: dV% = 100 x 2 x L x (I / runs) x R / 1000 / V with I = n x Imp of the strings carried and V = N x Vmp of the string, R at the insulation's maximum operating temperature (90 degC XLPE, 70 degC PVC) (info per segment). Along the path from each string template to its inverter the segment drops are summed and compared with design_basis.vdrop_dc_max: error when over, and error when a cable on the path has no catalogued resistance (the drop cannot be checked)."""
    net, limit = project.network, project.design_basis.vdrop_dc_max
    yield from _segments(project, catalog, True, limit, "vdrop.dc")
    for s in net.by_kind(NodeKind.PV_STRING):
        if (nodes := _path(net, s.id)) is None:
            continue
        edges = []
        for e in _path_edges(net, nodes):
            if e.service is not Service.DC:
                break
            edges.append(e)
        if edges:
            yield _cumulative(project, catalog, "vdrop.dc", s.id, edges, limit, "vdrop_dc_max", "DC voltage drop")


@rule("vdrop.ac", "AC voltage drop from inverter to the point of interconnection within the design basis")
def ac(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Per AC cable: dV% = 100 x k x L x (Ib / runs) x (R cos phi + X sin phi) / 1000 / V with k = sqrt(3) for three-phase and 2 for single-phase, Ib the maximum inverter current, V the zone voltage, R at the insulation's maximum operating temperature (90 degC XLPE, 70 degC PVC) and cos phi the design-basis power factor (info per segment). Along the path from each inverter or PCS template to the POI the segment drops of every AC voltage level are summed and compared with design_basis.vdrop_ac_max: error when over, and error when a cable on the path has no catalogued resistance (the drop cannot be checked)."""
    net, limit = project.network, project.design_basis.vdrop_ac_max
    yield from _segments(project, catalog, False, limit, "vdrop.ac")
    for g in net.by_kind(*GENERATORS):
        if (nodes := _path(net, g.id)) is None:
            continue
        edges = [e for e in _path_edges(net, nodes) if e.service is not Service.DC]
        if edges:
            yield _cumulative(project, catalog, "vdrop.ac", g.id, edges, limit, "vdrop_ac_max", "AC voltage drop")


@rule("vdrop.bands", "Length-band cables keep within the worst-case cable's drop and carry the current")
def bands(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """A template edge may give its shorter instances smaller cables (Edge.bands): each band's cable, at the band's longest route (upto_m), must have a
    voltage drop no larger than the edge's own cable at the worst-case length (so every instance stays inside the path limit vdrop.dc / vdrop.ac check
    on the worst case), and an ampacity no lower than the design current and the rating of every device protecting the edge. Error otherwise."""
    from unicad.rules.cables import edge_ampacity, protectors

    net = project.network
    guards = None
    for e in net.edges:
        if not e.bands or e.length_m is None:
            continue
        worst = segment_drop(project, catalog, e)
        ib = edge_current(project, catalog, e)
        if guards is None:
            guards = protectors(project, catalog)
        need = max([ib.value if ib is not None else 0.0, *(s.rated_current for _, s in guards.get(e.id, []) if s.rated_current)])
        for b in e.bands:
            v = e.model_copy(update={"cable": b.cable, "runs": b.runs, "length_m": b.upto_m, "bands": ()})
            d, iz = segment_drop(project, catalog, v), edge_ampacity(project, catalog, v)
            problems = []
            if worst is not None and d is not None and d.value > worst.value + 1e-6:
                problems.append(f"drop {d.value:.3f} % at {b.upto_m:g} m exceeds the worst-case {worst.value:.3f} % of {e.cable} at {e.length_m:g} m")
            if iz is not None and iz.value < need - 1e-6:
                problems.append(f"ampacity {iz.value:.1f} A is below the {need:.1f} A it must carry")
            if not problems:
                continue
            yield Finding(
                rule_id="vdrop.bands", severity=Severity.ERROR, subjects=(e.id,),
                message=f"{edge_label(net, e)} band up to {b.upto_m:g} m ({b.runs} x {b.cable}): " + "; ".join(problems),
            )  # fmt: skip

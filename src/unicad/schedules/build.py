"""Build the cable schedule of a project from its network, the rules engine's per-edge results and the route estimates."""

from collections import defaultdict
from statistics import fmean

from unicad.catalog.store import Catalog
from unicad.domain.catalog import CableSpec
from unicad.domain.network import Edge, ElectricalNetwork, NetworkError, NodeKind, Service
from unicad.domain.project import Project
from unicad.routing import RouteAssumptions, RouteEstimate, estimate_routes, estimable_edges, template_of
from unicad.rules.cables import cable_of, edge_ampacity, edge_label, needs_cable, phases
from unicad.rules.topology import edge_current, instance_tag, module_of, per_instance
from unicad.rules.vdrop import segment_drop
from unicad.schedules.ids import SCHEME, Ids, indices
from unicad.schedules.model import CableRow, LengthMode, Schedule, Section

SERVICE_ORDER = {Service.DC: 0, Service.LV_AC: 1, Service.MV_AC: 2, Service.HV_AC: 3}
LOSS_BASIS = "Power loss is I^2 x R x L of every loaded conductor (two for DC, three for three-phase AC, two for single-phase AC) at the same resistance and current as the voltage drop."
POLARITY_BASIS = "Where the network states one length for a DC cable, +ve and -ve lengths are taken equal."
UNITS_BASIS = "Design current Ib and derated ampacity Iz are for all parallel runs of a row together; length is per run."


SHORT_LABEL = {NodeKind.PV_STRING: "STRING", NodeKind.PV_ARRAY_BLOCK: "ARRAY BLOCK"}


def kind_label(net: ElectricalNetwork, node_id: str) -> str:
    kind = net.node(node_id).kind
    return SHORT_LABEL.get(kind, kind.value.replace("_", " ").upper())


def section_title(net: ElectricalNetwork, e: Edge, cable: CableSpec) -> str:
    return f"{kind_label(net, e.src_node)} TO {kind_label(net, e.dst_node)} CABLE ({cable.size_mm2:g} sq.mm)"


def cables_per_run(cable: CableSpec, e: Edge, n_phases: int) -> int:
    if cable.cores > 1:
        return 1
    return 2 if e.service is Service.DC or n_phases == 1 else 3


def operating_current(project: Project, catalog: Catalog, e: Edge) -> float | None:
    """Current the voltage drop and loss are evaluated at: DC the carried strings' Imp (as the rules engine does), AC the design current."""
    net = project.network
    if e.service is not Service.DC:
        ib = edge_current(project, catalog, e)
        return None if ib is None else ib.value
    ids = [e.src_node, *net.upstream(e.src_node)]
    total, found = 0.0, False
    for s in ids:
        node = net.node(s)
        if node.kind is not NodeKind.PV_STRING or (m := module_of(net, catalog, node)) is None:
            continue
        total += (1 if s == e.src_node else per_instance(net, s, e.src_node)) * m.imp
        found = True
    return total if found else None


class _Electrical:
    """Per-edge results from the rules engine's functions, cached on edge and length."""

    def __init__(self, project: Project, catalog: Catalog):
        self.project, self.catalog = project, catalog
        self._by_edge: dict[str, tuple[float | None, float | None, int]] = {}
        self._by_length: dict[tuple[str, float], tuple[float | None, float | None]] = {}

    def fixed(self, e: Edge) -> tuple[float | None, float | None, int]:
        if e.id not in self._by_edge:
            ib = edge_current(self.project, self.catalog, e)
            iz = edge_ampacity(self.project, self.catalog, e)
            self._by_edge[e.id] = (None if ib is None else ib.value, None if iz is None else iz.value, phases(self.project, self.catalog, e))
        return self._by_edge[e.id]

    def at_length(self, e: Edge, length: float) -> tuple[float | None, float | None]:
        key = (e.id, round(length, 6))
        if key not in self._by_length:
            drop = segment_drop(self.project, self.catalog, e.model_copy(update={"length_m": length}))
            self._by_length[key] = (None if drop is None else drop.value, self._loss(e, length))
        return self._by_length[key]

    def _loss(self, e: Edge, length: float) -> float | None:
        cable = cable_of(self.catalog, e)
        current = operating_current(self.project, self.catalog, e)
        if cable is None or cable.r_ohm_per_km_90c is None or current is None:
            return None
        n_ph = self.fixed(e)[2]
        conductors = 2 if e.service is Service.DC or n_ph == 1 else 3
        return conductors * e.runs * (current / e.runs) ** 2 * cable.r_ohm_per_km_90c * length / 1000.0


def build_schedule(
    project: Project,
    catalog: Catalog,
    mode: LengthMode = LengthMode.AUTO,
    assumptions: RouteAssumptions = RouteAssumptions(),
    estimate: RouteEstimate | None = None,
) -> Schedule:
    """One row per cable instance. Lengths the network states are used as given in `auto` and `network` mode; `auto` estimates
    the edges that state none, `estimated` replaces the stated ones wherever the layout allows."""
    net = project.network
    expanded = net.expand()
    cabled = estimable_edges(project, catalog)
    if estimate is None:
        if mode is LengthMode.NETWORK:
            estimate = RouteEstimate()
        else:
            want = [e.id for e in cabled if mode is LengthMode.ESTIMATED or e.length_m is None]
            estimate = estimate_routes(project, catalog, assumptions, want) if want else RouteEstimate()
    ids = Ids(project, expanded)
    elec = _Electrical(project, catalog)
    instances: dict[str, list[Edge]] = defaultdict(list)
    for ie in expanded.edges:
        instances[template_of(ie.id)].append(ie)

    ordered = sorted(cabled, key=lambda e: (SERVICE_ORDER[e.service], net.edges.index(e)))
    sections: list[Section] = []
    gaps: list[str] = []
    seen_ids: set[str] = set()
    sl = 0
    for e in ordered:
        cable = cable_of(catalog, e)
        assert cable is not None
        ib, iz, n_ph = elec.fixed(e)
        per_run = cables_per_run(cable, e, n_ph)
        rows: list[CableRow] = []
        src_node = net.node(e.src_node)
        module = module_of(net, catalog, src_node) if src_node.kind is NodeKind.PV_STRING else None
        for ie in sorted(instances[e.id], key=lambda x: indices(x.id)):
            est = estimate.instances.get(ie.id)
            if est is not None:
                pos, neg, source, basis = est.positive_m, est.negative_m, "estimated", est.basis
            elif e.length_m is not None:
                pos, neg, source, basis = e.length_m, None, "network", "length stated by the network"
            else:
                pos, neg, source, basis = None, None, "none", estimate.unresolved.get(e.id, "no length stated and none estimated")
            if e.service is Service.DC and pos is not None and neg is None:
                neg = pos
            eff = None if pos is None else ((pos + neg) / 2 if neg is not None else pos)
            vd, loss = elec.at_length(e, eff) if eff is not None else (None, None)
            from_tag, to_tag = instance_tag(net, ie.src_node), instance_tag(net, ie.dst_node)
            cable_id = _unique(f"{ids.string_stem(ie.src_node)}-{cable.size_mm2:g} sqmm" if src_node.kind is NodeKind.PV_STRING else f"{from_tag}-{to_tag}", seen_ids)
            if src_node.kind is NodeKind.PV_STRING:
                from_tag = ids.string_stem(ie.src_node)
            sl += 1
            rows.append(
                CableRow(
                    sl=sl, cable_id=cable_id, edge=e.id, instance=ie.id, src=ie.src_node, dst=ie.dst_node, service=e.service,
                    block=ids.block_of(ie.src_node), is_string=src_node.kind is NodeKind.PV_STRING, from_tag=from_tag, to_tag=to_tag,
                    cable_ref=e.cable if isinstance(e.cable, str) else cable.id, callout=cable.callout(), cores=cable.core_label,
                    size_mm2=cable.size_mm2, conductor=cable.conductor.value.upper(), runs=e.runs, cables_per_run=per_run,
                    length_pos_m=pos, length_neg_m=neg, length_source=source, length_basis=basis,
                    laying=e.laying.value if e.laying else None, ib_a=ib, iz_a=iz, vd_pct=vd, loss_w=loss,
                    module=module.model if module else None, module_wp=module.pmax if module else None, remarks=e.remarks or "",
                    flags=_flags(e, iz, pos, vd),
                )
            )  # fmt: skip
        if rows:
            sections.append(Section(section_title(net, e, cable), e.id, e.service, tuple(rows)))
        if e.length_m is None and any(r.length_pos_m is None for r in rows):
            gaps.append(f"{edge_label(net, e)}: no length stated and none estimated ({estimate.unresolved.get(e.id, 'no array layout')}); its {len(rows)} row(s) carry no length, voltage drop or loss")
    gaps += _missing_cables(project, catalog)

    schedule_rows = [r for s in sections for r in s.rows]
    basis = _basis(project, catalog, sections)
    summary = _vd_summary(project, expanded, schedule_rows)
    notes = [UNITS_BASIS, LOSS_BASIS, POLARITY_BASIS, *estimate.assumptions] if any(r.length_source == "estimated" for r in schedule_rows) else [UNITS_BASIS, LOSS_BASIS, POLARITY_BASIS]
    if mode is not LengthMode.ESTIMATED and any(r.length_source == "network" for r in schedule_rows):
        notes.append("Lengths marked NET are as stated in the network and are not measured on the layout.")
    notes.append(SCHEME)
    notes += list(project.design_basis.assumptions)
    return Schedule(
        project_id=project.id, project_name=project.name, sections=tuple(sections), basis=basis, vd_summary=summary,
        assumptions=tuple(dict.fromkeys(notes)), warnings=estimate.warnings, gaps=tuple(gaps), template_lengths=estimate.templates(),
    )  # fmt: skip


def _flags(e: Edge, iz: float | None, length: float | None, vd: float | None) -> tuple[str, ...]:
    flags = []
    if e.laying is None:
        flags.append("no laying method: ampacity not checked")
    elif iz is None:
        flags.append("no catalog ampacity for this laying")
    if length is not None and vd is None:
        flags.append("voltage drop not evaluated: no catalog resistance or no carried generation")
    return tuple(flags)


def _unique(candidate: str, seen: set[str]) -> str:
    out, n = candidate, 1
    while out in seen:
        n += 1
        out = f"{candidate}/{n}"
    seen.add(out)
    return out


def _missing_cables(project: Project, catalog: Catalog) -> list[str]:
    net = project.network
    return [f"{edge_label(net, e)} ({e.service}) has no cable; it is not scheduled" for e in net.edges if e.is_power and e.cable is None and needs_cable(net, e)]


def _basis(project: Project, catalog: Catalog, sections: list[Section]) -> tuple[tuple[str, str], ...]:
    net = project.network
    out: list[tuple[str, str]] = []
    dc = next((s for s in sections if s.service is Service.DC), None)
    if dc is not None:
        e = net.edge_by_id[dc.edge]
        mod = module_of(net, catalog, net.node(e.src_node)) if net.node(e.src_node).kind is NodeKind.PV_STRING else None
        if mod is not None:
            out.append(("MODULE", f"{mod.manufacturer} {mod.model}: {mod.pmax:g} Wp, Vmp {mod.vmp:g} V, Imp {mod.imp:g} A, Voc {mod.voc:g} V, Isc {mod.isc:g} A"))
    for service, name in ((Service.DC, "DC"), (Service.LV_AC, "LV AC"), (Service.MV_AC, "MV AC")):
        used = {}
        for s in sections:
            if s.service is service:
                cable = cable_of(catalog, net.edge_by_id[s.edge])
                if cable is not None:
                    used[cable.id] = cable
        if used:
            text = "; ".join(f"{c.callout()} {'R90 ' + format(c.r_ohm_per_km_90c, 'g') + ' ohm/km' if c.r_ohm_per_km_90c else 'no catalog resistance'}" for c in used.values())
            out.append((f"{name} CABLES", text))
    for service, label in ((Service.DC, "DC VOLTAGE DROP"), (Service.LV_AC, "AC VOLTAGE DROP")):
        s = next((s for s in sections if s.service is service or (label.startswith("AC") and s.service in (Service.LV_AC, Service.MV_AC, Service.HV_AC))), None)
        if s is None:
            continue
        e = net.edge_by_id[s.edge]
        d = segment_drop(project, catalog, e)
        if d is not None:
            out.append((label, f"{d.formula}. {' '.join(d.assumptions)}"))
    return tuple(out)


def _vd_summary(project: Project, expanded: ElectricalNetwork, rows: list[CableRow]) -> tuple[tuple[str, str], ...]:
    """Average and worst voltage drop per cable type and along each path to the POI, summed from the rows' own per-cable values."""
    db = project.design_basis
    by_pair = {(r.src, r.dst): r for r in rows}
    out: list[tuple[str, str]] = []
    per_edge: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.vd_pct is not None:
            per_edge[r.edge].append(r.vd_pct)
    for edge, values in per_edge.items():
        out.append((f"VD {edge}", f"average {fmean(values):.2f} %, maximum {max(values):.2f} %"))
    for name, dc, limit, kinds in (("DC", True, db.vdrop_dc_max, (NodeKind.PV_STRING,)), ("AC", False, db.vdrop_ac_max, (NodeKind.INVERTER, NodeKind.PCS))):
        totals = []
        for start in expanded.by_kind(*kinds):
            try:
                path = expanded.path_to_poi(start.id)
            except NetworkError:
                continue
            drops = []
            for a, b in zip(path, path[1:]):
                r = by_pair.get((a, b))
                if r is None or r.vd_pct is None:
                    continue
                if r.is_dc != dc:
                    if dc:
                        break
                    continue
                drops.append(r.vd_pct)
            if drops:
                totals.append(sum(drops))
        if totals:
            verdict = "within" if max(totals) <= 100 * limit else "exceeds"
            out.append((f"{name} VD, WORST PATH", f"{max(totals):.2f} % {verdict} the design-basis limit of {100 * limit:g} % (average path {fmean(totals):.2f} %, {len(totals)} paths)"))
    return tuple(out)

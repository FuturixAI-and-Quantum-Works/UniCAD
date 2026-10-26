"""From a short intent to a sized, rule-checked electrical network."""

import math
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from unicad.arrays import PlacementResult
from unicad.catalog.store import Catalog
from unicad.domain.catalog import InverterSpec, ModuleSpec, ProtectionSpec, TransformerSpec
from unicad.domain.layout import ArrayLayout, StringAssignment
from unicad.domain.network import ElectricalNetwork
from unicad.domain.project import GridConnection, Project, Segment
from unicad.design import templates as T
from unicad.design.plan import PvPlan, from_layout, uniform
from unicad.design.sizing import size_network
from unicad.design.strings import DesignError, StringDesign, size_strings
from unicad.rules import Finding, Severity, run_rules
from unicad.rules.ratio import plant_capacity
from unicad.rules.thresholds import VOLTAGE_TOLERANCE

Template = Literal["residential", "ci_rooftop", "utility_string", "utility_central", "bess_ac", "bess_dc", "hybrid"]
SEGMENT_TEMPLATE = {
    Segment.RESIDENTIAL: "residential",
    Segment.CI_ROOFTOP: "ci_rooftop",
    Segment.CARPORT: "ci_rooftop",
    Segment.BESS: "bess_ac",
    Segment.HYBRID: "hybrid",
}
TEMPLATE_SEGMENT = {
    "residential": Segment.RESIDENTIAL, "ci_rooftop": Segment.CI_ROOFTOP, "utility_string": Segment.UTILITY, "utility_central": Segment.UTILITY,
    "bess_ac": Segment.BESS, "bess_dc": Segment.BESS, "hybrid": Segment.HYBRID,
}  # fmt: skip
PV_TEMPLATES = frozenset({"residential", "ci_rooftop", "utility_string", "utility_central", "bess_dc", "hybrid"})
CI_STEP_UP_KW = 1000.0


class Intent(BaseModel):
    """What the agent asks for. Everything except the size is optional; defaults are picked from the catalog and reported as assumptions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template: Template | None = Field(default=None, description="Topology; default from the project segment (utility picks central or string from the inverter)")
    ac_kw: float | None = Field(default=None, gt=0, description="PV AC capacity in kW (inverter kVA); for bess_ac the storage power")
    dc_kwp: float | None = Field(default=None, gt=0, description="PV DC capacity in kWp")
    module: str | None = None
    inverter: str | None = None
    grid_voltage_v: float | None = Field(default=None, gt=0)
    modules_per_string: int | None = Field(default=None, gt=0)
    strings_per_inverter: int | None = Field(default=None, gt=0)
    inverters_per_block: int | None = Field(default=None, gt=0)
    step_up_kv: float | None = Field(default=None, gt=0, description="C&I only: step up to this HT voltage; default 11 kV above 1 MW when the grid is HT")
    connection: Literal["lt_panel", "dedicated_feeder"] = "lt_panel"
    metering: Literal["net", "abt"] = "net"
    block_switch: Literal["vcb", "rmu"] = "vcb"
    auxiliary: bool = True
    export_limit_kw: float | None = Field(default=None, gt=0)
    bess_kw: float | None = Field(default=None, gt=0)
    bess_kwh: float | None = Field(default=None, gt=0)
    pcs: str | None = None
    battery: str | None = None
    lengths: dict[str, float] = Field(default_factory=dict, description="Override cable route lengths in m by role (string, scb_inv, vcb_bus, ...)")


class AutobuildResult(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    project: Project
    template: str
    strings: StringDesign | None
    counts: dict[str, int]
    findings: tuple[Finding, ...]
    blockers: tuple[str, ...]
    assumptions: tuple[str, ...]
    summary: dict[str, Any]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)


def _module(catalog: Catalog, module_id: str | None, notes: list[str]) -> ModuleSpec:
    if module_id:
        m = catalog.get(module_id)
        if not isinstance(m, ModuleSpec):
            raise DesignError(f"{module_id!r} is a {m.kind}, not a module")
        return m
    mods = [m for m in catalog.filter("module") if isinstance(m, ModuleSpec) and m.almm_listed]
    if not mods:
        raise DesignError("the catalog has no ALMM-listed module; pass module")
    m = max(mods, key=lambda m: (m.pmax, m.id))
    notes.append(f"Module not given: picked {m.id} (highest Pmax among ALMM-listed catalog modules).")
    return m


def _inverter(catalog: Catalog, inv_id: str | None, template: str, grid: GridConnection, notes: list[str]) -> InverterSpec:
    if inv_id:
        i = catalog.get(inv_id)
        if not isinstance(i, InverterSpec):
            raise DesignError(f"{inv_id!r} is a {i.kind}, not an inverter")
        return i
    phases = grid.phases
    lt_v = grid.voltage_v if grid.voltage_v <= 1000 else 415.0
    invs = [i for i in catalog.filter("inverter") if isinstance(i, InverterSpec)]
    lt_ok = [i for i in invs if abs(i.ac_voltage - lt_v) / i.ac_voltage <= VOLTAGE_TOLERANCE.value]
    if template in ("utility_central", "bess_dc"):
        pool = [i for i in invs if i.topology == "central"]
        why = "largest central inverter"
        pick = max(pool, key=lambda i: (i.ac_rated_kva, i.id), default=None)
    elif template == "residential":
        pool = [i for i in lt_ok if i.topology == "string" and i.phases == phases]
        why = f"smallest {phases}-phase string inverter"
        pick = min(pool, key=lambda i: (i.ac_rated_kva, i.id), default=None)
    else:
        pool = [i for i in lt_ok if i.topology == "string" and i.phases == 3]
        why = "largest 400 V three-phase string inverter"
        pick = max(pool, key=lambda i: (i.ac_rated_kva, i.id), default=None)
    if pick is None:
        raise DesignError(f"the catalog has no {why.split(' ', 1)[1]}; pass inverter")
    notes.append(f"Inverter not given: picked {pick.id} ({why} in the catalog).")
    return pick


def _template(project: Project, intent: Intent, catalog: Catalog) -> str:
    if intent.template:
        return intent.template
    if project.segment is Segment.UTILITY:
        if intent.inverter and getattr(catalog.get(intent.inverter), "topology", None) == "central":
            return "utility_central"
        return "utility_string" if intent.inverter else "utility_central"
    return SEGMENT_TEMPLATE[project.segment]


def _with_grid(project: Project, v: float | None) -> Project:
    if v is None or v == project.design_basis.grid.voltage_v:
        return project
    g = project.design_basis.grid
    grid = GridConnection(voltage_v=v, frequency_hz=g.frequency_hz, phases=1 if v <= 250 else 3, utility=g.utility)
    basis = project.design_basis.model_copy(update={"grid": grid, "assumptions": (*project.design_basis.assumptions, f"Grid voltage set to {v:g} V by network_autobuild.")})
    return project.model_copy(update={"design_basis": basis})


def _layout_of(layout: PlacementResult | ArrayLayout | None) -> ArrayLayout | None:
    if isinstance(layout, PlacementResult):
        return layout.layout
    return layout


def _plan(template: str, intent: Intent, sd: StringDesign, inverter: InverterSpec, module: ModuleSpec, catalog: Catalog, layout: ArrayLayout | None, target: float, kv: float) -> PvPlan:
    mps = sd.modules_per_string
    string_kwp = mps * module.pmax / 1000
    spi_cap = sd.strings_per_inverter_max
    spi = intent.strings_per_inverter or sd.strings_per_inverter
    if intent.strings_per_inverter and intent.strings_per_inverter > spi_cap:
        raise DesignError(f"strings_per_inverter {intent.strings_per_inverter} exceeds the limit {spi_cap} ({sd.binding['strings_per_inverter_max']})")
    per_block_cap = None
    if template in ("utility_string", "hybrid") and inverter.topology != "central":
        per_block_cap = intent.inverters_per_block or _string_block_cap(catalog, inverter, kv)
    elif inverter.topology == "central":
        per_block_cap = intent.inverters_per_block or 2
    if layout is not None:
        if template == "residential":
            raise DesignError("the residential template does not take a layout")
        return from_layout(layout, mps, spi, spi_cap, per_block_cap)
    if template == "residential":
        dc = intent.dc_kwp or (intent.ac_kw or 0) * target
        strings = max(1, round(dc / string_kwp))
        n_inv = math.ceil(strings / spi_cap)
        return uniform(1, n_inv, math.ceil(strings / n_inv), mps)
    if intent.ac_kw:
        n_inv = math.ceil(intent.ac_kw / inverter.ac_rated_kva - 1e-9)
        if intent.dc_kwp and not intent.strings_per_inverter:
            spi = min(spi_cap, math.ceil(intent.dc_kwp / string_kwp / n_inv))
    elif intent.dc_kwp:
        strings = math.ceil(intent.dc_kwp / string_kwp - 1e-9)
        n_inv = math.ceil(strings / spi)
        spi = math.ceil(strings / n_inv)
    else:
        raise DesignError("give ac_kw or dc_kwp")
    if inverter.topology == "central":
        m = math.ceil(spi / T.SCB_STRINGS)
        spi = m * (spi // m)
    if per_block_cap is None:
        return uniform(1, n_inv, spi, mps)
    blocks = math.ceil(n_inv / per_block_cap)
    return uniform(blocks, math.ceil(n_inv / blocks), spi, mps)


def _string_block_cap(catalog: Catalog, inverter: InverterSpec, kv: float) -> int:
    tr_max = max((s.rated_kva for s in catalog.filter("transformer") if isinstance(s, TransformerSpec) and s.hv_kv == kv), default=0.0)
    acb = max((p.rated_current for p in catalog.filter("protection") if isinstance(p, ProtectionSpec) and p.device == "acb" and p.rated_current), default=0.0)
    by_acb = acb / 1.25 * math.sqrt(3) * 415.0 / 1000
    return max(1, int(min(tr_max, by_acb) // inverter.ac_max_kva))


def _link_strings(net: ElectricalNetwork, links) -> tuple[StringAssignment, ...]:
    out = []
    for node_ids, count, slots in links:
        chunks = []
        for nid in node_ids:
            ids = net.instance_ids(nid)
            size = len(ids) // count
            chunks.append([ids[j * size : (j + 1) * size] for j in range(count)])
        for j in range(count):
            mine = [i for ch in chunks for i in ch[j]]
            if len(mine) != len(slots[j]):
                raise DesignError(f"internal: inverter {j + 1} of {node_ids} has {len(mine)} network strings for {len(slots[j])} layout strings")
            out += [StringAssignment(string=sid, spans=a.spans) for sid, a in zip(mine, slots[j], strict=True)]
    return tuple(out)


def summarize(project: Project, catalog: Catalog) -> dict[str, Any]:
    net = project.network
    kinds: Counter[str] = Counter()
    for n in net.nodes:
        kinds[n.kind.value] += net.instance_count(n.id)
    dc, ac = plant_capacity(project, catalog)
    return {
        "templates": {"groups": len(net.groups), "nodes": len(net.nodes), "edges": len(net.edges)},
        "instances": dict(sorted(kinds.items())),
        "dc_kwp": round(dc, 3),
        "ac_kva": round(ac, 1),
        "dc_ac_ratio": round(dc / ac, 4) if ac else None,
    }


def autobuild(project: Project, catalog: Catalog, intent: Intent | dict, layout: PlacementResult | ArrayLayout | None = None) -> AutobuildResult:
    """Replace the project's network with one built from `intent`, sized by the rules, and report what the rules still find.

    With a layout (a place_array result or the project's array layout) the PV plan follows its blocks and every layout string becomes
    exactly one pv_string instance; the returned project's array_layout carries the links."""
    intent = Intent.model_validate(intent)
    notes: list[str] = []
    project = _with_grid(project, intent.grid_voltage_v)
    template = _template(project, intent, catalog)
    lay = _layout_of(layout)
    basis = project.design_basis
    lengths = {**T.default_lengths(template), **intent.lengths}
    ctx = T.Ctx(project, catalog, lengths)
    sd = None
    if template in PV_TEMPLATES:
        if not (intent.ac_kw or intent.dc_kwp or lay is not None):
            raise DesignError(f"template {template} needs ac_kw, dc_kwp or a layout")
        module = _module(catalog, intent.module, notes)
        inverter = _inverter(catalog, intent.inverter, template, basis.grid, notes)
        sd = size_strings(module, inverter, basis, intent.modules_per_string)
        plan = _plan(template, intent, sd, inverter, module, catalog, lay, basis.dc_ac_ratio_target, basis.grid.voltage_v / 1000)
        if plan.unstrung_modules:
            notes.append(f"{plan.unstrung_modules} layout modules do not make up a whole string of {plan.modules_per_string} and are left unstrung.")
    if template == "residential":
        if inverter.ac_rated_kva > 2 * (intent.ac_kw or intent.dc_kwp or 0.0):
            ctx.built.blockers.append(f"catalog gap: no inverter near {intent.ac_kw or intent.dc_kwp:g} kW; {inverter.id} ({inverter.ac_rated_kva:g} kVA) is the smallest {basis.grid.phases}-phase string inverter catalogued")
        T.residential(ctx, plan, inverter, module)
    elif template == "ci_rooftop":
        step = intent.step_up_kv
        if step is None and basis.grid.voltage_v > 1000:
            step = basis.grid.voltage_v / 1000
        if step is None and (intent.ac_kw or 0) > CI_STEP_UP_KW:
            notes.append(f"Above {CI_STEP_UP_KW:g} kW an HT connection (step_up_kv) is usual; this build stays at LT because the grid is {basis.grid.voltage_v:g} V.")
        T.ci_rooftop(ctx, plan, inverter, module, step, intent.connection, intent.metering)
    elif template == "utility_string":
        T.utility_string(ctx, plan, inverter, module, intent.block_switch, intent.auxiliary, intent.export_limit_kw)
    elif template == "utility_central":
        T.utility_central(ctx, plan, inverter, module, intent.block_switch, intent.auxiliary, intent.export_limit_kw)
    elif template == "bess_dc":
        kw = intent.bess_kw or (intent.ac_kw or plan.inverters * inverter.ac_rated_kva) * 0.25
        if not intent.bess_kw:
            notes.append(f"bess_kw not given: {kw:g} kW (25 % of the PV AC capacity) assumed.")
        battery = T.bess_dc_battery(catalog, inverter, plan.inverters, kw, intent.bess_kwh, intent.battery)
        T.bess_dc(ctx, plan, inverter, module, battery, intent.block_switch, intent.auxiliary, intent.export_limit_kw)
    elif template in ("bess_ac", "hybrid"):
        kw = intent.bess_kw or (intent.ac_kw if template == "bess_ac" else None)
        if not kw:
            raise DesignError(f"template {template} needs bess_kw{' or ac_kw' if template == 'bess_ac' else ''}")
        bp = T.plan_bess(catalog, kw, intent.bess_kwh, intent.pcs, intent.battery)
        notes.append(f"BESS: {bp.blocks} blocks x {bp.pcs_per_block} x {bp.pcs.id} with {bp.batteries_per_pcs} x {bp.battery.id} each: {bp.kw:g} kVA, {bp.kwh:g} kWh.")
        if template == "bess_ac":
            T.bess_ac(ctx, bp, intent.block_switch, intent.auxiliary, intent.export_limit_kw)
        else:
            T.hybrid(ctx, plan, inverter, module, bp, intent.block_switch, intent.auxiliary, intent.export_limit_kw)
    built = ctx.built
    b = built.builder
    net = b.network()
    linked = _link_strings(net, built.string_links) if lay is not None else ()
    if lay is not None:
        array_layout = ArrayLayout(blocks=lay.blocks, strings=linked)
    elif project.array_layout is not None:
        array_layout = project.array_layout.model_copy(update={"strings": ()})
        notes.append("The existing array layout is kept without string links: the new network's strings were not built from it.")
    else:
        array_layout = None
    sheets = []
    for sh in project.sheets:
        keep = tuple(r for r in sh.scope.nodes if r in net.node_by_id or r in net.group_by_id)
        if keep != sh.scope.nodes:
            notes.append(f"Sheet {sh.id!r} scope {list(sh.scope.nodes)} no longer matches the network; kept only {list(keep)}.")
            sh = sh.model_copy(update={"scope": sh.scope.model_copy(update={"nodes": keep})})
        sheets.append(sh)
    draft = project.model_copy(update={"network": net, "array_layout": array_layout, "sheets": tuple(sheets)})
    sizing = size_network(draft, catalog, b.node_hints, b.edge_hints)
    sized = Project.model_validate(sizing.project.model_dump(mode="json"), context={"catalog": catalog})
    report = run_rules(sized, catalog)
    findings = tuple(f for f in report.findings if f.severity is not Severity.INFO)
    summary = summarize(sized, catalog) | {"strings_linked": len(linked), "segment_hint": TEMPLATE_SEGMENT[template].value}
    if sd is not None:
        summary |= {"modules_per_string": plan.modules_per_string, "inverters": plan.inverters, "strings": plan.strings}
    return AutobuildResult(
        project=sized,
        template=template,
        strings=sd,
        counts=report.counts,
        findings=findings,
        blockers=tuple(dict.fromkeys([*built.blockers, *sizing.blockers])),
        assumptions=tuple(dict.fromkeys([*notes, *(sd.assumptions if sd else ()), *b.assumptions, *sizing.assumptions])),
        summary=summary,
    )

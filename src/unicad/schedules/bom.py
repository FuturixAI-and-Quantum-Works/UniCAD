"""Bill of materials: quantities by catalog item from the network expansion, plus cable metres and terminations from the schedule."""

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.catalog import (
    BatterySpec,
    CableSpec,
    DataLoggerSpec,
    InverterSpec,
    MeterSpec,
    ModuleSpec,
    PCSSpec,
    ProtectionSpec,
    TransformerSpec,
)
from unicad.domain.network import Node, NodeKind, Service
from unicad.domain.project import Project
from unicad.rules.protection import isolator_devices
from unicad.rules.topology import per_instance
from unicad.schedules.earthing_bom import EARTHING, LIGHTNING, earthing_lines
from unicad.schedules.lines import BomLine
from unicad.schedules.model import Schedule

DEFAULT_WASTAGE_PCT = 5.0

CATEGORIES: tuple[tuple[str, frozenset[NodeKind]], ...] = (
    ("PV MODULES", frozenset({NodeKind.PV_STRING})),
    ("INVERTERS AND PCS", frozenset({NodeKind.INVERTER, NodeKind.PCS})),
    ("BATTERIES", frozenset({NodeKind.BATTERY})),
    ("COMBINER BOXES", frozenset({NodeKind.SCB, NodeKind.DC_FUSE_BOX})),
    ("TRANSFORMERS", frozenset({NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER})),
    ("BREAKERS", frozenset({NodeKind.BREAKER, NodeKind.VCB})),
    ("ISOLATORS, FUSES AND RMUs", frozenset({NodeKind.ISOLATOR, NodeKind.DC_ISOLATOR, NodeKind.FUSE, NodeKind.RMU})),
    ("SURGE AND LIGHTNING PROTECTION", frozenset({NodeKind.DC_SPD, NodeKind.AC_SPD, NodeKind.LIGHTNING_ARRESTER})),
    ("METERING AND INSTRUMENT TRANSFORMERS", frozenset({NodeKind.METER, NodeKind.CT, NodeKind.PT})),
    ("PROTECTION RELAYS", frozenset({NodeKind.RELAY})),
    ("PANELS AND BUSBARS", frozenset({NodeKind.ACDB, NodeKind.LT_PANEL, NodeKind.HT_PANEL, NodeKind.BUSBAR})),
    (EARTHING, frozenset({NodeKind.EARTH_ELECTRODE})),
)
EXCLUDED = frozenset({NodeKind.POI_GRID, NodeKind.LOAD, NodeKind.DG})
CABLES = "CABLES"
ACCESSORIES = "CABLE ACCESSORIES"
CATEGORY_ORDER = tuple(c for c, _ in CATEGORIES) + (LIGHTNING, CABLES, ACCESSORIES)
BY_OTHERS = "by others"
NOT_SUPPLIED = ("existing", "by_others")
FUSED_POLES = "fused_poles"


@dataclass(frozen=True)
class Bom:
    project_id: str
    project_name: str
    lines: tuple[BomLine, ...]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]

    def qty(self, category: str, catalog_id: str | None = None) -> float:
        return sum(ln.qty for ln in self.lines if ln.category == category and (catalog_id is None or ln.catalog_id == catalog_id))

    def by_category(self) -> dict[str, list[BomLine]]:
        out: dict[str, list[BomLine]] = defaultdict(list)
        for ln in self.lines:
            out[ln.category].append(ln)
        return dict(out)

    def summary(self) -> dict[str, float]:
        return {
            "lines": len(self.lines),
            "modules": self.qty("PV MODULES"),
            "inverters": self.qty("INVERTERS AND PCS"),
            "combiner_boxes": self.qty("COMBINER BOXES"),
            "transformers": self.qty("TRANSFORMERS"),
            "cable_m_ordered": self.qty(CABLES),
        }


UTILITY_SPEC = re.compile(r"^Generic \((?P<who>[A-Z][A-Z0-9-]+) specification\)$")


def _maker(spec: object, utility: str | None) -> tuple[str, str]:
    """The maker shown for a catalog item, and a remark. A generic item transcribed from one utility's purchase specification is billed as generic to the
    project's utility specification, with the source utility in the remark, so a GETCO project does not order to a WBSEDCL specification."""
    maker = getattr(spec, "manufacturer", "")
    m = UTILITY_SPEC.match(maker)
    if m is None or (utility and m["who"].upper() == utility.upper()):
        return maker, ""
    return f"Generic ({utility or 'utility'} specification)", f"ratings transcribed from a {m['who']} specification; confirm against {utility or 'the utility'}'s"


def _describe(spec: object, node: Node, utility: str | None = None) -> str:
    maker, _ = _maker(spec, utility)
    if maker != getattr(spec, "manufacturer", maker):
        spec = spec.model_copy(update={"manufacturer": maker})
    match spec:
        case ModuleSpec():
            return f"{spec.manufacturer} {spec.model}, {spec.pmax:g} Wp"
        case InverterSpec():
            return f"{spec.manufacturer} {spec.model}, {spec.ac_rated_kva:g} kVA"
        case PCSSpec():
            return f"{spec.manufacturer} {spec.model}, {spec.rated_kva:g} kVA"
        case BatterySpec():
            return f"{spec.manufacturer} {spec.model}, {spec.energy_kwh:g} kWh"
        case TransformerSpec():
            return f"{spec.manufacturer} {spec.model}, {spec.rated_kva:g} kVA, {spec.hv_kv:g}/{'-'.join(f'{v:g}' for v in spec.lv_kv)} kV, {spec.vector_group}"
        case ProtectionSpec():
            rating = f", {spec.rated_current:g} A" if spec.rated_current else ""
            return f"{spec.manufacturer} {spec.model}, {spec.device.upper().replace('_', ' ')}{rating}, {spec.rated_voltage:g} V"
        case MeterSpec():
            return f"{spec.manufacturer} {spec.model}, {spec.function} meter, class {spec.accuracy_class}"
        case DataLoggerSpec():
            return f"{spec.manufacturer} {spec.model}, data logger, {spec.rs485_ports} x RS-485"
    return node.title or node.kind.value.replace("_", " ").upper()


def _node_lines(project: Project, catalog: Catalog, warnings: list[str], skip: frozenset[NodeKind] = frozenset()) -> list[BomLine]:
    net = project.network
    totals: dict[tuple[str, str | None, str], float] = {}
    notes: dict[tuple[str, str | None, str], dict[str, None]] = {}
    strings: dict[tuple[str, str | None, str], dict[int, int]] = {}
    for category, kinds in CATEGORIES:
        for n in net.nodes:
            if n.kind not in kinds or n.kind in skip or not_supplied(n):
                continue
            count = net.instance_count(n.id)
            remarks = ""
            series, n_strings = 0, 0
            if n.kind is NodeKind.PV_STRING:
                if n.modules_in_series is None:
                    warnings.append(f"string {n.id} has no modules_in_series, so its modules are not counted")
                    continue
                series, n_strings = n.modules_in_series, count
                count *= n.modules_in_series
            elif n.kind is NodeKind.FUSE and isinstance(poles := n.attributes.get(FUSED_POLES), int) and poles > 1:
                count *= poles
                remarks = f"{poles} fuse links per string (+ and - poles)" + (" in the SCB" if any(net.node(m).kind is NodeKind.SCB for m in net.downstream(n.id)[:1]) else "")
            elif n.kind is NodeKind.DC_ISOLATOR and n.spec is not None and (split := isolator_devices(n, catalog.get(n.spec))) is not None:
                ways, per, devices = split
                count *= devices
                remarks = f"{ways} string circuits per inverter on {devices} x {2 * per}-pole switch-disconnectors ({per} circuit(s) each)"
            elif n.kind in (NodeKind.SCB, NodeKind.DC_FUSE_BOX):
                inputs = sum(per_instance(net, s, n.id) for s in net.upstream(n.id) if net.node(s).kind is NodeKind.PV_STRING)
                remarks = f"{inputs} string inputs" if inputs else ""
            if n.spec is None:
                key = (category, None, n.title or n.kind.value.replace("_", " ").upper())
                remarks = "; ".join(r for r in (remarks, "no catalog item: specify before ordering") if r)
            else:
                spec = catalog.get(n.spec)
                utility = project.design_basis.grid.utility
                key = (category, spec.id, _describe(spec, n, utility))
                if note := _maker(spec, utility)[1]:
                    remarks = "; ".join(r for r in (remarks, note) if r)
            totals[key] = totals.get(key, 0) + count
            notes.setdefault(key, {})
            if remarks:
                notes[key][remarks] = None
            if n_strings:
                per = strings.setdefault(key, {})
                per[series] = per.get(series, 0) + n_strings

    def remark(key) -> str:
        made = [f"{k:,} strings of {m}" for m, k in sorted(strings.get(key, {}).items())]
        return "; ".join([*made, *notes[key]])

    return [BomLine(c, item, cid, float(q), "nos", remark((c, cid, item))) for (c, cid, item), q in totals.items()] + _fuse_box_lines(project)


def not_supplied(node: Node) -> bool:
    """Existing equipment or equipment by others (node attribute `existing` or `by_others` true) is shown on the drawings but not billed."""
    return any(node.attributes.get(k) is True for k in NOT_SUPPLIED)


def _fuse_box_lines(project: Project) -> list[BomLine]:
    """One string fuse box enclosure per inverter whose strings land on their own gPV fuses (a FUSE node fed by strings, without an SCB or DC fuse box node)."""
    net = project.network
    boxes: dict[tuple[int, int], int] = defaultdict(int)
    for n in net.by_kind(NodeKind.FUSE):
        fed = [e for e in net.edges if e.is_power and e.dst_node == n.id and net.node(e.src_node).kind is NodeKind.PV_STRING]
        if not fed or any(net.node(e.dst_node).kind in (NodeKind.SCB, NodeKind.DC_FUSE_BOX) for e in net.edges if e.is_power and e.src_node == n.id):
            continue
        poles = n.attributes.get(FUSED_POLES)
        boxes[(n.count, poles if isinstance(poles, int) else 1)] += net.instance_count(n.id) // max(1, n.count)
    return [
        BomLine("COMBINER BOXES", f"STRING FUSE BOX, {ways}-way gPV fuse holders{', + and - poles' if poles > 1 else ''}, outdoor enclosure", None, float(q), "nos",
                f"{ways * poles} fuse holders each; no catalog item: specify before ordering")
        for (ways, poles), q in sorted(boxes.items())
    ]  # fmt: skip


def _conductors(cable: CableSpec) -> int:
    return cable.cores + (1 if cable.reduced_neutral else 0)


def _cable_lines(schedule: Schedule, project: Project, catalog: Catalog, wastage_pct: float) -> tuple[list[BomLine], list[str]]:
    metres: dict[str, float] = defaultdict(float)
    rows: dict[str, int] = defaultdict(int)
    unmeasured: dict[str, int] = defaultdict(int)
    estimated: dict[str, int] = defaultdict(int)
    for r in schedule.rows:
        if _by_others(r):
            continue
        metres[r.cable_ref] += r.cable_metres
        rows[r.cable_ref] += 1
        unmeasured[r.cable_ref] += r.length_pos_m is None
        estimated[r.cable_ref] += r.length_source == "estimated"
    lines, warnings = [], []
    specs = _cable_specs(project, catalog, schedule)
    for ref, net_m in metres.items():
        spec = specs[ref]
        ordered = math.ceil(net_m * (1 + wastage_pct / 100))
        extra = f"; {estimated[ref]} of {rows[ref]} rows estimated from the layout" if estimated[ref] else ""
        short = f"; {unmeasured[ref]} of {rows[ref]} rows have no length and add nothing" if unmeasured[ref] else ""
        if unmeasured[ref]:
            warnings.append(f"{ref}: {unmeasured[ref]} of {rows[ref]} cable rows have no length, so the metres are short by their length")
        lines.append(
            BomLine(CABLES, f"{spec.manufacturer} {spec.model}, {spec.callout()}, {spec.voltage_grade}", spec.id, float(ordered), "m", f"{net_m:.1f} m routed + {wastage_pct:g} % wastage{extra}{short}")
        )
    return sorted(lines, key=lambda ln: ln.item), warnings


def _by_others(row) -> bool:
    return BY_OTHERS in (row.remarks or "").lower()


def _cable_specs(project: Project, catalog: Catalog, schedule: Schedule) -> dict[str, CableSpec]:
    inline = {e.cable.id: e.cable for e in project.network.edges if isinstance(e.cable, CableSpec)}
    out = {}
    for ref in dict.fromkeys(r.cable_ref for r in schedule.rows):
        spec = inline.get(ref) or catalog.get(ref)
        assert isinstance(spec, CableSpec)
        out[ref] = spec
    return out


def _accessory_lines(schedule: Schedule, project: Project, catalog: Catalog) -> list[BomLine]:
    mc4 = 0
    lugs: dict[tuple[str, float], int] = defaultdict(int)
    glands: dict[str, int] = defaultdict(int)
    kits: dict[str, int] = defaultdict(int)
    net = project.network
    specs = _cable_specs(project, catalog, schedule)
    for r in schedule.rows:
        if _by_others(r):
            continue
        spec = specs[r.cable_ref]
        cables = r.runs * r.cables_per_run
        string_cable = net.node(net.edge_by_id[r.edge].src_node).kind is NodeKind.PV_STRING
        ends = 1 if string_cable else 2
        if string_cable:
            mc4 += r.runs
        if r.service in (Service.MV_AC, Service.HV_AC):
            kits[spec.callout()] += ends * cables
            continue
        lugs[(r.conductor, r.size_mm2)] += ends * cables * (_conductors(spec) if spec.cores > 1 else 1)
        glands[spec.callout()] += ends * cables
    out = []
    if mc4:
        out.append(BomLine(ACCESSORIES, "MC4-type connector pair (male + female), one per string cable at the string end", None, float(mc4), "pair", "string-end terminations; the combiner end is counted as lugs"))
    for (conductor, size), n in sorted(lugs.items()):
        out.append(BomLine(ACCESSORIES, f"Cable lug, {'Cu' if conductor == 'CU' else 'Al'}, {size:g} sq.mm", None, float(n), "nos", "one per conductor end"))
    for callout, n in sorted(glands.items()):
        out.append(BomLine(ACCESSORIES, f"Cable gland for {callout}", None, float(n), "nos", "one per cable end"))
    for callout, n in sorted(kits.items()):
        out.append(BomLine(ACCESSORIES, f"MV cable termination kit for {callout}", None, float(n), "set", "one per cable end (3-pole set for a three-core cable)"))
    return out


def build_bom(project: Project, catalog: Catalog, schedule: Schedule, wastage_pct: float = DEFAULT_WASTAGE_PCT) -> Bom:
    if wastage_pct < 0:
        raise ValueError("wastage_pct cannot be negative")
    warnings: list[str] = []
    earth_lines, earth_assumptions, earth_warnings = earthing_lines(project)
    lines = _node_lines(project, catalog, warnings, frozenset({NodeKind.EARTH_ELECTRODE}) if earth_lines else frozenset())
    lines += earth_lines
    cable_lines, cable_warnings = _cable_lines(schedule, project, catalog, wastage_pct)
    lines += cable_lines + _accessory_lines(schedule, project, catalog)
    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    lines.sort(key=lambda ln: order[ln.category])
    assumptions = (
        f"Cable wastage of {wastage_pct:g} % is added to the routed metres and the result rounded up to whole metres: a procurement allowance, not a design value.",
        "Cable metres count every run and every loaded conductor: +ve and -ve for DC single-core, three phases for three-phase single-core, one cable per run for multicore. Neutral and earth conductors are not scheduled.",
        "Lugs are one per conductor end and glands one per cable end for DC and LV cables; MV cable ends take one termination kit each. String cables are terminated with an MC4-type connector at the string end.",
        "Existing supply, loads and generators (POI, load and DG nodes) and equipment marked existing or by others (the consumer LT panel or DB the plant connects to) are not part of the bill, nor are cables the schedule marks 'by others' (the DISCOM service connection, existing consumer wiring). "
        "Mounting structures, trays, conduits and ferrules are not scheduled.",
        "Items without a catalog id come from network nodes that name no equipment, or from the earthing and lightning designs; their description is the node title or the design's.",
        *earth_assumptions,
    )
    return Bom(project.id, project.name, tuple(lines), assumptions, tuple(dict.fromkeys(warnings + cable_warnings + list(earth_warnings))))

"""Text the SLD writes: symbol attribute values, cable callouts and the system parameters table.

Every value comes from the project or the catalog; when the model has no value the field stays empty.
"""

import re
from collections.abc import Callable, Mapping
from typing import Final

from unicad.catalog.store import Catalog
from unicad.domain.catalog import (
    BatterySpec,
    CableSpec,
    InverterSpec,
    MeterSpec,
    ModuleSpec,
    PCSSpec,
    ProtectionSpec,
    TransformerSpec,
)
from unicad.domain.network import SPEC_KINDS, Edge, ElectricalNetwork, Group, Node, NodeKind
from unicad.domain.project import DesignBasis, Project
from unicad.rules.ratio import plant_capacity
from unicad.rules.topology import AC_SERVICES, GENERATORS

_KIND_LABEL = {
    NodeKind.SCB: "STRING COMBINER BOX",
    NodeKind.DC_SPD: "DC SURGE PROTECTION DEVICE",
    NodeKind.AC_SPD: "AC SURGE PROTECTION DEVICE",
    NodeKind.CT: "CURRENT TRANSFORMER",
    NodeKind.PT: "POTENTIAL TRANSFORMER",
    NodeKind.ACDB: "AC DISTRIBUTION BOARD",
    NodeKind.LT_PANEL: "LT PANEL",
    NodeKind.HT_PANEL: "HT PANEL",
    NodeKind.POI_GRID: "GRID",
    NodeKind.VCB: "VACUUM CIRCUIT BREAKER",
    NodeKind.BREAKER: "CIRCUIT BREAKER",
    NodeKind.AUX_TRANSFORMER: "AUXILIARY TRANSFORMER",
}
_METER_LABEL = {"net": "NET METER", "generation": "GENERATION METER", "abt": "ABT METER", "check": "CHECK METER"}
_LAYING = {
    "air": "IN AIR",
    "tray": "ON TRAY",
    "conduit": "IN CONDUIT",
    "trench": "IN TRENCH",
    "buried_direct": "DIRECTLY BURIED",
    "buried_duct": "IN BURIED DUCT",
    "overhead": "OVERHEAD",
}


UNSPECIFIED = "RATING TO BE SPECIFIED"


def unspecified(node: Node) -> bool:
    """A node whose kind is chosen from the catalog but has no spec: its rating is an open item on the drawing."""
    if node.spec is not None or node.kind not in SPEC_KINDS:
        return False
    return not (node.kind in (NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER) and node.attributes)


def kind_label(kind: NodeKind) -> str:
    return _KIND_LABEL.get(kind, kind.value.replace("_", " ").upper())


def volts(v: float) -> str:
    return f"{v / 1000:g} kV" if v >= 1000 else f"{v:g} V"


def _kva(v: float) -> str:
    return f"{v / 1000:g} MVA" if v >= 1000 else f"{v:g} kVA"


def tag_range(tag: str, depth: int, count: int) -> str:
    """'INV-01..INV-05' for the instances under the first enclosing instance; the bare tag when only one."""
    first = tag.format(*([1] * depth))
    if count <= 1 or depth == 0:
        return first
    return f"{first}..{tag.format(*([1] * (depth - 1) + [count]))}"


def node_tag(net: ElectricalNetwork, node: Node) -> str:
    depth = len(net.instance_ids(node.id)[0].split("@", 1)[1].split(".")) if "@" in net.instance_ids(node.id)[0] else 0
    return tag_range(node.tag, depth, node.count)


def group_label(net: ElectricalNetwork, group: Group) -> str:
    depth = 0
    g: Group | None = group
    while g is not None:
        depth += g.count > 1
        g = net.group_by_id[g.within] if g.within else None
    return tag_range(group.tag, depth, group.count)


def _spec(node: Node, catalog: Catalog | None):
    return catalog.get(node.spec) if node.spec and catalog is not None else None


def _join(*parts: str | None) -> str:
    return ", ".join(p for p in parts if p)


SQRT = "\u221a"
_CORE = r"([0-9][0-9A-Za-z.]*)\s*\((\d+(?:\.\d+)?) VA[^)]*\)"
_CORES = re.compile(rf"{_CORE}(?:\s*\+\s*{_CORE})*")
_CORE_ITEM = re.compile(_CORE)
_ROOT3 = re.compile(r"(\d+(?:\.\d+)?)(/sqrt3)?")


def _switching_rating(spec: ProtectionSpec) -> str:
    parts = [f"{spec.rated_current:g} A" if spec.rated_current else None, f"{spec.poles}P"]
    if spec.breaking_capacity_ka:
        parts.append(f"{spec.breaking_capacity_ka:g} kA")
    if spec.spd_type:
        parts.append(f"TYPE {spec.spd_type}")
    dc = spec.device in ("spd_dc", "dc_isolator", "fuse_gpv")
    parts.append(f"{volts(spec.rated_voltage)}{' DC' if dc else ''}")
    return _join(*parts)


def _rmu_rating(spec: ProtectionSpec) -> str:
    withstand = spec.short_time_withstand_ka
    duration = f" {spec.short_time_duration_s:g} s" if spec.short_time_duration_s else ""
    return _join(
        volts(spec.rated_voltage),
        f"{spec.rated_current:g} A" if spec.rated_current else None,
        f"{spec.breaking_capacity_ka:g} kA BREAKING" if spec.breaking_capacity_ka and spec.breaking_capacity_ka != withstand else None,
        f"{withstand:g} kA{duration}" if withstand else None,
    )


def _classes_and_burden(spec: ProtectionSpec) -> tuple[str, str]:
    """('0.5S / 5P20', '15/30 VA') from a class string like '0.5S (15 VA, metering) + 5P20 (30 VA, protection)'."""
    acc = spec.accuracy_class or ""
    classes, burdens = acc, []
    if _CORES.fullmatch(acc):
        found = _CORE_ITEM.findall(acc)
        classes = " / ".join(c for c, _ in found)
        burdens = [b for _, b in found]
    burden = f"{spec.burden_va:g}" if spec.burden_va else "/".join(dict.fromkeys(burdens))
    return (f"CL {classes}" if classes else ""), (f"{burden} VA" if burden else "")


def _root3_voltage(term: str, unit_kv: bool) -> str:
    m = _ROOT3.fullmatch(term.strip())
    if m is None:
        return term.strip().replace("sqrt3", SQRT)
    v = float(m.group(1))
    return f"{volts(v) if unit_kv else f'{v:g} V'}{'/' + SQRT + '3' if m.group(2) else ''}"


def _pt_ratio(ratio: str) -> str:
    """'33 kV/\u221a3 / 110 V/\u221a3 - 110 V/\u221a3' from '33000/sqrt3 : 110/sqrt3 - 110/sqrt3 V'."""
    primary, sep, secondary = ratio.partition(":")
    if not sep:
        return ratio.replace("sqrt3", SQRT)
    sec = secondary.strip().removesuffix("V").strip()
    return f"{_root3_voltage(primary, True)} / {' - '.join(_root3_voltage(t, False) for t in sec.split(' - '))}"


def _instrument_rating(spec: ProtectionSpec) -> str:
    classes, burden = _classes_and_burden(spec)
    ratio = _pt_ratio(spec.ratio) if spec.device == "pt" and spec.ratio else spec.ratio
    return _join(ratio, classes, burden) or volts(spec.rated_voltage)


def _arrester_rating(spec: ProtectionSpec) -> str:
    return _join(
        volts(spec.rated_voltage),
        f"{spec.nominal_discharge_current_ka:g} kA" if spec.nominal_discharge_current_ka else None,
        "WITH SURGE COUNTER" if spec.surge_counter else None,
    )


ProtectionText = tuple[str, Callable[[ProtectionSpec], str]]
TYPE_LABELLED: Final = frozenset({"mccb", "acb"})
"""Devices whose symbol carries its type label (MCCB, ACB): the description beside it names the make and model instead of repeating the type."""
PROTECTION_TEXT: Final[dict[str, ProtectionText]] = {
    "mcb": ("MCB", _switching_rating),
    "mccb": ("MCCB", _switching_rating),
    "acb": ("ACB", _switching_rating),
    "vcb": ("VCB", _switching_rating),
    "rccb": ("RCCB", _switching_rating),
    "fuse_gpv": ("gPV FUSE", _switching_rating),
    "fuse_hrc": ("HRC FUSE", _switching_rating),
    "dc_isolator": ("DC ISOLATOR", _switching_rating),
    "ac_isolator": ("AC ISOLATOR", _switching_rating),
    "spd_dc": ("DC SPD", _switching_rating),
    "spd_ac": ("AC SPD", _switching_rating),
    "sfu": ("SWITCH FUSE UNIT", _switching_rating),
    "rmu": ("RING MAIN UNIT", _rmu_rating),
    "ct": ("CURRENT TRANSFORMER", _instrument_rating),
    "pt": ("POTENTIAL TRANSFORMER", _instrument_rating),
    "lightning_arrester": ("LIGHTNING ARRESTER", _arrester_rating),
}


def _tap_text(spec: TransformerSpec) -> str | None:
    if spec.tap_changer is None:
        return None
    name = spec.tap_changer.upper()
    if spec.tap_range_pct is None or spec.tap_step_pct is None:
        return name
    lo, hi = spec.tap_range_pct
    span = f"+/-{hi:g} %" if lo == -hi else f"{lo:+g} TO {hi:+g} %"
    return f"{name} {span} IN {spec.tap_step_pct:g} % STEPS"


def _impedance_text(spec: TransformerSpec) -> str:
    base = spec.impedance_base_kva
    return f"Z = {spec.impedance_pct:g} %{f' ON {_kva(base)}' if base and base != spec.rated_kva else ''}"


ANSI_CIRCLES = 6
ANSI_CIRCLE_CHARS = 4
_ANSI_SPLIT = re.compile(r"[,;\s]+")


def ansi_functions(node: Node) -> tuple[str, ...]:
    """ANSI device numbers from node.attributes['ansi'], one per circle: '50/51, 50N/51N' -> 50 51 50N 51N."""
    raw = node.attributes.get("ansi")
    items = raw if isinstance(raw, list | tuple) else _ANSI_SPLIT.split(str(raw)) if raw else []
    return tuple(f for item in items for f in re.split(r"/(?=\d)", str(item).strip().upper()) if f)


def _ansi_texts(node: Node) -> dict[str, str]:
    functions = ansi_functions(node)
    fits = [f for f in functions if len(f) <= ANSI_CIRCLE_CHARS][:ANSI_CIRCLES]
    out = {"RATING": ", ".join(f for f in functions if f not in fits)}
    if len(functions) == 1 and fits:
        return out | {"ANSI": fits[0]}
    return out | {f"ANSI{i + 1}": f for i, f in enumerate(fits)}


def node_texts(net: ElectricalNetwork, node: Node, catalog: Catalog | None) -> dict[str, str]:
    """TAG / RATING / DESC / VECTOR / COUNT values for the node's symbol."""
    spec = _spec(node, catalog)
    rating = ""
    desc = node.title or kind_label(node.kind)
    vector = ""
    match spec:
        case ModuleSpec():
            n = node.modules_in_series
            rating = f"{n} x {spec.pmax:g} Wp = {n * spec.pmax / 1000:g} kWp" if n else f"{spec.pmax:g} Wp"
            desc = node.title or f"PV STRING, {spec.tech.value.upper().replace('_', ' ')}{' BIFACIAL' if spec.bifacial else ''}"
        case InverterSpec():
            rating = _join(_kva(spec.ac_rated_kva), f"{volts(spec.ac_voltage)} AC", f"{spec.phases} PH")
            desc = node.title or f"{spec.manufacturer} {spec.model}".upper()
        case PCSSpec():
            rating = _join(_kva(spec.rated_kva), f"{volts(spec.ac_voltage)} AC")
            desc = node.title or f"{spec.manufacturer} {spec.model}".upper()
        case BatterySpec():
            rating = _join(f"{spec.energy_kwh:g} kWh", f"{volts(spec.nominal_voltage)} DC")
            desc = node.title or f"{spec.chemistry.upper()} BATTERY"
        case TransformerSpec():
            rating = f"{_kva(spec.rated_kva)}, {spec.hv_kv:g}/{'-'.join(f'{v:g}' for v in spec.lv_kv)} kV"
            vector = spec.vector_group
            desc = node.title or _join(spec.cooling if "cooling" in spec.model_fields_set else None, _impedance_text(spec), _tap_text(spec))
        case ProtectionSpec():
            label, rating_of = PROTECTION_TEXT[spec.device]
            rating = rating_of(spec)
            desc = node.title or (f"{spec.manufacturer} {spec.model}".upper() if spec.device in TYPE_LABELLED else label)
        case MeterSpec():
            rating = f"CL {spec.accuracy_class}, {spec.connection.upper().replace('_', '/')} OPERATED"
            desc = node.title or _METER_LABEL[spec.function]
        case _:
            a = node.attributes
            if node.kind in (NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER) and a:
                kva = a.get("rated_kva")
                ratio = f"{a['hv_kv']:g}/{a['lv_kv']} kV" if "hv_kv" in a and "lv_kv" in a else None
                rating = _join(_kva(float(kva)) if isinstance(kva, int | float) else None, ratio)
                vector = str(a.get("vector_group", ""))
            elif node.voltage_v:
                rating = volts(node.voltage_v)
            if unspecified(node):
                rating = _join(rating or None, UNSPECIFIED)
    out = {"TAG": node_tag(net, node), "RATING": rating, "DESC": desc, "VECTOR": vector}
    if node.kind is NodeKind.RELAY:
        ansi = _ansi_texts(node)
        out |= ansi | {"RATING": _join(rating, ansi["RATING"])}
    if node.count > 1:
        out["COUNT"] = f"x {node.count} NOS"
    return out


def symbol_values(texts: Mapping[str, str], attr_tags: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Every attribute gets a value, empty when unknown, so no symbol default leaks onto the drawing."""
    values = {t: texts.get(t, "") for t in attr_tags}
    if "COUNT" not in attr_tags and texts.get("COUNT"):
        values["TAG"] = f"{values.get('TAG', '')} ({texts['COUNT']})".strip()
    return tuple((t, values[t]) for t in attr_tags if t in values)


_BUS_CURRENT = ("rated_current_a", "rating_a", "rated_current")
_BUS_FAULT_KA = ("short_circuit_ka", "short_time_ka", "fault_ka")
_BUS_FAULT_S = ("short_time_s", "fault_duration_s")
_BUS_MATERIAL = ("material", "conductor")


def _attr(node: Node, keys: tuple[str, ...]):
    return next((node.attributes[k] for k in keys if k in node.attributes), None)


def bus_rating(node: Node) -> str:
    """'33 kV, 1250 A, 25 kA 1 s, AL BUSBAR' from the node's voltage and rating attributes; '' when it has none."""
    amps, ka, secs, mat = (_attr(node, k) for k in (_BUS_CURRENT, _BUS_FAULT_KA, _BUS_FAULT_S, _BUS_MATERIAL))
    if amps is None and ka is None and mat is None:
        return ""
    fault = f"{ka:g} kA{f' {secs:g} s' if isinstance(secs, int | float) else ''}" if isinstance(ka, int | float) else None
    return _join(
        volts(node.voltage_v) if node.voltage_v else None,
        f"{amps:g} A" if isinstance(amps, int | float) else None,
        fault,
        f"{str(mat).upper()} BUSBAR" if mat else "BUSBAR",
    )


def bus_texts(net: ElectricalNetwork, node: Node) -> tuple[str, ...]:
    head = f"{node_tag(net, node)}  {prose(node.title) if node.title else kind_label(node.kind)}"
    rating = bus_rating(node)
    if rating:
        return (head, rating)
    return (head, f"{volts(node.voltage_v)} BUS") if node.voltage_v else (head,)


REMARK_INLINE_CHARS = 42
GENERAL_NOTES = (
    "REPEATED GROUPS ARE DRAWN ONCE IN A DASHED FRAME; (x N) IS THE NUMBER OF IDENTICAL INSTANCES.",
    "CABLE CALLOUTS: RUNS x CORES x SIZE, CONDUCTOR, INSULATION, ARMOUR, VOLTAGE GRADE, LAYING, LENGTH.",
)
"""The sheet's first notes; cable remarks too long for a callout follow them, then the design-basis assumptions."""


def remark(edge: Edge) -> str:
    return prose(edge.remarks) if edge.remarks else ""


def is_long_remark(text: str) -> bool:
    return len(text) > REMARK_INLINE_CHARS


def ac_phases(net: ElectricalNetwork, catalog: Catalog | None, edge: Edge) -> int:
    """Phase conductors of an AC circuit: 1 when only single-phase generators feed it (as the cable rules and the bill count), else 3."""
    src = edge.src_node if edge.src_node in net.node_by_id else edge.dst_node
    if src not in net.node_by_id:
        return 3
    gens = [net.node(g) for g in (src, *net.upstream(src)) if net.node(g).kind in GENERATORS and net.node(g).spec]
    ph = {getattr(catalog.get(g.spec), "phases", 3) for g in gens} if catalog is not None else set()
    return 1 if ph == {1} else 3


def band_cables(edge: Edge, catalog: Catalog | None) -> list[tuple[CableSpec, int]]:
    """The edge's own cable and runs, then those of each length band, distinct; empty when a cable is not in the catalog."""
    refs = [(edge.cable, edge.runs), *((b.cable, b.runs) for b in edge.bands)]
    specs = [(catalog.get(c) if isinstance(c, str) and catalog is not None else c, r) for c, r in refs]
    if not all(isinstance(c, CableSpec) for c, _ in specs):
        return []
    return list(dict.fromkeys(specs))


def banded_text(cables: list[tuple[CableSpec, int]], per_run: str) -> str:
    """'1R x 1C x 4 / 6 sq.mm Cu XLPE': the sizes of the length bands of one edge, grouped by runs when the rest of the callout agrees."""
    first = cables[0][0]
    if len({c.callout().replace(f"x {c.size_mm2:g} sq.mm", "") for c, _ in cables}) > 1:
        return " / ".join(f"{r}R x {per_run}{c.callout()}" for c, r in cables)
    parts = []
    for runs in sorted({r for _, r in cables}):
        sizes = " / ".join(f"{z:g}" for z in sorted({c.size_mm2 for c, r in cables if r == runs}))
        parts.append(f"{runs}R x {per_run}{first.core_label}C x {sizes} sq.mm")
    return " OR ".join(parts) + first.callout().split(" sq.mm", 1)[1]


def cable_callout(edge: Edge, catalog: Catalog | None, note_of: Mapping[str, int] | None = None, net: ElectricalNetwork | None = None) -> tuple[str, ...]:
    """Cable callout lines; a remark too long to sit beside the wire is referred to its note number in `note_of`. A single-core AC run
    names its cables per run ('1R x 3 x 1C x 400 sq.mm'), as the bill counts them."""
    cable = edge.cable
    if isinstance(cable, str) and catalog is not None:
        cable = catalog.get(cable)
    lines: list[str] = []
    tail: list[str] = []
    if isinstance(cable, CableSpec):
        per_run = ""
        if cable.cores == 1 and edge.service in AC_SERVICES and net is not None:
            per_run = f"{2 if ac_phases(net, catalog, edge) == 1 else 3} x "
        banded = band_cables(edge, catalog)
        lines.append(banded_text(banded, per_run) if len(banded) > 1 else f"{edge.runs}R x {per_run}{cable.callout()}")
        tail.append(cable.voltage_grade)
        if edge.laying:
            tail.append(_LAYING[edge.laying.value])
        if len(banded) > 1:
            if "CABLE SCHEDULE" not in remark(edge).upper():
                tail.append("SEE CABLE SCHEDULE")
        elif edge.length_m:
            tail.append(f"L = {edge.length_m:g} m")
    elif edge.runs > 1:
        lines.append(f"{edge.runs}R")
    text = remark(edge)
    if text and note_of and text in note_of:
        tail.append(f"SEE NOTE {note_of[text]}")
    elif text:
        if tail:
            lines.append(", ".join(tail))
            tail = []
        lines.append(text)
    if tail:
        lines.append(", ".join(tail))
    return tuple(lines)


_DEG = "\x00"
_BASIS_TERMS: dict[str, tuple[str, Callable[[DesignBasis], str]]] = {
    "ambient_min_c": ("minimum ambient temperature", lambda b: f"{b.ambient_min_c:g} {_DEG}C"),
    "ambient_max_c": ("maximum ambient temperature", lambda b: f"{b.ambient_max_c:g} {_DEG}C"),
    "altitude_m": ("site altitude", lambda b: f"{b.altitude_m:g} m"),
    "vdrop_dc_max": ("DC voltage drop limit", lambda b: f"{b.vdrop_dc_max * 100:g} %"),
    "vdrop_ac_max": ("AC voltage drop limit", lambda b: f"{b.vdrop_ac_max * 100:g} %"),
    "power_factor": ("power factor", lambda b: f"{b.power_factor:g}"),
    "dc_ac_ratio_target": ("DC/AC ratio target", lambda b: f"{b.dc_ac_ratio_target:g}"),
}
_IDENT = re.compile(r"\b(" + "|".join(_BASIS_TERMS) + r")\b")


def prose(text: str, basis: DesignBasis | None = None) -> str:
    """Drawing text in engineer wording: design-basis field names become their name and value,
    other identifiers lose their underscores, and the result is upper case."""
    if basis is not None:

        def term(m: re.Match[str]) -> str:
            name, value = _BASIS_TERMS[m.group(1)]
            return f"{name} {value(basis)}"

        text = _IDENT.sub(term, text)
    text = _UNIT.sub(lambda m: m.group(1) + _UNITS[m.group(2)], text.replace("_", " ").upper())
    return re.sub(r"(\d) C\b", rf"\1 {_DEG}C", text).replace(_DEG, "%%d")


_UNITS = {u.upper(): u for u in ("kV", "kVA", "MVA", "kW", "MW", "kWp", "MWp", "MWac", "MWdc", "kWh", "MWh", "Wp", "Hz", "mm", "sq.mm", "kA")}
_UNIT = re.compile(r"(\d\s?)(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True)) + r")\b")


def assumption_notes(project: Project) -> tuple[str, ...]:
    basis = project.design_basis
    return tuple(f"ASSUMPTION: {prose(a, basis)}" for a in basis.assumptions)


def _fmt_power(kw: float, unit: str) -> str:
    return f"{kw / 1000:,.3f} M{unit}" if kw >= 1000 else f"{kw:,.2f} k{unit}"


def system_parameters(project: Project, catalog: Catalog | None) -> tuple[tuple[str, str], ...]:
    net = project.network
    rows: list[tuple[str, str]] = []
    dc_kwp = 0.0
    modules: dict[str, int] = {}
    strings = 0
    for n in net.by_kind(NodeKind.PV_STRING):
        spec = _spec(n, catalog)
        k = net.instance_count(n.id)
        strings += k
        if isinstance(spec, ModuleSpec) and n.modules_in_series:
            dc_kwp += k * n.modules_in_series * spec.pmax / 1000
            label = f"{spec.pmax:g} Wp"
            modules[label] = modules.get(label, 0) + k * n.modules_in_series
    inverters: dict[str, int] = {}
    pcs: dict[str, int] = {}
    for n in net.by_kind(NodeKind.INVERTER, NodeKind.PCS):
        spec = _spec(n, catalog)
        if isinstance(spec, InverterSpec | PCSSpec):
            kva = spec.ac_rated_kva if isinstance(spec, InverterSpec) else spec.rated_kva
            into = inverters if isinstance(spec, InverterSpec) else pcs
            label = prose(f"{_kva(kva)} ({spec.manufacturer} {spec.model})")
            into[label] = into.get(label, 0) + net.instance_count(n.id)
    if dc_kwp:
        rows.append(("DC CAPACITY", _fmt_power(dc_kwp, "Wp")))
    if catalog is not None:
        ratio_dc, ac_kva = plant_capacity(project, catalog)
        if ac_kva:
            rows.append(("AC CAPACITY (INVERTER RATED)", _fmt_power(ac_kva, "VA")))
        if ratio_dc and ac_kva:
            rows.append(("DC / AC RATIO", f"{ratio_dc / ac_kva:.2f}"))
    rows += [("PV MODULES", f"{c:,} x {label}") for label, c in modules.items()]
    if strings:
        series = sorted({n.modules_in_series for n in net.by_kind(NodeKind.PV_STRING) if n.modules_in_series})
        rows.append(("PV STRINGS", f"{strings:,} x {'/'.join(map(str, series))} MODULES"))
    rows += [("INVERTERS", f"{c} x {label}") for label, c in inverters.items()]
    rows += [("BESS PCS", f"{c} x {label}") for label, c in pcs.items()]
    g = project.design_basis.grid
    rows.append(("GRID CONNECTION", f"{volts(g.voltage_v)}, {g.phases} PH, {g.frequency_hz:g} Hz{', ' + g.utility if g.utility else ''}"))
    return tuple(rows)

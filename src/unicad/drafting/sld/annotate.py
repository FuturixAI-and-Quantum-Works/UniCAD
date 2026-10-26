"""Text the SLD writes: symbol attribute values, cable callouts and the system parameters table.

Every value comes from the project or the catalog; when the model has no value the field stays empty.
"""

import re
from collections.abc import Callable, Mapping

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
from unicad.domain.network import Edge, ElectricalNetwork, Group, Node, NodeKind
from unicad.domain.project import DesignBasis, Project

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
    NodeKind.AUX_TRANSFORMER: "AUXILIARY TRANSFORMER",
}
_DEVICE_LABEL = {
    "mcb": "MCB",
    "mccb": "MCCB",
    "acb": "ACB",
    "vcb": "VCB",
    "rccb": "RCCB",
    "fuse_gpv": "gPV FUSE",
    "fuse_hrc": "HRC FUSE",
    "dc_isolator": "DC ISOLATOR",
    "ac_isolator": "AC ISOLATOR",
    "spd_dc": "DC SPD",
    "spd_ac": "AC SPD",
    "sfu": "SWITCH FUSE UNIT",
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
            desc = node.title or f"{spec.cooling}, Z = {spec.impedance_pct:g} %"
        case ProtectionSpec():
            parts = [f"{spec.rated_current:g} A" if spec.rated_current else None, f"{spec.poles}P"]
            if spec.breaking_capacity_ka:
                parts.append(f"{spec.breaking_capacity_ka:g} kA")
            if spec.spd_type:
                parts.append(f"TYPE {spec.spd_type}")
            dc = spec.device in ("spd_dc", "dc_isolator", "fuse_gpv")
            parts.append(f"{volts(spec.rated_voltage)}{' DC' if dc else ''}")
            rating = _join(*parts)
            desc = node.title or _DEVICE_LABEL[spec.device]
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
    out = {"TAG": node_tag(net, node), "RATING": rating, "DESC": desc, "VECTOR": vector}
    if node.count > 1:
        out["COUNT"] = f"x {node.count} NOS"
    return out


def symbol_values(texts: Mapping[str, str], attr_tags: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Every attribute gets a value, empty when unknown, so no symbol default leaks onto the drawing."""
    values = {t: texts.get(t, "") for t in attr_tags}
    if "COUNT" not in attr_tags and texts.get("COUNT"):
        values["TAG"] = f"{values.get('TAG', '')} ({texts['COUNT']})".strip()
    return tuple((t, values[t]) for t in attr_tags if t in values)


def bus_texts(net: ElectricalNetwork, node: Node) -> tuple[str, ...]:
    head = f"{node_tag(net, node)}  {node.title or kind_label(node.kind)}"
    return (head, f"{volts(node.voltage_v)} BUS") if node.voltage_v else (head,)


REMARK_INLINE_CHARS = 42


def remark(edge: Edge) -> str:
    return prose(edge.remarks) if edge.remarks else ""


def is_long_remark(text: str) -> bool:
    return len(text) > REMARK_INLINE_CHARS


def cable_callout(edge: Edge, catalog: Catalog | None, note_of: Mapping[str, int] | None = None) -> tuple[str, ...]:
    """Cable callout lines; a remark too long to sit beside the wire is referred to its note number in `note_of`."""
    cable = edge.cable
    if isinstance(cable, str) and catalog is not None:
        cable = catalog.get(cable)
    lines: list[str] = []
    tail: list[str] = []
    if isinstance(cable, CableSpec):
        lines.append(f"{edge.runs}R x {cable.callout()}")
        tail.append(cable.voltage_grade)
        if edge.laying:
            tail.append(_LAYING[edge.laying.value])
        if edge.length_m:
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
    ac_kva = 0.0
    inverters: dict[str, int] = {}
    for n in net.by_kind(NodeKind.INVERTER, NodeKind.PCS):
        spec = _spec(n, catalog)
        k = net.instance_count(n.id)
        if isinstance(spec, InverterSpec | PCSSpec):
            kva = spec.ac_rated_kva if isinstance(spec, InverterSpec) else spec.rated_kva
            ac_kva += k * kva
            label = prose(f"{_kva(kva)} ({spec.manufacturer} {spec.model})")
            inverters[label] = inverters.get(label, 0) + k
    if dc_kwp:
        rows.append(("DC CAPACITY", _fmt_power(dc_kwp, "Wp")))
    if ac_kva:
        rows.append(("AC CAPACITY (INVERTER RATED)", _fmt_power(ac_kva, "VA")))
    if dc_kwp and ac_kva:
        rows.append(("DC / AC RATIO", f"{dc_kwp / ac_kva:.2f}"))
    rows += [("PV MODULES", f"{c:,} x {label}") for label, c in modules.items()]
    if strings:
        series = sorted({n.modules_in_series for n in net.by_kind(NodeKind.PV_STRING) if n.modules_in_series})
        rows.append(("PV STRINGS", f"{strings:,} x {'/'.join(map(str, series))} MODULES"))
    rows += [("INVERTERS", f"{c} x {label}") for label, c in inverters.items()]
    g = project.design_basis.grid
    rows.append(("GRID CONNECTION", f"{volts(g.voltage_v)}, {g.phases} PH, {g.frequency_hz:g} Hz{', ' + g.utility if g.utility else ''}"))
    return tuple(rows)

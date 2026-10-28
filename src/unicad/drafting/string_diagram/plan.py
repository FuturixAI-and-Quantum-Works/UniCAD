"""What a string diagram shows, as plain data: the inverter or SCB types of a project, the strings of a typical instance in series
order with their cables, fuses, MPPT inputs and table positions, and every instance a type stands for."""

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Literal

from unicad.catalog.store import Catalog
from unicad.domain.catalog import CableSpec, InverterSpec, ModuleSpec, ProtectionSpec
from unicad.domain.layout import ArrayLayout, Table
from unicad.domain.network import Edge, ElectricalNetwork, Node, NodeKind, Service
from unicad.domain.project import Project
from unicad.rules.cables import cable_of
from unicad.rules.pv import cell_temp_min, string_voltage
from unicad.rules.thresholds import ISC_FACTOR
from unicad.rules.topology import EVEN_MPPT, instance_tag, mppt_of, mppt_split, string_feeds
from unicad.schedules import LengthMode, build_schedule
from unicad.schedules.ids import Ids, indices

SinkKind = Literal["inverter", "scb"]
PASS_THROUGH = frozenset({NodeKind.DC_FUSE_BOX, NodeKind.DC_ISOLATOR, NodeKind.FUSE})
FUSE_ASSUMPTION = (
    "String fuse ratings are proposed, not modelled: the smallest catalogued gPV fuse rated at or above 1.25 x module Isc and the string Voc at the minimum temperature, "
    "and not above the module maximum series fuse."
)


@dataclass(frozen=True)
class Fuse:
    rating_a: float | None
    spec_id: str | None
    label: str
    modelled: bool
    poles: int = 1
    """Poles of each string fused (2: the + and the - conductor), as the network records it."""


@dataclass(frozen=True)
class MpptPlan:
    mppt_count: int
    inputs_per_mppt: int
    inputs: int
    used: int
    overflow: bool


@dataclass(frozen=True)
class StringLine:
    instance: str
    tag: str
    number: int
    ip: str
    cable_id: str
    modules: int
    mppt: int | None
    voc_cold_v: float
    isc_a: float
    length_m: float | None
    slots: tuple[tuple[str, int, int], ...]
    length_text: str = ""
    """The routed length the cable schedule gives this string ('62.4 m'), or the min..max over every instance the diagram stands for."""
    callout: str = ""
    """The cable the schedule gives this string ('1C x 4 sq.mm Cu (+) / (-)'), or every band size over the instances the diagram stands for."""

    @property
    def positions(self) -> str:
        """Table positions as 'T03 R1 C1-18', one entry per run of consecutive slots in a row."""
        out: list[str] = []
        run: list[tuple[str, int, int]] = []
        for s in self.slots:
            if run and run[-1][:2] == s[:2] and abs(s[2] - run[-1][2]) == 1:
                run.append(s)
            else:
                if run:
                    out.append(_run_text(run))
                run = [s]
        if run:
            out.append(_run_text(run))
        return ", ".join(out)


def _run_text(run: list[tuple[str, int, int]]) -> str:
    cols = [c + 1 for _, _, c in run]
    span = f"C{min(cols)}" if len(cols) == 1 else f"C{min(cols)}-{max(cols)}"
    return f"{run[0][0]} R{run[0][1] + 1} {span}"


@dataclass(frozen=True)
class Member:
    instance: str
    tag: str
    strings: tuple[str, ...]
    tables: tuple[str, ...]
    destination: str


@dataclass(frozen=True)
class Output:
    cable_id: str
    callout: str
    runs: int
    length_m: float | None
    to_tag: str
    to_input: str
    length_text: str = ""
    cable: CableSpec | None = None


@dataclass(frozen=True)
class DiagramType:
    kind: SinkKind
    sink: str
    members: tuple[Member, ...]
    lines: tuple[StringLine, ...]
    module: ModuleSpec
    inverter: InverterSpec
    inverter_tags: str
    string_cable: CableSpec | None
    string_callout: str
    devices: tuple[str, ...]
    envelope: Literal["none", "dcjb", "scb"]
    envelope_title: str
    fuse: Fuse | None
    mppt: MpptPlan | None
    output: Output | None
    table_defs: dict[str, Table]
    table_marks: dict[str, str]
    tmin_c: float
    voc_limit_v: float
    notes: tuple[str, ...]
    has_layout: bool
    string_runs: int = 1

    @property
    def representative(self) -> Member:
        return self.members[0]

    @property
    def tag_range(self) -> str:
        first, last = self.members[0].tag, self.members[-1].tag
        return first if len(self.members) == 1 else f"{first}..{last}"

    @property
    def typical(self) -> str:
        if len(self.members) == 1:
            return self.members[0].tag
        return f"{self.tag_range} ({len(self.members)} NOS)"

    @property
    def string_instances(self) -> tuple[str, ...]:
        return tuple(s for m in self.members for s in m.strings)

    @property
    def label(self) -> str:
        return "STRING COMBINER BOX" if self.kind == "scb" else "INVERTER"


def pick_fuse(catalog: Catalog, module: ModuleSpec, voc_cold_v: float) -> Fuse:
    need = ISC_FACTOR.value * module.isc
    ok = [
        p for p in catalog.filter("protection")
        if isinstance(p, ProtectionSpec) and p.device == "fuse_gpv" and p.rated_current is not None and p.rated_current >= need
        and p.rated_voltage >= voc_cold_v and (module.max_series_fuse is None or p.rated_current <= module.max_series_fuse)
    ]  # fmt: skip
    best = min(ok, key=lambda p: (p.rated_current, p.rated_voltage, p.id), default=None)
    if best is None:
        return Fuse(None, None, "FUSE TBC", False)
    return Fuse(best.rated_current, best.id, f"{best.rated_current:g} A gPV", False)


def cable_text(cable: CableSpec | None, runs: int = 1) -> str:
    if cable is None:
        return "CABLE NOT SPECIFIED"
    base = f"{cable.core_label}C x {cable.size_mm2:g} sq.mm {cable.conductor.value.capitalize()} (+) / (-)"
    return base if runs == 1 else f"{runs} RUNS x {base}"


def _upstream_strings(net: ElectricalNetwork, feeders: dict[str, list[str]], sink: str) -> list[str]:
    order = {n.id: k for k, n in enumerate(net.nodes)}
    out: list[str] = []
    seen: set[str] = set()
    stack = list(reversed(feeders[sink]))
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        kind = net.node(n.partition("@")[0]).kind
        if kind is NodeKind.PV_STRING:
            out.append(n)
        elif kind in PASS_THROUGH:
            stack.extend(reversed(feeders[n]))
    return sorted(out, key=lambda i: (order[i.partition("@")[0]], indices(i)))


KIND_TITLE = {NodeKind.SCB: "STRING COMBINER BOX", NodeKind.DC_FUSE_BOX: "DC FUSE BOX", NodeKind.DC_ISOLATOR: "DC ISOLATOR", NodeKind.FUSE: "STRING FUSE"}


def _short(node: Node) -> str:
    return (node.title or KIND_TITLE.get(node.kind) or node.kind.value.replace("_", " ")).upper()


def _chain(net: ElectricalNetwork, successors: dict[str, list[str]], start: str, templates: frozenset[str]) -> dict[str, str]:
    """Template id to instance id along the way down from `start` through the nodes of `templates`."""
    out: dict[str, str] = {}
    cur = start
    while True:
        nxt = next((n for n in successors[cur] if n.partition("@")[0] in templates and n.partition("@")[0] not in out), None)
        if nxt is None:
            return out
        out[nxt.partition("@")[0]] = nxt
        cur = nxt


def _devices(net: ElectricalNetwork, catalog: Catalog, path: tuple[str, ...], chain: dict[str, str], skip: set[str]) -> tuple[str, ...]:
    out = []
    for nid in path:
        if nid in skip or nid not in chain:
            continue
        node = net.node(nid)
        spec = catalog.get(node.spec) if node.spec else None
        extra = ""
        if isinstance(spec, ProtectionSpec):
            amps = f" {spec.rated_current:g} A" if spec.rated_current else ""
            extra = f"{amps} {spec.rated_voltage:g} V"
        out.append(f"{instance_tag(net, chain[nid])}: {_short(node)}{extra}")
    return tuple(out)


def _tag_range(net: ElectricalNetwork, template: str) -> str:
    ids = net.instance_ids(template)
    tags = [instance_tag(net, i) for i in ids]
    return tags[0] if len(tags) == 1 else f"{tags[0]}..{tags[-1]}"


def plan_types(project: Project, catalog: Catalog) -> tuple[list[DiagramType], list[str]]:
    """One DiagramType per string-inverter or SCB template that strings feed, and warnings for strings that cannot be drawn."""
    net = project.network
    expanded = net.expand()
    ids = Ids(project, expanded)
    schedule_rows = build_schedule(project, catalog, LengthMode.AUTO).rows
    string_cable_ids = {r.src: r.cable_id for r in schedule_rows if r.is_string}
    output_cable_ids = {r.src: r.cable_id for r in schedule_rows if not r.is_string and r.service is Service.DC}
    routed = {r.src: r.run_length_m for r in schedule_rows if r.service is Service.DC}
    edges = {e.id: e for e in net.edges}
    cables = {r.src: _row_cable(catalog, edges[r.edge], r.size_mm2, r.runs) for r in schedule_rows if r.service is Service.DC}
    feeders: dict[str, list[str]] = defaultdict(list)
    successors: dict[str, list[str]] = defaultdict(list)
    for e in expanded.edges:
        if e.is_power:
            feeders[e.dst_node].append(e.src_node)
            successors[e.src_node].append(e.dst_node)
    layout: ArrayLayout | None = project.array_layout
    slots_of = {a.string: a.slots() for a in layout.strings} if layout else {}
    basis = project.design_basis
    cold = cell_temp_min(basis)

    groups: dict[tuple[SinkKind, str], list] = {}
    for f in string_feeds(project, catalog):
        key = ("scb", f.combiner.id) if f.combiner is not None else ("inverter", f.inverter.id)
        groups.setdefault(key, []).append(f)

    types: list[DiagramType] = []
    covered: set[str] = set()
    for (kind, sink), feeds in sorted(groups.items(), key=lambda kv: [n.id for n in net.nodes].index(kv[0][1])):
        first = feeds[0]
        member_ids = net.instance_ids(sink)
        members: list[Member] = []
        for mid in member_ids:
            strings = _upstream_strings(net, feeders, mid)
            tables = tuple(dict.fromkeys(t for s in strings for t, _, _ in slots_of.get(s, ())))
            nxt = next(iter(successors[mid]), None)
            dest = ""
            if kind == "scb" and nxt is not None:
                inv_feeders = sorted((x for x in feeders[nxt] if x.partition("@")[0] == sink), key=indices)
                dest = f"{instance_tag(net, nxt)} IP{inv_feeders.index(mid) + 1:02d}" if mid in inv_feeders else instance_tag(net, nxt)
            members.append(Member(mid, instance_tag(net, mid), tuple(strings), tables, dest))
            covered.update(strings)
        rep = members[0]
        module = first.module
        n_series = first.n_series
        voc = string_voltage(module, n_series, "voc", cold).value
        inv_spec = first.spec
        string_node = first.string
        edge = next((e for e in net.edges if e.src_node == string_node.id and e.service is Service.DC and e.cable is not None), None)
        cable = cable_of(catalog, edge) if edge else None
        n_inputs = len(rep.strings)
        mppt = None
        if kind == "inverter":
            split = mppt_split(n_inputs, inv_spec.mppt_count)
            ipm, used = max(split, default=1), len(split)
            overflow = used > inv_spec.mppt_count or (inv_spec.inputs_per_mppt is not None and ipm > inv_spec.inputs_per_mppt)
            mppt = MpptPlan(inv_spec.mppt_count, ipm, n_inputs, used, overflow)
        lines: list[StringLine] = []
        for i, s in enumerate(rep.strings, start=1):
            node = net.node(s.partition("@")[0])
            sid = ids.string_id(s)
            lines.append(StringLine(
                instance=s, tag=instance_tag(net, s), number=i, ip=f"IP{sid.input_no:02d}",
                cable_id=string_cable_ids.get(s, sid.text()), modules=node.modules_in_series or n_series,
                mppt=mppt_of(i, split) if mppt else None, voc_cold_v=round(voc, 1), isc_a=module.isc,
                length_m=edge.length_m if edge else None, slots=slots_of.get(s, ()),
            ))  # fmt: skip
        path_nodes = first.path
        modelled = next((net.node(n) for n in path_nodes if net.node(n).kind is NodeKind.FUSE and net.node(n).spec), None)
        notes: list[str] = []
        fuse: Fuse | None = None
        if modelled is not None:
            spec = catalog.get(modelled.spec)
            if isinstance(spec, ProtectionSpec) and spec.rated_current:
                poles = modelled.attributes.get("fused_poles")
                fuse = Fuse(spec.rated_current, spec.id, f"{spec.rated_current:g} A gPV", True, poles if isinstance(poles, int) and poles > 0 else 1)
        if fuse is None and (kind == "scb" or (mppt is not None and mppt.inputs_per_mppt * first.strings_per_input > 2)):
            fuse = pick_fuse(catalog, module, voc)
            notes.append(FUSE_ASSUMPTION)
            if kind == "scb":
                notes.append("The SCB is taken to hold one string fuse per input in the (+) pole; the network does not record fuse poles.")
        if fuse is None and mppt is not None:
            notes.append(f"No string fuses are drawn: an MPPT takes at most {mppt.inputs_per_mppt * first.strings_per_input} string(s), below the 3 parallel strings from which the string fuse rule asks for fuses.")
        if mppt is not None:
            notes.append(EVEN_MPPT)
            if mppt.overflow:
                notes.append(f"The {mppt.inputs} string inputs exceed the {inv_spec.mppt_count} MPPT x {inv_spec.inputs_per_mppt or '?'} inputs of {inv_spec.model}.")
        chain = _chain(net, successors, rep.strings[0], frozenset(path_nodes)) if rep.strings else {}
        envelope: Literal["none", "dcjb", "scb"] = "none"
        envelope_title = ""
        skip: set[str] = set()
        head = net.node(path_nodes[0]) if path_nodes else None
        if kind == "scb":
            envelope, envelope_title = "scb", f"{rep.tag} {_short(net.node(sink))}"
            skip.add(sink)
        elif head is not None and head.kind is NodeKind.DC_FUSE_BOX and head.id in chain:
            envelope, envelope_title = "dcjb", f"{instance_tag(net, chain[head.id])} {_short(head)}"
            skip.add(head.id)
        devices = _devices(net, catalog, path_nodes, chain, skip)
        output = None
        if kind == "scb":
            out_edge = next((e for e in net.edges if e.src_node == sink and e.service is Service.DC and e.cable is not None), None)
            if out_edge is not None and rep.destination:
                oc = cable_of(catalog, out_edge)
                inv_tag, _, ip = rep.destination.partition(" ")
                output = Output(output_cable_ids.get(rep.instance, f"{rep.tag}-{inv_tag}"), cable_text(oc, out_edge.runs), out_edge.runs, out_edge.length_m, inv_tag, ip, cable=oc)
        if layout is None or not any(ln.slots for ln in lines):
            notes.append("No table positions: the array layout has no string assignment for these strings.")
        defs = {t: layout.table_by_id[t] for t in rep_tables(lines) if t in layout.table_by_id} if layout else {}
        marks = {t: _table_mark(ids, t) for t in defs}
        types.append(DiagramType(
            kind=kind, sink=sink, members=tuple(members), lines=tuple(lines), module=module, inverter=inv_spec,
            inverter_tags=_tag_range(net, first.inverter.id), string_cable=cable, string_callout=cable_text(cable, edge.runs if edge else 1),
            devices=devices, envelope=envelope, envelope_title=envelope_title, fuse=fuse, mppt=mppt, output=output, table_defs=defs,
            table_marks=marks, tmin_c=basis.ambient_min_c, voc_limit_v=min(inv_spec.dc_max_voltage, module.max_system_voltage),
            notes=tuple(dict.fromkeys(notes)), has_layout=bool(defs), string_runs=edge.runs if edge else 1,
        ))  # fmt: skip
    types = [_with_cables(_with_lengths(t, routed), cables) for t in _merged(types)]
    every = {i for n in net.by_kind(NodeKind.PV_STRING) for i in net.instance_ids(n.id)}
    warnings = []
    if every - covered:
        warnings.append(f"{len(every - covered)} of {len(every)} string instances are not drawn: no inverter with a catalog spec or no module spec downstream")
    return types, warnings


RANGE_NOTE = ("A cable length given as min..max spans the {what} this diagram stands for; the cable schedule gives each cable's own "
              "routed length under its cable id.")


def _span(values: list[float | None]) -> tuple[str, bool]:
    vals = [round(v, 1) for v in values if v is not None]
    if not vals:
        return "", False
    lo, hi = min(vals), max(vals)
    return (f"{lo:g} m", False) if hi - lo < 0.05 else (f"{lo:g}..{hi:g} m", True)


def _with_lengths(t: DiagramType, routed: dict[str, float | None]) -> DiagramType:
    """`t` with every line and the output carrying the routed length of the cable schedule, a min..max range when its instances differ."""
    ranged = False
    lines = []
    for i, ln in enumerate(t.lines):
        text, r = _span([routed.get(m.strings[i]) for m in t.members if i < len(m.strings)] or [ln.length_m])
        ranged |= r
        lines.append(replace(ln, length_text=text))
    output = t.output
    if output is not None:
        text, r = _span([routed.get(m.instance) for m in t.members] or [output.length_m])
        ranged |= r
        output = replace(output, length_text=text)
    notes = t.notes + ((RANGE_NOTE.format(what=f"{len(t.members)} {t.label.lower()}es" if t.kind == "scb" else f"{len(t.members)} inverters"),) if ranged else ())
    return replace(t, lines=tuple(lines), output=output, notes=notes)


def _row_cable(catalog: Catalog, e: Edge, size_mm2: float, runs: int) -> tuple[CableSpec | None, int]:
    """The cable of a schedule row: the edge's own cable or the length band whose size and runs the row carries."""
    for ref, n in ((e.cable, e.runs), *((b.cable, b.runs) for b in e.bands)):
        spec = cable_of(catalog, e.model_copy(update={"cable": ref}))
        if spec is not None and spec.size_mm2 == size_mm2 and n == runs:
            return spec, n
    return cable_of(catalog, e), e.runs


def _with_cables(t: DiagramType, cables: dict[str, tuple[CableSpec | None, int]]) -> DiagramType:
    """`t` with each string line, the string cable and the output callout naming the cables the schedule gives the instances it stands for."""
    if not cables:
        return t
    lines = []
    every: list[tuple[CableSpec | None, int]] = []
    for i, ln in enumerate(t.lines):
        pairs = [cables[m.strings[i]] for m in t.members if i < len(m.strings) and m.strings[i] in cables]
        every += pairs
        lines.append(replace(ln, callout=cable_variants(pairs)) if pairs else ln)
    string_callout = cable_variants(every) if every else t.string_callout
    output = t.output
    outs = [cables[m.instance] for m in t.members if m.instance in cables]
    if output is not None and outs:
        output = replace(output, callout=cable_variants(outs))
    sized = len(set(every)) > 1 or len(set(outs)) > 1
    note = SIZE_NOTE.format(what=f"{len(t.members)} {t.label.lower()}es" if t.kind == "scb" else f"{len(t.members)} inverters")
    notes = t.notes if not sized or any(n.startswith("Cable sizes differ") for n in t.notes) else (*t.notes, note)
    return replace(t, lines=tuple(lines), string_callout=string_callout, output=output, notes=notes)


def _shape(t: DiagramType) -> tuple:
    """What a diagram draws with tags, cable sizes and lengths left out: types of the same shape share one diagram."""
    return (t.kind, tuple((ln.modules, ln.ip, ln.mppt) for ln in t.lines), t.module.id, t.inverter.id, t.envelope, t.fuse, t.mppt,
            tuple(d.split(": ", 1)[-1] for d in t.devices), t.output is not None, t.has_layout)  # fmt: skip


SIZE_NOTE = "Cable sizes differ between the {what} this diagram stands for (each sized for its own route): the cable schedule gives each cable's size; its cable id ends in the size."


def cable_variants(pairs: list[tuple[CableSpec | None, int]]) -> str:
    """One callout for the cables of several instances: '1C x 10 / 16 sq.mm Cu (+) / (-)' when only the size differs."""
    unique = list(dict.fromkeys((c, r) for c, r in pairs))
    if len(unique) == 1 or any(c is None for c, _ in unique) or len({(c.core_label, c.conductor) for c, _ in unique}) > 1:
        return " OR ".join(dict.fromkeys(cable_text(c, r) for c, r in unique))
    first = unique[0][0]
    tail = f"{first.conductor.value.capitalize()} (+) / (-)"
    parts = []
    for r in sorted({r for _, r in unique}):
        sizes = " / ".join(f"{z:g}" for z in sorted({c.size_mm2 for c, n in unique if n == r}))
        parts.append(f"{'' if r == 1 else f'{r} RUNS x '}{first.core_label}C x {sizes} sq.mm")
    return " OR ".join(parts) + f" {tail}"


def _merged(types: list[DiagramType]) -> list[DiagramType]:
    """Types of one shape (identical blocks whose templates the network keeps apart, or whose cables were sized per block) drawn once."""
    groups: dict[tuple, list[DiagramType]] = {}
    for t in types:
        groups.setdefault(_shape(t), []).append(t)
    out = []
    for same in groups.values():
        first = same[0]
        if len(same) == 1:
            out.append(first)
            continue
        what = f"{sum(len(x.members) for x in same)} {first.label.lower()}es" if first.kind == "scb" else f"{sum(len(x.members) for x in same)} inverters"
        output = first.output
        outs = [(x.output.cable, x.output.runs) for x in same if x.output]
        if output is not None:
            output = replace(output, callout=cable_variants(outs))
        strings = [(x.string_cable, x.string_runs) for x in same]
        ranges = [x.tag_range for x in same]
        sized = (SIZE_NOTE.format(what=what),) if len(set(strings)) > 1 or len(set(outs)) > 1 else ()
        notes = (*dict.fromkeys(n for x in same for n in x.notes), f"Typical for {', '.join(ranges)}.", *sized)
        last = same[-1].inverter_tags.split("..")[-1]
        out.append(replace(
            first, members=tuple(m for x in same for m in x.members), string_callout=cable_variants(strings), output=output,
            inverter_tags=f"{first.inverter_tags.split('..')[0]}..{last}", notes=notes,
        ))  # fmt: skip
    return out


def rep_tables(lines: tuple[StringLine, ...] | list[StringLine]) -> list[str]:
    return list(dict.fromkeys(t for ln in lines for t, _, _ in ln.slots))


def _table_mark(ids: Ids, table_id: str) -> str:
    pos = ids.table_position.get(table_id)
    return f"R{pos[0]:03d}-{pos[1]:04d}" if pos else ""

"""What a string diagram shows, as plain data: the inverter or SCB types of a project, the strings of a typical instance in series
order with their cables, fuses, MPPT inputs and table positions, and every instance a type stands for."""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from unicad.catalog.store import Catalog
from unicad.domain.catalog import CableSpec, InverterSpec, ModuleSpec, ProtectionSpec
from unicad.domain.layout import ArrayLayout, Table
from unicad.domain.network import ElectricalNetwork, Node, NodeKind, Service
from unicad.domain.project import Project
from unicad.rules.cables import cable_of
from unicad.rules.pv import cell_temp_min, string_voltage
from unicad.rules.thresholds import ISC_FACTOR
from unicad.rules.topology import EVEN_MPPT, instance_tag, string_feeds
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
    local_id: str
    ip: str
    stem: str
    cable_id: str
    modules: int
    mppt: int | None
    voc_cold_v: float
    isc_a: float
    length_m: float | None
    slots: tuple[tuple[str, int, int], ...]

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

    @property
    def representative(self) -> Member:
        return self.members[0]

    @property
    def tag_range(self) -> str:
        first, last = self.members[0].tag, self.members[-1].tag
        return first if len(self.members) == 1 else f"{first} .. {last}"

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
    return tags[0] if len(tags) == 1 else f"{tags[0]} .. {tags[-1]}"


def plan_types(project: Project, catalog: Catalog) -> tuple[list[DiagramType], list[str]]:
    """One DiagramType per string-inverter or SCB template that strings feed, and warnings for strings that cannot be drawn."""
    net = project.network
    expanded = net.expand()
    ids = Ids(project, expanded)
    schedule_rows = build_schedule(project, catalog, LengthMode.NETWORK).rows
    string_cable_ids = {r.src: r.cable_id for r in schedule_rows if r.is_string}
    output_cable_ids = {r.src: r.cable_id for r in schedule_rows if not r.is_string and r.service is Service.DC}
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
            ipm = max(1, math.ceil(n_inputs / inv_spec.mppt_count))
            used = math.ceil(n_inputs / ipm)
            overflow = used > inv_spec.mppt_count or (inv_spec.inputs_per_mppt is not None and ipm > inv_spec.inputs_per_mppt)
            mppt = MpptPlan(inv_spec.mppt_count, ipm, n_inputs, used, overflow)
        lines: list[StringLine] = []
        for i, s in enumerate(rep.strings, start=1):
            node = net.node(s.partition("@")[0])
            stem = ids.string_stem(s)
            ip = stem.rsplit("-", 1)[-1]
            lines.append(StringLine(
                instance=s, tag=instance_tag(net, s), number=i, local_id=f"S{i:02d}", ip=ip, stem=stem,
                cable_id=string_cable_ids.get(s, stem), modules=node.modules_in_series or n_series,
                mppt=((i - 1) // mppt.inputs_per_mppt + 1) if mppt else None, voc_cold_v=round(voc, 1), isc_a=module.isc,
                length_m=edge.length_m if edge else None, slots=slots_of.get(s, ()),
            ))  # fmt: skip
        path_nodes = first.path
        modelled = next((net.node(n) for n in path_nodes if net.node(n).kind is NodeKind.FUSE and net.node(n).spec), None)
        notes: list[str] = []
        fuse: Fuse | None = None
        if modelled is not None:
            spec = catalog.get(modelled.spec)
            if isinstance(spec, ProtectionSpec) and spec.rated_current:
                fuse = Fuse(spec.rated_current, spec.id, f"{spec.rated_current:g} A gPV", True)
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
                output = Output(output_cable_ids.get(rep.instance, f"{rep.tag}-{inv_tag}"), cable_text(oc, out_edge.runs), out_edge.runs, out_edge.length_m, inv_tag, ip)
        if layout is None or not any(ln.slots for ln in lines):
            notes.append("No table positions: the array layout has no string assignment for these strings.")
        defs = {t: layout.table_by_id[t] for t in rep_tables(lines) if t in layout.table_by_id} if layout else {}
        marks = {t: _table_mark(ids, t) for t in defs}
        types.append(DiagramType(
            kind=kind, sink=sink, members=tuple(members), lines=tuple(lines), module=module, inverter=inv_spec,
            inverter_tags=_tag_range(net, first.inverter.id), string_cable=cable, string_callout=cable_text(cable, edge.runs if edge else 1),
            devices=devices, envelope=envelope, envelope_title=envelope_title, fuse=fuse, mppt=mppt, output=output, table_defs=defs,
            table_marks=marks, tmin_c=basis.ambient_min_c, voc_limit_v=min(inv_spec.dc_max_voltage, module.max_system_voltage),
            notes=tuple(dict.fromkeys(notes)), has_layout=bool(defs),
        ))  # fmt: skip
    every = {i for n in net.by_kind(NodeKind.PV_STRING) for i in net.instance_ids(n.id)}
    warnings = []
    if every - covered:
        warnings.append(f"{len(every - covered)} of {len(every)} string instances are not drawn: no inverter with a catalog spec or no module spec downstream")
    return types, warnings


def rep_tables(lines: tuple[StringLine, ...] | list[StringLine]) -> list[str]:
    return list(dict.fromkeys(t for ln in lines for t, _, _ in ln.slots))


def _table_mark(ids: Ids, table_id: str) -> str:
    pos = ids.table_position.get(table_id)
    return f"R{pos[0]:03d}-{pos[1]:04d}" if pos else ""

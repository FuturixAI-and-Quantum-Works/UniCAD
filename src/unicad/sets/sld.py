"""The SLD part of a drawing set: an overall SLD with each identical block drawn once, a detail sheet per
distinct block type, and further sheets wherever a sheet would not be readable on A0."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from unicad.catalog.store import Catalog
from unicad.domain.network import Edge, ElectricalNetwork, Group, NodeKind
from unicad.domain.project import Project, Sheet, SheetKind, SheetScope
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sld import annotate
from unicad.drafting.sld.layout import ROW_REF, plan_pages
from unicad.drafting.sld.model import SldOptions
from unicad.drafting.sld.sheet import fit_layout, furniture_overflow, render_sld
from unicad.drafting.sld.xref import partition
from unicad.schedules import LengthMode, build_schedule
from unicad.sets.numbering import Numberer
from unicad.sets.registry import BuiltSheet, register

PAPERS = ("A3", "A2", "A1", "A0")
COMFORT = 1.0
"""Preferred scale: the smallest paper that draws the diagram at full size wins."""
GENERATION = frozenset({NodeKind.INVERTER, NodeKind.PCS})


def _members(net: ElectricalNetwork, gid: str) -> list[str]:
    out = []
    for n in net.nodes:
        g = n.group
        while g is not None:
            if g == gid:
                out.append(n.id)
                break
            g = net.group_by_id[g].within
    return out


def block_types(net: ElectricalNetwork) -> list[tuple[Group, tuple[str, ...]]]:
    """Top-level groups holding a transformer and, in sub-groups, the generation it collects: the plant's
    block types, each with the sub-groups its detail sheet draws."""
    out = []
    for g in net.groups:
        if g.within is not None or not any(n.group == g.id and n.kind is NodeKind.TRANSFORMER for n in net.nodes):
            continue
        subs = tuple(h.id for h in net.groups if h.within == g.id and any(net.node(m).kind in GENERATION for m in _members(net, h.id)))
        if subs:
            out.append((g, subs))
    return out


def _top(net: ElectricalNetwork, gid: str | None) -> str | None:
    while gid is not None and net.group_by_id[gid].within is not None:
        gid = net.group_by_id[gid].within
    return gid


def _ref_top(net: ElectricalNetwork, ref: str) -> str | None:
    return _top(net, net.node(ref).group if ref in net.node_by_id else ref)


def _block_edges(net: ElectricalNetwork, members: frozenset[str]) -> list[Edge]:
    return [e for e in net.edges if e.src_node in members or e.dst_node in members]


def _signature(net: ElectricalNetwork, g: Group) -> tuple:
    """The block's equipment, sub-groups and connections with ids, tags and cable sizing (cable, runs, derating, route length) left out:
    two blocks with the same signature draw the same detail sheet, which gives a cable that differs between them as a range."""
    members = _members(net, g.id)
    idx = {m: i for i, m in enumerate(members)}
    subs = [h.id for h in net.groups if h.id != g.id and _top(net, h.id) == g.id]
    gidx = {h: i for i, h in enumerate(subs)} | {g.id: -1}
    groups = tuple((net.group_by_id[h].count, net.group_by_id[h].title, gidx[net.group_by_id[h].within]) for h in subs)

    def spec(x: object) -> object:
        return x.model_dump_json() if isinstance(x, BaseModel) else x

    nodes = tuple(
        (n.kind, spec(n.spec), n.symbol, n.count, gidx[n.group], n.voltage_v, n.modules_in_series, n.ports, tuple(sorted(n.attributes.items())), n.title)
        for n in map(net.node, members)
    )

    def end(node: str, port: str) -> tuple:
        return ("m", idx[node], port) if node in idx else ("x", node, port)

    edges = tuple(
        (end(e.src_node, e.src_port), end(e.dst_node, e.dst_port), e.service, e.cable is None, e.laying)
        for e in _block_edges(net, frozenset(members))
    )
    return groups, nodes, edges


def block_classes(net: ElectricalNetwork) -> list[tuple[tuple[Group, ...], tuple[str, ...]]]:
    """Block types grouped by signature, in network order: the blocks one TYPICAL detail sheet stands for and the sub-groups of
    the first, which the sheet draws."""
    out: dict[tuple, tuple[list[Group], tuple[str, ...]]] = {}
    for g, subs in block_types(net):
        out.setdefault(_signature(net, g), ([], subs))[0].append(g)
    return [(tuple(gs), subs) for gs, subs in out.values()]


def _class_of(net: ElectricalNetwork, sheet: Sheet) -> tuple[Group, ...]:
    """The blocks a detail sheet stands for when it is TYPICAL for more than one (the block's own sheet or a piece split from it, never a
    piece of the overall diagram that happens to start in that block)."""
    if sheet.kind is not SheetKind.SLD_DETAIL or not sheet.scope.nodes:
        return ()
    top = _ref_top(net, sheet.scope.nodes[0])
    if sheet.id != f"sld_{top}" and not sheet.id.startswith(f"sld_{top}_"):
        return ()
    return next((gs for gs, _ in block_classes(net) if len(gs) > 1 and gs[0].id == top), ())


def _drawn(net: ElectricalNetwork, sheet: Sheet, nodes: frozenset[str]) -> frozenset[str]:
    """What a sheet draws of the nodes it owns: a TYPICAL sheet owns every block of its class (so cut conductors elsewhere point
    to it) and draws the first."""
    blocks = _class_of(net, sheet)
    return frozenset(n for n in nodes if _top(net, net.node(n).group) == blocks[0].id) if blocks else nodes


_routed: list[tuple[Project, Catalog | None, dict[str, list[float]]]] = []


def _routed_lengths(project: Project, catalog: Catalog | None) -> dict[str, list[float]]:
    """Per template edge, the routed length of each of its instances as the set's cable schedule gives it (kept for the last project asked)."""
    if _routed and _routed[0][0] is project and _routed[0][1] is catalog:
        return _routed[0][2]
    out: dict[str, list[float]] = {}
    if catalog is not None:
        for r in build_schedule(project, catalog, LengthMode.AUTO).rows:
            if (v := r.run_length_m) is not None:
                out.setdefault(r.edge, []).append(v)
    _routed[:] = [(project, catalog, out)]
    return out


def _shown(project: Project, sheet: Sheet, catalog: Catalog | None = None) -> Project:
    """The project as a sheet shows it: each cable with the routed length of the cable schedule, or `L = min..max m` when its instances
    (on a TYPICAL sheet, those of every block it stands for) differ; a cable sized differently between those blocks names the sizes."""
    net = project.network
    blocks = _class_of(net, sheet)
    if blocks:
        per_block = [_block_edges(net, frozenset(_members(net, g.id))) for g in blocks]
        alike = {es[0].id: list(es) for es in zip(*per_block, strict=True)}
    else:
        alike = {e.id: [e] for e in net.edges}
    routed = _routed_lengths(project, catalog)
    changed = {}
    for eid, es in alike.items():
        e = es[0]
        notes = []
        lengths = [round(v, 1) for x in es for v in routed.get(x.id, [])]
        if not lengths and not any(x.length_m is None for x in es):
            lengths = [x.length_m for x in es]
        update: dict = {}
        if lengths and max(lengths) - min(lengths) >= 0.05:
            update["length_m"] = None
            notes.append(f"L = {min(lengths):g}..{max(lengths):g} m (SEE CABLE SCHEDULE)")
        elif lengths and min(lengths) != e.length_m:
            update["length_m"] = min(lengths)
        sizes = list(dict.fromkeys(annotate.cable_text(x, catalog, net) for x in es))
        if len(sizes) > 1:
            notes.append(f"CABLE PER BLOCK: {' / '.join(sizes)}, SEE THE CABLE SCHEDULE")
        if notes:
            update["remarks"] = "; ".join(([e.remarks] if e.remarks else []) + notes)
        if update:
            changed[eid] = e.model_copy(update=update)
    if not changed:
        return project
    shown = ElectricalNetwork(groups=net.groups, nodes=net.nodes, edges=tuple(changed.get(e.id, e) for e in net.edges))
    return project.model_copy(update={"network": shown})


PART = re.compile(r" \(PART \d+ OF \d+\)$")


def _drawing(sheet: Sheet) -> str:
    """The drawing a sheet is a part of: its title without 'PART k OF n', so the title block counts 'sheet k of n' over the same parts."""
    return PART.sub("", sheet.title)


def _papers_from(first: str) -> tuple[str, ...]:
    return PAPERS[PAPERS.index(first):] if first in PAPERS else PAPERS


def _tops(net: ElectricalNetwork, nodes: frozenset[str]) -> tuple[str, ...]:
    """Nodes of a split page with a conductor across its border: the roots its sheet scope names, so the
    page keeps them and everything that hangs off them (see `sld.xref`)."""
    out = {n for e in net.edges if e.is_power and (e.src_node in nodes) != (e.dst_node in nodes) for n in (e.src_node, e.dst_node) if n in nodes}
    return tuple(n.id for n in net.nodes if n.id in out)


@dataclass(frozen=True)
class SldBuilder:
    options: SldOptions = SldOptions()

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer) -> tuple[Sheet, ...]:
        net = project.network
        blocks = block_types(net)
        fixed: set[str] = set()
        base = numbers.reuse(SheetKind.SLD.value)
        sheets = [Sheet(
            id="sld",
            number=base.number if base else numbers.fresh(),
            kind=SheetKind.SLD,
            title=base.title if base else ("OVERALL SINGLE LINE DIAGRAM" if blocks else "SINGLE LINE DIAGRAM"),
            paper=base.paper if base else profile.default_paper.value,
            revisions=base.revisions if base else (),
        )]
        if base:
            fixed.add("sld")
        subs_of = {g.id: subs for g, subs in blocks}
        for members, subs in block_classes(net):
            g = members[0]
            old = numbers.reuse(SheetKind.SLD_DETAIL.value)
            name = annotate.prose(g.title) if g.title else "BLOCK"
            span = annotate.group_label(net, g)
            sid = f"sld_{g.id}"
            if len(members) > 1:
                title = f"TYPICAL {name} SLD FOR {', '.join(annotate.group_label(net, m) for m in members)} (SHOWN FOR {span})"
            else:
                title = f"TYPICAL {name} SLD ({span})" if span != name else f"TYPICAL {name} SLD"
            sheets.append(Sheet(
                id=sid,
                number=old.number if old else numbers.fresh(),
                kind=SheetKind.SLD_DETAIL,
                title=title,
                paper=old.paper if old else profile.default_paper.value,
                scope=SheetScope(nodes=tuple(r for m in members for r in subs_of[m.id])),
                revisions=old.revisions if old else (),
            ))
            if old:
                fixed.add(sid)
        return self._renumber(self._fit(project, catalog, profile, sheets, fixed, numbers), fixed)

    @staticmethod
    def _renumber(sheets: tuple[Sheet, ...], fixed: set[str]) -> tuple[Sheet, ...]:
        """Give the freshly numbered sheets their numbers in sheet order, so split pieces follow the sheet they were split from."""
        free = sorted((s.number for s in sheets if s.id not in fixed), key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])
        it = iter(free)
        return tuple(s if s.id in fixed else s.model_copy(update={"number": next(it)}) for s in sheets)

    def _fit(self, project: Project, catalog: Catalog | None, profile: CompanyProfile, sheets: list[Sheet], fixed: set[str],
             numbers: Numberer) -> tuple[Sheet, ...]:
        """Give every sheet the smallest paper it reads well on with room for its notes and legend. A sheet not
        readable even on A0 is split along `plan_pages`, and a piece still unreadable is split again; the pieces become sheets titled PART k OF n."""
        net = project.network
        smallest = profile.default_paper.value
        settled: set[str] = set()
        tried: set[tuple[str, frozenset[str]]] = set()
        origin: dict[str, str] = {}
        while (todo := next((i for i, s in enumerate(sheets) if s.id not in settled), None)) is not None:
            s = sheets[todo]
            part = next(p for p in partition(net, sheets) if p.sheet_id == s.id)
            drawn, shown = _drawn(net, s, part.nodes), _shown(project, s, catalog)
            papers = _papers_from(s.paper if s.id in fixed else smallest)
            target = self.options.min_scale if s.id in fixed else max(COMFORT, self.options.min_scale)
            scales = {}
            for paper in papers:
                layout, _, scale = fit_layout(shown, catalog, paper, drawn, self.options, part.xrefs)
                roomy = paper == papers[-1] or not furniture_overflow(shown, s.model_copy(update={"paper": paper}), layout, catalog, profile)
                scales[paper] = scale if roomy else 0.0
                if scales[paper] >= target:
                    break
            good = [p for p in scales if scales[p] >= target] or [p for p in scales if scales[p] >= self.options.min_scale]
            settled.add(s.id)
            sheets[todo] = s.model_copy(update={"paper": good[0] if good else papers[-1]})
            if good or (s.id, drawn) in tried:
                continue
            tried.add((s.id, drawn))
            area = metrics_for(PaperSize(papers[-1])).drawing_area
            pages = plan_pages(shown.network, catalog, area.width, area.height, self.options, drawn)
            taken = {sid for sid in (x.id for x in sheets)}
            extra = []
            for pg in pages[1:]:
                roots = _tops(net, frozenset(pg.node_ids))
                k = 2
                while f"{s.id}_{k}" in taken:
                    k += 1
                taken.add(f"{s.id}_{k}")
                if roots:
                    extra.append(Sheet(id=f"{s.id}_{k}", number=f"{s.number}/{k}", kind=SheetKind.SLD_DETAIL, title=s.title,
                                       paper=papers[-1], scope=SheetScope(nodes=roots), revisions=s.revisions))
            trial = sheets[:todo + 1] + extra + sheets[todo + 1:]
            owned = {p.sheet_id for p in partition(net, trial) if p.nodes}
            extra = [x.model_copy(update={"number": numbers.fresh()}) for x in extra if x.id in owned]
            if not extra:
                continue
            sheets[todo + 1:todo + 1] = extra
            settled.discard(s.id)
            for x in extra:
                origin[x.id] = origin.get(s.id, s.id)
        groups: dict[str, list[int]] = {}
        for i, s in enumerate(sheets):
            groups.setdefault(origin.get(s.id, s.id), []).append(i)
        for members in groups.values():
            if len(members) > 1:
                for k, i in enumerate(members, start=1):
                    sheets[i] = sheets[i].model_copy(update={"title": f"{sheets[i].title} (PART {k} OF {len(members)})"})
        return tuple(sheets)

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None, *, drawn_on: date | None = None) -> list[BuiltSheet]:
        out = []
        for s, part in zip(sheets, partition(project.network, sheets)):
            nodes = _drawn(project.network, s, part.nodes)
            pieces = [x for x in drawing_set if _drawing(x) == _drawing(s)] if PART.search(s.title) else [s]
            doc, res = render_sld(_shown(project, s, catalog), s.id, profile, catalog, options=self.options, sheets=pieces, nodes=nodes, xrefs=part.xrefs, drawn_on=drawn_on)
            marks = {o.symbol_id: o for o in res.layout.offsheets}
            refs = [
                {"edge": o.edge_id, "node": o.node_id, "to_sheet": o.target, "text": dict(sym.values).get("DESC", "")}
                for sym in res.layout.symbols if sym.role == "offsheet" and (o := marks.get(sym.id)) is not None and not (o.target or "").startswith(ROW_REF)
            ]
            meta = {"nodes": sorted(nodes), "cut_edges": list(part.cut_edges), "cross_refs": refs, "readable": res.placement.readable}
            out.append(BuiltSheet(s, doc, res.placement.scale, res.warnings, meta))
        return out


def plan_sld_set(project: Project, profile: CompanyProfile, catalog: Catalog | None = None, options: SldOptions = SldOptions()) -> tuple[Sheet, ...]:
    """The SLD sheets of the project's drawing set, numbered from the profile pattern (reusing the project's
    existing SLD numbers, papers and revisions in order)."""
    return SldBuilder(options).plan(project, profile, catalog, Numberer.for_project(project, profile))


register("sld", SldBuilder())

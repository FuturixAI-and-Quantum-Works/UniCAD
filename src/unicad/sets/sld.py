"""The SLD part of a drawing set: an overall SLD with each identical block drawn once, a detail sheet per
distinct block type, and further sheets wherever a sheet would not be readable on A0."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.network import ElectricalNetwork, Group, NodeKind
from unicad.domain.project import Project, Sheet, SheetKind, SheetScope
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sld import annotate
from unicad.drafting.sld.layout import plan_pages
from unicad.drafting.sld.model import SldOptions
from unicad.drafting.sld.sheet import fit_layout, furniture_overflow, render_sld
from unicad.drafting.sld.xref import partition
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
        for g, subs in blocks:
            old = numbers.reuse(SheetKind.SLD_DETAIL.value)
            name = annotate.prose(g.title) if g.title else "BLOCK"
            span = annotate.group_label(net, g)
            sid = f"sld_{g.id}"
            sheets.append(Sheet(
                id=sid,
                number=old.number if old else numbers.fresh(),
                kind=SheetKind.SLD_DETAIL,
                title=f"TYPICAL {name} SLD ({span})" if span != name else f"TYPICAL {name} SLD",
                paper=old.paper if old else profile.default_paper.value,
                scope=SheetScope(nodes=subs),
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
        readable even on A0 is split once along `plan_pages`; its pieces become sheets titled PART k OF n."""
        net = project.network
        smallest = profile.default_paper.value
        settled: set[str] = set()
        split: set[str] = set()
        origin: dict[str, str] = {}
        while (todo := next((i for i, s in enumerate(sheets) if s.id not in settled), None)) is not None:
            s = sheets[todo]
            part = next(p for p in partition(net, sheets) if p.sheet_id == s.id)
            papers = _papers_from(s.paper if s.id in fixed else smallest)
            target = self.options.min_scale if s.id in fixed else max(COMFORT, self.options.min_scale)
            scales = {}
            for paper in papers:
                layout, _, scale = fit_layout(project, catalog, paper, part.nodes, self.options, part.xrefs)
                roomy = paper == papers[-1] or not furniture_overflow(project, s.model_copy(update={"paper": paper}), layout, catalog, profile)
                scales[paper] = scale if roomy else 0.0
                if scales[paper] >= target:
                    break
            good = [p for p in scales if scales[p] >= target] or [p for p in scales if scales[p] >= self.options.min_scale]
            settled.add(s.id)
            sheets[todo] = s.model_copy(update={"paper": good[0] if good else papers[-1]})
            if good or s.id in split:
                continue
            area = metrics_for(PaperSize(papers[-1])).drawing_area
            pages = plan_pages(net, catalog, area.width, area.height, self.options, part.nodes)
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
            split.update({s.id, *(x.id for x in extra)})
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
              catalog: Catalog | None) -> list[BuiltSheet]:
        out = []
        for s, part in zip(sheets, partition(project.network, sheets)):
            doc, res = render_sld(project, s.id, profile, catalog, options=self.options, sheets=drawing_set, nodes=part.nodes, xrefs=part.xrefs)
            marks = {o.symbol_id: o for o in res.layout.offsheets}
            refs = [
                {"edge": o.edge_id, "node": o.node_id, "to_sheet": o.target, "text": dict(sym.values).get("DESC", "")}
                for sym in res.layout.symbols if sym.role == "offsheet" and (o := marks.get(sym.id)) is not None
            ]
            meta = {"nodes": sorted(part.nodes), "cut_edges": list(part.cut_edges), "cross_refs": refs, "readable": res.placement.readable}
            out.append(BuiltSheet(s, doc, res.placement.scale, res.warnings, meta))
        return out


def plan_sld_set(project: Project, profile: CompanyProfile, catalog: Catalog | None = None, options: SldOptions = SldOptions()) -> tuple[Sheet, ...]:
    """The SLD sheets of the project's drawing set, numbered from the profile pattern (reusing the project's
    existing SLD numbers, papers and revisions in order)."""
    return SldBuilder(options).plan(project, profile, catalog, Numberer.for_project(project, profile))


register("sld", SldBuilder())

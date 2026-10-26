"""The SLD part of a drawing set: an overall SLD with each identical block drawn once, a detail sheet per
distinct block type, and further sheets wherever a sheet would not be readable on A0."""

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
    """Nodes of a split page that feed something off the page: the roots its sheet scope names."""
    out = {e.src_node for e in net.edges if e.is_power and e.src_node in nodes and e.dst_node not in nodes}
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
        return self._fit(project, catalog, profile, sheets, fixed, numbers)

    def _fit(self, project: Project, catalog: Catalog | None, profile: CompanyProfile, sheets: list[Sheet], fixed: set[str],
             numbers: Numberer) -> tuple[Sheet, ...]:
        """Give every sheet the smallest paper it reads well on with room for its notes and legend, splitting a
        sheet that is not readable even on A0."""
        net = project.network
        smallest = profile.default_paper.value
        settled: set[str] = set()
        for _ in range(len(net.nodes) + 1):
            parts = {p.sheet_id: p for p in partition(net, sheets)}
            todo = next((i for i, s in enumerate(sheets) if s.id not in settled), None)
            if todo is None:
                break
            s = sheets[todo]
            part = parts[s.id]
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
            if good:
                sheets[todo] = s.model_copy(update={"paper": good[0]})
                continue
            paper = papers[-1]
            area = metrics_for(PaperSize(paper)).drawing_area
            pages = plan_pages(net, catalog, area.width, area.height, self.options, part.nodes)
            sheets[todo] = s.model_copy(update={"paper": paper})
            if len(pages) < 2:
                continue
            n = len(pages)
            sheets[todo] = sheets[todo].model_copy(update={"title": f"{s.title} (PART 1 OF {n})"})
            extra = [
                Sheet(id=f"{s.id}_{k}", number=numbers.fresh(), kind=SheetKind.SLD_DETAIL, title=f"{s.title} (PART {k} OF {n})",
                      paper=paper, scope=SheetScope(nodes=_tops(net, frozenset(pg.node_ids))), revisions=s.revisions)
                for k, pg in enumerate(pages[1:], start=2)
            ]
            sheets[todo + 1:todo + 1] = extra
            settled.discard(s.id)
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

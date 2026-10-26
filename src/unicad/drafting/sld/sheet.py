"""Render a project's SLD sheet: company sheet, legend, parameters, notes and the diagram."""

import io
from collections import OrderedDict
import re
from dataclasses import dataclass
from pathlib import Path

import ezdxf

from ezdxf.document import Drawing

from unicad.catalog.store import Catalog
from unicad.domain.project import Project, Sheet
from unicad.drafting.legend import legend_drawer
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import (
    CompanyProfile,
    IssuedFor,
    PaperSize,
    Revision,
    SheetSpec,
    TitleBlockData,
)
from unicad.drafting.sheets.render import RenderedSheet, render_sheet
from unicad.drafting.sld import annotate
from unicad.drafting.sld.layout import layout_sld, plan_pages
from unicad.drafting.sld.model import SldLayout, SldOptions, SldPage
from unicad.drafting.sld.write import SldPlacement, write_sld
from unicad.drafting.symbols import REGISTRY

NOTES = (
    "REPEATED EQUIPMENT GROUPS ARE DRAWN ONCE INSIDE A DASHED FRAME; (x N) GIVES THE NUMBER OF IDENTICAL INSTANCES.",
    "CABLE CALLOUTS READ: RUNS x CORES x SIZE, CONDUCTOR, INSULATION, ARMOUR / VOLTAGE GRADE, LAYING, LENGTH.",
)


@dataclass(frozen=True)
class SldSheet:
    layout: SldLayout
    sheet: RenderedSheet
    placement: SldPlacement
    pages: tuple[SldPage, ...]
    warnings: tuple[str, ...]


def _scope(project: Project, sheet: Sheet) -> frozenset[str] | None:
    refs = sheet.scope.nodes
    if not refs:
        return None
    net = project.network
    keep: set[str] = set()
    for ref in refs:
        if ref in net.node_by_id:
            keep |= {ref, *net.upstream(ref)}
        else:
            members = [n.id for n in net.nodes if _in_group(project, n.group, ref)]
            for m in members:
                keep |= {m, *net.upstream(m)}
    return frozenset(keep)


def _in_group(project: Project, gid: str | None, target: str) -> bool:
    groups = project.network.group_by_id
    while gid is not None:
        if gid == target:
            return True
        gid = groups[gid].within
    return False


def sheet_spec(project: Project, sheet: Sheet, layout: SldLayout, catalog: Catalog | None) -> SheetSpec:
    paper = PaperSize(sheet.paper)
    revs = sheet.revisions or project.revisions
    latest = revs[-1] if revs else None
    issued = IssuedFor[latest.issued_for.name] if latest and latest.issued_for else IssuedFor.INFORMATION
    site = project.site
    location = ", ".join(p for p in (site.name, site.address, site.state) if p)
    index = [s.id for s in project.sheets].index(sheet.id) + 1 if sheet in project.sheets else 1
    tb = TitleBlockData(
        client=annotate.prose(project.client.name),
        project_title=annotate.prose(project.name),
        location=annotate.prose(location),
        drawing_title=annotate.prose(sheet.title),
        drawing_number=sheet.number,
        revision=latest.rev if latest else "00",
        scale=sheet.scale,
        sheet_no=index,
        sheet_count=max(index, len(project.sheets)),
        paper=paper,
        drawn_by=(latest.drawn or "") if latest else "",
        checked_by=(latest.checked or "") if latest else "",
        approved_by=(latest.approved or "") if latest else "",
        drawn_date=latest.date if latest else None,
        issued_for=issued,
    )
    legend = tuple((k, REGISTRY[k].title.upper()) for k in layout.legend_keys() if k in REGISTRY)
    notes = layout.notes + NOTES + annotate.assumption_notes(project)
    return SheetSpec(
        paper=paper,
        title_block=tb,
        revisions=tuple(
            Revision(rev=r.rev, date=r.date, description=annotate.prose(r.remarks), by=r.drawn or "", checked=r.checked or "",
                     approved=r.approved or "")
            for r in revs
        ),
        legend=legend,
        notes=notes,
        parameters=annotate.system_parameters(project, catalog),
    )


def render_sld(
    project: Project,
    sheet_id: str,
    profile: CompanyProfile,
    catalog: Catalog | None = None,
    doc: Drawing | None = None,
    options: SldOptions = SldOptions(),
) -> tuple[Drawing, SldSheet]:
    sheet = next(s for s in project.sheets if s.id == sheet_id)
    scope = _scope(project, sheet)
    paper = PaperSize(sheet.paper)
    area = metrics_for(paper).drawing_area
    layout = layout_sld(project.network, catalog, options, scope)
    if layout.fit_scale(area.width, area.height, options) < options.min_scale:
        tight = options.compact()
        compact = layout_sld(project.network, catalog, tight, scope)
        if compact.fit_scale(area.width, area.height, tight) > layout.fit_scale(area.width, area.height, options):
            layout, options = compact, tight
    if doc is None:
        doc = ezdxf.new("R2018", setup=["linetypes"])
    spec = sheet_spec(project, sheet, layout, catalog)
    n = len(project.design_basis.assumptions)
    fallbacks = (NOTES + (f"{n} DESIGN BASIS ASSUMPTIONS ARE LISTED IN THE PROJECT RECORD.",) if n else NOTES, NOTES[:1], ())
    for notes in fallbacks:
        scratch = ezdxf.new("R2018", setup=["linetypes"])
        if not any("legend rows" in w for w in render_sheet(scratch, spec, profile, legend_drawer(scratch.modelspace())).warnings):
            break
        spec = spec.model_copy(update={"notes": notes})
    rendered = render_sheet(doc, spec, profile, legend_drawer(doc.modelspace()))
    area = rendered.drawing_area
    placement = write_sld(doc, layout, area, block_name=f"UC_SLD_{sheet.id.upper()}", text_style=profile.text_style,
                          options=options)
    warnings = list(rendered.warnings) + list(layout.warnings)
    pages: tuple[SldPage, ...] = (SldPage(tuple(n for n, _ in layout.represented), (), placement.scale),)
    if not placement.readable:
        pages = plan_pages(project.network, catalog, area.width, area.height, options, scope)
        warnings.append(f"diagram needs scale {placement.scale:.2f} on {sheet.paper}; split plan has {len(pages)} pages")
    return doc, SldSheet(layout, rendered, placement, pages, tuple(warnings))


_STAMP = re.compile(r" @ \d{4}-\d\d-\d\dT[0-9:.]+(\+00:00)?")


def dxf_text(doc: Drawing) -> str:
    """The DXF as text with fixed timestamps and GUIDs, so the same model always gives the same bytes."""
    # ezdxf registers CLASS entries by iterating a set of entity types; pre-register them in sorted order.
    doc.classes.add_required_classes(doc.dxfversion)
    doc.classes.classes = OrderedDict(sorted(doc.classes.classes.items()))
    old = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        buf = io.StringIO()
        doc.write(buf)
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = old
    return _STAMP.sub("", buf.getvalue())


def save_deterministic(doc: Drawing, path: Path) -> None:
    path.write_bytes(dxf_text(doc).encode(doc.output_encoding, errors="replace"))

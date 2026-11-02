"""Render a project's SLD sheet: company sheet, legend, parameters, notes and the diagram."""

import io
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import ezdxf

from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment

from unicad.catalog.store import Catalog
from unicad.domain.project import Project, Sheet
from unicad.drafting import assumptions, project_fields
from unicad.drafting.legend import COMMS_LEGEND, legend_drawer
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import (
    CompanyProfile,
    IssuedFor,
    LayerRole,
    PaperSize,
    Revision,
    SheetSpec,
    TitleBlockData,
)
from unicad.drafting.sheets.render import RenderedSheet, render_sheet
from unicad.drafting.sheets.text import text_width
from unicad.drafting.sld import annotate
from unicad.drafting.sld.layout import layout_sld, plan_pages, rows_win, stack_rows, xref_tag
from unicad.drafting.sld.model import SldLayout, SldOptions, SldPage, WireKind
from unicad.drafting.sld.write import SldPlacement, write_sld
from unicad.drafting.symbols import REGISTRY

NOTES = annotate.GENERAL_NOTES


def _flag_unreadable(doc: Drawing, profile: CompanyProfile, area, scale: float, minimum: float, paper: str) -> None:
    """A red banner over the top-left corner of the drawing area: the sheet must not go out looking like a good one."""
    h = metrics_for(PaperSize(paper)).th
    words = f"WARNING: THIS DIAGRAM IS DRAWN AT SCALE {scale:.2f} ON {paper}, BELOW THE {minimum:.2f} AT WHICH ITS SMALLEST TEXT PRINTS READABLY. SPLIT OR RE-LAY IT OUT BEFORE ISSUE.".split()
    lines, line = [], ""
    for w in words:
        if line and text_width(f"{line} {w}", h) > 0.6 * area.width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    top = area.y1 - h
    for i, t in enumerate(lines):
        doc.modelspace().add_text(t, dxfattribs={"layer": profile.layer(LayerRole.TEXT).name, "style": profile.text_style, "height": h, "color": 1}).set_placement(
            (area.x0 + 1.5 * h, top - 1.6 * h * i), align=TextEntityAlignment.TOP_LEFT)


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


def sheet_spec(
    project: Project, sheet: Sheet, layout: SldLayout, catalog: Catalog | None, sheets: Sequence[Sheet] | None = None, drawn_on: date | None = None
) -> SheetSpec:
    """Title block, legend and notes. `sheets` is the drawing set the sheet belongs to (default the project's), for 'sheet x of y';
    `drawn_on` dates a sheet with no revision."""
    paper = PaperSize(sheet.paper)
    revs = sheet.revisions or project.revisions
    latest = revs[-1] if revs else None
    issued = IssuedFor[latest.issued_for.name] if latest and latest.issued_for else IssuedFor.INFORMATION
    sheets = tuple(project.sheets if sheets is None else sheets)
    ids = [s.id for s in sheets]
    index = ids.index(sheet.id) + 1 if sheet.id in ids else 1
    tb = TitleBlockData(
        **project_fields.project_fields(project),
        drawing_title=annotate.prose(sheet.title),
        drawing_number=sheet.number,
        revision=latest.rev if latest else "00",
        scale=sheet.scale,
        sheet_no=index,
        sheet_count=max(index, len(sheets)),
        paper=paper,
        drawn_by=(latest.drawn or "") if latest else "",
        checked_by=(latest.checked or "") if latest else "",
        approved_by=(latest.approved or "") if latest else "",
        drawn_date=latest.date if latest else drawn_on,
        issued_for=issued,
    )
    legend = tuple((k, REGISTRY[k].title.upper()) for k in layout.legend_keys() if k in REGISTRY)
    drawn = [w.edge_id for w in layout.wires if w.kind is WireKind.COMMS] + [st.pin for st in layout.stubs if st.kind is WireKind.COMMS]
    comms = [e for e in project.network.edges if e.id in set(drawn)]
    if comms:
        legend += (COMMS_LEGEND,)
    notes = NOTES + layout.notes + comms_notes(project.network, [e for e in comms if e.remarks])
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
        assumptions=assumptions.sheet_assumptions(project, "sld"),
    )


def comms_notes(net, edges) -> tuple[str, ...]:
    """One note per far end and remark: 'COMMUNICATION LINKS INV-01, INV-05 TO DL-01: RS-485 DAISY CHAIN THROUGH THE INVERTERS.'"""
    by: dict[tuple[str, str], list[str]] = {}
    for e in edges:
        src = xref_tag(net, net.node(e.src_node), net.node(e.dst_node))
        by.setdefault((annotate.node_tag(net, net.node(e.dst_node)), annotate.prose(e.remarks)), []).append(src)
    return tuple(
        f"COMMUNICATION LINK{'S' if len(srcs) > 1 else ''} {', '.join(dict.fromkeys(srcs))} TO {dst}: {remark}."
        for (dst, remark), srcs in by.items()
    )


def fit_layout(
    project: Project,
    catalog: Catalog | None,
    paper: str,
    scope: frozenset[str] | None,
    options: SldOptions = SldOptions(),
    xrefs: Mapping[str, str] | None = None,
) -> tuple[SldLayout, SldOptions, float]:
    """The layout for `scope` on `paper`, in stacked rows when that draws it markedly larger (see `stack_rows`), compacted when that reads
    better, with the scale it fits at."""
    area = metrics_for(PaperSize(paper)).drawing_area

    def best(opts: SldOptions) -> tuple[SldLayout, SldOptions, float]:
        out = None
        for o in (opts, replace(opts, sat_lines=2)):
            layout = layout_sld(project.network, catalog, o, scope, xrefs)
            scale = layout.fit_scale(area.width, area.height, o)
            rows = stack_rows(project.network, catalog, o, scope, xrefs, area.width, area.height) if scale < o.max_scale else None
            if rows is not None and rows_win(scale, rows.fit_scale(area.width, area.height, o)):
                layout, scale = rows, rows.fit_scale(area.width, area.height, o)
            if out is None or scale > out[2] + 1e-9:
                out = (layout, o, scale)
        return out

    layout, opts, scale = best(options)
    if scale < 1.0:
        compact, tight, cscale = best(options.compact())
        if cscale > scale:
            return compact, tight, cscale
    return layout, opts, scale


def furniture_overflow(
    project: Project, sheet: Sheet, layout: SldLayout, catalog: Catalog | None, profile: CompanyProfile, sheets: Sequence[Sheet] | None = None
) -> tuple[str, ...]:
    """What of the legend, parameters and notes the sheet's paper has no room for, as render warnings."""
    scratch = ezdxf.new("R2018", setup=["linetypes"])
    spec = sheet_spec(project, sheet, layout, catalog, sheets)
    return tuple(w for w in render_sheet(scratch, spec, profile, legend_drawer(scratch.modelspace())).warnings if " rows did not fit" in w)


def render_sld(
    project: Project,
    sheet_id: str,
    profile: CompanyProfile,
    catalog: Catalog | None = None,
    doc: Drawing | None = None,
    options: SldOptions = SldOptions(),
    *,
    sheets: Sequence[Sheet] | None = None,
    nodes: frozenset[str] | None = None,
    xrefs: Mapping[str, str] | None = None,
    drawn_on: date | None = None,
) -> tuple[Drawing, SldSheet]:
    """Render one SLD sheet. `sheets` is the drawing set (default the project's sheets); `nodes`, when given,
    is exactly what the sheet owns (see `sld.xref.partition`) instead of its scope plus what feeds it;
    `xrefs` maps each cut edge id to the number of the sheet that continues it."""
    sheet = next(s for s in (project.sheets if sheets is None else sheets) if s.id == sheet_id)
    scope = nodes if nodes is not None else _scope(project, sheet)
    layout, options, fitted = fit_layout(project, catalog, sheet.paper, scope, options, xrefs)
    if doc is None:
        doc = ezdxf.new("R2018", setup=["linetypes"])
    spec = sheet_spec(project, sheet, layout, catalog, sheets, drawn_on)
    n = len(spec.assumptions)
    fallbacks = ((f"{n} ASSUMED INPUTS OF THIS SHEET ARE LISTED ON THE DRAWING REGISTER.",) if n else (), ())
    for assumed in fallbacks:
        scratch = ezdxf.new("R2018", setup=["linetypes"])
        if not any("legend rows" in w for w in render_sheet(scratch, spec, profile, legend_drawer(scratch.modelspace())).warnings):
            break
        spec = spec.model_copy(update={"assumptions": assumed})
    rendered = render_sheet(doc, spec, profile, legend_drawer(doc.modelspace()))
    area = rendered.drawing_area
    placement = write_sld(doc, layout, area, block_name=f"UC_SLD_{sheet.id.upper()}", text_style=profile.text_style,
                          options=options)
    warnings = list(rendered.warnings) + list(layout.warnings)
    if not placement.readable:
        _flag_unreadable(doc, profile, area, placement.scale, options.min_scale, sheet.paper)
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

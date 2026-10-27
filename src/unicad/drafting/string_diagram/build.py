"""String diagram sheets of a project: one sheet (or more, when a type has too many strings for one) per distinct inverter or SCB type."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

from unicad.catalog.store import Catalog
from unicad.domain.project import Project, Segment, Sheet, SheetKind
from unicad.drafting.legend import legend_drawer
from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, PaperSize, Revision, SheetSpec, TitleBlockData
from unicad.drafting.sheets.render import render_sheet
from unicad.drafting.sheets.titleblock import compose_location
from unicad.drafting.sld.annotate import prose
from unicad.drafting.sld.sheet import save_deterministic
from unicad.drafting.string_diagram import draw
from unicad.drafting.string_diagram.plan import DiagramType, StringLine, plan_types
from unicad.drafting.symbols import REGISTRY
from unicad.schedules.ids import SCHEME_FIELDS

LARGE_SEGMENTS = frozenset({Segment.UTILITY, Segment.BESS, Segment.HYBRID})
FIRST_SEQ = 401
CABLE_ID_NOTE = f"STRING CABLE IDS ARE THOSE OF THE CABLE SCHEDULE: {SCHEME_FIELDS.upper()}."


@dataclass(frozen=True)
class SheetResult:
    """One generated sheet: the document, the file when an output directory was given, and what the sheet stands for.

    `strings` are the string instances drawn; `covers` are all string instances the sheet stands for (the drawn ones plus the same
    positions in every other instance of the type)."""

    key: str
    title: str
    number: str
    paper: PaperSize
    doc: Drawing
    warnings: tuple[str, ...]
    path: Path | None = None
    sink_template: str = ""
    kind: str = "string_diagram"
    instances: tuple[str, ...] = ()
    strings: tuple[str, ...] = ()
    covers: tuple[str, ...] = ()
    texts: tuple[draw.Placed, ...] = field(default=(), repr=False)
    drawing_area: Region | None = None
    u: float = 0.0


@dataclass(frozen=True)
class PagePlan:
    first: int
    lines: tuple[StringLine, ...]
    u: float
    mode: str
    plan_region: Region | None
    plan_layout: draw.PlanLayout | None
    table_at: tuple[float, float]


def _choose(t: DiagramType, first: int, lines: tuple[StringLine, ...], area: Region, th: float) -> PagePlan | None:
    tables = draw.page_tables(t, lines) if t.has_layout else []
    for step in draw.U_STEPS:
        u = th * step
        if u < draw.MIN_U_MM - 1e-9:
            break
        wd, hd = draw.diagram_size(t, lines, u)
        wt, ht = draw.table_size(t, lines, u)
        options: list[tuple[float, PagePlan]] = []
        gap = 2.0 * u
        if wd <= area.width and wt <= area.width and ht <= area.height - hd - gap:
            top = area.y1 - hd - gap
            region = Region(area.x0 + wt + gap, area.y0, area.x1, top)
            lay = draw.pack_plan(tables, region, u) if tables else None
            if lay or not tables:
                options.append((lay.cw if lay else 0.0, PagePlan(first, lines, u, "below", region if tables else None, lay, (area.x0, top))))
            if tables and not lay:
                region = Region(area.x0, area.y0, area.x1, top - ht - gap)
                lay = draw.pack_plan(tables, region, u) if region.height > 0 else None
                if lay:
                    options.append((lay.cw, PagePlan(first, lines, u, "stacked", region, lay, (area.x0, top))))
        px = area.x0 + wd + gap
        if hd <= area.height and px + wt <= area.x1 and ht <= area.height:
            region = Region(px, area.y0, area.x1, area.y1 - ht - gap)
            lay = draw.pack_plan(tables, region, u) if tables else None
            if lay or not tables:
                options.append((lay.cw if lay else 0.0, PagePlan(first, lines, u, "right", region if tables else None, lay, (px, area.y1))))
        if options:
            return max(options, key=lambda o: o[0])[1]
    return None


class DoesNotFit(ValueError):
    pass


def fit_pages(t: DiagramType, area: Region, th: float) -> list[PagePlan]:
    """Split the strings into the fewest pages (halving, whole MPPTs together) that each fit at a readable size."""
    ipm = t.mppt.inputs_per_mppt if t.mppt else 1

    def solve(first: int, lines: tuple[StringLine, ...]) -> list[PagePlan]:
        plan = _choose(t, first, lines, area, th)
        if plan is not None:
            return [plan]
        if len(lines) <= ipm:
            raise DoesNotFit(
                f"string wiring diagram {t.label} {t.tag_range}: {len(lines)} string(s) of one MPPT do not fit the "
                f"{area.width:.0f} x {area.height:.0f} mm drawing area at the smallest readable text size"
            )
        half = max(ipm, (len(lines) // 2 // ipm) * ipm) if len(lines) > 2 * ipm else ipm
        return solve(first, lines[:half]) + solve(first + half, lines[half:])

    return solve(0, t.lines)


def notes_for(t: DiagramType) -> tuple[str, ...]:
    out = [
        "MODULES ARE DRAWN IN SERIES: FIRST 3 AND LAST 2 OF EACH STRING WITH THE TOTAL BETWEEN. (+) IS AT MODULE 1, (-) AT THE LAST MODULE.",
        CABLE_ID_NOTE,
    ]
    if t.has_layout:
        out.append(
            f"TABLE POSITIONS ARE THE SLOT ORDER OF THE ARRAY LAYOUT STRING ASSIGNMENT (LEFT TO RIGHT WITHIN EACH SPAN), SHOWN FOR {t.representative.tag}. "
            "OTHER INSTANCES ARE WIRED THE SAME; THEIR STRUCTURE ROW AND NUMBER ARE IN THEIR CABLE IDS."
        )
    out += [n.upper() for n in t.notes]
    return tuple(out)


def parameters_for(t: DiagramType) -> tuple[tuple[str, str], ...]:
    m, inv = t.module, t.inverter
    rows = [
        ("TYPICAL FOR", t.typical),
        ("MODULE", f"{m.pmax:g} WP {m.model}".upper()),
        ("MODULES IN SERIES", str(t.lines[0].modules)),
        ("STRINGS SHOWN", f"{len(t.lines)} PER {t.representative.tag}"),
        (f"VOC AT {t.tmin_c:g} DEG C", f"{t.lines[0].voc_cold_v:.1f} V (LIMIT {t.voc_limit_v:g} V)"),
        ("STRING ISC", f"{m.isc:g} A"),
        ("STRING CABLE", t.string_callout.upper()),
        ("STRING FUSE", (t.fuse.label.upper() + ("" if t.fuse.modelled else " (PROPOSED)")) if t.fuse else f"NONE (AT MOST {t.mppt.inputs_per_mppt} PER MPPT)" if t.mppt else "NONE"),
    ]
    if t.kind == "inverter" and t.mppt:
        rows += [("INVERTER", f"{inv.ac_rated_kva:g} KVA {inv.model}".upper()), ("MPPT", f"{t.mppt.used} OF {t.mppt.mppt_count} USED")]
    elif t.output:
        rows += [("INVERTER", f"{t.inverter_tags} {inv.ac_rated_kva:g} KVA {inv.model}".upper()), ("DC OUTPUT CABLE", t.output.callout.upper())]
    return tuple(rows)


def legend_for(t: DiagramType) -> tuple[tuple[str, str], ...]:
    keys = ["pv.module"]
    if t.fuse is not None:
        keys.append("dc.fuse")
    if t.kind == "inverter":
        keys.append(t.inverter.symbol or "inv.string")
    return tuple((k, REGISTRY[k].title.upper()) for k in keys if k in REGISTRY)


GROWTH = (PaperSize.A4, PaperSize.A3, PaperSize.A2, PaperSize.A1, PaperSize.A0)


def _start_paper(project: Project, pool: list[Sheet]) -> PaperSize:
    if pool:
        return PaperSize(pool[0].paper)
    return PaperSize.A1 if project.segment in LARGE_SEGMENTS else PaperSize.A3


def paper_and_pages(types: list[DiagramType], start: PaperSize) -> tuple[PaperSize, list[list[PagePlan]]]:
    """The smallest paper from `start` up to A0 on which every type's strings fit (split over pages), with those pages."""
    error: DoesNotFit | None = None
    for size in GROWTH[GROWTH.index(start) :]:
        m = metrics_for(size)
        try:
            return size, [fit_pages(t, m.drawing_area, m.th) for t in types]
        except DoesNotFit as exc:
            error = exc
    raise DoesNotFit(f"{error} even on A0; check the inverter's inputs per MPPT in the catalog, or draw this diagram by hand") from error


def sheet_spec(
    project: Project, profile: CompanyProfile, paper: PaperSize, title: str, number: str, sheet_no: int, sheet_count: int, t: DiagramType,
    drawn_on: date | None = None,
) -> SheetSpec:
    revs = project.revisions
    latest = revs[-1] if revs else None
    issued = IssuedFor[latest.issued_for.name] if latest and latest.issued_for else IssuedFor.INFORMATION
    site = project.site
    tb = TitleBlockData(
        client=prose(project.client.name), project_title=prose(project.name),
        location=prose(compose_location(site.name, site.address, site.state)), drawing_title=title, drawing_number=number,
        revision=latest.rev if latest else "00", scale="NTS", sheet_no=sheet_no, sheet_count=sheet_count, paper=paper,
        drawn_by=(latest.drawn or "") if latest else "", checked_by=(latest.checked or "") if latest else "",
        approved_by=(latest.approved or "") if latest else "", drawn_date=latest.date if latest else drawn_on, issued_for=issued,
    )  # fmt: skip
    return SheetSpec(
        paper=paper, title_block=tb, legend=legend_for(t), notes=notes_for(t), parameters=parameters_for(t),
        revisions=tuple(Revision(rev=r.rev, date=r.date, description=prose(r.remarks), by=r.drawn or "", checked=r.checked or "", approved=r.approved or "") for r in revs),
    )  # fmt: skip


def build_string_diagram_sheets(
    project: Project, catalog: Catalog, profile: CompanyProfile, out_dir: Path | None = None, *, paper: PaperSize | None = None, first_seq: int = FIRST_SEQ,
    drawn_on: date | None = None,
) -> list[SheetResult]:
    """One sheet per distinct inverter or SCB type that strings feed, more when a type has too many strings for one sheet.

    Sheet numbers come from the project's string_diagram sheets in order, else from the profile pattern from `first_seq`. With `out_dir`
    each sheet is saved there as `<project id>-strings-<type>[-<page>].dxf` with deterministic bytes. Returns [] when no strings are wired.
    """
    types, problems = plan_types(project, catalog)
    pool = [s for s in project.sheets if s.kind is SheetKind.STRING_DIAGRAM]
    if paper is not None:
        size, m = paper, metrics_for(paper)
        plans = [(t, fit_pages(t, m.drawing_area, m.th)) for t in types]
    else:
        size, pages = paper_and_pages(types, _start_paper(project, pool))
        plans = list(zip(types, pages, strict=True))
    total = sum(len(p) for _, p in plans)
    results: list[SheetResult] = []
    seq = 0
    for t, pages in plans:
        for page_no, page in enumerate(pages, start=1):
            title = f"STRING WIRING DIAGRAM : {t.label} {t.tag_range}" + (f" ({page_no}/{len(pages)})" if len(pages) > 1 else "")
            number = (
                pool[seq].number if seq < len(pool)
                else profile.drawing_number(client=project.client.code or "CLIENT", project=project.id.upper(), discipline="electrical", seq=first_seq + seq)
            )  # fmt: skip
            seq += 1
            doc = ezdxf.new("R2018", setup=["linetypes"])
            spec = sheet_spec(project, profile, size, prose(title), number, seq, total, t, drawn_on)
            rendered = render_sheet(doc, spec, profile, legend_drawer(doc.modelspace()))
            ink = draw.Ink(doc, profile.text_style)
            box = rendered.drawing_area
            draw.draw_diagram(ink, t, page.lines, box.x0, box.y1, page.u, (page_no, len(pages)))
            draw.draw_table(ink, t, page.lines, page.table_at[0], page.table_at[1], page.u)
            if page.plan_layout and page.plan_region:
                draw.draw_plan(ink, page.lines, draw.page_tables(t, page.lines), t.table_marks, page.plan_region, page.plan_layout, page.u)
            warnings = [*rendered.warnings, *problems, *_overlaps(ink.texts)]
            warnings += [f"text {p.tag!r} leaves the drawing area" for p in ink.texts if not box.contains(p.box, tol=1e-3)]
            suffix = f"-{page_no}" if len(pages) > 1 else ""
            path = None
            if out_dir is not None:
                out_dir.mkdir(parents=True, exist_ok=True)
                path = out_dir / f"{project.id}-strings-{t.sink}{suffix}.dxf"
                save_deterministic(doc, path)
            a, b = page.first, page.first + len(page.lines)
            results.append(SheetResult(
                key=f"{t.sink}{suffix}", title=title, number=number, paper=size, doc=doc, warnings=tuple(dict.fromkeys(warnings)), path=path,
                sink_template=t.sink, instances=tuple(m.instance for m in t.members), strings=tuple(ln.instance for ln in page.lines),
                covers=tuple(s for m in t.members for s in m.strings[a:b]), texts=tuple(ink.texts), drawing_area=box, u=page.u,
            ))  # fmt: skip
    return results


def _overlaps(texts: list[draw.Placed]) -> list[str]:
    out = []
    for i, a in enumerate(texts):
        for b in texts[i + 1 :]:
            if a.box.overlaps(b.box, tol=1e-3):
                out.append(f"text {a.tag!r} overlaps {b.tag!r}")
    return out

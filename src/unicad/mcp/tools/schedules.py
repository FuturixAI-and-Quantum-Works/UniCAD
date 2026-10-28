from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from unicad.domain.project import Project
from unicad.drafting.sld.sheet import save_deterministic
from unicad.drafting.tables import TableRender
from unicad.mcp.errors import guarded
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Workspace
from unicad.render import LibreCadRenderer, RenderError, Sheet
from unicad.routing import RouteAssumptions
from unicad.schedules import LengthMode, Schedule, build_bom, build_schedule, write_bom_xlsx, write_schedule_xlsx
from unicad.schedules.bom import DEFAULT_WASTAGE_PCT
from unicad.schedules.dxf import render_bom, render_schedule
from unicad.rules.thresholds import using
from unicad.tenants.store import load_profile, thresholds_of

Format = Annotated[Literal["xlsx", "dxf", "pdf"], Field(description="xlsx: one row per cable or item; dxf: company-sheet pages, one DXF each; pdf: those pages printed through LibreCAD into one PDF")]
OutDir = Annotated[str | None, Field(description="Directory to write into; default <workspace>/<project_id>/exports")]
Lengths = Annotated[
    Literal["auto", "network", "estimated"],
    Field(description="auto: use the lengths the network states and estimate the edges that state none from the array layout; network: only stated lengths; estimated: replace stated lengths with layout estimates wherever the layout allows"),
]
Metres = Annotated[float, Field(ge=0)]


def _assumptions(drop: float, termination: float, x: float | None, y: float | None) -> RouteAssumptions:
    if (x is None) != (y is None):
        raise ValueError("pass switchyard_x and switchyard_y together")
    return RouteAssumptions(drop, termination, None if x is None else (x, y))


def _write_pages(render: TableRender, out: Path, stem: str, titles: list[str], fmt: str) -> list[Path]:
    pages = []
    for i, doc in enumerate(render.docs, start=1):
        path = out / f"{stem}-p{i:02d}.dxf"
        save_deterministic(doc, path)
        pages.append(path)
    if fmt == "dxf":
        return pages
    sheets = [Sheet(p, paper.value, titles[part]) for p, part, paper in zip(pages, render.parts, render.papers, strict=True)]
    try:
        return [LibreCadRenderer().build_set(sheets, out / f"{stem}.pdf").pdf]
    except RenderError as e:
        raise ValueError(f"cannot print the PDF: {e}") from e


def _summary(project: Project, schedule: Schedule) -> dict[str, Any]:
    sources = Counter(r.length_source for r in schedule.rows)
    return {
        "rows": len(schedule.rows),
        "sections": [{"title": s.title, "rows": len(s.rows)} for s in schedule.sections],
        "totals": schedule.totals(),
        "length_sources": dict(sources),
        "voltage_drop": dict(schedule.vd_summary),
        "template_lengths": {k: {"instances": t.count, "min_m": round(t.minimum_m, 2), "mean_m": round(t.mean_m, 2), "max_m": round(t.maximum_m, 2)} for k, t in schedule.template_lengths.items()},
        "gaps": list(schedule.gaps),
        "warnings": list(schedule.warnings),
        "assumptions": list(schedule.assumptions),
    }


def register(server: MCPServer, ws: Workspace) -> None:
    def out_path(project_id: str, out_dir: str | None) -> Path:
        out = Path(out_dir).expanduser() if out_dir else ws.config.workspace / project_id / "exports"
        out.mkdir(parents=True, exist_ok=True)
        return out.resolve()

    @server.tool()
    @guarded
    def cable_schedule(
        project_id: ProjectIdArg,
        format: Format = "xlsx",
        out_dir: OutDir = None,
        lengths: Lengths = "auto",
        detail: Annotated[Literal["auto", "full", "summary"], Field(description="DXF and PDF only: list every string (full) or one row per combiner (summary); auto lists every string up to 150 strings. The XLSX always lists every cable")] = "auto",
        drop_per_end_m: Annotated[Metres, Field(description="Vertical drop added at each cable end when a length is estimated (default 1.5 m, reported as an assumption)")] = 1.5,
        termination_m: Annotated[Metres, Field(description="Termination slack added at each cable end when a length is estimated (default 1.0 m, reported as an assumption)")] = 1.0,
        switchyard_x: Annotated[float | None, Field(description="Local x (m) of the MV switchyard, which the project does not locate; default the centroid of the block stations")] = None,
        switchyard_y: Annotated[float | None, Field(description="Local y (m) of the MV switchyard")] = None,
        revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """Write the cable schedule: one row per cable with Indian-EPC positional id, from/to, cable, runs, +ve/-ve lengths, laying, design current, derated ampacity, % voltage drop and power loss.

        Voltage drop, current and ampacity come from the rules engine, so they match `validate`. Lengths the network states are used as given; the rest are estimated from the array layout
        (Manhattan route along the table rows plus drops and termination allowance, all reported as assumptions). Units: lengths and `cable_m` totals in metres, design current Ib and ampacity Iz in A, voltage drop in %, power loss in W.
        When the network's stored length is the worst-case route the cable was sized for (as network_autobuild stores it), each cable row carries its own routed length from the layout, not that worst case.
        Returns the file paths, totals, the worst-path voltage drop, the length source counts and any cable the schedule could not complete.
        Example: cable_schedule(project_id="rooftop-500kwp", format="pdf", lengths="estimated")."""
        project, rev = ws.load(project_id, revision)
        with using(thresholds_of(project.company_profile, ws.config.workspace)):
            schedule = build_schedule(project, ws.catalog, LengthMode(lengths), _assumptions(drop_per_end_m, termination_m, switchyard_x, switchyard_y))
        if not schedule.rows:
            raise ValueError("the project has no cabled power connections to schedule; give network edges a cable and a length")
        out = out_path(project_id, out_dir)
        stem = f"{project_id}-cable-schedule"
        result: dict[str, Any] = {"project_id": project_id, "revision": rev, "format": format}
        if format == "xlsx":
            files = [write_schedule_xlsx(schedule, out / f"{stem}.xlsx")]
        else:
            render, titles = render_schedule(project, schedule, load_profile(project.company_profile, ws.config.workspace), detail)
            files = _write_pages(render, out, stem, titles, format)
            result |= {"pages": render.pages, "layout_warnings": list(render.warnings)}
        return result | {"files": [str(f) for f in files]} | _summary(project, schedule)

    @server.tool()
    @guarded
    def bom(
        project_id: ProjectIdArg,
        format: Format = "xlsx",
        out_dir: OutDir = None,
        wastage_pct: Annotated[float, Field(ge=0, le=50, description="Cable wastage added to the routed metres (default 5 %, reported as an assumption)")] = DEFAULT_WASTAGE_PCT,
        lengths: Lengths = "auto",
        revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """Write the bill of materials: modules, inverters, combiner boxes, transformers, breakers, isolators, SPDs, meters and panels from the network expansion, cable metres by catalog type with wastage,
        and MC4 connectors, lugs, glands and MV termination kits counted per termination of the cable schedule. When the project has earthing and lightning designs, it also lists their material as those designs count it:
        earth pits by type (nos), GS flat by size and use (m, plan length), ESE terminals and their masts (nos).

        Units: cable in metres, equipment, pits, terminals and masts in nos, MC4 in pairs, MV termination kits in sets. Returns the file paths, the headline quantities and the assumptions behind every derived count.
        Cable metres use the same `lengths` basis as cable_schedule.
        Example: bom(project_id="utility-50mwac", format="xlsx", wastage_pct=5)."""
        project, rev = ws.load(project_id, revision)
        with using(thresholds_of(project.company_profile, ws.config.workspace)):
            schedule = build_schedule(project, ws.catalog, LengthMode(lengths))
            bill = build_bom(project, ws.catalog, schedule, wastage_pct)
        out = out_path(project_id, out_dir)
        stem = f"{project_id}-bom"
        result: dict[str, Any] = {"project_id": project_id, "revision": rev, "format": format}
        if format == "xlsx":
            files = [write_bom_xlsx(bill, out / f"{stem}.xlsx")]
        else:
            render = render_bom(project, bill, load_profile(project.company_profile, ws.config.workspace))
            files = _write_pages(render, out, stem, ["BILL OF MATERIALS"], format)
            result |= {"pages": render.pages, "layout_warnings": list(render.warnings)}
        return result | {"files": [str(f) for f in files], "summary": bill.summary(), "warnings": list(bill.warnings), "assumptions": list(bill.assumptions)}


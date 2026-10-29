"""Plan sheets drawn from the stored site and array layout: the array layout, the earthing layout and the lightning protection layout."""

import importlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import ezdxf

from unicad.arrays.geometry import frame
from unicad.catalog.store import Catalog
from unicad.domain.layout import ArrayLayout, PlacedEquipment
from unicad.domain.network import NodeKind
from unicad.domain.project import Project, Segment, Sheet, SheetKind
from unicad.domain.site import FeatureKind
from unicad.drafting.earthing_plan import earthing_notes, lps_notes, render_earthing_sheet, render_lps_sheet
from unicad.drafting.sheets.model import CompanyProfile
from unicad.drafting.site_plan import EquipmentArea, LayoutDrawing, render_layout_sheet
from unicad.drafting.site_plan.sheet import CableRoute, with_current_equipment
from unicad.drafting.sld import annotate
from unicad.earthing import EarthingDesign, EarthingError, design_earthing
from unicad.lps import design_lps
from unicad.sets.common import default_paper, sheet_spec
from unicad.sets.numbering import Numberer
from unicad.sets.registry import BuiltSheet, register

ROADS_NOT_STORED = "INTERNAL ROADS PLANNED WITH THE ARRAY ARE NOT STORED IN THE PROJECT AND ARE NOT DRAWN; ROADS IMPORTED WITH THE SITE ARE."


_last: list[tuple[Project, EarthingDesign]] = []


def earthing_of(project: Project) -> EarthingDesign:
    """The project's earthing design, kept for the next call (the earthing, layout and LPS sheets of one set share it)."""
    if _last and _last[0][0] is project:
        return _last[0][1]
    d = design_earthing(project)
    _last[:] = [(project, d)]
    return d


def plant_areas(project: Project) -> tuple[EquipmentArea, ...]:
    """The MV switchyard where the earthing design placed its mat, for the layout and LPS sheets to show at the same place."""
    if _no_earthing(project):
        return ()
    try:
        sy = earthing_of(project).layout.switchyard
    except EarthingError:
        return ()
    if sy is None:
        return ()
    name = f"MV SWITCHYARD ({sy.x1 - sy.x0:g} x {sy.y1 - sy.y0:g} m)"
    return (EquipmentArea(name, ((sy.x0, sy.y0), (sy.x1, sy.y0), (sy.x1, sy.y1), (sy.x0, sy.y1))),)


def row_pitch(layout: ArrayLayout) -> float | None:
    """The most common spacing between table rows, along the direction the tables face."""
    tables = list(layout.table_by_id.values())
    if not tables:
        return None
    _, (vx, vy) = frame(tables[0].azimuth_deg)
    rows = sorted({round(t.origin.x * vx + t.origin.y * vy, 3) for t in tables})
    steps = Counter(round(b - a, 2) for a, b in zip(rows, rows[1:]) if b - a > 0.01)
    return steps.most_common(1)[0][0] if steps else None


def _no_layout(project: Project) -> str | None:
    if project.site.geometry is None:
        return "the project has no site geometry; run site_import"
    if project.array_layout is None or not project.array_layout.table_by_id:
        return "the project has no array layout; run layout_place"
    return None


def _module(project: Project, catalog: Catalog):
    return catalog.get(next(iter(project.array_layout.table_by_id.values())).module)


def _one_sheet(numbers: Numberer, kind: SheetKind, block: str, sid: str, title: str, paper: str) -> Sheet:
    old = numbers.reuse(kind.value)
    return Sheet(id=sid, number=old.number if old else numbers.fresh(block), kind=kind, title=old.title if old else title,
                 paper=old.paper if old else paper, revisions=old.revisions if old else ())  # fmt: skip


STATION_KINDS = frozenset({NodeKind.INVERTER, NodeKind.PCS, NodeKind.TRANSFORMER, NodeKind.HT_PANEL, NodeKind.RMU, NodeKind.VCB})
"""Equipment that stands at a block's inverter station unless the layout places it in the field."""
LT_PANEL_KINDS = frozenset({NodeKind.ACDB, NodeKind.LT_PANEL})


def has_stations(project: Project) -> bool:
    """Whether any block station holds equipment: some inverter, PCS, transformer or MV switchgear instance the layout does not place in
    the field. String inverters beside their tables feeding an LT panel leave the stations empty."""
    net, placed = project.network, project.array_layout.equipment_by_node
    return any(i not in placed for n in net.nodes if n.kind in STATION_KINDS for i in net.instance_ids(n.id))


def main_lt_panel(project: Project):
    """The one LT panel of a plant without transformers, which stands at the layout's main LT panel location."""
    net = project.network
    if net.by_kind(NodeKind.TRANSFORMER):
        return None
    panels = [n for n in net.nodes if n.kind in LT_PANEL_KINDS and net.instance_count(n.id) == 1]
    return panels[0] if len(panels) == 1 else None


def drawn_layout(project: Project) -> ArrayLayout:
    """The layout as the sheet draws it: no block stations where nothing stands at them, and on an LT plant the main LT panel at its
    location when the layout does not place it itself."""
    layout = with_current_equipment(project.array_layout, project.network)
    if not has_stations(project):
        layout = layout.model_copy(update={"blocks": tuple(b.model_copy(update={"station": None}) for b in layout.blocks)})
    panel = main_lt_panel(project)
    sy = layout.switchyard
    if panel is not None and sy is not None and not any(e.kind in {k.value for k in LT_PANEL_KINDS} for e in layout.equipment):
        tag = annotate.node_tag(project.network, panel)
        layout = layout.model_copy(update={"equipment": (*layout.equipment, PlacedEquipment(node=panel.id, tag=tag, kind=panel.kind.value, at=sy.at, basis=sy.basis))})
    return layout


def panel_routes(project: Project, catalog: Catalog | None) -> tuple[CableRoute, ...]:
    """Cable routes between the panels the layout places, from the routing package when it provides them."""
    routes = getattr(importlib.import_module("unicad.routing"), "panel_routes", None)
    if routes is None:
        return ()
    net = project.network.expand()
    out = []
    for r in routes(project):
        edge = net.edge_by_id.get(r.edge)
        cable = annotate.cable_text(edge, catalog, net) if edge is not None else ""
        out.append(CableRoute(f"{r.from_tag} TO {r.to_tag}" + (f": {cable}" if cable else "") + f", ~{r.length_m:.0f} m", tuple(r.points)))
    return tuple(out)


def layout_notes(project: Project, pitch: float | None) -> tuple[str, ...]:
    layout = drawn_layout(project)
    tables = list(layout.table_by_id.values())
    t = tables[0]
    modules = sum(x.rows * x.cols - len(x.unused) for x in tables)
    full = max(x.cols for x in tables)
    site = project.site.geometry
    notes = [
        "ALL DIMENSIONS ARE IN METRES UNLESS OTHERWISE STATED.",
        f"{t.mounting.value.replace('_', ' ').upper()} TABLE {t.rows}{t.orientation.value[0].upper()} x {full}, TILT {t.tilt_deg:g} DEG, FACING AZIMUTH {t.azimuth_deg:g} DEG.",
        f"ROW PITCH {pitch:.2f} m (MOST COMMON ROW SPACING OF THE STORED LAYOUT)." if pitch else "ROW PITCH: ONE TABLE ROW, NO PITCH.",
        f"{modules:,} MODULES ON {len(tables)} TABLES IN {len(layout.blocks)} BLOCK{'S' if len(layout.blocks) != 1 else ''}.",
        f"SITE GRID {site.crs}, LOCAL ORIGIN {site.origin.lon:.5f} E, {site.origin.lat:.5f} N. NORTH IS GRID NORTH.",
    ]
    if any(b.station for b in layout.blocks):
        notes.append("INVERTER STATION LOCATIONS ARE INDICATIVE AND SUBJECT TO DETAILED ENGINEERING.")
    if project.segment not in (Segment.CI_ROOFTOP, Segment.RESIDENTIAL):
        notes.append(ROADS_NOT_STORED)
    return tuple(notes)


@dataclass(frozen=True)
class LayoutBuilder:
    def missing(self, project: Project, catalog: Catalog | None) -> str | None:
        return _no_layout(project)

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer) -> tuple[Sheet, ...]:
        if _no_layout(project):
            return ()
        rooftop = project.segment in (Segment.CI_ROOFTOP, Segment.RESIDENTIAL)
        title = "ROOFTOP SOLAR PV MODULE LAYOUT" if rooftop else "SOLAR PV ARRAY LAYOUT"
        return (_one_sheet(numbers, SheetKind.LAYOUT, "layout", "layout", title, default_paper(project, "A1", "A3")),)

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None, *, drawn_on: date | None = None) -> list[BuiltSheet]:  # fmt: skip
        if not sheets:
            return []
        out = []
        layout = project.array_layout
        pitch = row_pitch(layout)
        feats = {f.kind for f in project.site.geometry.features}
        view = "roofs" if project.segment in (Segment.CI_ROOFTOP, Segment.RESIDENTIAL) and FeatureKind.ROOF in feats else "site"
        for i, s in enumerate(sheets, start=1):
            drawing = LayoutDrawing(project.site.geometry, drawn_layout(project), _module(project, catalog), max(t.cols for t in layout.table_by_id.values()), pitch,
                                    notes=layout_notes(project, pitch), areas=plant_areas(project), routes=panel_routes(project, catalog))  # fmt: skip
            doc = ezdxf.new("R2018", setup=True)
            res = render_layout_sheet(doc, drawing, sheet_spec(project, s, i, len(sheets), drawn_on=drawn_on, kind="layout"), profile, view=view)  # type: ignore[arg-type]
            warnings = list(res.warnings)
            if view == "site":
                warnings.append("internal roads are not stored with the array layout, so the sheet shows only the roads imported with the site")
            out.append(BuiltSheet(s.model_copy(update={"scale": f"1:{res.scale}"}), doc, None, tuple(warnings), {"view": view}))
        return out


def _no_earthing(project: Project) -> str | None:
    if project.design_basis.earthing is None:
        return "the design basis has no earthing inputs; set them with project_design_basis or earthing_design"
    return _no_layout(project)


def _no_lps(project: Project) -> str | None:
    if project.design_basis.lps is None:
        return "the design basis has no lightning protection inputs; set them with project_design_basis or lps_design"
    return _no_layout(project)


@dataclass(frozen=True)
class EarthingBuilder:
    def missing(self, project: Project, catalog: Catalog | None) -> str | None:
        return _no_earthing(project)

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer) -> tuple[Sheet, ...]:
        if _no_earthing(project):
            return ()
        return (_one_sheet(numbers, SheetKind.EARTHING, "earthing", "earthing", "EARTHING LAYOUT", default_paper(project, "A1", "A2")),)

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None, *, drawn_on: date | None = None) -> list[BuiltSheet]:  # fmt: skip
        if not sheets:
            return []
        d = earthing_of(project)
        out = []
        for i, s in enumerate(sheets, start=1):
            doc = ezdxf.new("R2018", setup=True)
            res = render_earthing_sheet(doc, project, catalog, d, sheet_spec(project, s, i, len(sheets), notes=earthing_notes(d), drawn_on=drawn_on, kind="earthing"), profile)
            meta = {"pits": len(d.layout.pits), "resistance_ohm": round(d.resistance_ohm, 3), "target_ohm": d.target_ohm, "unbonded": len(d.unbonded)}
            out.append(BuiltSheet(s.model_copy(update={"scale": f"1:{res.scale}"}), doc, None, tuple(dict.fromkeys([*d.warnings, *res.warnings])), meta))
        return out


@dataclass(frozen=True)
class LpsBuilder:
    def missing(self, project: Project, catalog: Catalog | None) -> str | None:
        return _no_lps(project)

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer) -> tuple[Sheet, ...]:
        if _no_lps(project):
            return ()
        return (_one_sheet(numbers, SheetKind.LPS, "lps", "lps", "LIGHTNING ARRESTER (LA) LAYOUT", default_paper(project, "A1", "A3")),)

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None, *, drawn_on: date | None = None) -> list[BuiltSheet]:  # fmt: skip
        if not sheets:
            return []
        d = design_lps(project)
        out = []
        for i, s in enumerate(sheets, start=1):
            doc = ezdxf.new("R2018", setup=True)
            res = render_lps_sheet(doc, project, catalog, d, sheet_spec(project, s, i, len(sheets), notes=lps_notes(d), drawn_on=drawn_on, kind="lps"), profile,
                                   areas=plant_areas(project))  # fmt: skip
            meta = {"terminals": len(d.terminals), "radius_m": d.radius_m, "uncovered": len(d.uncovered)}
            out.append(BuiltSheet(s.model_copy(update={"scale": f"1:{res.scale}"}), doc, None, tuple(res.warnings), meta))
        return out


register("layout", LayoutBuilder())
register("earthing", EarthingBuilder())
register("lps", LpsBuilder())

"""Earthing layout and lightning protection (LA) layout sheets, drawn over a greyed site and array underlay at a true scale.

Earthing: conductors by GS flat size, each size with its own linetype and colour as on the reference earthing layouts, numbered
pits (treated, untreated, lightning), the earthing BOQ, the design calculation summary and the typical treated / untreated pit
sections. LA layout: terminals with their protection circles, the terminal schedule and the coverage proof.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ezdxf.document import Drawing
from shapely.geometry import MultiPoint, box
from shapely.geometry import Point as SPoint
from shapely.ops import unary_union
from unicad.arrays.geometry import table_corners
from unicad.catalog.store import Catalog
from unicad.domain.catalog import ModuleSpec
from unicad.domain.project import Project
from unicad.domain.site import FeatureKind, shape_of
from unicad.drafting.sheets.canvas import Canvas, Region
from unicad.drafting.sheets.model import CompanyProfile, LayerRole, SheetSpec
from unicad.drafting.sheets.render import RenderedSheet, render_sheet
from unicad.drafting.sheets.text import text_width
from unicad.drafting.site_plan.sheet import LayoutDrawing, LayoutSheetWriter, _Plan, _view_extent, choose_scale
from unicad.earthing import ConductorRole, EarthingDesign, PitRole, pit_text
from unicad.earthing.design import ROLE_TEXT
from unicad.earthing.sizing import FLAT_BY_NAME
from unicad.lps import LpsDesign

TXT = LayerRole.TEXT
UNDERLAY = 8
UNDERLAY_LIGHT = 9


@dataclass(frozen=True)
class Style:
    colour: int
    linetype: str
    lineweight: int


# Each conductor (size and use) has its own linetype and colour, as in the CIL/Refex earthing BOQ: green dash-dot for the main
# grid and trench conductor, magenta dash-dot for structure / LA / equipment earthing, cyan dashed for the MMS rows, light orange
# dashed for down conductors. LibreCAD draws only its built-in linetype names (docs/compat.md).
ROLE_STYLE = {
    ConductorRole.MAIN: Style(3, "DASHDOT", 50),
    ConductorRole.GRID: Style(3, "CONTINUOUS", 25),
    ConductorRole.EQUIPMENT: Style(6, "DASHDOT2", 35),
    ConductorRole.ARRAY: Style(4, "DASHED", 25),
    ConductorRole.DOWN: Style(40, "DASHED2", 35),
}
PIT_COLOUR = {True: 30, False: 6}
LE_COLOUR = 4
LPS_COLOUR = 1
PIT_DESC = {
    PitRole.EP: "EARTH ELECTRODE (TREATED)",
    PitRole.EPN: "TRANSFORMER NEUTRAL (TREATED)",
    PitRole.EPB: "TRANSFORMER BODY (TREATED)",
    PitRole.EPT: "POTENTIAL TRANSFORMER (UNTREATED)",
    PitRole.ELA: "LIGHTNING / SURGE ARRESTER (UNTREATED)",
    PitRole.LE: "LIGHTNING PROTECTION (UNTREATED)",
}
L_PIT = "E-PIT"
L_PIT_TAG = "E-PIT-TAG"
L_EQUIP = "E-EQUIPMENT"
L_YARD = "E-SWITCHYARD"
L_UNDER = "E-UNDERLAY"
L_DETAIL = "E-DETAIL"
L_DETAIL_HATCH = "E-DETAIL-HATCH"
L_LPS = "E-LPS"
L_LPS_ZONE = "E-LPS-ZONE"
GREY_SITE = {"L-BOUNDARY": (UNDERLAY, 50), "L-ROOF": (UNDERLAY, 35), "L-ROAD": (UNDERLAY_LIGHT, 18), "L-ROAD-FILL": (254, 0),
             "L-WATER": (UNDERLAY_LIGHT, 18), "L-WATER-FILL": (254, 0), "L-EXCLUSION": (UNDERLAY_LIGHT, 18), "L-EXCLUSION-FILL": (254, 0),
             "L-TLINE": (UNDERLAY_LIGHT, 18), "L-OBSTACLE": (UNDERLAY_LIGHT, 18), "L-LABEL": (UNDERLAY, 18)}  # fmt: skip


def cond_layer(size: str, role: ConductorRole) -> str:
    return f"E-COND-{size}-{role.value.upper()}"


@dataclass(frozen=True)
class EarthingSheet:
    scale: int
    sheet: RenderedSheet
    warnings: tuple[str, ...]


class _Writer(LayoutSheetWriter):
    def __init__(self, doc: Drawing, project: Project, module: ModuleSpec, sheet: SheetSpec, profile: CompanyProfile, scale: int | None):
        layout = project.array_layout
        d = LayoutDrawing(project.site.geometry, layout, module, max(t.cols for t in layout.table_by_id.values()) if layout and layout.table_by_id else 1)
        super().__init__(doc, d, sheet, profile, "site", scale)
        self.project = project

    def _grey_layers(self) -> None:
        for name, (colour, lw) in GREY_SITE.items():
            if name not in self.doc.layers:
                self.doc.layers.add(name, color=colour, lineweight=lw)
        for name, colour, lw in ((L_PIT, 7, 25), (L_PIT_TAG, 7, 18), (L_EQUIP, 7, 25), (L_YARD, 7, 35), (L_UNDER, UNDERLAY_LIGHT, 9),
                                 (L_DETAIL, 7, 25), (L_DETAIL_HATCH, 8, 9), (L_LPS, LPS_COLOUR, 35), (L_LPS_ZONE, LPS_COLOUR, 25)):  # fmt: skip
            if name not in self.doc.layers:
                self.doc.layers.add(name, color=colour, lineweight=lw)
        for size in FLAT_BY_NAME:
            for role, st in ROLE_STYLE.items():
                if cond_layer(size, role) not in self.doc.layers:
                    self.doc.layers.add(cond_layer(size, role), color=st.colour, linetype=st.linetype, lineweight=st.lineweight)

    def _extent(self) -> tuple[float, float, float, float]:
        return _view_extent(self.d, "site")

    def _start(self, legend_rows: int) -> tuple[_Plan, Region, int]:
        m, th = self.m, self.m.th
        area = m.drawing_area
        panel_w = min(0.42 * area.width, max(0.3 * area.width, 48 * th))
        panel = Region(area.x1 - panel_w, area.y0, area.x1, area.y1)
        room = Region(area.x0 + th, area.y0 + 7 * th, panel.x0 - 2 * th, area.y1 - th)
        bounds = self._extent()
        scale = self.forced_scale or choose_scale(bounds[2] - bounds[0], bounds[3] - bounds[1], room)
        tb = self.sheet.title_block.model_copy(update={"scale": f"1:{scale}"})
        spec = self.sheet.model_copy(update={"title_block": tb})
        self.rendered = render_sheet(self.doc, spec, self.profile)
        self._grey_layers()
        self._layers()
        self.cv = Canvas(self.doc, self.profile)
        P = _Plan(bounds, room, scale)
        self._site(P)
        for t in self.d.layout.table_by_id.values() if self.d.layout else ():
            self._poly(P.ring(table_corners(t)), L_UNDER)
        self.msp.add_line((panel.x0 - th, area.y0), (panel.x0 - th, area.y1), dxfattribs={"layer": self.profile.layer(LayerRole.FRAME).name})
        self._scale_bar(Region(area.x0 + th, area.y0, panel.x0 - 2 * th, area.y0 + 6 * th), scale)
        self.panel = panel
        return P, panel, scale

    def _pit_symbol(self, x: float, y: float, r: float, role: PitRole) -> None:
        colour = LE_COLOUR if role is PitRole.LE else PIT_COLOUR[role.treated]
        a = {"layer": L_PIT, "color": colour}
        self.msp.add_circle((x, y), r, dxfattribs=a)
        k = r * (0.7071 if role is not PitRole.LE else 1.0)
        if role is PitRole.LE:
            self.msp.add_line((x - k, y), (x + k, y), dxfattribs=a)
            self.msp.add_line((x, y - k), (x, y + k), dxfattribs=a)
        else:
            self.msp.add_line((x - k, y - k), (x + k, y + k), dxfattribs=a)
            self.msp.add_line((x - k, y + k), (x + k, y - k), dxfattribs=a)

    def _line_sample(self, c: Region, size: str, role: ConductorRole) -> None:
        y = c.centre[1]
        self.msp.add_line((c.x0, y), (c.x1, y), dxfattribs={"layer": cond_layer(size, role)})

    def _table(self, r: Region, title: str, heads: Sequence[str], widths: Sequence[float], rows: Sequence[Sequence[str]], h: float,
               draw_cell=None, row_h: float | None = None, head_h: float | None = None) -> float:  # fmt: skip
        th = self.m.th
        xs = [r.x0]
        for w in widths:
            xs.append(xs[-1] + w * r.width)
        cells = list(zip(xs, xs[1:]))
        title_h, head_h, row_h = 2.4 * th, head_h or 2.4 * th, row_h or 2.3 * th
        top = r.y1
        y = top
        self.cv.put(TXT, Region(r.x0, y - title_h, r.x1, y), title, 1.0 * th, align="center", valign="middle")
        y -= title_h
        self.cv.hline(TXT, y, r.x0, r.x1)
        self._row(cells, y - head_h, y, heads, 0.75 * th, max_lines=2)
        y -= head_h
        self.cv.hline(TXT, y, r.x0, r.x1)
        for i, row in enumerate(rows):
            self._row(cells, y - row_h, y, row, h, max_lines=2)
            if draw_cell is not None:
                draw_cell(i, cells, y - row_h, y)
            y -= row_h
            self.cv.hline(TXT, y, r.x0, r.x1)
        self.cv.rect(TXT, Region(r.x0, y, r.x1, top), heavy=True)
        for x in xs[1:-1]:
            self.cv.vline(TXT, x, y, top - title_h)
        return y

    def _row(self, cells, y0, y1, texts, h, colour=None, max_lines=1) -> None:
        for (x0, x1), s in zip(cells, texts):
            if not s:
                continue
            align = "left" if text_width(s, h) > 0.6 * (x1 - x0) else "center"
            self.cv.put(TXT, Region(x0, y0, x1, y1), s, h, align=align, valign="middle", max_lines=max_lines, pad_x=0.3 * self.m.th, pad_y=0.15 * self.m.th, min_h=0.45 * self.m.th)


class EarthingSheetWriter(_Writer):
    def __init__(self, doc, project, module, design: EarthingDesign, sheet, profile, scale=None):
        super().__init__(doc, project, module, sheet, profile, scale)
        self.e = design

    def _extent(self) -> tuple[float, float, float, float]:
        if not self.e.rooftop:
            return super()._extent()
        lay = self.e.layout
        pts = [(p.at.x, p.at.y) for p in lay.pits] + [(q.x, q.y) for r in lay.runs for q in r.points]
        shapes = [box(*MultiPoint(pts).bounds)] + [shape_of(f) for f in self.d.site.features if f.kind is FeatureKind.ROOF]
        return unary_union(shapes).buffer(6.0).bounds

    def render(self) -> EarthingSheet:
        P, panel, scale = self._start(0)
        th = self.m.th
        lay = self.e.layout
        if lay.switchyard is not None:
            s = lay.switchyard
            self._poly(P.ring([(s.x0, s.y0), (s.x1, s.y0), (s.x1, s.y1), (s.x0, s.y1)]), L_YARD)
            x, y = P((s.x0 + s.x1) / 2, s.y0)
            self._text(f"MV SWITCHYARD EARTH MAT, {s.spacing:.1f} m MESH", (x, y - 2.4 * th), 0.6 * th, layer=L_PIT_TAG, align="BC")
        order = {ConductorRole.ARRAY: 0, ConductorRole.GRID: 1, ConductorRole.MAIN: 2, ConductorRole.EQUIPMENT: 3, ConductorRole.DOWN: 4}
        for r in sorted(lay.runs, key=lambda r: order[r.role]):
            pts = [P(p.x, p.y) for p in r.points]
            if len(pts) == 2:
                self.msp.add_line(pts[0], pts[1], dxfattribs={"layer": cond_layer(r.size, r.role)})
            else:
                self.msp.add_lwpolyline(pts, dxfattribs={"layer": cond_layer(r.size, r.role)})
        sq = max(0.25 * th, 1.2 * P.k)
        for it in lay.items:
            if it.kind in ("ese", "air_terminal"):
                x, y = P(it.at.x, it.at.y)
                self._terminal(x, y, 0.45 * th)
                self._text(it.tag, (x + 0.6 * th, y + 0.5 * th), 0.55 * th, layer=L_PIT_TAG, align="BL", colour=LPS_COLOUR)
                continue
            x, y = P(it.at.x, it.at.y)
            self._poly([(x - sq, y - sq), (x + sq, y - sq), (x + sq, y + sq), (x - sq, y + sq)], L_EQUIP)
        r = max(0.32 * th, 0.75 * P.k)
        tag_h = 0.42 * th if len(lay.pits) > 150 else 0.5 * th
        for p in lay.pits:
            x, y = P(p.at.x, p.at.y)
            self._pit_symbol(x, y, r, p.role)
            colour = LE_COLOUR if p.role is PitRole.LE else PIT_COLOUR[p.role.treated]
            self._text(p.tag, (x + r + 0.15 * th, y + r * 0.2), tag_h, layer=L_PIT_TAG, align="BL", colour=colour)
        for b in self.d.layout.blocks if self.d.layout else ():
            if b.station is not None:
                x, y = P(b.station.x, b.station.y)
                self._text(f"{b.name} STATION", (x, y - 2.2 * th), 0.55 * th, layer=L_PIT_TAG, align="BC")
        self._panel_e(panel)
        return EarthingSheet(scale, self.rendered, tuple([*self.rendered.warnings, *self.warnings, *self.cv.warnings]))

    def _terminal(self, x: float, y: float, r: float) -> None:
        a = {"layer": L_LPS}
        self.msp.add_circle((x, y), r, dxfattribs=a)
        for i in range(8):
            ang = math.radians(45 * i + 22.5)
            self.msp.add_line((x + r * math.cos(ang), y + r * math.sin(ang)), (x + 2.2 * r * math.cos(ang), y + 2.2 * r * math.sin(ang)), dxfattribs=a)

    def _panel_e(self, panel: Region) -> None:
        th = self.m.th
        y = panel.y1
        y = self._north(Region(panel.x0, y - 9 * th, panel.x0 + 12 * th, y))
        y = panel.y1 - 9.5 * th
        y = self._boq(Region(panel.x0, panel.y0, panel.x1, y)) - 1.5 * th
        y = self._calc(Region(panel.x0, panel.y0, panel.x1, y)) - 1.5 * th
        self._details(Region(panel.x0, panel.y0, panel.x1, y))

    def _boq(self, r: Region) -> float:
        th = self.m.th
        e = self.e
        rows, draws = [], []
        for q in e.quantities:
            if q.item.startswith("pit."):
                role = PitRole(q.item.split(".")[1])
                rows.append((f"{role.value}**", f"{pit_text(role, e.basis)}: {PIT_DESC[role]}", f"{q.quantity:g} NOS."))
                draws.append(("pit", role))
            elif q.item.startswith("flat."):
                _, size, role = q.item.split(".")
                rows.append(("", f"{size} MM GS FLAT FOR {ROLE_TEXT[ConductorRole(role)]}", f"{q.quantity:,.0f} MTR."))
                draws.append(("flat", (size, ConductorRole(role))))
            else:
                rows.append(("", q.description, f"{q.quantity:g} NOS."))
                draws.append(("lps", None))

        def cell(i, cells, y0, y1):
            kind, what = draws[i]
            (x0, x1) = cells[0]
            c = Region(x0, y0, x1, y1)
            if kind == "pit":
                self._pit_symbol(x0 + 0.75 * (x1 - x0), c.centre[1], 0.5 * th, what)
            elif kind == "flat":
                self._line_sample(c.inset(0.5 * th, 0), *what)
            else:
                self._terminal(c.centre[0], c.centre[1], 0.35 * th)

        y = self._table(r, "EARTHING BOQ", ("SYMBOL", "DESCRIPTION", "QTY"), (0.16, 0.66, 0.18), rows, 0.68 * th, cell, row_h=2.6 * th)
        self.cv.put(TXT, Region(r.x0, y - 1.8 * th, r.x1, y), "(** INDICATES NUMBER. LENGTHS ARE PLAN LENGTHS, INDICATIVE)", 0.6 * th, valign="middle")
        return y - 1.8 * th

    def _calc(self, r: Region) -> float:
        th = self.m.th
        e, b = self.e, self.e.basis
        rows = [("SOIL RESISTIVITY", f"{b.soil_resistivity_ohm_m:g} OHM-M")]
        area = next((s for s in e.steps if s.name == "conductor_area"), None)
        cross = next((s for s in e.steps if s.name == "ieee80_area"), None)
        if area is not None:
            rows += [
                ("EARTH FAULT CURRENT / TIME", f"{b.fault_current_ka:g} kA / {b.fault_clearing_s:g} s"),
                ("A = I x SQRT(t) / k, k = 80 (GI, IS 3043)", f"{area.value:g} mm2"),
                ("IEEE 80 CROSS-CHECK (Kf 28.96)", f"{cross.value:g} mm2"),
            ]
        rows.append(("CONDUCTOR SELECTED", f"{e.sizes.main} GS FLAT ({FLAT_BY_NAME[e.sizes.main].area_mm2:g} mm2)"))
        rows.append(("R PIT = 100 RHO/(2 PI L) LN(4L/d)", f"{e.electrode.value:g} OHM"))
        rows.append(("PITS (BY CURRENT DENSITY)", f"{e.pit_count} ({e.pits_by_current} NEEDED)" if e.pits_by_current else f"{e.pit_count}"))
        rows.append(("EARTH RESISTANCE / TARGET", f"{e.resistance_ohm:.3g} / {e.target_ohm:g} OHM"))
        if e.grid is not None:
            g = e.grid
            rows += [
                ("SWITCHYARD GRID MESH / Rg", f"{e.grid_spacing_m:.2f} m / {g.rg:.3g} OHM"),
                ("TOUCH: Em / TOLERABLE (50 kg)", f"{g.e_mesh:,.0f} / {g.e_touch:,.0f} V"),
                ("STEP: Es / TOLERABLE (50 kg)", f"{g.e_s:,.0f} / {g.e_step:,.0f} V"),
            ]
        return self._table(r, "EARTHING DESIGN CALCULATION (SUMMARY)", ("PARAMETER", "VALUE"), (0.62, 0.38), rows, 0.68 * th, row_h=2.0 * th, head_h=2.0 * th)

    def _details(self, r: Region) -> None:
        th = self.m.th
        if r.height < 25 * th:
            self.warnings.append("no room for the earth pit sections in the side panel")
            return
        self.cv.put(TXT, Region(r.x0, r.y1 - 2.2 * th, r.x1, r.y1), "TYPICAL SECTIONS OF EARTH PIT (AS PER IS 3043, INDICATIVE)", 0.85 * th, align="center", valign="middle")
        body = Region(r.x0, r.y0, r.x1, r.y1 - 2.4 * th)
        half = body.width / 2
        for i, treated in enumerate((True, False)):
            cell = Region(body.x0 + i * half, body.y0, body.x0 + (i + 1) * half, body.y1)
            self._pit_section(cell.inset(0.3 * th), treated)
        self.cv.rect(TXT, r)

    def _pit_section(self, r: Region, treated: bool) -> None:
        """Pit section in mm: chamber 400 x 400 inside, 125 mm walls, CI hinged cover, 100 mm PVC watering conduit, electrode to depth,
        conductor 600 mm below FGL, and for a treated pit the homogeneous coke / charcoal / salt / sand fill around the electrode."""
        th = self.m.th
        b = self.e.basis
        L = b.electrode_length_m * 1000
        dia = b.electrode_diameter_mm
        depth = b.burial_depth_m * 1000
        W, D = 650.0, 700.0
        title_h = 2.0 * th
        label_w = 0.45 * r.width
        k = min((r.width - label_w) / 1400, (r.height - title_h - 3 * th) / (L + D + 300))
        cx = r.x0 + (r.width - label_w) / 2
        top = r.y1 - title_h - 1.5 * th

        def X(x: float) -> float:
            return cx + x * k

        def Y(y: float) -> float:
            return top - y * k

        ln = {"layer": L_DETAIL}
        self.cv.put(TXT, Region(r.x0, r.y1 - title_h, r.x1, r.y1), "TREATED PIT (EP/EPN/EPB)" if treated else "UNTREATED PIT (EPT/ELA/LE)", 0.7 * th, align="center", valign="middle")
        self.msp.add_line((X(-700), Y(0)), (X(700), Y(0)), dxfattribs=ln)
        self._text("FGL", (X(-700), Y(0) + 0.3 * th), 0.55 * th, layer=L_DETAIL, align="BL")
        for s in (-1, 1):
            self.msp.add_lwpolyline([(X(s * 200), Y(0)), (X(s * 200), Y(D - 125)), (X(s * W / 2), Y(D - 125)), (X(s * W / 2), Y(0))], dxfattribs=ln)
        self.msp.add_line((X(-200), Y(D - 125)), (X(200), Y(D - 125)), dxfattribs=ln)
        self.msp.add_lwpolyline([(X(-200), Y(-20)), (X(200), Y(-20)), (X(200), Y(0)), (X(-200), Y(0))], close=True, dxfattribs={"layer": L_DETAIL, "lineweight": 50})
        e0 = D - 250
        self.msp.add_lwpolyline([(X(-dia / 2), Y(e0)), (X(dia / 2), Y(e0)), (X(dia / 2), Y(e0 + L)), (X(0), Y(e0 + L + 120)), (X(-dia / 2), Y(e0 + L))], close=True, dxfattribs=ln)
        if treated:
            fill = [(X(-150), Y(D - 125)), (X(150), Y(D - 125)), (X(150), Y(e0 + L)), (X(-150), Y(e0 + L))]
            h = self.msp.add_hatch(dxfattribs={"layer": L_DETAIL_HATCH, "color": 256})
            h.set_pattern_fill("ANSI31", scale=0.4 * th, color=256)
            h.paths.add_polyline_path(fill, is_closed=True)
            self.msp.add_lwpolyline(fill, close=True, dxfattribs={"layer": L_DETAIL_HATCH})
        self.msp.add_lwpolyline([(X(120), Y(0)), (X(120), Y(D - 125)), (X(220), Y(D - 125)), (X(220), Y(0))], dxfattribs=ln)
        self.msp.add_lwpolyline([(X(dia / 2), Y(depth)), (X(700), Y(depth))], dxfattribs={"layer": cond_layer(self.e.sizes.main, ConductorRole.MAIN)})
        lx = r.x1 - label_w + 0.4 * th
        labels = [
            (Y(0) + 0.0, "CI COVER HINGED, 400 x 400"),
            (Y(D - 300), f"{self.e.sizes.main} GS FLAT TO MAIN EARTH GRID, {depth:g} BELOW FGL"),
            (Y(D + 200), "100 DIA PVC CONDUIT (WATERING)"),
            (Y(e0 + L * 0.45), f"{dia:g} DIA x {L:g} LONG {'GS PIPE' if treated else 'MS ROD'}"),
        ]
        if treated:
            labels.append((Y(e0 + L * 0.8), "HOMOGENEOUS LAYER OF COKE, CHARCOAL, SALT AND SAND"))
        anchor = {0: (X(200), Y(0)), 1: (X(700), Y(depth)), 2: (X(170), Y(D - 125)), 3: (X(dia / 2), Y(e0 + L * 0.45)), 4: (X(150), Y(e0 + L * 0.8))}
        ys = []
        for i, (yy, s) in enumerate(labels):
            yy = min(yy, ys[-1] - 2.4 * th) if ys else yy
            ys.append(yy)
            self.msp.add_line(anchor[i], (lx - 0.2 * th, yy), dxfattribs={"layer": L_DETAIL})
            self.cv.put(TXT, Region(lx, yy - 1.2 * th, r.x1, yy + 1.2 * th), s, 0.55 * th, valign="middle", max_lines=2, min_h=0.4 * th)
        self._dim_v(X(-W / 2) - 0.8 * th, Y(e0 + L), Y(e0), f"{L:g}", left=True)


class LpsSheetWriter(_Writer):
    def __init__(self, doc, project, module, lps: LpsDesign, sheet, profile, scale=None):
        super().__init__(doc, project, module, sheet, profile, scale)
        self.lps = lps

    def _extent(self) -> tuple[float, float, float, float]:
        if not any(t.on_roof for t in self.lps.terminals):
            return super()._extent()
        shapes = [SPoint(t.at.x, t.at.y).buffer(self.lps.radius_m) for t in self.lps.terminals]
        shapes += [shape_of(f) for f in self.d.site.features if f.kind in (FeatureKind.ROOF, FeatureKind.BOUNDARY)]
        return unary_union(shapes).buffer(3.0).bounds

    def render(self) -> EarthingSheet:
        P, panel, scale = self._start(0)
        th = self.m.th
        d = self.lps
        taken: list[Region] = []
        h = 0.6 * th
        for t in d.terminals:
            x, y = P(t.at.x, t.at.y)
            self.msp.add_circle((x, y), d.radius_m * P.k, dxfattribs={"layer": L_LPS_ZONE})
            EarthingSheetWriter._terminal(self, x, y, 0.45 * th)
        for t in d.terminals:
            x, y = P(t.at.x, t.at.y)
            lines = (t.tag, f"RADIUS COVERED {d.radius_m:g} m")
            w = max(text_width(s, h) for s in lines) + 0.4 * th
            hh = 2.6 * h
            opts = [Region(x + th, y + 0.2 * th, x + th + w, y + 0.2 * th + hh), Region(x + th, y - 0.2 * th - hh, x + th + w, y - 0.2 * th),
                    Region(x - th - w, y + 0.2 * th, x - th, y + 0.2 * th + hh), Region(x - th - w, y - 0.2 * th - hh, x - th, y - 0.2 * th)]  # fmt: skip
            box_ = next((o for o in opts if not any(o.overlaps(q) for q in taken)), opts[0])
            taken.append(box_)
            self._mask(box_)
            self._text(lines[0], (box_.x0 + 0.2 * th, box_.y1 - 1.1 * h), h, layer=L_LPS, align="BL", colour=LPS_COLOUR)
            self._text(lines[1], (box_.x0 + 0.2 * th, box_.y0 + 0.3 * h), 0.8 * h, layer=L_LPS, align="BL", colour=LPS_COLOUR)
        self._panel_l(panel)
        return EarthingSheet(scale, self.rendered, tuple([*self.rendered.warnings, *self.warnings, *self.cv.warnings]))

    def _panel_l(self, panel: Region) -> None:
        th = self.m.th
        d = self.lps
        y = self._north(Region(panel.x0, panel.y1 - 9 * th, panel.x0 + 12 * th, panel.y1))
        y = panel.y1 - 9.5 * th
        what = "ESE LIGHTNING ARRESTER" if d.method == "ese" else "AIR TERMINAL"
        rows = [(t.tag, f"{t.at.x:.1f}", f"{t.at.y:.1f}", f"{d.radius_m:g}", "ROOF" if t.on_roof else "GROUND MAST") for t in d.terminals]
        cap = max(1, int((y - panel.y0 - 40 * th) / (1.8 * th)))
        shown = rows[:cap]
        y = self._table(Region(panel.x0, panel.y0, panel.x1, y), f"{what} SCHEDULE", ("TAG", "X (m)", "Y (m)", "RADIUS (m)", "LOCATION"),
                        (0.18, 0.18, 0.18, 0.2, 0.26), shown, 0.65 * th, row_h=1.8 * th, head_h=2.0 * th)  # fmt: skip
        if len(rows) > len(shown):
            self.cv.put(TXT, Region(panel.x0, y - 1.8 * th, panel.x1, y), f"... AND {len(rows) - len(shown)} MORE (SEE MODEL / earthing_summary)", 0.6 * th, valign="middle")
            y -= 1.8 * th
        y -= 1.5 * th
        n_tab = sum(1 for t in d.targets if t.kind == "table")
        step = d.steps[-1]
        rows2 = [("METHOD", "ESE, NF C 17-102 (FRENCH STANDARD)" if d.method == "ese" else "ROLLING SPHERE, IS/IEC 62305-3"),
                 ("PROTECTION LEVEL / SPHERE RADIUS", f"LPL {d.level} / {d.steps[0].value:g} m"),
                 ("PROTECTION RADIUS", f"{d.radius_m:g} m ({step.formula})" if len(d.steps) > 1 else f"{d.radius_m:g} m (DATASHEET)")]
        if d.mast_height_m is not None:
            rows2.append(("TERMINAL HEIGHT ABOVE PROTECTED PLANE", f"{d.mast_height_m:g} m"))
        rows2 += [("TERMINALS", f"{len(d.terminals)} NOS."), ("TABLES COVERED", f"{n_tab - sum(1 for t in d.uncovered if t.kind == 'table')} OF {n_tab}"),
                  ("ALL POINTS COVERED (TABLE CENTRES, CORNERS, STATIONS, ROOFS)", "YES" if d.covered_all else f"NO, {len(d.uncovered)} OUTSIDE")]  # fmt: skip
        self._table(Region(panel.x0, panel.y0, panel.x1, y), "LIGHTNING PROTECTION COVERAGE", ("PARAMETER", "VALUE"), (0.55, 0.45), rows2, 0.62 * th, row_h=2.4 * th, head_h=2.0 * th)


def render_earthing_sheet(doc: Drawing, project: Project, catalog: Catalog, design: EarthingDesign, sheet: SheetSpec, profile: CompanyProfile, scale: int | None = None) -> EarthingSheet:
    """Draw the earthing layout sheet for `design` into `doc`'s modelspace."""
    return EarthingSheetWriter(doc, project, _module(project, catalog), design, sheet, profile, scale).render()


def render_lps_sheet(doc: Drawing, project: Project, catalog: Catalog, lps: LpsDesign, sheet: SheetSpec, profile: CompanyProfile, scale: int | None = None) -> EarthingSheet:
    """Draw the lightning protection (LA) layout sheet with coverage circles into `doc`'s modelspace."""
    return LpsSheetWriter(doc, project, _module(project, catalog), lps, sheet, profile, scale).render()


def _module(project: Project, catalog: Catalog) -> ModuleSpec:
    t = next(iter(project.array_layout.table_by_id.values()))
    return catalog.get(t.module)


def earthing_notes(design: EarthingDesign) -> tuple[str, ...]:
    """The standard earthing notes of the reference layouts, then the design's own assumptions."""
    notes = [
        "ALL DIMENSIONS ARE IN MM UNLESS OTHERWISE SPECIFIED; PLAN COORDINATES IN METRES.",
        "EARTHING INSTALLATION SHALL CONFORM TO IS 3043. SWITCHYARD EARTH GRID CHECKED AS PER IEEE 80.",
        "EARTH PIT AND CONDUCTOR LOCATIONS ARE INDICATIVE; MINOR MODIFICATION MAY BE CARRIED OUT AT SITE.",
        "WHEREVER THE EARTH CONDUCTOR CROSSES A CABLE TRENCH IT SHALL BE LAID 300 MM BELOW THE TRENCH.",
        f"MAIN EARTH CONDUCTOR SHALL BE LAID AT MINIMUM {design.basis.burial_depth_m * 1000:g} MM BELOW FGL.",
        f"EARTH ELECTRODES SHALL BE AT LEAST {2 * design.basis.electrode_length_m:g} M (TWO ELECTRODE LENGTHS) APART.",
        "WHEREVER THE EARTH CONDUCTOR MEETS A FOUNDATION IT SHALL BE DIVERTED AROUND IT.",
        "EVERY EQUIPMENT AND STRUCTURE SHALL BE CONNECTED TO THE EARTH NETWORK BY TWO DISTINCT CONNECTIONS.",
        "EVERY MMS TABLE SHALL BE BONDED TO THE ROW EARTH CONDUCTOR.",
        "ALL GRID JOINTS SHALL BE WELDED; EQUIPMENT CONNECTIONS SHALL BE BOLTED.",
        "LIGHTNING PROTECTION EARTH PITS (LE) SHALL BE BONDED TO THE MAIN EARTH NETWORK.",
    ]
    if design.rooftop:
        notes.append("ROOF ARRAY CONDUCTORS RUN ON THE ROOF; DOWN CONDUCTORS RUN ON THE BUILDING FACE TO PITS AT GROUND LEVEL.")
    notes += [f"ASSUMPTION: {a.upper()}." for a in design.assumptions]
    return tuple(notes)


def lps_notes(lps: LpsDesign) -> tuple[str, ...]:
    notes = [
        "LIGHTNING PROTECTION AS PER IS/IEC 62305." if lps.method == "rod" else "ESE LIGHTNING PROTECTION AS PER NF C 17-102 (A FRENCH STANDARD); RISK ASSESSMENT AS PER IS/IEC 62305-2.",
        "THE CIRCLES SHOW THE PROTECTION RADIUS OF EACH TERMINAL AT THE PROTECTED PLANE.",
        "TERMINAL POSITIONS ARE INDICATIVE; KEEP THEM IN ROW GAPS, ROADS OR FREE ROOF AREA.",
        "EACH TERMINAL HAS ITS OWN DOWN CONDUCTOR AND LIGHTNING EARTH PIT (LE), BONDED TO THE MAIN EARTH NETWORK.",
        "EVERY TABLE CENTRE AND CORNER, INVERTER STATION AND ROOF CORNER LIES INSIDE A PROTECTION CIRCLE (CHECKED BY RULE lps.coverage).",
    ]
    notes += [f"ASSUMPTION: {a.upper()}." for a in lps.assumptions]
    return tuple(notes)


__all__ = ["EarthingSheet", "earthing_notes", "lps_notes", "render_earthing_sheet", "render_lps_sheet"]

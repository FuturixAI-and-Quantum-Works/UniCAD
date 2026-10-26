"""Earthing layout and lightning protection (LA) layout sheets, drawn over a greyed site and array underlay at a true scale.

Earthing: conductors by GS flat size, each size with its own linetype and colour as on the reference earthing layouts, numbered
pits (treated, untreated, lightning), the earthing BOQ, the design calculation summary and the typical treated / untreated pit
sections. LA layout: terminals with their protection circles, the terminal schedule and the coverage proof.
"""

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from ezdxf.document import Drawing
from shapely.geometry import LineString, MultiPoint, Polygon, box
from shapely.geometry import Point as SPoint
from shapely.ops import unary_union

from unicad.arrays.geometry import table_corners
from unicad.catalog.store import Catalog
from unicad.domain.catalog import ModuleSpec
from unicad.domain.project import Project
from unicad.domain.site import FeatureKind, shape_of
from unicad.drafting.dxfrules import MIN_LINEWEIGHT
from unicad.drafting.labels import LabelPlacer
from unicad.drafting.sheets.canvas import Canvas, Region
from unicad.drafting.sheets.model import CompanyProfile, LayerRole, SheetSpec
from unicad.drafting.sheets.render import RenderedSheet, render_sheet
from unicad.drafting.sheets.text import text_width
from unicad.drafting.site_plan.sheet import SCALES, LayoutDrawing, LayoutSheetWriter, _Plan, _view_extent, choose_scale
from unicad.earthing import ConductorRole, EarthingDesign, PitRole, pit_text
from unicad.earthing.design import ROLE_TEXT
from unicad.earthing.layout import StationGroup
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
GREY_SITE = {"L-BOUNDARY": (UNDERLAY, 50), "L-ROOF": (UNDERLAY, 35), "L-ROAD": (UNDERLAY_LIGHT, 18), "L-ROAD-FILL": (254, MIN_LINEWEIGHT),
             "L-WATER": (UNDERLAY_LIGHT, 18), "L-WATER-FILL": (254, MIN_LINEWEIGHT), "L-EXCLUSION": (UNDERLAY_LIGHT, 18), "L-EXCLUSION-FILL": (254, MIN_LINEWEIGHT),
             "L-TLINE": (UNDERLAY_LIGHT, 18), "L-OBSTACLE": (UNDERLAY_LIGHT, 18), "L-LABEL": (UNDERLAY, 18)}  # fmt: skip


def cond_layer(size: str, role: ConductorRole) -> str:
    return f"E-COND-{size}-{role.value.upper()}"


@dataclass(frozen=True)
class EarthingSheet:
    scale: int
    sheet: RenderedSheet
    warnings: tuple[str, ...]
    labels: tuple[Region, ...] = ()
    room: Region | None = None
    extent: tuple[float, float, float, float] | None = None


def clip_circle(cx: float, cy: float, r: float, rect: Region) -> list[tuple[float, float]]:
    """Counter-clockwise arcs (start, end in degrees) of the circle that lie inside `rect`; [(0, 360)] when it is wholly inside."""
    angs = set()
    for X in (rect.x0, rect.x1):
        dx = X - cx
        if abs(dx) < r:
            dy = math.sqrt(r * r - dx * dx)
            for y in (cy - dy, cy + dy):
                if rect.y0 <= y <= rect.y1:
                    angs.add(round(math.degrees(math.atan2(y - cy, dx)) % 360, 9))
    for Y in (rect.y0, rect.y1):
        dy = Y - cy
        if abs(dy) < r:
            dx = math.sqrt(r * r - dy * dy)
            for x in (cx - dx, cx + dx):
                if rect.x0 <= x <= rect.x1:
                    angs.add(round(math.degrees(math.atan2(dy, x - cx)) % 360, 9))

    def inside(a: float) -> bool:
        x, y = cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))
        return rect.x0 - 1e-9 <= x <= rect.x1 + 1e-9 and rect.y0 - 1e-9 <= y <= rect.y1 + 1e-9

    if len(angs) < 2:
        return [(0.0, 360.0)] if inside(0.0) else []
    a = sorted(angs)
    out = []
    for s, e in zip(a, a[1:] + [a[0] + 360]):
        if e - s > 1e-6 and inside((s + e) / 2):
            out.append((s, e % 360 if e > 360 else e))
    return out


def tag_ranges(tags: Sequence[str]) -> str:
    """EP3, EP4, EPN1 .. EPN4 -> 'EP3-4, EPN1-4' (runs of consecutive numbers per prefix, in first-seen prefix order)."""
    by: dict[str, list[int]] = defaultdict(list)
    for t in tags:
        m = re.fullmatch(r"([A-Z]+)(\d+)", t)
        by[m.group(1) if m else t].append(int(m.group(2)) if m else -1)
    parts = []
    for k, ns in by.items():
        ns = sorted(n for n in ns if n >= 0)
        if not ns:
            parts.append(k)
            continue
        runs, start = [], ns[0]
        for a, b in zip(ns, ns[1:] + [None]):
            if b != a + 1:
                runs.append(f"{k}{start}" if start == a else f"{k}{start}-{a}")
                start = b
        parts += runs
    return ", ".join(parts)


def expand_ranges(text: str) -> set[str]:
    out = set()
    for part in text.split(","):
        m = re.fullmatch(r"\s*([A-Z]+)(\d+)(?:-(\d+))?\s*", part)
        if m:
            a, b = int(m.group(2)), int(m.group(3) or m.group(2))
            out |= {f"{m.group(1)}{n}" for n in range(a, b + 1)}
    return out


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
        for name, colour, lw in ((L_PIT, 7, 25), (L_PIT_TAG, 7, 18), (L_EQUIP, 7, 25), (L_YARD, 7, 35), (L_UNDER, UNDERLAY_LIGHT, MIN_LINEWEIGHT),
                                 (L_DETAIL, 7, 25), (L_DETAIL_HATCH, 8, MIN_LINEWEIGHT), (L_LPS, LPS_COLOUR, 35), (L_LPS_ZONE, LPS_COLOUR, 25)):  # fmt: skip
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
        self.room, self.bounds = room, bounds
        self.msp.add_line((panel.x0 - th, area.y0), (panel.x0 - th, area.y1), dxfattribs={"layer": self.profile.layer(LayerRole.FRAME).name})
        self._scale_bar(Region(area.x0 + th, area.y0, panel.x0 - 2 * th, area.y0 + 6 * th), scale)
        self.clip = room
        self._placer(room)
        self._site(P)
        for t in self.d.layout.table_by_id.values() if self.d.layout else ():
            self._poly(P.ring(table_corners(t)), L_UNDER)
        self.panel = panel
        return P, panel, scale

    def _done(self, scale: int) -> EarthingSheet:
        self.warnings += [f"label {lb.key} found no room on the plan" for lb in self.placer.overflow]
        return EarthingSheet(scale, self.rendered, tuple([*self.rendered.warnings, *self.warnings, *self.cv.warnings]),
                             tuple(p.box for p in self.placer.labels) + tuple(getattr(self, "detail_labels", ())), self.room, self.bounds)  # fmt: skip

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
            sy = lay.switchyard
            self._poly(P.ring([(sy.x0, sy.y0), (sy.x1, sy.y0), (sy.x1, sy.y1), (sy.x0, sy.y1)]), L_YARD)
        order = {ConductorRole.ARRAY: 0, ConductorRole.GRID: 1, ConductorRole.MAIN: 2, ConductorRole.EQUIPMENT: 3, ConductorRole.DOWN: 4}
        for r in sorted(lay.runs, key=lambda r: order[r.role]):
            self._run(r, P)
        r = max(0.32 * th, 0.75 * P.k)
        self.pit_r = r
        groups = self._groups(P, r)
        grouped = {t for g in groups for t in g.pits}
        sq = max(0.25 * th, 1.2 * P.k)
        terminals: dict[str, tuple[float, float]] = {}
        for it in lay.items:
            x, y = P(it.at.x, it.at.y)
            if it.kind in ("ese", "air_terminal"):
                self._terminal(x, y, 0.45 * th)
                self.placer.block(Region(x - 0.99 * th, y - 0.99 * th, x + 0.99 * th, y + 0.99 * th))
                terminals[it.tag] = (x, y)
                continue
            self._poly([(x - sq, y - sq), (x + sq, y - sq), (x + sq, y + sq), (x - sq, y + sq)], L_EQUIP)
        at_mast: dict[str, str] = {}
        drawn = []
        for p in lay.pits:
            if p.tag in grouped:
                continue
            x, y = P(p.at.x, p.at.y)
            t = terminals.get(p.serves[0]) if p.role is PitRole.LE and p.serves else None
            if t is not None and math.dist(t, (x, y)) < 1.2 * th:
                at_mast[p.serves[0]] = p.tag
                continue
            self._pit_symbol(x, y, r, p.role)
            self.placer.block(Region(x - r, y - r, x + r, y + r))
            drawn.append((p, x, y))
        tag_h = 0.5 * th
        for g in groups:
            x0, y0 = P(g.ring[0], g.ring[1])
            x1, y1 = P(g.ring[2], g.ring[3])
            m = 0.5 * r
            self._poly([(x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m)], L_PIT, colour=PIT_COLOUR[True])
            self.placer.block(Region(x0 - m, y0 - m, x1 + m, y1 + m))
        if lay.switchyard is not None:
            sy = lay.switchyard
            x, y = P((sy.x0 + sy.x1) / 2, sy.y0)
            self._label_text([f"MV SWITCHYARD EARTH MAT, {sy.spacing:.1f} m MESH"], (x, y), 0.6 * th, 0.3 * th, "switchyard", layer=L_PIT_TAG, mask=True)
        for g in groups:
            cx, cy = P((g.ring[0] + g.ring[2]) / 2, (g.ring[1] + g.ring[3]) / 2)
            ref = ", SEE DETAIL A"
            half = max(abs(P(g.ring[2], 0)[0] - P(g.ring[0], 0)[0]), abs(P(0, g.ring[3])[1] - P(0, g.ring[1])[1])) / 2 + r
            self._label_text([f"{g.name}{ref}", tag_ranges(g.pits)], (cx, cy), tag_h, half, g.name, layer=L_PIT_TAG, mask=True,
                             colour=PIT_COLOUR[True], hs=[0.55 * th, tag_h])  # fmt: skip
        for tag, (x, y) in terminals.items():
            text = f"{tag} ({at_mast[tag]})" if tag in at_mast else tag
            self._label_text([text], (x, y), 0.55 * th, 0.99 * th, tag, layer=L_PIT_TAG, colour=LPS_COLOUR, mask=True)
        for p, x, y in drawn:
            colour = LE_COLOUR if p.role is PitRole.LE else PIT_COLOUR[p.role.treated]
            self._label_text([p.tag], (x, y), tag_h, r, p.tag, layer=L_PIT_TAG, colour=colour, mask=True)
        self._feature_labels(P)
        self.clip = None
        self._panel_e(panel, groups)
        return self._done(scale)

    def _run(self, r, P, layer: str | None = None) -> None:
        pts = [P(p.x, p.y) for p in r.points]
        a = {"layer": layer or cond_layer(r.size, r.role)}
        parts = self._clipped(pts, False)
        for part in [pts] if parts is None else parts:
            if len(part) == 2:
                self.msp.add_line(part[0], part[1], dxfattribs=a)
            else:
                self.msp.add_lwpolyline(part, dxfattribs=a)

    def _groups(self, P: _Plan, r: float) -> list[StationGroup]:
        """Station areas whose pits would crowd at this scale: they are drawn as one tag group and shown in Detail A."""
        by_tag = {p.tag: p for p in self.e.layout.pits}
        out = []
        for g in self.e.layout.stations:
            pts = [(by_tag[t].at.x, by_tag[t].at.y) for t in g.pits]
            if len(pts) < 2:
                continue
            gap = min(math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1 :]) * P.k
            if gap < 2 * r + 2.5 * self.m.th:
                out.append(g)
        return out

    def _terminal(self, x: float, y: float, r: float) -> None:
        a = {"layer": L_LPS}
        self.msp.add_circle((x, y), r, dxfattribs=a)
        for i in range(8):
            ang = math.radians(45 * i + 22.5)
            self.msp.add_line((x + r * math.cos(ang), y + r * math.sin(ang)), (x + 2.2 * r * math.cos(ang), y + 2.2 * r * math.sin(ang)), dxfattribs=a)

    def _panel_e(self, panel: Region, groups: Sequence[StationGroup] = ()) -> None:
        th = self.m.th
        y = panel.y1
        y = self._north(Region(panel.x0, y - 9 * th, panel.x0 + 12 * th, y))
        y = panel.y1 - 9.5 * th
        y = self._boq(Region(panel.x0, panel.y0, panel.x1, y)) - 1.5 * th
        y = self._calc(Region(panel.x0, panel.y0, panel.x1, y)) - 1.5 * th
        if groups:
            room = y - panel.y0
            h = min(max(0.45 * room, room - 60 * th), 0.6 * room)
            if room - h < 25 * th:
                h = room - 25 * th
            if h >= 30 * th:
                self._station_detail(Region(panel.x0, y - h, panel.x1, y), groups[0], len(groups))
                y -= h + 1.5 * th
            else:
                self.warnings.append("no room for the station earthing detail in the side panel")
        self._details(Region(panel.x0, panel.y0, panel.x1, y))

    def _station_detail(self, r: Region, g: StationGroup, n: int) -> None:
        """Detail A: the first crowded station drawn large, with its ring, equipment, bonds and every pit tagged."""
        th = self.m.th
        lay = self.e.layout
        title_h = 2.2 * th
        body = Region(r.x0 + th, r.y0 + 2.4 * th, r.x1 - th, r.y1 - title_h - 0.5 * th)
        x0, y0, x1, y1 = g.ring
        m = max(3.0, 0.3 * max(x1 - x0, y1 - y0))
        win = (x0 - m, y0 - m, x1 + m, y1 + m)
        fits = [s for s in SCALES if (win[2] - win[0]) * 1000 / s <= 0.9 * body.width and (win[3] - win[1]) * 1000 / s <= 0.85 * body.height]
        scale = fits[0] if fits else SCALES[-1]
        k = 1000 / scale
        cx, cy = (win[0] + win[2]) / 2, (win[1] + win[3]) / 2
        px, py = body.centre

        def Q(x: float, y: float) -> tuple[float, float]:
            return px + (x - cx) * k, py + (y - cy) * k

        view = Region(body.x0, body.y0, body.x1, body.y1)
        site_win = box(cx - view.width / 2 / k, cy - view.height / 2 / k, cx + view.width / 2 / k, cy + view.height / 2 / k)
        self.cv.put(TXT, Region(r.x0, r.y1 - title_h, r.x1, r.y1), f"DETAIL A: TYPICAL STATION EARTHING ({g.name})", 0.8 * th, align="center", valign="middle", min_h=0.5 * th)
        self.cv.put(TXT, Region(r.x0, r.y0, r.x1, r.y0 + 2.2 * th), f"SCALE 1:{scale}. {'ALL ' + str(n) + ' STATIONS SIMILAR; PITS PER STATION AS TAGGED ON PLAN.' if n > 1 else ''}",
                    0.6 * th, align="center", valign="middle", min_h=0.4 * th)  # fmt: skip
        self.cv.rect(TXT, r)
        for run in lay.runs:
            line = LineString([(p.x, p.y) for p in run.points])
            if not line.intersects(site_win):
                continue
            part = line.intersection(site_win)
            for seg in getattr(part, "geoms", [part]):
                if isinstance(seg, LineString) and seg.length > 0:
                    pts = [Q(x, y) for x, y in seg.coords]
                    a = {"layer": cond_layer(run.size, run.role)}
                    self.msp.add_lwpolyline(pts, dxfattribs=a) if len(pts) > 2 else self.msp.add_line(pts[0], pts[1], dxfattribs=a)
        placer = LabelPlacer(view, max_leader=8 * th, gap=0.15 * th)
        saved, self.placer = self.placer, placer
        rr = 0.45 * th
        sq = 0.5 * th
        items = [it for it in lay.items if it.id in set(g.items)]
        for it in items:
            x, y = Q(it.at.x, it.at.y)
            self._poly([(x - sq, y - sq), (x + sq, y - sq), (x + sq, y + sq), (x - sq, y + sq)], L_EQUIP)
            placer.block(Region(x - sq, y - sq, x + sq, y + sq))
        by_tag = {p.tag: p for p in lay.pits}
        pits = [by_tag[t] for t in g.pits]
        for p in pits:
            x, y = Q(p.at.x, p.at.y)
            self._pit_symbol(x, y, rr, p.role)
            placer.block(Region(x - rr, y - rr, x + rr, y + rr))
        for p in pits:
            x, y = Q(p.at.x, p.at.y)
            self._label_text([p.tag], (x, y), 0.55 * th, rr, p.tag, layer=L_PIT_TAG, colour=PIT_COLOUR[p.role.treated], mask=True)
        for it in items:
            x, y = Q(it.at.x, it.at.y)
            self._label_text([it.tag], (x, y), 0.5 * th, sq, it.id, layer=L_PIT_TAG, mask=True)
        self.detail_labels = [p.box for p in placer.labels]
        self.warnings += [f"detail A: label {lb.key} found no room" for lb in placer.overflow]
        self.placer = saved

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
        shapes = [SPoint(t.at.x, t.at.y) for t in self.lps.terminals] + [Polygon(table_corners(t)) for t in self.d.layout.table_by_id.values()]
        shapes += [shape_of(f) for f in self.d.site.features if f.kind is FeatureKind.ROOF]
        return unary_union(shapes).buffer(3.0).bounds

    def render(self) -> EarthingSheet:
        P, panel, scale = self._start(0)
        th = self.m.th
        d = self.lps
        h = 0.6 * th
        pts = []
        for t in d.terminals:
            x, y = P(t.at.x, t.at.y)
            for a0, a1 in clip_circle(x, y, d.radius_m * P.k, self.room):
                if a1 - a0 >= 360 - 1e-9:
                    self.msp.add_circle((x, y), d.radius_m * P.k, dxfattribs={"layer": L_LPS_ZONE})
                else:
                    self.msp.add_arc((x, y), d.radius_m * P.k, a0, a1, dxfattribs={"layer": L_LPS_ZONE})
            EarthingSheetWriter._terminal(self, x, y, 0.45 * th)
            self.placer.block(Region(x - 0.99 * th, y - 0.99 * th, x + 0.99 * th, y + 0.99 * th))
            pts.append((t, x, y))
        for t, x, y in pts:
            self._label_text([t.tag, f"RADIUS COVERED {d.radius_m:g} m"], (x, y), h, 0.99 * th, t.tag, layer=L_LPS, colour=LPS_COLOUR, mask=True, hs=[h, 0.8 * h])
        self._feature_labels(P)
        self.clip = None
        self._panel_l(panel)
        return self._done(scale)

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

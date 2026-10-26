"""Array layout sheet: the site plan at a true scale with tables, blocks and inverter stations, plus the block capacity table,
typical-table inset, north arrow, legend and scale bar.

Modelspace is in paper millimetres like every other sheet (LibreCAD prints modelspace only), so the site is drawn
scaled by 1000 / S mm per metre and the sheet is exported with `-s 1`. Tables are blocks in site metres holding
module blocks; each table is one INSERT scaled by 1000 / S. Fills are solid HATCH on a coloured layer (LibreCAD
2.2.1.6 ignores an explicit colour on a hatch and draws pattern hatches inconsistently), dashes are drawn as segments.
"""

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import BaseLayout
from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from unicad.arrays.geometry import frame, plan_depth, plan_length, table_corners
from unicad.arrays.spec import PlacementResult, RoadLine
from unicad.domain.catalog import ModuleSpec
from unicad.domain.layout import ArrayLayout, Table
from unicad.domain.site import FeatureKind, SiteFeature, SiteGeometry, shape_of
from unicad.drafting.dxfrules import MIN_LINEWEIGHT, plain
from unicad.drafting.labels import Label, LabelPlacer
from unicad.drafting.legend import legend_drawer
from unicad.drafting.sheets.canvas import Canvas, Region
from unicad.drafting.sheets.layout import SheetMetrics, metrics_for
from unicad.drafting.sheets.model import CompanyProfile, LayerRole, SheetSpec
from unicad.drafting.sheets.render import RenderedSheet, render_sheet
from unicad.drafting.sheets.text import text_width
from unicad.drafting.symbols import REGISTRY
from unicad.drafting.symbols.install import install

SCALES = (100, 200, 250, 500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000, 7500, 10000, 15000, 20000)
PALETTE = (1, 5, 6, 30, 4, 200, 94, 40, 150, 12, 180, 230, 54, 110, 240, 70)
TOTAL_COLOUR = 1
# LibreCAD prints pure white (ACI 7 and 255, RGB FFFFFF) as black on a white page; off-white stays white.
MASK_RGB = 0xFFFFFE
FILL_FRACTION = 0.94
TXT = LayerRole.TEXT
View = Literal["site", "roofs"]


@dataclass(frozen=True)
class _LayerDef:
    name: str
    colour: int
    lineweight: int = 18


L_BOUNDARY = _LayerDef("L-BOUNDARY", 3, 70)
L_ROOF = _LayerDef("L-ROOF", 7, 50)
L_ROAD = _LayerDef("L-ROAD", 8, 25)
L_ROAD_FILL = _LayerDef("L-ROAD-FILL", 254, MIN_LINEWEIGHT)
L_WATER = _LayerDef("L-WATER", 4, 25)
L_WATER_FILL = _LayerDef("L-WATER-FILL", 131, MIN_LINEWEIGHT)
L_EXCL = _LayerDef("L-EXCLUSION", 8, 25)
L_EXCL_FILL = _LayerDef("L-EXCLUSION-FILL", 253, MIN_LINEWEIGHT)
L_TLINE = _LayerDef("L-TLINE", 6, 25)
L_OBST = _LayerDef("L-OBSTACLE", 8, 25)
L_TABLE = _LayerDef("L-ARRAY", 7, MIN_LINEWEIGHT)
L_BLOCK = _LayerDef("L-BLOCK-OUTLINE", 7, 35)
L_STATION = _LayerDef("L-STATION", 7, 25)
L_LABEL = _LayerDef("L-LABEL", 7, 18)
L_MASK = _LayerDef("L-MASK", 7, MIN_LINEWEIGHT)
L_DIM = _LayerDef("L-DIM", 7, 18)
COMPACT = frozenset({FeatureKind.ROOF, FeatureKind.OBSTACLE, FeatureKind.BUILDING, FeatureKind.EXISTING_STRUCTURE})
LAYERS = (L_BOUNDARY, L_ROOF, L_ROAD, L_ROAD_FILL, L_WATER, L_WATER_FILL, L_EXCL, L_EXCL_FILL, L_TLINE, L_OBST, L_TABLE, L_BLOCK, L_STATION,
          L_LABEL, L_MASK, L_DIM)  # fmt: skip


@dataclass(frozen=True)
class LayoutDrawing:
    """What the sheet shows. `full_cols` tells full tables from half ones; `pitch_m` and `table_gap_m` dimension the inset."""

    site: SiteGeometry
    layout: ArrayLayout
    module: ModuleSpec
    full_cols: int
    pitch_m: float | None = None
    table_gap_m: float | None = None
    roads: tuple[RoadLine, ...] = ()
    notes: tuple[str, ...] = ()

    @classmethod
    def from_result(cls, site: SiteGeometry, result: PlacementResult, module: ModuleSpec, table_gap_m: float | None = None, notes: Sequence[str] = ()) -> "LayoutDrawing":
        return cls(site, result.layout, module, result.full_table.cols, result.pitch.pitch_m, table_gap_m, result.roads, tuple(notes))


@dataclass(frozen=True)
class LayoutSheet:
    scale: int
    plan: Region
    panel: Region
    sheet: RenderedSheet
    block_colours: dict[str, int]
    warnings: tuple[str, ...] = ()
    labels: tuple[Region, ...] = ()


def choose_scale(width_m: float, height_m: float, room: Region) -> int:
    for s in SCALES:
        k = 1000 / s
        if width_m * k <= room.width * FILL_FRACTION and height_m * k <= room.height * FILL_FRACTION:
            return s
    return SCALES[-1]


def nice_step(length_m: float) -> float:
    exp = 10 ** math.floor(math.log10(length_m))
    for f in (1, 2, 5, 10):
        if f * exp >= length_m:
            return f * exp
    return 10 * exp


def _label(f: SiteFeature) -> str:
    return (f.name or f.id).replace("-", " ").replace("_", " ").upper()


def _polygons(g: BaseGeometry) -> list[Polygon]:
    if isinstance(g, Polygon):
        return [] if g.is_empty else [g]
    if isinstance(g, MultiPolygon):
        return [p for p in g.geoms if not p.is_empty]
    return [p for part in getattr(g, "geoms", []) for p in _polygons(part)]


def _view_extent(d: LayoutDrawing, view: View) -> tuple[float, float, float, float]:
    shapes = [shape_of(f) for f in d.site.features if view == "site" or f.kind in (FeatureKind.ROOF, FeatureKind.OBSTACLE)]
    shapes += [Polygon(table_corners(t)) for t in d.layout.table_by_id.values()]
    if not shapes:
        raise ValueError("nothing to draw: the site has no features and the layout no tables")
    return unary_union(shapes).bounds


class _Plan:
    """Site metres to paper millimetres."""

    def __init__(self, bounds: tuple[float, float, float, float], room: Region, scale: int):
        x0, y0, x1, y1 = bounds
        self.k = 1000 / scale
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.px, self.py = room.centre

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        return self.px + (x - self.cx) * self.k, self.py + (y - self.cy) * self.k

    def ring(self, coords: Iterable[tuple[float, ...]]) -> list[tuple[float, float]]:
        return [self(c[0], c[1]) for c in coords]


class LayoutSheetWriter:
    def __init__(self, doc: Drawing, d: LayoutDrawing, sheet: SheetSpec, profile: CompanyProfile, view: View = "site", scale: int | None = None):
        self.doc, self.d, self.profile, self.view = doc, d, profile, view
        self.msp: BaseLayout = doc.modelspace()
        self.m: SheetMetrics = metrics_for(sheet.paper)
        self.sheet = sheet
        self.forced_scale = scale
        self.warnings: list[str] = []
        self.colours = {b.id: PALETTE[i % len(PALETTE)] for i, b in enumerate(d.layout.blocks)}
        self.table_blocks: dict[tuple, str] = {}
        self.clip: Region | None = None
        self.placer: LabelPlacer | None = None

    def _placer(self, room: Region) -> LabelPlacer:
        self.placer = LabelPlacer(room, max_leader=10 * self.m.th, gap=0.15 * self.m.th, cell=2 * self.m.th)
        return self.placer

    def _clipped(self, pts: Sequence[tuple[float, float]], closed: bool) -> list[list[tuple[float, float]]] | None:
        """Parts of the polyline inside the clip region, or None when there is no clip or it lies wholly inside."""
        if self.clip is None:
            return None
        c = self.clip
        if all(c.x0 <= x <= c.x1 and c.y0 <= y <= c.y1 for x, y in pts):
            return None
        ring = list(pts) + ([pts[0]] if closed else [])
        if len(ring) < 2:
            return []
        g = LineString(ring).intersection(box(c.x0, c.y0, c.x1, c.y1))
        parts = getattr(g, "geoms", [g])
        return [list(p.coords) for p in parts if isinstance(p, LineString) and not p.is_empty and p.length > 0]

    def _label_text(self, lines: Sequence[str], anchor: tuple[float, float], h: float, clear: float, key: str, layer: str = L_LABEL.name,
                    colour: int | None = None, mask: bool = False, cost=None, hs: Sequence[float] | None = None) -> bool:  # fmt: skip
        """Place a (multi-line) label beside `anchor` with the placer; a leader joins it when it had to move away. False if it found no room."""
        hs = list(hs or [h] * len(lines))
        pad = 0.15 * h
        w = max(text_width(t, hh) for t, hh in zip(lines, hs)) + 2 * pad
        ht = sum(1.45 * hh for hh in hs) - 0.45 * hs[-1] + 2 * pad
        p = self.placer.place(Label(key, anchor, w, ht, clear), cost=cost)
        if p is None:
            return False
        b = p.box
        if mask:
            self._mask(b)
        if p.leader is not None:
            self.msp.add_line(p.leader[0], p.leader[1], dxfattribs={"layer": layer, **({"color": colour} if colour is not None else {})})
        y = b.y1 - pad
        for t, hh in zip(lines, hs):
            y -= hh
            self._text(t, (b.x0 + pad, y), hh, layer=layer, align="BL", colour=colour)
            y -= 0.45 * hh
        return True

    def _layers(self) -> None:
        for spec in LAYERS:
            if spec.name not in self.doc.layers:
                self.doc.layers.add(spec.name, color=spec.colour, lineweight=spec.lineweight)

    def _text(self, s: str, at: tuple[float, float], h: float, layer: str = L_LABEL.name, align: str = "ML", colour: int | None = None, rotation: float = 0) -> None:
        attribs: dict = {"layer": layer, "style": self.profile.text_style, "height": h, "rotation": rotation}
        if colour is not None:
            attribs["color"] = colour
        self.msp.add_text(plain(s), dxfattribs=attribs).set_placement(at, align=TextEntityAlignment[{"ML": "MIDDLE_LEFT", "MC": "MIDDLE_CENTER", "MR": "MIDDLE_RIGHT", "BL": "BOTTOM_LEFT", "BC": "BOTTOM_CENTER"}[align]])

    def _poly(self, pts: Sequence[tuple[float, float]], layer: str, closed: bool = True, colour: int | None = None, lineweight: int | None = None) -> None:
        attribs: dict = {"layer": layer}
        if colour is not None:
            attribs["color"] = colour
        if lineweight is not None:
            attribs["lineweight"] = lineweight
        parts = self._clipped(pts, closed)
        if parts is None:
            self.msp.add_lwpolyline(pts, close=closed, dxfattribs=attribs)
            return
        for part in parts:
            self.msp.add_lwpolyline(part, dxfattribs=attribs)

    def _dashed(self, pts: Sequence[tuple[float, float]], layer: str, dash: float, gap: float, closed: bool = True, colour: int | None = None, dot: bool = False) -> None:
        parts = self._clipped(pts, closed)
        if parts is not None:
            clip, self.clip = self.clip, None
            for part in parts:
                self._dashed(part, layer, dash, gap, closed=False, colour=colour, dot=dot)
            self.clip = clip
            return
        ring = list(pts) + ([pts[0]] if closed else [])
        attribs: dict = {"layer": layer}
        if colour is not None:
            attribs["color"] = colour
        pattern = [(0.0, dash)] + ([(dash + gap, dash + gap + 0.3)] if dot else [])
        period = dash + gap + (gap + 0.3 if dot else 0.0)
        s0 = 0.0
        for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg == 0:
                continue
            ux, uy = (x1 - x0) / seg, (y1 - y0) / seg
            k = math.floor(s0 / period)
            while k * period < s0 + seg:
                for a, b in pattern:
                    lo, hi = max(k * period + a, s0) - s0, min(k * period + b, s0 + seg) - s0
                    if hi > lo:
                        self.msp.add_line((x0 + ux * lo, y0 + uy * lo), (x0 + ux * hi, y0 + uy * hi), dxfattribs=attribs)
                k += 1
            s0 += seg

    def _fill(self, poly: Polygon, layer: str, to_paper: Callable) -> None:
        paper = Polygon(to_paper(poly.exterior.coords[:-1]), [to_paper(h.coords[:-1]) for h in poly.interiors])
        if self.clip is not None:
            c = self.clip
            paper = paper.intersection(box(c.x0, c.y0, c.x1, c.y1))
        for part in _polygons(paper):
            h = self.msp.add_hatch(dxfattribs={"layer": layer, "color": 256})
            h.set_solid_fill(color=256)
            h.paths.add_polyline_path(list(part.exterior.coords)[:-1], is_closed=True, flags=1)
            for hole in part.interiors:
                h.paths.add_polyline_path(list(hole.coords)[:-1], is_closed=True, flags=0)

    def _mask(self, r: Region) -> None:
        self.msp.add_solid([(r.x0, r.y0), (r.x1, r.y0), (r.x0, r.y1), (r.x1, r.y1)], dxfattribs={"layer": L_MASK.name, "true_color": MASK_RGB})

    def render(self) -> LayoutSheet:
        m, th = self.m, self.m.th
        area = m.drawing_area
        panel_w = min(0.4 * area.width, max(0.28 * area.width, 44 * th))
        panel = Region(area.x1 - panel_w, area.y0, area.x1, area.y1)
        plan_room = Region(area.x0 + th, area.y0 + 7 * th, panel.x0 - 2 * th, area.y1 - th)
        bounds = _view_extent(self.d, self.view)
        scale = self.forced_scale or choose_scale(bounds[2] - bounds[0], bounds[3] - bounds[1], plan_room)
        tb = self.sheet.title_block.model_copy(update={"scale": f"1:{scale}"})
        notes = self.sheet.notes or self.d.notes
        spec = self.sheet.model_copy(update={"title_block": tb, "notes": notes, "legend": self.sheet.legend or self._legend_entries()})
        rendered = render_sheet(self.doc, spec, self.profile, legend_drawer(self.msp))
        self._layers()
        self.cv = Canvas(self.doc, self.profile)
        P = _Plan(bounds, plan_room, scale)
        self._placer(plan_room)
        self._site(P)
        self._tables(P)
        self._blocks(P)
        self._feature_labels(P, compact=False)
        self._block_drawing(P)
        self._feature_labels(P, compact=True)
        self.msp.add_line((panel.x0 - th, area.y0), (panel.x0 - th, area.y1), dxfattribs={"layer": self.profile.layer(LayerRole.FRAME).name})
        self._panel(panel)
        self._scale_bar(Region(area.x0 + th, area.y0, panel.x0 - 2 * th, area.y0 + 6 * th), scale)
        x0, y0 = P(bounds[0], bounds[1])
        x1, y1 = P(bounds[2], bounds[3])
        if not plan_room.contains(Region(x0, y0, x1, y1), tol=0.01):
            self.warnings.append(f"the site does not fit the plan area at 1:{scale}")
        self.warnings += [f"label {lb.key} found no room on the plan" for lb in self.placer.overflow]
        return LayoutSheet(scale, plan_room, panel, rendered, dict(self.colours), tuple([*rendered.warnings, *self.warnings, *self.cv.warnings]),
                           tuple(p.box for p in self.placer.labels))  # fmt: skip

    def _legend_entries(self) -> tuple[tuple[str, str], ...]:
        mod = self.d.module
        out = [("layout.module", f"PV MODULE {mod.pmax:g} Wp, {mod.length:g} x {mod.width:g} mm")]
        if any(b.station for b in self.d.layout.blocks):
            out.append(("layout.inverter", "INVERTER STATION (INDICATIVE LOCATION)"))
        out.append(("ann.north", "GRID NORTH"))
        return tuple(out)

    def _site(self, P: _Plan) -> None:
        th = self.m.th
        feats = [f for f in self.d.site.features if self.view == "site" or f.kind in (FeatureKind.ROOF, FeatureKind.OBSTACLE)]
        fills = {FeatureKind.WATER: L_WATER_FILL, FeatureKind.EXCLUSION: L_EXCL_FILL, FeatureKind.BUILDING: L_EXCL_FILL,
                 FeatureKind.EXISTING_STRUCTURE: L_EXCL_FILL, FeatureKind.ROAD: L_ROAD_FILL}  # fmt: skip
        for f in feats:
            g = shape_of(f)
            if f.kind is FeatureKind.ROAD and f.polyline:
                g = g.buffer((f.width_m or 1.0) / 2, cap_style="flat", join_style="mitre")
            if (layer := fills.get(f.kind)) is not None:
                for p in _polygons(g):
                    self._fill(p, layer.name, P.ring)
        for r in self.d.roads:
            corridor = LineString([(p.x, p.y) for p in r.points]).buffer(r.width_m / 2, cap_style="flat")
            for p in _polygons(corridor):
                self._fill(p, L_ROAD_FILL.name, P.ring)
        for f in feats:
            g = shape_of(f)
            if f.kind is FeatureKind.BOUNDARY:
                self._poly(P.ring(g.exterior.coords[:-1]), L_BOUNDARY.name)
            elif f.kind is FeatureKind.ROOF:
                self._poly(P.ring(g.exterior.coords[:-1]), L_ROOF.name)
            elif f.kind is FeatureKind.TRANSMISSION_LINE:
                rings = [g.exterior.coords[:-1]] if f.polygon else [g.coords]
                for ring in rings:
                    self._dashed(P.ring(ring), L_TLINE.name, 4 * th / 3.5, 1.2 * th / 3.5, closed=bool(f.polygon), dot=True)
            elif f.kind is FeatureKind.ROAD:
                if f.polyline:
                    for p in _polygons(g.buffer((f.width_m or 1.0) / 2, cap_style="flat", join_style="mitre")):
                        self._poly(P.ring(p.exterior.coords[:-1]), L_ROAD.name)
                else:
                    self._poly(P.ring(g.exterior.coords[:-1]), L_ROAD.name)
            elif f.kind is FeatureKind.SETBACK_LINE:
                coords = g.exterior.coords[:-1] if f.polygon else g.coords
                self._dashed(P.ring(coords), L_EXCL.name, 2.0, 1.0, closed=bool(f.polygon))
            elif f.polygon:
                layer = L_WATER if f.kind is FeatureKind.WATER else L_OBST if f.kind is FeatureKind.OBSTACLE else L_EXCL
                self._poly(P.ring(g.exterior.coords[:-1]), layer.name)

    def _block_drawing(self, P: _Plan) -> None:
        """Outlines of roofs, obstacles and buildings and the modules are obstacles for their labels; roads, water and the like are labelled along or inside themselves first."""
        th = self.m.th
        for f in self.d.site.features:
            if f.kind in COMPACT and f.polygon and (self.view == "site" or f.kind in (FeatureKind.ROOF, FeatureKind.OBSTACLE)):
                for part in _polygons(shape_of(f)):
                    self.placer.block_path(P.ring(part.exterior.coords[:-1]), closed=True, thickness=0.1 * th, step=0.5 * th)
        for t in self.d.layout.table_by_id.values():
            pts = P.ring(table_corners(t))
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            self.placer.block(Region(min(xs), min(ys), max(xs), max(ys)))

    def _feature_labels(self, P: _Plan, compact: bool) -> None:
        feats = [f for f in self.d.site.features if self.view == "site" or f.kind in (FeatureKind.ROOF, FeatureKind.OBSTACLE)]
        for f in feats:
            if (f.kind in COMPACT) is not compact:
                continue
            if f.kind in (FeatureKind.WATER, FeatureKind.EXCLUSION, FeatureKind.TRANSMISSION_LINE, FeatureKind.ROAD, FeatureKind.BUILDING, FeatureKind.ROOF) or (
                f.kind is FeatureKind.OBSTACLE and self.view == "roofs"
            ):  # fmt: skip
                self._feature_label(f, P)

    def _rotated(self, label: str, at: tuple[float, float], h: float, ang: float, key: str, mask: bool = False) -> bool:
        """Centred text at `at`, rotated by `ang`, if its bounding box is clear of the labels placed so far."""
        w = text_width(label, h)
        c, s_ = abs(math.cos(math.radians(ang))), abs(math.sin(math.radians(ang)))
        bw, bh = w * c + 1.3 * h * s_, w * s_ + 1.3 * h * c
        if self.clip is not None and not self.clip.contains(Region(at[0] - bw / 2, at[1] - bh / 2, at[0] + bw / 2, at[1] + bh / 2)):
            return False
        if self.placer.fixed(Label(key, at, bw, bh), Region(at[0] - bw / 2, at[1] - bh / 2, at[0] + bw / 2, at[1] + bh / 2)) is None:
            return False
        self._text(label, at, h, align="MC", rotation=ang)
        return True

    def _feature_label(self, f: SiteFeature, P: _Plan) -> None:
        th = self.m.th
        g = shape_of(f)
        h = 0.8 * th if f.kind is not FeatureKind.OBSTACLE else 0.55 * th
        label = _label(f)
        if f.kind is FeatureKind.ROOF:
            x0, y0, x1, y1 = g.bounds
            self._label_text([label], P(x0, y1), 0.8 * th, 0.0, f.id)
            return
        if isinstance(g, LineString):
            mid = g.interpolate(0.5, normalized=True)
            a, b = g.coords[0], g.coords[-1]
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            if ang > 90 or ang < -90:
                ang += 180
            at = P(mid.x, mid.y)
            off = ((f.width_m or 1.0) / 2) * P.k + 0.8 * th
            n = math.radians(ang + 90)
            for side in (1, -1):
                if self._rotated(label, (at[0] + side * off * math.cos(n), at[1] + side * off * math.sin(n)), h, ang, f.id):
                    return
            self._label_text([label], at, 0.6 * th, off - 0.6 * th, f.id)
            return
        rect = g.minimum_rotated_rectangle
        cs = list(rect.exterior.coords)
        e1 = (cs[1][0] - cs[0][0], cs[1][1] - cs[0][1])
        e2 = (cs[2][0] - cs[1][0], cs[2][1] - cs[1][1])
        long = e1 if math.hypot(*e1) >= math.hypot(*e2) else e2
        ang = math.degrees(math.atan2(long[1], long[0]))
        if ang > 90 or ang < -90:
            ang += 180
        if abs(ang) < 1e-6 or abs(abs(ang) - 90) > 45:
            ang = 0.0 if abs(ang) < 45 else ang
        c = g.representative_point() if not g.centroid.within(g) else g.centroid
        avail = math.hypot(*long) * P.k * 0.9
        while text_width(label, h) > avail and h > 0.35 * th:
            h *= 0.85
        if text_width(label, h) <= avail and self._rotated(label, P(c.x, c.y), h, ang, f.id):
            return
        x0, y0, x1, y1 = g.bounds
        self._label_text([label], P(c.x, c.y), max(0.5 * th, min(h, 0.6 * th)), max(x1 - x0, y1 - y0) / 2 * P.k, f.id)

    def _module_block(self) -> str:
        name = "ARR_MODULE"
        if name not in self.doc.blocks:
            blk = self.doc.blocks.new(name)
            mod = self.d.module
            some = next(iter(self.d.layout.table_by_id.values()), None)
            portrait = some is None or some.orientation.value == "portrait"
            w, h = (mod.width / 1000, mod.length / 1000) if portrait else (mod.length / 1000, mod.width / 1000)
            blk.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True, dxfattribs={"layer": "0", "color": 0, "lineweight": -2})
        return name

    def _table_block(self, t: Table) -> str:
        key = (t.rows, t.cols, t.slot_width_m, t.slot_height_m, t.tilt_deg, t.unused)
        if key in self.table_blocks:
            return self.table_blocks[key]
        name = f"ARR_TABLE_{t.rows}x{t.cols}_{len(self.table_blocks) + 1}"
        mod = self._module_block()
        blk = self.doc.blocks.new(name)
        cos = math.cos(math.radians(t.tilt_deg))
        unused = set(t.unused)
        for r in range(t.rows):
            for c in range(t.cols):
                if (r, c) not in unused:
                    blk.add_blockref(mod, (c * t.slot_width_m, r * t.slot_height_m * cos), dxfattribs={"layer": "0", "color": 0, "yscale": cos})
        w, d = plan_length(t), plan_depth(t)
        blk.add_lwpolyline([(0, 0), (w, 0), (w, d), (0, d)], close=True, dxfattribs={"layer": "0", "color": 0, "lineweight": -2})
        self.table_blocks[key] = name
        return name

    def _tables(self, P: _Plan) -> None:
        for b in self.d.layout.blocks:
            colour = self.colours[b.id]
            for t in b.tables:
                (ux, uy), _ = frame(t.azimuth_deg)
                rot = math.degrees(math.atan2(uy, ux))
                self.msp.add_blockref(self._table_block(t), P(t.origin.x, t.origin.y),
                                      dxfattribs={"layer": L_TABLE.name, "color": colour, "xscale": P.k, "yscale": P.k, "rotation": rot})  # fmt: skip

    def _blocks(self, P: _Plan) -> None:
        th = self.m.th
        multi = len(self.d.layout.blocks) > 1
        sym = REGISTRY["layout.inverter"]
        install(self.doc, ["layout.inverter"])
        s = 0.16 * th
        footprints = [Polygon(P.ring(table_corners(t))) for t in self.d.layout.table_by_id.values()]
        tree = STRtree(footprints)
        taken: list[Polygon] = []
        stations: list = []

        def clash(r: Region) -> float:
            g = box(r.x0, r.y0, r.x1, r.y1)
            return sum(footprints[i].intersection(g).area for i in tree.query(g)) + sum(t.intersection(g).area * 10 for t in taken)

        for b in self.d.layout.blocks:
            colour = self.colours[b.id]
            if multi and b.boundary:
                self._dashed(P.ring([(p.x, p.y) for p in b.boundary]), L_BLOCK.name, 2.2 * th / 3.5, 1.0 * th / 3.5, colour=colour)
            if b.station is None:
                continue
            kwp = sum(t.slot_count for t in b.tables) * self.d.module.pmax / 1000
            lines = (b.name, f"{kwp / 1000:.3f} MWp" if kwp >= 1000 else f"{kwp:.2f} kWp")
            hs = (0.85 * th, 0.7 * th)
            x, y = P(b.station.x, b.station.y)
            w, h = sym.width * s, sym.height * s
            icon = Region(x - w / 2 - 0.25 * th, y - h / 2 - 0.25 * th, x + w / 2 + 0.25 * th, y + h / 2 + 0.25 * th)
            self.placer.block(icon)
            self._mask(icon)
            self._poly([(icon.x0, icon.y0), (icon.x1, icon.y0), (icon.x1, icon.y1), (icon.x0, icon.y1)], L_STATION.name, colour=colour)
            self.msp.add_blockref(sym.block_name, (x - w / 2, y - h / 2), dxfattribs={"layer": L_STATION.name, "xscale": s, "yscale": s})
            stations.append((b, lines, hs, (x, y), icon, colour))
        for b, lines, hs, (x, y), icon, colour in stations:
            p = self.placer.place(Label(b.id, (x, y), max(text_width(t, hh) for t, hh in zip(lines, hs)) + 0.8 * th, 2.9 * th, clear=(icon.x1 - icon.x0) / 2 + 0.3 * th), cost=clash)
            if p is None:
                self.warnings.append(f"no room for the {b.name} station label")
                continue
            label = p.box
            taken.append(box(label.x0, label.y0, label.x1, label.y1))
            self._mask(label)
            self._poly([(label.x0, label.y0), (label.x1, label.y0), (label.x1, label.y1), (label.x0, label.y1)], L_STATION.name, colour=colour)
            if p.leader is not None:
                self.msp.add_line(p.leader[0], p.leader[1], dxfattribs={"layer": L_STATION.name, "color": colour})
            tx, cy = label.x0 + 0.4 * th, label.centre[1]
            self._text(lines[0], (tx, cy + 0.6 * th), hs[0], align="ML")
            self._text(lines[1], (tx, cy - 0.65 * th), hs[1], align="ML")

    def _panel(self, panel: Region) -> None:
        th = self.m.th
        y = panel.y1
        y = self._north(Region(panel.x0, y - 16 * th, panel.x1, y))
        y = self._mms_table(Region(panel.x0, panel.y0, panel.x1, y - 2 * th))
        y = self._inset(Region(panel.x0, panel.y0, panel.x1, y - 2 * th))
        self._line_legend(Region(panel.x0, panel.y0, panel.x1, y - 2 * th))

    def _north(self, r: Region) -> float:
        sym = REGISTRY["ann.north"]
        install(self.doc, ["ann.north"])
        s = (r.height * 0.9) / sym.height
        self.msp.add_blockref(sym.block_name, (r.centre[0] - sym.width * s / 2, r.y0 + 0.05 * r.height), dxfattribs={"layer": L_LABEL.name, "xscale": s, "yscale": s})
        return r.y0

    def _row(self, cells: Sequence[tuple[float, float]], y0: float, y1: float, texts: Sequence[str], h: float, colour: int | None = None, max_lines: int = 1) -> None:
        for (x0, x1), s in zip(cells, texts):
            if not s:
                continue
            before = len(self.cv.text_cells)
            self.cv.put(TXT, Region(x0, y0, x1, y1), s, h, align="center", valign="middle", max_lines=max_lines, pad_x=0.3 * self.m.th, pad_y=0.2 * self.m.th, min_h=0.5 * self.m.th)
            if colour is not None:
                for tc in self.cv.text_cells[before:]:
                    self.doc.entitydb[tc.handle].dxf.color = colour

    def _mms_table(self, r: Region) -> float:
        th = self.m.th
        blocks = self.d.layout.blocks
        widths = (0.17, 0.12, 0.12, 0.13, 0.13, 0.13, 0.20)
        xs = [r.x0]
        for w in widths:
            xs.append(xs[-1] + w * r.width)
        cells = list(zip(xs, xs[1:]))
        title_h, head_h, row_h = 2.6 * th, 3.6 * th, 2.3 * th
        top = r.y1
        total_h = title_h + head_h + row_h * (len(blocks) + 1)
        y = top
        self.cv.put(TXT, Region(r.x0, y - title_h, r.x1, y), "MMS TABLE DETAILS", 1.1 * th, align="center", valign="middle")
        y -= title_h
        self.cv.hline(TXT, y, r.x0, r.x1)
        unit = "kWp"
        heads = ("BLOCK NO", "MODULE Wp", "COLOUR", "FULL TABLE", "HALF TABLE", "TOTAL", unit)
        self._row(cells, y - head_h, y, heads, 0.8 * th, max_lines=2)
        y -= head_h
        self.cv.hline(TXT, y, r.x0, r.x1)
        full_cols = self.d.full_cols
        tot = [0, 0, 0, 0.0]
        wp = self.d.module.pmax
        for b in blocks:
            nf = sum(1 for t in b.tables if t.cols >= full_cols)
            nh = len(b.tables) - nf
            kwp = sum(t.slot_count for t in b.tables) * wp / 1000
            tot = [tot[0] + nf, tot[1] + nh, tot[2] + nf + nh, tot[3] + kwp]
            self._row(cells, y - row_h, y, (b.name, f"{wp:g}", "", str(nf), str(nh), str(nf + nh), f"{kwp:,.2f}"), 0.8 * th)
            sw = Region(cells[2][0], y - row_h, cells[2][1], y).inset(0.25 * (cells[2][1] - cells[2][0]), 0.5 * th)
            h = self.msp.add_hatch(dxfattribs={"layer": self._swatch_layer(self.colours[b.id])})
            h.set_solid_fill(color=256)
            h.paths.add_polyline_path([(sw.x0, sw.y0), (sw.x1, sw.y0), (sw.x1, sw.y1), (sw.x0, sw.y1)], is_closed=True)
            y -= row_h
            self.cv.hline(TXT, y, r.x0, r.x1)
        self._row(cells, y - row_h, y, ("TOTAL", "", "", str(tot[0]), str(tot[1]), str(tot[2]), f"{tot[3]:,.2f}"), 0.8 * th, colour=TOTAL_COLOUR)
        y -= row_h
        self.cv.rect(TXT, Region(r.x0, y, r.x1, top), heavy=True)
        for x in xs[1:-1]:
            self.cv.vline(TXT, x, y, top - title_h)
        assert abs((top - y) - total_h) < 1e-6
        return y

    def _swatch_layer(self, colour: int) -> str:
        name = f"L-SWATCH-{colour}"
        if name not in self.doc.layers:
            self.doc.layers.add(name, color=colour, lineweight=MIN_LINEWEIGHT)
        return name

    def _inset(self, r: Region) -> float:
        th = self.m.th
        tables = list(self.d.layout.table_by_id.values())
        if not tables:
            return r.y1
        full = next((t for t in tables if t.cols >= self.d.full_cols), tables[0])
        half = next((t for t in tables if t.cols < self.d.full_cols), None)
        L, D = plan_length(full), plan_depth(full)
        pitch = self.d.pitch_m
        inner_w = r.width - 16 * th
        k = inner_w / L
        rows_drawn = 2 if pitch else 1
        span = (pitch or 0) + D
        k = min(k, 14 * th / span) if span * k > 14 * th else k
        title = f"FULL TABLE TYPICAL ARRANGEMENT ({full.rows}{full.orientation.value[0].upper()} x {full.cols})"
        y = r.y1
        self.cv.put(TXT, Region(r.x0, y - 2.4 * th, r.x1, y), title, 0.95 * th, align="center", valign="middle", min_h=0.6 * th)
        y -= 2.4 * th + 2.2 * th
        x0 = r.x0 + 5 * th
        blk = self._table_block(full)
        y_top = y
        fronts = []
        for i in range(rows_drawn):
            yb = y_top - D * k - (rows_drawn - 1 - i) * (pitch or 0) * k
            fronts.append(yb)
            self.msp.add_blockref(blk, (x0, yb), dxfattribs={"layer": L_TABLE.name, "color": PALETTE[0], "xscale": k, "yscale": k})
        y_bottom = fronts[0]
        self._dim_h(x0, x0 + L * k, y_top + 1.0 * th, f"{L:.2f}")
        self._dim_v(x0 - 1.0 * th, fronts[-1], fronts[-1] + D * k, f"{D:.2f}", left=True)
        if pitch:
            self._dim_v(x0 + L * k + 1.0 * th, fronts[0], fronts[1], f"PITCH {pitch:.2f}", left=False)
        y = y_bottom - 1.6 * th
        info = f"{full.slot_count} MODULES, TILT {full.tilt_deg:g} DEG, PLAN DEPTH {D:.2f} m"
        self.cv.put(TXT, Region(r.x0, y - 1.8 * th, r.x1, y), info, 0.75 * th, align="center", valign="middle", min_h=0.5 * th)
        y -= 1.8 * th
        if half is not None:
            y -= 1.0 * th
            self.cv.put(TXT, Region(r.x0, y - 2.2 * th, r.x1, y), f"HALF TABLE ({half.rows}{half.orientation.value[0].upper()} x {half.cols})", 0.95 * th, align="center", valign="middle")
            y -= 2.2 * th + 1.2 * th
            hl, hd = plan_length(half), plan_depth(half)
            self.msp.add_blockref(self._table_block(half), (x0, y - hd * k), dxfattribs={"layer": L_TABLE.name, "color": PALETTE[0], "xscale": k, "yscale": k})
            self._dim_h(x0, x0 + hl * k, y + 0.6 * th, f"{hl:.2f}")
            self.cv.put(TXT, Region(x0 + hl * k + th, y - max(hd * k, 1.6 * th), r.x1, y), f"{half.slot_count} MODULES", 0.75 * th, valign="middle", min_h=0.5 * th)
            y -= max(hd * k, 1.6 * th)
        y -= 0.8 * th
        self.cv.put(TXT, Region(r.x0, y - 1.8 * th, r.x1, y), "ALL DIMENSIONS ARE IN METRES", 0.7 * th, align="center", valign="middle")
        y -= 1.8 * th
        self.cv.rect(TXT, Region(r.x0, y, r.x1, r.y1))
        return y

    def _tick(self, x: float, y: float) -> None:
        d = 0.35 * self.m.th
        self.msp.add_line((x - d, y - d), (x + d, y + d), dxfattribs={"layer": L_DIM.name})

    def _dim_h(self, x0: float, x1: float, y: float, label: str) -> None:
        th = self.m.th
        self.msp.add_line((x0, y), (x1, y), dxfattribs={"layer": L_DIM.name})
        for x in (x0, x1):
            self.msp.add_line((x, y - 0.8 * th), (x, y + 0.4 * th), dxfattribs={"layer": L_DIM.name})
            self._tick(x, y)
        self._text(label, ((x0 + x1) / 2, y + 0.3 * th), 0.7 * th, layer=L_DIM.name, align="BC")

    def _dim_v(self, x: float, y0: float, y1: float, label: str, left: bool) -> None:
        th = self.m.th
        self.msp.add_line((x, y0), (x, y1), dxfattribs={"layer": L_DIM.name})
        for y in (y0, y1):
            self._tick(x, y)
        tx = x - 0.4 * th if left else x + 0.4 * th
        self._text(label, (tx, (y0 + y1) / 2), 0.65 * th, layer=L_DIM.name, align="MR" if left else "ML")

    def _line_legend(self, r: Region) -> float:
        th = self.m.th
        kinds = {f.kind for f in self.d.site.features}
        rows: list[tuple[str, Callable[[Region], None]]] = []
        if FeatureKind.BOUNDARY in kinds and self.view == "site":
            rows.append(("PLANT BOUNDARY / FENCE", lambda c: self._poly([(c.x0, c.centre[1]), (c.x1, c.centre[1])], L_BOUNDARY.name, closed=False)))
        if FeatureKind.ROOF in kinds:
            rows.append(("ROOF EDGE", lambda c: self._poly([(c.x0, c.centre[1]), (c.x1, c.centre[1])], L_ROOF.name, closed=False)))
        if FeatureKind.ROAD in kinds or self.d.roads:
            rows.append(("ROAD", lambda c: self._swatch(c, L_ROAD_FILL.name, L_ROAD.name)))
        if FeatureKind.WATER in kinds and self.view == "site":
            rows.append(("WATER BODY", lambda c: self._swatch(c, L_WATER_FILL.name, L_WATER.name)))
        if kinds & {FeatureKind.EXCLUSION, FeatureKind.BUILDING, FeatureKind.EXISTING_STRUCTURE} and self.view == "site":
            rows.append(("EXCLUSION / NO-BUILD AREA", lambda c: self._swatch(c, L_EXCL_FILL.name, L_EXCL.name)))
        if FeatureKind.OBSTACLE in kinds:
            rows.append(("OBSTACLE", lambda c: self._poly([(c.x0, c.y0), (c.x1, c.y0), (c.x1, c.y1), (c.x0, c.y1)], L_OBST.name)))
        if FeatureKind.TRANSMISSION_LINE in kinds and self.view == "site":
            rows.append(("TRANSMISSION LINE CORRIDOR", lambda c: self._dashed([(c.x0, c.centre[1]), (c.x1, c.centre[1])], L_TLINE.name, 4 * th / 3.5, 1.2 * th / 3.5, closed=False, dot=True)))
        if len(self.d.layout.blocks) > 1:
            rows.append(("INVERTER BLOCK BOUNDARY", lambda c: self._dashed([(c.x0, c.centre[1]), (c.x1, c.centre[1])], L_BLOCK.name, 2.2 * th / 3.5, th / 3.5, closed=False, colour=PALETTE[0])))
        if not rows:
            return r.y1
        row_h = 2.2 * th
        need = 2.4 * th + row_h * len(rows)
        if need > r.height:
            self.warnings.append("no room for the line legend in the side panel")
            return r.y1
        y = r.y1
        self.cv.put(TXT, Region(r.x0, y - 2.4 * th, r.x1, y), "LINE LEGEND", 0.95 * th, align="center", valign="middle")
        y -= 2.4 * th
        for label, draw in rows:
            sample = Region(r.x0 + 1.5 * th, y - row_h + 0.6 * th, r.x0 + 9 * th, y - 0.6 * th)
            draw(sample)
            self.cv.put(TXT, Region(sample.x1 + th, y - row_h, r.x1, y), label, 0.75 * th, valign="middle", min_h=0.5 * th)
            y -= row_h
        self.cv.rect(TXT, Region(r.x0, y, r.x1, r.y1))
        return y

    def _swatch(self, c: Region, fill: str, line: str) -> None:
        pts = [(c.x0, c.y0), (c.x1, c.y0), (c.x1, c.y1), (c.x0, c.y1)]
        h = self.msp.add_hatch(dxfattribs={"layer": fill, "color": 256})
        h.set_solid_fill(color=256)
        h.paths.add_polyline_path(pts, is_closed=True)
        self._poly(pts, line)

    def _scale_bar(self, r: Region, scale: int) -> None:
        th = self.m.th
        k = 1000 / scale
        step = nice_step(r.width * 0.25 / k / 4)
        n = 4
        x0, y = r.x0 + 2 * th, r.y0 + 3.2 * th
        bh = 0.8 * th
        for i in range(n):
            xa, xb = x0 + i * step * k, x0 + (i + 1) * step * k
            if i % 2 == 0:
                self.msp.add_solid([(xa, y), (xb, y), (xa, y + bh), (xb, y + bh)], dxfattribs={"layer": L_DIM.name})
            self._poly([(xa, y), (xb, y), (xb, y + bh), (xa, y + bh)], L_DIM.name)
        for i in range(n + 1):
            v = i * step
            self._text(f"{v:g}", (x0 + i * step * k, y + bh + 0.4 * th), 0.65 * th, layer=L_DIM.name, align="BC")
        end = x0 + n * step * k
        self._text("m", (end + 0.8 * th, y + bh / 2), 0.65 * th, layer=L_DIM.name, align="ML")
        self._text(f"SCALE 1:{scale} ON {self.sheet.paper.value}", (x0, y - 1.4 * th), 0.8 * th, layer=L_DIM.name, align="ML")


def render_layout_sheet(
    doc: Drawing, drawing: LayoutDrawing, sheet: SheetSpec, profile: CompanyProfile, *, view: View = "site", scale: int | None = None,
) -> LayoutSheet:  # fmt: skip
    """Draw the array layout sheet into `doc`'s modelspace. The scale is the largest standard scale that fits unless given;
    the title block's scale field is set to it."""
    return LayoutSheetWriter(doc, drawing, sheet, profile, view, scale).render()


def default_notes(result: PlacementResult, site: SiteGeometry, spec_text: str) -> tuple[str, ...]:
    m = result.metrics
    notes = [
        "ALL DIMENSIONS ARE IN METRES UNLESS OTHERWISE STATED.",
        spec_text,
        f"ROW PITCH {result.pitch.pitch_m:.2f} m, GCR {m.gcr:.3f}: {result.pitch.note.upper()}.",
        f"DC CAPACITY {m.dc_kwp:,.2f} kWp: {m.modules:,} MODULES OF {m.module_wp:g} Wp ON {m.tables_full} FULL AND {m.tables_half} HALF TABLES IN {len(result.blocks)} BLOCK{'S' if len(result.blocks) != 1 else ''}.",
        f"SITE GRID {site.crs}, LOCAL ORIGIN {site.origin.lon:.5f} E, {site.origin.lat:.5f} N. NORTH IS GRID NORTH.",
        "INVERTER STATION LOCATIONS ARE INDICATIVE AND SUBJECT TO DETAILED ENGINEERING.",
    ]
    notes += [f"ASSUMPTION: {a.upper()}." for a in result.assumptions]
    return tuple(notes)


__all__ = ["LayoutDrawing", "LayoutSheet", "choose_scale", "default_notes", "nice_step", "render_layout_sheet"]

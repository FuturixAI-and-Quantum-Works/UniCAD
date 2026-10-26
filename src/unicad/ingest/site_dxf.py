"""DXF site plans: closed polylines, hatches and circles on named layers, in the file's own units."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import path as ezpath
from ezdxf.document import Drawing
from ezdxf.lldxf.const import DXFError
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon

from unicad.ingest.site_common import Coord, ParsedSite, RawFeature, SiteImportError, drop_repeats

INSUNITS_TO_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0, 10: 0.9144, 14: 0.1}
UNIT_NAMES = {"in": 0.0254, "ft": 0.3048, "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "yd": 0.9144, "dm": 0.1}
CLOSE_GAP_M = 0.5
FLATTEN_M = 0.02
MAX_INSERT_DEPTH = 4
PROJECTED_LOOKING_M = 1e5
SHAPE_TYPES = {"LWPOLYLINE", "POLYLINE", "HATCH", "CIRCLE", "ELLIPSE", "SPLINE", "ARC"}


@dataclass
class _Shape:
    points: list[Coord]
    closed: bool
    layer: str
    holes: list[list[Coord]] = field(default_factory=list)


def unit_factor(doc: Drawing, units: str | None, warnings: list[str]) -> float:
    if units is not None:
        try:
            return UNIT_NAMES[units.strip().lower()]
        except KeyError:
            raise SiteImportError(f"unknown units {units!r}; use one of {sorted(UNIT_NAMES)}") from None
    code = int(doc.header.get("$INSUNITS", 0))
    if code == 0:
        warnings.append("DXF has no $INSUNITS (unitless); coordinates were taken as metres. Pass units='mm' (or ft, in, cm) if that is wrong")
        return 1.0
    if code not in INSUNITS_TO_M:
        raise SiteImportError(f"unsupported $INSUNITS {code}; pass units= one of {sorted(UNIT_NAMES)}")
    return INSUNITS_TO_M[code]


def nest_rings(rings: list[list[Coord]]) -> list[tuple[list[Coord], list[list[Coord]]]]:
    """Polygons with holes from nested rings: even nesting depth is an outer boundary, odd depth a hole of its container."""
    polys = [Polygon(r) for r in rings]
    order = sorted(range(len(rings)), key=lambda i: -polys[i].area)
    parent: dict[int, int | None] = {}
    depth: dict[int, int] = {}
    for n, i in enumerate(order):
        point = polys[i].representative_point()
        containers = [j for j in order[:n] if polys[j].contains(point)]
        parent[i] = containers[-1] if containers else None
        p = parent[i]
        depth[i] = depth[p] + 1 if p is not None else 0
    holes: dict[int, list[list[Coord]]] = {i: [] for i in order if depth[i] % 2 == 0}
    for i in order:
        if depth[i] % 2 == 1 and parent[i] in holes:
            holes[parent[i]].append(rings[i])  # type: ignore[index]
    return [(rings[i], h) for i, h in holes.items()]


class _Reader:
    def __init__(self, dist: float, close_gap: float) -> None:
        self.dist, self.close_gap = dist, close_gap
        self.warnings: list[str] = []
        self.texts: list[tuple[str, Coord]] = []
        self.ignored: dict[str, int] = {}

    def shapes(self, entities: Iterable, depth: int = 0, parent_layer: str | None = None) -> Iterator[_Shape]:
        for e in entities:
            t = e.dxftype()
            if t == "INSERT" and depth < MAX_INSERT_DEPTH:
                try:
                    inner = list(e.virtual_entities())
                except (DXFError, ValueError, TypeError):
                    continue
                yield from self.shapes(inner, depth + 1, e.dxf.layer)
            elif t in SHAPE_TYPES:
                for s in self._entity(e):
                    if s.layer == "0" and parent_layer:
                        s.layer = parent_layer
                    yield s
            elif t in ("TEXT", "MTEXT"):
                text = " ".join((e.plain_text() if t == "MTEXT" else e.dxf.text).split())
                if text:
                    self.texts.append((text, (e.dxf.insert.x, e.dxf.insert.y)))
            elif t in ("LINE", "POINT"):
                self.ignored[t] = self.ignored.get(t, 0) + 1

    def _entity(self, entity) -> list[_Shape]:
        layer, kind = entity.dxf.layer, entity.dxftype()
        try:
            if kind == "HATCH":
                rings = [pts for p in ezpath.from_hatch(entity) if len(pts := drop_repeats([(v.x, v.y) for v in p.flattening(self.dist)], True)) >= 3]
                return [_Shape(outer, True, layer, holes) for outer, holes in nest_rings(rings)]
            out = []
            for sub in ezpath.make_path(entity).sub_paths():
                pts = [(v.x, v.y) for v in sub.flattening(self.dist)]
                gap = abs(pts[0][0] - pts[-1][0]) + abs(pts[0][1] - pts[-1][1]) if pts else 0.0
                closed = sub.is_closed or (len(pts) > 2 and gap <= self.close_gap)
                out.append(_Shape(drop_repeats(pts, closed), closed, layer))
            return out
        except (DXFError, ValueError, TypeError, AttributeError, IndexError) as e:
            self.warnings.append(f"layer {layer!r}: could not read a {kind} ({e}); skipped")
            return []


def parse_dxf(path: Path, units: str | None = None, source_epsg: int | None = None) -> ParsedSite:
    try:
        doc = ezdxf.readfile(path)
    except (OSError, DXFError, UnicodeDecodeError) as e:
        raise SiteImportError(f"{path.name} is not a readable DXF: {e}") from None
    parsed = ParsedSite([], "projected" if source_epsg else "local", source_epsg)
    factor = unit_factor(doc, units, parsed.warnings)
    reader = _Reader(FLATTEN_M / factor, CLOSE_GAP_M / factor)
    hidden = {layer.dxf.name for layer in doc.layers if layer.is_off() or layer.is_frozen()}
    shapes = [s for s in reader.shapes(doc.modelspace()) if s.layer not in hidden]
    parsed.warnings += reader.warnings
    if hidden:
        parsed.warnings.append(f"layers switched off or frozen were skipped: {sorted(hidden)}")

    def scaled(points: list[Coord]) -> list[Coord]:
        return [(x * factor, y * factor) for x, y in points]

    usable = [s for s in shapes if len(s.points) >= (3 if s.closed else 2)]
    polys = {id(s): Polygon(s.points) for s in usable if s.closed}
    label_of: dict[int, str] = {}
    for text, xy in reader.texts:
        point = ShapelyPoint(xy)
        around = [s for s in usable if s.closed and polys[id(s)].is_valid and polys[id(s)].contains(point)]
        if around:
            label_of.setdefault(id(min(around, key=lambda s: polys[id(s)].area)), text)
    for s in usable:
        raw = RawFeature(scaled(s.points), s.closed, (s.layer,), holes=[scaled(h) for h in s.holes], attributes={"layer": s.layer})
        if text := label_of.get(id(s)):
            raw.name, raw.labels = text, (s.layer, text)
        parsed.features.append(raw)
    if n := reader.ignored.get("LINE"):
        parsed.warnings.append(f"{n} loose LINE entities ignored; draw paths and boundaries as polylines")
    if source_epsg is None and any(abs(c) > PROJECTED_LOOKING_M for f in parsed.features for c in f.points[0]):
        parsed.warnings.append("coordinates are over 100 km from the drawing origin; if they are projected eastings/northings pass source_crs (e.g. 'EPSG:32643')")
    return parsed

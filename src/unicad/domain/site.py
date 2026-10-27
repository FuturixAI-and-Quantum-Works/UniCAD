"""Site geometry: boundary, roofs, exclusions, roads and obstacles in local metres.

Local axes are grid east (x) and grid north (y) of `crs`, a projected metric CRS, shifted so that
local (0, 0) sits at `origin`. Everything downstream (array layout, drawings) works in these metres.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property, lru_cache
from typing import Annotated, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyproj import CRS, Proj, Transformer
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import explain_validity

from unicad.domain.layout import Point
from unicad.domain.network import AttrValue

SiteId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
EpsgCode = Annotated[str, Field(pattern=r"^EPSG:[0-9]{4,6}$")]
SQM_PER_ACRE = 4046.8564224
ORIGIN_DECIMALS = 7
OUTSIDE_TOLERANCE = 0.005


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeatureKind(StrEnum):
    BOUNDARY = "boundary"
    ROOF = "roof"
    EXCLUSION = "exclusion"
    ROAD = "road"
    BUILDING = "building"
    OBSTACLE = "obstacle"
    WATER = "water"
    SETBACK_LINE = "setback_line"
    TRANSMISSION_LINE = "transmission_line"
    EXISTING_STRUCTURE = "existing_structure"
    SWITCHYARD = "switchyard"


LINE_KINDS = frozenset({FeatureKind.ROAD, FeatureKind.SETBACK_LINE, FeatureKind.TRANSMISSION_LINE})
GROUND_CARVE_KINDS = frozenset({
    FeatureKind.EXCLUSION, FeatureKind.WATER, FeatureKind.OBSTACLE, FeatureKind.BUILDING, FeatureKind.EXISTING_STRUCTURE, FeatureKind.SWITCHYARD,
}) | LINE_KINDS  # fmt: skip
ROOF_CARVE_KINDS = frozenset({FeatureKind.EXCLUSION, FeatureKind.OBSTACLE, FeatureKind.EXISTING_STRUCTURE})


class LonLat(_Frozen):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


def _ring_problem(ring: tuple[Point, ...]) -> str | None:
    for p in ring:
        if not (math.isfinite(p.x) and math.isfinite(p.y)):
            return "has a non-finite coordinate"
    return None


def _strip_closing(ring: tuple[Point, ...]) -> tuple[Point, ...]:
    return ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring


class SiteFeature(_Frozen):
    """One piece of site geometry. Area kinds carry `polygon`; road, setback_line and transmission_line carry `polyline` or `polygon`.

    `width_m` is the corridor width of a polyline, `height_m` the height above its surface (roof or ground),
    `setback_m` overrides the default clearance kept around the feature when computing buildable area.
    Rings are open (the closing point is not repeated); `holes` are inner rings of the polygon.
    """

    id: SiteId
    kind: FeatureKind
    polygon: tuple[Point, ...] = ()
    holes: tuple[tuple[Point, ...], ...] = ()
    polyline: tuple[Point, ...] = ()
    name: str | None = None
    height_m: float | None = Field(default=None, ge=0)
    width_m: float | None = Field(default=None, gt=0)
    setback_m: float | None = Field(default=None, ge=0)
    attributes: dict[str, AttrValue] = Field(default_factory=dict)

    @field_validator("polygon", "polyline", mode="after")
    @classmethod
    def _open_ring(cls, v: tuple[Point, ...]) -> tuple[Point, ...]:
        return _strip_closing(v) if len(v) > 2 else v

    @field_validator("holes", mode="after")
    @classmethod
    def _open_holes(cls, v: tuple[tuple[Point, ...], ...]) -> tuple[tuple[Point, ...], ...]:
        return tuple(_strip_closing(h) for h in v)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        tag = f"{self.kind.value} {self.id!r}"
        if bool(self.polygon) == bool(self.polyline):
            raise ValueError(f"{tag} needs exactly one of polygon or polyline")
        if self.polyline and self.kind not in LINE_KINDS:
            raise ValueError(f"{tag} is an area and needs a polygon, not a polyline")
        if self.holes and not self.polygon:
            raise ValueError(f"{tag} has holes but no polygon")
        for label, ring in [("polygon", self.polygon), ("polyline", self.polyline), *[("hole", h) for h in self.holes]]:
            if ring and (bad := _ring_problem(ring)):
                raise ValueError(f"{tag} {label} {bad}")
        if self.polyline:
            if len(self.polyline) < 2:
                raise ValueError(f"{tag} polyline needs at least 2 points")
            if shape_of(self).length <= 0:
                raise ValueError(f"{tag} polyline has zero length")
        else:
            if len(self.polygon) < 3 or any(len(h) < 3 for h in self.holes):
                raise ValueError(f"{tag} polygon and holes need at least 3 points")
            poly = shape_of(self)
            if not poly.is_valid:
                raise ValueError(f"{tag} polygon is invalid: {explain_validity(poly)}")
            if poly.area <= 0:
                raise ValueError(f"{tag} polygon has zero area")
        return self

    @property
    def is_area(self) -> bool:
        return bool(self.polygon)

    @property
    def area_m2(self) -> float:
        return shape_of(self).area if self.polygon else 0.0

    @property
    def length_m(self) -> float:
        return shape_of(self).length if self.polyline else 0.0


def shape_of(feature: SiteFeature) -> Polygon | LineString:
    if feature.polyline:
        return LineString([(p.x, p.y) for p in feature.polyline])
    return Polygon([(p.x, p.y) for p in feature.polygon], [[(p.x, p.y) for p in h] for h in feature.holes])


class Setbacks(_Frozen):
    """Default clearances in metres. A feature's own `setback_m` replaces the matching default for that feature."""

    boundary_m: float = Field(default=0, ge=0)
    roof_edge_m: float = Field(default=0, ge=0)
    exclusion_m: float = Field(default=0, ge=0)
    obstacle_m: float = Field(default=0, ge=0)


class SiteGeometry(_Frozen):
    crs: EpsgCode
    origin: LonLat
    features: tuple[SiteFeature, ...] = ()
    setbacks: Setbacks = Setbacks()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems = []
        try:
            crs = CRS.from_user_input(self.crs)
        except Exception:
            crs = None
            problems.append(f"unknown CRS {self.crs!r}")
        if crs is not None and (not crs.is_projected or any(ax.unit_name != "metre" for ax in crs.axis_info)):
            problems.append(f"CRS {self.crs} must be a projected CRS in metres")
        ids = [f.id for f in self.features]
        problems += [f"duplicate site feature id {i!r}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        if problems:
            raise ValueError("invalid site geometry: " + "; ".join(problems))
        return self

    @cached_property
    def feature_by_id(self) -> dict[str, SiteFeature]:
        return {f.id: f for f in self.features}

    def of_kind(self, *kinds: FeatureKind) -> tuple[SiteFeature, ...]:
        return tuple(f for f in self.features if f.kind in kinds)

    def local_to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        to_ll, _, (ox, oy) = _transformers(self.crs, self.origin.lon, self.origin.lat)
        return to_ll.transform(ox + x, oy + y)

    def lonlat_to_local(self, lon: float, lat: float) -> tuple[float, float]:
        _, to_xy, (ox, oy) = _transformers(self.crs, self.origin.lon, self.origin.lat)
        x, y = to_xy.transform(lon, lat)
        return x - ox, y - oy

    def grid_scale_factor(self) -> float:
        """Ratio of grid distance to ground distance at the origin; grid areas are this squared times ground areas."""
        return float(_projection_scale(self.crs, self.origin.lon, self.origin.lat))

    def with_features(self, features: Iterable[SiteFeature]) -> Self:
        return type(self)(crs=self.crs, origin=self.origin, features=tuple(features), setbacks=self.setbacks)


@lru_cache(maxsize=32)
def _transformers(crs: str, lon: float, lat: float) -> tuple[Transformer, Transformer, tuple[float, float]]:
    to_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return to_ll, to_xy, to_xy.transform(lon, lat)


@lru_cache(maxsize=32)
def _projection_scale(crs: str, lon: float, lat: float) -> float:
    return Proj(CRS.from_user_input(crs)).get_factors(lon, lat).meridional_scale


def utm_crs_for(lon: float, lat: float) -> str:
    """WGS 84 / UTM zone EPSG code for a position. India falls in zones 42N to 47N."""
    zone = int((lon + 180) // 6) % 60 + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


@dataclass(frozen=True)
class BuildableArea:
    ground: BaseGeometry
    roofs: dict[str, BaseGeometry] = field(default_factory=dict)

    @property
    def ground_m2(self) -> float:
        return self.ground.area

    @property
    def roof_m2(self) -> float:
        return sum(g.area for g in self.roofs.values())


def _carve(feature: SiteFeature, default: float) -> BaseGeometry:
    clearance = feature.setback_m if feature.setback_m is not None else default
    geom = shape_of(feature)
    if feature.polyline:
        if feature.width_m is None:
            return LineString()
        return geom.buffer(feature.width_m / 2 + clearance, cap_style="flat", join_style="mitre")
    return geom.buffer(clearance, join_style="mitre") if clearance else geom


def _inset(feature: SiteFeature, default: float) -> BaseGeometry:
    d = feature.setback_m if feature.setback_m is not None else default
    geom = shape_of(feature)
    return geom.buffer(-d, join_style="mitre") if d else geom


def buildable_area(site: SiteGeometry, setbacks: Setbacks | None = None) -> BuildableArea:
    """Where arrays may go: boundary inset by its setback, minus roofs, exclusions, water, buildings, obstacles and buffered road or line corridors.

    Each roof is its own region: the roof inset by the edge setback, minus obstacles and exclusions that touch it.
    Polylines without `width_m` carve nothing. Pure function of the geometry and the setbacks.
    """
    sb = setbacks or site.setbacks

    def carve_default(f: SiteFeature) -> float:
        return sb.obstacle_m if f.kind is FeatureKind.OBSTACLE else sb.exclusion_m

    boundaries = [_inset(f, sb.boundary_m) for f in site.of_kind(FeatureKind.BOUNDARY)]
    roofs = site.of_kind(FeatureKind.ROOF)
    ground_carves = [_carve(f, carve_default(f)) for f in site.features if f.kind in GROUND_CARVE_KINDS]
    ground_carves += [shape_of(f) for f in roofs]
    ground = unary_union(boundaries).difference(unary_union(ground_carves)) if boundaries else Polygon()

    roof_regions: dict[str, BaseGeometry] = {}
    roof_carves = [_carve(f, carve_default(f)) for f in site.features if f.kind in ROOF_CARVE_KINDS]
    carve_all = unary_union(roof_carves) if roof_carves else Polygon()
    for r in roofs:
        roof_regions[r.id] = _inset(r, sb.roof_edge_m).difference(carve_all)
    return BuildableArea(_polygonal(ground), {k: _polygonal(v) for k, v in roof_regions.items()})


def _polygonal(geom: BaseGeometry) -> BaseGeometry:
    if geom.is_empty or geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else Polygon()


class SiteIssue(NamedTuple):
    severity: str
    subject: str
    message: str


def site_issues(site: SiteGeometry) -> list[SiteIssue]:
    """Gaps and implausible placements. Hard errors (invalid polygons, duplicate ids) are rejected by the model itself."""
    out: list[SiteIssue] = []
    boundaries = site.of_kind(FeatureKind.BOUNDARY)
    roofs = site.of_kind(FeatureKind.ROOF)
    if not boundaries and not roofs:
        out.append(SiteIssue("warning", "site", "no boundary or roof: nothing to lay an array on"))
    if len(boundaries) > 1:
        out.append(SiteIssue("info", "site", f"{len(boundaries)} boundaries: {[b.id for b in boundaries]}; buildable area unions them"))
    bound_union = unary_union([shape_of(b) for b in boundaries]) if boundaries else None
    if bound_union is not None:
        for f in site.features:
            if f.kind is FeatureKind.BOUNDARY:
                continue
            g = shape_of(f)
            if not g.intersects(bound_union):
                out.append(SiteIssue("warning", f.id, f"{f.kind.value} lies entirely outside the boundary"))
            elif f.kind in (FeatureKind.ROOF, FeatureKind.BUILDING) and f.polygon:
                outside = g.difference(bound_union).area / g.area
                if outside > OUTSIDE_TOLERANCE:
                    out.append(SiteIssue("warning", f.id, f"{outside:.0%} of this {f.kind.value} lies outside the boundary"))
    if roofs:
        roof_union = unary_union([shape_of(r) for r in roofs])
        for f in site.of_kind(FeatureKind.OBSTACLE):
            g = shape_of(f)
            if not g.intersects(roof_union) and (bound_union is None or not g.intersects(bound_union)):
                out.append(SiteIssue("warning", f.id, "obstacle touches no roof or boundary"))
        for i, a in enumerate(roofs):
            ga = shape_of(a)
            for b in roofs[i + 1 :]:
                overlap = ga.intersection(shape_of(b)).area
                if overlap > OUTSIDE_TOLERANCE * min(ga.area, shape_of(b).area):
                    out.append(SiteIssue("warning", a.id, f"overlaps roof {b.id!r} by {overlap:.1f} m2"))
    area = buildable_area(site)
    if (boundaries or roofs) and area.ground_m2 + area.roof_m2 <= 0:
        out.append(SiteIssue("warning", "site", "buildable area is zero with the current setbacks"))
    return out


"""Shared pieces of the site importers: raw features, kind classification, and placement into local metres."""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError
from pyproj import CRS, Transformer

from unicad.domain.layout import Point
from unicad.domain.site import (
    LINE_KINDS,
    ORIGIN_DECIMALS,
    FeatureKind,
    LonLat,
    SiteFeature,
    SiteGeometry,
    utm_crs_for,
)

Frame = Literal["lonlat", "projected", "local"]
Coord = tuple[float, float]
IGNORE = "ignore"
MM = 3
FAR_M = 50_000
HEIGHT_KEYS = ("height_m", "height", "h")
WIDTH_KEYS = ("width_m", "width")
SETBACK_KEYS = ("setback_m", "setback")

# First match wins, so more specific kinds come first ("water tank" is an obstacle, not water).
KIND_RULES: tuple[tuple[FeatureKind, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern, re.I))
    for kind, pattern in (
        (FeatureKind.TRANSMISSION_LINE, r"transmission|\bt\.?l\.?\b|power ?line|overhead line|\bh\.?t\.? ?line|\bkv\b|\brow\b|right of way|corridor"),
        (FeatureKind.ROAD, r"\broads?\b|\btracks?\b|\bpath\b|access|approach|driveway|\bstreet\b|\blane\b"),
        (FeatureKind.SETBACK_LINE, r"set ?-?back|\boffset\b|\bbuffer\b"),
        (FeatureKind.OBSTACLE, r"obstacle|obstruction|hvac|skylight|chimney|\bvents?\b|parapet|\bac unit|\bduct|water tank|overhead tank|\blift\b|stair|exhaust|antenna|\bmast\b|rooftop unit|\bahu\b|turbine"),
        (FeatureKind.ROOF, r"\broofs?\b|rooftop|\bsheds?\b|terrace|\bdeck\b"),
        (FeatureKind.WATER, r"pond|lake|water|nala|nalla|canal|stream|river|\btank\b|drain|wetland|reservoir|\bpool\b"),
        (FeatureKind.EXCLUSION, r"exclu|no ?-?go|restrict|keep ?-?out|forest|grave|temple|\brocks?\b|slope|dump|encroach|reserved|hazard|swamp|prohibit|\bhill"),
        (FeatureKind.EXISTING_STRUCTURE, r"existing|\btower\b|pylon|\bpole\b|\bwell\b|\btrees?\b|\bhut\b"),
        (FeatureKind.BUILDING, r"building|office|substation|sub-station|control room|warehouse|\bhouse\b|\bblock\b|\bplant room"),
        (FeatureKind.BOUNDARY, r"boundar|\bplot\b|\bsite\b|fence|perimeter|\bland\b|\blease\b|propert|parcel|plant area|\bcompound\b|\bextent"),
    )
)  # fmt: skip

_NORMALISE = re.compile(r"[_\s]+")


class SiteImportError(ValueError):
    pass


@dataclass
class RawFeature:
    """A shape read from a file, still in the file's own frame.

    `labels` are the texts used to classify it, most specific first (placemark name, folder names, DXF layer).
    """

    points: list[Coord]
    closed: bool
    labels: tuple[str, ...] = ()
    name: str | None = None
    holes: list[list[Coord]] = field(default_factory=list)
    kind: FeatureKind | None = None
    id: str | None = None
    height_m: float | None = None
    width_m: float | None = None
    setback_m: float | None = None
    attributes: dict[str, str | float | bool] = field(default_factory=dict)


@dataclass
class ParsedSite:
    features: list[RawFeature]
    frame: Frame
    epsg: int | None = None
    warnings: list[str] = field(default_factory=list)


def normalise(text: str) -> str:
    return _NORMALISE.sub(" ", text.replace("-", " ")).strip().lower()


def parse_kind_mapping(mapping: Mapping[str, str] | None) -> dict[str, FeatureKind | None]:
    out: dict[str, FeatureKind | None] = {}
    for key, value in (mapping or {}).items():
        v = value.strip().lower()
        if v == IGNORE:
            out[normalise(key)] = None
            continue
        try:
            out[normalise(key)] = FeatureKind(v)
        except ValueError:
            raise SiteImportError(f"kind_mapping[{key!r}] = {value!r} is not a feature kind; kinds: {[k.value for k in FeatureKind]} or 'ignore'") from None
    return out


Classification = FeatureKind | Literal["ignored"] | None


def classify(labels: Iterable[str], mapping: Mapping[str, FeatureKind | None], rules: bool = True) -> Classification:
    """Kind for a feature from its labels: explicit mapping (exact, then substring) beats the rules table. None means unclassified."""
    norm = [n for n in (normalise(label) for label in labels) if n]
    for exact in (True, False):
        for label in norm:
            for key, kind in mapping.items():
                if (key == label) if exact else (key in label):
                    return kind if kind is not None else "ignored"
    if rules:
        for label in norm:
            for kind, pattern in KIND_RULES:
                if pattern.search(label):
                    return kind
    return None


def find_number(attrs: Mapping[str, object], keys: tuple[str, ...]) -> float | None:
    lowered = {str(k).strip().lower(): v for k, v in attrs.items()}
    for key in keys:
        if key in lowered:
            try:
                value = float(str(lowered[key]).strip().rstrip("m").strip())
            except ValueError:
                continue
            if math.isfinite(value):
                return value
    return None


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "-", text.strip()).strip("-_").lower()[:48].strip("-_")


def drop_repeats(points: list[Coord], closed: bool) -> list[Coord]:
    out: list[Coord] = []
    for p in points:
        if not out or p != out[-1]:
            out.append(p)
    if closed and len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def project_points(frame_points: list[Coord], transformer: Transformer) -> list[Coord]:
    if not frame_points:
        return []
    xs, ys = transformer.transform([p[0] for p in frame_points], [p[1] for p in frame_points])
    return list(zip(xs, ys, strict=True))


@dataclass(frozen=True)
class Georef:
    """Where an imported file sits on Earth, for frames that do not carry coordinates themselves."""

    origin: LonLat | None = None


@dataclass
class Placed:
    geometry: SiteGeometry
    added: list[str]
    replaced: list[str]
    removed: list[str]
    skipped: list[str]
    warnings: list[str]


def _kind_for(raw: RawFeature, mapping: Mapping[str, FeatureKind | None]) -> Classification:
    if mapping and (explicit := classify(raw.labels, mapping, rules=False)) is not None:
        return explicit
    return raw.kind or classify(raw.labels, {})


def resolve_kinds(parsed: ParsedSite, mapping: Mapping[str, FeatureKind | None]) -> tuple[list[tuple[RawFeature, FeatureKind]], list[str]]:
    """Assign kinds, dropping shapes that cannot be used. Returns the usable features and a skip message per dropped shape."""
    kept: list[tuple[RawFeature, FeatureKind]] = []
    skipped: list[str] = []
    unclassified: list[RawFeature] = []
    for raw in parsed.features:
        kind = _kind_for(raw, mapping)
        if kind == "ignored":
            continue
        if kind is None:
            unclassified.append(raw)
            continue
        if not raw.closed and kind not in LINE_KINDS:
            skipped.append(f"{describe(raw)} is an open path but {kind.value} needs a closed polygon")
            continue
        kept.append((raw, kind))
    area_kinds = {k for _, k in kept}
    polygons = [r for r in unclassified if r.closed]
    if polygons and len(polygons) == 1 and FeatureKind.BOUNDARY not in area_kinds and FeatureKind.ROOF not in area_kinds:
        only = polygons[0]
        kept.append((only, FeatureKind.BOUNDARY))
        parsed.warnings.append(f"{describe(only)} matched no kind rule; it is the only polygon, so it was taken as the boundary (override with kind_mapping)")
        unclassified.remove(only)
    for raw in unclassified:
        skipped.append(f"{describe(raw)} matched no kind rule; map it with kind_mapping, e.g. {{{(raw.labels or ('<name>',))[0]!r}: 'exclusion'}}")
    return kept, skipped


def describe(raw: RawFeature) -> str:
    label = raw.name or (raw.labels[0] if raw.labels else None)
    return f"{'polygon' if raw.closed else 'path'} {label!r}" if label else ("unnamed polygon" if raw.closed else "unnamed path")


def to_lonlat(points: list[Coord], parsed: ParsedSite, georef: Georef, existing: SiteGeometry | None) -> list[Coord]:
    """Convert points of the file's frame to lon/lat."""
    if parsed.frame == "lonlat":
        return points
    if parsed.frame == "projected":
        assert parsed.epsg is not None
        return project_points(points, Transformer.from_crs(CRS.from_epsg(parsed.epsg), "EPSG:4326", always_xy=True))
    origin = georef.origin or (existing.origin if existing else None)
    if origin is None:
        raise SiteImportError(
            "these coordinates are local metres with no georeference: pass origin_lon and origin_lat (where the file's 0,0 is), "
            "set site latitude/longitude on the project, or import a file in lon/lat first"
        )
    crs = utm_crs_for(origin.lon, origin.lat)
    to_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    ox, oy = to_xy.transform(origin.lon, origin.lat)
    return project_points([(ox + x, oy + y) for x, y in points], to_ll)


Shape = tuple[list[Coord], list[list[Coord]]]


def _to_local(
    shapes: list[Shape], parsed: ParsedSite, georef: Georef, base: SiteGeometry | None
) -> tuple[str, LonLat, list[Shape]]:
    """Crs, origin and the shapes in local metres. Local-frame files keep their own coordinates when the origin is known."""
    if parsed.frame == "local" and base is not None and (georef.origin is None or georef.origin == base.origin):
        return base.crs, base.origin, [(_snap(pts), [_snap(h) for h in holes]) for pts, holes in shapes]
    if parsed.frame == "local" and base is None and georef.origin is not None:
        return utm_crs_for(georef.origin.lon, georef.origin.lat), georef.origin, [(_snap(pts), [_snap(h) for h in holes]) for pts, holes in shapes]

    ll = [(to_lonlat(pts, parsed, georef, base), [to_lonlat(h, parsed, georef, base) for h in holes]) for pts, holes in shapes]
    if base is not None:
        crs, origin = base.crs, base.origin
    else:
        allp = [p for pts, _ in ll for p in pts]
        lons, lats = [p[0] for p in allp], [p[1] for p in allp]
        crs = utm_crs_for((min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2)
        xy = project_points(allp, Transformer.from_crs("EPSG:4326", crs, always_xy=True))
        lon0, lat0 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(min(x for x, _ in xy), min(y for _, y in xy))
        origin = LonLat(lon=round(lon0, ORIGIN_DECIMALS), lat=round(lat0, ORIGIN_DECIMALS))
    to_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    ox, oy = to_xy.transform(origin.lon, origin.lat)

    def local(points: list[Coord]) -> list[Coord]:
        return _snap([(x - ox, y - oy) for x, y in project_points(points, to_xy)])

    return crs, origin, [(local(pts), [local(h) for h in holes]) for pts, holes in ll]


def place(
    parsed: ParsedSite,
    existing: SiteGeometry | None,
    *,
    georef: Georef | None = None,
    kind_mapping: Mapping[str, str] | None = None,
    source: str | None = None,
    replace: bool = False,
) -> Placed:
    """Classify the parsed shapes and place them in local metres, merged into `existing` (or a new geometry).

    Features previously imported from the same `source` are replaced; features from other sources or added by hand are kept.
    With `replace`, `existing` is discarded and a new origin is chosen.
    """
    kept, skipped = resolve_kinds(parsed, parse_kind_mapping(kind_mapping))
    base = None if replace else existing
    if not kept:
        detail = "; ".join([*skipped[:5], *parsed.warnings])
        raise SiteImportError("no usable features found" + (f": {detail}" if detail else ""))
    crs, origin, local = _to_local([(r.points, r.holes) for r, _ in kept], parsed, georef or Georef(), base)
    far = max((abs(c) for pts, _ in local for p in pts for c in p), default=0.0)
    if far > FAR_M:
        parsed.warnings.append(f"some shapes lie {far / 1000:.0f} km from the site origin; check the file belongs to this site")

    previous = list(base.features) if base is not None else []
    discarded = list(existing.features) if replace and existing is not None else []
    keep_old = [f for f in previous if source is None or f.attributes.get("source") != source]
    old_ids = {f.id for f in previous}
    used = {f.id for f in keep_old}
    features: list[SiteFeature] = []
    for (raw, kind), (pts, holes) in zip(kept, local, strict=True):
        pts = drop_repeats(pts, raw.closed)
        fid = raw.id or _fresh_id(raw, kind, used)
        used.add(fid)
        attrs = dict(raw.attributes)
        if source is not None:
            attrs["source"] = source
        shape: dict[str, object] = {"polygon" if raw.closed else "polyline": _pts(pts)}
        if holes:
            shape["holes"] = tuple(_pts(drop_repeats(h, True)) for h in holes)
        try:
            features.append(
                SiteFeature(
                    id=fid, kind=kind, name=raw.name, height_m=raw.height_m, width_m=raw.width_m, setback_m=raw.setback_m,
                    attributes=attrs, **shape,
                )  # fmt: skip
            )
        except ValidationError as e:
            skipped.append(f"{describe(raw)} skipped: {e.errors()[0]['msg'].removeprefix('Value error, ')}")
    if not features:
        raise SiteImportError("no valid features: " + "; ".join(skipped[:5]))

    new = {f.id: f for f in features}
    previous_by_id = {f.id: f for f in previous}
    replaced = sorted(i for i in new if i in old_ids and previous_by_id[i] != new[i])
    merged: list[SiteFeature] = []
    for f in previous:
        if f.id in new:
            merged.append(new[f.id])
        elif f in keep_old:
            merged.append(f)
    kept_ids = {m.id for m in merged}
    merged += [f for f in features if f.id not in kept_ids]
    geometry = base.with_features(merged) if base is not None else SiteGeometry(crs=crs, origin=origin, features=tuple(merged))
    removed = sorted({f.id for f in [*previous, *discarded] if f not in keep_old and f.id not in new})
    return Placed(geometry, list(new), replaced, removed, skipped, list(parsed.warnings))


def _snap(points: list[Coord]) -> list[Coord]:
    return [(round(x, MM) + 0.0, round(y, MM) + 0.0) for x, y in points]


def _pts(points: list[Coord]) -> tuple[Point, ...]:
    return tuple(Point(x=x, y=y) for x, y in points)


def _fresh_id(raw: RawFeature, kind: FeatureKind, taken: set[str]) -> str:
    """Id from the feature's name, else the kind; numbered when already used so re-imports of one file stay stable."""
    named = bool(raw.name and slug(raw.name))
    base = slug(raw.name or "") if named else kind.value.replace("_", "-")
    if named and base not in taken:
        return base
    n = 1 if named else 0
    while f"{base}-{n + 1}" in taken:
        n += 1
    return f"{base}-{n + 1}"

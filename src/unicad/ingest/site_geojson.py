"""GeoJSON site files: Polygon, MultiPolygon, LineString and MultiLineString features."""

import json
import re
from pathlib import Path
from typing import Any

from unicad.domain.site import FeatureKind
from unicad.ingest.site_common import (
    HEIGHT_KEYS,
    SETBACK_KEYS,
    WIDTH_KEYS,
    Coord,
    ParsedSite,
    RawFeature,
    SiteImportError,
    drop_repeats,
    find_number,
)

NAME_KEYS = ("name", "title", "label", "id")
LABEL_KEYS = ("kind", "type", "layer", "category", "class", "landuse", "feature")
NUMERIC_KEYS = {*HEIGHT_KEYS, *WIDTH_KEYS, *SETBACK_KEYS}
EPSG_NAME = re.compile(r"EPSG:{1,2}(\d+)", re.I)
KINDS = {k.value for k in FeatureKind}


def _pick(props: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    lowered = {str(k).lower(): v for k, v in props.items()}
    for key in keys:
        value = lowered.get(key)
        if isinstance(value, str | int | float) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    return None


def _coords(raw: Any, closed: bool) -> list[Coord] | None:
    try:
        pts = [(float(p[0]), float(p[1])) for p in raw]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return drop_repeats(pts, closed)


def _declared_epsg(doc: dict[str, Any]) -> int | None:
    crs = doc.get("crs")
    name = (crs.get("properties") or {}).get("name") if isinstance(crs, dict) else None
    if not isinstance(name, str) or "CRS84" in name.upper():
        return None
    m = EPSG_NAME.search(name)
    if m is None:
        raise SiteImportError(f"unsupported GeoJSON crs {name!r}; use WGS 84 lon/lat or an 'EPSG:<code>' name")
    code = int(m.group(1))
    return None if code == 4326 else code


def parse_geojson(path: Path) -> ParsedSite:
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SiteImportError(f"{path.name} is not valid GeoJSON: {e}") from None
    if not isinstance(doc, dict):
        raise SiteImportError(f"{path.name}: GeoJSON must be an object")
    epsg = _declared_epsg(doc)
    parsed = ParsedSite([], "projected" if epsg else "lonlat", epsg)
    doc_type = doc.get("type")
    if doc_type == "FeatureCollection":
        features = doc.get("features") or []
    elif doc_type == "Feature":
        features = [doc]
    elif doc_type in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
        features = [{"type": "Feature", "geometry": doc, "properties": {}}]
    else:
        raise SiteImportError(f"{path.name}: expected a GeoJSON FeatureCollection, Feature or geometry, got type {doc_type!r}")

    ignored: dict[str, int] = {}
    for feat in features:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        props = feat.get("properties") if isinstance(feat, dict) and isinstance(feat.get("properties"), dict) else {}
        if not isinstance(geom, dict):
            continue
        gtype, coords = geom.get("type"), geom.get("coordinates")
        name = _pick(props, NAME_KEYS)
        hint = _pick(props, LABEL_KEYS)
        hint_kind = hint.lower().replace(" ", "_") if hint else None
        preset = FeatureKind(hint_kind) if hint_kind in KINDS else None
        labels = tuple(x for x in (name, hint) if x)
        shapes: list[tuple[list[Coord], list[list[Coord]], bool]] = []
        if gtype in ("Polygon", "MultiPolygon"):
            for poly in [coords] if gtype == "Polygon" else (coords or []):
                rings = [_coords(r, True) for r in poly or []]
                if not rings or rings[0] is None or len(rings[0]) < 3:
                    parsed.warnings.append(f"{name or gtype}: polygon without a usable outer ring; skipped")
                    continue
                shapes.append((rings[0], [r for r in rings[1:] if r and len(r) >= 3], True))
        elif gtype in ("LineString", "MultiLineString"):
            for line in [coords] if gtype == "LineString" else (coords or []):
                pts = _coords(line, False)
                if pts is None or len(pts) < 2:
                    parsed.warnings.append(f"{name or gtype}: path without 2 usable points; skipped")
                    continue
                shapes.append((pts, [], False))
        else:
            ignored[str(gtype)] = ignored.get(str(gtype), 0) + 1
            continue
        attributes = {
            str(k): v for k, v in props.items()
            if isinstance(v, str | int | float | bool) and str(k).lower() not in NUMERIC_KEYS | {"name"}
        }  # fmt: skip
        for i, (pts, holes, closed) in enumerate(shapes, 1):
            parsed.features.append(
                RawFeature(
                    pts, closed, labels, f"{name} {i}" if name and len(shapes) > 1 else name, holes=holes, kind=preset,
                    height_m=find_number(props, HEIGHT_KEYS), width_m=find_number(props, WIDTH_KEYS), setback_m=find_number(props, SETBACK_KEYS),
                    attributes=dict(attributes),
                )  # fmt: skip
            )
    for gtype, n in ignored.items():
        parsed.warnings.append(f"{n} {gtype} feature(s) ignored: the site model holds polygons and paths only")
    return parsed

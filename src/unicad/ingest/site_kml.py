"""KML and KMZ (Google Earth) site files: polygons and paths in lon/lat."""

import zipfile
from pathlib import Path
from xml.etree import ElementTree

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

MAX_KMZ_BYTES = 100 * 1024 * 1024
NUMERIC_KEYS = {*HEIGHT_KEYS, *WIDTH_KEYS, *SETBACK_KEYS}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ElementTree.Element | None, name: str) -> ElementTree.Element | None:
    return next((c for c in el if _local(c.tag) == name), None) if el is not None else None


def _text(el: ElementTree.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def read_kml_bytes(path: Path) -> bytes:
    if not zipfile.is_zipfile(path):
        return path.read_bytes()
    with zipfile.ZipFile(path) as z:
        kmls = [i for i in z.infolist() if i.filename.lower().endswith(".kml")]
        if not kmls:
            raise SiteImportError(f"{path.name} is a zip archive without a .kml file")
        chosen = next((i for i in kmls if Path(i.filename).name.lower() == "doc.kml"), kmls[0])
        if chosen.file_size > MAX_KMZ_BYTES:
            raise SiteImportError(f"{chosen.filename} in {path.name} is larger than {MAX_KMZ_BYTES // 2**20} MB uncompressed")
        return z.read(chosen)


def _coords(text: str, warnings: list[str], label: str) -> list[Coord]:
    out: list[Coord] = []
    for token in text.split():
        parts = token.split(",")
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            warnings.append(f"{label}: unreadable coordinate {token!r}")
            return []
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            warnings.append(f"{label}: coordinate {token!r} is not lon,lat in degrees")
            return []
        out.append((lon, lat))
    return out


def _extended(placemark: ElementTree.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for el in placemark.iter():
        tag = _local(el.tag)
        if tag == "Data" and (name := el.get("name")):
            out[name] = _text(_child(el, "value"))
        elif tag == "SimpleData" and (name := el.get("name")):
            out[name] = _text(el)
    return out


def _ring(el: ElementTree.Element | None, warnings: list[str], label: str) -> list[Coord]:
    ring = _child(el, "LinearRing")
    return drop_repeats(_coords(_text(_child(ring, "coordinates")), warnings, label), True)


def parse_kml(path: Path) -> ParsedSite:
    try:
        root = ElementTree.fromstring(read_kml_bytes(path))
    except ElementTree.ParseError as e:
        raise SiteImportError(f"{path.name} is not valid KML: {e}") from None
    parsed = ParsedSite([], "lonlat")
    points_skipped = 0

    def placemark(pm: ElementTree.Element, folders: list[str]) -> None:
        nonlocal points_skipped
        name = _text(_child(pm, "name")) or None
        label = name or "unnamed placemark"
        ext = _extended(pm)
        labels = tuple(x for x in [name, *reversed(folders)] if x)
        shapes: list[RawFeature] = []
        for el in pm.iter():
            tag = _local(el.tag)
            if tag == "Polygon":
                pts = _ring(_child(el, "outerBoundaryIs"), parsed.warnings, label)
                if len(pts) < 3:
                    parsed.warnings.append(f"{label}: polygon has fewer than 3 usable points; skipped")
                    continue
                holes = [h for inner in el if _local(inner.tag) == "innerBoundaryIs" if len(h := _ring(inner, parsed.warnings, label)) >= 3]
                shapes.append(RawFeature(pts, True, labels, name, holes=holes))
            elif tag == "LineString":
                pts = drop_repeats(_coords(_text(_child(el, "coordinates")), parsed.warnings, label), False)
                if len(pts) < 2:
                    parsed.warnings.append(f"{label}: path has fewer than 2 usable points; skipped")
                    continue
                shapes.append(RawFeature(pts, False, labels, name))
            elif tag == "Point":
                points_skipped += 1
        for i, raw in enumerate(shapes, 1):
            if len(shapes) > 1 and name:
                raw.name = f"{name} {i}"
            raw.height_m = find_number(ext, HEIGHT_KEYS)
            raw.width_m = find_number(ext, WIDTH_KEYS)
            raw.setback_m = find_number(ext, SETBACK_KEYS)
            raw.attributes = {k: v for k, v in ext.items() if v and k.strip().lower() not in NUMERIC_KEYS}
            if folders:
                raw.attributes["folder"] = "/".join(folders)
            parsed.features.append(raw)

    def walk(el: ElementTree.Element, folders: list[str]) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag == "Folder":
                name = _text(_child(child, "name"))
                walk(child, [*folders, name] if name else folders)
            elif tag == "Document":
                walk(child, folders)
            elif tag == "Placemark":
                placemark(child, folders)

    if _local(root.tag) == "Placemark":
        placemark(root, [])
    else:
        walk(root, [])
    if points_skipped:
        parsed.warnings.append(f"{points_skipped} point placemark(s) ignored: the site model holds polygons and paths only")
    return parsed

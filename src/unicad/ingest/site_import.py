"""Entry point for site files: pick the reader by format, then classify and place the shapes in local metres."""

import re
from collections.abc import Mapping
from pathlib import Path

from unicad.domain.site import LonLat, SiteGeometry
from unicad.ingest.site_common import Georef, Placed, SiteImportError, place
from unicad.ingest.site_coords import CoordinateSystem, parse_coords
from unicad.ingest.site_dxf import parse_dxf
from unicad.ingest.site_geojson import parse_geojson
from unicad.ingest.site_kml import parse_kml

FORMATS = ("kml", "kmz", "geojson", "dxf", "coords")
EXTENSIONS = {
    ".kml": "kml", ".kmz": "kmz", ".geojson": "geojson", ".json": "geojson", ".dxf": "dxf",
    ".csv": "coords", ".txt": "coords", ".xyz": "coords", ".pts": "coords",
}  # fmt: skip
ALIASES = {"geo": "geojson", "csv": "coords", "txt": "coords"}
MAX_FILE_BYTES = 200 * 1024 * 1024


def detect_format(path: Path, fmt: str | None) -> str:
    if fmt is not None:
        f = fmt.strip().lower().lstrip(".")
        f = ALIASES.get(f, f)
        if f not in FORMATS:
            raise SiteImportError(f"unknown site format {fmt!r}; formats: {list(FORMATS)}")
        return f
    try:
        return EXTENSIONS[path.suffix.lower()]
    except KeyError:
        raise SiteImportError(f"cannot tell the format of {path.name}; pass format= one of {list(FORMATS)}") from None


def parse_epsg(crs: str | int) -> int:
    m = re.fullmatch(r"(?:epsg:)?(\d{4,6})", str(crs).strip(), re.I)
    if m is None:
        raise SiteImportError(f"source_crs {crs!r} must be an EPSG code such as 'EPSG:32643'")
    return int(m.group(1))


def import_site(
    path: Path,
    existing: SiteGeometry | None,
    *,
    format: str | None = None,
    kind_mapping: Mapping[str, str] | None = None,
    origin: LonLat | None = None,
    source_crs: str | None = None,
    units: str | None = None,
    coordinate_system: CoordinateSystem | None = None,
    replace: bool = False,
) -> Placed:
    """Read `path` and merge its shapes into `existing`. `origin` is where a local-metre file's 0,0 lies; `source_crs` marks its coordinates as projected metres."""
    if not path.is_file():
        raise SiteImportError(f"no such file: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise SiteImportError(f"{path.name} is larger than {MAX_FILE_BYTES // 2**20} MB")
    fmt = detect_format(path, format)
    epsg = parse_epsg(source_crs) if source_crs else None
    if fmt in ("kml", "kmz"):
        parsed = parse_kml(path)
    elif fmt == "geojson":
        parsed = parse_geojson(path)
    elif fmt == "dxf":
        parsed = parse_dxf(path, units, epsg)
    else:
        parsed = parse_coords(path, coordinate_system)
        if epsg is not None and parsed.frame == "local":
            parsed.frame, parsed.epsg = "projected", epsg
    return place(parsed, existing, georef=Georef(origin), kind_mapping=kind_mapping, source=path.name, replace=replace)

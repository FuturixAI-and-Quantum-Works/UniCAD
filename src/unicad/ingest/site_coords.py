"""Plain coordinate lists: CSV or whitespace text with one point per line.

Blank lines separate shapes. A comment line before a shape names it: `# roof shed_a` (kind, then id/name).
Without a comment the first shape is the boundary. Points are lon/lat in degrees or local metres.
"""

import re
from pathlib import Path
from typing import Literal

from unicad.domain.site import LINE_KINDS, FeatureKind
from unicad.ingest.site_common import Coord, ParsedSite, RawFeature, SiteImportError, classify, drop_repeats

CoordinateSystem = Literal["lonlat", "latlon", "local"]
NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
SEPARATORS = re.compile(r"[,;\t ]+")
KINDS = {k.value for k in FeatureKind}
INDIA_LON, INDIA_LAT = (60.0, 100.0), (5.0, 40.0)


def _numbers(line: str) -> list[float] | None:
    tokens = [t for t in SEPARATORS.split(line.strip()) if t]
    values = [float(t) for t in tokens if NUMBER.match(t)]
    return values[:2] if len(values) >= 2 and len(values) >= len(tokens) - 1 else None


def detect_system(points: list[Coord]) -> tuple[CoordinateSystem, str | None]:
    """Guess the coordinate system. Only Indian lon/lat (either order) is recognised; anything else is local metres."""
    if all(INDIA_LON[0] <= x <= INDIA_LON[1] and INDIA_LAT[0] <= y <= INDIA_LAT[1] for x, y in points):
        return "lonlat", None
    if all(INDIA_LAT[0] <= x <= INDIA_LAT[1] and INDIA_LON[0] <= y <= INDIA_LON[1] for x, y in points):
        return "latlon", "values read as lat,lon (latitude first) because they fall in India's range only in that order"
    return "local", None


def parse_coords(path: Path, system: CoordinateSystem | None = None) -> ParsedSite:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks: list[tuple[str | None, list[Coord]]] = []
    header: str | None = None
    points: list[Coord] = []
    header_order: CoordinateSystem | None = None

    def flush() -> None:
        nonlocal header, points
        if points:
            blocks.append((header, points))
        header, points = None, []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("#"):
            if points:
                flush()
            header = stripped.lstrip("#").strip() or None
        elif (nums := _numbers(stripped)) is not None:
            points.append((nums[0], nums[1]))
        elif not blocks and not points:
            lowered = stripped.lower()
            if "lat" in lowered and "lon" in lowered:
                header_order = "latlon" if lowered.index("lat") < lowered.index("lon") else "lonlat"
        else:
            raise SiteImportError(f"{path.name}: cannot read a coordinate from line {stripped!r}")
    flush()
    if not blocks:
        raise SiteImportError(f"{path.name}: no coordinates found; expected lines like '75.1234, 26.5678' or '120.5 80.25'")

    chosen = system or header_order
    note = None
    if chosen is None:
        chosen, note = detect_system([p for _, pts in blocks for p in pts])
    parsed = ParsedSite([], "local" if chosen == "local" else "lonlat")
    if note:
        parsed.warnings.append(note)
    for n, (head, pts) in enumerate(blocks):
        if chosen == "latlon":
            pts = [(y, x) for x, y in pts]
        words = head.split(None, 1) if head else []
        kind_word = words[0] if words else None
        label = words[1] if len(words) > 1 else None
        kind = FeatureKind(kind_word.lower()) if kind_word and kind_word.lower() in KINDS else None
        labels = tuple(w for w in (kind_word, label) if w) if kind is None else ((label,) if label else ())
        if head is None and n == 0:
            kind = FeatureKind.BOUNDARY
        likely = kind or classify(labels, {})
        closed = likely not in LINE_KINDS or pts[0] == pts[-1]
        pts = drop_repeats(pts, closed)
        if len(pts) < (3 if closed else 2):
            parsed.warnings.append(f"shape {head or n + 1!r} has too few points; skipped")
            continue
        parsed.features.append(RawFeature(pts, closed, labels, label, kind=kind))
    return parsed

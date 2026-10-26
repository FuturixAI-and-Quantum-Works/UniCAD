"""The DXF writer rules of docs/compat.md: a header setup every drafting writer calls, and a checker.

`violations(doc)` lists every way a document breaks the rules LibreCAD 2.2.1.6 needs to print it
as written; an empty list means compliant.
"""

import re

from ezdxf.document import Drawing
from ezdxf.enums import InsertUnits

INSUNITS_MM = 4
MIN_LINEWEIGHT = 13
DIM_HEADER: dict[str, float] = {
    "$DIMTXT": 2.5,
    "$DIMASZ": 2.5,
    "$DIMEXE": 1.25,
    "$DIMEXO": 0.6,
    "$DIMGAP": 0.8,
    "$DIMDEC": 1,
    "$DIMSCALE": 1.0,
    "$DIMLFAC": 1.0,
}
# .lff stems shipped with LibreCAD 2.2.1.6 (usr/share/librecad/fonts); the style name picks the font.
LIBRECAD_FONTS = frozenset(
    "amiri-regular azomix azomix_i cursive cyrillic_ii gothgbt gothgrt gothitt greek_ol greekc greekcs greekp "
    "greeks iso iso3098 iso3098_i italicc italiccs italict kochigothic kochimincho kst32b lc_opengost-ar "
    "lc_opengost-br noto-sans-sc noto-sans-tc opengosttypea-regular opengosttypeb-regular romanc romancs romand "
    "romanp romans romansi romant scriptc scripts simplex standard syastro symap symath symbol symbol_misc1 "
    "symbol_misc2 symeteo symusic unicode wqy-microhei".split()
)
UNICODE_STYLES = frozenset({"unicode", "iso3098"})
FORBIDDEN = frozenset({"MULTILEADER", "MLEADER", "IMAGE", "ACAD_TABLE", "WIPEOUT", "XLINE", "RAY"})
HATCH_PATTERNS = frozenset({"SOLID", "ANSI31"})
_MTEXT_CODE = re.compile(r"\\[^P]|[{}]|%%[uUoO]")
_TEXT_CODE = re.compile(r"%%[uUoO]")


def prepare(doc: Drawing) -> None:
    """Millimetre units and $DIM* header values sized for a paper-millimetre drawing."""
    doc.units = InsertUnits.Millimeters
    doc.header["$INSUNITS"] = INSUNITS_MM
    doc.header["$MEASUREMENT"] = 1
    for var, value in DIM_HEADER.items():
        doc.header[var] = value


def plain(text: str) -> str:
    """Text safe to draw: LibreCAD's underscore glyph is 11 font units wide against a 9-unit cap
    height (standard, iso and unicode .lff), so one `_` reads as a doubled `__`. Identifiers get
    a hyphen instead."""
    return text.replace("_", "-")


def _texts(e) -> list[str]:
    t = e.dxftype()
    if t in ("TEXT", "ATTRIB", "ATTDEF"):
        return [e.dxf.text]
    if t == "MTEXT":
        return [e.text]
    return []


def violations(doc: Drawing) -> list[str]:
    out: list[str] = []
    if doc.header.get("$INSUNITS", 0) != INSUNITS_MM:
        out.append(f"$INSUNITS is {doc.header.get('$INSUNITS', 0)}, not {INSUNITS_MM}")
    for var, value in DIM_HEADER.items():
        got = doc.header.get(var)
        if got is None or abs(float(got) - value) > 1e-9:
            out.append(f"header {var} is {got}, not {value}")
    for layout in doc.layouts:
        if not layout.is_modelspace and len(layout):
            out.append(f"paperspace layout {layout.name!r} has {len(layout)} entities")
    used_blocks = {e.dxf.name for e in doc.modelspace().query("INSERT")}
    pending = list(used_blocks)
    while pending:
        blk = doc.blocks.get(pending.pop())
        if blk is None:
            continue
        for e in blk.query("INSERT"):
            if e.dxf.name not in used_blocks:
                used_blocks.add(e.dxf.name)
                pending.append(e.dxf.name)
    spaces = [("modelspace", doc.modelspace())] + [(f"block {n}", doc.blocks.get(n)) for n in sorted(used_blocks) if doc.blocks.get(n)]
    layers: set[str] = set()
    for where, space in spaces:
        for e in space:
            t = e.dxftype()
            layer = e.dxf.get("layer", "0")
            if where == "modelspace" or layer != "0":
                layers.add(layer)
            lw = e.dxf.get("lineweight", -1)
            if lw == 0:
                out.append(f"{where}: {t} has lineweight 0")
            if t in FORBIDDEN:
                out.append(f"{where}: forbidden entity {t}")
            if t == "HATCH" and e.dxf.pattern_name.upper() not in HATCH_PATTERNS:
                out.append(f"{where}: hatch pattern {e.dxf.pattern_name}")
            if t in ("LWPOLYLINE", "POLYLINE") and (e.dxf.get("const_width", 0) or 0) > 0:
                out.append(f"{where}: {t} with width")
            for text in _texts(e):
                style = e.dxf.get("style", "Standard").lower()
                if style not in LIBRECAD_FONTS:
                    out.append(f"{where}: {t} {text[:30]!r} uses style {style!r}, which LibreCAD has no font for")
                if any(ord(c) > 127 for c in text) and style not in UNICODE_STYLES:
                    out.append(f"{where}: {t} {text[:30]!r} has non-ASCII text in style {style!r}")
                if t == "MTEXT" and _MTEXT_CODE.search(text):
                    out.append(f"{where}: MTEXT {text[:30]!r} has inline codes")
                if _TEXT_CODE.search(text):
                    out.append(f"{where}: {t} {text[:30]!r} uses %%u / %%o")
                if "_" in text and e.dxf.get("invisible", 0) == 0 and t in ("TEXT", "MTEXT"):
                    out.append(f"{where}: {t} {text[:30]!r} draws an underscore")
    for name in sorted(layers):
        if name not in doc.layers:
            out.append(f"layer {name!r} is used but not defined")
            continue
        lw = doc.layers.get(name).dxf.get("lineweight", -3)
        if lw < MIN_LINEWEIGHT:
            out.append(f"layer {name!r} has lineweight {lw}")
    return sorted(set(out))

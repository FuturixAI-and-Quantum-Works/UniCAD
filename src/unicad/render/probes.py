"""Probe DXFs for the LibreCAD compatibility matrix: one small drawing per DXF feature.

Each probe draws on a 200 x 120 mm sheet in paper millimetres and labels what it expects.
tools/compat_matrix.py renders them and records verdicts after looking at the evidence.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf import colors
from ezdxf.document import Drawing
from ezdxf.enums import MTextEntityAlignment, TextEntityAlignment
from ezdxf.math import Vec2
from ezdxf.layouts import Modelspace
from ezdxf.render import ConnectionSide
from ezdxf.tools import pattern as ezpattern

SHEET_MM = (200.0, 120.0)
ASSET_DIR = Path("library/compat")


@dataclass(frozen=True)
class Probe:
    id: str
    category: str
    feature: str
    build: Callable[[Drawing, Modelspace], None]
    guarded: bool = True


PROBES: list[Probe] = []


def probe(id: str, category: str, feature: str, guarded: bool = True):
    def register(fn: Callable[[Drawing, Modelspace], None]):
        PROBES.append(Probe(id, category, feature, fn, guarded))
        return fn

    return register


def label(msp: Modelspace, x: float, y: float, text: str, h: float = 2.2, **attribs) -> None:
    msp.add_text(text, height=h, dxfattribs={"layer": "LABEL", "insert": (x, y), **attribs})


def cross(msp: Modelspace, x: float, y: float, r: float = 1.5) -> None:
    msp.add_line((x - r, y), (x + r, y), dxfattribs={"layer": "MARK", "color": 1})
    msp.add_line((x, y - r), (x, y + r), dxfattribs={"layer": "MARK", "color": 1})


def new_doc(units: int = 4) -> Drawing:
    doc = ezdxf.new("R2018", setup=["linetypes", "dimstyles"])
    doc.units = units
    doc.layers.get("0").dxf.lineweight = 25
    doc.layers.add("LABEL", color=250, lineweight=18)
    doc.layers.add("MARK", color=1, lineweight=18)
    for var, value in {"$DIMTXT": 2.5, "$DIMASZ": 2.5, "$DIMEXE": 1.25, "$DIMEXO": 0.6, "$DIMGAP": 0.8, "$DIMDEC": 1,
                       "$DIMSCALE": 1.0, "$DIMLFAC": 1.0}.items():
        doc.header[var] = value
    return doc


def solar_dimstyle(doc: Drawing) -> None:
    doc.dimstyles.new(
        "SOLAR",
        dxfattribs={
            "dimtxsty": "Standard", "dimtxt": 2.5, "dimasz": 2.5, "dimexe": 1.25, "dimexo": 0.6, "dimgap": 0.8,
            "dimdec": 1, "dimscale": 1.0, "dimlfac": 1.0, "dimtad": 1, "dimtih": 0, "dimtoh": 0, "dimdle": 0.0,
            "dimclrd": 0, "dimclrt": 0, "dimclre": 0, "dimblk": "", "dimsah": 0,
        },
    )
    doc.header["$DIMSTYLE"] = "SOLAR"


def _style(doc: Drawing, name: str, font: str) -> str:
    if name not in doc.styles:
        doc.styles.add(name, font=font)
    return name


@probe("text_styles", "TEXT", "TEXT style to font mapping and glyph coverage per LibreCAD .lff font")
def _(doc, msp):
    sample = "Solar 540Wp 33kV \u00b15% \u00d825 \u03a9 \u221a \u03b1 \u0394 \u00b2"
    stems = ["standard", "iso", "iso3098", "unicode", "simplex", "romans", "greekc", "opengosttypeb-regular", "amiri-regular"]
    for i, stem in enumerate(stems):
        doc.styles.add(stem, font="txt.shx")
        y = 114 - i * 7.2
        label(msp, 2, y, f"style {stem}", 2)
        msp.add_text(sample, height=3.2, dxfattribs={"style": stem, "insert": (62, y)})
    for k, (name, font) in enumerate([("ISOCP", "isocp.shx"), ("SOLAR_TXT", "iso.shx"), ("ARIAL_STYLE", "arial.ttf")]):
        doc.styles.add(name, font=font)
        y = 114 - (len(stems) + k) * 7.2
        label(msp, 2, y, f"style {name} font {font}", 2)
        msp.add_text(sample, height=3.2, dxfattribs={"style": name, "insert": (62, y)})


@probe("text_alignments", "TEXT", "TEXT horizontal/vertical alignments")
def _(doc, msp):
    aligns = [
        TextEntityAlignment.LEFT, TextEntityAlignment.CENTER, TextEntityAlignment.RIGHT,
        TextEntityAlignment.TOP_LEFT, TextEntityAlignment.TOP_CENTER, TextEntityAlignment.TOP_RIGHT,
        TextEntityAlignment.MIDDLE_LEFT, TextEntityAlignment.MIDDLE_CENTER, TextEntityAlignment.MIDDLE_RIGHT,
        TextEntityAlignment.BOTTOM_LEFT, TextEntityAlignment.BOTTOM_CENTER, TextEntityAlignment.BOTTOM_RIGHT,
        TextEntityAlignment.MIDDLE, TextEntityAlignment.ALIGNED, TextEntityAlignment.FIT,
    ]
    for i, a in enumerate(aligns):
        cx, cy = 22 + (i % 5) * 38, 95 - (i // 5) * 38
        cross(msp, cx, cy)
        t = msp.add_text(a.name, height=2, dxfattribs={"layer": "0"})
        if a in (TextEntityAlignment.ALIGNED, TextEntityAlignment.FIT):
            t.set_placement((cx, cy), (cx + 22, cy + 6), align=a)
            msp.add_line((cx, cy), (cx + 22, cy + 6), dxfattribs={"layer": "MARK", "color": 3})
        else:
            t.set_placement((cx, cy), align=a)


@probe("text_geometry", "TEXT", "TEXT height, rotation, width factor, oblique, mirror")
def _(doc, msp):
    for i, h in enumerate([1.8, 2.5, 3.5, 5.0, 7.0]):
        msp.add_text(f"H={h} Hello 123", height=h, dxfattribs={"insert": (3, 108 - i * 10 - h / 2)})
    for i, a in enumerate([0, 30, 60, 90, 135]):
        x, y = 100 + i * 20, 70
        cross(msp, x, y, 1)
        msp.add_text(f"R{a}", height=3, dxfattribs={"rotation": a, "insert": (x, y)})
    for i, w in enumerate([0.6, 1.0, 1.5]):
        msp.add_text(f"WIDTH {w}", height=3.5, dxfattribs={"width": w, "insert": (100, 100 - i * 8)})
    for i, ob in enumerate([0, 15, -15]):
        msp.add_text(f"OBLIQUE {ob}", height=3.5, dxfattribs={"oblique": ob, "insert": (150, 100 - i * 8)})
    msp.add_text("MIRROR X (flag 2)", height=3.5, dxfattribs={"insert": (160, 40), "text_generation_flag": 2})
    msp.add_text("UPSIDE DOWN (flag 4)", height=3.5, dxfattribs={"insert": (160, 30), "text_generation_flag": 4})
    msp.add_text("Normal reference", height=3.5, dxfattribs={"insert": (100, 20)})


@probe("text_special_codes", "TEXT", "TEXT %% codes: %%d %%p %%c %%u %%o")
def _(doc, msp):
    rows = ["45%%d C ambient", "Tolerance %%p5 %%", "Dia %%c110 mm HDPE", "%%uUnderlined%%u text", "%%oOverlined%%o text",
            "Mixed 33 kV%%p10%% at 50%%dC", "Percent sign 100%%%% load"]
    for i, r in enumerate(rows):
        label(msp, 3, 105 - i * 12, "in: " + r, 2.2)
        msp.add_text(r, height=4, dxfattribs={"insert": (95, 105 - i * 12)})


@probe("text_unicode_symbols", "Unicode", "Unicode symbols in TEXT: degree, plus-minus, sqrt, diameter, superscripts")
def _(doc, msp):
    _style(doc, "ROMANS", "romans.shx")
    _style(doc, "UNI", "unicode.shx")
    _style(doc, "ISO", "iso.shx")
    s = "\u00b0 \u00b1 \u221a \u00d8 \u00b2 \u00b3 \u00b5 \u03a9 \u2264 \u2265 \u2248 \u2030 \u0394 \u03c6 \u00bd \u20b9 kW\u00b7h"
    for i, (st, nm) in enumerate([("Standard", "Standard"), ("ROMANS", "romans"), ("UNI", "unicode"), ("ISO", "iso")]):
        y = 105 - i * 22
        label(msp, 3, y + 8, f"style: {nm}", 2.2)
        msp.add_text(s, height=5, dxfattribs={"style": st, "insert": (3, y)})
    label(msp, 3, 14, "single glyphs: 25\u00b0C  \u00b15%  \u221a3  \u00d825  10 mm\u00b2  1.5 \u03a9  \u20b95,00,000", 2.2)
    msp.add_text("25\u00b0C  \u00b15%  \u221a3  \u00d825  10 mm\u00b2  1.5 \u03a9  \u20b95,00,000", height=5,
                 dxfattribs={"insert": (3, 2)})


@probe("text_devanagari", "Unicode", "Devanagari (Hindi) text in TEXT and MTEXT")
def _(doc, msp):
    _style(doc, "UNI", "unicode.shx")
    _style(doc, "AMIRI", "amiri-regular")
    _style(doc, "NOTOSC", "noto-sans-sc")
    _style(doc, "ARIALU", "arial.ttf")
    hi = "\u0938\u094c\u0930 \u090a\u0930\u094d\u091c\u093e \u0938\u0902\u092f\u0902\u0924\u094d\u0930 \u096b\u0966\u0966 kWp"
    for i, st in enumerate(["Standard", "UNI", "AMIRI", "NOTOSC", "ARIALU"]):
        y = 105 - i * 14
        label(msp, 3, y, st, 2.2)
        msp.add_text(hi, height=5, dxfattribs={"style": st, "insert": (45, y)})
    msp.add_mtext(hi + "\\P" + hi, dxfattribs={"insert": (45, 28), "char_height": 5, "style": "ARIALU"})
    label(msp, 3, 28, "MTEXT (arial.ttf)", 2.2)


@probe("mtext_layout", "MTEXT", "MTEXT wrapping, line breaks, attachment points, line spacing")
def _(doc, msp):
    long = "Total installed capacity 100 MWac / 135 MWp. Evacuation at 33 kV through 8 blocks of 12.5 MVA."
    m = msp.add_mtext(long, dxfattribs={"insert": (3, 115), "char_height": 3, "width": 50})
    msp.add_line((3, 115), (3, 60), dxfattribs={"layer": "MARK", "color": 1})
    msp.add_line((53, 115), (53, 60), dxfattribs={"layer": "MARK", "color": 1})
    msp.add_mtext("Line one\\PLine two\\PLine three", dxfattribs={"insert": (70, 115), "char_height": 3.5})
    msp.add_mtext("Spacing 2.0\\PLine two\\PLine three", dxfattribs={"insert": (70, 90), "char_height": 3.5, "line_spacing_factor": 2.0})
    for i, ap in enumerate(MTextEntityAlignment):
        x, y = 120 + (i % 3) * 28, 105 - (i // 3) * 22
        cross(msp, x, y)
        t = msp.add_mtext(ap.name, dxfattribs={"char_height": 2.5, "width": 24})
        t.set_location((x, y), attachment_point=ap)
    msp.add_mtext("Rotated 30", dxfattribs={"insert": (10, 40), "char_height": 3.5, "rotation": 30})
    m2 = msp.add_mtext("Background fill\\Pmask", dxfattribs={"insert": (60, 40), "char_height": 3.5})
    msp.add_line((55, 36), (100, 36), dxfattribs={"layer": "MARK"})
    msp.add_line((55, 30), (100, 30), dxfattribs={"layer": "MARK"})
    m2.set_bg_color((255, 255, 150), scale=1.3)
    label(msp, 3, 8, "wrap box 50 mm (red) | spacing | attachment points | rotation | bg fill", 2)


@probe("mtext_inline_formats", "MTEXT", "MTEXT inline codes: height, colour, font, underline, overline, bold")
def _(doc, msp):
    rows = [
        "Plain then \\H1.5x;LARGER\\H1x; back",
        "Colour \\C1;red\\C3; green\\C5; blue\\C0; normal",
        "\\LUnderlined\\l and \\OOverlined\\o text",
        "\\Fromans|c0;romans font\\F txt|c0; txt font",
        "Width \\W2;wide\\W1; normal and \\Q15;oblique\\Q0; normal",
        "Tracking \\T2;spaced out\\T1; normal",
        "Escaped \\\\ backslash and \\{ braces \\}",
        "{\\C1;grouped red} outside group",
        "Non-breaking~space and %%c110 code",
    ]
    for i, r in enumerate(rows):
        msp.add_mtext(r, dxfattribs={"insert": (3, 114 - i * 12.5), "char_height": 3.5})


@probe("mtext_stacking", "MTEXT", "MTEXT stacked fractions and tolerances")
def _(doc, msp):
    rows = ["Fraction \\S1/2; slash", "Fraction \\S1#2; hash", "Tol \\S+0.5^-0.2; stacked", "10 mm\\S2^; superscript",
            "Isc 25 kA for 1 sec", "I\\S1^2;t = 25^2 x 1", "\\SA/B;C", "100 \\S1/4;\"  pipe"]
    for i, r in enumerate(rows):
        msp.add_mtext(r, dxfattribs={"insert": (3 + (i % 2) * 100, 112 - (i // 2) * 22), "char_height": 4})


@probe("attrib_attdef", "ATTRIB", "ATTDEF / ATTRIB in INSERT (visible and invisible)")
def _(doc, msp):
    blk = doc.blocks.new("TAGBLK")
    blk.add_circle((0, 0), 6)
    blk.add_attdef("TAG", (8, 0), "DEFTAG", dxfattribs={"height": 3})
    blk.add_attdef("HIDDEN", (8, -5), "DEFHID", dxfattribs={"height": 3, "flags": 1})
    blk.add_text("PLAIN TEXT", height=3, dxfattribs={"insert": (8, 5)})
    r = msp.add_blockref("TAGBLK", (20, 90))
    r.add_attrib("TAG", "INV-01", (28, 90), dxfattribs={"height": 3})
    r.add_attrib("HIDDEN", "H-01", (28, 85), dxfattribs={"height": 3, "flags": 1})
    r2 = msp.add_blockref("TAGBLK", (20, 50), dxfattribs={"rotation": 30, "xscale": 1.5, "yscale": 1.5})
    r2.add_auto_attribs({"TAG": "INV-02", "HIDDEN": "H-02"})
    label(msp, 70, 95, "expected: ATTRIB TAG value INV-01 drawn next to circle", 2.2)
    label(msp, 70, 88, "expected: ATTDEF prompts NOT drawn in model", 2.2)
    label(msp, 70, 81, "expected: hidden attrib not drawn", 2.2)


@probe("blocks_nested", "BLOCK", "nested blocks with scale, rotation, mirror and MINSERT")
def _(doc, msp):
    arrow = doc.blocks.new("ARROWL")
    arrow.add_lwpolyline([(0, 0), (10, 0), (10, 3), (4, 3), (4, 8), (0, 8)], close=True)
    arrow.add_text("A", height=3, dxfattribs={"insert": (1, 1)})
    pair = doc.blocks.new("PAIR")
    pair.add_blockref("ARROWL", (0, 0))
    pair.add_blockref("ARROWL", (14, 0), dxfattribs={"rotation": 90, "xscale": 0.5, "yscale": 0.5})
    pair.add_circle((0, 0), 1)
    specs = [("1:1", {}), ("rot 30", {"rotation": 30}), ("scale 0.5", {"xscale": 0.5, "yscale": 0.5}),
             ("mirror X (xscale -1)", {"xscale": -1}), ("mirror Y (yscale -1)", {"yscale": -1}),
             ("non-uniform 2 x 0.5", {"xscale": 2, "yscale": 0.5}), ("rot 45 + mirror", {"rotation": 45, "xscale": -1})]
    for i, (nm, kw) in enumerate(specs):
        x, y = 20 + (i % 4) * 45, 90 - (i // 4) * 40
        msp.add_blockref("PAIR", (x, y), dxfattribs=kw)
        label(msp, x - 8, y - 14, nm, 2)
    msp.add_blockref("ARROWL", (20, 15), dxfattribs={"column_count": 4, "row_count": 2, "column_spacing": 14, "row_spacing": 12})
    label(msp, 20, 3, "MINSERT 4 x 2", 2)


@probe("blocks_byblock_inheritance", "BLOCK", "BYBLOCK colour / layer 0 inheritance inside blocks")
def _(doc, msp):
    doc.layers.add("RED_L", color=1, lineweight=25)
    doc.layers.add("BLUE_L", color=5, lineweight=25)
    blk = doc.blocks.new("INHERIT")
    blk.add_line((0, 0), (15, 0), dxfattribs={"layer": "0", "color": 0})
    blk.add_circle((7.5, 8), 4, dxfattribs={"layer": "0", "color": 256})
    blk.add_line((0, 16), (15, 16), dxfattribs={"layer": "0", "color": 3})
    blk.add_text("BB", height=3, dxfattribs={"layer": "0", "color": 0, "insert": (4, 4)})
    msp.add_blockref("INHERIT", (10, 80), dxfattribs={"layer": "RED_L"})
    msp.add_blockref("INHERIT", (50, 80), dxfattribs={"layer": "BLUE_L"})
    msp.add_blockref("INHERIT", (90, 80), dxfattribs={"layer": "BLUE_L", "color": 2})
    label(msp, 10, 60, "layer RED_L", 2)
    label(msp, 50, 60, "layer BLUE_L", 2)
    label(msp, 90, 60, "BLUE_L + INSERT colour yellow", 2)
    label(msp, 10, 40, "expect: BYBLOCK line/text take INSERT colour, BYLAYER circle takes layer 0 / insert layer", 2)
    label(msp, 10, 33, "expect: third line stays green (ACI 3)", 2)


@probe("lwpolyline_width_bulge", "LWPOLYLINE", "LWPOLYLINE width, taper, bulge, POLYLINE width")
def _(doc, msp):
    msp.add_lwpolyline([(10, 100), (60, 100)], format="xyseb", dxfattribs={"const_width": 3})
    msp.add_lwpolyline([(10, 90, 0, 3, 0), (60, 90, 3, 0, 0)], format="xyseb")
    msp.add_lwpolyline([(10, 78, 1, 1, 0), (30, 78, 1, 1, 1), (50, 78, 1, 1, -0.5), (60, 70, 0, 0, 0)], format="xyseb")
    msp.add_lwpolyline([(75, 100), (95, 100), (95, 80), (75, 80)], close=True, dxfattribs={"const_width": 1.5})
    msp.add_lwpolyline([(110, 100, 0), (130, 100, 1), (150, 100, 0), (150, 85, -1), (110, 85, 0)], format="xyb")
    msp.add_polyline2d([(10, 55), (40, 55), (60, 45)], dxfattribs={"default_start_width": 2, "default_end_width": 2})
    msp.add_polyline2d([(75, 55), (105, 55), (110, 45)], format="xy", dxfattribs={})
    msp.add_lwpolyline([(120, 55), (150, 55), (150, 40)], dxfattribs={"const_width": 0.0, "lineweight": 100})
    label(msp, 10, 105, "width 3", 2)
    label(msp, 10, 93, "taper 3 to 0", 2)
    label(msp, 10, 66, "per-vertex widths + bulge", 2)
    label(msp, 75, 105, "closed width 1.5", 2)
    label(msp, 110, 105, "bulge 1 / -1 (semicircles)", 2)
    label(msp, 10, 36, "POLYLINE width 2", 2)
    label(msp, 120, 36, "zero width, lineweight 1.0", 2)


@probe("solid_trace", "SOLID", "SOLID triangle, quad, vertex order, colours")
def _(doc, msp):
    msp.add_solid([(10, 100), (30, 100), (20, 85)], dxfattribs={"color": 1})
    msp.add_solid([(40, 100), (60, 100), (40, 85), (60, 85)], dxfattribs={"color": 5})
    msp.add_solid([(70, 100), (90, 100), (90, 85), (70, 85)], dxfattribs={"color": 3})
    msp.add_solid([(100, 100), (120, 100), (100, 85), (120, 85)], dxfattribs={"true_color": colors.rgb2int((255, 128, 0))})
    msp.add_solid([(10, 70), (60, 70), (10, 66), (60, 66)], dxfattribs={"color": 0})
    msp.add_solid([(130, 100), (150, 100), (150, 85), (130, 85)], dxfattribs={"color": 7})
    label(msp, 10, 80, "tri", 2)
    label(msp, 40, 80, "quad 1-2-4-3 order (bowtie if wrong)", 2)
    label(msp, 70, 80, "quad ordered as polygon (bowtie expected)", 2)
    label(msp, 10, 60, "thin bar (rule fills use SOLID)", 2)


@probe("hatch_solid", "HATCH", "HATCH solid: polyline, holes, arc edges, colours, transparency")
def _(doc, msp):
    h = msp.add_hatch(color=1)
    h.paths.add_polyline_path([(10, 80), (50, 80), (50, 110), (10, 110)], is_closed=True)
    h = msp.add_hatch(color=5)
    h.paths.add_polyline_path([(60, 80), (100, 80), (100, 110), (60, 110)], is_closed=True)
    h.paths.add_polyline_path([(70, 88), (90, 88), (90, 102), (70, 102)], is_closed=True, flags=16)
    h = msp.add_hatch(color=3)
    ep = h.paths.add_edge_path()
    ep.add_arc((130, 95), 15, 0, 360)
    h = msp.add_hatch(dxfattribs={"true_color": colors.rgb2int((255, 140, 0))})
    h.paths.add_polyline_path([(10, 40), (50, 40), (30, 70)], is_closed=True)
    h = msp.add_hatch(color=2)
    ep = h.paths.add_edge_path()
    ep.add_line((60, 40), (100, 40))
    ep.add_arc((100, 55), 15, 270, 90)
    ep.add_line((100, 70), (60, 70))
    ep.add_line((60, 70), (60, 40))
    h = msp.add_hatch(color=1)
    h.paths.add_polyline_path([(115, 40), (155, 40), (155, 70), (115, 70)], is_closed=True)
    h.dxf.transparency = colors.float2transparency(0.5)
    msp.add_line((120, 35), (150, 75), dxfattribs={"color": 7})
    label(msp, 10, 74, "polyline", 2)
    label(msp, 60, 74, "island", 2)
    label(msp, 115, 74, "50% transparent", 2)


def _pattern_probe(names: list[str], start: int):
    def build(doc, msp):
        avail = ezpattern.load()
        for i, nm in enumerate(names):
            x, y = 4 + (i % 8) * 24, 96 - (i // 8) * 34
            h = msp.add_hatch(color=7)
            if nm == "SOLID":
                h.set_solid_fill()
            elif nm in avail:
                h.set_pattern_fill(nm, scale=0.25)
            h.paths.add_polyline_path([(x, y), (x + 20, y), (x + 20, y + 20), (x, y + 20)], is_closed=True)
            msp.add_lwpolyline([(x, y), (x + 20, y), (x + 20, y + 20), (x, y + 20)], close=True, dxfattribs={"color": 8})
            label(msp, x, y - 4, nm, 1.8)

    return build


_PAT_A = ["SOLID", "ANSI31", "ANSI32", "ANSI33", "ANSI34", "ANSI35", "ANSI36", "ANSI37", "ANSI38", "ANGLE", "AR-B816",
          "AR-B88", "AR-BRSTD", "AR-CONC", "AR-HBONE", "AR-PARQ1"]
_PAT_B = ["AR-RROOF", "AR-RSHKE", "AR-SAND", "BOX", "BRASS", "BRICK", "BRSTONE", "CLAY", "CORK", "CROSS", "DASH", "DOLMIT",
          "DOTS", "EARTH", "ESCHER", "FLEX", "GRASS", "GRATE", "HEX", "HONEY", "HOUND", "INSUL", "LINE", "NET"]
_PAT_C = ["NET3", "PLAST", "PLASTI", "SACNCR", "SQUARE", "STARS", "STEEL", "SWAMP", "TRANS", "TRIANG", "ZIGZAG",
          "ACAD_ISO02W100", "ACAD_ISO03W100", "ACAD_ISO05W100", "GRAVEL", "GRID"]
for _id, _names in (("hatch_patterns_ansi", _PAT_A), ("hatch_patterns_ar_misc", _PAT_B), ("hatch_patterns_other", _PAT_C)):
    PROBES.append(Probe(_id, "HATCH", f"HATCH patterns: {_names[0]} .. {_names[-1]}", _pattern_probe(_names, 0)))


@probe("hatch_pattern_scale", "HATCH", "pattern hatch scale: does the DXF scale reach LibreCAD's pattern?")
def _(doc, msp):
    names = ["ANSI31", "BRICK", "CROSS", "EARTH", "SQUARE", "AR-CONC", "HEX", "ANGLE"]
    scales = [0.1, 0.25, 1.0, 4.0]
    for r, nm in enumerate(names):
        label(msp, 1, 108 - r * 13.5, nm, 1.8)
        for c, sc in enumerate(scales):
            x, y = 22 + c * 44, 104 - r * 13.5
            h = msp.add_hatch(color=7)
            h.set_pattern_fill(nm, scale=sc)
            h.paths.add_polyline_path([(x, y), (x + 40, y), (x + 40, y + 10), (x, y + 10)], is_closed=True)
            msp.add_lwpolyline([(x, y), (x + 40, y), (x + 40, y + 10), (x, y + 10)], close=True, dxfattribs={"color": 8})
            if r == 0:
                label(msp, x, 113, f"scale {sc:g}", 1.8)


@probe("hatch_gradient", "HATCH", "HATCH gradient fills")
def _(doc, msp):
    for i, (c1, c2, rot, gt) in enumerate([((255, 0, 0), (0, 0, 255), 0, 1), ((255, 255, 0), (0, 128, 0), 90, 1),
                                           ((200, 200, 200), (30, 30, 30), 45, 1), ((255, 0, 0), (255, 255, 255), 0, 2)]):
        x = 10 + i * 46
        h = msp.add_hatch()
        h.set_gradient(c1, c2, rotation=rot)
        h.paths.add_polyline_path([(x, 70), (x + 40, 70), (x + 40, 110), (x, 110)], is_closed=True)
        label(msp, x, 64, f"rot {rot}", 2)
    label(msp, 10, 40, "expect: LibreCAD 2.2 has no gradient; ideal fallback is a flat fill", 2)


@probe("dim_linear", "DIMENSION", "linear DIMENSION horizontal, vertical, rotated, with ezdxf-rendered geometry")
def _(doc, msp):
    solar_dimstyle(doc)
    for p1, p2, base, ang in [((10, 100), (60, 100), (35, 110), 0), ((75, 70), (75, 105), (90, 88), 90), ((110, 70), (150, 90), (130, 98), 30)]:
        msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=ang, dimstyle="SOLAR").render()
    for x, y in [(10, 100), (60, 100), (75, 70), (75, 105), (110, 70), (150, 90)]:
        cross(msp, x, y, 1)
    label(msp, 10, 40, "expect: text 'DIM' value = 50 / 35 / 44.72, arrows at both ends", 2)


@probe("dim_linear_noblock", "DIMENSION", "linear DIMENSION without geometry block (LibreCAD must regenerate)")
def _(doc, msp):
    solar_dimstyle(doc)
    for p1, p2, base, ang in [((10, 100), (60, 100), (35, 110), 0), ((75, 70), (75, 105), (90, 88), 90)]:
        d = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=ang, dimstyle="SOLAR")
        d.dimension.dxf.text_midpoint = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2 + (6 if ang == 0 else 0), 0)
    label(msp, 10, 60, "expect: LibreCAD regenerates dimension from definition points", 2)


@probe("dim_aligned_radial", "DIMENSION", "aligned, radius, diameter and angular DIMENSION")
def _(doc, msp):
    solar_dimstyle(doc)
    msp.add_aligned_dim(p1=(10, 70), p2=(50, 100), distance=6, dimstyle="SOLAR").render()
    msp.add_circle((90, 85), 15)
    msp.add_radius_dim(center=(90, 85), mpoint=(90 + 15 * math.cos(0.8), 85 + 15 * math.sin(0.8)), dimstyle="SOLAR").render()
    msp.add_circle((145, 85), 15)
    msp.add_diameter_dim(center=(145, 85), radius=15, angle=200, dimstyle="SOLAR").render()
    msp.add_line((20, 30), (60, 30))
    msp.add_line((20, 30), (50, 55))
    msp.add_angular_dim_2l(base=(40, 40), line1=((20, 30), (60, 30)), line2=((20, 30), (50, 55)), dimstyle="SOLAR").render()


@probe("dim_overrides", "DIMENSION", "DIMENSION overrides: text, scale, decimals, suffix, tolerance, colours")
def _(doc, msp):
    solar_dimstyle(doc)
    msp.add_linear_dim(base=(35, 108), p1=(10, 100), p2=(60, 100), dimstyle="SOLAR", text="CUSTOM <> mm",
                       override={"dimtxt": 3.5, "dimasz": 3}).render()
    msp.add_linear_dim(base=(115, 108), p1=(80, 100), p2=(150, 100), dimstyle="SOLAR",
                       override={"dimdec": 0, "dimpost": "<> mm", "dimtxt": 2.5}).render()
    msp.add_linear_dim(base=(35, 80), p1=(10, 72), p2=(60, 72), dimstyle="SOLAR",
                       override={"dimlfac": 10.0, "dimdec": 1, "dimclrd": 1, "dimclrt": 5, "dimtxt": 3}).render()
    msp.add_linear_dim(base=(115, 80), p1=(80, 72), p2=(150, 72), dimstyle="SOLAR",
                       override={"dimtol": 1, "dimtp": 0.5, "dimtm": 0.2, "dimtdec": 1, "dimtxt": 2.5}).render()
    msp.add_linear_dim(base=(35, 52), p1=(10, 44), p2=(60, 44), dimstyle="SOLAR",
                       override={"dimscale": 2.0, "dimtxt": 2.5, "dimasz": 2.5}).render()
    msp.add_linear_dim(base=(115, 52), p1=(80, 44), p2=(150, 44), dimstyle="SOLAR",
                       override={"dimtad": 0, "dimtih": 1, "dimtxt": 2.5, "dimdle": 1.5}).render()
    label(msp, 10, 20, "custom text | suffix mm, 0 dec | lfac x10 red/blue | tolerance +0.5/-0.2 | dimscale 2 | text inline", 1.8)


@probe("linetypes_standard", "Linetype", "standard linetypes on LINE, circle, arc, LWPOLYLINE")
def _(doc, msp):
    names = ["CONTINUOUS", "DASHED", "DASHED2", "DASHEDX2", "CENTER", "CENTER2", "DASHDOT", "DASHDOT2", "PHANTOM", "HIDDEN",
             "DOT", "DIVIDE", "BORDER", "ACAD_ISO02W100", "ACAD_ISO04W100"]
    if "HIDDEN" not in doc.linetypes:
        doc.linetypes.add("HIDDEN", pattern=[6.0, 3.0, -3.0], description="Hidden __ __ __")
    for i, n in enumerate(names):
        if n not in doc.linetypes:
            continue
        y = 112 - i * 7
        label(msp, 2, y - 1, n, 2)
        msp.add_line((42, y), (95, y), dxfattribs={"linetype": n})
        msp.add_lwpolyline([(100, y), (140, y), (150, y - 3)], dxfattribs={"linetype": n})
    msp.add_circle((175, 95), 15, dxfattribs={"linetype": "DASHED"})
    msp.add_arc((175, 55), 15, 0, 270, dxfattribs={"linetype": "CENTER"})
    msp.add_circle((175, 20), 10, dxfattribs={"linetype": "DASHDOT"})


@probe("linetypes_custom", "Linetype", "custom linetypes, simple dashes and complex (text) linetype")
def _(doc, msp):
    doc.linetypes.add("SOLAR_DC", pattern=[12.0, 8.0, -2.0, 0.0, -2.0], description="DC ___ . ___")
    doc.linetypes.add("LONGDASH", pattern=[30.0, 20.0, -10.0], description="long dash")
    doc.linetypes.add("DOTS_ONLY", pattern=[4.0, 0.0, -4.0], description="dots")
    for i, n in enumerate(["SOLAR_DC", "LONGDASH", "DOTS_ONLY"]):
        y = 105 - i * 9
        label(msp, 2, y - 1, n, 2)
        msp.add_line((40, y), (190, y), dxfattribs={"linetype": n})
    try:
        doc.linetypes.add("GASLINE", pattern='A,12.7,-2.5,["GAS",STANDARD,S=2.5,U=0.0,X=-2.5,Y=-1.25],-2.5',
                          description="Gas line ----GAS----")
        label(msp, 2, 74, "GASLINE (complex)", 2)
        msp.add_line((40, 75), (190, 75), dxfattribs={"linetype": "GASLINE"})
    except Exception as exc:
        label(msp, 2, 74, f"complex linetype not created: {exc}", 2)
    label(msp, 2, 60, "expect: complex text linetype may print as plain dashes or solid", 2)


@probe("linetype_scale_global", "Linetype", "header $LTSCALE 0.5 and entity linetype scale")
def _(doc, msp):
    doc.header["$LTSCALE"] = 0.5
    for i, s in enumerate([0.5, 1.0, 2.0, 4.0]):
        y = 105 - i * 12
        label(msp, 2, y - 1, f"DASHED entity scale {s}, $LTSCALE 0.5", 2)
        msp.add_line((70, y), (190, y), dxfattribs={"linetype": "DASHED", "ltscale": s})
    msp.add_line((70, 45), (190, 45), dxfattribs={"linetype": "CENTER"})
    label(msp, 2, 44, "CENTER default entity scale", 2)


@probe("linetype_scale_ltscale2", "Linetype", "header $LTSCALE 2.0 with default entity scale")
def _(doc, msp):
    doc.header["$LTSCALE"] = 2.0
    for i, n in enumerate(["DASHED", "CENTER", "DASHDOT", "DOT"]):
        y = 105 - i * 12
        label(msp, 2, y - 1, f"{n}, $LTSCALE 2.0", 2)
        msp.add_line((70, y), (190, y), dxfattribs={"linetype": n})


@probe("lineweights", "Lineweight", "entity and layer lineweights, default weight, hairline")
def _(doc, msp):
    weights = [0, 13, 18, 25, 35, 50, 70, 100, 140, 200]
    for i, w in enumerate(weights):
        y = 112 - i * 6
        label(msp, 2, y - 1, f"lineweight {w / 100:.2f} mm", 2)
        msp.add_line((50, y), (110, y), dxfattribs={"lineweight": w})
    doc.layers.add("W035", lineweight=35)
    doc.layers.add("W100", lineweight=100)
    msp.add_line((125, 112), (190, 112), dxfattribs={"layer": "W035"})
    msp.add_line((125, 106), (190, 106), dxfattribs={"layer": "W100"})
    msp.add_line((125, 100), (190, 100), dxfattribs={"layer": "W100", "lineweight": 25})
    msp.add_line((125, 94), (190, 94))
    msp.add_circle((150, 60), 15, dxfattribs={"lineweight": 70})
    msp.add_lwpolyline([(130, 30), (170, 30), (170, 40)], dxfattribs={"lineweight": 50})
    label(msp, 125, 114, "layer 0.35 | layer 1.0 | entity 0.25 over layer 1.0 | default", 1.6)


@probe("lineweight_unset", "Lineweight", "layer and entity with no lineweight set (default -3)")
def _(doc, msp):
    doc.layers.add("NOWEIGHT", color=7)
    doc.layers.add("W025", color=7, lineweight=25)
    for i, layer in enumerate(["NOWEIGHT", "W025"]):
        y = 100 - i * 40
        msp.add_line((10, y), (80, y), dxfattribs={"layer": layer})
        msp.add_circle((100, y), 10, dxfattribs={"layer": layer})
        msp.add_lwpolyline([(120, y - 8), (150, y - 8), (150, y + 8)], dxfattribs={"layer": layer})
        msp.add_text("TEXT " + layer, height=4, dxfattribs={"layer": layer, "insert": (10, y - 12)})
        msp.add_solid([(160, y - 8), (180, y - 8), (160, y + 8), (180, y + 8)], dxfattribs={"layer": layer})
    label(msp, 10, 5, "expect: top row (no lineweight) prints as invisible hairline slivers", 2)


@probe("colors_aci", "Colour", "ACI colours 1-9, high indices, BYLAYER/BYBLOCK, white on paper")
def _(doc, msp):
    idx = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 30, 50, 90, 130, 170, 210, 250, 251, 252, 253, 254, 255]
    for i, c in enumerate(idx):
        x, y = 4 + (i % 11) * 17.5, 100 - (i // 11) * 30
        msp.add_solid([(x, y), (x + 12, y), (x, y + 10), (x + 12, y + 10)], dxfattribs={"color": c})
        msp.add_line((x, y - 3), (x + 12, y - 3), dxfattribs={"color": c, "lineweight": 50})
        label(msp, x, y - 8, f"{c}", 2)
    doc.layers.add("COLR", color=6, lineweight=25)
    msp.add_line((4, 30), (60, 30), dxfattribs={"layer": "COLR"})
    label(msp, 64, 29, "BYLAYER (layer ACI 6)", 2)
    msp.add_line((4, 22), (60, 22), dxfattribs={"layer": "COLR", "color": 3})
    label(msp, 64, 21, "explicit ACI 3 on layer ACI 6", 2)
    msp.add_text("TEXT ACI 1", height=4, dxfattribs={"color": 1, "insert": (4, 8)})
    msp.add_text("TEXT ACI 5", height=4, dxfattribs={"color": 5, "insert": (50, 8)})
    msp.add_text("TEXT ACI 7", height=4, dxfattribs={"color": 7, "insert": (96, 8)})


@probe("colors_truecolor", "Colour", "true colour on entities and layers")
def _(doc, msp):
    rgbs = [(255, 0, 0), (255, 140, 0), (0, 128, 0), (0, 90, 200), (128, 0, 128), (90, 90, 90)]
    for i, rgb in enumerate(rgbs):
        x = 5 + i * 32
        tc = colors.rgb2int(rgb)
        msp.add_line((x, 105), (x + 25, 105), dxfattribs={"true_color": tc, "lineweight": 70})
        msp.add_solid([(x, 85), (x + 25, 85), (x, 95), (x + 25, 95)], dxfattribs={"true_color": tc})
        h = msp.add_hatch(dxfattribs={"true_color": tc})
        h.paths.add_polyline_path([(x, 60), (x + 25, 60), (x + 25, 78), (x, 78)], is_closed=True)
        msp.add_text(f"{rgb[0]},{rgb[1]},{rgb[2]}", height=2.5, dxfattribs={"true_color": tc, "insert": (x, 50)})
    lay = doc.layers.add("TC_LAYER", lineweight=25)
    lay.rgb = (200, 0, 100)
    msp.add_line((5, 30), (80, 30), dxfattribs={"layer": "TC_LAYER", "lineweight": 50})
    label(msp, 85, 29, "layer true colour 200,0,100", 2)


@probe("layers_visibility", "Layer", "layer off, frozen, locked, no-plot")
def _(doc, msp):
    states = ["on", "off", "frozen", "locked", "noplot"]
    for i, state in enumerate(states):
        name = f"L_{state.upper()}"
        layer = doc.layers.add(name, color=i + 1, lineweight=50)
        if state == "off":
            layer.off()
        elif state == "frozen":
            layer.freeze()
        elif state == "locked":
            layer.lock()
        elif state == "noplot":
            layer.dxf.plot = 0
        y = 105 - i * 15
        msp.add_line((50, y), (120, y), dxfattribs={"layer": name})
        msp.add_text(name + " content", height=3, dxfattribs={"layer": name, "insert": (125, y - 1.5)})
        label(msp, 2, y - 1, f"{name}: {state}", 2)
    label(msp, 2, 25, "expect: OFF and FROZEN hidden; LOCKED shown; NOPLOT hidden in print", 2)


@probe("leader_simple", "LEADER", "LEADER with arrowhead and annotation text")
def _(doc, msp):
    ld = msp.add_leader([(10, 70), (40, 90), (60, 90)], dxfattribs={"dimstyle": "EZDXF"})
    msp.add_text("LEADER TEXT", height=3, dxfattribs={"insert": (62, 88.5)})
    msp.add_leader([(100, 70), (130, 90)], dxfattribs={"dimstyle": "EZDXF"})
    msp.add_leader([(150, 50), (170, 80), (190, 80)], dxfattribs={"dimstyle": "EZDXF", "has_hookline": 1})
    label(msp, 10, 100, "expect: arrow at first vertex, hook line to text", 2)


@probe("mleader", "LEADER", "MULTILEADER (MLEADER) with MTEXT content")
def _(doc, msp):
    ml = msp.add_multileader_mtext("Standard")
    ml.set_content("INVERTER INV-01\\P3.125 MW", char_height=3)
    ml.add_leader_line(ConnectionSide.right, [(40, 90)])
    ml.build(insert=Vec2(70, 100))
    ml2 = msp.add_multileader_mtext("Standard")
    ml2.set_content("SECOND LEADER", char_height=3)
    ml2.add_leader_line(ConnectionSide.left, [(120, 50)])
    ml2.build(insert=Vec2(90, 70))
    label(msp, 10, 20, "expect: LibreCAD 2.2.1 has no MLEADER; entity probably dropped", 2)


@probe("image_raster", "IMAGE", "raster IMAGE entity (PNG)", guarded=False)
def _(doc, msp):
    from PIL import Image

    out = (ASSET_DIR / "probe_image.png").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (200, 120), (255, 255, 255))
    px = img.load()
    for x in range(200):
        for y in range(120):
            px[x, y] = (int(255 * x / 200), 80, int(255 * y / 120))
    img.save(out)
    idef = doc.add_image_def(filename=str(out), size_in_pixel=(200, 120))
    msp.add_image(idef, insert=(10, 20), size_in_units=(60, 36), rotation=0)
    msp.add_image(idef, insert=(100, 30), size_in_units=(60, 36), rotation=20)
    msp.add_lwpolyline([(10, 20), (70, 20), (70, 56), (10, 56)], close=True, dxfattribs={"color": 1})
    label(msp, 10, 100, "IMAGE absolute path in IMAGEDEF; red frame = intended extents", 2)


@probe("curves_ellipse_spline", "Curves", "ELLIPSE (full, arc, rotated), SPLINE (fit, control), CIRCLE, ARC")
def _(doc, msp):
    msp.add_ellipse((30, 90), major_axis=(20, 0), ratio=0.5)
    msp.add_ellipse((80, 90), major_axis=(15, 10), ratio=0.4, start_param=0, end_param=math.pi * 1.2)
    msp.add_ellipse((130, 90), major_axis=(0, 15), ratio=0.6, start_param=1.0, end_param=5.0, dxfattribs={"color": 5})
    msp.add_spline(fit_points=[(10, 50), (30, 70), (50, 45), (70, 65)])
    msp.add_open_spline(control_points=[(85, 45), (95, 70), (110, 45), (125, 70), (140, 45)], degree=3)
    s = msp.add_spline(fit_points=[(150, 60), (170, 70), (185, 55), (165, 40), (150, 60)])
    s.closed = True
    msp.add_circle((30, 20), 10)
    msp.add_arc((70, 20), 10, 30, 250)
    msp.add_arc((110, 20), 10, 300, 60)
    label(msp, 10, 112, "ellipse | elliptical arc | rotated arc | fit spline | control spline | closed spline", 2)


@probe("paperspace_layout", "Layout", "paperspace layout with viewport vs modelspace")
def _(doc, msp):
    msp.add_text("MODELSPACE CONTENT", height=5, dxfattribs={"insert": (10, 80)})
    msp.add_circle((50, 50), 20)
    layout = doc.layout("Layout1")
    layout.add_text("PAPERSPACE TEXT", height=5, dxfattribs={"insert": (10, 100)})
    layout.add_lwpolyline([(5, 5), (190, 5), (190, 115), (5, 115)], close=True)
    layout.add_viewport(center=(100, 60), size=(80, 50), view_center_point=(50, 50), view_height=60)
    label(msp, 10, 5, "expect: LibreCAD prints modelspace only; paperspace text/frame absent", 2)


@probe("table_entity", "TABLE", "ACAD_TABLE entity with anonymous geometry block")
def _(doc, msp):
    blk = doc.blocks.new("*T1")
    for i in range(4):
        blk.add_line((0, -i * 8), (60, -i * 8))
    for j in range(4):
        blk.add_line((j * 20, 0), (j * 20, -24))
    blk.add_text("CELL", height=3, dxfattribs={"insert": (2, -6)})
    handle = doc.entitydb.next_handle()
    from ezdxf.lldxf.tags import Tags
    from ezdxf.lldxf.types import DXFTag

    msp.add_text("Table below drawn via ACAD_TABLE referencing block *T1", height=2.5, dxfattribs={"insert": (10, 105)})
    raw = Tags([
        DXFTag(0, "ACAD_TABLE"), DXFTag(5, handle), DXFTag(330, msp.layout_key), DXFTag(100, "AcDbEntity"), DXFTag(8, "0"),
        DXFTag(100, "AcDbBlockReference"), DXFTag(2, "*T1"), DXFTag(10, 10.0), DXFTag(20, 90.0), DXFTag(30, 0.0),
        DXFTag(100, "AcDbTable"), DXFTag(91, 3), DXFTag(92, 3),
    ])
    from ezdxf.entities import DXFTagStorage

    ent = DXFTagStorage.load(ezdxf.lldxf.extendedtags.ExtendedTags(raw), doc)
    ent.dxf.handle = handle
    ent.dxf.owner = msp.layout_key
    doc.entitydb.add(ent)
    msp.add_entity(ent)
    label(msp, 10, 20, "expect: nothing drawn from the ACAD_TABLE (we draw tables as lines + TEXT)", 2)


@probe("wipeout_mask", "WIPEOUT", "WIPEOUT masking over lines")
def _(doc, msp):
    for i in range(6):
        msp.add_line((10, 100 - i * 6), (100, 100 - i * 6), dxfattribs={"color": 1})
    w = msp.add_wipeout([(30, 85), (70, 85), (70, 75), (30, 75)])
    msp.add_text("MASKED LABEL", height=3, dxfattribs={"insert": (35, 79)})
    for i in range(6):
        msp.add_line((110, 100 - i * 6), (200, 100 - i * 6), dxfattribs={"color": 1})
    msp.add_solid([(125, 85), (155, 85), (125, 75), (155, 75)], dxfattribs={"true_color": colors.rgb2int((255, 255, 255))})
    msp.add_solid([(160, 85), (190, 85), (160, 75), (190, 75)], dxfattribs={"true_color": colors.rgb2int((254, 254, 254))})
    msp.add_text("RGB 255", height=3, dxfattribs={"insert": (128, 79)})
    msp.add_text("RGB 254", height=3, dxfattribs={"insert": (163, 79)})
    label(msp, 10, 50, "left: WIPEOUT; right: SOLID true colour 255,255,255 then 254,254,254, drawn after the lines", 1.8)


@probe("point_markers", "POINT", "POINT entity with PDMODE/PDSIZE")
def _(doc, msp):
    doc.header["$PDMODE"] = 35
    doc.header["$PDSIZE"] = 4
    for i in range(5):
        msp.add_point((20 + i * 30, 90))
    label(msp, 10, 70, "PDMODE 35 (circle + cross), size 4", 2)


@probe("construction_lines", "XLINE", "XLINE / RAY construction geometry")
def _(doc, msp):
    msp.add_xline((100, 60), (1, 1))
    msp.add_ray((20, 20), (1, 0.2))
    msp.add_line((20, 100), (60, 100))
    label(msp, 5, 105, "expect: XLINE/RAY are infinite; LibreCAD should not print them on the sheet", 1.8)


@probe("insunits_metres", "Units", "$INSUNITS = 6 (metres, the ezdxf default) instead of 4 (mm)", guarded=False)
def _(doc, msp):
    doc.units = 6
    msp.add_lwpolyline([(0, 0), (200, 0), (200, 120), (0, 120)], close=True)
    msp.add_text("INSUNITS 6: 200 x 120 sheet", height=5, dxfattribs={"insert": (10, 60)})

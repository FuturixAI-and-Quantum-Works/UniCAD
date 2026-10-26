"""Render every compatibility probe through LibreCAD and ezdxf, and write the matrix.

Outputs (library/compat/): <id>.dxf, <id>.png (LibreCAD above, ezdxf reference below),
matrix.json, and the table between the markers in docs/compat.md. Verdicts are the human
judgements in VERDICTS, recorded after viewing each <id>.png; a probe without one is "unreviewed".

Run: uv run python tools/compat_matrix.py [probe-id ...]
"""

from __future__ import annotations

import io
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pymupdf
from ezdxf.addons.drawing import Frontend, RenderContext, layout
from ezdxf.addons.drawing import pymupdf as ez_pymupdf
from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unicad.render import DwgConverter, LibreCadRenderer, ScaleMode, probes, unavailable_reason  # noqa: E402
from unicad.render.backend import RenderError  # noqa: E402
from unicad.render.preview import pdf_to_png  # noqa: E402

OUT = ROOT / "library" / "compat"
DOC = ROOT / "docs" / "compat.md"
MARK_START, MARK_END = "<!-- matrix:start -->", "<!-- matrix:end -->"
PX_W = 1200

VERDICTS: dict[str, tuple[str, str]] = {
    "text_styles": ("degraded", "Font is chosen by the TEXT STYLE NAME matching an .lff stem (standard, iso, iso3098, unicode, simplex, romans, greekc, ...); the style's font file (arial.ttf, txt.shx) is ignored. Only unicode and iso3098 print Ω √ α Δ; use style `unicode`."),
    "text_alignments": ("works", "All 15 horizontal/vertical alignments, ALIGNED and FIT match the reference. Underscore glyph is wide in the .lff font."),
    "text_geometry": ("degraded", "Height, rotation and width factor work. Oblique angle and the mirror/upside-down generation flags are ignored (text prints upright and unmirrored)."),
    "text_special_codes": ("degraded", "%%d (°), %%p (±) and %%c (Ø) convert. %%u and %%o print literally; %%%% is not collapsed to a single %."),
    "text_unicode_symbols": ("degraded", "° ± Ø ² ³ µ ≤ ≥ ½ · print in the default font. √ Ω ≈ ‰ Δ φ ₹ print as a diamond placeholder unless the style is named `unicode` (then Ω √ α Δ work; ₹ and ≈ ‰ still missing)."),
    "text_devanagari": ("broken", "Devanagari prints as diamond placeholders in every style (no shipped .lff has the glyphs). Keep Hindi out of DXF text, or draw it as pre-converted strokes."),
    "mtext_layout": ("degraded", "\\P line breaks, line spacing and rotation work. The width box does not wrap (a long line runs off the sheet), background fill is ignored. Wrap manually with \\P and draw any mask separately."),
    "mtext_inline_formats": ("broken", "\\H \\C \\L \\O \\F \\W \\Q \\T print literally; {} groups are stripped without applying colour. Use plain MTEXT or TEXT."),
    "mtext_stacking": ("degraded", "Only the ^ form (\\S+0.5^-0.2; and \\S2^;) stacks. \\S1/2; and \\S1#2; print the raw code in small text, so fractions must be written inline."),
    "attrib_attdef": ("degraded", "ATTRIB values are not drawn (hidden or not) and ATTDEF prompts are not drawn. Plain TEXT in the block does print. Matches PLAN section 5: write visible TEXT next to the INSERT."),
    "blocks_nested": ("degraded", "Nested blocks with rotation and uniform scale, and MINSERT arrays, match the reference. A negative scale (mirror) combined with a nested rotated/scaled child detaches the child; text inside mirrored blocks does not mirror. Avoid mirrored INSERTs of nested blocks."),
    "blocks_byblock_inheritance": ("works", "BYBLOCK colour and layer-0 entities inherit the INSERT's layer and colour; explicit colour inside the block stays. The INSERT's layer needs a lineweight (see lineweight_unset)."),
    "lwpolyline_width_bulge": ("degraded", "Bulge arcs work. LWPOLYLINE and POLYLINE widths (constant, tapered, per-vertex) are ignored, giving a plain line at the layer lineweight. Use SOLID for filled shapes and lineweight for thickness."),
    "solid_trace": ("works", "Triangles and 4-point SOLIDs (vertex order 1-2-4-3), ACI, true colour and thin bars fill correctly."),
    "hatch_solid": ("degraded", "Solid hatch with polyline boundaries, islands, arc edges and true colour fill correctly. HATCH transparency is ignored (opaque)."),
    "hatch_patterns_ansi": ("degraded", "At scale 0.25: ANSI31, ANGLE, AR-B816, AR-B88, AR-BRSTD, AR-CONC, AR-HBONE, AR-PARQ1 draw. ANSI32-ANSI38 are absent from LibreCAD's pattern library and draw nothing. Use SOLID or ANSI31."),
    "hatch_patterns_ar_misc": ("degraded", "LibreCAD uses its own pattern files by name. At scale 0.25 AR-RSHKE, BOX, BRICK, BRSTONE, CLAY, CROSS, DOLMIT, EARTH, ESCHER, FLEX, GRASS, HEX, HOUND draw (not always like AutoCAD's tile); AR-RROOF, AR-SAND, BRASS, CORK, DASH, DOTS, GRATE, HONEY, INSUL, LINE, NET draw nothing."),
    "hatch_patterns_other": ("broken", "Only SACNCR and SQUARE draw; NET3, PLAST, PLASTI, STARS, STEEL, SWAMP, TRANS, TRIANG, ZIGZAG, ISO patterns, GRAVEL and GRID draw nothing."),
    "hatch_pattern_scale": ("works", "The DXF pattern scale is honoured for patterns LibreCAD has: tile size follows scale (0.1 to 4). Pattern scale 1 is far too coarse for a 20 mm cell on a mm drawing; ANSI31 at 0.25 gives ~1.6 mm spacing."),
    "hatch_gradient": ("degraded", "Gradient hatch prints as a flat fill in the hatch's ACI colour (black if unset). Set the ACI/true colour you want as the fallback."),
    "dim_linear": ("works", "Horizontal, vertical and rotated linear dimensions are regenerated by LibreCAD from the definition points and the HEADER $DIM* variables ($DIMTXT, $DIMASZ, $DIMEXE, $DIMEXO, $DIMGAP, $DIMDEC...), not from the DIMSTYLE table or the geometry block. ezdxf's default header values (0.18) print text and arrows invisibly small in mm."),
    "dim_linear_noblock": ("works", "A DIMENSION with no geometry block still prints with arrows and text, confirming LibreCAD ignores the block. Text sits at LibreCAD's own default position, not group 11."),
    "dim_aligned_radial": ("degraded", "Aligned and angular dimensions work. Radius and diameter print the bare number (no R or Ø prefix) and the text lands outside the circle."),
    "dim_overrides": ("degraded", "Only an explicit text override (CUSTOM <> mm) is honoured. Per-dimension DIMSTYLE overrides (dimpost, dimlfac, colours, tolerance, dimscale, dimtad/dimtih) are ignored; only the HEADER $DIM* values apply."),
    "linetypes_standard": ("degraded", "LibreCAD substitutes its own pattern by linetype NAME and ignores the DXF pattern. CENTER, CENTER2, DASHED, DASHED2, DASHEDX2, DASHDOT, DASHDOT2, DOT, DIVIDE, HIDDEN draw; PHANTOM prints solid. Dash lengths are LibreCAD's (about 12 mm dash, 6 mm gap for DASHED)."),
    "linetypes_custom": ("broken", "Custom (SOLAR_DC, LONGDASH, DOTS_ONLY) and complex text linetypes print as continuous lines. Only built-in linetype names work."),
    "linetype_scale_global": ("broken", "Header $LTSCALE 0.5 and per-entity linetype scale (0.5 to 4) change nothing: the dash pattern is identical. Pick a different linetype name (DASHED2, DASHED, DASHEDX2) to change dash length."),
    "linetype_scale_ltscale2": ("broken", "Header $LTSCALE 2.0 changes nothing; patterns match the unscaled run."),
    "lineweights": ("works", "Entity and layer lineweights print at their true width (0.13, 0.25, 0.35, 0.50, 0.70, 1.0, 1.4, 2.0 mm measured in the PDF); 0.18 prints as 0.19. A lineweight of 0 prints invisibly. Entity weight overrides layer weight. Needs `-s 1 -c` or ACTUAL mode."),
    "lineweight_unset": ("broken", "A layer or entity with no lineweight (-3 default or BYLAYER to such a layer) prints as a hairline sliver that is invisible at normal zoom (text, lines, circles). Every layer we write must carry an explicit lineweight. SOLID fills are unaffected."),
    "colors_aci": ("works", "ACI 1-9, 10-255 and BYLAYER/explicit colours print as the AutoCAD palette. ACI 7 prints black; ACI 255 prints black rather than white."),
    "colors_truecolor": ("works", "True colour on LINE, SOLID, HATCH, TEXT and layers prints exactly. True colour 255,255,255 prints black, not white."),
    "layers_visibility": ("degraded", "Frozen layers are hidden, locked layers print, plot=0 layers are hidden. Layers switched OFF still print, in black. Use freeze (or plot=0), never off."),
    "leader_simple": ("works", "LEADER draws the arrowhead at the first vertex (sized by header $DIMASZ) and the hook line. The annotation text is not part of the LEADER; add TEXT beside it."),
    "mleader": ("broken", "MULTILEADER is not drawn at all (no leader line, no arrow, no text). Use LEADER plus TEXT/MTEXT."),
    "image_raster": ("broken", "dxf2pdf segfaults (SIGSEGV) on any IMAGE entity, absolute or relative path. The renderer rejects IMAGE before launching LibreCAD."),
    "curves_ellipse_spline": ("degraded", "Circles, arcs, ellipses, elliptical arcs and control-point splines match. Fit-point splines (open and closed) draw a different, flatter curve; write control points or flatten to a polyline."),
    "paperspace_layout": ("broken", "LibreCAD prints paperspace entities (text, frame) on top of modelspace in the same coordinates; viewports are not drawn. Never write paperspace content. This contradicts the drafting/sheets docstring that paperspace is ignored."),
    "table_entity": ("broken", "ACAD_TABLE is not drawn, even with a populated anonymous block. Draw tables as lines + TEXT."),
    "wipeout_mask": ("degraded", "WIPEOUT is ignored. A SOLID in true colour 254,254,254 drawn after the lines masks them (255,255,255 prints black). Draw order is file order."),
    "point_markers": ("works", "POINT with $PDMODE 35 and $PDSIZE 4 prints circle plus cross."),
    "construction_lines": ("broken", "XLINE and RAY print as a stub of about 1 mm instead of an infinite line. Use LINE."),
    "insunits_metres": ("broken", "$INSUNITS 6 (ezdxf's default) makes the PDF page 1000x too big (200000 x 120000 mm). $INSUNITS 1 gives 25.4x, 5 gives 10x; 0 and 4 are right. Always write $INSUNITS = 4."),
}


def reference_png(doc, px_w: int) -> bytes:
    w, h = probes.SHEET_MM
    msp = doc.modelspace()
    white = {"true_color": 0xFFFFFF, "lineweight": 0}
    msp.add_line((0, 0), (0.01, 0.01), dxfattribs=white)
    msp.add_line((w - 0.01, h - 0.01), (w, h), dxfattribs=white)
    backend = ez_pymupdf.PyMuPdfBackend()
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(doc.modelspace())
    page = layout.Page(w, h, layout.Units.mm, margins=layout.Margins.all(0))
    settings = layout.Settings(fit_page=False, scale=1.0, page_alignment=layout.PageAlignment.BOTTOM_LEFT)
    return backend.get_pixmap_bytes(page, fmt="png", settings=settings, dpi=round(px_w / (w / 25.4)))


def pdf_stats(pdf: Path) -> dict:
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        drawings = page.get_drawings()
        widths = Counter(round((d.get("width") or 0) / 72 * 25.4, 2) for d in drawings if "s" in d.get("type", ""))
        strokes = Counter(_hex(d["color"]) for d in drawings if d.get("color") is not None)
        fills = Counter(_hex(d["fill"]) for d in drawings if d.get("fill") is not None)
        dashed = sum(1 for d in drawings if d.get("dashes") and d["dashes"].replace(" ", "") != "[]0")
        return {
            "page_mm": [round(page.rect.width / 72 * 25.4, 1), round(page.rect.height / 72 * 25.4, 1)],
            "paths": len(drawings),
            "images": len(page.get_images()),
            "stroke_widths_mm": {str(k): v for k, v in sorted(widths.items())},
            "stroke_colours": dict(strokes.most_common(12)),
            "fill_colours": dict(fills.most_common(12)),
            "dashed_paths": dashed,
        }


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb[:3])


def caption(img: Image.Image, text: str) -> Image.Image:
    bar = 26
    out = Image.new("RGB", (img.width, img.height + bar), (235, 235, 235))
    ImageDraw.Draw(out).text((8, 6), text, fill=(0, 0, 0), font=ImageFont.load_default(size=15))
    out.paste(img, (0, bar))
    return out


def compose(lc_png: bytes | None, ref_png: bytes, title: str) -> Image.Image:
    ref = caption(Image.open(io.BytesIO(ref_png)).convert("RGB"), f"{title}  |  ezdxf reference")
    if lc_png is None:
        return ref
    lc = caption(Image.open(io.BytesIO(lc_png)).convert("RGB"), f"{title}  |  LibreCAD 2.2.1.6 PDF (rasterised)")
    sheet = Image.new("RGB", (PX_W, lc.height + ref.height + 6), (120, 120, 120))
    sheet.paste(lc, (0, 0))
    sheet.paste(ref, (0, lc.height + 6))
    return sheet


def run_probe(p: probes.Probe, renderer: LibreCadRenderer, tmp: Path) -> dict:
    doc = probes.new_doc()
    p.build(doc, doc.modelspace())
    dxf = OUT / f"{p.id}.dxf"
    doc.saveas(dxf)
    entry: dict = {
        "id": p.id,
        "category": p.category,
        "feature": p.feature,
        "evidence": f"library/compat/{p.id}.png",
        "dxf": f"library/compat/{p.id}.dxf",
    }

    lc_png = None
    pdf = tmp / f"{p.id}.pdf"
    try:
        if p.guarded:
            renderer.to_pdf(dxf, pdf, probes.SHEET_MM, ScaleMode.ACTUAL)
        else:
            w, h = probes.SHEET_MM
            LibreCadRenderer(strict_units=False)._run(
                ["dxf2pdf", "-s", "1", "-p", f"{w:g}x{h:g}", "-o", str(pdf), str(dxf)], pdf
            )
        entry["librecad"] = {"rendered": True, **pdf_stats(pdf)}
        lc_png = pdf_to_png(pdf, (0, 0, *probes.SHEET_MM), px=PX_W)
    except RenderError as exc:
        entry["librecad"] = {"rendered": False, "error": str(exc)}

    try:
        ref = reference_png(doc, PX_W)
    except Exception as exc:
        failed = Image.new("RGB", (PX_W, PX_W * 12 // 20), (255, 255, 255))
        ImageDraw.Draw(failed).text((20, 20), f"ezdxf reference failed: {type(exc).__name__}: {exc}", fill=(200, 0, 0))
        buf = io.BytesIO()
        failed.save(buf, format="PNG")
        ref = buf.getvalue()
        entry["ezdxf_reference_error"] = f"{type(exc).__name__}: {exc}"
    compose(lc_png, ref, p.id).save(OUT / f"{p.id}.png", optimize=True)
    entry["dwg"] = dwg_roundtrip(dxf, tmp / f"{p.id}.dwg")
    entry["verdict"], entry["note"] = VERDICTS.get(p.id, ("unreviewed", ""))
    return entry


def dwg_roundtrip(dxf: Path, dwg: Path) -> dict:
    reason = unavailable_reason()
    if reason:
        return {"status": "unavailable", "detail": reason}
    try:
        result = DwgConverter().to_dwg(dxf, dwg, "R2000", strict=False)
    except RenderError as exc:
        return {"status": "failed", "detail": str(exc)}
    if result.verified:
        return {"status": "ok", "converter": result.converter, "entities": sum(result.source_counts.values())}
    return {
        "status": "lossy",
        "converter": result.converter,
        "mismatches": {k: {"dxf": a, "readback": b} for k, (a, b) in result.mismatches.items()},
    }


def render_table(entries: list[dict]) -> str:
    rows = [
        "| Feature | Verdict | LibreCAD PDF behaviour | DWG round-trip (R2000) | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in entries:
        dwg = e["dwg"]
        cell = dwg["status"]
        if dwg["status"] == "lossy":
            cell += ": " + ", ".join(f"{k} {v['dxf']}→{v['readback']}" for k, v in dwg["mismatches"].items())
        elif dwg["status"] == "failed":
            reason = re.search(r"ERROR: ([^|]+)", dwg["detail"])
            cell += f": {reason.group(1).strip() if reason else dwg['detail'][:70]}"
        rows.append(
            f"| {e['category']}: {e['feature']} | **{e['verdict']}** | {e['note']} | {cell} | `{e['evidence']}` |"
        )
    return "\n".join(rows)


def write_docs(entries: list[dict]) -> None:
    text = DOC.read_text() if DOC.exists() else f"# LibreCAD compatibility\n\n{MARK_START}\n{MARK_END}\n"
    head, _, rest = text.partition(MARK_START)
    _, _, tail = rest.partition(MARK_END)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(f"{head}{MARK_START}\n{render_table(entries)}\n{MARK_END}{tail}")


def main(argv: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    probes.ASSET_DIR = OUT
    renderer = LibreCadRenderer()
    matrix = OUT / "matrix.json"
    previous = {e["id"]: e for e in json.loads(matrix.read_text())["probes"]} if matrix.exists() else {}
    entries: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="compat-") as tmp:
        for p in probes.PROBES:
            if not argv or p.id in argv:
                print(f"probe {p.id}", flush=True)
                entries.append(run_probe(p, renderer, Path(tmp)))
            elif p.id in previous:
                prev = previous[p.id]
                prev["verdict"], prev["note"] = VERDICTS.get(p.id, ("unreviewed", ""))
                entries.append(prev)
    payload = {
        "librecad": "2.2.1.6",
        "dwg_converter": next((e["dwg"]["converter"] for e in entries if e["dwg"].get("converter")), None),
        "probes": entries,
    }
    matrix.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    write_docs(entries)
    print(dict(Counter(e["verdict"] for e in entries)))


if __name__ == "__main__":
    main(sys.argv[1:])

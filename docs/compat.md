# LibreCAD compatibility

What LibreCAD 2.2.1.6 (`dxf2pdf`, AppImage extracted to `/tmp/opencode/proto/squashfs-root`) prints for each DXF feature we might write, and what the DWG converter does with it. Every row is a probe drawing in `src/unicad/render/probes.py`, rendered by `tools/compat_matrix.py` into `library/compat/`:

- `<id>.dxf`: the probe.
- `<id>.png`: LibreCAD's PDF rasterised (top) above ezdxf's own drawing add-on (bottom). The two are registered to the same 200 x 120 mm sheet. The ezdxf half is a sanity reference for what the DXF says, not for fonts: ezdxf draws every font as a system sans.
- `matrix.json`: the machine-readable version (`verdict`, `note`, LibreCAD PDF measurements, DWG round-trip result). `tests/test_compat.py` fails if a probe has no reviewed verdict or evidence.

Regenerate with `uv run python tools/compat_matrix.py [probe-id ...]` (about 2.5 minutes for all 43). Verdicts are human judgements recorded in `VERDICTS` in that file after viewing each PNG; a new probe stays `unreviewed` until someone looks.

Verdict meanings: **works** means the printed result matches the DXF intent; **degraded** means it prints but loses something named in the note; **broken** means wrong output, no output, or a crash.

## Rules for the DXF writer

Each one is backed by a row in the table below.

| Rule | Why |
| --- | --- |
| Write `$INSUNITS = 4`. | Any other unit rescales the PDF page (6 = metres, ezdxf's default, gives 1000x; 1 = inch gives 25.4x). 0 and 4 are right. |
| Give every layer an explicit lineweight (0.13 mm minimum). Never rely on the default. | A layer or entity with no lineweight prints as a hairline sliver, and a lineweight of 0 prints invisibly. The ezdxf `Layers.add(..., dxfattribs={"lineweight": n})` form silently drops the value; pass `lineweight=n`. |
| Set `$DIMTXT`, `$DIMASZ`, `$DIMEXE`, `$DIMEXO`, `$DIMGAP`, `$DIMDEC` in the HEADER. | LibreCAD regenerates dimensions from the definition points and the header variables, ignoring the DIMSTYLE table, per-dimension overrides and the geometry block. ezdxf's header defaults print text and arrows at 0.18 mm. |
| Pick the font by TEXT STYLE NAME: `unicode` (or `iso3098`) for engineering symbols. | LibreCAD matches the style name to an `.lff` file and ignores the style's font file. Only `unicode` and `iso3098` carry Ω √ α Δ. No font has Devanagari or ₹. |
| Use plain TEXT and `\P` line breaks. No MTEXT inline codes, no MTEXT width wrap, no `%%u` / `%%o`. | Inline codes print literally, the width box does not wrap, and underline/overline codes are not parsed. `%%d` `%%p` `%%c` work. |
| Draw filled shapes with SOLID or HATCH. Never LWPOLYLINE width. | LWPOLYLINE and POLYLINE widths are ignored. Hatch gradient prints flat and hatch transparency prints opaque. |
| Hatch patterns: SOLID and ANSI31 only (scale 0.25 for a mm drawing). | ANSI32-ANSI38 and many AutoCAD patterns are not in LibreCAD's library and draw nothing; the others use LibreCAD's own tiles. |
| Use only built-in linetype names (CENTER, DASHED, DASHDOT, DOT, DIVIDE, HIDDEN and their 2 / X2 variants). Do not use PHANTOM, custom or complex linetypes. Do not rely on `$LTSCALE` or entity linetype scale. | LibreCAD substitutes its own pattern by name at a fixed size; custom names print continuous; scale is ignored. |
| Hide layers by freezing or `plot = 0`, never by switching them off. | Off layers still print, in black. |
| Write modelspace only, as paper millimetres with the origin at the sheet's lower-left corner. | LibreCAD prints paperspace entities on top of modelspace and draws no viewports. |
| No IMAGE, MULTILEADER, ACAD_TABLE, WIPEOUT, XLINE or RAY. | IMAGE segfaults `dxf2pdf`; MULTILEADER, ACAD_TABLE, WIPEOUT are dropped; XLINE and RAY print as 1 mm stubs. Use LEADER + TEXT, lines + TEXT, and a 254,254,254 SOLID as a label mask. |
| Visible TEXT next to every INSERT; ATTRIB only for machine reading. | ATTRIB values and ATTDEF prompts are never drawn. |
| Do not mirror INSERTs of nested blocks (negative scale). | The nested child detaches from its parent. |
| Write fit-point splines as control-point splines or polylines. | Fit-point splines print a different curve. |
| Radius and diameter dimensions: add the `R` / `Ø` through the text override. | LibreCAD prints the bare number. |

Corrections to `PLAN.md` section 5 found by these probes:

- "`$INSUNITS` unset makes the page 1000x": an unset value (0) prints correctly. The 1000x comes from a non-mm unit, in particular ezdxf's default of metres.
- `drafting/sheets/render.py` says LibreCAD prints only modelspace and ignores paperspace. It prints both, overlaid.
- "LWPOLYLINE width is ignored": confirmed (`lwpolyline_width_bulge`).

## Renderer behaviour (`src/unicad/render`)

- `ScaleMode.ACTUAL` runs `dxf2pdf -s 1 -p WxH`. Drawing mm (x, y) lands x mm right of and y mm above the page's lower-left corner, with no centring, so crops by drawing coordinates are exact (tested). `CENTERED` adds `-c` (centres on the entity extents, so the registration moves). `FIT` uses `-a`.
- Page sizes come back as whole points; A0 to A4 are within 1 pt of the requested paper.
- Guards run before LibreCAD starts, because LibreCAD exits 0 for a missing file, hangs on a malformed DXF, and segfaults on IMAGE: unreadable DXF, `$INSUNITS != 4` and IMAGE entities raise `InvalidDrawing`. A hung run is killed (whole process group) at the timeout. A run that produces no file, a stale file, or a page of the wrong size raises `RenderFailed`.
- With `DISPLAY` unset the renderer wraps LibreCAD in `xvfb-run -a` when it is installed, otherwise raises `BackendUnavailable`.
- `dxf2svg` works but only fits the drawing extents (a 410 x 287 mm viewBox for the A3 sample) and the output has no `<text>` elements; PDF rasterising is the preview path.

## DWG

Status: **available through LibreDWG 0.13.4 `dxf2dwg`**, built from the GNU release tarball into `/tmp/opencode/libredwg/install` (set `LIBREDWG_BIN` to its `bin`). ODA File Converter was not obtainable.

- ODA File Converter: `opendesign.com` does not answer from this machine (TCP connect times out) and the download URL is a signed S3 link issued by `https://www.opendesign.com/guestfiles/get?filename=...`; the direct bucket URL returns 403. The ODA FAQ describes File Converter as a free download. I could not read its licence, so whether it may be bundled in a commercial product is **unverified**. `DwgConverter` still prefers it when `ezdxf.addons.odafc.is_installed()` is true (configure the path in ezdxf's `odafc-addon` settings); that code path is not exercised here.
- LibreDWG: GPL, marked experimental by its authors. Build needs `-Wno-error -std=gnu17` with the current GCC. `dxf2dwg` writes R12, R14 and R2000 only (the `--as` list shows newer versions as "planned"), so `to_dwg` raises `DwgUnavailable` for anything newer. Output opens in LibreDWG's own reader; it was not opened in AutoCAD, so treat it as unvalidated outside this round trip.
- Validation: `to_dwg` converts the DWG back with `dwg2dxf` and compares entity counts by type over modelspace, paperspace and named blocks. A mismatch raises `DwgMismatch` unless `strict=False`. The sample sheets round-trip exactly.
- Probe results (R2000): 36 round-trip exactly, 3 differ, 4 fail. Differ: MULTILEADER and its TEXT are dropped (LibreCAD does not draw MULTILEADER either); ATTRIB (4 to 8) and POLYLINE VERTEX (6 to 12) come back twice in the `dwg2dxf` output, a LibreDWG quirk that looks like duplicated sub-entities rather than lost data (not confirmed in a CAD viewer). Fail: MTEXT with a rotation (`Invalid DXF code 50 for MTEXT`), which also takes out every ezdxf-rendered dimension because its geometry block holds MTEXT; and `dwg2dxf` hanging on the ACAD_TABLE probe (killed at 120 s). LibreCAD 2.2.1.6 `dxf2pdf` cannot read DWG at all, so DWG cannot be previewed through it.
- Implication: until ODA is available, DWG output should come from a DXF that avoids rotated MTEXT and MULTILEADER. Write DIMENSION entities without the ezdxf-rendered geometry block (`dim_linear_noblock`): LibreCAD ignores the block anyway and that variant round-trips. The matrix column shows each feature.

## Matrix

<!-- matrix:start -->
| Feature | Verdict | LibreCAD PDF behaviour | DWG round-trip (R2000) | Evidence |
| --- | --- | --- | --- | --- |
| TEXT: TEXT style to font mapping and glyph coverage per LibreCAD .lff font | **degraded** | Font is chosen by the TEXT STYLE NAME matching an .lff stem (standard, iso, iso3098, unicode, simplex, romans, greekc, ...); the style's font file (arial.ttf, txt.shx) is ignored. Only unicode and iso3098 print Ω √ α Δ; use style `unicode`. | ok | `library/compat/text_styles.png` |
| TEXT: TEXT horizontal/vertical alignments | **works** | All 15 horizontal/vertical alignments, ALIGNED and FIT match the reference. Underscore glyph is wide in the .lff font. | ok | `library/compat/text_alignments.png` |
| TEXT: TEXT height, rotation, width factor, oblique, mirror | **degraded** | Height, rotation and width factor work. Oblique angle and the mirror/upside-down generation flags are ignored (text prints upright and unmirrored). | ok | `library/compat/text_geometry.png` |
| TEXT: TEXT %% codes: %%d %%p %%c %%u %%o | **degraded** | %%d (°), %%p (±) and %%c (Ø) convert. %%u and %%o print literally; %%%% is not collapsed to a single %. | ok | `library/compat/text_special_codes.png` |
| Unicode: Unicode symbols in TEXT: degree, plus-minus, sqrt, diameter, superscripts | **degraded** | ° ± Ø ² ³ µ ≤ ≥ ½ · print in the default font. √ Ω ≈ ‰ Δ φ ₹ print as a diamond placeholder unless the style is named `unicode` (then Ω √ α Δ work; ₹ and ≈ ‰ still missing). | ok | `library/compat/text_unicode_symbols.png` |
| Unicode: Devanagari (Hindi) text in TEXT and MTEXT | **broken** | Devanagari prints as diamond placeholders in every style (no shipped .lff has the glyphs). Keep Hindi out of DXF text, or draw it as pre-converted strokes. | ok | `library/compat/text_devanagari.png` |
| MTEXT: MTEXT wrapping, line breaks, attachment points, line spacing | **degraded** | \P line breaks, line spacing and rotation work. The width box does not wrap (a long line runs off the sheet), background fill is ignored. Wrap manually with \P and draw any mask separately. | failed: Invalid DXF code 50 for MTEXT | `library/compat/mtext_layout.png` |
| MTEXT: MTEXT inline codes: height, colour, font, underline, overline, bold | **broken** | \H \C \L \O \F \W \Q \T print literally; {} groups are stripped without applying colour. Use plain MTEXT or TEXT. | ok | `library/compat/mtext_inline_formats.png` |
| MTEXT: MTEXT stacked fractions and tolerances | **degraded** | Only the ^ form (\S+0.5^-0.2; and \S2^;) stacks. \S1/2; and \S1#2; print the raw code in small text, so fractions must be written inline. | ok | `library/compat/mtext_stacking.png` |
| ATTRIB: ATTDEF / ATTRIB in INSERT (visible and invisible) | **degraded** | ATTRIB values are not drawn (hidden or not) and ATTDEF prompts are not drawn. Plain TEXT in the block does print. Matches PLAN section 5: write visible TEXT next to the INSERT. | lossy: ATTRIB 4→8 | `library/compat/attrib_attdef.png` |
| BLOCK: nested blocks with scale, rotation, mirror and MINSERT | **degraded** | Nested blocks with rotation and uniform scale, and MINSERT arrays, match the reference. A negative scale (mirror) combined with a nested rotated/scaled child detaches the child; text inside mirrored blocks does not mirror. Avoid mirrored INSERTs of nested blocks. | ok | `library/compat/blocks_nested.png` |
| BLOCK: BYBLOCK colour / layer 0 inheritance inside blocks | **works** | BYBLOCK colour and layer-0 entities inherit the INSERT's layer and colour; explicit colour inside the block stays. The INSERT's layer needs a lineweight (see lineweight_unset). | ok | `library/compat/blocks_byblock_inheritance.png` |
| LWPOLYLINE: LWPOLYLINE width, taper, bulge, POLYLINE width | **degraded** | Bulge arcs work. LWPOLYLINE and POLYLINE widths (constant, tapered, per-vertex) are ignored, giving a plain line at the layer lineweight. Use SOLID for filled shapes and lineweight for thickness. | lossy: VERTEX 6→12 | `library/compat/lwpolyline_width_bulge.png` |
| SOLID: SOLID triangle, quad, vertex order, colours | **works** | Triangles and 4-point SOLIDs (vertex order 1-2-4-3), ACI, true colour and thin bars fill correctly. | ok | `library/compat/solid_trace.png` |
| HATCH: HATCH solid: polyline, holes, arc edges, colours, transparency | **degraded** | Solid hatch with polyline boundaries, islands, arc edges and true colour fill correctly. HATCH transparency is ignored (opaque). | ok | `library/compat/hatch_solid.png` |
| HATCH: HATCH patterns: SOLID .. AR-PARQ1 | **degraded** | At scale 0.25: ANSI31, ANGLE, AR-B816, AR-B88, AR-BRSTD, AR-CONC, AR-HBONE, AR-PARQ1 draw. ANSI32-ANSI38 are absent from LibreCAD's pattern library and draw nothing. Use SOLID or ANSI31. | ok | `library/compat/hatch_patterns_ansi.png` |
| HATCH: HATCH patterns: AR-RROOF .. NET | **degraded** | LibreCAD uses its own pattern files by name. At scale 0.25 AR-RSHKE, BOX, BRICK, BRSTONE, CLAY, CROSS, DOLMIT, EARTH, ESCHER, FLEX, GRASS, HEX, HOUND draw (not always like AutoCAD's tile); AR-RROOF, AR-SAND, BRASS, CORK, DASH, DOTS, GRATE, HONEY, INSUL, LINE, NET draw nothing. | ok | `library/compat/hatch_patterns_ar_misc.png` |
| HATCH: HATCH patterns: NET3 .. GRID | **broken** | Only SACNCR and SQUARE draw; NET3, PLAST, PLASTI, STARS, STEEL, SWAMP, TRANS, TRIANG, ZIGZAG, ISO patterns, GRAVEL and GRID draw nothing. | ok | `library/compat/hatch_patterns_other.png` |
| HATCH: pattern hatch scale: does the DXF scale reach LibreCAD's pattern? | **works** | The DXF pattern scale is honoured for patterns LibreCAD has: tile size follows scale (0.1 to 4). Pattern scale 1 is far too coarse for a 20 mm cell on a mm drawing; ANSI31 at 0.25 gives ~1.6 mm spacing. | ok | `library/compat/hatch_pattern_scale.png` |
| HATCH: HATCH gradient fills | **degraded** | Gradient hatch prints as a flat fill in the hatch's ACI colour (black if unset). Set the ACI/true colour you want as the fallback. | ok | `library/compat/hatch_gradient.png` |
| DIMENSION: linear DIMENSION horizontal, vertical, rotated, with ezdxf-rendered geometry | **works** | Horizontal, vertical and rotated linear dimensions are regenerated by LibreCAD from the definition points and the HEADER $DIM* variables ($DIMTXT, $DIMASZ, $DIMEXE, $DIMEXO, $DIMGAP, $DIMDEC...), not from the DIMSTYLE table or the geometry block. ezdxf's default header values (0.18) print text and arrows invisibly small in mm. | failed: Invalid DXF code 50 for MTEXT | `library/compat/dim_linear.png` |
| DIMENSION: linear DIMENSION without geometry block (LibreCAD must regenerate) | **works** | A DIMENSION with no geometry block still prints with arrows and text, confirming LibreCAD ignores the block. Text sits at LibreCAD's own default position, not group 11. | ok | `library/compat/dim_linear_noblock.png` |
| DIMENSION: aligned, radius, diameter and angular DIMENSION | **degraded** | Aligned and angular dimensions work. Radius and diameter print the bare number (no R or Ø prefix) and the text lands outside the circle. | failed: Invalid DXF code 50 for MTEXT | `library/compat/dim_aligned_radial.png` |
| DIMENSION: DIMENSION overrides: text, scale, decimals, suffix, tolerance, colours | **degraded** | Only an explicit text override (CUSTOM <> mm) is honoured. Per-dimension DIMSTYLE overrides (dimpost, dimlfac, colours, tolerance, dimscale, dimtad/dimtih) are ignored; only the HEADER $DIM* values apply. | ok | `library/compat/dim_overrides.png` |
| Linetype: standard linetypes on LINE, circle, arc, LWPOLYLINE | **degraded** | LibreCAD substitutes its own pattern by linetype NAME and ignores the DXF pattern. CENTER, CENTER2, DASHED, DASHED2, DASHEDX2, DASHDOT, DASHDOT2, DOT, DIVIDE, HIDDEN draw; PHANTOM prints solid. Dash lengths are LibreCAD's (about 12 mm dash, 6 mm gap for DASHED). | ok | `library/compat/linetypes_standard.png` |
| Linetype: custom linetypes, simple dashes and complex (text) linetype | **broken** | Custom (SOLAR_DC, LONGDASH, DOTS_ONLY) and complex text linetypes print as continuous lines. Only built-in linetype names work. | ok | `library/compat/linetypes_custom.png` |
| Linetype: header $LTSCALE 0.5 and entity linetype scale | **broken** | Header $LTSCALE 0.5 and per-entity linetype scale (0.5 to 4) change nothing: the dash pattern is identical. Pick a different linetype name (DASHED2, DASHED, DASHEDX2) to change dash length. | ok | `library/compat/linetype_scale_global.png` |
| Linetype: header $LTSCALE 2.0 with default entity scale | **broken** | Header $LTSCALE 2.0 changes nothing; patterns match the unscaled run. | ok | `library/compat/linetype_scale_ltscale2.png` |
| Lineweight: entity and layer lineweights, default weight, hairline | **works** | Entity and layer lineweights print at their true width (0.13, 0.25, 0.35, 0.50, 0.70, 1.0, 1.4, 2.0 mm measured in the PDF); 0.18 prints as 0.19. A lineweight of 0 prints invisibly. Entity weight overrides layer weight. Needs `-s 1 -c` or ACTUAL mode. | ok | `library/compat/lineweights.png` |
| Lineweight: layer and entity with no lineweight set (default -3) | **broken** | A layer or entity with no lineweight (-3 default or BYLAYER to such a layer) prints as a hairline sliver that is invisible at normal zoom (text, lines, circles). Every layer we write must carry an explicit lineweight. SOLID fills are unaffected. | ok | `library/compat/lineweight_unset.png` |
| Colour: ACI colours 1-9, high indices, BYLAYER/BYBLOCK, white on paper | **works** | ACI 1-9, 10-255 and BYLAYER/explicit colours print as the AutoCAD palette. ACI 7 prints black; ACI 255 prints black rather than white. | ok | `library/compat/colors_aci.png` |
| Colour: true colour on entities and layers | **works** | True colour on LINE, SOLID, HATCH, TEXT and layers prints exactly. True colour 255,255,255 prints black, not white. | ok | `library/compat/colors_truecolor.png` |
| Layer: layer off, frozen, locked, no-plot | **degraded** | Frozen layers are hidden, locked layers print, plot=0 layers are hidden. Layers switched OFF still print, in black. Use freeze (or plot=0), never off. | ok | `library/compat/layers_visibility.png` |
| LEADER: LEADER with arrowhead and annotation text | **works** | LEADER draws the arrowhead at the first vertex (sized by header $DIMASZ) and the hook line. The annotation text is not part of the LEADER; add TEXT beside it. | ok | `library/compat/leader_simple.png` |
| LEADER: MULTILEADER (MLEADER) with MTEXT content | **broken** | MULTILEADER is not drawn at all (no leader line, no arrow, no text). Use LEADER plus TEXT/MTEXT. | lossy: MULTILEADER 2→0, TEXT 1→0 | `library/compat/mleader.png` |
| IMAGE: raster IMAGE entity (PNG) | **broken** | dxf2pdf segfaults (SIGSEGV) on any IMAGE entity, absolute or relative path. The renderer rejects IMAGE before launching LibreCAD. | ok | `library/compat/image_raster.png` |
| Curves: ELLIPSE (full, arc, rotated), SPLINE (fit, control), CIRCLE, ARC | **degraded** | Circles, arcs, ellipses, elliptical arcs and control-point splines match. Fit-point splines (open and closed) draw a different, flatter curve; write control points or flatten to a polyline. | ok | `library/compat/curves_ellipse_spline.png` |
| Layout: paperspace layout with viewport vs modelspace | **broken** | LibreCAD prints paperspace entities (text, frame) on top of modelspace in the same coordinates; viewports are not drawn. Never write paperspace content. This contradicts the drafting/sheets docstring that paperspace is ignored. | ok | `library/compat/paperspace_layout.png` |
| TABLE: ACAD_TABLE entity with anonymous geometry block | **broken** | ACAD_TABLE is not drawn, even with a populated anonymous block. Draw tables as lines + TEXT. | failed: dwg2dxf did not finish within 120 s | `library/compat/table_entity.png` |
| WIPEOUT: WIPEOUT masking over lines | **degraded** | WIPEOUT is ignored. A SOLID in true colour 254,254,254 drawn after the lines masks them (255,255,255 prints black). Draw order is file order. | ok | `library/compat/wipeout_mask.png` |
| POINT: POINT entity with PDMODE/PDSIZE | **works** | POINT with $PDMODE 35 and $PDSIZE 4 prints circle plus cross. | ok | `library/compat/point_markers.png` |
| XLINE: XLINE / RAY construction geometry | **broken** | XLINE and RAY print as a stub of about 1 mm instead of an infinite line. Use LINE. | ok | `library/compat/construction_lines.png` |
| Units: $INSUNITS = 6 (metres, the ezdxf default) instead of 4 (mm) | **broken** | $INSUNITS 6 (ezdxf's default) makes the PDF page 1000x too big (200000 x 120000 mm). $INSUNITS 1 gives 25.4x, 5 gives 10x; 0 and 4 are right. Always write $INSUNITS = 4. | ok | `library/compat/insunits_metres.png` |
<!-- matrix:end -->

import math
from collections.abc import Iterable, Mapping

from ezdxf.document import Drawing
from ezdxf.entities import Insert
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import BaseLayout

from .library.kit import LAYER, TEXT_STYLE
from .model import SymbolDef
from .registry import REGISTRY

_ATTRIB_INVISIBLE = 1
_SYMBOL_LINEWEIGHT = 25
TEXT_LAYER = "G-SYMBOL-TXT"
UNICODE_STYLE = "unicode"


def text_style_for(value: str) -> str:
    """LibreCAD prints √ Ω Δ only in a style named `unicode`; other text keeps the symbol style."""
    return UNICODE_STYLE if any(ord(c) > 127 for c in value) else TEXT_STYLE


def _symbol(key: str) -> SymbolDef:
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown symbol {key!r}") from None


def _ensure_resources(doc: Drawing) -> None:
    if LAYER not in doc.layers:
        doc.layers.add(LAYER, color=7, lineweight=_SYMBOL_LINEWEIGHT)
    if TEXT_LAYER not in doc.layers:
        doc.layers.add(TEXT_LAYER, color=7, lineweight=_SYMBOL_LINEWEIGHT)
    if TEXT_STYLE not in doc.styles:
        doc.styles.add(TEXT_STYLE, font="simplex.shx")


def install(doc: Drawing, keys: Iterable[str] | None = None) -> None:
    """Create the block definitions for `keys` (default: all) unless the doc already has them.

    ATTDEF anchors are the left-middle point of the text, or its middle for centred attributes.
    """
    _ensure_resources(doc)
    for key in REGISTRY if keys is None else keys:
        sym = _symbol(key)
        if sym.block_name in doc.blocks:
            continue
        block = doc.blocks.new(sym.block_name, base_point=(0, 0))
        sym.draw(block)
        for a in sym.attrs:
            attdef = block.add_attdef(
                a.tag,
                (a.x, a.y),
                a.default,
                height=a.height,
                dxfattribs={"layer": LAYER, "style": TEXT_STYLE, "prompt": a.tag},
            )
            attdef.set_placement(
                (a.x, a.y),
                align=TextEntityAlignment.MIDDLE_CENTER if a.centred else TextEntityAlignment.MIDDLE_LEFT,
            )
            if not a.visible:
                attdef.dxf.flags |= _ATTRIB_INVISIBLE


TURNED_SUFFIX = "_T180"
_H_FLIP = {"LEFT": "RIGHT", "RIGHT": "LEFT", "CENTER": "CENTER", "MIDDLE": "MIDDLE", "ALIGNED": "ALIGNED", "FIT": "FIT"}
_V_FLIP = {"TOP": "BOTTOM", "BOTTOM": "TOP", "MIDDLE": "MIDDLE", "": "TOP"}


def reads_upside_down(rotation: float) -> bool:
    """Text turned by `rotation` degrees reads upside down (or top to bottom) unless it is turned back."""
    r = round(rotation, 6) % 360
    return 90 < r <= 270


def _turned_alignment(align: TextEntityAlignment) -> TextEntityAlignment:
    """The alignment that keeps a text in the same box once it is turned 180 degrees about its alignment point."""
    v, _, h = align.name.rpartition("_")
    if v == "" and h in ("ALIGNED", "FIT", "MIDDLE"):
        return align
    v = _V_FLIP[v]
    return TextEntityAlignment[f"{v}_{_H_FLIP[h]}" if v else _H_FLIP[h]]


def block_for(doc: Drawing, key: str, rotation: float) -> str:
    """The block to insert symbol `key` at `rotation`: its own block, or a copy whose texts are turned so they read upright on the sheet.

    The copy is made from the block as it is in `doc` (a company override included) the first time it is needed."""
    sym = _symbol(key)
    install(doc, [key])
    block = doc.blocks.get(sym.block_name)
    if not reads_upside_down(rotation) or not any(e.dxftype() == "TEXT" for e in block):
        return sym.block_name
    name = sym.block_name + TURNED_SUFFIX
    if name not in doc.blocks:
        doc.blocks.new(name, base_point=(0, 0))
        refresh_turned(doc, key)
    return name


def refresh_turned(doc: Drawing, key: str) -> None:
    """Redraw the turned copy of `key`'s block (if the doc has one) from the block as it is now."""
    sym = _symbol(key)
    if sym.block_name + TURNED_SUFFIX not in doc.blocks:
        return
    turned = doc.blocks.get(sym.block_name + TURNED_SUFFIX)
    for e in list(turned):
        turned.delete_entity(e)
    for e in doc.blocks.get(sym.block_name):
        c = e.copy()
        if c.dxftype() == "TEXT":
            align, p1, p2 = c.get_placement()
            c.dxf.rotation = (c.dxf.get("rotation", 0.0) + 180.0) % 360
            c.set_placement(p1, p2, align=_turned_alignment(align))
        turned.add_entity(c)


def _rotate(x: float, y: float, rotation: float) -> tuple[float, float]:
    r = math.radians(rotation)
    c, s = math.cos(r), math.sin(r)
    return x * c - y * s, x * s + y * c


def _clean(v: float) -> float:
    return round(v, 6) + 0.0


def upright_attr(sym: SymbolDef, x: float, y: float) -> tuple[float, float]:
    """Where attribute (x, y) goes, relative to the insertion point, on a symbol turned 180 degrees
    with its labels kept upright: the same offset from the turned body's lower-left corner."""
    return _clean(x - sym.width), _clean(y - sym.height)


def wrap_lines(text: str, width: int) -> tuple[str, ...]:
    """`text` broken after its commas into lines of at most `width` characters where it can be; one line when `width` is 0."""
    if width <= 0 or len(text) <= width:
        return (text,)
    lines: list[str] = []
    for part in text.split(", "):
        if lines and len(lines[-1]) + 2 + len(part) <= width:
            lines[-1] += f", {part}"
        else:
            if lines:
                lines[-1] += ","
            lines.append(part)
    return tuple(lines)


def attr_lines(sym: SymbolDef, values: Mapping[str, str], rotation: float, width: int) -> dict[str, tuple[tuple[str, ...], float]]:
    """Per attribute: its text lines and how far it moves down for the extra lines of the attributes above it. Values wrap (see
    `wrap_lines`) only on an unrotated symbol and only in labels beside the body, never inside it."""
    wrap = width if round(rotation) % 360 == 0 else 0
    out: dict[str, tuple[tuple[str, ...], float]] = {}
    side = [a for a in sym.attrs if not a.centred]
    for a in sym.attrs:
        text = values.get(a.tag, a.default)
        lines = (text,) if a.centred else wrap_lines(text, wrap)
        out[a.tag] = (lines, 0.0)
    for a in side:
        extra = sum((len(out[b.tag][0]) - 1) * 2 * b.height for b in side if b.y > a.y + 1e-9 and abs(b.x - a.x) < 1e-6)
        out[a.tag] = (out[a.tag][0], extra)
    return out


def pin_at(key: str, pin_name: str, at: tuple[float, float], rotation: float = 0) -> tuple[float, float]:
    """World position of a pin for a symbol inserted at `at` with `rotation` degrees CCW."""
    sym = _symbol(key)
    for p in sym.pins:
        if p.name == pin_name:
            dx, dy = _rotate(p.x, p.y, rotation)
            return _clean(at[0] + dx), _clean(at[1] + dy)
    raise KeyError(f"symbol {key!r} has no pin {pin_name!r}")


def insert(
    layout: BaseLayout,
    key: str,
    at: tuple[float, float],
    rotation: float = 0,
    values: Mapping[str, str] | None = None,
    *,
    visible_text: bool = True,
    upright: bool = False,
    wrap: int = 0,
) -> Insert:
    """Place a symbol with one ATTRIB per ATTDEF.

    LibreCAD does not draw ATTRIB entities, so by default the ATTRIBs are flagged
    invisible (still readable by ezdxf and AutoCAD tools) and each non-empty visible
    value is also written as a plain TEXT on TEXT_LAYER. Pass visible_text=False to
    get classic visible ATTRIBs instead. Text stays horizontal; it is right-aligned
    when the symbol is rotated so that its local +x points west. Centred attributes
    (text inside a circle) stay centred. With `wrap`, a long value beside an unrotated symbol is written over several lines (`attr_lines`).

    With `upright`, a symbol turned over (180 degrees) keeps its label stack as drawn
    unrotated: same order, same side, left-aligned, relative to the turned body.
    """
    sym = _symbol(key)
    values = dict(values or {})
    unknown = set(values) - {a.tag for a in sym.attrs}
    if unknown:
        raise ValueError(f"symbol {key!r} has no attribute(s) {sorted(unknown)}; valid: {[a.tag for a in sym.attrs]}")
    ref = layout.add_blockref(block_for(layout.doc, key, rotation), at, dxfattribs={"layer": LAYER, "rotation": rotation})
    flow = TextEntityAlignment.MIDDLE_LEFT if math.cos(math.radians(rotation)) > -1e-9 else TextEntityAlignment.MIDDLE_RIGHT
    turned = upright and round(rotation) % 360 == 180
    if turned:
        flow = TextEntityAlignment.MIDDLE_LEFT
    lines_of = attr_lines(sym, values, rotation, wrap)
    for a in sym.attrs:
        align = TextEntityAlignment.MIDDLE_CENTER if a.centred else flow
        dx, dy = upright_attr(sym, a.x, a.y) if turned and not a.centred else _rotate(a.x, a.y, rotation)
        pos = (_clean(at[0] + dx), _clean(at[1] + dy))
        value = values.get(a.tag, a.default)
        style = text_style_for(value)
        if style not in layout.doc.styles:
            layout.doc.styles.add(style, font="unicode.shx")
        attrib = ref.add_attrib(a.tag, value, pos, dxfattribs={"layer": LAYER, "style": style, "height": a.height})
        attrib.set_placement(pos, align=align)
        if not a.visible or visible_text:
            attrib.dxf.flags |= _ATTRIB_INVISIBLE
        if visible_text and a.visible and value:
            lines, down = lines_of[a.tag]
            for i, line in enumerate(lines):
                at_line = (pos[0], _clean(pos[1] - down - i * 2 * a.height))
                layout.add_text(
                    line, height=a.height, dxfattribs={"layer": TEXT_LAYER, "style": style}
                ).set_placement(at_line, align=align)
    return ref

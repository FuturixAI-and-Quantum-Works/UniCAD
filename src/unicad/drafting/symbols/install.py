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

    ATTDEF anchors are the left-middle point of the text.
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
            attdef.set_placement((a.x, a.y), align=TextEntityAlignment.MIDDLE_LEFT)
            if not a.visible:
                attdef.dxf.flags |= _ATTRIB_INVISIBLE


def _rotate(x: float, y: float, rotation: float) -> tuple[float, float]:
    r = math.radians(rotation)
    c, s = math.cos(r), math.sin(r)
    return x * c - y * s, x * s + y * c


def _clean(v: float) -> float:
    return round(v, 6) + 0.0


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
) -> Insert:
    """Place a symbol with one ATTRIB per ATTDEF.

    LibreCAD does not draw ATTRIB entities, so by default the ATTRIBs are flagged
    invisible (still readable by ezdxf and AutoCAD tools) and each non-empty visible
    value is also written as a plain TEXT on TEXT_LAYER. Pass visible_text=False to
    get classic visible ATTRIBs instead. Text stays horizontal; it is right-aligned
    when the symbol is rotated so that its local +x points west.
    """
    sym = _symbol(key)
    values = dict(values or {})
    unknown = set(values) - {a.tag for a in sym.attrs}
    if unknown:
        raise ValueError(f"symbol {key!r} has no attribute(s) {sorted(unknown)}; valid: {[a.tag for a in sym.attrs]}")
    install(layout.doc, [key])
    ref = layout.add_blockref(sym.block_name, at, dxfattribs={"layer": LAYER, "rotation": rotation})
    align = TextEntityAlignment.MIDDLE_LEFT if math.cos(math.radians(rotation)) > -1e-9 else TextEntityAlignment.MIDDLE_RIGHT
    for a in sym.attrs:
        dx, dy = _rotate(a.x, a.y, rotation)
        pos = (_clean(at[0] + dx), _clean(at[1] + dy))
        value = values.get(a.tag, a.default)
        attrib = ref.add_attrib(a.tag, value, pos, dxfattribs={"layer": LAYER, "style": TEXT_STYLE, "height": a.height})
        attrib.set_placement(pos, align=align)
        if not a.visible or visible_text:
            attrib.dxf.flags |= _ATTRIB_INVISIBLE
        if visible_text and a.visible and value:
            layout.add_text(
                value, height=a.height, dxfattribs={"layer": TEXT_LAYER, "style": TEXT_STYLE}
            ).set_placement(pos, align=align)
    return ref

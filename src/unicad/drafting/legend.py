from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import BaseLayout

from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.tables import SymbolDrawer
from unicad.drafting.symbols import REGISTRY
from unicad.drafting.symbols.catalogue import sample_values
from unicad.drafting.symbols.install import TEXT_LAYER, install
from unicad.drafting.symbols.library.kit import LAYER, TEXT_STYLE

LEGEND_TEXT_MIN = 1.2
LINE_SAMPLES = {"line.comms": "E-SLD-COMM"}
"""Legend keys drawn as a sample of a wire layer's line style instead of a symbol."""
COMMS_LEGEND = ("line.comms", "COMMUNICATION LINK (DATA CABLE)")


def _line_sample(layout: BaseLayout, layer: str, cell: Region) -> bool:
    from unicad.drafting.sld.write import LAYERS

    doc = layout.doc
    if layer not in doc.layers:
        colour, linetype, lw = LAYERS[layer]
        doc.layers.add(layer, color=colour, linetype=linetype if linetype in doc.linetypes else "CONTINUOUS", lineweight=lw)
    y = (cell.y0 + cell.y1) / 2
    layout.add_line((cell.x0 + 0.1 * (cell.x1 - cell.x0), y), (cell.x1 - 0.1 * (cell.x1 - cell.x0), y), dxfattribs={"layer": layer})
    return True


def legend_drawer(layout: BaseLayout, fill: float = 0.8) -> SymbolDrawer:
    """Fits each registered symbol into its legend cell, without its label stack; text that is part
    of the symbol's face (an ANSI number in a relay circle) is shown with a sample value."""

    def draw(key: str, cell: Region) -> bool:
        if key in LINE_SAMPLES:
            return _line_sample(layout, LINE_SAMPLES[key], cell)
        sym = REGISTRY.get(key)
        if sym is None:
            return False
        install(layout.doc, [key])
        w, h = cell.x1 - cell.x0, cell.y1 - cell.y0
        scale = fill * min(w / sym.width, h / sym.height)
        at = (cell.x0 + (w - sym.width * scale) / 2, cell.y0 + (h - sym.height * scale) / 2)
        layout.add_blockref(sym.block_name, at, dxfattribs={"layer": LAYER, "xscale": scale, "yscale": scale})
        samples = sample_values(sym)
        for a in sym.attrs:
            if a.centred and a.visible and samples.get(a.tag):
                layout.add_text(
                    samples[a.tag], height=max(a.height * scale, LEGEND_TEXT_MIN), dxfattribs={"layer": TEXT_LAYER, "style": TEXT_STYLE}
                ).set_placement((at[0] + a.x * scale, at[1] + a.y * scale), align=TextEntityAlignment.MIDDLE_CENTER)
        return True

    return draw

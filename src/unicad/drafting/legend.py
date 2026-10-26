from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import BaseLayout

from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.tables import SymbolDrawer
from unicad.drafting.symbols import REGISTRY
from unicad.drafting.symbols.catalogue import sample_values
from unicad.drafting.symbols.install import TEXT_LAYER, install
from unicad.drafting.symbols.library.kit import LAYER, TEXT_STYLE

LEGEND_TEXT_MIN = 1.2


def legend_drawer(layout: BaseLayout, fill: float = 0.8) -> SymbolDrawer:
    """Fits each registered symbol into its legend cell, without its label stack; text that is part
    of the symbol's face (an ANSI number in a relay circle) is shown with a sample value."""

    def draw(key: str, cell: Region) -> bool:
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

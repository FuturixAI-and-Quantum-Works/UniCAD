from ezdxf.layouts import BaseLayout

from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.tables import SymbolDrawer
from unicad.drafting.symbols import REGISTRY
from unicad.drafting.symbols.install import install
from unicad.drafting.symbols.library.kit import LAYER


def legend_drawer(layout: BaseLayout, fill: float = 0.8) -> SymbolDrawer:
    """Fits each registered symbol, without its attribute text, into its legend cell."""

    def draw(key: str, cell: Region) -> bool:
        sym = REGISTRY.get(key)
        if sym is None:
            return False
        install(layout.doc, [key])
        w, h = cell.x1 - cell.x0, cell.y1 - cell.y0
        scale = fill * min(w / sym.width, h / sym.height)
        at = (cell.x0 + (w - sym.width * scale) / 2, cell.y0 + (h - sym.height * scale) / 2)
        layout.add_blockref(sym.block_name, at, dxfattribs={"layer": LAYER, "xscale": scale, "yscale": scale})
        return True

    return draw

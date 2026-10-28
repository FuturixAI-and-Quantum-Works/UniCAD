"""Write an SldLayout into a DXF document, scaled to fit a sheet region."""

import math
from dataclasses import dataclass

from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import BaseLayout

from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sld.model import Label, SldLayout, SldOptions, WireKind
from unicad.drafting.symbols import insert
from unicad.drafting.symbols.install import block_for

LAYERS: dict[str, tuple[int, str, int]] = {
    "E-SLD-AC": (7, "CONTINUOUS", 35),
    "E-SLD-DC": (7, "CONTINUOUS", 35),
    "E-SLD-CTRL": (4, "DASHED2", 18),
    "E-SLD-COMM": (6, "DASHDOT2", 18),
    "E-SLD-EARTH": (3, "CONTINUOUS", 25),
    "E-SLD-BUS": (7, "CONTINUOUS", 70),
    "E-SLD-FRAME": (3, "DASHED", 25),
    "E-SLD-TEXT": (7, "CONTINUOUS", 18),
}
_WIRE_LAYER = {WireKind.AC: "E-SLD-AC", WireKind.DC: "E-SLD-DC", WireKind.CONTROL: "E-SLD-CTRL", WireKind.COMMS: "E-SLD-COMM", WireKind.EARTH: "E-SLD-EARTH"}
_ALIGN = {
    "left": TextEntityAlignment.MIDDLE_LEFT,
    "right": TextEntityAlignment.MIDDLE_RIGHT,
    "center": TextEntityAlignment.MIDDLE_CENTER,
}
JUNCTION_R = 0.8


@dataclass(frozen=True)
class SldPlacement:
    block: str
    scale: float
    origin: tuple[float, float]
    """Modelspace point the layout's (0, 0) maps to."""
    readable: bool

    def to_sheet(self, p: tuple[float, float]) -> tuple[float, float]:
        return self.origin[0] + p[0] * self.scale, self.origin[1] + p[1] * self.scale


def _layers(doc: Drawing) -> None:
    for name, (colour, linetype, lw) in LAYERS.items():
        if name in doc.layers:
            continue
        lt = linetype if linetype in doc.linetypes else "CONTINUOUS"
        doc.layers.add(name, color=colour, linetype=lt, lineweight=lw)


def _text(block: BaseLayout, lb: Label, style: str) -> None:
    block.add_text(lb.text, height=lb.height, dxfattribs={"layer": "E-SLD-TEXT", "style": style}).set_placement(
        lb.at, align=_ALIGN[lb.align]
    )


def _dot(block: BaseLayout, c: tuple[float, float], r: float, layer: str) -> None:
    pts = [(c[0] + r * math.cos(math.radians(45 * i)), c[1] + r * math.sin(math.radians(45 * i))) for i in range(8)]
    for i in range(0, 8, 2):
        block.add_solid([c, pts[i], pts[(i + 2) % 8], pts[i + 1]], dxfattribs={"layer": layer})


def write_body(doc: Drawing, layout: SldLayout, block_name: str, text_style: str = "Standard") -> None:
    """Draw the layout at 1:1 into block `block_name`, in layout coordinates."""
    _layers(doc)
    if text_style not in doc.styles:
        doc.styles.add(text_style, font="standard.shx")
    for sym in layout.symbols:
        # LibreDWG drops the contents of a block defined after the block inserting it with attributes, so define them first.
        block_for(doc, sym.key, sym.rotation)
    block = doc.blocks.new(block_name, base_point=(0, 0))
    for f in layout.frames:
        b = f.box
        for loop in f.outline or (((b.x0, b.y0), (b.x1, b.y0), (b.x1, b.y1), (b.x0, b.y1)),):
            block.add_lwpolyline(list(loop), close=True, dxfattribs={"layer": "E-SLD-FRAME"})
        for lb in f.labels:
            _text(block, lb, text_style)
    for w in layout.wires:
        block.add_lwpolyline(list(w.points), dxfattribs={"layer": _WIRE_LAYER[w.kind]})
    for bus in layout.busbars:
        block.add_line((bus.x0, bus.y), (bus.x1, bus.y), dxfattribs={"layer": "E-SLD-BUS"})
        for lb in bus.labels:
            _text(block, lb, text_style)
    for s in layout.symbols:
        insert(block, s.key, s.at, s.rotation, dict(s.values), upright=True)
    for c in layout.callouts:
        block.add_ellipse(c.tick, major_axis=(1.6, 0), ratio=0.5, dxfattribs={"layer": "E-SLD-TEXT"})
        for lb in c.labels:
            _text(block, lb, text_style)
    for st in layout.stubs:
        block.add_lwpolyline(list(st.points), dxfattribs={"layer": "E-SLD-AC"})
        for lb in st.labels:
            _text(block, lb, text_style)
    for j in layout.junctions:
        _dot(block, j, JUNCTION_R, "E-SLD-AC")
    for lb in layout.headings:
        _text(block, lb, text_style)


def write_sld(
    doc: Drawing,
    layout: SldLayout,
    region: Region,
    *,
    block_name: str = "UC_SLD",
    text_style: str = "Standard",
    options: SldOptions = SldOptions(),
) -> SldPlacement:
    """Draw `layout` as a block and insert it into modelspace, scaled uniformly and centred in `region`."""
    write_body(doc, layout, block_name, text_style)
    scale = layout.fit_scale(region.width, region.height, options)
    ext = layout.extent
    cx, cy = (ext.x0 + ext.x1) / 2, (ext.y0 + ext.y1) / 2
    rx, ry = region.centre
    origin = (round(rx - cx * scale, 6), round(ry - cy * scale, 6))
    doc.modelspace().add_blockref(block_name, origin, dxfattribs={"xscale": scale, "yscale": scale, "layer": "E-SLD-AC"})
    return SldPlacement(block_name, scale, origin, scale >= options.min_scale)

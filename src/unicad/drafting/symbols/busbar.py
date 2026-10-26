from ezdxf.entities import Solid
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import BaseLayout

from .install import TEXT_LAYER, _ensure_resources
from .library.kit import LAYER, TAG_H, TEXT_STYLE

BUSBAR_THICKNESS = 1.0
LABEL_GAP = 2.0


def busbar(layout: BaseLayout, x0: float, x1: float, y: float, label: str = "") -> Solid:
    """Draw a horizontal busbar as a SOLID band centred on `y`, with its rating text above the left end.

    A busbar has no fixed length, so it is a helper rather than a block. Feeders
    attach to the band at any x between x0 and x1.
    """
    if x1 <= x0:
        raise ValueError(f"busbar needs x1 > x0, got {x0} .. {x1}")
    _ensure_resources(layout.doc)
    lo, hi = y - BUSBAR_THICKNESS / 2, y + BUSBAR_THICKNESS / 2
    band = layout.add_solid([(x0, lo), (x1, lo), (x0, hi), (x1, hi)], dxfattribs={"layer": LAYER})
    if label:
        layout.add_text(label, height=TAG_H, dxfattribs={"layer": TEXT_LAYER, "style": TEXT_STYLE}).set_placement(
            (x0, hi + LABEL_GAP + TAG_H / 2), align=TextEntityAlignment.MIDDLE_LEFT
        )
    return band

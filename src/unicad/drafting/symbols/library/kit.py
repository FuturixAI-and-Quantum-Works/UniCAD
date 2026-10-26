import math
from collections.abc import Sequence

from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import BlockLayout

from ..model import Attr, Pin, PinKind, Side

LAYER = "G-SYMBOL"
TEXT_STYLE = "UC_TEXT"

TAG_H = 2.5
ATTR_H = 2.0
ATTR_GAP = 2.5
LINE_PITCH = 4.0

Point = tuple[float, float]

_ALIGN = {
    "c": TextEntityAlignment.MIDDLE_CENTER,
    "l": TextEntityAlignment.MIDDLE_LEFT,
    "r": TextEntityAlignment.MIDDLE_RIGHT,
}

DC = PinKind.DC
DC_POS = PinKind.DC_POS
DC_NEG = PinKind.DC_NEG
AC = PinKind.AC
SIG = PinKind.SIGNAL
PE = PinKind.EARTH


class Pen:
    """Draws one symbol's geometry. Everything goes on LAYER with BYLAYER styling."""

    def __init__(self, block: BlockLayout):
        self.b = block

    def line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.b.add_line((x0, y0), (x1, y1), dxfattribs={"layer": LAYER})

    def path(self, *pts: Point) -> None:
        for p, q in zip(pts, pts[1:]):
            self.line(*p, *q)

    def poly(self, pts: Sequence[Point], close: bool = False) -> None:
        self.b.add_lwpolyline(list(pts), close=close, dxfattribs={"layer": LAYER})

    def rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True)

    def circle(self, cx: float, cy: float, r: float) -> None:
        self.b.add_circle((cx, cy), r, dxfattribs={"layer": LAYER})

    def arc(self, cx: float, cy: float, r: float, a0: float, a1: float) -> None:
        self.b.add_arc((cx, cy), r, a0, a1, dxfattribs={"layer": LAYER})

    def solid(self, *pts: Point) -> None:
        if len(pts) == 4:
            pts = (pts[0], pts[1], pts[3], pts[2])
        self.b.add_solid(list(pts), dxfattribs={"layer": LAYER})

    def fill_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.solid((x0, y0), (x1, y0), (x1, y1), (x0, y1))

    def dot(self, cx: float, cy: float, r: float = 1.0) -> None:
        pts = [(cx + r * math.cos(math.radians(45 * i)), cy + r * math.sin(math.radians(45 * i))) for i in range(8)]
        for i in range(0, 8, 2):
            self.solid((cx, cy), pts[i], pts[i + 1], pts[(i + 2) % 8])

    def arrowhead(self, tip: Point, direction_deg: float, length: float = 2.5, width: float = 1.4) -> None:
        a = math.radians(direction_deg)
        ux, uy = math.cos(a), math.sin(a)
        bx, by = tip[0] - ux * length, tip[1] - uy * length
        nx, ny = -uy * width / 2, ux * width / 2
        self.solid(tip, (bx + nx, by + ny), (bx - nx, by - ny))

    def arrow(self, x0: float, y0: float, x1: float, y1: float, length: float = 2.5, width: float = 1.4) -> None:
        a = math.atan2(y1 - y0, x1 - x0)
        self.line(x0, y0, x1 - math.cos(a) * length * 0.5, y1 - math.sin(a) * length * 0.5)
        self.arrowhead((x1, y1), math.degrees(a), length, width)

    def dashed(self, x0: float, y0: float, x1: float, y1: float, dash: float = 2.5, gap: float = 1.5) -> None:
        length = math.hypot(x1 - x0, y1 - y0)
        n = max(1, round((length + gap) / (dash + gap)))
        d = (length - (n - 1) * gap) / n
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        for i in range(n):
            s = i * (d + gap)
            self.line(x0 + ux * s, y0 + uy * s, x0 + ux * (s + d), y0 + uy * (s + d))

    def dashed_rect(self, x0: float, y0: float, x1: float, y1: float, dash: float = 3.0, gap: float = 1.5) -> None:
        self.dashed(x0, y0, x1, y0, dash, gap)
        self.dashed(x1, y0, x1, y1, dash, gap)
        self.dashed(x1, y1, x0, y1, dash, gap)
        self.dashed(x0, y1, x0, y0, dash, gap)

    def dashed_circle(self, cx: float, cy: float, r: float, segments: int = 16) -> None:
        step = 360 / segments
        for i in range(segments):
            self.arc(cx, cy, r, i * step, i * step + step * 0.6)

    def text(self, s: str, x: float, y: float, h: float = 2.5, align: str = "c") -> None:
        t = self.b.add_text(s, height=h, dxfattribs={"layer": LAYER, "style": TEXT_STYLE})
        t.set_placement((x, y), align=_ALIGN[align])

    def tilde(self, cx: float, cy: float, w: float = 6.0) -> None:
        r = w / 4
        self.arc(cx - r, cy, r, 0, 180)
        self.arc(cx + r, cy, r, 180, 360)

    def equals(self, cx: float, cy: float, w: float = 5.0, gap: float = 1.6) -> None:
        self.line(cx - w / 2, cy + gap / 2, cx + w / 2, cy + gap / 2)
        self.line(cx - w / 2, cy - gap / 2, cx + w / 2, cy - gap / 2)

    def earth(self, cx: float, y_top: float, w: float = 6.0, step: float = 1.25) -> None:
        for i, f in enumerate((1.0, 0.64, 0.28)):
            y = y_top - i * step
            self.line(cx - w / 2 * f, y, cx + w / 2 * f, y)

    def switch(
        self,
        cx: float,
        y0: float,
        y1: float,
        qual: str | None = None,
        *,
        dx: float = 3.2,
        hinge: float = 0.3,
        contact: float = 0.7,
    ) -> tuple[float, float]:
        """Vertical single-pole switch between y0 (bottom stem end) and y1 (top stem end).

        qual picks the IEC function qualifier at the fixed contact. Returns the
        (hinge_y, contact_y) so callers can hang extra glyphs on the stems.
        """
        length = y1 - y0
        yh, yc = y0 + hinge * length, y0 + contact * length
        self.line(cx, y0, cx, yh)
        self.line(cx, yh, cx - dx, yc + 1.0)
        qual_h = 2.5
        if qual == "cross":
            self.line(cx, y1, cx, yc)
            self.line(cx - qual_h / 2, yc - qual_h / 2, cx + qual_h / 2, yc + qual_h / 2)
            self.line(cx - qual_h / 2, yc + qual_h / 2, cx + qual_h / 2, yc - qual_h / 2)
        elif qual == "bar":
            self.line(cx, y1, cx, yc)
            self.line(cx - 1.75, yc, cx + 1.75, yc)
        elif qual == "square":
            self.line(cx, y1, cx, yc + qual_h / 2)
            self.rect(cx - qual_h / 2, yc - qual_h / 2, cx + qual_h / 2, yc + qual_h / 2)
        elif qual == "arc":
            r = 1.5
            self.line(cx, y1, cx, yc + 2 * r)
            self.arc(cx, yc + r, r, 90, 270)
        else:
            self.line(cx, y1, cx, yc)
        return yh, yc


def attrs_east(
    width: float, height: float, spec: Sequence[tuple[str, float, str]], centre_y: float | None = None
) -> tuple[Attr, ...]:
    cy = height / 2 if centre_y is None else centre_y
    top = cy + (len(spec) - 1) * LINE_PITCH / 2
    return tuple(
        Attr(tag, width + ATTR_GAP, round(top - i * LINE_PITCH - h / 2, 3), h, default)
        for i, (tag, h, default) in enumerate(spec)
    )


def std_attrs(
    width: float,
    height: float,
    *extra: tuple[str, str],
    desc: bool = True,
    centre_y: float | None = None,
) -> tuple[Attr, ...]:
    spec: list[tuple[str, float, str]] = [("TAG", TAG_H, ""), ("RATING", ATTR_H, "")]
    spec += [(t, ATTR_H, d) for t, d in extra]
    if desc:
        spec.append(("DESC", ATTR_H, ""))
    return attrs_east(width, height, spec, centre_y)


def pin_n(x: float, h: float, name: str = "in", kind: PinKind = AC) -> Pin:
    return Pin(name, x, h, Side.N, kind)


def pin_s(x: float, name: str = "out", kind: PinKind = AC) -> Pin:
    return Pin(name, x, 0.0, Side.S, kind)


def inline_pins(cx: float, h: float, kind: PinKind) -> tuple[Pin, Pin]:
    return pin_n(cx, h, "in", kind), pin_s(cx, "out", kind)


def blade_box(
    p: Pen,
    cx: float,
    yh: float,
    yc: float,
    dx: float,
    t: float,
    length: float,
    half_width: float,
    solid: bool = False,
) -> None:
    """Rectangle laid along the switch blade (trip unit or fuse body), centred at fraction t."""
    tip = (cx - dx, yc + 1.0)
    vx, vy = tip[0] - cx, tip[1] - yh
    n = math.hypot(vx, vy)
    ux, uy = vx / n, vy / n
    mx, my = cx + vx * t, yh + vy * t
    nx, ny = -uy * half_width, ux * half_width
    a, b = length / 2, -length / 2
    pts = [
        (mx + ux * b + nx, my + uy * b + ny),
        (mx + ux * a + nx, my + uy * a + ny),
        (mx + ux * a - nx, my + uy * a - ny),
        (mx + ux * b - nx, my + uy * b - ny),
    ]
    if solid:
        p.solid(*pts)
    else:
        p.poly(pts, close=True)


def dc_mark(p: Pen, x: float, y: float, w: float = 3.0) -> None:
    p.line(x - w / 2, y + 0.5, x + w / 2, y + 0.5)
    p.line(x - w / 2, y - 0.5, x - w / 6, y - 0.5)
    p.line(x + w / 6, y - 0.5, x + w / 2, y - 0.5)

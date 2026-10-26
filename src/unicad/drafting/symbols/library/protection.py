from ..model import Attr, Category, Pin, Side
from ..registry import symbol
from .kit import AC, ATTR_GAP, ATTR_H, SIG, TAG_H, Pen, std_attrs

C = Category.PROTECTION


def _spd_body(p: Pen, cx: float) -> None:
    p.line(cx, 20.0, cx, 15.0)
    p.rect(cx - 1.75, 7.5, cx + 1.75, 15.0)
    p.arrow(cx - 2.6, 8.8, cx + 2.6, 14.2, 2.2, 1.3)
    p.line(cx, 7.5, cx, 5.0)
    p.earth(cx, 5.0, w=6.0, step=1.2)


@symbol(
    "prot.spd_ac",
    "AC surge protective device (Type 2)",
    C,
    "IEC 60617-7 surge protective device / IS 12032",
    10.0,
    20.0,
    (Pin("in", 5.0, 20.0, Side.N, AC),),
    std_attrs(10.0, 20.0),
)
def spd_ac(b):
    _spd_body(Pen(b), 5.0)


@symbol(
    "prot.lightning_arrester",
    "Lightning / surge arrester to earth",
    C,
    "IEC 60617-7 surge arrester / IS 12032",
    10.0,
    20.0,
    (Pin("in", 5.0, 20.0, Side.N, AC),),
    std_attrs(10.0, 20.0),
)
def lightning_arrester(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 15.0)
    p.rect(3.25, 7.5, 6.75, 15.0)
    p.arrow(5.0, 13.8, 5.0, 8.7, 2.4, 1.5)
    p.line(5.0, 7.5, 5.0, 5.0)
    p.earth(5.0, 5.0, w=6.0, step=1.2)


@symbol(
    "prot.relay",
    "Protection relay (ANSI function)",
    C,
    "IEC 60617-7 relay / IEEE C37.2 device numbers",
    20.0,
    20.0,
    (Pin("in", 0.0, 10.0, Side.W, SIG), Pin("trip", 20.0, 10.0, Side.E, SIG)),
    (
        Attr("TAG", 20.0 + ATTR_GAP, 14.25, TAG_H),
        Attr("DESC", 20.0 + ATTR_GAP, 18.25, ATTR_H),
        Attr("ANSI", 20.0 + ATTR_GAP, 6.25, ATTR_H),
        Attr("RATING", 20.0 + ATTR_GAP, 2.25, ATTR_H),
    ),
)
def relay(b):
    p = Pen(b)
    p.line(0.0, 10.0, 2.5, 10.0)
    p.line(17.5, 10.0, 20.0, 10.0)
    p.rect(2.5, 5.0, 17.5, 15.0)
    p.line(6.0, 5.0, 6.0, 15.0)

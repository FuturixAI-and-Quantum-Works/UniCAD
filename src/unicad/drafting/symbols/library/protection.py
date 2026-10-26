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
    "IEC 60617 S00373 surge diverter; lightning arrester / IS 12032",
    10.0,
    20.0,
    (Pin("in", 5.0, 20.0, Side.N, AC),),
    std_attrs(10.0, 20.0),
    tag_prefix="SPD",
)
def spd_ac(b):
    _spd_body(Pen(b), 5.0)


@symbol(
    "prot.lightning_arrester",
    "Lightning / surge arrester to earth",
    C,
    "IEC 60617 S00373 surge diverter; lightning arrester / IS 12032",
    10.0,
    20.0,
    (Pin("in", 5.0, 20.0, Side.N, AC),),
    std_attrs(10.0, 20.0),
    tag_prefix="LA",
)
def lightning_arrester(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 15.0)
    p.rect(3.25, 7.5, 6.75, 15.0)
    p.arrow(5.0, 13.8, 5.0, 8.7, 2.4, 1.5)
    p.line(5.0, 7.5, 5.0, 5.0)
    p.earth(5.0, 5.0, w=6.0, step=1.2)


RELAY_STD = "IEEE C37.2 device number in circle; IEC 60617 S00327 measuring relay"
RELAY_R = 5.0
RELAY_H = 17.5
RELAY_Y = 7.5
RELAY_GROUP_MAX = 6
ANSI_H = 2.5
ANSI_CHARS = 4


def _relay_attrs(width: float, ansi: tuple[Attr, ...]) -> tuple[Attr, ...]:
    x = width + ATTR_GAP
    return (
        Attr("TAG", x, 15.5, TAG_H),
        Attr("DESC", x, 11.5, ATTR_H),
        *ansi,
        Attr("RATING", x, 3.5, ATTR_H),
    )


def _ansi_attr(tag: str, cx: float) -> Attr:
    return Attr(tag, cx, RELAY_Y, ANSI_H, centred=True, max_chars=ANSI_CHARS)


@symbol(
    "prot.relay",
    "Protection relay (ANSI function)",
    C,
    RELAY_STD,
    2 * RELAY_R,
    RELAY_H,
    (Pin("in", 0.0, RELAY_Y, Side.W, SIG), Pin("trip", 2 * RELAY_R, RELAY_Y, Side.E, SIG)),
    _relay_attrs(2 * RELAY_R, (_ansi_attr("ANSI", RELAY_R),)),
    tag_prefix="RLY",
)
def relay(b):
    Pen(b).circle(RELAY_R, RELAY_Y, RELAY_R)


_GROUP_W = 2 * RELAY_R * RELAY_GROUP_MAX


@symbol(
    "prot.relay_group",
    "Protection relay, multi-function (up to 6 ANSI circles)",
    C,
    RELAY_STD,
    _GROUP_W,
    RELAY_H,
    (Pin("in", 0.0, RELAY_Y, Side.W, SIG), Pin("trip", _GROUP_W, RELAY_Y, Side.E, SIG)),
    _relay_attrs(
        _GROUP_W, tuple(_ansi_attr(f"ANSI{i + 1}", RELAY_R + 2 * RELAY_R * i) for i in range(RELAY_GROUP_MAX))
    ),
    tag_prefix="RLY",
)
def relay_group(b):
    p = Pen(b)
    for i in range(RELAY_GROUP_MAX):
        p.circle(RELAY_R + 2 * RELAY_R * i, RELAY_Y, RELAY_R)


@symbol(
    "prot.la_counter",
    "Lightning arrester with surge counter",
    C,
    "IEC 60617 S00373 surge diverter + surge counter box",
    10.0,
    27.5,
    (Pin("in", 5.0, 27.5, Side.N, AC),),
    std_attrs(10.0, 27.5),
    tag_prefix="LA",
)
def la_counter(b):
    p = Pen(b)
    p.line(5.0, 27.5, 5.0, 22.5)
    p.rect(3.25, 15.0, 6.75, 22.5)
    p.arrow(5.0, 21.3, 5.0, 16.2, 2.4, 1.5)
    p.line(5.0, 15.0, 5.0, 12.5)
    p.rect(2.5, 7.5, 7.5, 12.5)
    p.text("SC", 5.0, 10.0, 1.8)
    p.line(5.0, 7.5, 5.0, 5.0)
    p.earth(5.0, 5.0, w=6.0, step=1.2)

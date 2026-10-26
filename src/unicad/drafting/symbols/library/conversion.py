from ..model import Category, Pin, Side
from ..registry import symbol
from .kit import AC, DC, DC_NEG, DC_POS, Pen, pin_n, pin_s, std_attrs

C = Category.CONVERSION
STD = "IEC 60617-6 converter / IS 12032"


def _converter(p: Pen, x0: float, y0: float, x1: float, y1: float, upper: str = "=", lower: str = "~", glyph: float = 5.0) -> None:
    w, h = x1 - x0, y1 - y0
    p.rect(x0, y0, x1, y1)
    p.line(x0, y0, x1, y1)
    for s, cx, cy in ((upper, x0 + 0.27 * w, y0 + 0.73 * h), (lower, x0 + 0.73 * w, y0 + 0.27 * h)):
        if s == "=":
            p.equals(cx, cy, glyph)
        else:
            p.tilde(cx, cy, glyph * 1.2)


@symbol(
    "inv.string",
    "String inverter",
    C,
    STD,
    25.0,
    25.0,
    (pin_n(12.5, 25.0, "dc", DC), pin_s(12.5, "ac")),
    std_attrs(25.0, 25.0),
)
def string_inverter(b):
    p = Pen(b)
    p.line(12.5, 25.0, 12.5, 22.5)
    p.line(12.5, 2.5, 12.5, 0.0)
    _converter(p, 0.0, 2.5, 25.0, 22.5)


@symbol(
    "inv.central",
    "Central inverter",
    C,
    STD,
    35.0,
    32.5,
    (pin_n(17.5, 32.5, "dc", DC), pin_s(17.5, "ac")),
    std_attrs(35.0, 32.5),
)
def central_inverter(b):
    p = Pen(b)
    p.line(17.5, 32.5, 17.5, 30.0)
    p.line(17.5, 2.5, 17.5, 0.0)
    p.rect(0.0, 2.5, 35.0, 30.0)
    _converter(p, 1.5, 4.0, 33.5, 28.5, glyph=7.0)


@symbol(
    "inv.micro",
    "Microinverter",
    C,
    STD,
    15.0,
    20.0,
    (
        Pin("dc+", 5.0, 20.0, Side.N, DC_POS),
        Pin("dc-", 10.0, 20.0, Side.N, DC_NEG),
        pin_s(7.5, "ac"),
    ),
    std_attrs(15.0, 20.0),
)
def micro_inverter(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 17.5)
    p.line(10.0, 20.0, 10.0, 17.5)
    p.line(7.5, 2.5, 7.5, 0.0)
    _converter(p, 0.0, 2.5, 15.0, 17.5, glyph=3.5)


@symbol(
    "inv.pcs",
    "Bidirectional power conversion system (PCS)",
    C,
    STD,
    25.0,
    25.0,
    (pin_n(12.5, 25.0, "dc", DC), pin_s(12.5, "ac")),
    std_attrs(25.0, 25.0),
)
def pcs(b):
    p = Pen(b)
    p.line(12.5, 25.0, 12.5, 22.5)
    p.line(12.5, 2.5, 12.5, 0.0)
    p.rect(0.0, 2.5, 25.0, 22.5)
    p.line(3.0, 4.5, 22.0, 20.5)
    p.arrowhead((22.5, 20.9), 40.1, 2.8, 1.6)
    p.arrowhead((2.5, 4.1), 220.1, 2.8, 1.6)
    p.equals(6.5, 17.5, 5.0)
    p.tilde(18.5, 7.5, 6.0)


@symbol(
    "inv.optimiser",
    "DC-DC power optimiser",
    C,
    STD,
    15.0,
    20.0,
    (
        Pin("in+", 5.0, 20.0, Side.N, DC_POS),
        Pin("in-", 10.0, 20.0, Side.N, DC_NEG),
        Pin("out+", 5.0, 0.0, Side.S, DC_POS),
        Pin("out-", 10.0, 0.0, Side.S, DC_NEG),
    ),
    std_attrs(15.0, 20.0),
)
def optimiser(b):
    p = Pen(b)
    for x in (5.0, 10.0):
        p.line(x, 20.0, x, 17.5)
        p.line(x, 2.5, x, 0.0)
    _converter(p, 0.0, 2.5, 15.0, 17.5, upper="=", lower="=", glyph=3.5)

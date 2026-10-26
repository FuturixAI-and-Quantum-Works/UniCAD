from ..model import Category, Pin, Side
from ..registry import symbol
from .kit import DC, DC_NEG, DC_POS, Pen, pin_s, std_attrs

C = Category.PV
STD = "IEC 60617-6 photovoltaic generator / IS 12032"


@symbol(
    "pv.module",
    "PV module",
    C,
    STD,
    10.0,
    20.0,
    (Pin("dc+", 5.0, 20.0, Side.N, DC_POS), Pin("dc-", 5.0, 0.0, Side.S, DC_NEG)),
    std_attrs(10.0, 20.0),
)
def module(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 17.5)
    p.line(5.0, 2.5, 5.0, 0.0)
    p.rect(0.0, 2.5, 10.0, 17.5)
    p.line(0.0, 2.5, 10.0, 17.5)
    p.line(1.5, 14.0, 3.5, 14.0)
    p.line(2.5, 13.0, 2.5, 15.0)
    p.line(6.5, 6.0, 8.5, 6.0)


@symbol(
    "pv.string",
    "PV string (series group)",
    C,
    STD,
    30.0,
    15.0,
    (pin_s(15.0, "dc", DC),),
    std_attrs(30.0, 15.0, ("COUNT", "")),
)
def string(b):
    p = Pen(b)
    p.line(15.0, 5.0, 15.0, 0.0)
    p.rect(0.0, 5.0, 30.0, 15.0)
    for i in range(4):
        x0 = i * 7.5
        if i:
            p.line(x0, 5.0, x0, 15.0)
        p.line(x0, 5.0, x0 + 7.5, 15.0)


@symbol(
    "pv.array",
    "PV array / sub-array",
    C,
    STD,
    35.0,
    40.0,
    (pin_s(17.5, "dc", DC),),
    std_attrs(35.0, 40.0, ("COUNT", "")),
)
def array(b):
    p = Pen(b)
    p.line(17.5, 5.0, 17.5, 0.0)
    p.dashed_rect(0.0, 5.0, 35.0, 40.0)
    for row in range(3):
        y0 = 7.5 + row * 10.0
        x0 = 2.5
        p.rect(x0, y0, x0 + 30.0, y0 + 7.5)
        for i in range(4):
            xi = x0 + i * 7.5
            if i:
                p.line(xi, y0, xi, y0 + 7.5)
            p.line(xi, y0, xi + 7.5, y0 + 7.5)

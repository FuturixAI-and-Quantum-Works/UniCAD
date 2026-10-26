from ..model import Attr, Category, Pin, Side
from ..registry import symbol
from .kit import AC, ATTR_GAP, ATTR_H, SIG, TAG_H, Pen, pin_n, pin_s, std_attrs

C = Category.TRANSFORMER
STD = "IEC 60617-6 transformer / IS 12032"


@symbol(
    "tx.power_2w",
    "Two-winding power transformer",
    C,
    STD,
    15.0,
    22.5,
    (pin_n(7.5, 22.5), pin_s(7.5)),
    std_attrs(15.0, 22.5, ("VECTOR", "Dyn11")),
)
def power_2w(b):
    p = Pen(b)
    p.circle(7.5, 15.0, 7.5)
    p.circle(7.5, 7.5, 7.5)


@symbol(
    "tx.power_3w",
    "Three-winding power transformer",
    C,
    STD,
    20.0,
    20.0,
    (pin_n(7.5, 20.0), pin_s(7.5), Pin("ter", 20.0, 10.0, Side.E, AC)),
    (
        Attr("TAG", 22.5, 15.0, TAG_H),
        Attr("RATING", 22.5, 18.5, ATTR_H),
        Attr("VECTOR", 22.5, 5.0, ATTR_H, "YNyn0d11"),
        Attr("DESC", 22.5, 1.5, ATTR_H),
    ),
)
def power_3w(b):
    p = Pen(b)
    p.circle(7.5, 13.75, 5.0)
    p.circle(7.5, 6.25, 5.0)
    p.circle(14.0, 10.0, 5.0)
    p.line(7.5, 20.0, 7.5, 18.75)
    p.line(7.5, 1.25, 7.5, 0.0)
    p.line(19.0, 10.0, 20.0, 10.0)


@symbol(
    "tx.aux",
    "Auxiliary / station transformer",
    C,
    STD,
    10.0,
    15.0,
    (pin_n(5.0, 15.0), pin_s(5.0)),
    std_attrs(10.0, 15.0, ("VECTOR", "Dyn11")),
)
def aux(b):
    p = Pen(b)
    p.circle(5.0, 10.0, 5.0)
    p.circle(5.0, 5.0, 5.0)


@symbol(
    "tx.ct",
    "Current transformer (CT)",
    C,
    "IEC 60617-6 current transformer / IS 12032",
    10.0,
    15.0,
    (pin_n(5.0, 15.0), pin_s(5.0), Pin("sec", 10.0, 7.5, Side.E, SIG)),
    (
        Attr("TAG", 12.5, 12.5, TAG_H),
        Attr("DESC", 12.5, 16.5, ATTR_H),
        Attr("RATING", 12.5, 3.0, ATTR_H),
    ),
)
def ct(b):
    p = Pen(b)
    p.line(5.0, 15.0, 5.0, 0.0)
    p.circle(5.0, 7.5, 3.0)
    p.line(8.0, 7.5, 10.0, 7.5)
    p.dot(3.4, 11.6, 0.55)


@symbol(
    "tx.pt",
    "Potential / voltage transformer (PT / VT)",
    C,
    "IEC 60617-6 voltage transformer / IS 12032",
    10.0,
    12.5,
    (pin_n(5.0, 12.5, "pri"), pin_s(5.0, "sec", SIG)),
    std_attrs(10.0, 12.5, ("RATIO", "")),
)
def pt(b):
    p = Pen(b)
    p.circle(5.0, 8.75, 3.75)
    p.circle(5.0, 3.75, 3.75)

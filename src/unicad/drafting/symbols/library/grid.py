from ..model import Category
from ..registry import symbol
from .kit import Pen, pin_n, pin_s, std_attrs

C = Category.GRID


@symbol(
    "grid.utility",
    "Utility grid infeed",
    C,
    "UniCAD composite (network feeder)",
    20.0,
    17.5,
    (pin_s(10.0, "out"),),
    std_attrs(20.0, 17.5),
)
def utility(b):
    p = Pen(b)
    p.rect(0.0, 5.0, 20.0, 17.5)
    p.line(10.0, 5.0, 10.0, 0.0)
    p.path((11.5, 16.0), (7.0, 10.5), (12.0, 10.5), (8.5, 6.0))
    p.arrowhead((8.5, 6.0), 235.0, 2.2, 1.5)


@symbol(
    "grid.oh_line",
    "Overhead line termination",
    C,
    "IEC 60617-11 overhead line / IS 12032",
    15.0,
    20.0,
    (pin_s(7.5, "out"),),
    std_attrs(15.0, 20.0),
)
def oh_line(b):
    p = Pen(b)
    p.line(7.5, 0.0, 7.5, 12.5)
    p.dot(7.5, 12.5, 0.9)
    p.line(2.5, 15.0, 12.5, 15.0)
    p.line(7.5, 12.5, 7.5, 15.0)
    p.dashed(7.5, 15.0, 7.5, 20.0, dash=1.8, gap=1.2)


@symbol(
    "grid.cable_end",
    "Cable termination / sealing end",
    C,
    "IEC 60617-11 cable sealing end / IS 12032",
    10.0,
    20.0,
    (pin_n(5.0, 20.0, "cable"), pin_s(5.0, "term")),
    std_attrs(10.0, 20.0),
)
def cable_end(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 14.0)
    p.solid((5.0, 14.0), (1.75, 6.5), (8.25, 6.5))
    p.line(5.0, 6.5, 5.0, 0.0)


@symbol(
    "grid.dg",
    "Diesel generator set (DG)",
    C,
    "IEC 60617-6 generator / IS 12032",
    15.0,
    17.5,
    (pin_s(7.5, "out"),),
    std_attrs(15.0, 17.5),
)
def dg(b):
    p = Pen(b)
    p.circle(7.5, 10.0, 7.5)
    p.line(7.5, 2.5, 7.5, 0.0)
    p.text("G", 7.5, 11.5, 4.0)
    p.tilde(7.5, 6.5, 4.0)


@symbol(
    "grid.load",
    "Load",
    C,
    "IEC 60617-2 load block",
    15.0,
    17.5,
    (pin_n(7.5, 17.5, "in"),),
    std_attrs(15.0, 17.5),
)
def load(b):
    p = Pen(b)
    p.line(7.5, 17.5, 7.5, 10.0)
    p.rect(0.0, 0.0, 15.0, 10.0)
    p.text("LOAD", 7.5, 5.0, 2.8)

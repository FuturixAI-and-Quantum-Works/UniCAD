from ..model import Attr, Category, Pin, Side
from ..registry import symbol
from .kit import AC, ATTR_GAP, ATTR_H, TAG_H, Pen

C = Category.ANNOTATION
STD = "UniCAD drafting convention (IS 696 / IS 12032 style)"


@symbol("ann.junction", "Junction dot", C, STD, 5.0, 5.0, (), ())
def junction(b):
    Pen(b).dot(2.5, 2.5, 1.0)


@symbol(
    "ann.offsheet",
    "Off-sheet connector",
    C,
    STD,
    10.0,
    15.0,
    (Pin("link", 5.0, 15.0, Side.N, AC),),
    (
        Attr("SHEET", 10.0 + ATTR_GAP, 8.5, TAG_H),
        Attr("DESC", 10.0 + ATTR_GAP, 4.0, ATTR_H),
    ),
)
def offsheet(b):
    p = Pen(b)
    p.line(5.0, 15.0, 5.0, 12.5)
    p.poly([(0.0, 12.5), (10.0, 12.5), (10.0, 5.0), (5.0, 0.0), (0.0, 5.0)], close=True)


@symbol(
    "ann.cable_tag",
    "Cable callout tag",
    C,
    STD,
    35.0,
    5.0,
    (),
    (
        Attr("TAG", 6.5, 8.25, TAG_H),
        Attr("RATING", 6.5, 2.5, ATTR_H),
        Attr("DESC", 6.5, 12.25, ATTR_H),
    ),
)
def cable_tag(b):
    p = Pen(b)
    p.line(0.0, 0.0, 5.0, 5.0)
    p.line(5.0, 5.0, 35.0, 5.0)


@symbol(
    "ann.revision",
    "Revision triangle",
    C,
    STD,
    10.0,
    10.0,
    (),
    (
        Attr("REV", 3.9, 3.2, TAG_H),
        Attr("DESC", 12.5, 3.0, ATTR_H),
    ),
)
def revision(b):
    p = Pen(b)
    p.poly([(0.0, 0.0), (10.0, 0.0), (5.0, 8.66)], close=True)


@symbol("ann.north", "North arrow", C, STD, 15.0, 15.0, (), ())
def north(b):
    p = Pen(b)
    p.circle(7.5, 6.0, 5.5)
    p.arrow(7.5, 1.5, 7.5, 10.5, 4.5, 3.0)
    p.text("N", 7.5, 13.3, 2.8)


@symbol(
    "ann.note_marker",
    "Note marker",
    C,
    STD,
    10.0,
    10.0,
    (),
    (Attr("NUM", 3.3, 5.0, TAG_H),),
)
def note_marker(b):
    Pen(b).circle(5.0, 5.0, 4.5)


@symbol(
    "ann.section_marker",
    "Section / detail marker",
    C,
    STD,
    10.0,
    12.0,
    (),
    (
        Attr("ID", 3.3, 4.0, TAG_H),
        Attr("SHEET", 10.0 + ATTR_GAP, 4.0, ATTR_H),
    ),
)
def section_marker(b):
    p = Pen(b)
    p.circle(5.0, 4.5, 4.5)
    p.solid((5.0, 12.0), (3.5, 9.0), (6.5, 9.0))

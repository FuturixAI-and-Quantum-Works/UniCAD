from ..model import Category, Pin, Side
from ..registry import symbol
from .kit import DC, DC_POS, Pen, blade_box, dc_mark, inline_pins, std_attrs

C = Category.DC_PROTECTION
CX = 5.0


@symbol("dc.fuse", "gPV string fuse", C, "IEC 60617 S00362", 10.0, 20.0, inline_pins(CX, 20.0, DC), std_attrs(10.0, 20.0))
def fuse(b):
    p = Pen(b)
    p.line(CX, 0, CX, 20.0)
    p.rect(CX - 1.5, 5.5, CX + 1.5, 14.5)


@symbol("dc.isolator", "DC isolator / disconnector", C, "IEC 60617 S00290 + DC", 10.0, 20.0, inline_pins(CX, 20.0, DC), std_attrs(10.0, 20.0))
def isolator(b):
    p = Pen(b)
    p.switch(CX, 0, 20.0, "square")
    dc_mark(p, 8.0, 3.0, 3.0)


@symbol("dc.mcb", "DC miniature circuit breaker", C, "IEC 60617 S00287 + DC", 10.0, 20.0, inline_pins(CX, 20.0, DC), std_attrs(10.0, 20.0))
def mcb(b):
    p = Pen(b)
    yh, yc = p.switch(CX, 0, 20.0, "cross")
    blade_box(p, CX, yh, yc, 3.2, 0.5, 3.2, 0.8)
    dc_mark(p, 8.0, 3.0, 3.0)


@symbol(
    "dc.spd",
    "DC surge protective device (Type 2)",
    C,
    "IEC 60617-7 surge protective device / IS 12032",
    15.0,
    25.0,
    (Pin("in", 7.5, 25.0, Side.N, DC),),
    std_attrs(15.0, 25.0),
)
def spd(b):
    p = Pen(b)
    p.line(7.5, 25.0, 7.5, 20.0)
    p.line(3.75, 20.0, 11.25, 20.0)
    p.dot(7.5, 20.0, 0.6)
    for x in (3.75, 11.25):
        p.line(x, 20.0, x, 16.5)
        p.rect(x - 1.5, 9.5, x + 1.5, 16.5)
        p.arrow(x - 1.0, 10.5, x + 1.0, 15.5, 1.8, 1.1)
        p.line(x, 9.5, x, 6.5)
    p.line(3.75, 6.5, 11.25, 6.5)
    p.dot(7.5, 6.5, 0.6)
    p.line(7.5, 6.5, 7.5, 5.0)
    p.earth(7.5, 5.0, w=6.0, step=1.2)


@symbol(
    "dc.diode",
    "Blocking diode",
    C,
    "IEC 60617-5 semiconductor diode / IS 12032",
    10.0,
    20.0,
    (Pin("in", CX, 20.0, Side.N, DC_POS), Pin("out", CX, 0.0, Side.S, DC_POS)),
    std_attrs(10.0, 20.0),
)
def diode(b):
    p = Pen(b)
    p.line(CX, 20.0, CX, 13.5)
    p.poly([(CX - 2.5, 13.5), (CX + 2.5, 13.5), (CX, 8.5)], close=True)
    p.line(CX - 2.5, 8.5, CX + 2.5, 8.5)
    p.line(CX, 8.5, CX, 0.0)


@symbol(
    "dc.scb",
    "String combiner box (SCB)",
    C,
    "IEC 60617 enclosure + S00362 fuses",
    30.0,
    35.0,
    (Pin("in", 15.0, 35.0, Side.N, DC), Pin("out", 15.0, 0.0, Side.S, DC)),
    std_attrs(30.0, 35.0, ("COUNT", "")),
)
def scb(b):
    p = Pen(b)
    p.dashed_rect(0.0, 2.5, 30.0, 35.0)
    p.line(15.0, 35.0, 15.0, 30.0)
    p.fill_rect(5.0, 29.6, 25.0, 30.4)
    for x in (5.0, 10.0, 15.0, 20.0, 25.0):
        p.line(x, 30.0, x, 13.0)
        p.rect(x - 1.25, 16.5, x + 1.25, 25.0)
    p.fill_rect(5.0, 12.6, 25.0, 13.4)
    p.line(15.0, 13.0, 15.0, 0.0)

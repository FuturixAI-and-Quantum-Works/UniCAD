from ..model import Category, Pin, Side
from ..registry import symbol
from .kit import PE, Pen, std_attrs

C = Category.EARTHING
STD = "IEC 60617 S00200 earth, general symbol / IS 12032"


def _pe_pin(cx: float, h: float) -> tuple[Pin, ...]:
    return (Pin("pe", cx, h, Side.N, PE),)


@symbol("earth.earth", "Earth", C, STD, 10.0, 10.0, _pe_pin(5.0, 10.0), std_attrs(10.0, 10.0), tag_prefix="ES")
def earth(b):
    p = Pen(b)
    p.line(5.0, 10.0, 5.0, 6.5)
    p.earth(5.0, 6.5, w=8.0, step=1.6)


@symbol("earth.pe", "Protective earth (PE)", C, STD, 10.0, 15.0, _pe_pin(5.0, 15.0), std_attrs(10.0, 15.0), tag_prefix="PE")
def pe(b):
    p = Pen(b)
    p.line(5.0, 15.0, 5.0, 11.5)
    p.earth(5.0, 11.5, w=8.0, step=1.6)
    p.text("PE", 5.0, 3.0, 2.5)


@symbol(
    "earth.electrode",
    "Earth electrode / earth pit",
    C,
    "IEC 60617 S00200 earth symbol on an electrode, IS 3043 practice",
    10.0,
    20.0,
    _pe_pin(5.0, 20.0),
    std_attrs(10.0, 20.0),
    tag_prefix="EP",
)
def electrode(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 16.5)
    p.earth(5.0, 16.5, w=8.0, step=1.6)
    p.poly([(4.0, 13.3), (6.0, 13.3), (6.0, 3.5), (5.0, 0.5), (4.0, 3.5)], close=True)


@symbol("earth.chassis", "Chassis / frame", C, "IEC 60617 frame or chassis / IS 12032", 10.0, 10.0, _pe_pin(5.0, 10.0), std_attrs(10.0, 10.0), tag_prefix="FR")
def chassis(b):
    p = Pen(b)
    p.line(5.0, 10.0, 5.0, 6.0)
    p.line(0.5, 6.0, 9.5, 6.0)
    for i in range(4):
        x = 1.5 + i * 2.5
        p.line(x, 6.0, x - 1.5, 3.0)


@symbol(
    "earth.ngr",
    "Neutral grounding resistor (NGR)",
    C,
    "UniCAD composite: resistor box + IEC 60617 S00200 earth",
    10.0,
    22.5,
    (Pin("neutral", 5.0, 22.5, Side.N, PE),),
    std_attrs(10.0, 22.5),
    tag_prefix="NGR",
)
def ngr(b):
    p = Pen(b)
    p.line(5.0, 22.5, 5.0, 17.5)
    p.rect(3.5, 7.5, 6.5, 17.5)
    p.line(5.0, 7.5, 5.0, 5.0)
    p.earth(5.0, 5.0, w=6.0, step=1.2)

from ..model import Category
from ..registry import symbol
from .kit import DC, Pen, pin_n, pin_s, std_attrs

C = Category.STORAGE
STD = "IEC 60617-6 primary/secondary cell, battery / IS 12032"


def _plates(p: Pen, cx: float, y_top: float, cells: int) -> float:
    y = y_top
    for _ in range(cells):
        p.line(cx - 4.0, y, cx + 4.0, y)
        p.fill_rect(cx - 2.0, y - 2.5 - 0.35, cx + 2.0, y - 2.5 + 0.35)
        y -= 5.0
    return y + 2.5


@symbol(
    "bat.battery",
    "Battery (cell stack)",
    C,
    STD,
    10.0,
    20.0,
    (pin_n(5.0, 20.0, "dc", DC),),
    std_attrs(10.0, 20.0),
)
def battery(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 17.0)
    _plates(p, 5.0, 17.0, 3)
    p.line(7.2, 18.8, 9.2, 18.8)
    p.line(8.2, 17.8, 8.2, 19.8)


@symbol(
    "bat.bess",
    "BESS container",
    C,
    "UniCAD composite (container + battery + PCS)",
    40.0,
    30.0,
    (pin_s(20.0, "ac"),),
    std_attrs(40.0, 30.0),
)
def bess(b):
    p = Pen(b)
    p.rect(0.0, 2.5, 40.0, 30.0)
    p.line(20.0, 2.5, 20.0, 0.0)
    _plates(p, 5.0, 22.5, 3)
    p.line(5.0, 9.5, 5.0, 8.0)
    p.line(5.0, 8.0, 14.0, 8.0)
    p.rect(14.0, 5.0, 26.0, 17.0)
    p.line(14.0, 5.0, 26.0, 17.0)
    p.equals(17.2, 14.3, 3.5)
    p.tilde(22.8, 7.7, 4.2)
    p.line(20.0, 5.0, 20.0, 2.5)
    p.text("BESS", 33.0, 16.0, 3.0)

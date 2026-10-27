from ..model import Category
from ..registry import symbol
from .kit import DC, Pen, pin_n, pin_s, std_attrs

C = Category.STORAGE
STD = "IEC 60617 S01342 battery of primary or secondary cells / IS 12032"


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
    tag_prefix="BAT",
)
def battery(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 17.0)
    _plates(p, 5.0, 17.0, 3)
    p.line(7.2, 18.8, 9.2, 18.8)
    p.line(8.2, 17.8, 8.2, 19.8)


@symbol(
    "bat.bess",
    "BESS battery container",
    C,
    "UniCAD composite (container + battery); the PCS is drawn as its own symbol",
    40.0,
    30.0,
    (pin_s(20.0, "dc", DC),),
    std_attrs(40.0, 30.0),
    tag_prefix="BESS",
)
def bess(b):
    p = Pen(b)
    p.rect(0.0, 2.5, 40.0, 30.0)
    bottom = _plates(p, 12.0, 25.0, 3) - 0.35
    p.line(12.0, bottom, 12.0, 6.0)
    p.line(12.0, 6.0, 20.0, 6.0)
    p.line(20.0, 6.0, 20.0, 0.0)
    p.line(14.2, 26.8, 16.2, 26.8)
    p.line(15.2, 25.8, 15.2, 27.8)
    p.text("BESS", 29.0, 16.0, 3.0)

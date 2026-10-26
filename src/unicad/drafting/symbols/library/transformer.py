import math

from ..model import Attr, Category, Pin, Side
from ..registry import symbol
from .kit import AC, ATTR_H, SIG, TAG_H, Pen, pin_n, pin_s, std_attrs

C = Category.TRANSFORMER
STD = "IEC 60617 S00841 transformer with two windings / IS 12032"
STD_3W = "IEC 60617 transformer with three windings / IS 12032"


@symbol(
    "tx.power_2w",
    "Two-winding power transformer",
    C,
    STD,
    15.0,
    22.5,
    (pin_n(7.5, 22.5), pin_s(7.5)),
    std_attrs(15.0, 22.5, ("VECTOR", "Dyn11")),
    tag_prefix="TX",
)
def power_2w(b):
    p = Pen(b)
    p.circle(7.5, 15.0, 7.5)
    p.circle(7.5, 7.5, 7.5)


@symbol(
    "tx.power_3w",
    "Three-winding power transformer",
    C,
    STD_3W,
    20.0,
    20.0,
    (pin_n(7.5, 20.0), pin_s(7.5), Pin("ter", 20.0, 10.0, Side.E, AC)),
    (
        Attr("TAG", 22.5, 18.0, TAG_H),
        Attr("RATING", 22.5, 14.0, ATTR_H),
        Attr("VECTOR", 22.5, 5.0, ATTR_H, "YNyn0d11"),
        Attr("DESC", 22.5, 1.5, ATTR_H),
    ),
    tag_prefix="TX",
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
    tag_prefix="ATX",
)
def aux(b):
    p = Pen(b)
    p.circle(5.0, 10.0, 5.0)
    p.circle(5.0, 5.0, 5.0)


@symbol(
    "tx.ct",
    "Current transformer (CT)",
    C,
    "IEC 60617 S00850 current transformer, general symbol / IS 12032",
    10.0,
    15.0,
    (pin_n(5.0, 15.0), pin_s(5.0), Pin("sec", 10.0, 7.5, Side.E, SIG)),
    (
        Attr("TAG", 12.5, 15.5, TAG_H),
        Attr("DESC", 12.5, 12.0, ATTR_H),
        Attr("RATING", 12.5, 3.0, ATTR_H),
    ),
    tag_prefix="CT",
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
    "IEC 60617 S00878 voltage transformer / IS 12032",
    10.0,
    12.5,
    (pin_n(5.0, 12.5, "pri"), pin_s(5.0, "sec", SIG)),
    std_attrs(10.0, 12.5),
    tag_prefix="PT",
)
def pt(b):
    p = Pen(b)
    p.circle(5.0, 8.75, 3.75)
    p.circle(5.0, 3.75, 3.75)

CT_CORE_PITCH = 5.0
CT_CORE_R = 2.5
CT_STEM = 7.5
CT_ATTR_X = 17.5


def _multi_core_ct(cores: int) -> None:
    height = CT_CORE_PITCH * cores + 2 * CT_STEM
    ys = [CT_STEM + CT_CORE_R + CT_CORE_PITCH * i for i in range(cores)]
    pins = (pin_n(5.0, height), pin_s(5.0)) + tuple(
        Pin(f"sec{i + 1}", 10.0, y, Side.E, SIG) for i, y in enumerate(ys)
    )
    attrs = (
        Attr("TAG", CT_ATTR_X, height - 1.5, TAG_H),
        Attr("DESC", CT_ATTR_X, height - 5.5, ATTR_H),
        *(Attr(f"RATIO{i + 1}", CT_ATTR_X, y, ATTR_H) for i, y in enumerate(ys)),
        Attr("RATING", CT_ATTR_X, 6.0, ATTR_H),
    )

    @symbol(
        f"tx.ct_{cores}core",
        f"Current transformer, {cores} cores",
        C,
        "IEC 60617 S00850 current transformer, one ring per core",
        10.0,
        height,
        pins,
        attrs,
        tag_prefix="CT",
    )
    def draw(b):
        p = Pen(b)
        p.line(5.0, height, 5.0, 0.0)
        for y in ys:
            p.circle(5.0, y, CT_CORE_R)
            p.dot(3.5, y + 1.3, 0.45)
            p.line(5.0 + CT_CORE_R, y, 10.0, y)


for _cores in (2, 3):
    _multi_core_ct(_cores)


IDT_R = 6.25
IDT_PITCH = 10.0
IDT_MARGIN_X = 7.5
IDT_HV_DY = 10.0
IDT_VECTOR = {2: "YNd11d11", 3: "YNd11d11d11", 4: "YNd11d11d11d11"}


def _idt(lv: int) -> None:
    width = 2 * IDT_MARGIN_X + IDT_PITCH * (lv - 1)
    lv_x = [IDT_MARGIN_X + IDT_PITCH * i for i in range(lv)]
    hv_x = IDT_MARGIN_X + IDT_PITCH * (lv - 1) / 2
    lv_y = IDT_R
    hv_y = lv_y + IDT_HV_DY
    height = hv_y + IDT_R
    pins = (pin_n(hv_x, height, "hv"),) + tuple(pin_s(x, f"lv{i + 1}") for i, x in enumerate(lv_x))

    @symbol(
        f"tx.idt_{lv}lv",
        f"Inverter-duty transformer, 1 HV + {lv} LV windings",
        C,
        "IEC 60617 S00841 transformer, extended to N LV windings",
        width,
        height,
        pins,
        std_attrs(width, height, ("VECTOR", IDT_VECTOR[lv])),
        tag_prefix="IDT",
    )
    def draw(b):
        p = Pen(b)
        p.circle(hv_x, hv_y, IDT_R)
        for a in (30.0, 150.0, 270.0):
            ar = math.radians(a)
            p.line(hv_x, hv_y, hv_x + 3.0 * math.cos(ar), hv_y + 3.0 * math.sin(ar))
        for x in lv_x:
            p.circle(x, lv_y, IDT_R)
            p.poly([(x - 2.8, lv_y - 1.8), (x + 2.8, lv_y - 1.8), (x, lv_y + 3.0)], close=True)


for _lv in (2, 3, 4):
    _idt(_lv)


@symbol(
    "tx.earthing",
    "Earthing transformer (zig-zag)",
    C,
    "UniCAD composite; zig-zag mark cf. IEC 60617 S00866",
    10.0,
    20.0,
    (pin_n(5.0, 20.0), pin_s(5.0, "neutral")),
    std_attrs(10.0, 20.0),
    tag_prefix="ET",
)
def earthing(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 15.0)
    p.circle(5.0, 10.0, 5.0)
    p.path((2.5, 12.5), (7.5, 12.5), (2.5, 7.5), (7.5, 7.5))
    p.line(5.0, 5.0, 5.0, 0.0)

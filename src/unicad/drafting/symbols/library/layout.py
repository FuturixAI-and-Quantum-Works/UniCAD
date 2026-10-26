import math

from ..model import Category
from ..registry import symbol
from .kit import Pen, std_attrs

C = Category.LAYOUT
STD = "UniCAD plan-view convention"


@symbol("layout.module", "PV module (plan)", C, STD, 5.0, 10.0, (), ())
def module_plan(b):
    p = Pen(b)
    p.rect(0.0, 0.0, 5.0, 10.0)
    for y in (2.5, 5.0, 7.5):
        p.line(0.0, y, 5.0, y)
    p.line(2.5, 0.0, 2.5, 10.0)


@symbol("layout.inverter", "Inverter (plan)", C, STD, 15.0, 10.0, (), std_attrs(15.0, 10.0))
def inverter_plan(b):
    p = Pen(b)
    p.rect(0.0, 0.0, 15.0, 10.0)
    p.line(0.0, 0.0, 15.0, 10.0)
    p.equals(4.0, 7.2, 3.6)
    p.tilde(11.0, 2.8, 4.4)


@symbol("layout.earth_pit", "Earth pit (plan)", C, STD, 7.5, 7.5, (), std_attrs(7.5, 7.5))
def earth_pit_plan(b):
    p = Pen(b)
    p.circle(3.75, 3.75, 3.75)
    p.line(3.75, 0.0, 3.75, 7.5)
    p.line(0.0, 3.75, 7.5, 3.75)
    p.circle(3.75, 3.75, 1.5)


@symbol("layout.ese_la", "ESE lightning arrester (plan)", C, STD, 10.0, 10.0, (), std_attrs(10.0, 10.0, ("RADIUS", "")))
def ese_la_plan(b):
    p = Pen(b)
    p.circle(5.0, 5.0, 2.0)
    p.dot(5.0, 5.0, 0.8)
    for i in range(8):
        a = math.radians(45 * i + 22.5)
        p.line(5.0 + 2.0 * math.cos(a), 5.0 + 2.0 * math.sin(a), 5.0 + 4.8 * math.cos(a), 5.0 + 4.8 * math.sin(a))


@symbol("layout.transformer", "Transformer (plan)", C, STD, 15.0, 10.0, (), std_attrs(15.0, 10.0))
def transformer_plan(b):
    p = Pen(b)
    p.rect(0.0, 0.0, 15.0, 10.0)
    p.circle(5.75, 5.0, 3.0)
    p.circle(9.25, 5.0, 3.0)


@symbol("layout.scb", "String combiner box (plan)", C, STD, 10.0, 7.5, (), std_attrs(10.0, 7.5))
def scb_plan(b):
    p = Pen(b)
    p.rect(0.0, 0.0, 10.0, 7.5)
    p.text("SCB", 5.0, 3.75, 2.2)

import math

from ..model import Category, Pin, Side
from ..registry import symbol
from .kit import SIG, Pen, pin_n, pin_s, std_attrs

C = Category.INSTRUMENT
STD = "IEC 60617-8 measuring instrument / IS 12032"


def _meter_pins(cx: float, h: float):
    return (pin_n(cx, h, "in"), pin_s(cx, "out"))


@symbol("inst.kwh", "Energy meter (kWh)", C, STD, 10.0, 20.0, _meter_pins(5.0, 20.0), std_attrs(10.0, 20.0))
def kwh(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 15.0)
    p.circle(5.0, 10.0, 5.0)
    p.line(5.0, 5.0, 5.0, 0.0)
    p.text("kWh", 5.0, 10.0, 2.2)


@symbol("inst.net_meter", "Bidirectional net meter (import / export)", C, STD, 15.0, 22.5, _meter_pins(7.5, 22.5), std_attrs(15.0, 22.5))
def net_meter(b):
    p = Pen(b)
    p.line(7.5, 22.5, 7.5, 17.5)
    p.circle(7.5, 11.25, 6.25)
    p.line(7.5, 5.0, 7.5, 0.0)
    p.arrow(3.8, 13.9, 11.2, 13.9, 1.8, 1.1)
    p.arrow(11.2, 15.6, 3.8, 15.6, 1.8, 1.1)
    p.text("kWh", 7.5, 9.0, 2.2)


@symbol("inst.abt", "ABT / TVM meter", C, STD, 20.0, 22.5, _meter_pins(10.0, 22.5), std_attrs(20.0, 22.5))
def abt(b):
    p = Pen(b)
    p.line(10.0, 22.5, 10.0, 17.5)
    p.rect(0.0, 5.0, 20.0, 17.5)
    p.line(10.0, 5.0, 10.0, 0.0)
    p.text("ABT", 10.0, 11.25, 3.5)


@symbol(
    "inst.ammeter",
    "Ammeter",
    C,
    STD,
    10.0,
    15.0,
    (Pin("in", 5.0, 15.0, Side.N, SIG),),
    std_attrs(10.0, 15.0),
)
def ammeter(b):
    p = Pen(b)
    p.line(5.0, 15.0, 5.0, 10.0)
    p.circle(5.0, 5.0, 5.0)
    p.text("A", 5.0, 5.0, 4.0)


@symbol(
    "inst.voltmeter",
    "Voltmeter",
    C,
    STD,
    10.0,
    15.0,
    (Pin("in", 5.0, 15.0, Side.N, SIG),),
    std_attrs(10.0, 15.0),
)
def voltmeter(b):
    p = Pen(b)
    p.line(5.0, 15.0, 5.0, 10.0)
    p.circle(5.0, 5.0, 5.0)
    p.text("V", 5.0, 5.0, 4.0)


@symbol(
    "inst.mfm",
    "Multifunction meter (MFM)",
    C,
    STD,
    10.0,
    15.0,
    (Pin("in", 5.0, 15.0, Side.N, SIG),),
    std_attrs(10.0, 15.0),
)
def mfm(b):
    p = Pen(b)
    p.line(5.0, 15.0, 5.0, 10.0)
    p.circle(5.0, 5.0, 5.0)
    p.text("MFM", 5.0, 5.0, 2.2)


@symbol(
    "inst.datalogger",
    "Data logger / SCADA box",
    C,
    "UniCAD composite (rectangle, IS 12032 style)",
    20.0,
    15.0,
    (Pin("in", 10.0, 15.0, Side.N, SIG), Pin("out", 10.0, 0.0, Side.S, SIG)),
    std_attrs(20.0, 15.0),
)
def datalogger(b):
    p = Pen(b)
    p.line(10.0, 15.0, 10.0, 12.5)
    p.rect(0.0, 2.5, 20.0, 12.5)
    p.line(10.0, 2.5, 10.0, 0.0)
    p.text("DL", 10.0, 7.5, 3.5)


@symbol(
    "inst.weather_station",
    "Weather station (irradiance / temperature / wind)",
    C,
    "UniCAD composite (sensor circle, IS 12032 style)",
    15.0,
    17.5,
    (Pin("out", 7.5, 0.0, Side.S, SIG),),
    std_attrs(15.0, 17.5),
)
def weather_station(b):
    p = Pen(b)
    p.circle(7.5, 10.0, 7.5)
    p.line(7.5, 2.5, 7.5, 0.0)
    p.circle(7.5, 12.0, 1.6)
    for i in range(8):
        a = math.radians(45 * i)
        p.line(7.5 + 2.3 * math.cos(a), 12.0 + 2.3 * math.sin(a), 7.5 + 3.4 * math.cos(a), 12.0 + 3.4 * math.sin(a))
    p.text("WS", 7.5, 5.8, 2.2)

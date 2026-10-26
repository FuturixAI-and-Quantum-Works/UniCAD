import math

from ..model import Category, Pin, Side
from ..registry import symbol
from .kit import AC, SIG, Pen, pin_n, pin_s, std_attrs

C = Category.INSTRUMENT
STD = "IEC 60617 S00910 indicating instrument, general symbol / IS 12032"


def _meter_pins(cx: float, h: float):
    return (pin_n(cx, h, "in"), pin_s(cx, "out"))


@symbol("inst.kwh", "Energy meter (kWh)", C, "IEC 60617 S00933 watt-hour meter / IS 12032", 10.0, 20.0, _meter_pins(5.0, 20.0), std_attrs(10.0, 20.0), tag_prefix="EM")
def kwh(b):
    p = Pen(b)
    p.line(5.0, 20.0, 5.0, 15.0)
    p.circle(5.0, 10.0, 5.0)
    p.line(5.0, 5.0, 5.0, 0.0)
    p.text("kWh", 5.0, 10.0, 2.2)


@symbol("inst.net_meter", "Bidirectional net meter (import / export)", C, "IEC 60617 S00937 watt-hour meter, both directions / IS 12032", 15.0, 22.5, _meter_pins(7.5, 22.5), std_attrs(15.0, 22.5), tag_prefix="NM")
def net_meter(b):
    p = Pen(b)
    p.line(7.5, 22.5, 7.5, 17.5)
    p.circle(7.5, 11.25, 6.25)
    p.line(7.5, 5.0, 7.5, 0.0)
    p.arrow(3.8, 13.9, 11.2, 13.9, 1.8, 1.1)
    p.arrow(11.2, 15.6, 3.8, 15.6, 1.8, 1.1)
    p.text("kWh", 7.5, 9.0, 2.2)


@symbol("inst.abt", "ABT / TVM meter", C, "IEC 60617 S00933 watt-hour meter; ABT / TVM box", 20.0, 22.5, _meter_pins(10.0, 22.5), std_attrs(20.0, 22.5), tag_prefix="ABT")
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
    tag_prefix="AM",
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
    "IEC 60617 S00913 voltmeter / IS 12032",
    10.0,
    15.0,
    (Pin("in", 5.0, 15.0, Side.N, SIG),),
    std_attrs(10.0, 15.0),
    tag_prefix="VM",
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
    tag_prefix="MFM",
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
    tag_prefix="DL",
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
    tag_prefix="WS",
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


LAMP_R = 2.5


@symbol(
    "inst.lamp_ryb",
    "Phase indication lamps R / Y / B",
    C,
    "IEC 60617 S00965 lamp, general symbol; R-Y-B letters",
    25.0,
    10.0,
    (Pin("in", 0.0, 5.0, Side.W, SIG),),
    std_attrs(25.0, 10.0),
    tag_prefix="IL",
)
def lamp_ryb(b):
    p = Pen(b)
    for x0, x1 in ((0.0, 2.5), (7.5, 10.0), (15.0, 17.5)):
        p.line(x0, 5.0, x1, 5.0)
    d = LAMP_R * math.cos(math.radians(45))
    for x, letter in zip((5.0, 12.5, 20.0), "RYB"):
        p.circle(x, 5.0, LAMP_R)
        p.line(x - d, 5.0 - d, x + d, 5.0 + d)
        p.line(x - d, 5.0 + d, x + d, 5.0 - d)
        p.text(letter, x, 9.0, 2.0)


@symbol(
    "inst.annunciator",
    "Alarm annunciator (12 window)",
    C,
    "UniCAD composite; cf. IEC 60617 S00967 annunciator element",
    20.0,
    15.0,
    (Pin("in", 0.0, 7.5, Side.W, SIG),),
    std_attrs(20.0, 15.0),
    tag_prefix="ANN",
)
def annunciator(b):
    p = Pen(b)
    p.rect(2.5, 0.0, 20.0, 15.0)
    p.line(0.0, 7.5, 2.5, 7.5)
    for i in range(1, 4):
        x = 2.5 + i * 17.5 / 4
        p.line(x, 0.0, x, 15.0)
    for y in (5.0, 10.0):
        p.line(2.5, y, 20.0, y)


@symbol(
    "inst.metering_cubicle",
    "CT / PT metering cubicle (TVM / ABT meter)",
    C,
    "UniCAD composite: IEC 60617 S00850 CT + S00878 VT + S00933 meter",
    35.0,
    45.0,
    (Pin("in", 10.0, 45.0, Side.N, AC), Pin("out", 10.0, 0.0, Side.S, AC)),
    std_attrs(35.0, 45.0),
    tag_prefix="MTC",
)
def metering_cubicle(b):
    p = Pen(b)
    p.dashed_rect(0.0, 2.5, 35.0, 45.0)
    p.line(10.0, 45.0, 10.0, 0.0)
    p.circle(10.0, 34.0, 3.0)
    p.dot(8.2, 36.2, 0.5)
    p.path((13.0, 34.0), (30.0, 34.0), (30.0, 12.0))
    p.dot(10.0, 24.0, 0.7)
    p.line(10.0, 24.0, 25.0, 24.0)
    p.circle(25.0, 21.5, 2.5)
    p.circle(25.0, 16.5, 2.5)
    p.line(25.0, 14.0, 25.0, 12.0)
    p.rect(14.0, 5.0, 32.0, 12.0)
    p.text("TVM/ABT", 23.0, 8.5, 2.0)


@symbol(
    "inst.wms",
    "Weather monitoring station (WMS)",
    C,
    "UniCAD composite (sensor circles G, T, W in an enclosure)",
    30.0,
    20.0,
    (Pin("out", 15.0, 0.0, Side.S, SIG),),
    std_attrs(30.0, 20.0),
    tag_prefix="WMS",
)
def wms(b):
    p = Pen(b)
    p.rect(0.0, 2.5, 30.0, 20.0)
    p.line(15.0, 2.5, 15.0, 0.0)
    for x, letter in zip((5.5, 15.0, 24.5), "GTW"):
        p.circle(x, 8.5, 3.5)
        p.text(letter, x, 8.5, 2.5)
    p.text("WMS", 15.0, 16.5, 2.5)


@symbol(
    "inst.scada_box",
    "SCADA / RTU panel",
    C,
    "UniCAD composite (rectangle, IS 12032 style)",
    30.0,
    20.0,
    (
        Pin("in", 15.0, 20.0, Side.N, SIG),
        Pin("out", 15.0, 0.0, Side.S, SIG),
        Pin("comm", 0.0, 10.0, Side.W, SIG),
    ),
    std_attrs(30.0, 20.0),
    tag_prefix="SCADA",
)
def scada_box(b):
    p = Pen(b)
    p.line(15.0, 20.0, 15.0, 17.5)
    p.rect(2.5, 2.5, 27.5, 17.5)
    p.line(15.0, 2.5, 15.0, 0.0)
    p.line(0.0, 10.0, 2.5, 10.0)
    p.text("SCADA", 15.0, 10.0, 3.0)


@symbol(
    "inst.cctv",
    "CCTV camera",
    C,
    "UniCAD composite (camera body and lens)",
    20.0,
    15.0,
    (Pin("in", 0.0, 7.5, Side.W, SIG),),
    std_attrs(20.0, 15.0),
    tag_prefix="CAM",
)
def cctv(b):
    p = Pen(b)
    p.line(0.0, 7.5, 2.5, 7.5)
    p.rect(2.5, 2.5, 13.0, 12.5)
    p.poly([(13.0, 5.0), (20.0, 2.5), (20.0, 12.5), (13.0, 10.0)], close=True)
    p.text("CCTV", 7.75, 7.5, 2.0)

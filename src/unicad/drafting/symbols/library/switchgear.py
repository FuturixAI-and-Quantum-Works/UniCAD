from ..model import Category, Pin, Side
from ..registry import symbol
from .kit import AC, Pen, blade_box, inline_pins, pin_s, std_attrs

C = Category.SWITCHGEAR
W, H = 10.0, 20.0
CX = W / 2
DX = 3.2


def _inline(key: str, title: str, standard: str, w: float = W, h: float = H):
    return symbol(key, title, C, standard, w, h, inline_pins(w / 2, h, AC), std_attrs(w, h))


@_inline("sw.mcb", "Miniature circuit breaker (MCB)", "IEC 60617 S00287 + thermal release")
def mcb(b):
    p = Pen(b)
    yh, yc = p.switch(CX, 0, H, "cross")
    blade_box(p, CX, yh, yc, DX, 0.5, 3.2, 0.8)


@_inline("sw.mccb", "Moulded-case circuit breaker (MCCB)", "IEC 60617 S00287 + thermal-magnetic release")
def mccb(b):
    p = Pen(b)
    yh, yc = p.switch(CX, 0, H, "cross")
    blade_box(p, CX, yh, yc, DX, 0.32, 2.8, 0.8)
    blade_box(p, CX, yh, yc, DX, 0.72, 2.8, 0.8, solid=True)


@symbol(
    "sw.acb",
    "Air circuit breaker (ACB)",
    C,
    "IEC 60617 S00287 + electronic release",
    W,
    25.0,
    inline_pins(CX, 25.0, AC),
    std_attrs(W, 25.0),
)
def acb(b):
    p = Pen(b)
    yh, yc = p.switch(CX, 0, 25.0, "cross")
    blade_box(p, CX, yh, yc, DX, 0.32, 3.2, 0.9)
    blade_box(p, CX, yh, yc, DX, 0.74, 3.2, 0.9, solid=True)
    p.dot(CX, yh, 0.7)


@symbol(
    "sw.rccb",
    "Residual current circuit breaker (RCCB / RCD)",
    C,
    "IEC 60617 S00287 + residual-current release",
    W,
    25.0,
    inline_pins(CX, 25.0, AC),
    std_attrs(W, 25.0),
)
def rccb(b):
    p = Pen(b)
    p.switch(CX, 0, 25.0, "cross")
    p.circle(CX, 4.0, 2.0)
    p.dashed(3.4, 5.9, 3.2, 14.0, dash=2.2, gap=1.4)


@_inline("sw.hrc_fuse", "HRC fuse", "IEC 60617 S00362")
def hrc_fuse(b):
    p = Pen(b)
    p.line(CX, 0, CX, H)
    p.rect(CX - 1.5, 5.5, CX + 1.5, 14.5)


@_inline("sw.switch_fuse", "Switch-fuse unit (fuse-switch disconnector)", "IEC 60617 S00288 + S00362")
def switch_fuse(b):
    p = Pen(b)
    yh, yc = p.switch(CX, 0, H, "bar")
    blade_box(p, CX, yh, yc, DX, 0.5, 5.4, 1.3)


@_inline("sw.isolator", "Isolator / disconnector", "IEC 60617 S00288")
def isolator(b):
    Pen(b).switch(CX, 0, H, "bar")


@symbol(
    "sw.isolator_es",
    "Isolator with earth switch",
    C,
    "IEC 60617 S00288 + S00290 (earthing)",
    20.0,
    32.5,
    inline_pins(10.0, 32.5, AC),
    std_attrs(20.0, 32.5),
)
def isolator_es(b):
    p = Pen(b)
    cx, ex = 10.0, 16.0
    p.line(cx, 0, cx, 15.0)
    p.switch(cx, 15.0, 32.5, "bar")
    p.dot(cx, 12.0, 0.7)
    p.line(cx, 12.0, ex, 12.0)
    p.line(ex, 12.0, ex, 9.5)
    p.line(ex - 1.5, 9.5, ex + 1.5, 9.5)
    p.line(ex, 5.0, ex, 3.5)
    p.line(ex, 5.0, ex - 1.5, 9.9)
    p.earth(ex, 3.5, w=5.0, step=1.0)


@_inline("sw.lbs", "Load-break switch (switch-disconnector)", "IEC 60617 S00290")
def lbs(b):
    Pen(b).switch(CX, 0, H, "square")


@_inline("sw.contactor", "Contactor", "IEC 60617 S00284")
def contactor(b):
    Pen(b).switch(CX, 0, H, "arc")


@symbol("sw.vcb", "Vacuum circuit breaker (VCB)", C, "IEC 60617 S00287 + vacuum envelope", 15.0, 25.0, inline_pins(7.5, 25.0, AC), std_attrs(15.0, 25.0))
def vcb(b):
    p = Pen(b)
    p.switch(7.5, 0, 25.0, "cross")
    p.circle(7.5, 12.5, 7.0)


@symbol("sw.sf6", "SF6 circuit breaker", C, "IEC 60617 S00287 + gas housing", 15.0, 25.0, inline_pins(7.5, 25.0, AC), std_attrs(15.0, 25.0))
def sf6(b):
    p = Pen(b)
    p.switch(7.5, 0, 25.0, "cross")
    p.rect(0.5, 4.5, 14.5, 20.5)
    p.text("SF6", 11.2, 9.0, 1.8)


@symbol("sw.draw_out", "Draw-out (withdrawable) circuit breaker", C, "IEC 60617 S00287 + withdrawable contacts", 10.0, 25.0, inline_pins(5.0, 25.0, AC), std_attrs(10.0, 25.0))
def draw_out(b):
    p = Pen(b)
    p.switch(5.0, 0, 25.0, "cross")
    for y in (3.5, 21.5):
        p.circle(5.0, y, 1.2)


@symbol(
    "sw.changeover",
    "Changeover switch (ATS / MTS)",
    C,
    "IEC 60617 S00289",
    15.0,
    20.0,
    (
        Pin("in1", 2.5, 20.0, Side.N, AC),
        Pin("in2", 12.5, 20.0, Side.N, AC),
        pin_s(7.5, "out"),
    ),
    std_attrs(15.0, 20.0),
)
def changeover(b):
    p = Pen(b)
    p.line(7.5, 0, 7.5, 5.0)
    p.line(7.5, 5.0, 2.5, 14.0)
    p.line(2.5, 14.0, 2.5, 20.0)
    p.line(12.5, 14.0, 12.5, 20.0)
    p.line(11.25, 14.0, 13.75, 14.0)
    p.dot(7.5, 5.0, 0.7)


@symbol(
    "sw.rmu",
    "Ring main unit (2 ring switches + tee-off)",
    C,
    "IEC 60617 S00290 / S00362",
    40.0,
    50.0,
    (
        Pin("ring1", 5.0, 50.0, Side.N, AC),
        Pin("ring2", 35.0, 50.0, Side.N, AC),
        pin_s(20.0, "tee"),
    ),
    std_attrs(40.0, 50.0),
)
def rmu(b):
    p = Pen(b)
    p.dashed_rect(0.0, 2.5, 40.0, 50.0)
    p.switch(5.0, 24.0, 50.0, "square")
    p.switch(35.0, 24.0, 50.0, "square")
    yh, yc = p.switch(20.0, 0, 24.0, "bar")
    blade_box(p, 20.0, yh, yc, 3.2, 0.5, 4.2, 1.1)
    p.fill_rect(5.0, 23.6, 35.0, 24.4)
    p.text("RMU", 20.0, 41.0, 3.0)

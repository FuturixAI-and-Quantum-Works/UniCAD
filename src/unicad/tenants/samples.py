"""The synthetic "Acme Solar EPC" company drawing used to prove onboarding: `uv run python -m unicad.tenants.samples <dir>`.

One DXF holds the company's title blocks for A3 and A1 (blocks `ACME_TB_A3`, `ACME_TB_A1`, with `{PLACEHOLDER}` texts), its logo block,
two company symbols and its layer standard; `answers.yaml` is the short questionnaire that goes with it. The title block follows the
fields of an Indian EPC sheet (references/samples/CONVENTIONS.md section 1) but is laid out differently from UniCAD's own.
"""

import sys
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import BlockLayout

from unicad.drafting.dxfrules import prepare

WIDTH_T = 46.0
LAYERS = {
    "ACME-FRAME": (7, 50),
    "ACME-TITLE": (7, 25),
    "ACME-TEXT": (7, 25),
    "ACME-TB": (7, 25),
    "ACME-TB-OUT": (7, 50),
    "ACME-TB-TXT": (7, 25),
    "ACME-LOGO": (30, 35),
    "ACME-SYM": (3, 25),
    "ACME-SYM-TXT": (7, 18),
}
ISSUED = ("PROPOSAL", "INFORMATION", "APPROVAL", "CONSTRUCTION", "AS_BUILT")

ANSWERS = """\
id: acme
name: Acme Solar EPC Pvt. Ltd.
short_code: ACME
address: "4th Floor, Acme Towers, Baner Road, Pune - 411 045, Maharashtra. Tel: +91 20 5550 0188. www.acme-solar.example"
confidentiality_note: "THIS DRAWING IS THE PROPERTY OF ACME SOLAR EPC PVT. LTD. IT SHALL NOT BE COPIED OR USED WITHOUT WRITTEN PERMISSION."
date_format: "%d.%m.%Y"
default_paper: A3
roles: {drawn: DRN, checked: CHKD, approved: APPD}
numbering:
  pattern: "{company}/{client}/{project}/{discipline}/{seq:03d}"
  disciplines: {electrical: EL, civil: CV, substation: SS}
  per_discipline: true
  start: {civil: 101}
logo_block: ACME_LOGO
layers:
  frame: ACME-FRAME
  title: ACME-TITLE
  text: ACME-TEXT
layer_map:
  G-SYMBOL: ACME-SYM
  G-SYMBOL-TXT: ACME-SYM-TXT
symbols:
  pv.module: {block: ACME_PV_MODULE, pins: named}
  inst.kwh: {block: ACME_KWH_METER, pins: fit}
thresholds:
  dc_ac_band:
    value: 0.15
    source: "Acme design standard DS-04 clause 3.2 permits a DC/AC ratio within +/-15 % of the project target"
  isc_factor:
    value: 1.25
    source: "Acme design standard DS-04 clause 4.1: worst-case string current is 1.25 x Isc"
revision_marks: {style: cloud, stamp_text: REVISED}
"""


class _Draw:
    def __init__(self, block: BlockLayout, t: float):
        self.b, self.t = block, t

    def line(self, p, q, heavy: bool = False) -> None:
        self.b.add_line(p, q, dxfattribs={"layer": "ACME-TB-OUT" if heavy else "ACME-TB"})

    def rect(self, x0, y0, x1, y1, heavy: bool = False) -> None:
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        for i in range(4):
            self.line(pts[i], pts[(i + 1) % 4], heavy)

    def text(self, s: str, x: float, y: float, h: float, align: TextEntityAlignment | None = None) -> None:
        e = self.b.add_text(s, height=h, dxfattribs={"layer": "ACME-TB-TXT", "style": "Standard"})
        if align is None:
            e.set_placement((x, y))
        else:
            e.set_placement((x, y), align=align)

    def label(self, s: str, x0: float, top: float) -> None:
        self.text(s, x0 + 0.5 * self.t, top - 0.25 * self.t - 0.62 * self.t, 0.62 * self.t)

    def field(self, name: str, x0: float, y0: float, h: float) -> None:
        self.text("{" + name + "}", x0 + 0.5 * self.t, y0 + 0.7 * self.t, h * self.t)


def title_block(doc: Drawing, name: str, t: float) -> None:
    b = doc.blocks.new(name)
    d = _Draw(b, t)
    w = WIDTH_T * t
    y = 0.0

    def row(height: float) -> tuple[float, float]:
        nonlocal y
        lo, hi = y, y + height * t
        y = hi
        if lo > 0:
            d.line((0, lo), (w, lo))
        return lo, hi

    lo, hi = row(3.2)
    d.field("CONFIDENTIALITY", 0, lo, 0.55)
    lo, hi = row(8.0)
    d.line((8 * t, lo), (8 * t, hi))
    d.text("{LOGO}", 4 * t, (lo + hi) / 2, 0.8 * t, TextEntityAlignment.MIDDLE_CENTER)
    d.line((8 * t, hi - 3.4 * t), (w, hi - 3.4 * t))
    d.text("{COMPANY}", 8.5 * t, hi - 3.4 * t + 0.9 * t, 1.2 * t)
    d.text("{ADDRESS}", 8.5 * t, hi - 3.4 * t - 1.1 * t, 0.65 * t)
    lo, hi = row(5.0)
    d.label("DRAWING ISSUED FOR", 0, hi)
    col = w / len(ISSUED)
    for i, opt in enumerate(ISSUED):
        x = i * col
        if i:
            d.line((x, lo), (x, hi - 1.2 * t))
        box = 1.2 * t
        bx, by = x + 0.5 * t, lo + 0.9 * t
        d.rect(bx, by, bx + box, by + box)
        d.text("{ISSUED_" + opt + "}", bx + box / 2, by + box / 2, 0.5 * t, TextEntityAlignment.MIDDLE_CENTER)
        d.text(opt.replace("_", " "), bx + box + 0.5 * t, by + 0.3 * t, 0.6 * t)
    lo, hi = row(3.6)
    weights = [1.25, 1.1, 0.9, 0.8]
    x = 0.0
    for i, (label, field, wt) in enumerate(zip(("SCALE", "SHEET NO.", "PAPER SIZE", "REV"), ("SCALE", "SHEET", "PAPER", "REV"), weights, strict=True)):
        if i:
            d.line((x, lo), (x, hi))
        d.label(label, x, hi)
        d.field(field, x, lo, 1.0)
        x += w * wt / sum(weights)
    lo, hi = row(3.6)
    for i, name_ in enumerate(("DRAWN", "CHECKED", "APPROVED", "DATE")):
        x = i * w / 4
        if i:
            d.line((x, lo), (x, hi))
        d.label(name_, x, hi)
        d.field(name_, x, lo, 1.0)
    lo, hi = row(4.4)
    d.label("DRAWING NO.", 0, hi)
    d.field("DRAWING_NO", 0, lo, 1.3)
    lo, hi = row(6.4)
    d.label("DRAWING TITLE", 0, hi)
    d.field("TITLE", 0, lo, 1.1)
    lo, hi = row(5.4)
    d.line((w / 2, lo), (w / 2, hi))
    d.label("LOCATION", 0, hi)
    d.field("LOCATION", 0, lo, 1.0)
    d.label("LOA / PO NO.", w / 2, hi)
    d.field("LOA_NO", w / 2, lo, 1.0)
    lo, hi = row(6.4)
    d.label("PROJECT TITLE", 0, hi)
    d.field("PROJECT_TITLE", 0, lo, 1.0)
    lo, hi = row(3.6)
    d.line((w / 2, lo), (w / 2, hi))
    d.label("OWNER", 0, hi)
    d.field("OWNER", 0, lo, 1.0)
    d.label("CONSULTANT", w / 2, hi)
    d.field("CONSULTANT", w / 2, lo, 1.0)
    d.rect(0, 0, w, y, heavy=True)


def logo_block(doc: Drawing) -> None:
    b = doc.blocks.new("ACME_LOGO")
    attrs = {"layer": "ACME-LOGO"}
    b.add_circle((10, 10), 9, dxfattribs=attrs)
    b.add_circle((10, 10), 7.6, dxfattribs=attrs)
    b.add_solid([(10, 16.5), (3.5, 4.5), (6.5, 4.5), (10, 11)], dxfattribs=attrs)
    b.add_solid([(10, 16.5), (16.5, 4.5), (13.5, 4.5), (10, 11)], dxfattribs=attrs)
    b.add_line((7, 7.5), (13, 7.5), dxfattribs=attrs)
    b.add_line((8, 9.2), (12, 9.2), dxfattribs=attrs)


def pv_module_block(doc: Drawing) -> None:
    b = doc.blocks.new("ACME_PV_MODULE")
    a = {"layer": "ACME-SYM"}
    b.add_line((0, 11), (0, 9), dxfattribs=a)
    b.add_line((0, -9), (0, -11), dxfattribs=a)
    b.add_lwpolyline([(-6, -9), (6, -9), (6, 9), (-6, 9)], close=True, dxfattribs=a)
    for x in (-2, 2):
        b.add_line((x, -9), (x, 9), dxfattribs=a)
    for y in (-3, 3):
        b.add_line((-6, y), (6, y), dxfattribs=a)
    b.add_point((0, 11), dxfattribs={"layer": "PIN_dc+"})
    b.add_point((0, -11), dxfattribs={"layer": "PIN_dc-"})


def kwh_block(doc: Drawing) -> None:
    b = doc.blocks.new("ACME_KWH_METER")
    a = {"layer": "ACME-SYM"}
    b.add_line((6, 24), (6, 18), dxfattribs=a)
    b.add_circle((6, 12), 6, dxfattribs=a)
    b.add_line((6, 6), (6, 0), dxfattribs=a)
    b.add_text("Wh", height=3.0, dxfattribs={"layer": "ACME-SYM", "style": "Standard"}).set_placement((6, 12), align=TextEntityAlignment.MIDDLE_CENTER)


def company_drawing() -> Drawing:
    doc = ezdxf.new("R2018", setup=["linetypes"])
    prepare(doc)
    for name, (colour, lw) in LAYERS.items():
        doc.layers.add(name, color=colour, lineweight=lw)
    for pin in ("PIN_dc+", "PIN_dc-"):
        doc.layers.add(pin, color=2, lineweight=13)
    title_block(doc, "ACME_TB_A3", 2.5)
    title_block(doc, "ACME_TB_A1", 3.5)
    logo_block(doc)
    pv_module_block(doc)
    kwh_block(doc)
    msp = doc.modelspace()
    msp.add_blockref("ACME_TB_A3", (0, 0))
    msp.add_blockref("ACME_TB_A1", (200, 0))
    msp.add_blockref("ACME_LOGO", (0, 200))
    msp.add_blockref("ACME_PV_MODULE", (60, 220))
    msp.add_blockref("ACME_KWH_METER", (90, 200))
    return doc


def write_sample(out: Path) -> tuple[Path, Path]:
    from unicad.drafting.sld.sheet import dxf_text

    out.mkdir(parents=True, exist_ok=True)
    dxf = out / "acme-company.dxf"
    dxf.write_text(dxf_text(company_drawing()), encoding="utf-8")
    answers = out / "answers.yaml"
    answers.write_text(ANSWERS, encoding="utf-8")
    return dxf, answers


if __name__ == "__main__":
    for p in write_sample(Path(sys.argv[1] if len(sys.argv) > 1 else "profiles/samples/acme")):
        print(p)

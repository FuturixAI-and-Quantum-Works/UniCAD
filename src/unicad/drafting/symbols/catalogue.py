from collections import defaultdict
from collections.abc import Iterable

import ezdxf
from ezdxf import units
from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment

from . import library  # noqa: F401
from .install import insert, install
from .library.kit import TEXT_STYLE
from .model import Category, Side, SymbolDef
from .registry import REGISTRY

CELL_W = 80.0
COLUMNS = 10
MARGIN = 10.0
FOOTER = 12.0
HEADER = 14.0

CATEGORY_TITLES = {
    Category.PV: "PV generator",
    Category.DC_PROTECTION: "DC protection",
    Category.CONVERSION: "Conversion",
    Category.STORAGE: "Storage",
    Category.SWITCHGEAR: "Switchgear",
    Category.PROTECTION: "Protection",
    Category.TRANSFORMER: "Transformers",
    Category.INSTRUMENT: "Instruments and metering",
    Category.GRID: "Grid, generation and load",
    Category.EARTHING: "Earthing",
    Category.ANNOTATION: "Annotation",
    Category.LAYOUT: "Layout (plan view)",
}

_RATING = {
    "pv.module": "550 Wp",
    "pv.string": "18 x 550 Wp",
    "pv.array": "180 kWp",
    "dc.fuse": "15 A 1500 Vdc",
    "dc.isolator": "63 A 1500 Vdc",
    "dc.spd": "Type 2 1500 Vdc",
    "dc.mcb": "32 A 1000 Vdc",
    "dc.diode": "20 A 1500 V",
    "dc.scb": "16 in 1 out",
    "inv.string": "100 kW 415 Vac",
    "inv.central": "2.5 MW 800 Vac",
    "inv.micro": "1.2 kW 230 Vac",
    "inv.pcs": "500 kW 415 Vac",
    "inv.optimiser": "600 W",
    "bat.battery": "51.2 V 280 Ah",
    "bat.bess": "1 MW / 2 MWh",
    "sw.mcb": "C 32 A 10 kA",
    "sw.mccb": "250 A 36 kA",
    "sw.acb": "1600 A 50 kA",
    "sw.rccb": "63 A 30 mA",
    "sw.hrc_fuse": "160 A gG",
    "sw.switch_fuse": "250 A TPN",
    "sw.isolator": "400 A",
    "sw.isolator_es": "630 A 33 kV",
    "sw.lbs": "630 A 33 kV",
    "sw.contactor": "100 A AC-3",
    "sw.vcb": "630 A 25 kA",
    "sw.sf6": "1250 A 31.5 kA",
    "sw.draw_out": "1250 A 25 kA",
    "sw.changeover": "400 A",
    "sw.rmu": "3-way 630 A 33 kV",
    "prot.spd_ac": "Type 2 40 kA",
    "prot.lightning_arrester": "30 kV 10 kA",
    "prot.relay": "Aux 110 Vdc",
    "tx.power_2w": "2.5 MVA 33/0.8 kV",
    "tx.power_3w": "5 MVA 33/11/0.8 kV",
    "tx.aux": "50 kVA 415/240 V",
    "tx.ct": "400/5 A 5P10",
    "tx.pt": "33 kV/110 V",
    "inst.kwh": "CT 400/5 A",
    "inst.net_meter": "Class 0.5S",
    "inst.abt": "Class 0.2S",
    "inst.ammeter": "0-400 A",
    "inst.voltmeter": "0-500 V",
    "inst.mfm": "RS485",
    "inst.datalogger": "Modbus TCP",
    "inst.weather_station": "GHI / Tmod",
    "grid.utility": "33 kV 50 Hz",
    "grid.oh_line": "33 kV",
    "grid.cable_end": "3C x 240 mm2",
    "grid.dg": "250 kVA",
    "grid.load": "120 kW",
    "earth.earth": "< 1 ohm",
    "earth.pe": "PE",
    "earth.electrode": "< 1 ohm",
    "earth.chassis": "Frame",
    "ann.cable_tag": "3C x 300 sqmm Al",
    "layout.inverter": "100 kW",
    "layout.earth_pit": "< 1 ohm",
    "layout.ese_la": "ESE Level I",
    "layout.transformer": "2.5 MVA",
    "layout.scb": "16 in 1 out",
}

_SAMPLE = {
    "DESC": "Description",
    "VECTOR": "Dyn11",
    "ANSI": "50/51N",
    "COUNT": "18",
    "SHEET": "E-103",
    "RADIUS": "R = 45 m",
    "RATIO": "33000/110 V",
    "REV": "A",
    "NUM": "1",
    "ID": "A",
}


def sample_values(sym: SymbolDef) -> dict[str, str]:
    name = sym.key.split(".", 1)[1].upper().replace("_", "")[:5]
    out: dict[str, str] = {}
    for a in sym.attrs:
        if a.tag == "TAG":
            out[a.tag] = f"{name}-01"
        elif a.tag == "RATING":
            out[a.tag] = _RATING.get(sym.key, "Rating")
        else:
            out[a.tag] = _SAMPLE.get(a.tag, a.default or a.tag.title())
    return out


def manifest(keys: Iterable[str] | None = None) -> list[dict]:
    return [
        {
            "key": s.key,
            "title": s.title,
            "category": s.category.value,
            "standard": s.standard,
            "block_name": s.block_name,
            "size": {"width": s.width, "height": s.height},
            "pins": [
                {"name": p.name, "x": p.x, "y": p.y, "side": p.side.value, "kind": p.kind.value} for p in s.pins
            ],
            "attrs": [
                {"tag": a.tag, "x": a.x, "y": a.y, "height": a.height, "default": a.default, "visible": a.visible}
                for a in s.attrs
            ],
        }
        for s in (REGISTRY[k] for k in (REGISTRY if keys is None else keys))
    ]


def _label(msp, text: str, x: float, y: float, h: float, layer: str, align: TextEntityAlignment) -> None:
    msp.add_text(text, height=h, dxfattribs={"layer": layer, "style": TEXT_STYLE}).set_placement((x, y), align=align)


def _pin_marker(msp, sym: SymbolDef, ox: float, oy: float) -> None:
    for p in sym.pins:
        x, y = ox + p.x, oy + p.y
        msp.add_circle((x, y), 0.8, dxfattribs={"layer": "G-PIN"})
        gap = 1.4
        if p.side is Side.N:
            _label(msp, p.name, x, y + gap + 0.6, 1.2, "G-PIN", TextEntityAlignment.MIDDLE_CENTER)
        elif p.side is Side.S:
            _label(msp, p.name, x, y - gap - 0.6, 1.2, "G-PIN", TextEntityAlignment.MIDDLE_CENTER)
        elif p.side is Side.E:
            _label(msp, p.name, x + gap, y + 1.2, 1.2, "G-PIN", TextEntityAlignment.MIDDLE_LEFT)
        else:
            _label(msp, p.name, x - gap, y + 1.2, 1.2, "G-PIN", TextEntityAlignment.MIDDLE_RIGHT)


def build_catalogue(
    categories: Iterable[Category] | None = None, columns: int = COLUMNS, keys: Iterable[str] | None = None
) -> Drawing:
    """A DXF holding every block definition plus a laid-out catalogue sheet in modelspace."""
    doc = ezdxf.new("R2018", setup=False)
    doc.units = units.MM
    install(doc)
    for name, colour in (("G-PIN", 1), ("G-CATALOG", 8), ("G-BBOX", 9)):
        doc.layers.add(name, color=colour)
    msp = doc.modelspace()

    wanted = list(Category) if categories is None else list(categories)
    by_cat: dict[Category, list[SymbolDef]] = defaultdict(list)
    selected = None if keys is None else set(keys)
    for sym in REGISTRY.values():
        if selected is None or sym.key in selected:
            by_cat[sym.category].append(sym)

    y_cursor = 0.0
    for cat in wanted:
        syms = by_cat.get(cat, [])
        if not syms:
            continue
        cell_h = max(max(s.height for s in syms), 30.0) + FOOTER + 10.0
        rows = -(-len(syms) // columns)
        _label(
            msp,
            f"{CATEGORY_TITLES[cat].upper()}  ({len(syms)})",
            MARGIN,
            y_cursor - HEADER / 2,
            5.0,
            "G-CATALOG",
            TextEntityAlignment.MIDDLE_LEFT,
        )
        top = y_cursor - HEADER
        for i, sym in enumerate(syms):
            col, row = i % columns, i // columns
            x0 = MARGIN + col * CELL_W
            y0 = top - (row + 1) * cell_h
            msp.add_lwpolyline(
                [(x0, y0), (x0 + CELL_W, y0), (x0 + CELL_W, y0 + cell_h), (x0, y0 + cell_h)],
                close=True,
                dxfattribs={"layer": "G-CATALOG"},
            )
            ox, oy = x0 + 8.0, y0 + FOOTER + 3.0
            msp.add_lwpolyline(
                [(ox, oy), (ox + sym.width, oy), (ox + sym.width, oy + sym.height), (ox, oy + sym.height)],
                close=True,
                dxfattribs={"layer": "G-BBOX"},
            )
            insert(msp, sym.key, (ox, oy), values=sample_values(sym))
            _pin_marker(msp, sym, ox, oy)
            _label(msp, sym.key, x0 + 2.0, y0 + 9.0, 2.5, "G-CATALOG", TextEntityAlignment.MIDDLE_LEFT)
            _label(msp, sym.title, x0 + 2.0, y0 + 5.5, 2.0, "G-CATALOG", TextEntityAlignment.MIDDLE_LEFT)
            _label(msp, sym.standard, x0 + 2.0, y0 + 2.5, 1.5, "G-CATALOG", TextEntityAlignment.MIDDLE_LEFT)
        y_cursor = top - rows * cell_h - 8.0
    return doc

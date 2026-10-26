import sys
import time

import ezdxf
from ezdxf.enums import TextEntityAlignment


def symbols(doc):
    b = doc.blocks.new("PV_STRING")
    b.add_lwpolyline([(0, 0), (6, 0), (6, 4), (0, 4)], close=True)
    b.add_line((0, 0), (6, 4))
    b.add_attdef("TAG", (0, 5), dxfattribs={"height": 1.2})
    b = doc.blocks.new("FUSE")
    b.add_lwpolyline([(0, -0.8), (3, -0.8), (3, 0.8), (0, 0.8)], close=True)
    b.add_line((-1, 0), (4, 0))
    b = doc.blocks.new("INVERTER")
    b.add_lwpolyline([(0, 0), (20, 0), (20, 14), (0, 14)], close=True)
    b.add_line((0, 0), (20, 14))
    b.add_text("=", dxfattribs={"height": 3}).set_placement((3, 9))
    b.add_text("~", dxfattribs={"height": 3}).set_placement((14, 2))
    b.add_attdef("TAG", (0, 16), dxfattribs={"height": 2})
    b.add_attdef("RATING", (0, -3), dxfattribs={"height": 1.5})
    b = doc.blocks.new("BREAKER")
    b.add_line((0, 0), (0, 3))
    b.add_line((0, 3), (2, 6))
    b.add_line((0, 7), (0, 10))
    b = doc.blocks.new("TRAFO")
    b.add_circle((0, 0), 6)
    b.add_circle((0, 8), 6)
    b.add_attdef("TAG", (8, 4), dxfattribs={"height": 2})


def sld(n_inv, strings_per_inv):
    doc = ezdxf.new("R2018", setup=True)
    symbols(doc)
    for name in ("DC", "AC", "SYMBOL", "TEXT"):
        doc.layers.add(name)
    msp = doc.modelspace()
    col_w = strings_per_inv * 9 + 30
    for i in range(n_inv):
        ox = (i % 20) * col_w
        oy = -(i // 20) * 160
        for s in range(strings_per_inv):
            x = ox + s * 9
            ins = msp.add_blockref("PV_STRING", (x, oy + 100), dxfattribs={"layer": "SYMBOL"})
            ins.add_attrib("TAG", f"I{i+1}-S{s+1}", (x, oy + 105), dxfattribs={"height": 1.2})
            msp.add_blockref("FUSE", (x + 1.5, oy + 90), dxfattribs={"layer": "SYMBOL", "rotation": 90})
            msp.add_lwpolyline([(x + 3, oy + 100), (x + 3, oy + 60), (ox + col_w / 2 - 10, oy + 48)], dxfattribs={"layer": "DC"})
        inv = msp.add_blockref("INVERTER", (ox + col_w / 2 - 10, oy + 35), dxfattribs={"layer": "SYMBOL"})
        inv.add_attrib("TAG", f"INV-{i+1:03d}", (ox + col_w / 2 - 10, oy + 51), dxfattribs={"height": 2})
        inv.add_attrib("RATING", "320 kVA 800 Vac", (ox + col_w / 2 - 10, oy + 32), dxfattribs={"height": 1.5})
        msp.add_blockref("BREAKER", (ox + col_w / 2, oy + 15), dxfattribs={"layer": "SYMBOL"})
        msp.add_line((ox + col_w / 2, oy + 15), (ox + col_w / 2, oy - 20), dxfattribs={"layer": "AC"})
        msp.add_text(f"3C x 300 sq.mm Al XLPE", dxfattribs={"height": 1.5, "layer": "TEXT"}).set_placement((ox + col_w / 2 + 1, oy), align=TextEntityAlignment.LEFT)
    for t in range(n_inv // 10):
        t_ins = msp.add_blockref("TRAFO", (t * col_w * 2, -(n_inv // 20 + 1) * 160), dxfattribs={"layer": "SYMBOL"})
        t_ins.add_attrib("TAG", f"IDT-{t+1:02d} 3.3 MVA 0.8/33 kV", (t * col_w * 2 + 8, -(n_inv // 20 + 1) * 160 + 4), dxfattribs={"height": 2})
    return doc


def layout(n_tables, flat):
    doc = ezdxf.new("R2018", setup=True)
    mod = doc.blocks.new("MODULE")
    mod.add_lwpolyline([(0, 0), (1.134, 0), (1.134, 2.278), (0, 2.278)], close=True)
    table = doc.blocks.new("TABLE_2P28")
    for r in range(2):
        for c in range(28):
            table.add_blockref("MODULE", (c * 1.15, r * 2.3))
    msp = doc.modelspace()
    per_row = 60
    for t in range(n_tables):
        x = (t % per_row) * 33.0
        y = (t // per_row) * 10.0
        if flat:
            for r in range(2):
                for c in range(28):
                    msp.add_blockref("MODULE", (x + c * 1.15, y + r * 2.3))
        else:
            msp.add_blockref("TABLE_2P28", (x, y))
    return doc


def timed(label, fn):
    t = time.perf_counter()
    out = fn()
    print(f"{label}: {time.perf_counter() - t:.2f}s", flush=True)
    return out


if __name__ == "__main__":
    kind = sys.argv[1]
    if kind == "sld":
        doc = timed("build", lambda: sld(int(sys.argv[2]), int(sys.argv[3])))
    else:
        doc = timed("build", lambda: layout(int(sys.argv[2]), sys.argv[3] == "flat"))
    out = sys.argv[4]
    timed("save", lambda: doc.saveas(out))
    print("entities in modelspace:", len(doc.modelspace()))
    timed("readback", lambda: ezdxf.readfile(out))

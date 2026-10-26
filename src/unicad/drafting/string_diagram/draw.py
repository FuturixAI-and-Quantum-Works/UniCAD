"""Draw one string diagram page: strings as module chains with polarity, cables, fuses, the MPPT or SCB they land on, a summary table and a
schematic plan of the table slots they occupy. Dimensions are multiples of `u`, so a page looks the same at every paper size."""

import math
from dataclasses import dataclass, field

from ezdxf.document import Drawing

from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.text import DESCENDER, text_width
from unicad.drafting.string_diagram.plan import DiagramType, StringLine
from unicad.drafting.symbols import REGISTRY, pin_at
from unicad.drafting.symbols.install import install
from unicad.drafting.symbols.library.kit import LAYER as SYMBOL_LAYER

L_MOD = "E-STR-MOD"
L_POS = "E-STR-POS"
L_NEG = "E-STR-NEG"
L_BOX = "E-STR-BOX"
L_MPPT = "E-STR-MPPT"
L_TEXT = "E-STR-TEXT"
L_TABLE = "E-STR-TABLE"
L_PLAN = "E-STR-PLAN"
L_PLAN_OFF = "E-STR-PLAN-OFF"
LAYERS = {
    L_MOD: (7, "CONTINUOUS", 35), L_POS: (1, "CONTINUOUS", 35), L_NEG: (5, "CONTINUOUS", 35), L_BOX: (7, "DASHED2", 18),
    L_MPPT: (7, "CONTINUOUS", 25), L_TEXT: (7, "CONTINUOUS", 18), L_TABLE: (7, "CONTINUOUS", 18), L_PLAN: (7, "CONTINUOUS", 18),
    L_PLAN_OFF: (8, "CONTINUOUS", 13),
}  # fmt: skip

MIN_U_MM = 2.2
U_STEPS = (1.0, 0.96, 0.92, 0.88, 0.84, 0.8, 0.76, 0.72, 0.68, 0.64, 0.6)
MODULE_LEN, MODULE_H = 6.4, 3.2
MODULE_SCALE = MODULE_H / 10.0
LINK = 1.4
BREAK = 10.0
ROW_PITCH = 5.4
RET = 2.2
CABLE_H = 0.75
SHOWN_ALL = 5
PLAN_MIN_TEXT = 1.4
FUSE_LEN = 6.0
BUS_W = 5.0
MPPT_BOX_W = 9.0
INV_W = 8.0


def box(x: float, baseline: float, h: float, w: float) -> Region:
    return Region(x, baseline - DESCENDER * h, x + w, baseline + h)


@dataclass(frozen=True)
class Placed:
    tag: str
    text: str
    box: Region


@dataclass
class Ink:
    """The page's drawing surface; remembers every text box so tests can check for overlaps."""

    doc: Drawing
    style: str
    texts: list[Placed] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.msp = self.doc.modelspace()
        for name, (colour, lt, lw) in LAYERS.items():
            if name not in self.doc.layers:
                self.doc.layers.add(name, color=colour, linetype=lt if lt in self.doc.linetypes else "CONTINUOUS", lineweight=lw)

    def line(self, p: tuple[float, float], q: tuple[float, float], layer: str = L_MOD) -> None:
        self.msp.add_line((round(p[0], 4), round(p[1], 4)), (round(q[0], 4), round(q[1], 4)), dxfattribs={"layer": layer})

    def path(self, *pts: tuple[float, float], layer: str = L_MOD) -> None:
        for p, q in zip(pts, pts[1:], strict=False):
            self.line(p, q, layer)

    def rect(self, r: Region, layer: str = L_MOD) -> None:
        self.path((r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0), layer=layer)

    def dot(self, x: float, y: float, r: float, layer: str = L_MOD) -> None:
        pts = [(x + r * math.cos(math.radians(45 * i)), y + r * math.sin(math.radians(45 * i))) for i in range(8)]
        for i in range(0, 8, 2):
            self.msp.add_solid([(x, y), pts[i], pts[(i + 2) % 8], pts[i + 1]], dxfattribs={"layer": layer})

    def text(self, s: str, x: float, y: float, h: float, align: str = "left", layer: str = L_TEXT, tag: str = "") -> Region:
        w = text_width(s, h)
        left = {"left": x, "center": x - w / 2, "right": x - w}[align]
        self.msp.add_text(s, dxfattribs={"layer": layer, "style": self.style, "height": round(h, 4), "insert": (round(left, 4), round(y, 4))})
        r = box(left, y, h, w)
        self.texts.append(Placed(tag or s, s, r))
        return r

    def symbol(self, key: str, at: tuple[float, float], rotation: float, scale: float) -> None:
        install(self.doc, [key])
        self.msp.add_blockref(REGISTRY[key].block_name, (round(at[0], 4), round(at[1], 4)),
                              dxfattribs={"layer": SYMBOL_LAYER, "rotation": rotation, "xscale": round(scale, 5), "yscale": round(scale, 5)})  # fmt: skip

    def symbol_with_pin(self, key: str, pin_name: str, pin_xy: tuple[float, float], rotation: float, scale: float) -> None:
        """Insert `key` so that its pin `pin_name` lands on `pin_xy`."""
        ox, oy = pin_at(key, pin_name, (0.0, 0.0), rotation)
        self.symbol(key, (pin_xy[0] - ox * scale, pin_xy[1] - oy * scale), rotation, scale)


def string_label(t: DiagramType, ln: StringLine) -> str:
    return f"{ln.tag} : {ln.modules} x {t.module.pmax:g} Wp"


def callout(t: DiagramType, ln: StringLine) -> str:
    return t.string_callout + (f", {ln.length_m:g} m" if ln.length_m else "")


def shown_modules(n: int) -> list[int]:
    return list(range(1, n + 1)) if n <= SHOWN_ALL else [1, 2, 3, n - 1, n]


def chain_length(n: int, u: float) -> float:
    shown = min(n, SHOWN_ALL)
    if n <= SHOWN_ALL:
        return (shown * MODULE_LEN + (shown - 1) * LINK) * u
    return (SHOWN_ALL * MODULE_LEN + 3 * LINK + BREAK) * u


# ---------------------------------------------------------------- measurements


@dataclass(frozen=True)
class Columns:
    label: float
    chain: float
    cable: float
    device: float
    sink: float

    @property
    def total(self) -> float:
        return self.label + self.chain + self.cable + self.device + self.sink


def columns(t: DiagramType, lines: tuple[StringLine, ...], u: float) -> Columns:
    label = 4.0 * u
    chain = chain_length(max(ln.modules for ln in lines), u) + 1.0 * u
    cable = max(max(text_width(ln.cable_id, CABLE_H * u), text_width(callout(t, ln), CABLE_H * u)) for ln in lines) + 3.0 * u
    device = (FUSE_LEN + 1.0) * u if t.fuse is not None else 0.0
    if t.envelope != "none":
        device += 6.0 * u
    if t.kind == "inverter":
        sink = (MPPT_BOX_W + 2.0 + 2.0 + INV_W + 2.0) * u
    else:
        device += BUS_W * u
        out = t.output
        text = 12.0 * u if out is None else max(text_width(out.cable_id, 0.8 * u), text_width(out_callout(out), 0.8 * u))
        sink = text + 3.5 * u + 15.0 * u
    return Columns(label, chain, cable, device, sink)


def out_callout(out) -> str:
    return out.callout + (f", {out.length_m:g} m" if out.length_m else "")


def top_zone(t: DiagramType, u: float) -> float:
    n = 3 if t.kind == "inverter" else 0
    if t.envelope != "none":
        n = max(n, 1 + len(t.devices))
    return (n * 1.15 + 1.2) * u if n else 0.0


def diagram_size(t: DiagramType, lines: tuple[StringLine, ...], u: float) -> tuple[float, float]:
    h = 3.4 * u + top_zone(t, u) + 3.0 * u + (len(lines) - 1) * ROW_PITCH * u + (4.0 if t.kind == "inverter" else 7.0) * u
    return columns(t, lines, u).total + 1.0 * u, h


def page_tables(t: DiagramType, lines: tuple[StringLine, ...]):
    ids = list(dict.fromkeys(tb for ln in lines for tb, _, _ in ln.slots))
    return [t.table_defs[i] for i in ids if i in t.table_defs]


# ---------------------------------------------------------------- summary table


def table_cells(t: DiagramType, lines: tuple[StringLine, ...], with_positions: bool) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    head = ["STRING", "CABLE ID", "MODS", "VOC V", "ISC A", "FUSE", "MPPT-IP" if t.kind == "inverter" else "INPUT"]
    if with_positions:
        head.append("TABLE POSITION")
    rows = []
    for ln in lines:
        fuse = t.fuse.label.replace(" gPV", "") if t.fuse else "-"
        row = [ln.tag, ln.cable_id, str(ln.modules), f"{ln.voc_cold_v:.1f}", f"{ln.isc_a:g}", fuse, f"{ln.mppt} / {ln.ip}" if t.kind == "inverter" else ln.ip]
        if with_positions:
            row.append(ln.positions or "-")
        rows.append(tuple(row))
    return tuple(head), rows


def table_title(t: DiagramType) -> str:
    return f"STRING SUMMARY : VOC AT MINIMUM AMBIENT {t.tmin_c:g} DEG C, ISC AT STC, VOC LIMIT {t.voc_limit_v:g} V"


def table_widths(t: DiagramType, lines: tuple[StringLine, ...], u: float) -> list[float]:
    head, rows = table_cells(t, lines, t.has_layout)
    widths = [max(text_width(c, 0.8 * u) for c in col) + 1.2 * u for col in zip(head, *rows, strict=True)]
    widths[-1] += max(0.0, text_width(table_title(t), 0.85 * u) + 0.5 * u - sum(widths))
    return widths


def table_size(t: DiagramType, lines: tuple[StringLine, ...], u: float) -> tuple[float, float]:
    return sum(table_widths(t, lines, u)), 2.6 * u + (len(lines) + 1) * 1.7 * u


def draw_table(ink: Ink, t: DiagramType, lines: tuple[StringLine, ...], x: float, y_top: float, u: float) -> None:
    h, pitch = 0.8 * u, 1.7 * u
    head, rows = table_cells(t, lines, t.has_layout)
    widths = table_widths(t, lines, u)
    w = sum(widths)
    ink.text(table_title(t), x, y_top - 1.1 * u, 0.85 * u, tag="table title")
    top = y_top - 2.0 * u
    n = len(rows) + 1
    for i in range(n + 1):
        ink.line((x, top - i * pitch), (x + w, top - i * pitch), L_TABLE)
    cx = x
    for col_w in widths:
        ink.line((cx, top), (cx, top - n * pitch), L_TABLE)
        cx += col_w
    ink.line((x + w, top), (x + w, top - n * pitch), L_TABLE)
    for r, cells in enumerate((head, *rows)):
        cx = x
        for c, (text, col_w) in enumerate(zip(cells, widths, strict=True)):
            ink.text(text, cx + 0.6 * u, top - (r + 1) * pitch + 0.45 * u, h, tag=f"table r{r} c{c}")
            cx += col_w


# ---------------------------------------------------------------- table position plan


@dataclass(frozen=True)
class PlanLayout:
    tp: float
    cw: float
    per_row: int
    block_w: float
    block_h: float


def plan_block_h(tp: float, rows: int) -> float:
    return 1.5 * tp + rows * 3.6 * tp + 0.3 * tp


PLAN_TITLE = "TABLE POSITIONS (SCHEMATIC, NOT TO SCALE)"
PLAN_KEYS = ("MODULE NUMBER = SERIES ORDER, 1+ IS THE (+) END, N- THE (-) END.", "ROW 1 IS NEAREST THE TABLE ORIGIN. GREY SLOTS HOLD OTHER STRINGS.")


def pack_plan(tables, region: Region, u: float) -> PlanLayout | None:
    if not tables or region.width < max(text_width(PLAN_TITLE, 0.85 * u), *(text_width(k, 0.7 * u) for k in PLAN_KEYS)):
        return None
    head = 4.4 * u
    max_cols = max(tb.cols for tb in tables)
    max_rows = max(tb.rows for tb in tables)
    for tp in (0.7 * u, 0.6 * u, 0.5 * u):
        if tp < PLAN_MIN_TEXT:
            continue
        gutter = text_width("ROW 2", tp) + 0.8 * tp
        need = text_width("28-", tp) + 0.6 * tp
        best: PlanLayout | None = None
        for per_row in range(1, len(tables) + 1):
            cw = (region.width - per_row * gutter - (per_row - 1) * 2 * tp) / (per_row * max_cols)
            if cw < need:
                break
            cw = min(cw, 4.0 * tp)
            bh = plan_block_h(tp, max_rows)
            if head + math.ceil(len(tables) / per_row) * (bh + tp) > region.height:
                continue
            cand = PlanLayout(tp, cw, per_row, max_cols * cw + gutter, bh)
            if best is None or cand.cw > best.cw + 1e-9:
                best = cand
        if best:
            return best
    return None


def draw_plan(ink: Ink, lines: tuple[StringLine, ...], tables, marks: dict[str, str], region: Region, lay: PlanLayout, u: float) -> None:
    tp = lay.tp
    ink.text(PLAN_TITLE, region.x0, region.y1 - 1.1 * u, 0.85 * u, tag="plan title")
    ink.text(PLAN_KEYS[0], region.x0, region.y1 - 2.3 * u, 0.7 * u, tag="plan key")
    ink.text(PLAN_KEYS[1], region.x0, region.y1 - 3.4 * u, 0.7 * u, tag="plan key 2")
    owner: dict[tuple[str, int, int], tuple[StringLine, int]] = {}
    for ln in lines:
        for k, slot in enumerate(ln.slots, start=1):
            owner[slot] = (ln, k)
    gutter = text_width("ROW 2", tp) + 0.8 * tp
    top = region.y1 - 4.4 * u
    for idx, tb in enumerate(tables):
        col, row = idx % lay.per_row, idx // lay.per_row
        x0 = region.x0 + col * (lay.block_w + 2 * tp)
        y_top = top - row * (lay.block_h + tp)
        mark = marks.get(tb.id)
        ink.text(f"{tb.id}  {mark}" if mark else tb.id, x0, y_top - 1.1 * tp, 1.05 * tp, tag=f"plan {tb.id}")
        for r in range(tb.rows):
            y_cell_top = y_top - 1.5 * tp - (tb.rows - 1 - r) * 3.6 * tp - 1.6 * tp
            y_cell_bot = y_cell_top - 2.0 * tp
            ink.text(f"ROW {r + 1}", x0, y_cell_bot + 0.6 * tp, tp, tag=f"plan {tb.id} row {r}")
            for c in range(tb.cols):
                if (r, c) in tb.unused:
                    continue
                x = x0 + gutter + c * lay.cw
                cell = Region(x, y_cell_bot, x + lay.cw, y_cell_top)
                hit = owner.get((tb.id, r, c))
                if hit is None:
                    ink.rect(cell, L_PLAN_OFF)
                    continue
                ln, k = hit
                ink.rect(cell, L_PLAN)
                last = k == len(ln.slots)
                label = f"{k}+" if k == 1 else f"{k}-" if last else str(k)
                layer = L_POS if k == 1 else L_NEG if last else L_TEXT
                if layer != L_TEXT:
                    ink.rect(Region(x + 0.15 * tp, y_cell_bot + 0.15 * tp, x + lay.cw - 0.15 * tp, y_cell_top - 0.15 * tp), layer)
                ink.text(label, x + lay.cw / 2, y_cell_bot + 0.6 * tp, tp, "center", layer, tag=f"plan {tb.id} {r},{c}")
                prev = owner.get((tb.id, r, c - 1))
                if prev is None or prev[0] is not ln:
                    ink.text(ln.tag, x, y_cell_top + 0.35 * tp, tp, tag=f"plan {tb.id} {ln.tag} {r},{c}")


# ---------------------------------------------------------------- the diagram


@dataclass(frozen=True)
class Row:
    y0: float
    y_ret: float


def draw_chain(ink: Ink, ln: StringLine, x: float, y0: float, y_ret: float, u: float) -> float:
    """Module chain with the (+) return conductor under it; returns the x of the (-) end."""
    s = MODULE_SCALE * u
    n = ln.modules
    cx = x
    right: float | None = None
    for pos in range(len(shown_modules(n))):
        if pos == 3 and n > SHOWN_ALL and right is not None:
            x1 = cx + BREAK * u - LINK * u
            ink.line((right, y0), (right + 1.5 * u, y0), L_MOD)
            ink.line((x1 - 1.5 * u, y0), (x1, y0), L_MOD)
            for bx in (right + 1.5 * u, x1 - 1.5 * u):
                ink.line((bx - 0.35 * u, y0 - 0.9 * u), (bx + 0.35 * u, y0 + 0.9 * u), L_MOD)
            ink.text(f"...{n} NOS...", (right + x1) / 2, y0 + 0.7 * u, 0.7 * u, "center", tag=f"{ln.tag} break")
            cx = x1
        elif right is not None:
            ink.line((right, y0), (cx, y0), L_MOD)
        ink.symbol("pv.module", (cx + MODULE_LEN * u, y0 - 5 * s), 90, s)
        right = cx + MODULE_LEN * u
        cx = right + LINK * u
    assert right is not None
    stub = x - 1.5 * u
    ink.path((x, y0), (stub, y0), (stub, y_ret), layer=L_POS)
    ink.text("+", stub - 1.0 * u, y0 + 0.4 * u, 0.9 * u, layer=L_POS, tag=f"{ln.tag} plus")
    ink.text("-", right + 0.5 * u, y0 - 1.5 * u, 0.9 * u, layer=L_NEG, tag=f"{ln.tag} minus")
    return right


def draw_diagram(ink: Ink, t: DiagramType, lines: tuple[StringLine, ...], x0: float, y_top: float, u: float, page: tuple[int, int]) -> None:
    cols = columns(t, lines, u)
    banner = f"TYPICAL FOR {t.typical} : SHOWN FOR {t.representative.tag}" if len(t.members) > 1 else t.representative.tag
    if page[1] > 1:
        banner += f" : PAGE {page[0]} OF {page[1]}"
    r = ink.text(banner, x0 + 0.8 * u, y_top - 2.1 * u, 1.1 * u, tag="banner")
    ink.rect(Region(x0, y_top - 3.0 * u, r.x1 + 0.8 * u, y_top - 0.1 * u), L_MPPT)
    y_first = y_top - 3.4 * u - top_zone(t, u) - 3.0 * u
    rows = [Row(y_first - i * ROW_PITCH * u, y_first - i * ROW_PITCH * u - RET * u) for i in range(len(lines))]
    x_chain = x0 + cols.label
    x_cable = x_chain + cols.chain
    x_dev = x_cable + cols.cable
    x_sink = x_dev + cols.device
    for ln, g in zip(lines, rows, strict=True):
        ink.text(string_label(t, ln), x_chain - 1.5 * u, g.y0 + 1.95 * u, 0.85 * u, tag=f"{ln.tag} label")
        x_minus = draw_chain(ink, ln, x_chain, g.y0, g.y_ret, u)
        ink.text(ln.cable_id, x_minus + 1.8 * u, g.y0 + 1.8 * u, CABLE_H * u, tag=f"{ln.tag} cable id")
        ink.text(callout(t, ln), x_minus + 1.8 * u, g.y0 + 0.7 * u, CABLE_H * u, tag=f"{ln.tag} cable")
        ink.line((x_minus, g.y0), (x_sink if t.kind == "inverter" else x_sink - 1.5 * u, g.y0), L_NEG)
    y_bottom = rows[-1].y_ret - (1.8 if t.kind == "inverter" else 4.8) * u
    y_env_top = rows[0].y0 + 2.8 * u
    zone_y = y_top - 3.4 * u - 1.2 * u
    env = Region(x_dev + 0.5 * u, y_bottom, x_sink - 0.5 * u, y_env_top) if t.envelope != "none" else None
    x_plus = x_chain - 1.5 * u
    x_fuse_in = x_dev + 2.0 * u if env else x_dev + 0.5 * u
    x_plus_end = x_sink if t.kind == "inverter" else x_sink - BUS_W * u
    for ln, g in zip(lines, rows, strict=True):
        if t.fuse is not None:
            ink.line((x_plus, g.y_ret), (x_fuse_in, g.y_ret), L_POS)
            ink.symbol_with_pin("dc.fuse", "in", (x_fuse_in, g.y_ret), 90, FUSE_LEN / 20.0 * u)
            ink.text(t.fuse.label.replace(" gPV", ""), x_fuse_in + FUSE_LEN * u / 2, g.y_ret - 1.9 * u, 0.7 * u, "center", tag=f"{ln.tag} fuse")
            ink.line((x_fuse_in + FUSE_LEN * u, g.y_ret), (x_plus_end, g.y_ret), L_POS)
        else:
            ink.line((x_plus, g.y_ret), (x_plus_end, g.y_ret), L_POS)
    if env is not None:
        ink.rect(env, L_BOX)
        align, ex = ("left", env.x0) if t.kind == "scb" else ("right", env.x1 - 1.5 * u)
        for i, text in enumerate([t.envelope_title, *t.devices]):
            ink.text(text, ex, zone_y - i * 1.15 * u, 0.8 * u, align, tag=f"env title {i}")
    if t.kind == "inverter":
        _inverter_side(ink, t, lines, rows, x_sink, y_bottom, y_env_top, u)
    else:
        _scb_side(ink, t, rows, x_sink, u)


def _inverter_side(ink: Ink, t: DiagramType, lines: tuple[StringLine, ...], rows: list[Row], x: float, y_bottom: float, y_env_top: float, u: float) -> None:
    box_w = MPPT_BOX_W * u
    env = Region(x - 1.0 * u, y_bottom - 0.4 * u, x + (MPPT_BOX_W + 2 + 2 + INV_W + 1) * u, y_env_top + 0.2 * u)
    ink.rect(env, L_BOX)
    groups: dict[int, list[int]] = {}
    for i, ln in enumerate(lines):
        groups.setdefault(ln.mppt or 1, []).append(i)
    mids = []
    for m, idx in sorted(groups.items()):
        top, bot = rows[idx[0]].y0 + 2.0 * u, rows[idx[-1]].y_ret - 0.9 * u
        ink.rect(Region(x, bot, x + box_w, top), L_MPPT)
        ink.text(f"MPPT-{m}", x + 0.6 * u, top - 1.1 * u, 0.7 * u, tag=f"mppt {m}")
        for i in idx:
            g = rows[i]
            ink.dot(x, g.y0, 0.28 * u, L_NEG)
            ink.dot(x, g.y_ret, 0.28 * u, L_POS)
            ink.text(lines[i].ip, x + 1.2 * u, g.y0 - 1.0 * u, 0.7 * u, tag=f"mppt {m} {lines[i].ip}")
        mid = (top + bot) / 2
        mids.append(mid)
        ink.line((x + box_w, mid), (x + box_w + 2.0 * u, mid), L_MOD)
    xb = x + box_w + 2.0 * u
    ink.line((xb, min(mids)), (xb, max(mids)), L_MOD)
    cy = (min(mids) + max(mids)) / 2
    key = t.inverter.symbol or "inv.string"
    s = INV_W * u / REGISTRY[key].width
    pin_xy = (xb + 2.0 * u, cy)
    ink.line((xb, cy), pin_xy, L_MOD)
    ink.symbol_with_pin(key, "dc", pin_xy, 90, s)
    inv = t.inverter
    ink.text(t.inverter_tags, env.x0, env.y1 + 3.4 * u, 0.85 * u, tag="inv tags")
    ink.text(f"{inv.ac_rated_kva:g} kVA {inv.model}", env.x0, env.y1 + 2.25 * u, 0.8 * u, tag="inv model")
    assert t.mppt is not None
    ink.text(f"{t.mppt.used} OF {t.mppt.mppt_count} MPPT USED, {t.mppt.inputs_per_mppt} INPUT PER MPPT", env.x0, env.y1 + 1.1 * u, 0.7 * u, tag="inv mppt")


def _scb_side(ink: Ink, t: DiagramType, rows: list[Row], x_sink: float, u: float) -> None:
    x_bus = x_sink - BUS_W * u
    xp, xn = x_bus + 1.5 * u, x_bus + 3.5 * u
    yn = rows[-1].y_ret - 1.2 * u
    yp = yn - 2.2 * u
    for g in rows:
        ink.line((x_bus, g.y_ret), (xp, g.y_ret), L_POS)
        ink.dot(xp, g.y_ret, 0.28 * u, L_POS)
        ink.dot(xn, g.y0, 0.28 * u, L_NEG)
    ink.line((xp, rows[0].y_ret), (xp, yp), L_POS)
    ink.line((xn, rows[0].y0), (xn, yn), L_NEG)
    out = t.output
    if out is None:
        ink.line((xp, yp), (x_sink, yp), L_POS)
        ink.line((xn, yn), (x_sink, yn), L_NEG)
        return
    text_w = max(text_width(out.cable_id, 0.8 * u), text_width(out_callout(out), 0.8 * u))
    tx = x_sink + 1.2 * u
    dest_x = tx + text_w + 2.0 * u
    ink.line((x_sink, yp), (dest_x, yp), L_POS)
    ink.line((x_sink, yn), (dest_x, yn), L_NEG)
    ink.text(out.cable_id, tx, yn + 2.4 * u, 0.8 * u, tag="out id")
    ink.text(out_callout(out), tx, yn + 1.3 * u, 0.8 * u, tag="out cable")
    ink.text("(-)", tx, yn - 1.3 * u, 0.8 * u, layer=L_NEG, tag="out neg")
    ink.text("(+)", tx, yp - 1.3 * u, 0.8 * u, layer=L_POS, tag="out pos")
    dest = Region(dest_x, yp - 2.0 * u, dest_x + 13.0 * u, yn + 2.0 * u)
    ink.rect(dest, L_MPPT)
    ink.dot(dest_x, yp, 0.28 * u, L_POS)
    ink.dot(dest_x, yn, 0.28 * u, L_NEG)
    ink.text(f"TO {out.to_tag}", dest_x + 1.2 * u, yn + 0.5 * u, 0.85 * u, tag="dest tag")
    ink.text(f"DC INPUT {out.to_input}", dest_x + 1.2 * u, yn - 0.9 * u, 0.8 * u, tag="dest input")

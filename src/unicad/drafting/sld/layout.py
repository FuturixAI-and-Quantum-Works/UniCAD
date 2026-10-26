"""Deterministic layered placement of an ElectricalNetwork into an SldLayout.

Ranks count power edges from the POI: the POI is rank 0 at the top and generation hangs
below it, rank by rank. Branches that do not reach the POI (auxiliary feeders, loads)
hang below the node that feeds them, so every tree edge spans exactly one rank. Single-
terminal devices (SPD, LA, PT on a feeder) and CT-operated meters and relays are drawn as
satellites beside their host. Repeated groups are drawn once inside a dashed frame
labelled with their multiplicity.

x positions come from a tidy-tree pass over per-level contours (levels: 2r for rank r,
2r - 1 for the channel above it), so subtrees never overlap and chains stay straight.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field

from unicad.catalog.store import Catalog
from unicad.domain.network import (
    EARTH_PORT,
    KIND_SYMBOLS,
    SIGNAL_PORTS,
    SOURCE_KINDS,
    Edge,
    ElectricalNetwork,
    Group,
    Node,
    NodeKind,
    Service,
)
from unicad.drafting.sheets.text import DESCENDER, LINE_PITCH, text_width
from unicad.drafting.sld import annotate
from unicad.drafting.sld.model import (
    Align,
    Box,
    Busbar,
    Callout,
    Frame,
    Label,
    OffSheet,
    PinRef,
    PlacedSymbol,
    Point,
    SldLayout,
    SldOptions,
    SldPage,
    Wire,
    WireKind,
    union_all,
)
from unicad.drafting.symbols import GRID, REGISTRY, PinKind, Side, SymbolDef, pin_at

BUS_KINDS = frozenset({NodeKind.BUSBAR, NodeKind.ACDB, NodeKind.LT_PANEL, NodeKind.HT_PANEL})
_TERMINAL_KINDS = SOURCE_KINDS | {NodeKind.LOAD}
# A string's catalog spec is its module, whose symbol is one module; the string symbol comes from its kind.
_ARRAY_KINDS = frozenset({NodeKind.PV_STRING, NodeKind.PV_ARRAY_BLOCK})
EARTH_SYMBOL = "earth.earth"
OFFSHEET_SYMBOL = "ann.offsheet"
STUB = 2.5
_FLIP = {Side.N: Side.S, Side.S: Side.N, Side.E: Side.W, Side.W: Side.E}

Contour = dict[int, tuple[float, float]]


class LayoutError(ValueError):
    pass


def label(text: str, at: Point, height: float, align: Align = "left") -> Label:
    w = text_width(text, height)
    x0 = {"left": at[0], "right": at[0] - w, "center": at[0] - w / 2}[align]
    box = Box(x0, at[1] - 0.5 * height - DESCENDER * height, x0 + w, at[1] + 0.5 * height)
    return Label(text, at, height, align, box)


def _rot(x: float, y: float, rotation: float) -> Point:
    r = math.radians(rotation)
    c, s = math.cos(r), math.sin(r)
    return round(x * c - y * s, 6) + 0.0, round(x * s + y * c, 6) + 0.0


def _side(side: Side, rotation: float) -> Side:
    return _FLIP[side] if round(rotation) % 360 == 180 else side


def _symbol(key: str) -> SymbolDef:
    try:
        return REGISTRY[key]
    except KeyError:
        raise LayoutError(f"symbol {key!r} is not in the symbol registry") from None


@dataclass
class _Geom:
    """A symbol at rotation, in coordinates where the insertion point is the origin."""

    key: str
    rotation: float
    values: tuple[tuple[str, str], ...]
    body: Box
    labels: list[Label]
    pins: dict[str, tuple[Point, Side]]

    def shifted(self, dx: float, dy: float) -> "_Geom":
        return _Geom(
            self.key,
            self.rotation,
            self.values,
            self.body.shift(dx, dy),
            [label(lb.text, (lb.at[0] + dx, lb.at[1] + dy), lb.height, lb.align) for lb in self.labels],
            {p: ((at[0] + dx, at[1] + dy), s) for p, (at, s) in self.pins.items()},
        )


def _geom(key: str, rotation: float, values: tuple[tuple[str, str], ...]) -> _Geom:
    sym = _symbol(key)
    corners = [_rot(x, y, rotation) for x, y in ((0, 0), (sym.width, 0), (sym.width, sym.height), (0, sym.height))]
    body = Box(min(c[0] for c in corners), min(c[1] for c in corners), max(c[0] for c in corners), max(c[1] for c in corners))
    align: Align = "left" if math.cos(math.radians(rotation)) > -1e-9 else "right"
    given = dict(values)
    labels = []
    for a in sym.attrs:
        text = given.get(a.tag, a.default)
        if a.visible and text:
            labels.append(label(text, _rot(a.x, a.y, rotation), a.height, align))
    pins = {p.name: (pin_at(key, p.name, (0, 0), rotation), _side(p.side, rotation)) for p in sym.pins}
    return _Geom(key, rotation, values, body, labels, pins)


def _map_ports(node: Node, sym: SymbolDef) -> dict[str, str]:
    """Network port -> symbol pin: by name first, then power ports onto power pins in order."""
    ports = [p for p in node.port_names if p != EARTH_PORT]
    used: set[str] = set()
    out: dict[str, str] = {}
    names = {p.name for p in sym.pins}
    for port in ports:
        if port in names:
            out[port] = port
            used.add(port)
    power_pins = [p.name for p in sym.pins if p.kind not in (PinKind.SIGNAL, PinKind.EARTH) and p.name not in used]
    sig_pins = [p.name for p in sym.pins if p.kind is PinKind.SIGNAL and p.name not in used]
    for port in ports:
        if port in out:
            continue
        pool = sig_pins if port in SIGNAL_PORTS and sig_pins else power_pins
        if not pool:
            pool = [p.name for p in sym.pins if p.name not in used]
        if pool:
            pin = pool.pop(0)
            out[port] = pin
            used.add(pin)
            for other in (power_pins, sig_pins):
                if pin in other:
                    other.remove(pin)
    return out


def _orient(sym: SymbolDef, mapping: dict[str, str], up: set[str], down: set[str]) -> tuple[float, dict[str, str]]:
    """Rotation 0 or 180 and the port mapping that put `up` ports on the north side and `down` ports south.

    Two-terminal devices whose terminals are alike (breakers, CTs, transformers) swap terminals
    instead of turning over, so their ratings stay on the right like the rest of the column.
    """
    sides = {p.name: p.side for p in sym.pins}
    kinds = {p.name: p.kind for p in sym.pins}
    options: list[tuple[float, dict[str, str]]] = [(0.0, mapping)]
    power = [p for p in mapping if p not in SIGNAL_PORTS]
    if len(power) == 2:
        a, b = (mapping[p] for p in power)
        if {sides[a], sides[b]} == {Side.N, Side.S} and kinds[a] == kinds[b]:
            options.append((0.0, {**mapping, power[0]: b, power[1]: a}))
    options.append((180.0, mapping))

    def score(opt: tuple[float, dict[str, str]]) -> int:
        rot, m = opt
        s = sum(1 for p in up if p in m and _side(sides[m[p]], rot) is Side.N)
        return s + sum(1 for p in down if p in m and _side(sides[m[p]], rot) is Side.S)

    best = max(score(o) for o in options)
    return next(o for o in options if score(o) == best)


@dataclass
class _Sat:
    node_id: str
    edge: Edge
    host_port: str
    kind: str  # "tap" | "control"
    geom: _Geom | None = None
    pin: str = ""


@dataclass
class _Rec:
    node: Node
    key: str | None
    in_r: bool = False
    rank: int = 0
    parent: str | None = None
    tree_edge: Edge | None = None
    children: list[str] = field(default_factory=list)
    sats: list[_Sat] = field(default_factory=list)
    earth: bool = False
    mapping: dict[str, str] = field(default_factory=dict)
    geom: _Geom | None = None
    extent: Box = Box(0, 0, 0, 0)
    earth_geom: _Geom | None = None
    earth_wire: tuple[Point, ...] = ()
    bus_x: tuple[float, float] = (0.0, 0.0)
    ctrl_x: float = 0.0
    ctrl_y: float = 0.0
    bus_labels: list[Label] = field(default_factory=list)
    child_dx: dict[str, float] = field(default_factory=dict)
    callout: tuple[str, ...] = ()
    x: float = 0.0
    y: float = 0.0

    @property
    def is_bus(self) -> bool:
        return self.key is None


@dataclass
class _FrameRec:
    group: Group
    root: str
    x0: float
    x1: float
    top: int
    bottom: int
    lines: tuple[str, ...]
    members: frozenset[str]
    i_top: int = 1
    i_bottom: int = 1


class _Builder:
    def __init__(self, net: ElectricalNetwork, catalog: Catalog | None, options: SldOptions, scope: frozenset[str] | None):
        self.net, self.catalog, self.o = net, catalog, options
        self.warnings: list[str] = []
        keep = scope if scope is not None else frozenset(net.node_by_id)
        self.order = {e.id: i for i, e in enumerate(net.edges)}
        self.recs: dict[str, _Rec] = {}
        self.earth_nodes: set[str] = set()
        self.control_only: list[Node] = []
        for n in net.nodes:
            if n.id not in keep:
                continue
            if n.kind is NodeKind.EARTH_ELECTRODE:
                self.earth_nodes.add(n.id)
                continue
            power = [p for p in n.port_names if p not in SIGNAL_PORTS]
            if not power:
                self.control_only.append(n)
            key = None if n.kind in BUS_KINDS else (KIND_SYMBOLS[n.kind] if n.kind in _ARRAY_KINDS and not n.symbol else n.symbol_key(catalog))
            if key is None and n.kind not in BUS_KINDS:
                raise LayoutError(f"node {n.id!r} ({n.kind}) has no symbol")
            self.recs[n.id] = _Rec(n, key)
        self.power_edges = [e for e in net.edges if e.is_power and e.src_node in self.recs and e.dst_node in self.recs]
        self.cut_edges = [e for e in net.edges if e.is_power and (e.src_node in self.recs) != (e.dst_node in self.recs)]
        self.frames: list[_FrameRec] = []
        self.sat_of: dict[str, str] = {}

    # ---------------------------------------------------------------- topology
    def topology(self) -> None:
        edges_at: dict[str, list[Edge]] = defaultdict(list)
        for e in self.power_edges:
            edges_at[e.src_node].append(e)
            edges_at[e.dst_node].append(e)
        for nid, rec in self.recs.items():
            n = rec.node
            power = [p for p in n.port_names if p not in SIGNAL_PORTS]
            es = edges_at[nid]
            if len(power) == 1 and len(es) == 1 and n.kind not in _TERMINAL_KINDS and n.kind not in BUS_KINDS:
                e = es[0]
                host = e.dst_node if e.src_node == nid else e.src_node
                if not self.recs[host].is_bus and host not in self.sat_of:
                    port = e.dst_port if e.dst_node == host else e.src_port
                    self.sat_of[nid] = host
                    self.recs[host].sats.append(_Sat(nid, e, port, "tap"))
        down: dict[str, list[Edge]] = defaultdict(list)
        up: dict[str, list[Edge]] = defaultdict(list)
        for e in self.power_edges:
            if e.src_node in self.sat_of or e.dst_node in self.sat_of:
                continue
            down[e.src_node].append(e)
            up[e.dst_node].append(e)
        self.down, self.up = down, up

        placed: set[str] = set()
        roots: list[str] = []
        movable = [nid for nid in self.recs if nid not in self.sat_of and nid not in {n.id for n in self.control_only}]
        poi = [nid for nid in movable if self.recs[nid].node.kind is NodeKind.POI_GRID]
        while True:
            free = [nid for nid in movable if nid not in placed]
            if not free:
                break
            if poi:
                comp_roots, poi = poi, []
            else:
                comp_roots = [nid for nid in free if not any(e.dst_node not in placed for e in down[nid])][:1] or free[:1]
            roots += comp_roots
            self._rank_component(comp_roots, placed)
        self.roots = roots
        for n in self.control_only:
            host = next(
                (
                    e.src_node if e.dst_node == n.id else e.dst_node
                    for e in self.net.edges
                    if e.service is Service.CONTROL and n.id in (e.src_node, e.dst_node)
                    and (e.src_node if e.dst_node == n.id else e.dst_node) in self.recs
                    and (e.src_node if e.dst_node == n.id else e.dst_node) not in {c.id for c in self.control_only}
                ),
                None,
            )
            if host is None:
                self.warnings.append(f"{n.id}: control-only node with no host on this sheet; not drawn")
                continue
            e = next(e for e in self.net.edges if e.service is Service.CONTROL and {e.src_node, e.dst_node} == {n.id, host})
            host_port = e.src_port if e.src_node == host else e.dst_port
            self.sat_of[n.id] = host
            self.recs[host].sats.append(_Sat(n.id, e, host_port, "control"))
        for e in self.net.edges:
            if e.service is Service.EARTH:
                for a, b in ((e.src_node, e.dst_node), (e.dst_node, e.src_node)):
                    if b in self.earth_nodes and a in self.recs and a not in self.sat_of:
                        self.recs[a].earth = True
        for rec in self.recs.values():
            rec.sats.sort(key=lambda s: (s.kind == "tap", not self._west_pinned(s), self.order[s.edge.id]))
            rec.children.sort(key=lambda c: (not self.recs[c].in_r, self.order[self.recs[c].tree_edge.id]))

    def _west_pinned(self, s: _Sat) -> bool:
        rec = self.recs[s.node_id]
        sym = _symbol(rec.key)
        port = s.edge.dst_port if s.edge.dst_node == s.node_id else s.edge.src_port
        pin = _map_ports(rec.node, sym).get(port)
        return any(p.name == pin and p.side is Side.W for p in sym.pins)

    def _rank_component(self, roots: list[str], placed: set[str]) -> None:
        reach: set[str] = set()
        frontier = list(roots)
        while frontier:
            nid = frontier.pop()
            if nid in reach:
                continue
            reach.add(nid)
            frontier += [e.src_node for e in self.up[nid] if e.src_node not in placed]
        memo: dict[str, int] = {}

        def rank(nid: str) -> int:
            if nid in memo:
                return memo[nid]
            if nid in roots:
                memo[nid] = 0
                return 0
            best, best_e = -1, None
            for e in self.down[nid]:
                if e.dst_node in reach:
                    r = rank(e.dst_node)
                    if r > best:
                        best, best_e = r, e
            memo[nid] = best + 1
            rec = self.recs[nid]
            rec.parent, rec.tree_edge = best_e.dst_node, best_e
            return memo[nid]

        for nid in sorted(reach, key=lambda n: list(self.recs).index(n)):
            self.recs[nid].rank = rank(nid)
            self.recs[nid].in_r = True
        placed |= reach
        for nid in sorted(reach, key=lambda n: list(self.recs).index(n)):
            rec = self.recs[nid]
            if rec.parent is not None:
                self.recs[rec.parent].children.append(nid)
        changed = True
        while changed:
            changed = False
            for e in sorted(self.power_edges, key=lambda e: self.order[e.id]):
                if e.src_node in placed and e.dst_node not in placed and e.dst_node not in self.sat_of:
                    rec = self.recs[e.dst_node]
                    rec.parent, rec.tree_edge, rec.rank = e.src_node, e, self.recs[e.src_node].rank + 1
                    self.recs[e.src_node].children.append(e.dst_node)
                    placed.add(e.dst_node)
                    changed = True

    # ---------------------------------------------------------------- geometry
    def _values(self, node: Node, key: str) -> tuple[tuple[str, str], ...]:
        texts = annotate.node_texts(self.net, node, self.catalog)
        return annotate.symbol_values(texts, tuple(a.tag for a in _symbol(key).attrs))

    def _ports(self, nid: str) -> tuple[set[str], set[str]]:
        rec = self.recs[nid]
        up, down = set(), set()
        e = rec.tree_edge
        if e is not None:
            up.add(e.src_port if e.src_node == nid else e.dst_port)
        for c in rec.children:
            ce = self.recs[c].tree_edge
            down.add(ce.dst_port if ce.dst_node == nid else ce.src_port)
        return up, down

    def _anchored(self, key: str, rotation: float, values, anchor_pin: str | None) -> _Geom:
        g = _geom(key, rotation, values)
        if anchor_pin is not None and anchor_pin in g.pins:
            ax, ay = g.pins[anchor_pin][0]
        else:
            ax, ay = (g.body.x0 + g.body.x1) / 2, g.body.y1
        return g.shifted(-ax, -ay)

    def geometry(self) -> None:
        for nid, rec in self.recs.items():
            if rec.is_bus or nid in self.sat_of:
                continue
            sym = _symbol(rec.key)
            up, down = self._ports(nid)
            rot, mapping = _orient(sym, _map_ports(rec.node, sym), up, down)
            rec.mapping = mapping
            anchor = mapping.get(next(iter(up))) if up else (mapping.get(sorted(down)[0]) if down else None)
            g = self._anchored(rec.key, rot, self._values(rec.node, rec.key), anchor)
            if not up and anchor is not None:
                g = g.shifted(0, -g.body.y1)
            rec.geom = g
            boxes = [g.body] + [lb.box for lb in g.labels]
            ext = union_all(boxes)
            if rec.earth:
                ext = self._earth(rec, ext, right=rot == 180.0)
            rec.ctrl_x = _ceil_grid(ext.x1 + STUB)
            cursor = rec.ctrl_x + STUB
            for s in rec.sats:
                srec = self.recs[s.node_id]
                ssym = _symbol(srec.key)
                smap = _map_ports(srec.node, ssym)
                sport = s.edge.dst_port if s.edge.dst_node == s.node_id else s.edge.src_port
                s.pin = smap.get(sport, ssym.pins[0].name if ssym.pins else "")
                srec.mapping = smap
                sg = self._anchored(srec.key, 0.0, self._values(srec.node, srec.key), s.pin)
                side = sg.pins[s.pin][1] if s.pin in sg.pins else Side.N
                dy = 0.0
                if s.kind == "control":
                    host_pin = mapping.get(s.host_port)
                    hy = g.pins[host_pin][0][1] if host_pin in g.pins else g.body.y0
                    dy = hy if side is Side.W else 0.0
                left = min([sg.body.x0] + [lb.box.x0 for lb in sg.labels])
                sg = sg.shifted(cursor - left + (STUB if side is Side.W else 0), dy)
                s.geom = sg
                srec.geom = sg
                sext = union_all([sg.body] + [lb.box for lb in sg.labels])
                ext = ext.union(sext)
                cursor = sext.x1 + STUB * 2
            west = [s.geom.body.y1 for s in rec.sats if s.kind == "control" and s.geom.pins[s.pin][1] is Side.W]
            rec.ctrl_y = _ceil_grid(max([0.0] + west) + STUB)
            if any(s.kind == "control" for s in rec.sats):
                ext = ext.union(Box(ext.x0, ext.y0, ext.x1, rec.ctrl_y + STUB / 2))
            rec.extent = ext
        for rec in self.recs.values():
            lines = annotate.cable_callout(rec.tree_edge, self.catalog) if rec.tree_edge else ()
            if len(lines) > self.o.callout_lines:
                lines = lines[: self.o.callout_lines - 1] + (", ".join(lines[self.o.callout_lines - 1 :]),)
            rec.callout = lines

    def _earth(self, rec: _Rec, ext: Box, right: bool) -> Box:
        g = rec.geom
        mid = (g.body.y0 + g.body.y1) / 2
        esym = _symbol(EARTH_SYMBOL)
        epin = esym.pins[0].name
        eg = _geom(EARTH_SYMBOL, 0.0, tuple((a.tag, "") for a in esym.attrs))
        px, py = eg.pins[epin][0]
        if right:
            x = ext.x1 + STUB + esym.width / 2
            start = (g.body.x1, mid)
        else:
            x = ext.x0 - STUB - esym.width / 2
            start = (g.body.x0, mid)
        eg = eg.shifted(x - px, mid - STUB - py)
        rec.earth_geom = eg
        rec.earth_wire = (start, (x, mid), eg.pins[epin][0])
        return ext.union(eg.body)

    # ---------------------------------------------------------------- tidy x
    def _members(self, g: Group) -> frozenset[str]:
        out = set()
        for nid, rec in self.recs.items():
            gid = rec.node.group
            while gid is not None:
                if gid == g.id:
                    out.add(nid)
                    break
                gid = self.net.group_by_id[gid].within
        return frozenset(out)

    def _subtree_nodes(self, nid: str) -> set[str]:
        out = {nid}
        rec = self.recs[nid]
        out |= {s.node_id for s in rec.sats}
        for c in rec.children:
            out |= self._subtree_nodes(c)
        return out

    def _subtree(self, nid: str) -> Contour:
        o = self.o
        rec = self.recs[nid]
        contours: list[Contour] = []
        for c in rec.children:
            cc = dict(self._subtree(c))
            crec = self.recs[c]
            if crec.callout:
                w = max(text_width(t, o.callout_height) for t in crec.callout)
                lvl = 2 * crec.rank - 1
                lo, hi = cc.get(lvl, (0.0, 0.0))
                cc[lvl] = (min(lo, -w - STUB), max(hi, 0.0))
            contours.append(cc)
        offsets: list[float] = []
        merged: Contour = {}
        for cc in contours:
            off = 0.0
            if offsets:
                off = max(
                    (merged[lv][1] + o.h_gap - cc[lv][0] for lv in cc if lv in merged),
                    default=offsets[-1] + o.h_gap,
                )
            off = _ceil_grid(off)
            offsets.append(off)
            for lv, (lo, hi) in cc.items():
                if lv in merged:
                    merged[lv] = (min(merged[lv][0], lo + off), max(merged[lv][1], hi + off))
                else:
                    merged[lv] = (lo + off, hi + off)
        anchor = (offsets[0] + offsets[-1]) / 2 if offsets else 0.0
        if offsets and not rec.is_bus:
            anchor -= self._down_pin_x(nid)
        anchor = _snap(anchor)
        contour: Contour = {lv: (lo - anchor, hi - anchor) for lv, (lo, hi) in merged.items()}
        for c, off in zip(rec.children, offsets):
            rec.child_dx[c] = off - anchor
        lvl = 2 * rec.rank
        if rec.is_bus:
            xs = list(rec.child_dx.values()) or [0.0]
            x0, x1 = min(xs) - 2 * STUB, max(xs) + 2 * STUB
            lines = annotate.bus_texts(self.net, rec.node)
            h = o.bus_label_height
            w = max(text_width(t, h) for t in lines)
            x0 = _floor_grid(min(x0, -w - 2 * STUB))
            if rec.earth:
                x1 += 2 * STUB
            rec.bus_x = (x0, x1)
            ext = Box(x0, -15.0 if rec.earth else 0.0, x1 + (5.0 if rec.earth else 0.0), 0.0)
            rec.extent = ext
        lo, hi = contour.get(lvl, (math.inf, -math.inf))
        contour[lvl] = (min(lo, rec.extent.x0), max(hi, rec.extent.x1))
        taps = [s for s in rec.sats if s.kind == "tap"]
        if taps:
            tlo, thi = contour.get(lvl - 1, (0.0, 0.0))
            contour[lvl - 1] = (min(tlo, 0.0), max(thi, rec.extent.x1))
        self._frames_at(nid, contour)
        return contour

    def _down_pin_x(self, nid: str) -> float:
        rec = self.recs[nid]
        for c in rec.children:
            e = self.recs[c].tree_edge
            port = e.dst_port if e.dst_node == nid else e.src_port
            pin = rec.mapping.get(port)
            if rec.geom and pin in rec.geom.pins:
                return rec.geom.pins[pin][0][0]
        return 0.0

    def _frames_at(self, nid: str, contour: Contour) -> None:
        o = self.o
        rec = self.recs[nid]
        chain: list[Group] = []
        gid = rec.node.group
        while gid is not None:
            chain.append(self.net.group_by_id[gid])
            gid = self.net.group_by_id[gid].within
        parent_groups: set[str] = set()
        if rec.parent is not None:
            gid = self.recs[rec.parent].node.group
            while gid is not None:
                parent_groups.add(gid)
                gid = self.net.group_by_id[gid].within
        sub = None
        for g in chain:
            if g.id in parent_groups:
                continue
            members = self._members(g)
            sub = sub if sub is not None else self._subtree_nodes(nid)
            if not (sub <= members and members <= sub):
                self.warnings.append(f"group {g.id!r} does not form one subtree on this sheet; drawn without a frame")
                continue
            lines = self._frame_lines(g)
            top = rec.rank
            bottom = max(self.recs[m].rank for m in sub if m not in self.sat_of)
            x0 = min(lo for lo, _ in contour.values()) - o.frame_pad
            x1 = max(hi for _, hi in contour.values()) + o.frame_pad
            tw = max(text_width(t, o.frame_title_height) for t in lines)
            x1 = max(x1, x0 + tw + 2 * STUB)
            for lv in range(2 * top - 1, 2 * bottom + 1):
                lo, hi = contour.get(lv, (x0, x1))
                contour[lv] = (min(lo, x0), max(hi, x1))
            self.frames.append(_FrameRec(g, nid, x0, x1, top, bottom, lines, frozenset(sub)))

    def _frame_lines(self, g: Group) -> tuple[str, ...]:
        rng = annotate.group_label(self.net, g)
        head = f"{rng}  (x {g.count})" if g.count > 1 else rng
        return (g.title.upper(), head) if g.title else (head,)

    def place_x(self) -> None:
        o = self.o
        merged: Contour = {}
        last = None
        for r in self.roots:
            cc = self._subtree(r)
            off = 0.0
            if last is not None:
                off = _ceil_grid(max((merged[lv][1] + 2 * o.h_gap - cc[lv][0] for lv in cc if lv in merged), default=last + 2 * o.h_gap))
            for lv, (lo, hi) in cc.items():
                merged[lv] = (min(merged.get(lv, (math.inf, -math.inf))[0], lo + off), max(merged.get(lv, (math.inf, -math.inf))[1], hi + off))
            self._assign_x(r, off)
            last = off

    def _assign_x(self, nid: str, x: float) -> None:
        rec = self.recs[nid]
        rec.x = _snap(x)
        for c in rec.children:
            self._assign_x(c, x + rec.child_dx[c])

    # ---------------------------------------------------------------- y
    def place_y(self) -> None:
        o = self.o
        pitch = LINE_PITCH * o.callout_height
        tpitch = LINE_PITCH * o.frame_title_height
        ranks = sorted({rec.rank for nid, rec in self.recs.items() if nid not in self.sat_of})
        above: dict[int, float] = defaultdict(float)
        below: dict[int, float] = defaultdict(float)
        callout: dict[int, float] = defaultdict(float)
        jog: dict[int, bool] = defaultdict(bool)
        tap: dict[int, bool] = defaultdict(bool)
        for nid, rec in self.recs.items():
            if nid in self.sat_of:
                continue
            r = rec.rank
            if rec.is_bus:
                n = len(annotate.bus_texts(self.net, rec.node))
                above[r] = max(above[r], n * LINE_PITCH * o.bus_label_height + STUB)
                below[r] = max(below[r], -rec.extent.y0)
            else:
                above[r] = max(above[r], rec.extent.y1)
                below[r] = max(below[r], -rec.extent.y0)
            if rec.callout:
                callout[r] = max(callout[r], len(rec.callout) * pitch + 1.0)
            if any(s.kind == "tap" for s in rec.sats):
                tap[r] = True
            if rec.parent is not None and not self.recs[rec.parent].is_bus:
                e = rec.tree_edge
                pport = e.dst_port if e.src_node == nid else e.src_port
                prec = self.recs[rec.parent]
                pin = prec.mapping.get(pport)
                px = prec.x + (prec.geom.pins[pin][0][0] if prec.geom and pin in prec.geom.pins else 0.0)
                if abs(px - rec.x) > 1e-6:
                    jog[r] = True
        for f in self.frames:
            f.i_top = sum(1 for g in self.frames if g.top == f.top and g.members <= f.members)
            f.i_bottom = sum(1 for g in self.frames if g.bottom == f.bottom and g.members <= f.members)
        top_n: dict[int, int] = defaultdict(int)
        bot_n: dict[int, int] = defaultdict(int)
        for f in self.frames:
            top_n[f.top] = max(top_n[f.top], f.i_top)
            bot_n[f.bottom] = max(bot_n[f.bottom], f.i_bottom)
        self.band_top: dict[int, float] = {}
        self.pin_y: dict[int, float] = {}
        self.jog_y: dict[int, float] = {}
        self.callout_top: dict[int, float] = {}
        self.tap_h: dict[int, float] = {}
        self.items_bottom: dict[int, float] = {}
        y = 0.0
        for i, r in enumerate(ranks):
            if i:
                y -= o.channel_gap / 2
                if jog[r]:
                    self.jog_y[r] = _floor_grid(y - o.jog_band / 2)
                    y = self.jog_y[r] - o.jog_band / 2
                self.callout_top[r] = y
                y -= callout[r]
            y -= top_n[r] * o.frame_pad
            self.tap_h[r] = o.tap_band if tap[r] else 0.0
            y -= self.tap_h[r] + (o.channel_gap / 2 if i else 0)
            self.band_top[r] = y
            self.pin_y[r] = _floor_grid(y - above[r])
            self.items_bottom[r] = self.pin_y[r] - below[r]
            y = self.items_bottom[r] - bot_n[r] * (o.frame_pad + 2 * tpitch + 1.0)
        self.tpitch = tpitch
        for nid, rec in self.recs.items():
            if nid not in self.sat_of:
                rec.y = self.pin_y[rec.rank]

    # ---------------------------------------------------------------- emit
    def _abs(self, rec: _Rec, p: Point) -> Point:
        return (round(rec.x + p[0], 6) + 0.0, round(rec.y + p[1], 6) + 0.0)

    def _pin(self, nid: str, port: str) -> PinRef | None:
        rec = self.recs.get(nid)
        if rec is None:
            return None
        host = self.recs[self.sat_of[nid]] if nid in self.sat_of and self.recs[self.sat_of[nid]].geom else rec
        g = rec.geom
        if g is None:
            return None
        pin = rec.mapping.get(port)
        if pin is None or pin not in g.pins:
            return None
        base = host if nid in self.sat_of else rec
        return PinRef(nid, pin, self._abs(base, g.pins[pin][0]))

    def _side_of(self, nid: str, port: str) -> Side | None:
        rec = self.recs[nid]
        pin = rec.mapping.get(port)
        return rec.geom.pins[pin][1] if rec.geom and pin in rec.geom.pins else None

    def emit(self) -> SldLayout:
        o = self.o
        symbols: list[PlacedSymbol] = []
        busbars: list[Busbar] = []
        wires: list[Wire] = []
        callouts: list[Callout] = []
        represented: list[tuple[str, str]] = []
        bodies: list[Box] = []
        for nid, rec in self.recs.items():
            if rec.is_bus:
                continue
            if rec.geom is None:
                continue
            base = self.recs[self.sat_of[nid]] if nid in self.sat_of else rec
            g = rec.geom
            symbols.append(self._placed(nid, nid, g, base, "node"))
            represented.append((nid, "satellite" if nid in self.sat_of else "symbol"))
            if rec.earth_geom is not None:
                symbols.append(self._placed(f"{nid}~pe", None, rec.earth_geom, rec, "earth"))
                pts = tuple(self._abs(rec, p) for p in rec.earth_wire)
                epin = rec.earth_geom.pins[_symbol(EARTH_SYMBOL).pins[0].name][0]
                wires.append(Wire(f"{nid}~pe", WireKind.EARTH, pts, None, PinRef(f"{nid}~pe", "pe", self._abs(rec, epin))))
        for nid, rec in self.recs.items():
            if not rec.is_bus or nid in self.sat_of:
                continue
            x0, x1 = rec.x + rec.bus_x[0], rec.x + rec.bus_x[1]
            h = o.bus_label_height
            lines = annotate.bus_texts(self.net, rec.node)
            labels = tuple(
                label(t, (x0 + STUB, rec.y + STUB + (len(lines) - 1 - i) * LINE_PITCH * h + h / 2), h)
                for i, t in enumerate(lines)
            )
            busbars.append(Busbar(nid, rec.y, round(x0, 6), round(x1, 6), labels))
            represented.append((nid, "busbar"))
            if rec.earth:
                esym = _symbol(EARTH_SYMBOL)
                eg = _geom(EARTH_SYMBOL, 0.0, tuple((a.tag, "") for a in esym.attrs))
                px, py = eg.pins[esym.pins[0].name][0]
                ex = rec.bus_x[1]
                eg = eg.shifted(ex - px, -2 * STUB - py)
                rec.earth_geom = eg
                symbols.append(self._placed(f"{nid}~pe", None, eg, rec, "earth"))
                pin = self._abs(rec, eg.pins[esym.pins[0].name][0])
                wires.append(Wire(f"{nid}~pe", WireKind.EARTH, ((x1, rec.y), pin), None, PinRef(f"{nid}~pe", "pe", pin)))
        drawn_edges: set[str] = set()
        for nid, rec in self.recs.items():
            e = rec.tree_edge
            if e is None or nid in self.sat_of:
                continue
            parent = self.recs[rec.parent]
            child_port = e.src_port if e.src_node == nid else e.dst_port
            parent_port = e.dst_port if e.src_node == nid else e.src_port
            cpin = self._pin(nid, child_port) if not rec.is_bus else None
            cx = rec.x if rec.is_bus else (cpin.at[0] if cpin else rec.x)
            cy = rec.y
            start = cpin or PinRef(nid, "bus", (cx, cy))
            if parent.is_bus:
                end = PinRef(parent.node.id, "bus", (cx, parent.y))
                pts = [start.at, end.at]
            else:
                ppin = self._pin(parent.node.id, parent_port)
                if ppin is None:
                    self.warnings.append(f"edge {e.id!r}: port {parent_port!r} has no pin on {parent.key}")
                    continue
                end = ppin
                jy = self.jog_y.get(rec.rank, (cy + ppin.at[1]) / 2)
                pts = [start.at, (cx, jy), (ppin.at[0], jy), ppin.at]
            if e.src_node != nid:
                pts.reverse()
                start, end = end, start
            wires.append(Wire(e.id, _kind(e), _clean(pts), start, end))
            drawn_edges.add(e.id)
            if rec.callout:
                top = self.callout_top[rec.rank]
                pitch = LINE_PITCH * o.callout_height
                labels = tuple(
                    label(t, (cx - STUB, top - 0.5 - (i + 0.5) * pitch), o.callout_height, "right")
                    for i, t in enumerate(rec.callout)
                )
                tick = (cx, top - 0.5 - len(rec.callout) * pitch / 2)
                callouts.append(Callout(e.id, tick, labels))
        junction_taps: list[Point] = []
        for nid, rec in self.recs.items():
            for s in rec.sats:
                e = s.edge
                hpin = self._pin(nid, s.host_port)
                spin = self._pin(s.node_id, e.dst_port if e.dst_node == s.node_id else e.src_port)
                if hpin is None or spin is None:
                    self.warnings.append(f"edge {e.id!r}: no pin to attach {s.node_id}")
                    continue
                if s.kind == "tap" and hpin.at[1] >= rec.y - 1e-6:
                    ty = _snap(self.band_top[rec.rank] + self.tap_h[rec.rank] / 2)
                    pts = [hpin.at, (hpin.at[0], ty), (spin.at[0], ty), spin.at]
                    junction_taps.append((hpin.at[0], ty))
                elif s.kind == "control" and abs(spin.at[1] - hpin.at[1]) < 1e-6:
                    pts = [hpin.at, spin.at]
                elif s.kind == "control":
                    xv, yc = rec.x + rec.ctrl_x, rec.y + rec.ctrl_y
                    pts = [hpin.at, (xv, hpin.at[1]), (xv, yc), (spin.at[0], yc), spin.at]
                else:
                    pts = list(self._route(hpin, self._side_of(nid, s.host_port), spin, self._side_of(s.node_id, s.pin), bodies))
                if e.src_node != nid:
                    pts.reverse()
                    hpin, spin = spin, hpin
                wires.append(Wire(e.id, _kind(e), _clean(pts), hpin, spin))
                drawn_edges.add(e.id)
        frames = self._emit_frames()
        bodies = [s.body for s in symbols] + [lb.box for s in symbols for lb in s.labels]
        bodies += [lb.box for x in (*busbars, *callouts, *frames) for lb in x.labels]
        for e in self.net.edges:
            if e.id in drawn_edges or e.service is Service.EARTH:
                continue
            if e.src_node not in self.recs or e.dst_node not in self.recs:
                continue
            a, b = self._pin(e.src_node, e.src_port), self._pin(e.dst_node, e.dst_port)
            if a is None or b is None:
                a = a or self._bus_ref(e.src_node, b)
                b = b or self._bus_ref(e.dst_node, a)
            if a is None or b is None:
                self.warnings.append(f"edge {e.id!r} could not be drawn: missing pin")
                continue
            sa = self._side_of(e.src_node, e.src_port) if not self.recs[e.src_node].is_bus else Side.S
            sb = self._side_of(e.dst_node, e.dst_port) if not self.recs[e.dst_node].is_bus else Side.S
            pts = self._route(a, sa, b, sb, bodies)
            wires.append(Wire(e.id, _kind(e), _clean(list(pts)), a, b))
        junctions = _junctions(wires)
        boxes = [s.body for s in symbols] + [lb.box for s in symbols for lb in s.labels]
        boxes += [Box(b.x0, b.y - 0.5, b.x1, b.y + 0.5) for b in busbars] + [lb.box for b in busbars for lb in b.labels]
        boxes += [lb.box for c in callouts for lb in c.labels] + [f.box for f in frames] + [lb.box for f in frames for lb in f.labels]
        boxes += [Box(min(p[0] for p in w.points), min(p[1] for p in w.points), max(p[0] for p in w.points), max(p[1] for p in w.points)) for w in wires]
        extent = union_all(boxes) if boxes else Box(0, 0, 1, 1)
        offsheets = tuple(OffSheet(e.id, e.src_node if e.src_node in self.recs else e.dst_node, "") for e in self.cut_edges)
        return SldLayout(
            symbols=tuple(symbols),
            busbars=tuple(busbars),
            wires=tuple(wires),
            callouts=tuple(callouts),
            frames=tuple(frames),
            junctions=junctions,
            offsheets=offsheets,
            represented=tuple(represented),
            extent=extent,
            warnings=tuple(self.warnings),
        )

    def _bus_ref(self, nid: str, other: PinRef | None) -> PinRef | None:
        rec = self.recs[nid]
        if not rec.is_bus or other is None:
            return None
        x = min(max(other.at[0], rec.x + rec.bus_x[0]), rec.x + rec.bus_x[1])
        return PinRef(nid, "bus", (x, rec.y))

    def _placed(self, sid: str, nid: str | None, g: _Geom, base: _Rec, role: str) -> PlacedSymbol:
        dx, dy = base.x, base.y
        sh = g.shifted(dx, dy)
        at = (round(dx + _origin(g)[0], 6) + 0.0, round(dy + _origin(g)[1], 6) + 0.0)
        pins = tuple(PinRef(sid if nid is None else nid, p, (round(a[0], 6) + 0.0, round(a[1], 6) + 0.0)) for p, (a, _) in sh.pins.items())
        return PlacedSymbol(sid, nid, g.key, at, g.rotation, g.values, tuple(sh.labels), sh.body, pins, role)

    def _route(self, a: PinRef, sa: Side | None, b: PinRef, sb: Side | None, bodies: list[Box]) -> tuple[Point, ...]:
        def stub(p: Point, s: Side | None) -> Point:
            d = {Side.N: (0, STUB), Side.S: (0, -STUB), Side.E: (STUB, 0), Side.W: (-STUB, 0)}.get(s, (0, 0))
            return (p[0] + d[0], p[1] + d[1])

        a1, b1 = stub(a.at, sa), stub(b.at, sb)
        xs = sorted({a1[0], b1[0]} | {bx for bd in bodies for bx in (bd.x0 - STUB, bd.x1 + STUB) if min(a1[0], b1[0]) - 60 <= bx <= max(a1[0], b1[0]) + 60})
        ys = sorted({a1[1], b1[1]} | {by for bd in bodies for by in (bd.y0 - STUB, bd.y1 + STUB) if min(a1[1], b1[1]) - 60 <= by <= max(a1[1], b1[1]) + 60})
        cands: list[list[Point]] = []
        for x in xs:
            cands.append([a.at, a1, (x, a1[1]), (x, b1[1]), b1, b.at])
        for y in ys:
            cands.append([a.at, a1, (a1[0], y), (b1[0], y), b1, b.at])

        def cost(pts: list[Point]) -> tuple[int, float]:
            hits = 0
            for p, q in zip(pts[1:-2], pts[2:-1]):
                seg = Box(min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))
                hits += sum(1 for bd in bodies if _seg_hits(seg, bd))
            length = sum(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(pts, pts[1:]))
            return hits, round(length, 6)

        return tuple(min(cands, key=cost))

    def _emit_frames(self) -> list[Frame]:
        o = self.o
        out = []
        for f in self.frames:
            root = self.recs[f.root]
            x0, x1 = root.x + f.x0, root.x + f.x1
            y1 = self.band_top[f.top] + self.tap_h[f.top] + f.i_top * o.frame_pad
            y0 = self.items_bottom[f.bottom] - f.i_bottom * o.frame_pad - (f.i_bottom - 1) * (2 * self.tpitch + 1.0)
            h = o.frame_title_height
            labels = tuple(label(t, (x0 + STUB, y0 - 1.0 - (i + 0.5) * self.tpitch), h) for i, t in enumerate(f.lines))
            out.append(Frame(f.group.id, Box(round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)), labels, f.group.count))
        return out


def _snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 6) + 0.0


def _ceil_grid(v: float) -> float:
    return round(math.ceil(v / GRID - 1e-9) * GRID, 6) + 0.0


def _floor_grid(v: float) -> float:
    return round(math.floor(v / GRID + 1e-9) * GRID, 6) + 0.0


def _origin(g: _Geom) -> Point:
    """Insertion point of a geometry: _geom puts it at the origin, shifts move it with everything else."""
    sym = _symbol(g.key)
    ref = sym.pins[0] if sym.pins else None
    if ref is None:
        corners = [_rot(x, y, g.rotation) for x, y in ((0, 0), (sym.width, 0), (sym.width, sym.height), (0, sym.height))]
        return g.body.x0 - min(c[0] for c in corners), g.body.y0 - min(c[1] for c in corners)
    local = pin_at(g.key, ref.name, (0, 0), g.rotation)
    at = g.pins[ref.name][0]
    return at[0] - local[0], at[1] - local[1]


def _seg_hits(seg: Box, body: Box) -> bool:
    return seg.x0 < body.x1 - 1e-6 and body.x0 < seg.x1 - 1e-6 and seg.y0 < body.y1 - 1e-6 and body.y0 < seg.y1 - 1e-6 or (
        (seg.x0 == seg.x1 and body.x0 + 1e-6 < seg.x0 < body.x1 - 1e-6 and seg.y0 < body.y1 and body.y0 < seg.y1)
        or (seg.y0 == seg.y1 and body.y0 + 1e-6 < seg.y0 < body.y1 - 1e-6 and seg.x0 < body.x1 and body.x0 < seg.x1)
    )


def _kind(e: Edge) -> WireKind:
    if e.service is Service.DC:
        return WireKind.DC
    if e.service is Service.CONTROL:
        return WireKind.CONTROL
    if e.service is Service.EARTH:
        return WireKind.EARTH
    return WireKind.AC


def _clean(pts: list[Point]) -> tuple[Point, ...]:
    out: list[Point] = []
    for p in pts:
        p = (round(p[0], 6) + 0.0, round(p[1], 6) + 0.0)
        if out and abs(out[-1][0] - p[0]) < 1e-6 and abs(out[-1][1] - p[1]) < 1e-6:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs(a[0] - b[0]) < 1e-6 and abs(b[0] - p[0]) < 1e-6) or (abs(a[1] - b[1]) < 1e-6 and abs(b[1] - p[1]) < 1e-6):
                out[-1] = p
                continue
        out.append(p)
    return tuple(out)


def _junctions(wires: list[Wire]) -> tuple[Point, ...]:
    by_kind: dict[WireKind, list[tuple[Point, Point]]] = defaultdict(list)
    for w in wires:
        by_kind[w.kind] += list(zip(w.points, w.points[1:]))
    found: set[Point] = set()
    for segs in by_kind.values():
        vertices = {p for s in segs for p in s}
        for v in vertices:
            dirs = set()
            for p, q in segs:
                if not _on_segment(v, p, q):
                    continue
                for end in (p, q):
                    if end != v:
                        dirs.add((_sign(end[0] - v[0]), _sign(end[1] - v[1])))
            if len(dirs) >= 3:
                found.add(v)
    return tuple(sorted(found))


def _sign(v: float) -> int:
    return 0 if abs(v) < 1e-6 else (1 if v > 0 else -1)


def _on_segment(v: Point, p: Point, q: Point) -> bool:
    if abs(p[0] - q[0]) < 1e-6:
        return abs(v[0] - p[0]) < 1e-6 and min(p[1], q[1]) - 1e-6 <= v[1] <= max(p[1], q[1]) + 1e-6
    return abs(v[1] - p[1]) < 1e-6 and min(p[0], q[0]) - 1e-6 <= v[0] <= max(p[0], q[0]) + 1e-6


def layout_sld(
    network: ElectricalNetwork,
    catalog: Catalog | None = None,
    options: SldOptions = SldOptions(),
    scope: frozenset[str] | None = None,
) -> SldLayout:
    """Place `network` (template level: groups drawn once) as an SLD; `scope` limits it to some nodes."""
    return _build(network, catalog, options, scope)[1]


def _build(network: ElectricalNetwork, catalog: Catalog | None, options: SldOptions, scope: frozenset[str] | None) -> tuple[_Builder, SldLayout]:
    b = _Builder(network, catalog, options, scope)
    b.topology()
    b.geometry()
    b.place_x()
    b.place_y()
    return b, b.emit()


def plan_pages(
    network: ElectricalNetwork,
    catalog: Catalog | None,
    width: float,
    height: float,
    options: SldOptions = SldOptions(),
    scope: frozenset[str] | None = None,
) -> tuple[SldPage, ...]:
    """Split plan for a drawing area: one page when the SLD is readable there, else the first
    node with several feeders stays on a main page and its feeder subtrees are packed onto
    further pages. Cross-reference markers on the cut edges are the pagination unit's job."""
    b, lay = _build(network, catalog, options, scope)
    nodes = frozenset(b.recs)

    def page(ids: frozenset[str], lay: SldLayout) -> SldPage:
        cut = tuple(e.id for e in network.edges if e.is_power and (e.src_node in ids) != (e.dst_node in ids))
        return SldPage(tuple(n for n in b.recs if n in ids), cut, lay.fit_scale(width, height, options))

    if lay.fit_scale(width, height, options) >= options.min_scale:
        return (page(nodes, lay),)
    split = None
    frontier = list(b.roots)
    while frontier and split is None:
        nxt = []
        for nid in frontier:
            if len(b.recs[nid].children) > 1:
                split = nid
                break
            nxt += b.recs[nid].children
        frontier = nxt
    if split is None:
        return (page(nodes, lay),)
    subtrees = [frozenset(b._subtree_nodes(c)) for c in b.recs[split].children]
    main = nodes - frozenset().union(*subtrees)
    pages = [page(main, layout_sld(network, catalog, options, main))]
    current: frozenset[str] = frozenset()
    for sub in subtrees:
        trial = current | sub
        if current and layout_sld(network, catalog, options, trial).fit_scale(width, height, options) < options.min_scale:
            pages += _flush(network, catalog, width, height, options, current)
            trial = sub
        current = trial
    if current:
        pages += _flush(network, catalog, width, height, options, current)
    return tuple(pages)


def _flush(network, catalog, width, height, options, ids: frozenset[str]) -> list[SldPage]:
    lay = layout_sld(network, catalog, options, ids)
    if lay.fit_scale(width, height, options) >= options.min_scale:
        cut = tuple(e.id for e in network.edges if e.is_power and (e.src_node in ids) != (e.dst_node in ids))
        return [SldPage(tuple(n.id for n in network.nodes if n.id in ids), cut, lay.fit_scale(width, height, options))]
    return list(plan_pages(network, catalog, width, height, options, ids))

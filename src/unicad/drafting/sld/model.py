"""The SLD layout: plain data in drawing millimetres at 1:1, no ezdxf.

y grows upwards. The POI sits on top and generation at the bottom, as on Indian
utility and rooftop SLDs (CIL 33 kV SLD, HeavenSolar C&I SLD) and PLAN.md section 5.
"""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Literal

Point = tuple[float, float]
Align = Literal["left", "right", "center"]


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def union(self, other: "Box") -> "Box":
        return Box(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))

    def shift(self, dx: float, dy: float) -> "Box":
        return Box(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def grow(self, d: float) -> "Box":
        return Box(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def overlaps(self, other: "Box", tol: float = 1e-6) -> bool:
        return self.x0 < other.x1 - tol and other.x0 < self.x1 - tol and self.y0 < other.y1 - tol and other.y0 < self.y1 - tol

    def contains(self, other: "Box", tol: float = 1e-6) -> bool:
        return self.x0 - tol <= other.x0 and other.x1 <= self.x1 + tol and self.y0 - tol <= other.y0 and other.y1 <= self.y1 + tol

    def contains_point(self, p: Point, tol: float = 1e-6) -> bool:
        return self.x0 - tol <= p[0] <= self.x1 + tol and self.y0 - tol <= p[1] <= self.y1 + tol


def union_all(boxes: "list[Box] | tuple[Box, ...]") -> Box:
    out = boxes[0]
    for b in boxes[1:]:
        out = out.union(b)
    return out


class WireKind(StrEnum):
    DC = "dc"
    AC = "ac"
    CONTROL = "control"
    COMMS = "comms"
    EARTH = "earth"


@dataclass(frozen=True)
class Label:
    """One line of single-line TEXT. `at` is the alignment point at mid-height."""

    text: str
    at: Point
    height: float
    align: Align = "left"
    box: Box = field(default=Box(0, 0, 0, 0))


@dataclass(frozen=True)
class PinRef:
    owner: str
    pin: str
    at: Point


@dataclass(frozen=True)
class PlacedSymbol:
    """A symbol insert. `labels` are the visible TEXTs `insert()` writes for `values`."""

    id: str
    node_id: str | None
    key: str
    at: Point
    rotation: float
    values: tuple[tuple[str, str], ...]
    labels: tuple[Label, ...]
    body: Box
    pins: tuple[PinRef, ...]
    role: Literal["node", "earth", "offsheet"] = "node"


@dataclass(frozen=True)
class Busbar:
    node_id: str
    y: float
    x0: float
    x1: float
    labels: tuple[Label, ...]


@dataclass(frozen=True)
class Wire:
    edge_id: str
    kind: WireKind
    points: tuple[Point, ...]
    start: PinRef | None
    end: PinRef | None


@dataclass(frozen=True)
class Callout:
    edge_id: str
    tick: Point
    labels: tuple[Label, ...]


@dataclass(frozen=True)
class Frame:
    """A group's dashed frame: `box`, or when the group is not one subtree, the rectilinear
    `outline` loops (counter-clockwise) around its members, with `box` their bounds."""

    group_id: str
    box: Box
    labels: tuple[Label, ...]
    multiplicity: int
    outline: tuple[tuple[Point, ...], ...] = ()


@dataclass(frozen=True)
class Stub:
    """A short open conductor from a spare terminal (a further LV winding), with its note."""

    node_id: str
    pin: str
    points: tuple[Point, ...]
    labels: tuple[Label, ...]
    kind: WireKind | None = None
    """None: a spare power terminal; COMMS or CONTROL: a link continued at a node drawn elsewhere on the sheet, `pin` naming the edge."""


@dataclass(frozen=True)
class OffSheet:
    """Where an edge leaves this page; `target` is the number of the sheet that continues it, when known."""

    edge_id: str
    node_id: str
    symbol_id: str
    target: str | None = None


MIN_PRINTED_TEXT_MM = 1.8
"""Smallest lettering height on the printed sheet: the smallest nominal height of the ISO 3098 lettering series."""
SMALLEST_TEXT_MM = 2.0
"""Smallest text the diagram is drawn with at full size (cable callouts, symbol ratings and descriptions)."""


LABEL_CHARS = 48
"""A symbol label longer than this is written over several lines, broken after its commas."""


@dataclass(frozen=True)
class SldOptions:
    h_gap: float = 7.5
    channel_gap: float = 5.0
    jog_band: float = 5.0
    tap_band: float = 5.0
    frame_pad: float = 3.75
    callout_height: float = 2.0
    callout_lines: int = 2
    bus_label_height: float = 2.5
    frame_title_height: float = 2.5
    min_scale: float = MIN_PRINTED_TEXT_MM / SMALLEST_TEXT_MM
    max_scale: float = 1.6
    margin: float = 5.0
    row_gap: float = 15.0
    """Space between the stacked rows of one sheet."""
    sat_lines: int = 1
    """Lines the control satellites of one host (relay, meters, logger beside a CT) are drawn in: two make the incomer narrower and taller."""

    def compact(self) -> "SldOptions":
        """Tighter channels and one-line cable callouts, for tall diagrams on small paper."""
        return replace(self, channel_gap=2.5, tap_band=3.75, jog_band=3.75, callout_lines=1, row_gap=7.5, h_gap=5.0)


@dataclass(frozen=True)
class SldLayout:
    symbols: tuple[PlacedSymbol, ...]
    busbars: tuple[Busbar, ...]
    wires: tuple[Wire, ...]
    callouts: tuple[Callout, ...]
    frames: tuple[Frame, ...]
    junctions: tuple[Point, ...]
    offsheets: tuple[OffSheet, ...]
    represented: tuple[tuple[str, str], ...]
    """(node id, how): 'symbol', 'busbar' or 'satellite' for every power node on the page."""
    extent: Box
    stubs: tuple[Stub, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    """Cable remarks too long for a callout, in order: callouts refer to them as NOTE 1, 2, ..."""
    headings: tuple[Label, ...] = ()
    """Free text over the diagram: the row headings of a diagram drawn in stacked rows."""

    def shifted(self, dx: float, dy: float) -> "SldLayout":
        """The same layout moved by (dx, dy)."""

        def p(q: Point) -> Point:
            return (q[0] + dx, q[1] + dy)

        def lbs(labels: tuple[Label, ...]) -> tuple[Label, ...]:
            return tuple(replace(lb, at=p(lb.at), box=lb.box.shift(dx, dy)) for lb in labels)

        def pin(r: PinRef | None) -> PinRef | None:
            return None if r is None else replace(r, at=p(r.at))

        return replace(
            self,
            symbols=tuple(replace(s, at=p(s.at), labels=lbs(s.labels), body=s.body.shift(dx, dy), pins=tuple(pin(r) for r in s.pins)) for s in self.symbols),
            busbars=tuple(replace(b, y=b.y + dy, x0=b.x0 + dx, x1=b.x1 + dx, labels=lbs(b.labels)) for b in self.busbars),
            wires=tuple(replace(w, points=tuple(map(p, w.points)), start=pin(w.start), end=pin(w.end)) for w in self.wires),
            callouts=tuple(replace(c, tick=p(c.tick), labels=lbs(c.labels)) for c in self.callouts),
            frames=tuple(replace(f, box=f.box.shift(dx, dy), labels=lbs(f.labels), outline=tuple(tuple(map(p, loop)) for loop in f.outline)) for f in self.frames),
            junctions=tuple(map(p, self.junctions)),
            extent=self.extent.shift(dx, dy),
            stubs=tuple(replace(st, points=tuple(map(p, st.points)), labels=lbs(st.labels)) for st in self.stubs),
            headings=lbs(self.headings),
        )

    def texts(self) -> tuple[Label, ...]:
        out: list[Label] = []
        for s in self.symbols:
            out += s.labels
        for b in self.busbars:
            out += b.labels
        for c in self.callouts:
            out += c.labels
        for f in self.frames:
            out += f.labels
        for st in self.stubs:
            out += st.labels
        return tuple(out) + self.headings

    def legend_keys(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for s in self.symbols:
            if s.role != "offsheet":
                seen.setdefault(s.key, None)
        return tuple(seen)

    def fit_scale(self, width: float, height: float, options: SldOptions = SldOptions()) -> float:
        w = self.extent.width + 2 * options.margin
        h = self.extent.height + 2 * options.margin
        return min(options.max_scale, width / w, height / h)


@dataclass(frozen=True)
class SldPage:
    """One sheet of a split SLD: the nodes it owns (a device drawn on several pages is owned by the first) and the edges cut at its border."""

    node_ids: tuple[str, ...]
    cut_edges: tuple[str, ...]
    scale: float

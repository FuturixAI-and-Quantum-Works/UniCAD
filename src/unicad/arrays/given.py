"""One block per inverter when the strings are given per inverter (inverter_strings): blocks that hold exactly each inverter's strings, with tables as
long as each string where the plant mixes string lengths."""

import math
from collections import Counter
from collections.abc import Sequence

from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from unicad.arrays.blocks import WIDE_GAP_RATIO, _median, compact
from unicad.arrays.geometry import frame, table_centre, table_polygon
from unicad.arrays.place import INSIDE_TOL, PlacementError
from unicad.arrays.spec import FillOrder, PlacementSpec
from unicad.domain.layout import Point, Table

__all__ = ["GivenPlan", "fit_given"]

SWAP_WINDOW = 30


class GivenPlan:
    """The strings of every inverter by length: `main` is the most common length (the table length), `per_inverter[k]` counts inverter k's strings by length."""

    def __init__(self, inverter_strings: Sequence[Sequence[int]], rows: int):
        self.rows = rows
        self.per_inverter = [Counter(s) for s in inverter_strings]
        total = Counter(n for c in self.per_inverter for n in c.elements())
        self.main = min(total, key=lambda n: (-total[n], n))
        self.lengths = frozenset(total)
        self.longest = max(self.lengths)
        self.strings = [sum(c.values()) for c in self.per_inverter]
        self.long_tables = [sum(v // rows for n, v in c.items() if n != self.main) for c in self.per_inverter]

    def problems(self) -> list[str]:
        out = []
        for k, c in enumerate(self.per_inverter, start=1):
            odd = {n: v for n, v in c.items() if n != self.main and v % self.rows}
            if odd:
                out.append(f"inverter {k:02d} has {', '.join(f'{v} string(s) of {n}' for n, v in sorted(odd.items()))}: a {self.rows}-row table of {n} modules holds "
                           f"{self.rows} such strings, so give them in multiples of {self.rows}")  # fmt: skip
        return out

    def modules(self, k: int) -> int:
        return sum(n * v for n, v in self.per_inverter[k].items())


def _units(t: Table, plan: GivenPlan, grid_cols: int) -> int:
    """Strings a placed table position holds: a full grid position one per row (any length). The half tables the grid leaves at row ends are not
    used: a block whose strings do not fill whole tables cuts one of its own full positions to a half table instead, so it stays compact."""
    return t.rows if t.cols == grid_cols else 0


def _select(tables: Sequence[Table], units: Sequence[int], need: int, spec: PlacementSpec) -> list[int]:
    """Table indices holding exactly `need` strings: nearest `fill_from` with a nearest fill, else in row order."""
    order = list(range(len(tables)))
    if spec.fill is FillOrder.NEAREST and spec.fill_from is not None:
        fx, fy = spec.fill_from.x, spec.fill_from.y
        order.sort(key=lambda i: (round(math.hypot(table_centre(tables[i])[0] - fx, table_centre(tables[i])[1] - fy), 6), i))
    got, out = 0, []
    for i in order:
        if units[i] and got + units[i] <= need:
            out.append(i)
            got += units[i]
        if got == need:
            break
    return sorted(out) if got == need else []


def _partition(tables: Sequence[Table], units: Sequence[int], targets: Sequence[int], azimuth_deg: float) -> list[list[int]]:
    """Recursive bisection of the tables into groups of exactly `targets[k]` strings each, in target order along the cuts; each cut takes the
    widest gap among the exact positions and, where no position is exact, moves or swaps one table across the cut."""
    (ux, uy), (vx, vy) = frame(azimuth_deg)
    rows = max(tables[i].rows for i in range(len(tables)))
    pts = []
    for t in tables:
        x, y = table_centre(t)
        pts.append((x * ux + y * uy, x * vx + y * vy))

    def cut(idx: list[int], want: Sequence[int], axis: int) -> tuple[float, int, list[int], int]:
        """The cut position: exact strings on each side, each side holding a half table for every target its full tables cannot make up, at the widest gap."""
        k_lo = len(want) // 2
        need = sum(want[:k_lo])
        odd_lo, odd_hi = sum(1 for w in want[:k_lo] if w % rows), sum(1 for w in want[k_lo:] if w % rows)
        ordered = sorted(idx, key=lambda i: (round(pts[i][axis], 6), round(pts[i][1 - axis], 6), tables[i].id))
        coords = [pts[i][axis] for i in ordered]
        typical = _median([b - a for a, b in zip(coords, coords[1:]) if b - a > 1e-6]) or 1.0
        halves = sum(1 for i in ordered if units[i] % rows)
        acc, h, best = 0, 0, None
        for pos in range(1, len(ordered)):
            acc += units[ordered[pos - 1]]
            h += units[ordered[pos - 1]] % rows != 0
            feasible = h >= odd_lo and halves - h >= odd_hi
            key = (not feasible, abs(acc - need), -(coords[pos] - coords[pos - 1]) / typical)
            if best is None or key < best[0]:
                best = (key, pos, acc - need)
        (infeasible, miss, neg_gap), pos, d = best
        return (-neg_gap if not miss and not infeasible else -math.inf), pos, ordered, d

    def fix(lo: list[int], hi: list[int], d: int, axis: int) -> tuple[list[int], list[int]]:
        """Move one table across the cut, or swap one for one or one for two near it, so the low side holds `d` strings fewer."""
        if not d:
            return lo, hi
        edge = (pts[lo[-1]][axis] + pts[hi[0]][axis]) / 2
        far = lambda i: abs(pts[i][axis] - edge)  # noqa: E731
        near_lo, near_hi = lo[-SWAP_WINDOW:], hi[:SWAP_WINDOW]
        moves = [(far(a), (a,), ()) for a in lo if units[a] == d] + [(far(b), (), (b,)) for b in hi if units[b] == -d]
        moves += [(far(a) + far(b), (a,), (b,)) for a in near_lo for b in near_hi if units[a] - units[b] == d]
        if not moves:
            moves += [(far(a) + far(b) + far(c), (a,), (b, c)) for a in near_lo for j, b in enumerate(near_hi) for c in near_hi[j + 1 :] if units[a] - units[b] - units[c] == d]
            moves += [(far(a) + far(b) + far(c), (a, b), (c,)) for i, a in enumerate(near_lo) for b in near_lo[i + 1 :] for c in near_hi if units[a] + units[b] - units[c] == d]
        if not moves:
            raise PlacementError(f"the tables cannot be split into blocks of exactly the inverters' strings ({len(lo)} and {len(hi)} tables either side of a cut)")
        _, out, into = min(moves, key=lambda m: (m[0], m[1], m[2]))
        return [i for i in lo if i not in out] + list(into), list(out) + [i for i in hi if i not in into]

    def split(idx: list[int], ids: list[int]) -> dict[int, list[int]]:
        """Groups by target index. The targets of each side may be any of a few orders of `ids` (as given, reversed, those needing a half table
        first or last), so the side holding the half tables takes the targets that need them."""
        if len(ids) == 1:
            return {ids[0]: idx}
        k_lo = len(ids) // 2
        xs, ys = [pts[i][0] for i in idx], [pts[i][1] for i in idx]
        longer = 0 if max(xs) - min(xs) >= max(ys) - min(ys) else 1
        odd = [t for t in ids if targets[t] % rows]
        even = [t for t in ids if not targets[t] % rows]
        orders = list(dict.fromkeys(tuple(o) for o in (ids, ids[::-1], even + odd, odd + even)))
        tries = []
        for rank, order in enumerate(orders):
            cuts = {axis: cut(idx, [targets[t] for t in order], axis) for axis in (longer, 1 - longer)}
            wide = cuts[1 - longer][0] >= WIDE_GAP_RATIO and cuts[1 - longer][0] > cuts[longer][0]
            for axis, (score, pos, ordered, d) in cuts.items():
                across = (axis != longer) != wide
                tries.append(((score == -math.inf, across, rank, -score if score > -math.inf else 0.0), list(order), axis, pos, ordered, d))
        last = None
        for _, order, axis, pos, ordered, d in sorted(tries, key=lambda t: t[0]):
            try:
                lo, hi = fix(ordered[:pos], ordered[pos:], d, axis)
                return split(lo, order[:k_lo]) | split(hi, order[k_lo:])
            except PlacementError as e:
                last = e
        raise last

    by_target = split(list(range(len(tables))), list(range(len(targets))))
    groups = [by_target[t] for t in range(len(targets))]
    return compact(groups, pts, list(units))


def _lengths(groups: Sequence[Sequence[int]], tables: Sequence[Table], plan: GivenPlan, half_cols: int) -> dict[int, int]:
    """The length (cols) of every table: per group, the strings other than the main length on whole tables nearest the group's centre, a half table at
    the group's edge where its main-length strings do not fill whole tables, the main length elsewhere."""
    cols: dict[int, int] = {}
    per_half = plan.rows * half_cols // plan.main
    for k, g in enumerate(groups):
        cx = sum(table_centre(tables[i])[0] for i in g) / len(g)
        cy = sum(table_centre(tables[i])[1] for i in g) / len(g)
        near = sorted(g, key=lambda i: (round(math.hypot(table_centre(tables[i])[0] - cx, table_centre(tables[i])[1] - cy), 6), tables[i].id))
        other = [n for n, v in sorted(plan.per_inverter[k].items()) if n != plan.main for _ in range(v // plan.rows)]
        short = -plan.strings[k] % plan.rows
        halves = short // (plan.rows - per_half) if short else 0
        if len(other) + halves > len(near):
            raise PlacementError(f"block {k + 1:02d} has {len(near)} tables for the {len(other)} tables of {sorted(set(other))}-module strings and {halves} half table(s) its inverter needs")
        for i, n in zip(near, other):
            cols[i] = n
        for i in near[len(other) :]:
            cols[i] = plan.main
        for i in near[::-1][:halves]:
            cols[i] = half_cols
    return cols


def _repack(tables: list[Table], cols: dict[int, int], gap_m: float, grid_cols: int, area: BaseGeometry) -> tuple[list[Table], bool]:
    """Tables set to their lengths. Along a row, consecutive full positions close up to the table gap where a table is shorter than the grid
    position, so the row keeps one gap between tables; a row is left on the grid where closing it would leave the buildable area."""
    out = [t.model_copy(update={"cols": cols[i]}) if i in cols else t for i, t in enumerate(tables)]
    if all(c == grid_cols for c in cols.values()):
        return out, True
    inside = prep(area.buffer(INSIDE_TOL))
    rows: dict[tuple[float, float], list[int]] = {}
    for i, t in enumerate(tables):
        (ux, uy), (vx, vy) = frame(t.azimuth_deg)
        rows.setdefault((t.azimuth_deg, round(t.origin.x * vx + t.origin.y * vy, 3)), []).append(i)
    all_packed = True
    for (az, _), members in rows.items():
        (ux, uy), _ = frame(az)
        along = lambda i: tables[i].origin.x * ux + tables[i].origin.y * uy  # noqa: E731
        members.sort(key=along)
        step = tables[members[0]].slot_width_m
        moved, shift, prev = {}, 0.0, None
        for i in members:
            t = tables[i]
            if prev is not None and abs(along(i) - along(prev) - (grid_cols * step + gap_m)) > 1e-3:
                shift = 0.0
            if shift:
                moved[i] = out[i].model_copy(update={"origin": Point(x=round(t.origin.x - shift * ux, 4), y=round(t.origin.y - shift * uy, 4))})
            shift += (grid_cols - out[i].cols) * step
            prev = i
        if all(inside.contains(table_polygon(m)) for m in moved.values()):
            for i, m in moved.items():
                out[i] = m
        else:
            all_packed = False
    return out, all_packed


def fit_given(tables: list[Table], spec: PlacementSpec, plan: GivenPlan, grid_cols: int, area: BaseGeometry) -> tuple[list[Table], list[list[int]], list[str]]:
    """The tables of the plant and their groups, one per inverter in numbering order, each holding exactly that inverter's strings."""
    per_half = plan.rows * spec.half_table_cols // plan.main if spec.half_tables else 0
    if any(n % plan.rows for n in plan.strings) and (plan.rows < 2 or per_half < 1 or (plan.rows * spec.half_table_cols) % plan.main):
        odd = [k + 1 for k, n in enumerate(plan.strings) if n % plan.rows]
        raise PlacementError(
            f"inverter(s) {odd} have strings that do not fill whole {plan.rows}-row tables and no half table of {plan.rows} x {spec.half_table_cols} holds whole "
            f"{plan.main}-module strings: turn half tables on with half_cols={plan.main // plan.rows if plan.main % plan.rows == 0 else plan.main}"
        )
    units = [_units(t, plan, grid_cols) for t in tables]
    rounded = [-(-n // plan.rows) * plan.rows for n in plan.strings]
    need = sum(rounded)
    chosen = _select(tables, units, need, spec)
    if not chosen:
        have = sum(units)
        raise PlacementError(
            f"the buildable area holds {have} string positions (tables of {spec.table_rows} x {grid_cols} and half tables), fewer than the {need} strings "
            f"inverter_strings gives ({sum(plan.modules(k) for k in range(len(plan.strings)))} modules): enlarge the plot, reduce the setbacks or the row pitch"
        )
    picked = [tables[i] for i in chosen]
    groups = _partition(picked, [units[i] for i in chosen], rounded, spec.table_azimuth_deg)
    cols = _lengths(groups, picked, plan, spec.half_table_cols)
    packed, closed = _repack(picked, cols, spec.table_gap_m, grid_cols, area)
    notes = []
    if plan.longest != plan.main or len(plan.lengths) > 1:
        sizes = ", ".join(f"{spec.table_rows} x {n}" for n in sorted(plan.lengths))
        notes.append(
            f"tables as long as their strings ({sizes} modules, one string per table row; half tables of {spec.table_rows} x {spec.half_table_cols} hold one "
            f"{plan.main}-module string across both rows): strings never cross tables"
            + ("" if closed else "; some rows keep the longest table's spacing where closing them up would leave the buildable area")
        )
    for k, g in enumerate(groups):
        got = sum(packed[i].slot_count // plan.main if packed[i].cols not in plan.lengths else packed[i].rows for i in g)
        if got != plan.strings[k]:
            raise PlacementError(f"internal: block {k + 1:02d} holds {got} strings for the {plan.strings[k]} of inverter {k + 1:02d}")
    return packed, groups, notes


"""PV plant plans: how many blocks, inverters and strings, uniform or read from an array layout."""

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from unicad.arrays.strings import assign_strings
from unicad.domain.layout import ArrayLayout, StringAssignment
from unicad.design.strings import DesignError

STRING_ID = re.compile(r"^(?P<block>.+)-I(?P<inv>\d+)-S(?P<s>\d+)$")


@dataclass
class InvClass:
    """`count` identical inverters with `strings` strings each. `part` is the literal inverter number when count is 1 in a layout plan;
    `slots` holds each inverter's string assignments in layout order."""

    sfx: str
    count: int
    strings: int
    part: str | None = None
    slots: list[list[StringAssignment]] = field(default_factory=list)


@dataclass
class BlockClass:
    sfx: str
    count: int
    invs: list[InvClass]
    part: str | None = None
    name: str | None = None

    @property
    def inverters(self) -> int:
        return sum(i.count for i in self.invs)

    @property
    def strings(self) -> int:
        return sum(i.count * i.strings for i in self.invs)


@dataclass
class PvPlan:
    blocks: list[BlockClass]
    modules_per_string: int
    from_layout: bool = False
    unstrung_modules: int = 0

    @property
    def inverters(self) -> int:
        return sum(b.count * b.inverters for b in self.blocks)

    @property
    def strings(self) -> int:
        return sum(b.count * b.strings for b in self.blocks)


def uniform(n_blocks: int, inv_per_block: int, strings_per_inverter: int, mps: int) -> PvPlan:
    return PvPlan([BlockClass("", n_blocks, [InvClass("", inv_per_block, strings_per_inverter)])], mps)


def from_layout(layout: ArrayLayout, mps: int, target_spi: int, max_spi: int, max_inv_per_block: int | None = None) -> PvPlan:
    """Re-string each layout block with `mps` modules per string, split it into the fewest inverters of at most `target_spi` strings
    (balanced, so only the last inverter may carry fewer), and keep every string's slots so the network can link them 1:1."""
    blocks, unstrung = [], 0
    for bi, b in enumerate(layout.blocks):
        probe, left = assign_strings([b], mps, None)
        n = len(probe)
        if n == 0:
            unstrung += left
            continue
        n_inv = max(1, math.ceil(n / target_spi))
        if max_inv_per_block is not None and n_inv > max_inv_per_block:
            n_inv = max_inv_per_block
        spi = math.ceil(n / n_inv)
        if spi > max_spi:
            raise DesignError(f"layout block {b.id} has {n} strings: {n_inv} inverters would need {spi} strings each, above the inverter limit {max_spi}")
        assigned, left = assign_strings([b], mps, spi)
        unstrung += left
        per_inv: dict[int, list[StringAssignment]] = {}
        for a in assigned:
            m = STRING_ID.match(a.string)
            per_inv.setdefault(int(m["inv"]), []).append(a)
        order = sorted(per_inv)
        full = [i for i in order if len(per_inv[i]) == spi]
        rest = [i for i in order if len(per_inv[i]) != spi]
        invs = []
        if full:
            invs.append(InvClass("a", len(full), spi, None if len(full) > 1 else f"{full[0]:02d}", [per_inv[i] for i in full]))
        for k, i in enumerate(rest):
            invs.append(InvClass(chr(ord("b") + k), 1, len(per_inv[i]), f"{i:02d}", [per_inv[i]]))
        num = re.sub(r"\D", "", b.id) or str(bi + 1)
        blocks.append(BlockClass(f"_b{bi + 1:02d}", 1, invs, f"{int(num):02d}", b.name))
    if not blocks:
        raise DesignError(f"the layout holds no whole string of {mps} modules")
    return PvPlan(blocks, mps, from_layout=True, unstrung_modules=unstrung)


def merged(plan: PvPlan) -> PvPlan:
    """All blocks of a layout plan as one block whose inverters are numbered once across the plant, in block order: the inverters with
    the most common string count first as one class, the others one by one."""
    if len(plan.blocks) <= 1:
        return plan
    slots = [(ic.strings, s) for b in plan.blocks for ic in b.invs for s in (ic.slots or [[]] * ic.count) for _ in range(b.count)]
    common = Counter(n for n, _ in slots).most_common(1)[0][0]
    full = [s for n, s in slots if n == common]
    invs = [InvClass("a", len(full), common, None if len(full) > 1 else "01", full if plan.from_layout else [])]
    for k, (n, s) in enumerate((x for x in slots if x[0] != common), start=len(full) + 1):
        invs.append(InvClass(f"x{k:02d}", 1, n, f"{k:02d}", [s] if plan.from_layout else []))
    return PvPlan([BlockClass("", 1, invs)], plan.modules_per_string, plan.from_layout, plan.unstrung_modules)


def split_even(total: int, most: int) -> list[tuple[int, int]]:
    """Split `total` items into the fewest groups of at most `most`, as (groups, size) pairs with sizes differing by at most one."""
    m = math.ceil(total / most)
    base, extra = divmod(total, m)
    return [(c, s) for c, s in ((extra, base + 1), (m - extra, base)) if c > 0]

"""PV plant plans: how many blocks, inverters and strings, uniform or read from an array layout."""

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from unicad.arrays.strings import assign_strings, string_id
from unicad.domain.layout import ArrayLayout, StringAssignment
from unicad.design.patches import centre, patches, spanning
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
    notes: tuple[str, ...] = ()

    @property
    def inverters(self) -> int:
        return sum(b.count * b.inverters for b in self.blocks)

    @property
    def strings(self) -> int:
        return sum(b.count * b.strings for b in self.blocks)


def uniform(n_blocks: int, inv_per_block: int, strings_per_inverter: int, mps: int) -> PvPlan:
    return PvPlan([BlockClass("", n_blocks, [InvClass("", inv_per_block, strings_per_inverter)])], mps)


def from_layout(
    layout: ArrayLayout, mps: int, target_spi: int, max_spi: int, max_inv_per_block: int | None = None, string_kwp: float | None = None,
    sides: Mapping[str, int] | None = None,
) -> PvPlan:  # fmt: skip
    """Re-string each layout block with `mps` modules per string, split it into the fewest inverters of at most `target_spi` strings
    (balanced, so only the last inverter may carry fewer), and keep every string's slots so the network can link them 1:1."""
    blocks, unstrung, spans = [], 0, []
    side_of = sides or {}
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
            fix = ""
            if max_inv_per_block is not None and string_kwp:
                fix = (f"; a block takes at most {max_inv_per_block} inverters ({max_inv_per_block * target_spi} strings, "
                       f"{max_inv_per_block * target_spi * string_kwp:.1f} kWp): re-run layout_place with block_dc_kwp <= {max_inv_per_block * target_spi * string_kwp:.1f} and strings_per_inverter={target_spi}")
            raise DesignError(f"layout block {b.id} has {n} strings: {n_inv} inverters would need {spi} strings each, above the inverter limit {max_spi}{fix}")
        assigned, left = assign_strings([b], mps, spi)
        unstrung += left
        sizes = list(Counter(int(STRING_ID.match(a.string)["inv"]) for a in assigned).values())
        groups = patches([(centre(layout, a), a) for a in assigned], sizes, sides=[side_of.get(a.spans[0].table, 0) for a in assigned] if side_of else None)
        if side_of and (n_span := spanning(groups, side_of)):
            spans.append(f"{b.name}: {n_span} inverter(s) collect strings from both sides of the transmission line corridor, because the strings of each side do not make up whole inverters")
        per_inv = {
            k + 1: [StringAssignment(string=string_id(b.id, k + 1, j + 1), spans=a.spans) for j, a in enumerate(g)] for k, g in enumerate(groups)
        }
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
    return PvPlan(blocks, mps, from_layout=True, unstrung_modules=unstrung, notes=tuple(spans))


def _inverters(blk: BlockClass) -> list[tuple[int, list[StringAssignment]]]:
    return [(ic.strings, sl) for _ in range(blk.count) for ic in blk.invs for sl in (ic.slots or [[]] * ic.count)]


def _regroup(items: list[tuple[int, list[StringAssignment]]], with_slots: bool) -> list[InvClass]:
    """Inverters as classes numbered 01.. in order: those with the most common string count first as one class, the others one by one."""
    common = Counter(n for n, _ in items).most_common(1)[0][0]
    full = [sl for n, sl in items if n == common]
    invs = [InvClass("a", len(full), common, None if len(full) > 1 else "01", full if with_slots else [])]
    for k, (n, sl) in enumerate((x for x in items if x[0] != common), start=len(full) + 1):
        invs.append(InvClass(f"x{k:02d}", 1, n, f"{k:02d}", [sl] if with_slots else []))
    return invs


def merged(plan: PvPlan) -> PvPlan:
    """All blocks of a plan as one block whose inverters are numbered once across the plant, in block order."""
    if len(plan.blocks) <= 1:
        return plan
    items = [x for b in plan.blocks for x in _inverters(b)]
    return PvPlan([BlockClass("", 1, _regroup(items, plan.from_layout))], plan.modules_per_string, plan.from_layout, plan.unstrung_modules)


def sections(blk: BlockClass, most: int, with_slots: bool) -> list[BlockClass]:
    """One block's inverters split into the fewest LV sections of at most `most` inverters (sizes differing by at most one), in order;
    each section numbers its inverters from 01."""
    items = [x for ic in blk.invs for sl in (ic.slots or [[]] * ic.count) for x in [(ic.strings, sl)]]
    out, at = [], 0
    for count, size in split_even(len(items), most):
        for _ in range(count):
            letter = chr(ord("a") + len(out))
            out.append(BlockClass(f"{blk.sfx}{letter}", 1, _regroup(items[at : at + size], with_slots), letter.upper()))
            at += size
    return out


def split_even(total: int, most: int) -> list[tuple[int, int]]:
    """Split `total` items into the fewest groups of at most `most`, as (groups, size) pairs with sizes differing by at most one."""
    m = math.ceil(total / most)
    base, extra = divmod(total, m)
    return [(c, s) for c, s in ((extra, base + 1), (m - extra, base)) if c > 0]

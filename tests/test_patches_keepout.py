"""R6-m8: an inverter's string patch does not span a transmission line corridor (thar-45mw Block-06 / Block-08, kutch-hybrid Block-03)."""

import gzip

import pytest

from test_design import CATALOG
from test_sets import ROOT
from unicad.design.patches import keep_out_sides, patches, spanning
from unicad.design.plan import from_layout
from unicad.domain.layout import SlotSpan, StringAssignment
from unicad.store import parse_project

CASES = {"thar_45mw": (27, 24, 30, 16, 605.0), "kutch_hybrid": (28, 21, 30, 12, 620.0)}


def load(name: str):
    return parse_project(gzip.decompress((ROOT / "fixtures" / "projects" / f"{name}.json.gz").read_bytes()).decode("utf-8"), CATALOG)


def item(i: int, x: float, y: float, side: int):
    return ((x, y), StringAssignment(string=f"S{i:03d}", spans=(SlotSpan(table=f"T{side}", row=0, col=0, n=1),))), side


def synthetic(north: int, south: int):
    items = [item(i, i % 8 * 3.0, 100.0 + i // 8 * 3.0, 0) for i in range(north)]
    items += [item(north + i, i % 8 * 3.0, -100.0 - i // 8 * 3.0, 1) for i in range(south)]
    return [it for it, _ in items], [s for _, s in items]


def test_strings_that_divide_by_side_make_patches_that_each_keep_to_one_side():
    items, sides = synthetic(48, 24)
    groups = patches(items, [24, 24, 24], sides=sides)
    assert [len(g) for g in groups] == [24, 24, 24]
    assert spanning(groups, {"T0": 0, "T1": 1}) == 0


def test_strings_that_do_not_divide_leave_exactly_one_spanning_patch_taken_at_the_meeting_point():
    items, sides = synthetic(40, 32)
    groups = patches(items, [24, 24, 24], sides=sides)
    assert sorted(len(g) for g in groups) == [24, 24, 24]
    assert spanning(groups, {"T0": 0, "T1": 1}) == 1
    mixed = next(g for g in groups if len({a.spans[0].table for a in g}) > 1)
    assert {a.spans[0].table for a in mixed} == {"T0", "T1"}
    assert sum(1 for a in mixed if a.spans[0].table == "T0") + sum(1 for a in mixed if a.spans[0].table == "T1") == 24


def test_without_sides_patches_are_unchanged():
    items, _ = synthetic(40, 32)
    assert patches(items, [24, 24, 24]) == patches(items, [24, 24, 24], sides=None)


@pytest.mark.parametrize("name", ["thar_45mw", "kutch_hybrid"])
def test_logged_blocks_collect_each_inverters_strings_on_one_side_except_where_the_strings_do_not_divide(name):
    mps, spi, max_spi, cap, wp = CASES[name]
    p = load(name)
    side_of = keep_out_sides(p.array_layout, p.site.geometry)
    assert set(side_of.values()) == {0, 1}
    plan = from_layout(p.array_layout, mps, spi, max_spi, cap, mps * wp / 1000, side_of)
    flat = from_layout(p.array_layout, mps, spi, max_spi, cap, mps * wp / 1000)
    per_block = {b.name: sum(spanning([sl], side_of) for ic in b.invs for sl in ic.slots) for b in plan.blocks}
    old = sum(spanning([sl], side_of) for b in flat.blocks for ic in b.invs for sl in ic.slots)
    assert max(per_block.values()) <= 1
    assert sum(per_block.values()) <= old
    if name == "thar_45mw":
        assert sum(per_block.values()) < old
    assert len(plan.notes) == sum(1 for v in per_block.values() if v)
    assert all("transmission line corridor" in n for n in plan.notes)


def test_autobuild_reports_the_corridor_trade_off_and_still_builds_without_blockers():
    from unicad.design.autobuild import autobuild

    p = load("kutch_hybrid")
    args = dict(template="hybrid", ac_kw=18000, module="waaree-bin-21-620", inverter="huawei-sun2000-330ktl-h1", modules_per_string=28, strings_per_inverter=21,
                inverters_per_block=12, bess_kw=10080, bess_kwh=20060, pcs="sungrow-st5015kwh-2500kw-2h", battery="sungrow-st5015kwh", export_limit_kw=18000, export_control="ppc")  # fmt: skip
    r = autobuild(p, CATALOG, args, p.array_layout)
    assert not r.blockers
    text = " | ".join(r.assumptions)
    assert "keep to one side of a transmission line corridor" in text
    assert "BLOCK-03: 1 inverter(s) collect strings from both sides" in text

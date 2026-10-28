"""R6-m8: layout blocks do not run cable under a transmission line corridor (thar-45mw Block-06 / Block-08, kutch-hybrid Block-03)."""

import gzip

import pytest

from test_design import CATALOG
from test_sets import ROOT
from unicad.arrays.blocks import KEEP_OUT_BALANCE, allocate_even, build_blocks, regions
from unicad.arrays.geometry import table_centre
from unicad.store import parse_project

CASES = {"thar_45mw": (8, 24, 27, 605.0), "kutch_hybrid": (5, 21, 28, 620.0)}


def load(name: str):
    return parse_project(gzip.decompress((ROOT / "fixtures" / "projects" / f"{name}.json.gz").read_bytes()).decode("utf-8"), CATALOG)


def blocks_of(name: str):
    n, spi, mps, wp = CASES[name]
    p = load(name)
    tables = [t for b in p.array_layout.blocks for t in b.tables]
    kwp = [t.slot_count * wp / 1000 for t in tables]
    notes: list[str] = []
    blocks = build_blocks(
        tables, kwp, n, tables[0].azimuth_deg, p.site.geometry, [], 10.0, None, notes,
        units=[t.slot_count // mps for t in tables], quantum=spi, trim=True, mps=mps,
    )  # fmt: skip
    islands = regions(tables, p.site.geometry, keep_out_only=True)
    where = {table_centre(tables[j]): k for k, g in enumerate(islands) for j in g}
    return blocks, notes, where, len(islands)


@pytest.mark.parametrize("name", ["thar_45mw", "kutch_hybrid"])
def test_no_block_straddles_the_transmission_line_corridor(name):
    blocks, notes, where, islands = blocks_of(name)
    assert islands == 2 and len(blocks) == CASES[name][0]
    for b in blocks:
        assert len({where[table_centre(t)] for t in b.tables}) == 1, (b.name, "runs cable under the line")
    assert any("transmission line corridor" in n and "differ in capacity" in n for n in notes)


@pytest.mark.parametrize("name", ["thar_45mw", "kutch_hybrid"])
def test_the_trade_off_is_reported_with_the_spread_and_stays_within_the_accepted_limit(name):
    blocks, notes, _, _ = blocks_of(name)
    note = next(n for n in notes if "differ in capacity" in n)
    caps = [sum(t.slot_count for t in b.tables) for b in blocks]
    assert (max(caps) - min(caps)) / (sum(caps) / len(caps)) <= KEEP_OUT_BALANCE + 0.05
    assert "assumption" in note and "n_blocks" in note


def test_allocate_even_picks_the_split_with_the_closest_block_means():
    assert allocate_even([108.0, 19.0], 8, None) == [7, 1]
    assert allocate_even([10.0, 10.0, 10.0], 2, None) is None
    assert allocate_even([100.0, 20.0], 9, 15.0) == [7, 2]
    assert allocate_even([100.0, 20.0], 8, 15.0) is None

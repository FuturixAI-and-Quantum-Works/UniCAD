"""Render time of a large utility set (R6-M3: the 22-sheet 45 MW set took 179 s against a 60 s client timeout)."""

import gzip
import hashlib
import time
from pathlib import Path

import pytest

from test_design import CATALOG
from test_sets import DRAWN_ON, PROFILE, ROOT
from unicad.drafting.sld import layout
from unicad.sets import render_set
from unicad.store import parse_project

SLD_BOUND_S = 70.0


@pytest.fixture(scope="module")
def thar():
    raw = gzip.decompress((ROOT / "fixtures" / "projects" / "thar_45mw.json.gz").read_bytes()).decode("utf-8")
    return parse_project(raw, CATALOG)


def _digest(out: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob("*.dxf"))}


def test_the_45mw_sld_sheets_render_within_a_generous_bound_and_repeat_exactly(thar, tmp_path):
    layout._ROUTE_CACHE.clear()
    t0 = time.perf_counter()
    first = render_set(thar, tmp_path / "a", kinds=("sld",), formats=("dxf",), profile=PROFILE, catalog=CATALOG, drawn_on=DRAWN_ON)
    elapsed = time.perf_counter() - t0
    assert len(first.sheets) >= 3
    assert elapsed < SLD_BOUND_S, f"SLD planning and drawing took {elapsed:.0f} s (bound {SLD_BOUND_S:.0f} s; 145 s before the route search was memoised)"
    layout._ROUTE_CACHE.clear()
    render_set(thar, tmp_path / "b", kinds=("sld",), formats=("dxf",), profile=PROFILE, catalog=CATALOG, drawn_on=DRAWN_ON)
    cached = render_set(thar, tmp_path / "c", kinds=("sld",), formats=("dxf",), profile=PROFILE, catalog=CATALOG, drawn_on=DRAWN_ON)
    assert len(cached.sheets) == len(first.sheets)
    assert _digest(tmp_path / "b") == _digest(tmp_path / "c")


def test_the_route_search_gives_the_same_path_cold_and_remembered():
    from unicad.drafting.sld.model import Box

    bodies = [Box(10, 0, 20, 30), Box(40, -10, 50, 15)]
    outlines = [Box(0, -20, 0, 40)]
    layout._ROUTE_CACHE.clear()
    cold = layout._search_route((0, 5), (60, 5), bodies, outlines)
    assert layout._ROUTE_CACHE
    assert layout._search_route((0, 5), (60, 5), list(bodies), list(outlines)) == cold == layout._search_route_uncached((0, 5), (60, 5), bodies, outlines)

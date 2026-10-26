"""Lightning protection: NF C 17-102 and rolling-sphere radii against published values, set cover, and coverage of the samples."""

import math

import numpy as np
import pytest

from unicad.arrays.geometry import table_centre
from unicad.domain.project import LpsBasis
from unicad.earthing.samples import pune, rajasthan, with_basis
from unicad.lps import CoverageError, coverage_points, design_lps, ese_radius, greedy_cover, rod_radius


def test_ese_radius_matches_the_published_examples():
    # tools.mepbase.com: Level I, dT 60 us, h 5 m -> sqrt(6175) = 78.6 m; Level I h 10 m -> 79.4 m; table Level III 45 us h 5 m -> 81 m.
    assert ese_radius("I", 60, 5).value == pytest.approx(78.58, abs=0.01)
    assert ese_radius("I", 60, 10).value == pytest.approx(79.4, abs=0.05)
    assert round(ese_radius("III", 45, 5).value) == 81
    assert round(ese_radius("IV", 25, 2).value) == 26
    with pytest.raises(CoverageError):
        ese_radius("I", 61, 5)
    with pytest.raises(CoverageError):
        ese_radius("I", 60, 1.5)


def test_rolling_sphere_rod_radius():
    # R 45 m (LPL III), rod 10 m: at ground sqrt(10 x 80) = 28.28 m; at 3 m it shrinks by sqrt(3 x 87) = 16.16 m.
    assert rod_radius("III", 10, 0).value == pytest.approx(28.28, abs=0.01)
    assert rod_radius("III", 10, 3).value == pytest.approx(28.28 - 16.16, abs=0.02)
    assert rod_radius("I", 30, 0).value == pytest.approx(20.0, abs=1e-6)  # a rod taller than R protects as one of height R
    with pytest.raises(CoverageError):
        rod_radius("II", 3, 3)


def test_greedy_cover_is_minimal_on_a_simple_case():
    pts = np.array([[0, 0], [1, 0], [10, 0], [11, 0]], dtype=float)
    cands = np.array([[0.5, 0], [10.5, 0], [5, 0], [0, 0]], dtype=float)
    cover = greedy_cover(pts, cands, 1.0)
    assert sorted(cover.terminals) == [(0.5, 0.0), (10.5, 0.0)] and cover.uncovered == ()
    far = greedy_cover(np.array([[100.0, 100.0]]), cands, 1.0)
    assert far.terminals == () and far.uncovered == (0,)


def test_local_search_drops_terminals_that_greedy_cover_keeps():
    rng = np.random.default_rng(3)
    pts = rng.uniform(0, 300, size=(1500, 2))
    xs = np.arange(0, 301, 6.0)
    cands = np.array([(x, y) for x in xs for y in xs])
    plain = greedy_cover(pts, cands, 40.0, search=False)
    best = greedy_cover(pts, cands, 40.0)
    assert best.uncovered == () and len(best.terminals) < len(plain.terminals)
    t = np.asarray(best.terminals)
    assert (np.hypot(pts[:, None, 0] - t[None, :, 0], pts[:, None, 1] - t[None, :, 1]).min(axis=1) <= 40.0 + 1e-6).all()


@pytest.mark.parametrize("which", [rajasthan, pune])
def test_every_table_centroid_is_covered(which):
    p, _ = which()
    d = design_lps(p)
    assert d.covered_all
    terms = [(t.at.x, t.at.y) for t in d.terminals]
    for t in p.array_layout.table_by_id.values():
        c = table_centre(t)
        assert min(math.dist(c, q) for q in terms) <= d.radius_m + 1e-6, t.id
    ok, n = coverage_points(d)
    assert ok == n == len(p.array_layout.table_by_id)
    stations = [b.station for b in p.array_layout.blocks if b.station is not None]
    assert all(min(math.dist((s.x, s.y), q) for q in terms) <= d.radius_m for s in stations)


def test_ground_terminals_stand_off_the_tables():
    from shapely.geometry import Point, Polygon

    from unicad.arrays.geometry import table_corners

    p, _ = rajasthan()
    d = design_lps(p)
    tables = [Polygon(table_corners(t)) for t in p.array_layout.table_by_id.values()]
    assert not any(poly.contains(Point(t.at.x, t.at.y)) for t in d.terminals for poly in tables)
    assert len(d.terminals) <= 37  # 44 with greedy cover alone on the 24 m grid before the local search


def test_rooftop_terminals_sit_on_the_roofs():
    p, _ = pune()
    d = design_lps(p)
    assert d.radius_m == pytest.approx(ese_radius("II", 30, 5).value)
    assert any(t.on_roof for t in d.terminals)


def test_rod_method_needs_more_terminals_than_ese():
    p, _ = pune()
    ese = design_lps(p)
    rods = design_lps(with_basis(p, p.design_basis.earthing, LpsBasis(level="II", method="rod", mast_height_m=6, protected_height_m=1)))
    assert rods.covered_all and len(rods.terminals) > len(ese.terminals)
    assert any("rolling sphere" in (s.source or "").lower() for s in rods.steps)


def test_ese_without_radius_or_efficiency_is_refused():
    p, _ = pune()
    with pytest.raises(CoverageError, match="ese_radius_m"):
        design_lps(with_basis(p, None, LpsBasis(level="II")))

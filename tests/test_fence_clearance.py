"""R5-m7 / R6-m10: the switchyard earth mat and lightning masts keep a stated clearance from the boundary fence."""

import gzip

import pytest
from shapely.geometry import Point as SPoint
from shapely.geometry import box

from test_design import CATALOG
from test_sets import ROOT
from unicad.arrays import siting
from unicad.domain.layout import Point
from unicad.lps.design import design_lps
from unicad.lps.site import boundary
from unicad.rules import run_rules
from unicad.store import parse_project


def load(name: str):
    return parse_project(gzip.decompress((ROOT / "fixtures" / "projects" / f"{name}.json.gz").read_bytes()).decode("utf-8"), CATALOG)


@pytest.fixture(scope="module")
def thar():
    return load("thar_45mw")


@pytest.fixture(scope="module")
def kutch():
    return load("kutch_hybrid")


def test_the_derived_switchyard_mat_keeps_the_boundary_setback_from_the_fence(thar):
    site = thar.site.geometry
    got = siting.reserve_switchyard(site, Point(x=846.55, y=754.33), "nearest the line", (60.0, 45.0))
    assert got is not None
    mat = box(got.located.at.x - 30, got.located.at.y - 22.5, got.located.at.x + 30, got.located.at.y + 22.5)
    assert boundary(thar).boundary.distance(mat) >= siting.fence_clear_m(site) - 1e-6
    assert siting.fence_clear_m(site) == 10.0


def test_the_fence_clearance_is_at_least_the_stated_minimum_without_a_site_setback(thar):
    bare = thar.site.geometry.model_copy(update={"setbacks": thar.site.geometry.setbacks.model_copy(update={"boundary_m": 0.0})})
    assert siting.fence_clear_m(bare) == siting.FENCE_CLEAR_MIN_M
    ground = siting.equipment_ground(bare)
    assert boundary(thar).boundary.distance(ground) >= siting.FENCE_CLEAR_MIN_M - 1e-6


def test_masts_stand_the_stated_clearance_inside_the_fence(kutch, thar):
    for p in (kutch, thar):
        fence = boundary(p).boundary
        d = design_lps(p)
        assert d.terminals
        assert min(fence.distance(SPoint(t.at.x, t.at.y)) for t in d.terminals) >= siting.FENCE_CLEAR_MIN_M - 1e-6
        assert any("boundary fence" in a for a in d.assumptions)


def test_validate_errors_on_the_logged_mat_on_the_fence_and_warns_below_the_setback(thar, kutch):
    got = {f.rule_id: f for f in run_rules(thar, CATALOG).findings if f.rule_id.endswith("fence_clearance")}
    assert got["earthing.mat_fence_clearance"].severity.value == "error"
    assert 0.6 < got["earthing.mat_fence_clearance"].measured < 0.7
    assert got["lps.mast_fence_clearance"].severity.value == "info"
    ok = {f.rule_id: f for f in run_rules(kutch, CATALOG).findings if f.rule_id.endswith("fence_clearance")}
    assert ok["earthing.mat_fence_clearance"].severity.value == "warning" and ok["earthing.mat_fence_clearance"].measured == 7.5

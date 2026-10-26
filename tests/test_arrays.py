import math
import time
from pathlib import Path

import pytest
from shapely import STRtree
from shapely.geometry import Polygon
from shapely.ops import unary_union

from unicad.arrays import MountingKind, PlacementError, PlacementSpec, place_array, table_polygon
from unicad.arrays.strings import serpentine
from unicad.arrays.sun import fixed_tilt_gap, sun_path, sun_position, tracker_pitch
from unicad.catalog.store import load_catalog
from unicad.domain.layout import Point
from unicad.domain.site import FeatureKind, LonLat, Setbacks, SiteFeature, SiteGeometry, buildable_area, shape_of
from unicad.ingest.site_import import import_site

SITES = Path(__file__).resolve().parents[1] / "fixtures" / "sites"
MODULE_ID = "waaree-bi-55-540"
CATALOG = load_catalog()
MODULE = CATALOG.get(MODULE_ID)
SLOPE_2P = 2 * (MODULE.length / 1000 + 0.02)


@pytest.fixture(scope="module")
def ground() -> SiteGeometry:
    g = import_site(SITES / "ground_mount_rajasthan.kml", None).geometry
    return g.model_copy(update={"setbacks": Setbacks(boundary_m=10, exclusion_m=5)})


@pytest.fixture(scope="module")
def roof() -> SiteGeometry:
    g = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    return g.model_copy(update={"setbacks": Setbacks(roof_edge_m=1.2, obstacle_m=0.6)})


@pytest.fixture(scope="module")
def fixed(ground):
    spec = PlacementSpec(module=MODULE_ID, tilt_deg=25, road_every_rows=12, block_dc_kwp=15000, latitude_deg=27.0)
    return place_array(ground, spec, MODULE)


def tables_of(result):
    return [t for b in result.layout.blocks for t in b.tables]


def assert_no_overlaps(polys: list[Polygon]) -> None:
    tree = STRtree(polys)
    for i, p in enumerate(polys):
        for j in tree.query(p):
            if j > i:
                assert p.intersection(polys[j]).area < 1e-6, f"tables {i} and {j} overlap"


def test_ground_tables_lie_inside_the_buildable_area_and_do_not_overlap(ground, fixed):
    area = buildable_area(ground).ground.buffer(1e-3)
    polys = [table_polygon(t) for t in tables_of(fixed)]
    assert len(polys) > 2000
    outside = [i for i, p in enumerate(polys) if not area.contains(p)]
    assert outside == []
    assert_no_overlaps(polys)
    keep_out = unary_union([shape_of(f) for f in ground.features if f.kind in (FeatureKind.WATER, FeatureKind.EXCLUSION, FeatureKind.TRANSMISSION_LINE)])
    assert not any(p.intersection(keep_out).area > 1e-6 for p in polys)


def test_solstice_pitch_matches_the_hand_computed_shading_angle_at_27n(fixed):
    # 27 N, declination -23.44, 09:00 solar time (hour angle -45 deg):
    # sin(el) = sin27 sin(-23.44) + cos27 cos23.44 cos45 -> el = 23.42 deg; sun 44.99 deg east of south
    # profile angle = atan(tan 23.42 / cos 44.99) = 31.49 deg; noon elevation 90 - 27 - 23.44 = 39.56 deg, so 09:00 governs.
    p9 = sun_position(27.0, -23.44, 9.0)
    assert p9.elevation_deg == pytest.approx(23.42, abs=0.01)
    assert p9.azimuth_deg == pytest.approx(180 - 44.99, abs=0.02)
    assert sun_position(27.0, -23.44, 12.0).elevation_deg == pytest.approx(39.56, abs=0.01)
    depth = SLOPE_2P * math.cos(math.radians(25))
    expected = depth + SLOPE_2P * math.sin(math.radians(25)) / math.tan(math.radians(31.49))
    assert fixed.pitch.pitch_m == pytest.approx(expected, abs=0.005)
    assert fixed.pitch.shade_angle_deg == pytest.approx(31.49, abs=0.01)
    assert fixed.pitch.shade_hour in (9.0, 15.0)
    assert fixed.metrics.gcr == pytest.approx(SLOPE_2P / expected, abs=1e-3)


def test_rows_are_spaced_by_the_pitch_except_at_roads(fixed):
    ys = sorted({round(t.origin.y, 3) for t in tables_of(fixed)})
    steps = sorted({round(b - a, 3) for a, b in zip(ys, ys[1:])})
    pitch = fixed.pitch.pitch_m
    depth = SLOPE_2P * math.cos(math.radians(25))
    assert steps[0] == pytest.approx(pitch, abs=0.002)
    road = max(pitch, depth + 6.0)
    assert all(math.isclose(s, pitch, abs_tol=0.002) or math.isclose(s, road, abs_tol=0.002) or s > road for s in steps)
    assert len(fixed.roads) > 0 and all(r.width_m == pytest.approx(6.0, abs=0.01) for r in fixed.roads)


@pytest.mark.parametrize("method,args,expected", [
    ("explicit", {"pitch_m": 9.0}, 9.0),
    ("gcr", {"gcr": 0.5}, SLOPE_2P / 0.5),
    ("gap", {"row_gap_m": 3.0}, SLOPE_2P * math.cos(math.radians(25)) + 3.0),
])  # fmt: skip
def test_other_pitch_methods(ground, method, args, expected):
    r = place_array(ground, PlacementSpec(module=MODULE_ID, tilt_deg=25, pitch_method=method, target_dc_kwp=5000, **args), MODULE)
    assert r.pitch.pitch_m == pytest.approx(expected, abs=1e-3)
    ys = sorted({round(t.origin.y, 3) for t in tables_of(r)})
    assert min(b - a for a, b in zip(ys, ys[1:])) == pytest.approx(expected, abs=0.002)


def test_pitch_shorter_than_the_table_is_rejected(ground):
    with pytest.raises(PlacementError, match="rows would overlap"):
        place_array(ground, PlacementSpec(module=MODULE_ID, tilt_deg=25, pitch_method="explicit", pitch_m=2.0), MODULE)


def test_blocks_are_balanced_within_five_percent(fixed):
    caps = [b.dc_kwp for b in fixed.blocks]
    assert len(caps) == math.ceil(fixed.metrics.dc_kwp / 15000)
    assert all(c <= 15000 for c in caps)
    mean = sum(caps) / len(caps)
    assert (max(caps) - min(caps)) / mean < 0.05
    assert fixed.metrics.block_balance < 0.05
    assert sum(caps) == pytest.approx(fixed.metrics.dc_kwp, abs=0.01)


def test_blocks_are_spatially_compact(ground, fixed):
    buildable = buildable_area(ground).ground
    for b in fixed.layout.blocks:
        hull = Polygon([(p.x, p.y) for p in b.boundary]).intersection(buildable)
        tables = unary_union([table_polygon(t) for t in b.tables])
        assert tables.area / hull.area > 0.5, b.id
        others = unary_union([table_polygon(t) for o in fixed.layout.blocks if o.id != b.id for t in o.tables])
        assert others.intersection(hull).area / hull.area < 0.1, b.id
        assert b.station is not None


def test_station_sits_on_a_road_clear_of_tables(ground, fixed):
    from shapely.geometry import LineString
    from shapely.geometry import Point as SPoint

    roads = [LineString([(p.x, p.y) for p in r.points]) for r in fixed.roads] + [shape_of(f) for f in ground.of_kind(FeatureKind.ROAD)]
    for b in fixed.layout.blocks:
        s = SPoint(b.station.x, b.station.y)
        assert min(r.distance(s) for r in roads) < 0.01, b.id
        assert not any(table_polygon(t).contains(s) for t in tables_of(fixed))


@pytest.mark.parametrize("fill", ["rows", "nearest"])
def test_target_capacity_is_met_within_one_percent(ground, fill):
    target = 40000.0
    spec = PlacementSpec(module=MODULE_ID, tilt_deg=25, target_dc_kwp=target, n_blocks=3, fill=fill,
                         fill_from=Point(x=500, y=400) if fill == "nearest" else None)  # fmt: skip
    r = place_array(ground, spec, MODULE)
    assert r.target_reached
    assert abs(r.metrics.dc_kwp - target) / target < 0.01
    assert r.metrics.dc_kwp <= target
    if fill == "nearest":
        d = [math.hypot(table_polygon(t).centroid.x - 500, table_polygon(t).centroid.y - 400) for t in tables_of(r)]
        assert max(d) < 600


def test_target_beyond_the_site_reports_a_warning(ground):
    r = place_array(ground, PlacementSpec(module=MODULE_ID, tilt_deg=25, target_dc_kwp=10_000_000), MODULE)
    assert r.target_reached is False
    assert any("not reached" in w for w in r.warnings)


def test_half_tables_fill_edges_only_when_enabled(ground, fixed):
    assert fixed.metrics.tables_half > 0
    halves = [t for t in tables_of(fixed) if t.cols == 14]
    assert len(halves) == fixed.metrics.tables_half
    r = place_array(ground, PlacementSpec(module=MODULE_ID, tilt_deg=25, road_every_rows=12, half_tables=False, latitude_deg=27.0), MODULE)
    assert r.metrics.tables_half == 0 and r.metrics.tables_full == fixed.metrics.tables_full


def test_strings_fill_contiguous_slots_with_block_inverter_string_ids(fixed):
    strings = fixed.layout.strings
    assert len(strings) == 2 * fixed.metrics.tables_full + fixed.metrics.tables_half
    assert fixed.metrics.unstrung_modules == 0
    full = {t.id for t in tables_of(fixed) if t.cols == 28}
    for a in [a for a in strings if a.spans[0].table in full][:200]:
        assert len(a.slots()) == 28
        assert len({s[0] for s in a.slots()}) == 1
        assert len(a.spans) == 1
    import re

    assert all(re.fullmatch(r"B\d{2}-I1-S\d{2,}", a.string) for a in strings)
    b1 = [a.string for a in strings if a.string.startswith("B01-")]
    assert b1[:2] == ["B01-I1-S01", "B01-I1-S02"]


def test_strings_per_inverter_and_series_lengths_that_cross_rows(ground):
    spec = PlacementSpec(module=MODULE_ID, tilt_deg=25, target_dc_kwp=3000, modules_per_string=24, strings_per_inverter=10)
    r = place_array(ground, spec, MODULE)
    full = next(t for t in tables_of(r) if t.cols == 28)
    order = serpentine(full)
    assert order[27] == (0, 27) and order[28] == (1, 27)
    ids = [a.string for a in r.layout.strings]
    assert "B01-I1-S10" in ids and "B01-I2-S01" in ids and "B01-I1-S11" not in ids
    crossing = [a for a in r.layout.strings if len(a.spans) == 2]
    assert crossing and all(len(a.slots()) == 24 for a in crossing)
    assert r.metrics.unstrung_modules == sum(t.slot_count % 24 for t in tables_of(r))


def test_rooftop_tables_stay_on_roofs_clear_of_obstacles(roof):
    spec = PlacementSpec(module=MODULE_ID, mounting="rooftop_flush", table_rows=2, table_cols=12, pitch_method="gap", row_gap_m=0.6, table_gap_m=0.6)
    r = place_array(roof, spec, MODULE)
    area = buildable_area(roof)
    regions = [g.buffer(1e-3) for g in area.roofs.values()]
    polys = [table_polygon(t) for t in tables_of(r)]
    assert len(polys) > 50
    assert all(any(g.contains(p) for g in regions) for p in polys)
    obstacles = unary_union([shape_of(f) for f in roof.of_kind(FeatureKind.OBSTACLE)])
    assert not any(p.intersects(obstacles) and p.intersection(obstacles).area > 0 for p in polys)
    assert all(any(g.contains(p) for p in polys) for g in regions)
    assert all(t.mounting.value == "roof_flush" for t in tables_of(r))
    assert_no_overlaps(polys)


def test_tracker_rows_run_along_the_axis_and_are_shade_free(ground):
    spec = PlacementSpec(module=MODULE_ID, mounting=MountingKind.TRACKER_1P, table_rows=1, table_cols=56, latitude_deg=27.0, target_dc_kwp=20000)
    r = place_array(ground, spec, MODULE)
    t = tables_of(r)[0]
    assert t.azimuth_deg == 90.0 and t.tilt_deg == 0
    x0, y0, x1, y1 = table_polygon(t).bounds
    assert y1 - y0 > 10 * (x1 - x0)
    width = MODULE.length / 1000 + 0.02
    path = sun_path(27.0, 9.0, 15.0)
    assert r.pitch.pitch_m == pytest.approx(tracker_pitch(width, 0.0, 60.0, path)[0], abs=1e-3)
    assert r.pitch.pitch_m > width
    xs = sorted({round(t.origin.x, 3) for t in tables_of(r)})
    assert min(b - a for a, b in zip(xs, xs[1:])) == pytest.approx(r.pitch.pitch_m, abs=0.002)


def test_fixed_gap_ignores_sun_behind_the_row():
    path = [sun_position(27.0, -23.44, 12.0)]
    assert fixed_tilt_gap(4.0, 20.0, 0.0, path)[0] == 0.0
    assert fixed_tilt_gap(4.0, 20.0, 180.0, path)[0] > 0


def test_placement_is_deterministic(ground, fixed):
    again = place_array(ground, PlacementSpec(module=MODULE_ID, tilt_deg=25, road_every_rows=12, block_dc_kwp=15000, latitude_deg=27.0), MODULE)
    assert again.model_dump() == fixed.model_dump()


def test_defaults_are_reported_as_assumptions(fixed):
    joined = " ".join(fixed.assumptions)
    assert "gap between tables" in joined and "modules per string" in joined and "internal road width" in joined


def test_site_without_buildable_area_is_an_error():
    site = SiteGeometry(crs="EPSG:32643", origin=LonLat(lon=72.0, lat=27.0))
    with pytest.raises(PlacementError, match="no buildable area"):
        place_array(site, PlacementSpec(module=MODULE_ID), MODULE)


def test_hundred_mwp_site_places_in_under_thirty_seconds():
    side = 1500.0
    boundary = SiteFeature(id="b", kind=FeatureKind.BOUNDARY, polygon=tuple(Point(x=x, y=y) for x, y in ((0, 0), (side, 0), (side, side), (0, side))))
    pond = SiteFeature(id="p", kind=FeatureKind.WATER, polygon=tuple(Point(x=x, y=y) for x, y in ((400, 400), (600, 420), (580, 650), (380, 600))))
    site = SiteGeometry(crs="EPSG:32643", origin=LonLat(lon=72.0, lat=27.0), features=(boundary, pond))
    spec = PlacementSpec(module=MODULE_ID, tilt_deg=25, target_dc_kwp=100_000, road_every_rows=12, block_dc_kwp=12_500)
    t = time.perf_counter()
    r = place_array(site, spec, MODULE)
    elapsed = time.perf_counter() - t
    assert elapsed < 30, elapsed
    assert abs(r.metrics.dc_kwp - 100_000) / 100_000 < 0.01
    assert len(r.blocks) == 8 and r.metrics.block_balance < 0.05
    assert r.metrics.modules > 180_000


def test_block_capacity_never_exceeds_block_dc_kwp(ground):
    spec = PlacementSpec(module=MODULE_ID, tilt_deg=25, target_dc_kwp=29_900, block_dc_kwp=15_000)
    r = place_array(ground, spec, MODULE)
    assert len(r.blocks) == 2
    assert all(b.dc_kwp <= 15_000 for b in r.blocks)


def test_partition_cuts_at_wide_gaps_within_the_slack_but_not_past_the_cap():
    from unicad.arrays.blocks import partition
    from unicad.domain.layout import Mounting, Orientation, Table

    def table(i: int, x: float) -> Table:
        return Table(id=f"T{i:03d}", mounting=Mounting.FIXED_TILT, origin=Point(x=x, y=0), azimuth_deg=180, tilt_deg=0, rows=1, cols=1,
                     orientation=Orientation.PORTRAIT, module=MODULE_ID, slot_width_m=1.0, slot_height_m=2.0)  # fmt: skip

    tables = [table(i, i * 2.0) for i in range(51)] + [table(51 + i, 400 + i * 2.0) for i in range(49)]
    weights = [1.0] * len(tables)
    assert [len(g) for g in partition(tables, weights, 2, 180)] == [51, 49]
    assert [len(g) for g in partition(tables, weights, 2, 180, cap=50)] == [50, 50]

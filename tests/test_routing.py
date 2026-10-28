import json
from functools import cache
from pathlib import Path

import pytest

from unicad.arrays import PlacementSpec, place_array
from unicad.catalog.store import load_catalog
from unicad.domain.project import Project
from unicad.ingest.site_import import import_site
from unicad.routing import RouteAssumptions, estimate_routes, with_estimated_lengths
from unicad.routing.locate import manhattan, slot_centre
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog()
MODULE = "vikram-solar-hypersol-m10r-144-580-wp"
NO_ALLOWANCE = RouteAssumptions(drop_per_end_m=0, termination_m=0)


def fixture(name: str) -> Project:
    return parse_project((ROOT / "fixtures" / "projects" / f"{name}.json").read_text(encoding="utf-8"), CATALOG)


@cache
def ground_project() -> Project:
    """The utility fixture scaled to 2 blocks of 2 PCUs x 3 SCBs x 12 strings, laid out on the Rajasthan site."""
    data = json.loads((ROOT / "fixtures" / "projects" / "utility_50mwac.json").read_text(encoding="utf-8"))
    for g in data["network"]["groups"]:
        g["count"] = {"block": 2, "scb_sec": 3}.get(g["id"], g.get("count", 1))
    next(n for n in data["network"]["nodes"] if n["id"] == "str")["count"] = 12
    base = Project.model_validate(data, context={"catalog": CATALOG})
    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry
    module = CATALOG.get(MODULE)
    spec = PlacementSpec(module=MODULE, table_rows=2, table_cols=27, tilt_deg=25, n_blocks=2, modules_per_string=27, road_every_rows=12, target_dc_kwp=144 * 27 * 0.58 * 1.1)
    placed = place_array(site, spec, module)
    ids = base.network.instance_ids("str")
    strings = tuple(a.model_copy(update={"string": sid}) for a, sid in zip(placed.layout.strings, ids, strict=False))
    data = base.model_dump(mode="json")
    data["array_layout"] = placed.layout.model_copy(update={"strings": strings}).model_dump(mode="json")
    data["site"]["geometry"] = site.model_dump(mode="json")
    return Project.model_validate(data, context={"catalog": CATALOG})


def test_dc_string_lengths_are_positive_and_never_below_the_straight_line():
    p = fixture("rooftop_500kwp")
    est = estimate_routes(p, CATALOG)
    strings = [i for i in est.instances.values() if i.edge == "e_str"]
    assert len(strings) == p.network.instance_count("str") == 50
    for i in strings:
        assert i.positive_m > 0 and i.negative_m is not None and i.negative_m > 0
        assert i.positive_m >= i.straight_pos_m and i.negative_m >= i.straight_neg_m


def test_string_polarities_differ_by_no_more_than_the_string_extent():
    p = fixture("rooftop_500kwp")
    est = estimate_routes(p, CATALOG, NO_ALLOWANCE)
    t = p.array_layout.table_by_id["T01"]
    extent = (18 - 1) * t.slot_width_m
    gaps = [abs(i.positive_m - i.negative_m) for i in est.instances.values() if i.edge == "e_str"]
    assert 0 < max(gaps) <= extent + 1e-6


def test_allowances_add_exactly_two_ends_per_cable():
    p = fixture("rooftop_500kwp")
    bare = estimate_routes(p, CATALOG, NO_ALLOWANCE)
    padded = estimate_routes(p, CATALOG, RouteAssumptions(drop_per_end_m=2.0, termination_m=0.5))
    for k, b in bare.instances.items():
        assert padded.instances[k].positive_m == pytest.approx(b.positive_m + 5.0)
        assert padded.instances[k].negative_m == pytest.approx(b.negative_m + 5.0)


def test_zero_allowance_length_is_the_manhattan_distance_from_each_end_of_the_string_to_the_combiner():
    p = fixture("rooftop_500kwp")
    layout = p.array_layout
    est = estimate_routes(p, CATALOG, NO_ALLOWANCE)

    def centre(sid: str) -> tuple[float, float]:
        xy = [slot_centre(layout.table_by_id[t], r, c) for t, r, c in layout.string_slots(sid)]
        return sum(x for x, _ in xy) / len(xy), sum(y for _, y in xy) / len(xy)

    centres = [centre(f"str@1.{k}") for k in range(1, 11)]
    dcfb = (sum(x for x, _ in centres) / 10, sum(y for _, y in centres) / 10)
    (t_id, row, first), *_, (_, _, last) = layout.string_slots("str@1.1")
    t = layout.table_by_id[t_id]
    got = est.instances["e_str@1.1"]
    assert got.positive_m == pytest.approx(manhattan(slot_centre(t, row, first), dcfb, t.azimuth_deg))
    assert got.negative_m == pytest.approx(manhattan(slot_centre(t, row, last), dcfb, t.azimuth_deg))


def test_edges_that_cannot_be_placed_are_reported_not_guessed():
    est = estimate_routes(fixture("rooftop_500kwp"), CATALOG)
    assert {"e_inv_mccb", "e_acb_ct"} <= set(est.unresolved)
    assert "e_inv_mccb" not in {i.edge for i in est.instances.values()}
    assert "no position" in est.unresolved["e_inv_mccb"]


def test_a_project_without_a_layout_estimates_nothing_and_says_why():
    est = estimate_routes(fixture("utility_50mwac"), CATALOG)
    assert not est.instances and est.warnings and "no array layout" in est.warnings[0]
    assert "e_str" in est.unresolved


def test_stations_and_switchyard_give_block_to_switchyard_lengths():
    p = ground_project()
    est = estimate_routes(p, CATALOG)
    feeders = [i for i in est.instances.values() if i.edge == "e_icog_bus"]
    assert len(feeders) == 2
    assert all(i.positive_m >= i.straight_pos_m > 0 for i in feeders)
    assert p.array_layout.switchyard is not None and not any("switchyard is not located" in w for w in est.warnings)
    unlocated = p.model_copy(update={"array_layout": p.array_layout.model_copy(update={"switchyard": None})})
    assert any("switchyard is not located" in w for w in estimate_routes(unlocated, CATALOG).warnings)
    at = next(iter(p.array_layout.blocks)).station
    moved = estimate_routes(p, CATALOG, RouteAssumptions(switchyard=(at.x + 500.0, at.y)))
    assert not any("switchyard is not located" in w for w in moved.warnings)
    assert moved.instances["e_icog_bus@1"].positive_m > 500.0
    assert moved.instances["e_icog_bus@1"].positive_m != feeders[0].positive_m


def test_dc_mains_run_from_the_combiner_to_the_block_station_and_stay_above_the_straight_line():
    est = estimate_routes(ground_project(), CATALOG)
    mains = [i for i in est.instances.values() if i.edge == "e_scb_inv"]
    assert len(mains) == 12
    for i in mains:
        assert i.negative_m is None and i.positive_m >= i.straight_pos_m
        assert "station" in i.basis


def test_edges_inside_one_station_are_left_unresolved():
    est = estimate_routes(ground_project(), CATALOG)
    assert "e_inv_idt" in est.unresolved and "same place" in est.unresolved["e_inv_idt"]


def test_template_aggregates_report_min_mean_max_with_a_note():
    est = estimate_routes(ground_project(), CATALOG)
    t = est.templates()["e_str"]
    assert t.count == 144 and t.minimum_m <= t.mean_m <= t.maximum_m
    assert t.length("max") == t.maximum_m and t.length("mean") == t.mean_m
    assert "maximum is taken" in t.note
    with pytest.raises(ValueError):
        t.length("median")


def test_estimated_lengths_fill_only_the_edges_that_state_none():
    p = ground_project()
    data = p.model_dump(mode="json")
    for e in data["network"]["edges"]:
        if e["id"] == "e_icog_bus":
            e.pop("length_m")
    stripped = Project.model_validate(data, context={"catalog": CATALOG})
    est = estimate_routes(stripped, CATALOG, edges={"e_icog_bus"})
    filled = with_estimated_lengths(stripped, est)
    by_id = {e.id: e for e in filled.network.edges}
    assert by_id["e_icog_bus"].length_m == pytest.approx(est.templates()["e_icog_bus"].maximum_m)
    assert by_id["e_str"].length_m == 30.0
    assert stripped.network.edge_by_id["e_icog_bus"].length_m is None

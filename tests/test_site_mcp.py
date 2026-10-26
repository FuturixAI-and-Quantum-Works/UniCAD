from pathlib import Path

import pytest
from test_mcp import CREATE, session_test

from unicad.domain.site import FeatureKind, buildable_area
from unicad.ingest.site_import import import_site
from unicad.store import ProjectStore, canonical_json, parse_project

SITES = Path(__file__).resolve().parents[1] / "fixtures" / "sites"
GROUND = str(SITES / "ground_mount_rajasthan.kml")
ROOFTOP = str(SITES / "rooftop_midc_pune.kml")
DXF = str(SITES / "site_plan_kutch.dxf")


async def new_project(c, **overrides) -> str:
    return (await c.call("project_create", **{**CREATE, **overrides}))["project_id"]


def reference_buildable(path: str, **setbacks) -> tuple[float, float]:
    from unicad.domain.site import Setbacks

    geometry = import_site(Path(path), None).geometry
    area = buildable_area(geometry, Setbacks(**setbacks))
    return area.ground_m2, area.roof_m2


@session_test
async def test_site_tools_are_listed(c):
    tools = {t.name for t in (await c.session.list_tools()).tools}
    assert {"site_import", "site_set_feature", "site_remove_feature", "site_set_setbacks", "site_summary"} <= tools
    docs = await c.session.read_resource("unicad://docs/tools")
    assert "### `site_import`" in docs.contents[0].text


@session_test
async def test_import_kml_and_report_buildable_area(c):
    pid = await new_project(c)
    res = await c.call("site_import", project_id=pid, file_path=GROUND)
    assert res["changed"] and res["revision"] == 2
    assert res["crs"] == "EPSG:32643"
    assert res["imported_by_kind"] == {"boundary": 1, "water": 1, "exclusion": 1, "transmission_line": 1, "road": 1}
    assert res["problems"] == []
    ground_m2, _ = reference_buildable(GROUND)
    assert res["buildable"]["ground"]["m2"] == pytest.approx(ground_m2, abs=0.1)

    summary = await c.call("site_summary", project_id=pid)
    assert summary["areas"]["boundary"]["acres"] == pytest.approx(200.0, rel=0.002)
    assert summary["buildable"]["ground"]["m2"] == pytest.approx(ground_m2, abs=0.1)
    assert summary["buildable"]["ground"]["acres"] < summary["areas"]["boundary"]["acres"]
    assert summary["counts"]["road"] == 1 and summary["path_lengths_m"]["road"] == pytest.approx(403.9, rel=0.01)
    assert summary["grid_scale_factor"] == pytest.approx(1.0, abs=0.002)

    trial = await c.call("site_summary", project_id=pid, boundary_setback_m=10, exclusion_setback_m=5)
    expected, _ = reference_buildable(GROUND, boundary_m=10, exclusion_m=5)
    assert trial["setbacks_are_trial"] and trial["buildable"]["ground"]["m2"] == pytest.approx(expected, abs=0.1)
    assert (await c.call("site_summary", project_id=pid))["setbacks_m"]["boundary_m"] == 0

    got = await c.call("project_get", project_id=pid, full=True)
    site = got["project"]["site"]
    assert site["crs"] == "EPSG:32643" and 27.0 < site["latitude"] < 27.2 and 72.3 < site["longitude"] < 72.4
    assert len(site["geometry"]["features"]) == 5


@session_test
async def test_import_is_idempotent_and_setbacks_persist(c):
    pid = await new_project(c)
    first = await c.call("site_import", project_id=pid, file_path=ROOFTOP)
    again = await c.call("site_import", project_id=pid, file_path=ROOFTOP)
    assert not again["changed"] and again["revision"] == first["revision"]
    assert again["replaced"] == [] and again["removed"] == []

    stored = await c.call("site_set_setbacks", project_id=pid, roof_edge_m=1.0, obstacle_m=0.5)
    assert stored["changed"] and stored["setbacks_m"]["roof_edge_m"] == 1.0
    assert not (await c.call("site_set_setbacks", project_id=pid, roof_edge_m=1.0))["changed"]
    _, roof_m2 = reference_buildable(ROOFTOP, roof_edge_m=1.0, obstacle_m=0.5)
    summary = await c.call("site_summary", project_id=pid)
    assert summary["buildable"]["roofs"]["m2"] == pytest.approx(roof_m2, abs=0.1)
    assert {r["id"] for r in summary["buildable"]["roofs"]["by_roof"]} == {"shed-a-roof", "shed-b-roof"}
    assert summary["setbacks_m"]["obstacle_m"] == 0.5

    older = await c.call("site_summary", project_id=pid, revision=first["revision"])
    assert older["setbacks_m"]["roof_edge_m"] == 0


@session_test
async def test_dxf_needs_a_georeference_then_uses_the_origin(c):
    pid = await new_project(c)
    msg = await c.fail("site_import", project_id=pid, file_path=DXF)
    assert "origin_lon" in msg and "Traceback" not in msg
    assert (await c.call("project_get", project_id=pid))["revision"] == 1
    res = await c.call("site_import", project_id=pid, file_path=DXF, origin_lon=69.8, origin_lat=23.45)
    assert res["crs"] == "EPSG:32642" and res["imported_by_kind"]["boundary"] == 1


@session_test
async def test_import_fills_in_site_position(c):
    pid = await new_project(c)
    await c.call("site_import", project_id=pid, file_path=ROOFTOP)
    lat_lon = (await c.call("project_get", project_id=pid, full=True))["project"]["site"]
    assert abs(lat_lon["longitude"] - 73.86) < 0.01 and abs(lat_lon["latitude"] - 18.76) < 0.01


@session_test
async def test_set_remove_and_error_messages(c):
    pid = await new_project(c)
    msg = await c.fail("site_set_feature", project_id=pid, id="plot", kind="boundary", coordinates=[[0, 0], [10, 0], [10, 10], [0, 10]])
    assert "origin_lon" in msg
    made = await c.call(
        "site_set_feature", project_id=pid, id="plot", kind="boundary", coordinates=[[0, 0], [100, 0], [100, 80], [0, 80]],
        origin_lon=73.86, origin_lat=18.76,
    )  # fmt: skip
    assert made["crs"] == "EPSG:32643" and made["buildable"]["ground"]["m2"] == 8000.0
    again = await c.call("site_set_feature", project_id=pid, id="plot", kind="boundary", coordinates=[[0, 0], [100, 0], [100, 80], [0, 80]])
    assert not again["changed"]
    await c.call("site_set_feature", project_id=pid, id="ahu", kind="obstacle", coordinates=[[10, 10], [14, 10], [14, 14], [10, 14]], height_m=1.8)
    await c.call("site_set_feature", project_id=pid, id="track", kind="road", coordinates=[[0, 40], [100, 40]], width_m=4)
    summary = await c.call("site_summary", project_id=pid)
    assert summary["buildable"]["ground"]["m2"] == pytest.approx(8000 - 16 - 400)
    lonlat = await c.call("site_set_feature", project_id=pid, id="pond", kind="water", coordinates=[[73.8604, 18.7603], [73.8606, 18.7603], [73.8606, 18.7605]], coordinate_system="lonlat")
    assert lonlat["imported"][0]["area_m2"] > 0

    bad = await c.fail("site_set_feature", project_id=pid, id="bow", kind="exclusion", coordinates=[[0, 0], [10, 10], [10, 0], [0, 10]])
    assert "Self-intersection" in bad and "Traceback" not in bad
    assert "polygon" in await c.fail("site_set_feature", project_id=pid, id="line", kind="boundary", coordinates=[[0, 0], [10, 10]], closed=False)

    removed = await c.call("site_remove_feature", project_id=pid, ids=["ahu", "pond"])
    assert removed["removed"] == ["ahu", "pond"]
    assert "ghost" in await c.fail("site_remove_feature", project_id=pid, ids=["ghost"])
    assert "no site geometry" in await c.fail("site_summary", project_id=await new_project(c, name="Empty site"))


@session_test
async def test_import_errors_are_readable(c):
    pid = await new_project(c)
    assert "no such file" in await c.fail("site_import", project_id=pid, file_path="/nonexistent/site.kml")
    assert "format" in await c.fail("site_import", project_id=pid, file_path=GROUND, format="shp")
    assert "not a feature kind" in await c.fail("site_import", project_id=pid, file_path=GROUND, kind_mapping={"pond": "lake"})
    assert "origin_lon and origin_lat together" in await c.fail("site_import", project_id=pid, file_path=DXF, origin_lon=69.8)


@session_test
async def test_geometry_survives_revisions_snapshots_and_diff(c):
    pid = await new_project(c)
    await c.call("site_import", project_id=pid, file_path=GROUND)
    head = (await c.call("site_import", project_id=pid, file_path=ROOFTOP, replace=True))["revision"]
    assert head == 3
    await c.call("project_snapshot", project_id=pid, label="surveyed")
    before = await c.call("site_summary", project_id=pid, revision=2)
    after = await c.call("site_summary", project_id=pid, revision="surveyed")
    assert before["counts"].get("boundary") == 1 and "roof" not in before["counts"]
    assert after["counts"]["roof"] == 2 and after["revision"] == head
    diff = await c.call("project_diff", project_id=pid, from_revision=1, to_revision=2)
    assert {f["path"] for f in diff["fields"]} == {"site.crs", "site.latitude", "site.longitude", "site.geometry"}
    site = (await c.call("project_get", project_id=pid, full=True))["project"]["site"]
    assert abs(site["latitude"] - 18.76) < 0.01


def test_store_round_trip_preserves_geometry(tmp_path):
    from unicad.domain.project import Party, Project, Segment, Site
    from unicad.domain.site import SiteGeometry
    from unicad.mcp.tools.project import design_basis

    geometry = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    project = Project(
        id="roundtrip", name="t", client=Party(name="c"), site=Site(name="s", geometry=geometry, crs=geometry.crs),
        design_basis=design_basis(415, None, {}), company_profile="default", segment=Segment.CI_ROOFTOP,
    )  # fmt: skip
    store = ProjectStore(tmp_path)
    assert store.save(project) == 1
    assert store.save(project) == 1
    loaded = parse_project(canonical_json(project))
    assert loaded == project and isinstance(loaded.site.geometry, SiteGeometry)
    assert store.load("roundtrip", 1).site.geometry == geometry
    assert loaded.site.geometry.of_kind(FeatureKind.ROOF)

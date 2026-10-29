import asyncio
import json
import math
from pathlib import Path

import pytest
from shapely.geometry import MultiPoint, Point, Polygon, box
from shapely.ops import unary_union
from test_mcp import CREATE, connect

from unicad.arrays.geometry import table_corners
from unicad.arrays.sun import sun_path
from unicad.domain.layout import Table

MODULE = "waaree-bin-21-635"
PLOT_180X120 = "75.7186380, 26.8865184\n75.7204509, 26.8865184\n75.7204509, 26.8875982\n75.7186380, 26.8875982\n"
LAYOUT = dict(module=MODULE, mounting="fixed_tilt", tilt_deg=26, table_rows=2, table_cols=18, orientation="portrait", modules_per_string=18,
              strings_per_inverter=10, target_dc_kwp=1371.6, n_blocks=12, half_tables=False, road_every_rows=6, switchyard_x=12, switchyard_y=60)  # fmt: skip


def run(tmp_path: Path, scenario):
    async def go():
        async with connect(tmp_path / "ws") as c:
            return await scenario(c)

    return asyncio.run(asyncio.wait_for(go(), 120))


async def rvunl_plot(c, tmp_path: Path, csv: str = PLOT_180X120, setback: float | None = 5) -> str:
    pid = (await c.call("project_create", **{**CREATE, "segment": "utility", "grid_voltage_v": 415}))["project_id"]
    f = tmp_path / "plot.csv"
    f.write_text("# boundary heerapura_plot_ASSUMED\n" + csv, encoding="utf-8")
    await c.call("site_import", project_id=pid, file_path=str(f), format="coords", coordinate_system="lonlat")
    if setback is not None:
        await c.call("site_set_setbacks", project_id=pid, boundary_m=setback)
    return pid


def stored(tmp_path: Path, pid: str) -> dict:
    return json.loads((tmp_path / "ws" / pid / "project.json").read_text(encoding="utf-8"))


def test_rvunl_blocks_are_compact_not_interleaved(tmp_path):
    async def scenario(c):
        pid = await rvunl_plot(c, tmp_path)
        res = await c.call("layout_place", project_id=pid, **LAYOUT)
        assert res["target_reached"] and len(res["blocks"]) == 12
        return pid

    blocks = stored(tmp_path, run(tmp_path, scenario))["array_layout"]["blocks"]
    footprints = {b["id"]: [t["origin"] for t in b["tables"]] for b in blocks}
    for b in blocks:
        ring = Polygon([(q["x"], q["y"]) for q in b["boundary"]])
        xs, ys = ring.exterior.xy
        diagonal = [e for e in zip(zip(xs, ys), zip(xs[1:], ys[1:])) if abs(e[0][0] - e[1][0]) > 0.01 and abs(e[0][1] - e[1][1]) > 0.01]
        assert not diagonal, f"{b['id']} outline has diagonal edges {diagonal[:2]}"
        for other, origins in footprints.items():
            if other != b["id"]:
                inside = [o for o in origins if ring.contains(box(o["x"] + 1, o["y"] + 1, o["x"] + 20, o["y"] + 3))]
                assert not inside, f"tables of {other} stand inside the outline of {b['id']}"


def swept_shadow_m2(x: float, y: float, tables: list[Polygon], lat: float, height: float) -> float:
    path = sun_path(lat, 9.0, 15.0)
    pts = [(x, y)] + [
        (x + height / math.tan(math.radians(p.elevation_deg)) * math.sin(math.radians(p.azimuth_deg + 180)),
         y + height / math.tan(math.radians(p.elevation_deg)) * math.cos(math.radians(p.azimuth_deg + 180)))
        for p in path
    ]  # fmt: skip
    hull = MultiPoint(pts).convex_hull
    return sum(hull.intersection(t).area for t in tables)


def test_rvunl_single_mast_stands_where_it_shades_least_and_says_so(tmp_path):
    async def scenario(c):
        pid = await rvunl_plot(c, tmp_path)
        await c.call("layout_place", project_id=pid, **LAYOUT)
        res = await c.call("lps_design", project_id=pid, method="ese", level="III", ese_delta_t_us=60, mast_height_m=6)
        return pid, res["lps"]

    pid, lps = run(tmp_path, scenario)
    assert lps["terminal_count"] == 1 and lps["uncovered_count"] == 0
    stored_project = stored(tmp_path, pid)
    tables = [Polygon(table_corners(Table.model_validate(t))) for b in stored_project["array_layout"]["blocks"] for t in b["tables"]]
    union = unary_union(tables)
    mast = lps["terminals"][0]
    here = Point(mast["x"], mast["y"])
    assert union.distance(here) >= 1.4, "the mast stands on or against a table"
    lat = stored_project["site"]["geometry"]["origin"]["lat"]
    radius = lps["radius_m"]
    corners = [c for t in tables for c in t.exterior.coords]
    best = math.inf
    for x in range(8, 175, 2):
        for y in range(8, 112, 2):
            p = Point(x, y)
            if union.distance(p) >= 1.5 and all(math.dist((x, y), q) <= radius for q in corners):
                best = min(best, swept_shadow_m2(x, y, tables, lat, 6.0))
    assert best < math.inf
    assert swept_shadow_m2(mast["x"], mast["y"], tables, lat, 6.0) <= best * 1.1
    assert mast["shade_m2"] == pytest.approx(swept_shadow_m2(mast["x"], mast["y"], tables, lat, 6.0), rel=0.02)
    notes = " ".join(lps["assumptions"])
    assert "only terminal" in notes and "mast shadow over tables" in notes and "protection circle beyond the plot" in notes


INVERTER = "huawei-sun2000-100ktl-m2"


async def rvunl_lt_plant(c, tmp_path: Path) -> str:
    pid = await rvunl_plot(c, tmp_path)
    await c.call("layout_place", project_id=pid, **LAYOUT)
    await c.call(
        "network_autobuild", project_id=pid, template="ci_rooftop", dc_kwp=1371.6, module=MODULE, inverter=INVERTER, modules_per_string=18,
        strings_per_inverter=10, grid_voltage_v=415,
    )  # fmt: skip
    await c.call("network_remove", project_id=pid, ids=["ac_iso", "net_ct", "net_meter", "inc_brk", "ltp", "load"])
    await c.call(
        "network_connect", project_id=pid, src="solar_ct:out", dst="poi:in", service="lv_ac", cable="polycab-lt-xlpe-al-3.5c-300-arm", runs=9,
        length_m=60, laying="tray", edge_id="e_ct_poi",
    )  # fmt: skip
    await c.call("project_design_basis", project_id=pid, earthing={"soil_resistivity_ohm_m": 100, "fault_current_ka": 10, "fault_clearing_s": 1})
    return pid


def loaded(tmp_path: Path, pid: str):
    import unicad.earthing  # noqa: F401
    from unicad.mcp import Config
    from unicad.mcp.workspace import Workspace

    ws = Workspace(Config(workspace=tmp_path / "ws"))
    return ws, ws.load(pid, None)[0]


def test_rvunl_earthing_has_no_station_details_for_string_inverters(tmp_path):
    from unicad.earthing import design_earthing

    pid = run(tmp_path, lambda c: rvunl_lt_plant(c, tmp_path))
    ws, p = loaded(tmp_path, pid)
    assert len(p.array_layout.equipment_by_node) >= 12
    d = design_earthing(p, catalog=ws.catalog)
    assert [g.name for g in d.layout.stations if g.kind == "station"] == []
    assert not [i for i in d.layout.items if i.id.endswith("~station")]
    tags = {i.tag for i in d.layout.items}
    assert {"ACCB-01"} <= tags or any(i.kind == "acdb" for i in d.layout.items)


def test_rvunl_accb_and_client_panel_stand_outside_the_tables_with_a_route(tmp_path):
    async def scenario(c):
        pid = await rvunl_lt_plant(c, tmp_path)
        return pid, await c.call("layout_summary", project_id=pid)

    pid, summary = run(tmp_path, scenario)
    ws, p = loaded(tmp_path, pid)
    union = unary_union([Polygon(table_corners(t)) for t in p.array_layout.table_by_id.values()])
    panels = {e.kind: e for e in p.array_layout.equipment if e.kind in ("acdb", "poi_grid")}
    assert set(panels) == {"acdb", "poi_grid"}
    for e in panels.values():
        assert union.distance(Point(e.at.x, e.at.y)) >= 1.5, f"{e.tag} stands on or against the tables"
    assert math.dist((panels["acdb"].at.x, panels["acdb"].at.y), (panels["poi_grid"].at.x, panels["poi_grid"].at.y)) >= 5
    assert summary["field_equipment"]["acdb"] == 1 and summary["field_equipment"]["poi_grid"] == 1
    route = summary["lt_route"]
    assert len(route) == 1 and route[0]["from"] == panels["acdb"].tag
    pts = route[0]["points"]
    assert pts[0] == [panels["acdb"].at.x, panels["acdb"].at.y] and pts[-1] == [panels["poi_grid"].at.x, panels["poi_grid"].at.y]
    from shapely.geometry import LineString

    assert LineString(pts).intersection(union).length == 0


PLOT_170X110 = "75.7187380, 26.8865184\n75.7203509, 26.8865184\n75.7203509, 26.8875082\n75.7187380, 26.8875082\n"


def test_site_import_replace_keeps_the_setbacks(tmp_path):
    async def scenario(c):
        pid = await rvunl_plot(c, tmp_path, setback=5)
        before = await c.call("site_set_setbacks", project_id=pid, boundary_m=5, exclusion_m=3)
        smaller = tmp_path / "smaller.csv"
        smaller.write_text("# boundary smaller\n" + PLOT_170X110, encoding="utf-8")
        again = await c.call("site_import", project_id=pid, file_path=str(smaller), format="coords", coordinate_system="lonlat", replace=True)
        return before, again

    before, again = run(tmp_path, scenario)
    assert again["setbacks_m"]["boundary_m"] == 5 and again["setbacks_m"]["exclusion_m"] == 3
    assert again["buildable"]["ground"]["m2"] < again["imported"][0]["area_m2"]
    assert any("setbacks" in p["message"] for p in again.get("problems", [])), "the kept setbacks are reported"


PLOT_150X100 = "75.7187891, 26.8866084\n75.7202998, 26.8866084\n75.7202998, 26.8875082\n75.7187891, 26.8875082\n"


def test_layout_place_reports_shortfall_and_a_plot_that_fits(tmp_path):
    async def scenario(c):
        pid = await rvunl_plot(c, tmp_path, PLOT_150X100)
        short = await c.call("layout_place", project_id=pid, **LAYOUT)
        w, h = short["shortfall"]["plot_that_fits_m"]
        grown = tmp_path / "grown"
        grown.mkdir()
        lon = 75.7187891
        lat = 26.8866084
        dlon = (w / (111320 * math.cos(math.radians(lat)))) * 1.0
        dlat = h / 110574
        pid2 = (await c.call("project_create", **{**CREATE, "name": "grown", "segment": "utility", "grid_voltage_v": 415}))["project_id"]
        f = grown / "plot.csv"
        f.write_text(f"# boundary grown\n{lon}, {lat}\n{lon + dlon}, {lat}\n{lon + dlon}, {lat + dlat}\n{lon}, {lat + dlat}\n", encoding="utf-8")
        await c.call("site_import", project_id=pid2, file_path=str(f), format="coords", coordinate_system="lonlat")
        await c.call("site_set_setbacks", project_id=pid2, boundary_m=5)
        return short, await c.call("layout_place", project_id=pid2, **LAYOUT)

    short, fitted = run(tmp_path, scenario)
    assert short["target_reached"] is False and short["metrics"]["dc_kwp"] == pytest.approx(800.1)
    s = short["shortfall"]
    assert s["target_kwp"] == 1371.6 and s["short_kwp"] == pytest.approx(571.5) and s["plot_now_m"] == pytest.approx([150, 100], abs=1.5)
    assert s["plot_that_fits_m"][0] > 150 and s["plot_that_fits_m"][1] > 100
    assert any("fits 800.1 kWp" in p["message"] and "scaled to about" in p["message"] for p in short["problems"])
    assert fitted["target_reached"] is True, f"the plot the shortfall names does not fit the target: {fitted['metrics']}"

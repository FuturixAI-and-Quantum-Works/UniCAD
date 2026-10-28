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
    f.write_text("# boundary heerapura_plot_ASSUMED\n" + csv)
    await c.call("site_import", project_id=pid, file_path=str(f), format="coords", coordinate_system="lonlat")
    if setback is not None:
        await c.call("site_set_setbacks", project_id=pid, boundary_m=setback)
    return pid


def stored(tmp_path: Path, pid: str) -> dict:
    return json.loads((tmp_path / "ws" / pid / "project.json").read_text())


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

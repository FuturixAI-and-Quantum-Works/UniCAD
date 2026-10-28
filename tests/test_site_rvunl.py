import asyncio
import json
from pathlib import Path

from shapely.geometry import Polygon, box
from test_mcp import CREATE, connect

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

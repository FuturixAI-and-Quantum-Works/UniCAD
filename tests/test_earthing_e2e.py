"""Earthing regressions replayed from the close-e2e run 1 call log (Acme 793 kWp Pune rooftop, Thar 40 MWac Rajasthan utility) over MCP stdio."""

import asyncio
import json
import math
from pathlib import Path

import pytest
from shapely.geometry import Point as SPoint
from shapely.geometry import Polygon
from test_mcp import connect

from unicad.catalog.store import load_catalog
from unicad.domain.project import Project
from unicad.earthing import design_earthing
from unicad.earthing.sizing import Grid, grid_check
from unicad.rules import run_rules

ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / "fixtures" / "sites"
CATALOG = load_catalog()

ROOF_CREATE = {
    "name": "800 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop",
    "site": "Shree Ganesh Textiles factory roof, MIDC Chakan, Pune", "grid_voltage_v": 415, "state": "Maharashtra",
    "address": "MIDC Chakan, Pune, Maharashtra", "utility": "MSEDCL", "client_code": "SGT", "project_id": "sgt-chakan",
    "ambient_min_c": 5, "ambient_max_c": 45, "altitude_m": 650, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.2, "power_factor": 0.99,
}  # fmt: skip
UTILITY_CREATE = {
    "name": "40 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility",
    "site": "Thar Renewables solar park, Phalodi, Rajasthan", "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPN",
    "client_code": "TRL", "project_id": "thar-40mw", "ambient_min_c": 2, "ambient_max_c": 48, "altitude_m": 230, "vdrop_dc_max": 0.015,
    "vdrop_ac_max": 0.01, "dc_ac_ratio_target": 1.2, "power_factor": 0.95,
}  # fmt: skip
YARD = {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1, "surface_resistivity_ohm_m": 3000, "surface_layer_m": 0.15, "switchyard_m": [120, 100]}


def _replay(tmp: Path, steps) -> tuple[dict, Path]:
    out: dict = {}

    async def run():
        async with connect(tmp / "ws") as c:
            await steps(c, out)

    asyncio.run(asyncio.wait_for(run(), 240))
    return out, tmp / "ws"


def _load(ws: Path, pid: str, rev: int | None = None) -> Project:
    path = ws / pid / ("project.json" if rev is None else f"revisions/{rev}.json")
    return Project.model_validate(json.loads(path.read_text()), context={"catalog": CATALOG})


@pytest.fixture(scope="module")
def roof(tmp_path_factory):
    async def steps(c, out):
        pid = (await c.call("project_create", **ROOF_CREATE))["project_id"]
        await c.call("site_import", project_id=pid, file_path=str(SITES / "rooftop_midc_pune.kml"))
        await c.call("site_set_setbacks", project_id=pid, roof_edge_m=1.0, obstacle_m=0.6)
        await c.call("layout_place", project_id=pid, module="waaree-bin-08-580", mounting="rooftop_flush", modules_per_string=19, strings_per_inverter=12,
                     target_dc_kwp=793.44, table_rows=1, table_cols=19, half_tables=False, n_blocks=1)  # fmt: skip
        await c.call("network_autobuild", project_id=pid, template="ci_rooftop", ac_kw=660, module="waaree-bin-08-580", inverter="sungrow-sg110cx",
                     modules_per_string=19, strings_per_inverter=12, connection="lt_panel")  # fmt: skip
        await c.call("lps_design", project_id=pid, method="ese", level="II", ese_delta_t_us=60, mast_height_m=5)
        out["design"] = await c.call("earthing_design", project_id=pid, soil_resistivity=100, fault_ka=25, clearing_s=1)
        out["summary"] = await c.call("earthing_summary", project_id=pid)
        out["render"] = await c.call("drawings_render", project_id=pid, kinds=["layout", "earthing"], formats=["dxf"])
        out["pid"] = pid

    return _replay(tmp_path_factory.mktemp("roof"), steps)


@pytest.fixture(scope="module")
def roof_ground_station(tmp_path_factory):
    async def steps(c, out):
        pid = (await c.call("project_create", **ROOF_CREATE))["project_id"]
        await c.call("site_import", project_id=pid, file_path=str(SITES / "rooftop_midc_pune.kml"))
        await c.call("site_set_setbacks", project_id=pid, roof_edge_m=1.0, obstacle_m=0.6)
        await c.call("layout_place", project_id=pid, module="waaree-bin-08-580", mounting="rooftop_flush", modules_per_string=19, strings_per_inverter=12,
                     target_dc_kwp=793.44, table_rows=1, table_cols=19, half_tables=False, n_blocks=1, station_x=66.0, station_y=8.0)  # fmt: skip
        await c.call("network_autobuild", project_id=pid, template="ci_rooftop", ac_kw=660, module="waaree-bin-08-580", inverter="sungrow-sg110cx",
                     modules_per_string=19, strings_per_inverter=12, connection="lt_panel")  # fmt: skip
        await c.call("earthing_design", project_id=pid, soil_resistivity=100, fault_ka=25, clearing_s=1)
        out["pid"] = pid

    return _replay(tmp_path_factory.mktemp("roofground"), steps)


@pytest.fixture(scope="module")
def utility(tmp_path_factory):
    async def steps(c, out):
        pid = (await c.call("project_create", **UTILITY_CREATE))["project_id"]
        await c.call("site_import", project_id=pid, file_path=str(SITES / "ground_mount_rajasthan.kml"))
        await c.call("site_set_setbacks", project_id=pid, boundary_m=10, exclusion_m=10)
        await c.call("layout_place", project_id=pid, module="waaree-bin-21-635", mounting="fixed_tilt", tilt_deg=25, table_rows=2, table_cols=28, road_every_rows=12,
                     target_dc_kwp=48646.08, n_blocks=6, modules_per_string=28, strings_per_inverter=24)  # fmt: skip
        await c.call("network_autobuild", project_id=pid, template="utility_string", module="waaree-bin-21-635", inverter="sungrow-sg350hx", modules_per_string=28, inverters_per_block=20)
        await c.call("lps_design", project_id=pid, method="ese", level="III", ese_delta_t_us=60, mast_height_m=6)
        out["unsized"] = await c.call("earthing_design", project_id=pid, soil_resistivity=150, fault_ka=25, clearing_s=1, surface_resistivity=3000, surface_layer_m=0.15)
        out["unsized_rev"] = out["unsized"]["revision"]
        out["validate_unsized"] = await c.call("validate", project_id=pid, families=["earthing"])
        await c.call("project_design_basis", project_id=pid, earthing=YARD)
        out["redesign"] = await c.call("earthing_design", project_id=pid, soil_resistivity=150, fault_ka=25, clearing_s=1)
        out["basis_after"] = (await c.call("project_get", project_id=pid, full=True))["project"]["design_basis"]["earthing"]
        out["summary"] = await c.call("earthing_summary", project_id=pid)
        out["sized"] = await c.call("earthing_design", project_id=pid, switchyard_m=[80, 60])
        out["basis_sized"] = (await c.call("project_get", project_id=pid, full=True))["project"]["design_basis"]["earthing"]
        out["pid"] = pid

    return _replay(tmp_path_factory.mktemp("utility"), steps)


# --- M5: rooftop pits -------------------------------------------------------------------------


def test_rooftop_pits_stand_at_the_building_perimeter_inside_the_plot(roof):
    out, ws = roof
    p = _load(ws, out["pid"])
    d = design_earthing(p)
    site = Polygon([(q.x, q.y) for f in p.site.geometry.features if f.kind.value == "boundary" for q in f.polygon])
    roofs = [Polygon([(q.x, q.y) for q in f.polygon]) for f in p.site.geometry.features if f.kind.value == "roof"]
    for pit in d.layout.pits:
        at = SPoint(pit.at.x, pit.at.y)
        assert site.contains(at), f"{pit.tag} at ({pit.at.x}, {pit.at.y}) is outside the plot"
        assert min(r.exterior.distance(at) for r in roofs) <= 8.0, f"{pit.tag} is not at a building perimeter"
        assert not any(r.contains(at) for r in roofs)
    tables = len(p.array_layout.table_by_id)
    assert len(d.layout.pits) < tables / 2, "rooftop pit count follows down conductors, equipment and the resistance target, not the table count"
    assert out["design"]["earthing"]["pits"] == len(d.layout.pits)
    assert d.resistance_ohm <= d.target_ohm


def _roof_polys(p: Project) -> list[Polygon]:
    return [Polygon([(q.x, q.y) for q in f.polygon]) for f in p.site.geometry.features if f.kind.value == "roof"]


def test_rooftop_station_is_earthed_by_down_conductors_to_pits_at_its_building_edge(roof):
    out, ws = roof
    p = _load(ws, out["pid"])
    d = design_earthing(p)
    station = p.array_layout.blocks[0].station
    roofs = _roof_polys(p)
    here = next(r for r in roofs if r.contains(SPoint(station.x, station.y)))
    g = next(x for x in d.layout.stations if x.name == "BLOCK-01 STATION")
    assert not any(x.name == "PLANT EQUIPMENT AREA" for x in d.layout.stations)
    assert {"acdb", "ltp"} <= set(g.items)
    for it in d.layout.items:
        if it.id in g.items:
            assert it.on_roof and here.contains(SPoint(it.at.x, it.at.y))
    by_tag = {x.tag: x for x in d.layout.pits}
    assert len(g.pits) >= 2
    for t in g.pits:
        at = SPoint(by_tag[t].at.x, by_tag[t].at.y)
        assert not any(r.contains(at) for r in roofs)
        assert here.exterior.distance(at) == pytest.approx(1.5, abs=0.01)
    downs = [r for r in d.layout.runs if r.role.value == "down" and r.links == ("B01",)]
    assert len(downs) >= 2 and not any(r.buried for r in downs)
    ends = {(r.points[-1].x, r.points[-1].y) for r in downs}
    assert ends <= {(by_tag[t].at.x, by_tag[t].at.y) for t in g.pits}


def test_ground_station_on_a_rooftop_project_has_its_own_pit_cluster_at_the_layout_station(roof_ground_station):
    out, ws = roof_ground_station
    p = _load(ws, out["pid"])
    d = design_earthing(p)
    station = p.array_layout.blocks[0].station
    assert (station.x, station.y) == (66.0, 8.0)
    roofs = _roof_polys(p)
    g = next(x for x in d.layout.stations if x.name == "BLOCK-01 STATION")
    assert not any(x.name == "PLANT EQUIPMENT AREA" for x in d.layout.stations)
    x0, y0, x1, y1 = g.ring
    assert (x0 + x1) / 2 == pytest.approx(station.x, abs=0.01) and (y0 + y1) / 2 == pytest.approx(station.y, abs=0.01)
    inside = [it for it in d.layout.items if it.id in g.items]
    assert inside and not any(it.on_roof for it in inside)
    by_tag = {x.tag: x for x in d.layout.pits}
    for t in g.pits:
        at = SPoint(by_tag[t].at.x, by_tag[t].at.y)
        assert x0 - 0.01 <= at.x <= x1 + 0.01 and y0 - 0.01 <= at.y <= y1 + 0.01
        assert not any(r.contains(at) for r in roofs)
    assert not any(r.role.value == "down" and r.links == ("B01",) for r in d.layout.runs)


# --- M6: one governing resistance -------------------------------------------------------------


def test_rooftop_reports_one_governing_resistance_with_its_method(roof):
    out, ws = roof
    e, s = out["design"]["earthing"], out["summary"]["earthing"]
    assert e["resistance_ohm"] == s["resistance_ohm"]
    assert s["resistance_method"] and s["resistance_ohm"] == max(s["resistance_parts_ohm"].values())
    for w in s["warnings"]:
        assert "limits the earth resistance" not in w or float(w.split("about ")[1].split(" ")[0]) <= s["resistance_ohm"] + 1e-3
    p = _load(ws, out["pid"])
    f = next(x for x in run_rules(p, CATALOG, ["earthing"]).by_rule("earthing.electrode_resistance") if x.unit == "ohm")
    assert f.measured == s["resistance_ohm"]
    sheet = next(x for x in out["render"]["sheets"] if x["kind"] == "earthing")
    assert sheet.get("meta", {}).get("resistance_ohm", round(s["resistance_ohm"], 3)) == round(s["resistance_ohm"], 3)


# --- M7: equipment pits at block stations -----------------------------------------------------


def test_block_transformer_pits_sit_at_their_block_station(utility):
    out, ws = utility
    p = _load(ws, out["pid"])
    d = design_earthing(p)
    assert not any("all equipment is placed at the plant" in w for w in d.warnings)
    by_tag = {x.tag: x for x in d.layout.pits}
    idt = [it for it in d.layout.items if it.kind == "transformer"]
    assert len(idt) == 6
    blocks = [g for g in d.layout.stations if g.name.startswith("BLOCK-")]
    assert len(blocks) == 6
    for g in blocks:
        x0, y0, x1, y1 = g.ring
        roles = [by_tag[t].role.value for t in g.pits]
        assert roles.count("EPN") >= 2 and roles.count("EPB") >= 2, (g.name, roles)
        assert sum(1 for it in idt if it.id in g.items) == 1, g.name
        for t in g.pits:
            q = by_tag[t].at
            assert x0 - 1e-6 <= q.x <= x1 + 1e-6 and y0 - 1e-6 <= q.y <= y1 + 1e-6
    yard = d.layout.switchyard
    assert yard is not None
    in_yard = [x for x in d.layout.pits if yard.x0 - 1e-6 <= x.at.x <= yard.x1 + 1e-6 and yard.y0 - 1e-6 <= x.at.y <= yard.y1 + 1e-6]
    assert sum(1 for x in in_yard if x.role.value == "EPN") <= 2, "only plant-level transformers earth in the switchyard mat"


# --- M8: IEEE 80 split and consistency --------------------------------------------------------


def test_switchyard_size_is_never_silently_assumed(utility):
    out, _ = utility
    e = out["unsized"]["earthing"]
    assert "switchyard_grid" not in e
    assert any("switchyard_m" in w for w in e["warnings"])
    f = [x for x in out["validate_unsized"]["findings"] if x["rule_id"] == "earthing.touch_step"]
    assert f and all(x["severity"] == "warning" for x in f)


def test_switchyard_touch_and_step_use_one_split_grid_current(utility):
    out, _ = utility
    g = out["summary"]["earthing"]["switchyard_grid"]
    s = out["summary"]["earthing"]
    assert g["size_m"] == [120.0, 100.0]
    sf = g["rest_of_plant_ohm"] / (g["rest_of_plant_ohm"] + g["rg_ohm"])
    assert g["split_factor"] == pytest.approx(sf, rel=1e-3)
    assert g["grid_current_a"] == pytest.approx(25000 * g["split_factor"], rel=1e-3)
    assert g["gpr_v"] == pytest.approx(g["grid_current_a"] * g["rg_ohm"], rel=2e-3)
    assert g["gpr_v"] == pytest.approx(25000 * s["resistance_ohm"], rel=2e-3)
    assert g["e_step_v"] <= g["gpr_v"] and g["e_mesh_v"] <= g["gpr_v"]
    assert any("split factor" in a for a in s["assumptions"])


@pytest.mark.parametrize("rho", [50.0, 150.0, 1000.0])
@pytest.mark.parametrize("size", [(40.0, 30.0), (120.0, 100.0), (20.0, 15.0)])
@pytest.mark.parametrize("spacing", [2.0, 3.0, 6.0, 10.0])
def test_step_and_mesh_voltage_never_exceed_the_gpr(rho, size, spacing):
    g = Grid(size[0], size[1], spacing, 0.6, 0.0375, rods=8, rod_length=3.0)
    for sf in (1.0, 0.3, 0.05):
        c = grid_check(rho, g, 25000 * sf, 1.0, 3000, 0.15)
        assert c.e_s <= c.gpr and c.e_mesh <= c.gpr, (c.e_s, c.e_mesh, c.gpr)


# --- M9: switchyard size and stored basis -----------------------------------------------------


def test_earthing_design_keeps_values_stored_by_project_design_basis(utility):
    out, _ = utility
    b = out["basis_after"]
    assert b["switchyard_m"] == [120.0, 100.0] and b["surface_resistivity_ohm_m"] == 3000.0 and b["surface_layer_m"] == 0.15
    assert "switchyard_grid" in out["redesign"]["earthing"]
    s = out["basis_sized"]
    assert s["switchyard_m"] == [80.0, 60.0] and s["soil_resistivity_ohm_m"] == 150.0 and s["fault_current_ka"] == 25.0
    assert out["sized"]["earthing"]["switchyard_grid"]["size_m"] == [80.0, 60.0]


# --- m23: earthing sheet scale ----------------------------------------------------------------


def test_rooftop_earthing_sheet_is_at_least_as_large_scale_as_the_layout(roof):
    out, _ = roof
    sheets = {s["kind"]: s for s in out["render"]["sheets"]}
    scale = {k: int(s["drawing_scale"].split(":")[1]) for k, s in sheets.items()}
    assert scale["earthing"] <= scale["layout"], scale
    assert scale["earthing"] <= 1000
    assert math.isfinite(out["summary"]["earthing"]["resistance_ohm"])

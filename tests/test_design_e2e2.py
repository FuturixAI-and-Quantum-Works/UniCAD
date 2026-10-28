"""Regressions from the second end-to-end verifier run (close-e2e-2), replayed from its logged MCP tool arguments (stdio)."""

import asyncio
from pathlib import Path

from test_mcp import connect

ROOT = Path(__file__).resolve().parents[1]
PUNE = ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml"
RAJ = ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml"
KUTCH = ROOT / "fixtures" / "sites" / "site_plan_kutch.dxf"
ACME = ROOT / "profiles" / "samples" / "acme"

A_MOD, A_INV = "adani-solar-elan-shine-topcon-g2g-gen-ii-560-wp", "huawei-sun2000-100ktl-m2"
A_CREATE = {
    "name": "Shree Ganesh Textiles 800 kWp Rooftop", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "MIDC Chakan, Pune",
    "grid_voltage_v": 415, "project_id": "sgt-chakan", "state": "Maharashtra", "utility": "MSEDCL", "client_code": "SGT",
    "address": "Plot B-12, MIDC Chakan Phase II, Pune 410501",
}  # fmt: skip
A_BASIS = {
    "project_id": "sgt-chakan", "ambient_min_c": 8, "ambient_max_c": 42, "altitude_m": 640, "vdrop_dc_max": 0.02, "vdrop_ac_max": 0.02,
    "dc_ac_ratio_target": 1.2, "power_factor": 0.99,
    "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "user input: MSEDCL LT fault level 25 kA 1 s"}],
    "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1},
    "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5},
}  # fmt: skip
A_PLACE = {
    "project_id": "sgt-chakan", "module": A_MOD, "mounting": "rooftop_flush", "pitch_method": "gap", "row_gap_m": 0.6, "table_gap_m": 0.6,
    "table_rows": 2, "target_dc_kwp": 806.4, "modules_per_string": 20, "strings_per_inverter": 12,
}  # fmt: skip
A_AUTOBUILD = {
    "project_id": "sgt-chakan", "template": "ci_rooftop", "dc_kwp": 806.4, "module": A_MOD, "inverter": A_INV, "modules_per_string": 20,
    "strings_per_inverter": 12, "connection": "lt_panel",
}  # fmt: skip


def run(coro, timeout=300):
    asyncio.run(asyncio.wait_for(coro, timeout))


async def scenario_a(c, place=None):
    await c.call("company_onboard", dxf_path=str(ACME / "acme-company.dxf"), answers=str(ACME / "answers.yaml"))
    await c.call("project_create", **A_CREATE)
    await c.call("company_assign", project_id="sgt-chakan", profile_id="acme")
    await c.call("project_design_basis", **A_BASIS)
    await c.call("site_import", project_id="sgt-chakan", file_path=str(PUNE))
    await c.call("site_set_setbacks", project_id="sgt-chakan", roof_edge_m=1.0, obstacle_m=0.6)
    placed = await c.call("layout_place", **(A_PLACE | (place or {})))
    built = await c.call("network_autobuild", **A_AUTOBUILD)
    return placed, built


def test_n10_breaker_protects_the_cable_beyond_its_isolator(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            _, built = await scenario_a(c)
            assert not [f for f in built["findings"] if f["rule_id"] == "cables.protection"], built["findings"]
            p = (await c.call("project_get", project_id="sgt-chakan", full=True))["project"]
            edge = next(e for e in p["network"]["edges"] if e["id"] == "e_iso_ltp")
            assert (edge["cable"], edge["runs"]) != ("polycab-lt-xlpe-al-1c-300-arm", 2), edge
            await c.call("network_update", project_id="sgt-chakan", id="e_iso_ltp", fields={"cable": "polycab-lt-xlpe-al-1c-300-arm", "runs": 2})
            v = await c.call("validate", project_id="sgt-chakan", families=["cables"])
            hits = [f for f in v["findings"] if f["rule_id"] == "cables.protection"]
            assert {h["subjects"][0] for h in hits} == {"acdb_brk", "inc_brk"} and all(h["subjects"][1] == "e_iso_ltp" and h["measured"] == 1250 for h in hits), hits

    run(go())


def test_n12_autobuild_reads_the_company_thresholds_validate_reads(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            _, built = await scenario_a(c)
            v = await c.call("validate", project_id="sgt-chakan")
            ratio = lambda fs: [f for f in fs if f["rule_id"].startswith("ratio.")]  # noqa: E731
            assert ratio(built["findings"]) == ratio(v["findings"]) == [], (built["findings"], v["findings"])
            assert built["company_thresholds"] == v["company_thresholds"] and built["company_thresholds"]["values"]["dc_ac_band"] == 0.15
            assert built["rules"] == v["counts"]

    run(go())


def test_n11_lt_cable_short_circuit_uses_the_breaker_instantaneous_trip_not_one_second(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await scenario_a(c)
            x = await c.call("explain_finding", project_id="sgt-chakan", rule_id="cables.short_circuit", subject="e_brk_ct")
            (f,) = x["explanations"]
            t = next(q for q in f["inputs"] if q["name"] == "t")
            assert t["value"] == 0.1 and t["source"].startswith("assumption: instantaneous trip of"), t
            assert any("IEC 60364-4-43" in a for a in f["assumptions"])
            assert f["limit"] == round(25000 * 0.1**0.5 / 94, 2)

    run(go())


B_MOD, B_INV = "vikram-solar-hypersol-m10r-144-590-wp", "sungrow-sg350hx"
C_CREATE = {
    "name": "Kutch Hybrid 20 MWac PV + 10 MW 20 MWh BESS", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch, Gujarat",
    "grid_voltage_v": 33000, "project_id": "kutch-hybrid", "state": "Gujarat", "utility": "GETCO", "client_code": "KHP",
}  # fmt: skip
C_BASIS = {
    "project_id": "kutch-hybrid", "ambient_min_c": 5, "ambient_max_c": 46, "altitude_m": 20, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02,
    "dc_ac_ratio_target": 1.1, "power_factor": 0.95,
    "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "user input: GETCO 33 kV fault level 25 kA 1 s"}],
    "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1, "switchyard_m": [60, 45]},
    "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
}  # fmt: skip
C_PLACE = {
    "project_id": "kutch-hybrid", "module": B_MOD, "mounting": "fixed_tilt", "tilt_deg": 23, "table_rows": 2, "modules_per_string": 27,
    "strings_per_inverter": 24, "n_blocks": 3, "target_dc_kwp": 21792.24, "road_every_rows": 12,
}  # fmt: skip
C_AUTOBUILD = {
    "project_id": "kutch-hybrid", "template": "hybrid", "ac_kw": 20064, "module": B_MOD, "inverter": B_INV, "modules_per_string": 27,
    "strings_per_inverter": 24, "inverters_per_block": 19, "bess_kw": 10000, "bess_kwh": 20000, "pcs": "sungrow-st5015kwh-2500kw-2h",
    "battery": "sungrow-st5015kwh", "export_limit_kw": 30000,
}  # fmt: skip


async def scenario_c(c):
    await c.call("project_create", **C_CREATE)
    await c.call("project_design_basis", **C_BASIS)
    await c.call("site_import", project_id="kutch-hybrid", file_path=str(KUTCH), origin_lon=69.8, origin_lat=23.45)
    await c.call("site_set_setbacks", project_id="kutch-hybrid", boundary_m=10, exclusion_m=10)
    placed = await c.call("layout_place", **C_PLACE)
    built = await c.call("network_autobuild", **C_AUTOBUILD)
    return placed, built


def test_c_hybrid_places_inverters_bess_and_switchyard_and_validate_says_how_to_fix_touch_step(tmp_path):
    async def go():
        from shapely.geometry import Point as SPoint
        from shapely.geometry import Polygon

        async with connect(tmp_path / "ws") as c:
            placed, built = await scenario_c(c)
            yard = placed["switchyard"]
            assert yard and yard["basis"].startswith("derived") and "transmission line" in yard["basis"], yard
            p = (await c.call("project_get", project_id="kutch-hybrid", full=True))["project"]
            lay, net = p["array_layout"], p["network"]
            assert lay["switchyard"]["at"] == {"x": yard["x"], "y": yard["y"]}
            kinds = {}
            for e in lay["equipment"]:
                kinds.setdefault(e["kind"], []).append(e)
            # N3: 57 string inverters in the field, not at the block stations; their AC cables are routed, not the 60 m placeholder
            assert len(kinds["inverter"]) == 57
            stations = [(b["station"]["x"], b["station"]["y"]) for b in lay["blocks"]]
            assert all(min(abs(i["at"]["x"] - x) + abs(i["at"]["y"] - y) for x, y in stations) > 5 for i in kinds["inverter"])
            edges = {e["id"]: e for e in net["edges"]}
            inv_ac = [e for e in edges.values() if e["id"].startswith("e_inv_brk")]
            assert inv_ac and all(e["length_m"] != 60.0 for e in inv_ac), [e["length_m"] for e in inv_ac]
            assert not any("inv_accb 60" in a for a in built["assumptions"])
            strings = max(e.get("length_m") or 0 for e in edges.values() if e["id"].startswith("e_str_b"))
            assert strings < 200, strings
            # N6: a breaker between every PCS and its transformer; containers, PCS and transformers stand on the layout
            nodes = {n["id"]: n for n in net["nodes"]}
            assert nodes["pcs_brk"]["kind"] == "breaker" and nodes["pcs_brk"]["spec"]
            assert edges["e_pcs_brk"]["src"] == "pcs:ac" and edges["e_brk_tr"]["dst"] == "bess_tr:lv"
            assert len(kinds["pcs"]) == 4 and len(kinds["battery"]) == 4 and len(kinds["transformer"]) == 2
            tables = [Polygon([(q["x"], q["y"]) for q in _corners(t)]) for b in lay["blocks"] for t in b["tables"]]
            for e in kinds["pcs"] + kinds["battery"] + kinds["transformer"]:
                pt = SPoint(e["at"]["x"], e["at"]["y"])
                assert all(t.distance(pt) > 5 for t in tables), e
            assert edges["e_icog_bus_bess"]["length_m"] != 400.0
            # N7: earthing draws the switchyard grid around the same location
            await c.call("earthing_design", project_id="kutch-hybrid")
            s = (await c.call("earthing_summary", project_id="kutch-hybrid"))["earthing"]
            g = s["switchyard_grid"]["extent_m"]
            assert g["x0"] <= yard["x"] <= g["x1"] and g["y0"] <= yard["y"] <= g["y1"], (g, yard)
            # validate names the fix for the touch/step error
            v = await c.call("validate", project_id="kutch-hybrid")
            assert any(f["rule_id"] == "earthing.touch_step" and f["severity"] == "error" for f in v["findings"])
            assert "surface_resistivity" in v["next"] and "earthing_design" in v["next"], v.get("next")

    run(go(), 600)


def _corners(t):
    from unicad.arrays.geometry import table_corners
    from unicad.domain.layout import Table

    return [{"x": x, "y": y} for x, y in table_corners(Table.model_validate(t))]


def _every_input_has_a_source(ws_dir, project_id):
    from unicad.mcp.config import Config
    from unicad.mcp.workspace import Workspace
    from unicad.rules import explain, run_rules
    from unicad.tenants.store import thresholds_of

    ws = Workspace(Config.from_env({"UNICAD_WORKSPACE": str(ws_dir)}))
    p, _ = ws.load(project_id)
    report = run_rules(p, ws.catalog, None, thresholds_of(p.company_profile, ws.config.workspace))
    missing = sorted({(f.rule_id, q.name) for f in report.findings for q in explain(f, ws.catalog).inputs if not q.source})
    assert not missing, missing
    return report


def test_explain_finding_gives_a_source_for_every_input(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await scenario_a(c)
            await c.call("earthing_design", project_id="sgt-chakan")
            await c.call("lps_design", project_id="sgt-chakan", method="ese", level="II", ese_delta_t_us=60, mast_height_m=5)
            await scenario_c(c)
            await c.call("earthing_design", project_id="kutch-hybrid")
            await c.call("lps_design", project_id="kutch-hybrid", method="ese", level="III", ese_delta_t_us=60, mast_height_m=6)
            x = await c.call("explain_finding", project_id="kutch-hybrid", rule_id="earthing.touch_step", subject="switchyard")
            assert all(q["source"] for e in x["explanations"] for q in e["inputs"]), x

    run(go(), 900)
    for pid in ("sgt-chakan", "kutch-hybrid"):
        _every_input_has_a_source(tmp_path / "ws", pid)


def test_n24_rooftop_default_table_gap_is_a_maintenance_walkway_not_the_ground_gap(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            given, _ = await scenario_a(c)
            default = await c.call("layout_place", **{k: v for k, v in A_PLACE.items() if k != "table_gap_m"})
            assert default["metrics"]["dc_kwp"] == given["metrics"]["dc_kwp"] >= 800, (default["metrics"], given["metrics"])
            assert any("maintenance walkway" in a and "0.6 m" in a for a in default["assumptions"]), default["assumptions"]

    run(go())


B_PLACE = {
    "project_id": "thar-40mw", "module": B_MOD, "mounting": "fixed_tilt", "tilt_deg": 25, "table_rows": 2, "modules_per_string": 27,
    "strings_per_inverter": 24, "n_blocks": 6, "target_dc_kwp": 43584.5, "road_every_rows": 12,
}  # fmt: skip


def _off_roads_and_tables(project, points):
    from shapely.geometry import Point as SPoint
    from shapely.geometry import Polygon

    from unicad.arrays.geometry import table_corners
    from unicad.domain.layout import ArrayLayout
    from unicad.domain.site import FeatureKind, SiteGeometry, shape_of

    site = SiteGeometry.model_validate(project["site"]["geometry"])
    lay = ArrayLayout.model_validate(project["array_layout"])
    tables = [Polygon(table_corners(t)) for t in lay.table_by_id.values()]
    for name, (x, y) in points:
        p = SPoint(x, y)
        for f in site.features:
            g = shape_of(f)
            if f.kind is FeatureKind.ROAD:
                assert g.distance(p) > (f.width_m or 6.0) / 2, (name, f.id, g.distance(p))
            elif f.kind in (FeatureKind.EXCLUSION, FeatureKind.WATER, FeatureKind.BUILDING) and f.polygon:
                assert not g.contains(p), (name, f.id)
        assert all(not t.contains(p) for t in tables), name


def test_n9_stations_and_switchyard_stand_off_roads_water_and_tables(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await c.call("project_create", name="Thar", client="Thar Renewables Ltd", segment="utility", site="Phalodi", grid_voltage_v=33000, project_id="thar-40mw", state="Rajasthan")
            await c.call("site_import", project_id="thar-40mw", file_path=str(RAJ))
            await c.call("site_set_setbacks", project_id="thar-40mw", boundary_m=10, exclusion_m=10)
            await c.call("layout_place", **B_PLACE)
            await c.call("project_create", **C_CREATE)
            await c.call("site_import", project_id="kutch-hybrid", file_path=str(KUTCH), origin_lon=69.8, origin_lat=23.45)
            await c.call("site_set_setbacks", project_id="kutch-hybrid", boundary_m=10, exclusion_m=10)
            placed = await c.call("layout_place", **C_PLACE)
            assert any("have no width" in a and "road-1" in a for a in placed["assumptions"]), placed["assumptions"]
            for pid in ("thar-40mw", "kutch-hybrid"):
                p = (await c.call("project_get", project_id=pid, full=True))["project"]
                pts = [(b["id"], (b["station"]["x"], b["station"]["y"])) for b in p["array_layout"]["blocks"]]
                yard = p["array_layout"]["switchyard"]["at"]
                _off_roads_and_tables(p, [*pts, ("switchyard", (yard["x"], yard["y"]))])

    run(go())


def test_m18_rooftop_station_is_not_over_a_table(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await scenario_a(c)
            p = (await c.call("project_get", project_id="sgt-chakan", full=True))["project"]
            from shapely.geometry import Point as SPoint
            from shapely.geometry import Polygon

            from unicad.arrays.geometry import table_corners
            from unicad.domain.layout import ArrayLayout

            lay = ArrayLayout.model_validate(p["array_layout"])
            tables = [Polygon(table_corners(t)) for t in lay.table_by_id.values()]
            for b in lay.blocks:
                s = SPoint(b.station.x, b.station.y)
                assert min(t.distance(s) for t in tables) >= 0.49, (b.id, min(t.distance(s) for t in tables))

    run(go())


def test_n3_rooftop_string_inverters_stand_on_their_roof_apart_and_off_tables(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await scenario_a(c)
            return (await c.call("project_get", project_id="sgt-chakan", full=True))["project"]

    p = asyncio.run(asyncio.wait_for(go(), 300))
    from shapely.geometry import Point as SPoint
    from shapely.geometry import Polygon

    from unicad.arrays.geometry import table_corners
    from unicad.domain.layout import ArrayLayout
    from unicad.domain.site import FeatureKind, SiteGeometry, shape_of

    site = SiteGeometry.model_validate(p["site"]["geometry"])
    lay = ArrayLayout.model_validate(p["array_layout"])
    roofs = [shape_of(f) for f in site.of_kind(FeatureKind.ROOF)]
    obstacles = [shape_of(f) for f in site.of_kind(FeatureKind.OBSTACLE)]
    tables = [Polygon(table_corners(t)) for t in lay.table_by_id.values()]
    inv = [e for e in lay.equipment if e.kind == "inverter"]
    assert len(inv) == 6
    for e in inv:
        q = SPoint(e.at.x, e.at.y)
        assert any(r.contains(q) for r in roofs) and e.basis.startswith("on the roof"), e
        assert min(t.distance(q) for t in tables) >= 0.49 and all(not o.contains(q) for o in obstacles), e
    assert all(SPoint(a.at.x, a.at.y).distance(SPoint(b.at.x, b.at.y)) >= 0.99 for i, a in enumerate(inv) for b in inv[i + 1 :])

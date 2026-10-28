"""Regressions from the fourth end-to-end verifier run (close-e2e-4), replayed over MCP stdio from its logged tool arguments: string-inverter LT feeders,
breaker terminals and cable bands, gPV string fuses, BESS sizing, the export limit and DC/AC band explanations, and cables by others in the BOM."""

import asyncio
import math
from pathlib import Path

import openpyxl
import pytest
from test_mcp import connect

from unicad.catalog import load_catalog
from unicad.design.strings import DesignError
from unicad.design.templates import plan_bess

ROOT = Path(__file__).resolve().parents[1]
ACME = ROOT / "profiles" / "samples" / "acme"
CATALOG = load_catalog()
A, B, C = "sgt-chakan", "thar-25mw", "kutch-hybrid"

A_CALLS = [
    ("company_onboard", {"dxf_path": str(ACME / "acme-company.dxf"), "answers": str(ACME / "answers.yaml")}),
    ("project_create", {"name": "1 MWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "Shree Ganesh Textiles factory roof, MIDC Chakan",
                        "grid_voltage_v": 415, "state": "Maharashtra", "address": "Plot, MIDC Chakan, Pune, Maharashtra", "utility": "MSEDCL", "client_code": "SGT", "project_id": A}),
    ("company_assign", {"project_id": A, "profile_id": "acme"}),
    ("project_design_basis", {"project_id": A, "ambient_min_c": 6, "ambient_max_c": 44, "altitude_m": 640, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.15,
                              "power_factor": 0.99, "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5}}),
    ("site_import", {"project_id": A, "file_path": str(ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml")}),
    ("site_set_setbacks", {"project_id": A, "roof_edge_m": 1.0, "obstacle_m": 0.6}),
    ("layout_place", {"project_id": A, "module": "vikram-solar-hypersol-m10r-144-600-wp", "mounting": "rooftop_flush", "modules_per_string": 20, "strings_per_inverter": 12,
                      "target_dc_kwp": 1008, "n_blocks": 1}),
    ("network_autobuild", {"project_id": A, "template": "ci_rooftop", "ac_kw": 770, "module": "vikram-solar-hypersol-m10r-144-600-wp", "inverter": "sungrow-sg110cx-p2",
                           "modules_per_string": 20, "strings_per_inverter": 12, "connection": "lt_panel"}),
]  # fmt: skip
B_CALLS = [
    ("project_create", {"name": "25 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Phalodi",
                        "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPN", "client_code": "TRL", "project_id": B, "address": "Phalodi, Jodhpur district, Rajasthan"}),
    ("project_design_basis", {"project_id": B, "ambient_min_c": 3, "ambient_max_c": 49, "altitude_m": 240, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.25,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPN 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": B, "file_path": str(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml")}),
    ("site_set_setbacks", {"project_id": B, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": B, "module": "waaree-bin-08-590", "mounting": "fixed_tilt", "tilt_deg": 25, "table_rows": 2, "table_cols": 26, "road_every_rows": 12,
                      "modules_per_string": 26, "strings_per_inverter": 24, "n_blocks": 7, "target_dc_kwp": 30925.44}),
    ("network_autobuild", {"project_id": B, "template": "utility_string", "module": "waaree-bin-08-590", "inverter": "huawei-sun2000-330ktl-h1", "modules_per_string": 26,
                           "strings_per_inverter": 24}),
]  # fmt: skip
C_BUILD = {
    "project_id": C, "template": "hybrid", "module": "adani-solar-elan-shine-topcon-g2g-gen-ii-580-wp", "inverter": "sungrow-sg350hx", "modules_per_string": 27, "strings_per_inverter": 24,
    "bess_kw": 8000, "bess_kwh": 16000, "pcs": "sungrow-st5015kwh-2500kw-2h", "battery": "sungrow-st5015kwh", "export_limit_kw": 15000,
}  # fmt: skip
C_PLACE = {
    "project_id": C, "module": "adani-solar-elan-shine-topcon-g2g-gen-ii-580-wp", "mounting": "fixed_tilt", "tilt_deg": 23, "table_rows": 2, "table_cols": 27, "road_every_rows": 12,
    "modules_per_string": 27, "strings_per_inverter": 24, "n_blocks": 3, "target_dc_kwp": 16161.12,
}  # fmt: skip
C_CALLS = [
    ("project_create", {"name": "Kutch Hybrid 15 MWac PV + 8 MW 16 MWh BESS", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch hybrid park", "grid_voltage_v": 33000,
                        "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": C, "address": "Kutch district, Gujarat"}),
    ("project_design_basis", {"project_id": C, "ambient_min_c": 5, "ambient_max_c": 47, "altitude_m": 20, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.2,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": C, "file_path": str(ROOT / "fixtures" / "sites" / "site_plan_kutch.dxf"), "origin_lon": 69.8, "origin_lat": 23.45}),
    ("site_set_setbacks", {"project_id": C, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", C_PLACE),
    ("earthing_design", {"project_id": C, "soil_resistivity": 80, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45], "surface_resistivity": 3000, "surface_layer_m": 0.1}),
    ("network_autobuild", C_BUILD),
]  # fmt: skip


def run(coro, timeout=600):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def replay(c, calls):
    out = None
    for tool, args in calls:
        out = await c.call(tool, **args)
    return out


async def project(c, pid):
    return (await c.call("project_get", project_id=pid, full=True))["project"]


def table(path: str, sheet: str) -> list[dict]:
    ws = openpyxl.load_workbook(path, read_only=True)[sheet]
    head, _units, *body = list(ws.iter_rows(values_only=True))
    return [dict(zip(head, r, strict=False)) for r in body if r[0] is not None]


def warnings(v: dict, rule_id: str) -> list[dict]:
    return [f for f in v["findings"] if f["rule_id"] == rule_id and f["severity"] == "warning"]


def cables_of(e: dict) -> list[tuple[str, int]]:
    return [(e["cable"], e.get("runs", 1)), *((b["cable"], b.get("runs", 1)) for b in e.get("bands") or ())]


def test_m2_m4_string_inverter_feeders_take_50ka_mccbs_and_string_fuses_follow_iec_62548_in_both_poles(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            built = await replay(c, B_CALLS)
            v = await c.call("validate", project_id=B)
            bom = await c.call("bom", project_id=B, format="xlsx")
            return built, v, await project(c, B), bom

    built, v, proj, bom = run(go())
    assert v["counts"]["error"] == 0 and not built["blockers"], built["blockers"]
    nodes = proj["network"]["nodes"]
    brk = [n for n in nodes if n["id"].startswith("inv_brk")]
    specs = {n["id"]: CATALOG.get(n["spec"]) for n in brk}
    mccb = [n for n in brk if specs[n["id"]].device == "mccb"]
    # the run-4 build had an 800 A ACB on every 238 A inverter; now the 320 A 50 kA at 800 V MCCB covers 1.25 x 238.2 A and the 42.4 kA bus fault
    assert mccb and all(specs[n["id"]].rated_current == 320 and specs[n["id"]].breaking_capacity_ka >= 42.4 and specs[n["id"]].rated_voltage >= 800 for n in mccb)
    assert all(n["tag"].startswith("MCCB") for n in mccb)
    over = {s for f in warnings(v, "protection.oversize") for s in f["subjects"]}
    for n in brk:
        if specs[n["id"]].rated_current > 320:
            assert n["id"] in over, n["tag"]
    assert all("force" in f["message"] for f in warnings(v, "protection.oversize"))

    fuses = [n for n in nodes if n["kind"] == "fuse"]
    isc, ocpr = CATALOG.get("waaree-bin-08-590").isc, CATALOG.get("waaree-bin-08-590").max_series_fuse
    for n in fuses:
        rated = CATALOG.get(n["spec"]).rated_current
        assert 1.5 * isc < rated < 2.4 * isc and rated <= ocpr, (rated, isc)
        assert n["attributes"]["fused_poles"] == 2 and "+ AND - POLES" in n["title"]
    assert any("not connected to earth" in a for a in built["assumptions"])
    lines = table(bom["files"][0], "BOM")
    boxes = [ln for ln in lines if ln["ITEM"].startswith("STRING FUSE BOX")]
    assert boxes and sum(ln["QUANTITY"] for ln in boxes) == built["design"]["inverters"] == 84
    links = sum(ln["QUANTITY"] for ln in lines if ln["CATALOG ID"] and str(ln["CATALOG ID"]).startswith("mersen-hp15m"))
    assert links == 2 * built["design"]["strings"]


def test_m3_inverter_cables_fit_the_breaker_terminals_in_at_most_three_sizes_and_m4_by_others_cables_are_not_bought(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            built = await replay(c, A_CALLS)
            await c.call("network_update", project_id=A, id="e_ct_poi", fields={"cable": "polycab-lt-xlpe-al-1c-400-arm", "runs": 3, "length_m": 15, "laying": "tray"})
            v = await c.call("validate", project_id=A, families=["cables", "protection"])
            sched = await c.call("cable_schedule", project_id=A, format="xlsx")
            bom = await c.call("bom", project_id=A, format="xlsx")
            return built, v, await project(c, A), sched, bom

    built, v, proj, sched, bom = run(go())
    assert not built["blockers"] and not [f for f in v["findings"] if f["severity"] == "error"], v["findings"]
    net = proj["network"]
    inv_cable = next(e for e in net["edges"] if e["id"].startswith("e_inv_brk"))
    brk_node = next(n for n in net["nodes"] if n["id"].startswith("inv_brk"))
    brk = CATALOG.get(brk_node["spec"])
    assert brk.terminal_max_mm2 is not None
    used = cables_of(inv_cable)
    assert len(used) <= 3, used
    for cable, runs in used:
        assert CATALOG.get(cable).size_mm2 <= brk.terminal_max_mm2 and runs <= brk.terminal_cables_per_pole, (cable, runs, brk.id)
    conductance = [runs / CATALOG.get(cable).r_ohm_per_km_90c for cable, runs in [*used[1:], used[0]]]
    assert conductance == sorted(conductance), "longer bands get the larger cable"
    if brk.rated_current > 200:
        (f,) = [f for f in warnings(v, "protection.oversize") if brk_node["id"] in f["subjects"]]
        assert "terminals force it" in f["message"]

    rows = table(sched["files"][0], "Cable schedule")
    (others,) = [r for r in rows if "by others" in str(r["REMARKS"] or "").lower() and r["CABLE TO SUPPLY"]]
    assert others["NETWORK EDGE"] == "e_ct_poi"
    ref = others["CATALOG ID"]
    mine = sum(r["CABLE TO SUPPLY"] for r in rows if r["CATALOG ID"] == ref and r["NETWORK EDGE"] != "e_ct_poi")
    got = sum(ln["QUANTITY"] for ln in table(bom["files"][0], "BOM") if ln["CATALOG ID"] == ref)
    assert got == (math.ceil(mine * 1.05) if mine else 0), (ref, got, mine)


def test_m6_bess_is_the_closest_whole_unit_size_and_c_export_limit_and_dc_ac_say_why_and_the_fix_works(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            built = await replay(c, C_CALLS)
            v = await c.call("validate", project_id=C, include_info=True)
            proj = await project(c, C)
            note = next(a for a in built["assumptions"] if a.startswith("Export limit not met"))
            target = float(note.split("target_dc_kwp <= ")[1].split(" ")[0])
            await c.call("layout_place", **(C_PLACE | {"target_dc_kwp": target}))
            fixed = await c.call("network_autobuild", **C_BUILD)
            v2 = await c.call("validate", project_id=C)
            return built, v, proj, target, fixed, v2

    built, v, proj, target, fixed, v2 = run(go())
    nodes = proj["network"]["nodes"]
    pcs = sum(1 for n in nodes if n["kind"] == "pcs")
    assert pcs and v["counts"]["error"] == 0
    (intent,) = warnings(v, "ratio.bess_intent")
    assert "7560 kVA against 8000 asked (-5.5%)" in intent["message"] and "15045 kWh against 16000 asked (-6.0%)" in intent["message"]
    assert any("against the 8000 kW / 16000 kWh asked (-5.5%, -6.0%)" in a for a in built["assumptions"])
    dc = [f for f in v["findings"] if f["rule_id"] == "cables.battery_dc"]
    assert dc and all(f["severity"] == "info" and f["measured"] >= f["limit"] for f in dc), dc
    assert all("integrated in the battery container" in f["message"] for f in dc)

    (plant,) = warnings(v, "ratio.dc_ac_plant")
    assert "every one of the 24 string inputs is used" in plant["message"]
    (export,) = warnings(v, "ratio.export_limit")
    assert "at most 42 x 352 kVA inverters fit" in export["message"]
    assert target == pytest.approx(42 * 24 * 27 * 0.58, abs=0.01)
    assert fixed["design"]["ac_kva"] <= 15000 and fixed["design"]["inverters"] == 42
    (left,) = warnings(v2, "ratio.export_limit")
    assert "PV inverters 14784 kVA + BESS PCS 7560 kVA = 22344 kVA" in left["message"] and "export_control='ppc'" in left["message"]


def test_m6_plan_bess_refuses_with_options_when_no_whole_unit_count_is_close():
    pcs, bat = "sungrow-st5015kwh-2500kw-2h", "sungrow-st5015kwh"
    bp = plan_bess(CATALOG, 8000, 16000, pcs, bat)
    assert (bp.blocks * bp.pcs_per_block, bp.batteries_per_pcs, bp.kw, bp.kwh) == (3, 1, 7560, 15045)
    assert plan_bess(CATALOG, 10000, 20000, pcs, bat).kw == 10080
    with pytest.raises(DesignError, match=r"Options: .*1 PCS x 1 battery: 2520 kVA \(-37\.0%\)"):
        plan_bess(CATALOG, 4000, 8000, pcs, bat)

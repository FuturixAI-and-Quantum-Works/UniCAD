"""Regressions from the sixth end-to-end verifier run (close-e2e-6), replayed over MCP stdio from its logged tool arguments: voltage-drop fitting that
upsized 33 kV feeders for a 0.01 % gain, AC SPDs too low for IT buses, the rooftop inverter cable against the inverter's AC terminal, even MPPT
spread, ambient and trench derating, an unformatted tag in an assumption, a WBSEDCL PT on a GETCO project and the BESS built against its brief."""

import asyncio
import math
from pathlib import Path

import openpyxl
import pytest
from test_mcp import connect

from unicad.catalog import load_catalog
from unicad.rules.topology import mppt_of, mppt_split

ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / "fixtures" / "sites"
ACME = ROOT / "profiles" / "samples" / "acme"
CATALOG = load_catalog()
A, B, C = "sgt-chakan", "thar-45mw", "kutch-hybrid"

A_CALLS = [
    ("company_onboard", {"dxf_path": str(ACME / "acme-company.dxf"), "answers": str(ACME / "answers.yaml")}),
    ("project_create", {"name": "700 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "Shree Ganesh Textiles factory roof, MIDC Chakan",
                        "grid_voltage_v": 415, "state": "Maharashtra", "address": "MIDC Chakan, Pune", "utility": "MSEDCL", "client_code": "SGT", "project_id": A}),
    ("company_assign", {"project_id": A, "profile_id": "acme"}),
    ("project_design_basis", {"project_id": A, "ambient_min_c": 7, "ambient_max_c": 43, "altitude_m": 650, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.1,
                              "power_factor": 0.99, "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5}}),
    ("site_import", {"project_id": A, "file_path": str(SITES / "rooftop_midc_pune.kml")}),
    ("site_set_setbacks", {"project_id": A, "roof_edge_m": 1.0, "obstacle_m": 0.6}),
    ("layout_place", {"project_id": A, "module": "adani-solar-elan-shine-topcon-g2g-gen-ii-580-wp", "mounting": "rooftop_tilted", "tilt_deg": 10, "target_dc_kwp": 696,
                      "modules_per_string": 20, "strings_per_inverter": 10}),
    ("network_autobuild", {"project_id": A, "template": "ci_rooftop", "ac_kw": 660, "module": "adani-solar-elan-shine-topcon-g2g-gen-ii-580-wp", "inverter": "sungrow-sg110cx",
                           "modules_per_string": 20, "strings_per_inverter": 10}),
]  # fmt: skip
B_CALLS = [
    ("project_create", {"name": "45 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Rajasthan",
                        "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPNL", "client_code": "TRL", "project_id": B}),
    ("project_design_basis", {"project_id": B, "ambient_min_c": 3, "ambient_max_c": 49, "altitude_m": 230, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.15,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPNL 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": B, "file_path": str(SITES / "ground_mount_rajasthan.kml")}),
    ("site_set_setbacks", {"project_id": B, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": B, "module": "vikram-solar-hypersol-m10r-144-605-wp", "tilt_deg": 25, "road_every_rows": 10, "target_dc_kwp": 50181.12, "n_blocks": 8,
                      "modules_per_string": 27, "strings_per_inverter": 24}),
    ("network_autobuild", {"project_id": B, "template": "utility_string", "ac_kw": 45056, "module": "vikram-solar-hypersol-m10r-144-605-wp", "inverter": "sungrow-sg350hx",
                           "modules_per_string": 27, "strings_per_inverter": 24, "inverters_per_block": 16}),
]  # fmt: skip
C_BUILD = {
    "project_id": C, "template": "hybrid", "ac_kw": 18000, "module": "waaree-bin-21-620", "inverter": "huawei-sun2000-330ktl-h1", "modules_per_string": 28, "strings_per_inverter": 21,
    "inverters_per_block": 12, "pcs": "sungrow-st5015kwh-2500kw-2h", "battery": "sungrow-st5015kwh", "export_limit_kw": 18000, "export_control": "ppc",
}  # fmt: skip
C_CALLS = [
    ("project_create", {"name": "18 MWac Solar PV + 9 MW / 18 MWh BESS Hybrid Plant", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch hybrid site, Gujarat",
                        "grid_voltage_v": 33000, "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": C}),
    ("project_design_basis", {"project_id": C, "ambient_min_c": 5, "ambient_max_c": 46, "altitude_m": 20, "vdrop_dc_max": 0.02, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.2,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": C, "file_path": str(SITES / "site_plan_kutch.dxf"), "origin_lon": 69.8, "origin_lat": 23.45}),
    ("site_set_setbacks", {"project_id": C, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": C, "module": "waaree-bin-21-620", "tilt_deg": 22, "road_every_rows": 10, "target_dc_kwp": 21873.6, "n_blocks": 5, "modules_per_string": 28,
                      "strings_per_inverter": 21}),
    ("earthing_design", {"project_id": C, "soil_resistivity": 80, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45], "surface_resistivity": 3000, "surface_layer_m": 0.1}),
]  # fmt: skip


def run(coro, timeout=900):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def table(path: str, sheet: str) -> list[dict]:
    ws = openpyxl.load_workbook(path, read_only=True)[sheet]
    head, _units, *body = list(ws.iter_rows(values_only=True))
    return [dict(zip(head, r, strict=False)) for r in body if r[0] is not None]


def replayed(ws: Path, calls: list, extra=None) -> dict:
    async def go():
        out = {}
        async with connect(ws) as c:
            for tool, args in calls:
                out[tool] = await c.call(tool, **args)
            pid = calls[1][1]["project_id"] if calls[0][0] == "company_onboard" else calls[0][1]["project_id"]
            if extra is not None:
                out |= await extra(c, pid)
            out["validate"] = await c.call("validate", project_id=pid, include_info=True)
            out["project"] = (await c.call("project_get", project_id=pid, full=True))["project"]
            out["bom"] = await c.call("bom", project_id=pid, format="xlsx")
        return out

    return run(go())


@pytest.fixture(scope="module")
def rooftop(tmp_path_factory):
    return replayed(tmp_path_factory.mktemp("a") / "ws", A_CALLS)


@pytest.fixture(scope="module")
def utility(tmp_path_factory):
    async def explain(c, pid):
        return {"explain_mv": await c.call("explain_finding", project_id=pid, rule_id="cables.ampacity", subject="e_icog_bus_b01"),
                "explain_lv": await c.call("explain_finding", project_id=pid, rule_id="cables.ampacity", subject="e_inv_brk_b01aa")}  # fmt: skip

    return replayed(tmp_path_factory.mktemp("b") / "ws", B_CALLS, explain)


@pytest.fixture(scope="module")
def hybrid(tmp_path_factory):
    async def build(c, pid):
        refused = await c.fail("network_autobuild", **(C_BUILD | {"bess_kw": 9000, "bess_kwh": 18000}))
        built = await c.call("network_autobuild", **(C_BUILD | {"bess_kw": 10080, "bess_kwh": 20060, "bess_brief_kw": 9000, "bess_brief_kwh": 18000}))
        explain = await c.call("explain_finding", project_id=pid, rule_id="cables.ampacity", subject="e_inv_brk_b01a")
        return {"refused": refused, "built": built, "explain_inv": explain}

    return replayed(tmp_path_factory.mktemp("c") / "ws", C_CALLS, build)


def edges(r: dict) -> dict[str, dict]:
    return {e["id"]: e for e in r["project"]["network"]["edges"]}


def nodes(r: dict) -> dict[str, dict]:
    return {n["id"]: n for n in r["project"]["network"]["nodes"]}


def findings(v: dict, rule_id: str, subject: str | None = None) -> list[dict]:
    return [f for f in v["findings"] if f["rule_id"] == rule_id and (subject is None or subject in f["subjects"])]


def test_r6_M1_mv_feeders_are_sized_by_ampacity_and_fault_not_upsized_for_an_lt_path(utility):
    # run 6: blocks 01/05/06/08 got 3 x 1C 630 sq.mm 33 kV feeders for 98.5 A (about 0.01 % less drop) while the others had 3C 300
    mv = [e for e in edges(utility).values() if e["service"] == "mv_ac" and e.get("cable")]
    assert mv and {e["cable"] for e in mv if e["id"].startswith(("e_icog_bus", "e_tr_icog"))} == {"havells-ht-xlpe-al-3c-300-33kv-arm"}
    assert not [e for e in mv if e.get("sized_for")]
    assert not utility["network_autobuild"]["blockers"] and utility["validate"]["counts"]["error"] == 0
    assert any("Cables above 1000 V are sized by ampacity and short-circuit withstand only" in a for a in utility["network_autobuild"]["assumptions"])


def test_r6_M1_every_upsize_beyond_ampacity_is_explained_in_the_assumptions_and_by_explain_finding(utility):
    grown = {eid: e for eid, e in edges(utility).items() if e.get("sized_for")}
    assert grown and all("for voltage drop" in e["sized_for"] and "% limit" in e["sized_for"] for e in grown.values())
    said = utility["network_autobuild"]["assumptions"]
    for eid in grown:
        assert any(a.startswith(f"edge {eid} ") and "instead of" in a for a in said), eid
    assert utility["explain_mv"]["explanations"] and not any("Sized above its current" in a for x in utility["explain_mv"]["explanations"] for a in x["assumptions"])
    if "e_inv_brk_b01aa" in grown:
        assert any("Sized above its current" in a and "for voltage drop" in a for x in utility["explain_lv"]["explanations"] for a in x["assumptions"])


def test_r6_M2_ac_spds_on_delta_fed_it_buses_have_uc_above_the_line_voltage(utility, hybrid):
    # run 6: DEHN DG M WE 600 (Uc 600 V) on the 800 V and 690 V buses fed from delta LV windings
    for r, need in ((utility, {"acdb_spd": 880}), (hybrid, {"acdb_spd": 880, "pcs_acspd": 759})):
        spds = [n for n in nodes(r).values() if n["kind"] == "ac_spd"]
        assert spds
        for n in spds:
            want = next(v for k, v in need.items() if n["id"].startswith(k))
            spec = CATALOG.get(n["spec"])
            assert spec.rated_voltage >= want and spec.poles >= 3 and "DEHN" in spec.source.ref, (n["id"], spec.id)
        assert not findings(r["validate"], "protection.spd_ac_uc")


def test_r6_M2_an_spd_below_the_it_requirement_is_a_validate_error_stating_it(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            for tool, args in C_CALLS:
                await c.call(tool, **args)
            await c.call("network_autobuild", **(C_BUILD | {"bess_kw": 10080, "bess_kwh": 20060}))
            await c.call("network_update", project_id=C, id="acdb_spd_b01", fields={"spec": "dehn-dg-m-we-600"})
            return await c.call("validate", project_id=C, families=["protection"])

    v = run(go())
    (f,) = findings(v, "protection.spd_ac_uc", "acdb_spd_b01")
    assert f["severity"] == "error" and "880 V" in f["message"] and "IT" in f["message"]


def test_r6_M2_the_tn_rooftop_keeps_its_230_400_v_spd(rooftop):
    for n in nodes(rooftop).values():
        if n["kind"] == "ac_spd":
            assert CATALOG.get(n["spec"]).rated_voltage >= 1.1 * 415 / math.sqrt(3)
    assert not findings(rooftop["validate"], "protection.spd_ac_uc")


def test_r6_m6_the_inverter_ac_cable_fits_the_sg110cx_terminal_and_unstated_cables_per_pole_are_reported(rooftop):
    inv = CATALOG.get("sungrow-sg110cx")
    assert inv.ac_terminal_max_mm2 == 240 and inv.ac_terminal_cables_per_pole is None and "Max. 240" in inv.source.ref
    e = edges(rooftop)["e_inv_brk_b01a"]
    for cable in [e["cable"], *(b["cable"] for b in e.get("bands", []))]:
        assert CATALOG.get(cable).size_mm2 <= 240, cable
    warned = findings(rooftop["validate"], "cables.terminals", "e_inv_brk_b01a")
    assert (e["runs"] > 1) == bool(warned) and all(f["severity"] == "warning" and "confirm with the manufacturer" in f["message"] for f in warned)
    # run 6: 2 x 4C 240 with the main feeder at 3 runs; the cheaper of the compliant solutions spends the copper on the 918 A feeder first
    assert CATALOG.get(e["cable"]).size_mm2 < 240 and edges(rooftop)["e_brk_ct"]["runs"] == 4
    brk = CATALOG.get(nodes(rooftop)["inv_brk_b01a"]["spec"])
    assert any(brk.model in a and "voltage drop needs" in a for a in rooftop["network_autobuild"]["assumptions"])


def test_r6_m1_strings_spread_evenly_over_the_mppts():
    assert mppt_split(21, 6) == (4, 4, 4, 3, 3, 3)
    assert [mppt_of(i, mppt_split(21, 6)) for i in (4, 5, 12, 13, 15, 16, 21)] == [1, 2, 3, 4, 4, 5, 6]
    assert mppt_split(24, 12) == (2,) * 12 and mppt_split(3, 6) == (1, 1, 1)


def test_r5_m10_trench_and_ambient_derate_the_cables_from_the_cited_tables(hybrid):
    (f,) = findings(hybrid["validate"], "cables.ampacity", "e_inv_brk_b01a")
    assert "temperature factor 0.89" in f["message"] and "covered-trench factor 0.95" in f["message"]
    said = [a for x in hybrid["explain_inv"]["explanations"] for a in x["assumptions"]]
    assert any("Polycab_LT_Cable_Catalog_V9.pdf p.87" in a and "46 degC (50 degC column" in a and "KEI Technical Handbook" in a for a in said), said
    assert f["limit"] < CATALOG.get(edges(hybrid)["e_inv_brk_b01a"]["cable"]).ampacity_air


def test_r5_m2_assumptions_name_breakers_by_their_tags_not_a_format_pattern(rooftop):
    assert not [a for a in rooftop["network_autobuild"]["assumptions"] if "{0" in a or "{1" in a]


def test_r6_m4_a_utility_specification_item_is_billed_to_the_projects_utility(hybrid):
    rows = table(hybrid["bom"]["files"][0], "BOM")
    (pt,) = [r for r in rows if "PT" in (r["ITEM"] or "") and "33 kV" in r["ITEM"]]
    assert "WBSEDCL" not in pt["ITEM"] and "GETCO specification" in pt["ITEM"] and "WBSEDCL" in (pt["REMARKS"] or "")


def test_bess_built_size_and_brief_are_on_the_model_with_the_difference(hybrid):
    assert "bess_brief_kw=9000" in hybrid["refused"]
    bess = hybrid["project"]["design_basis"]["bess"]
    assert bess == {"built_kva": 10080.0, "built_kwh": 20060.0, "brief_kw": 9000.0, "brief_kwh": 18000.0}
    (f,) = findings(hybrid["validate"], "ratio.bess_intent")
    assert f["severity"] == "warning" and "+12.0%" in f["message"] and "+11.4%" in f["message"]

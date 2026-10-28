"""Regressions from the fifth end-to-end verifier run (close-e2e-5), replayed over MCP stdio from its logged tool arguments: voltage-drop fitting, the solar
incomer at the LT panel, grouping of parallel LT circuits, SCB string grouping and MC4-sized string cables, SCB fusing, the export limit with a BESS, DC
isolation per string circuit, the LT clearing time behind an IDT, existing equipment in the BOM and one schedule row per fuse-box link."""

import asyncio
import json
import re
from pathlib import Path

import openpyxl
import pytest
from test_mcp import connect

from unicad.catalog import load_catalog
from unicad.domain.network import Laying
from unicad.rules.grouping import grouping

ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / "fixtures" / "sites"
CATALOG = load_catalog()
A, B, C = "sgt-chakan", "thar-35mw", "kutch-hybrid"

A_CALLS = [
    ("project_create", {"name": "450 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "Shree Ganesh Textiles factory roof, MIDC Chakan",
                        "grid_voltage_v": 415, "state": "Maharashtra", "address": "MIDC Chakan, Pune", "utility": "MSEDCL", "client_code": "SGT", "project_id": A}),
    ("project_design_basis", {"project_id": A, "ambient_min_c": 6, "ambient_max_c": 44, "altitude_m": 640, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.12,
                              "power_factor": 0.99, "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5}}),
    ("site_import", {"project_id": A, "file_path": str(SITES / "rooftop_midc_pune.kml")}),
    ("site_set_setbacks", {"project_id": A, "roof_edge_m": 1.0, "obstacle_m": 0.6}),
    ("layout_place", {"project_id": A, "module": "tata-power-solar-tp600lg10tb-series-590-wp", "mounting": "rooftop_flush", "target_dc_kwp": 448.4, "modules_per_string": 19, "strings_per_inverter": 10}),
    ("network_autobuild", {"project_id": A, "template": "ci_rooftop", "ac_kw": 400, "module": "tata-power-solar-tp600lg10tb-series-590-wp", "inverter": "solis-s5-gc100k",
                           "modules_per_string": 19, "strings_per_inverter": 10}),
]  # fmt: skip
B_CALLS = [
    ("project_create", {"name": "35 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Rajasthan",
                        "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPNL", "client_code": "TRL", "project_id": B}),
    ("project_design_basis", {"project_id": B, "ambient_min_c": 2, "ambient_max_c": 48, "altitude_m": 250, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.25,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPNL 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": B, "file_path": str(SITES / "ground_mount_rajasthan.kml")}),
    ("site_set_setbacks", {"project_id": B, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": B, "module": "waaree-bin-21-630", "tilt_deg": 25, "road_every_rows": 10, "target_dc_kwp": 45500, "n_blocks": 4, "modules_per_string": 28, "strings_per_inverter": 312}),
    ("network_autobuild", {"project_id": B, "template": "utility_central", "ac_kw": 35200, "module": "waaree-bin-21-630", "inverter": "sma-sc-4400-up", "modules_per_string": 28,
                           "strings_per_inverter": 312, "inverters_per_block": 2}),
]  # fmt: skip
C_BUILD = {
    "project_id": C, "template": "hybrid", "ac_kw": 12000, "module": "waaree-bi-55-545", "inverter": "huawei-sun2000-330ktl-h1", "modules_per_string": 28, "strings_per_inverter": 24,
    "inverters_per_block": 10, "bess_kw": 7560, "bess_kwh": 15045, "pcs": "sungrow-st5015kwh-2500kw-2h", "battery": "sungrow-st5015kwh", "export_limit_kw": 12000,
}  # fmt: skip
C_CALLS = [
    ("project_create", {"name": "12 MWac Solar PV + 6 MW / 12 MWh BESS Hybrid Plant", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch hybrid site, Gujarat",
                        "grid_voltage_v": 33000, "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": C}),
    ("project_design_basis", {"project_id": C, "ambient_min_c": 5, "ambient_max_c": 46, "altitude_m": 20, "vdrop_dc_max": 0.02, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.2,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": C, "file_path": str(SITES / "site_plan_kutch.dxf"), "origin_lon": 69.8, "origin_lat": 23.45}),
    ("site_set_setbacks", {"project_id": C, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": C, "module": "waaree-bi-55-545", "tilt_deg": 22, "road_every_rows": 10, "target_dc_kwp": 14649.6, "n_blocks": 4, "modules_per_string": 28, "strings_per_inverter": 24}),
    ("earthing_design", {"project_id": C, "soil_resistivity": 80, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45], "surface_resistivity": 3000, "surface_layer_m": 0.1}),
    ("network_autobuild", C_BUILD),
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
            pid = calls[0][1]["project_id"]
            if extra is not None:
                out |= await extra(c, pid)
            out["validate"] = await c.call("validate", project_id=pid, include_info=True)
            out["project"] = (await c.call("project_get", project_id=pid, full=True))["project"]
            out["bom"] = await c.call("bom", project_id=pid, format="xlsx")
            out["cables"] = await c.call("cable_schedule", project_id=pid, format="xlsx")
        return out

    return run(go())


@pytest.fixture(scope="module")
def rooftop(tmp_path_factory):
    return replayed(tmp_path_factory.mktemp("a") / "ws", A_CALLS)


@pytest.fixture(scope="module")
def central(tmp_path_factory):
    return replayed(tmp_path_factory.mktemp("b") / "ws", B_CALLS)


@pytest.fixture(scope="module")
def hybrid(tmp_path_factory):
    async def ppc(c, pid):
        return {"ppc": await c.call("network_autobuild", **(C_BUILD | {"export_control": "ppc"})), "ppc_validate": await c.call("validate", project_id=pid, include_info=True)}

    async def before(c, pid):
        return {"no_ppc": await c.call("validate", project_id=pid, include_info=True)} | await ppc(c, pid)

    return replayed(tmp_path_factory.mktemp("c") / "ws", C_CALLS, before)


def derating(f: dict) -> tuple[float, int]:
    m = re.search(r"Iz = [\d.]+ A \(\w+\) x ([\d.]+) x (\d+) =", f["message"])
    assert m and "group rating factor" in f["message"], f["message"]
    return float(m[1]), int(m[2])


def edges(r: dict) -> dict[str, dict]:
    return {e["id"]: e for e in r["project"]["network"]["edges"]}


def nodes(r: dict) -> dict[str, dict]:
    return {n["id"]: n for n in r["project"]["network"]["nodes"]}


def findings(v: dict, rule_id: str, subject: str | None = None) -> list[dict]:
    return [f for f in v["findings"] if f["rule_id"] == rule_id and (subject is None or subject in f["subjects"])]


def test_r5_m1_voltage_drop_grows_the_inverter_feeder_not_twelve_runs_downstream(rooftop):
    # run 5 at vdrop_ac 1.5 %: ACB-01 -> CT-01 and AC-ISO -> LTP-01 got 12 runs of 3 x 1C 400 for 612 A
    es = edges(rooftop)
    assert es["e_brk_ct"]["runs"] <= 4 and es["e_iso_ltp"]["runs"] <= 4, (es["e_brk_ct"], es["e_iso_ltp"])
    inv = es["e_inv_brk_b01a"]
    assert inv["runs"] >= 2 or CATALOG.get(inv["cable"]).size_mm2 >= 300, inv
    assert not rooftop["network_autobuild"]["blockers"] and rooftop["validate"]["counts"]["error"] == 0
    assert any("only then parallel runs" in a for a in rooftop["network_autobuild"]["assumptions"])


def test_r5_m1_an_unreachable_drop_limit_is_reported_not_piled_with_runs(tmp_path):
    calls = [(t, a | {"vdrop_ac_max": 0.004}) if t == "project_design_basis" else (t, a) for t, a in A_CALLS]
    r = replayed(tmp_path / "ws", calls)
    blockers = r["network_autobuild"]["blockers"]
    assert any(b.startswith("AC ") and "not a sensible design" in b for b in blockers), blockers
    assert max(e.get("runs", 1) for e in edges(r).values() if e["service"] == "lv_ac") <= 4
    assert findings(r["validate"], "vdrop.ac") and any(f["severity"] == "error" for f in findings(r["validate"], "vdrop.ac"))


def test_r5_m2_the_solar_feeder_ends_in_an_incomer_breaker_at_the_lt_panel(rooftop):
    ns, es = nodes(rooftop), edges(rooftop)
    inc = ns["inc_brk"]
    assert inc["kind"] == "breaker" and CATALOG.get(inc["spec"]).rated_current >= CATALOG.get(ns["acdb_brk"]["spec"]).rated_current
    assert es["e_iso_ltp"]["dst"] == "inc_brk:in" and es["e_inc_ltp"]["dst"] == "ltp:in"
    assert not findings(rooftop["validate"], "protection.lv_both_ends")
    from unicad.rules import run_rules
    from unicad.store.files import parse_project

    hand = parse_project((ROOT / "fixtures" / "projects" / "rooftop_500kwp.json").read_text(), CATALOG)
    assert not run_rules(hand, CATALOG, ["protection"]).by_rule("protection.lv_both_ends")
    data = hand.model_dump(mode="json")
    data["network"]["nodes"] = [n for n in data["network"]["nodes"] if n["id"] != "solar_inc"]
    data["network"]["edges"] = [e | ({"dst": "ltp:in"} if e["id"] == "e_iso_ltp" else {}) for e in data["network"]["edges"] if e["id"] != "e_inc_ltp"]
    bare = parse_project(json.dumps(data), CATALOG)
    flagged = {(f.subjects, f.severity.value) for f in run_rules(bare, CATALOG, ["protection"]).by_rule("protection.lv_both_ends")}
    assert flagged == {(("e_acb_ct", "ltp"), "error"), (("e_iso_ltp", "ltp"), "error")}


def test_r5_m3_group_factors_cover_more_than_three_circuits_from_the_cited_tables():
    sc, mc = CATALOG.get("polycab-lt-xlpe-al-1c-400-arm"), CATALOG.get("polycab-lt-xlpe-al-4c-300-arm")
    assert grouping(sc, Laying.TRENCH, 3).factor == 0.88
    assert grouping(sc, Laying.TRENCH, 7).factor == 0.81 and "3 racks" in grouping(sc, Laying.TRENCH, 7).source
    assert grouping(sc, Laying.TRENCH, 12).factor == 0.81 and "6 racks of up to 2" in grouping(sc, Laying.TRENCH, 12).source
    assert grouping(sc, Laying.TRAY, 6).factor == 0.93
    assert grouping(mc, Laying.BURIED_DIRECT, 8).factor == 0.50 and "Havells" in grouping(mc, Laying.BURIED_DIRECT, 8).source
    assert grouping(sc, Laying.TRENCH, 19).factor is None


def test_r5_m3_b_central_inverter_feeders_are_derated_for_their_parallel_trefoils(central):
    # run 5: 7R x 3 x 1C 400 Al, Iz 7 x 589 = 4123 A for Ib 3849 A with factor 1.0
    feeders = [f for f in findings(central["validate"], "cables.ampacity") if f["subjects"][0].startswith("e_inv_idt")]
    assert feeders
    for f in feeders:
        k, runs = derating(f)
        assert runs > 3 and k < 1 and f["severity"] == "info" and f["measured"] <= f["limit"], f


def test_r5_m3_c_parallel_lt_circuits_under_3200_a_acbs_carry_the_breaker_rating(hybrid):
    v = hybrid["no_ppc"]
    assert not findings(v, "cables.protection") and not [f for f in findings(v, "cables.ampacity") if f["severity"] == "error"]
    for eid in ("e_brk_tr", "e_pcs_brk", "e_accb_idt_b01"):
        (f,) = findings(v, "cables.ampacity", eid)
        k, runs = derating(f)
        assert runs > 3 and k < 1 and f["limit"] >= 3200, f


def test_r5_m4_central_strings_go_to_nearby_scbs_on_mc4_sized_cables(central):
    rows = [r for r in table(central["cables"]["files"][0], "Cable schedule") if r["SERVICE"] == "dc" and r["FROM"].startswith("STR-")]
    assert rows
    runs = sorted(max(r["LENGTH POSITIVE POLARITY"], r["LENGTH NEGATIVE POLARITY"]) for r in rows)
    # run 5: STR-01-01-01-01 ran 474 / 461 m on 50 sq.mm to an SCB whose other strings were on another row; median string 134 m
    assert runs[-1] < 250 and runs[len(runs) // 2] < 80, (runs[-1], runs[len(runs) // 2])
    assert {CATALOG.get(r["CATALOG ID"]).size_mm2 for r in rows} <= {4.0, 6.0}
    assert any("MC4" in a for a in central["network_autobuild"]["assumptions"])


def test_r5_m5_scb_inputs_are_fused_in_both_poles_and_the_links_are_billed(central):
    ns = nodes(central)
    fuses = [n for n in ns.values() if n["id"].startswith("scb_fuse")]
    assert fuses
    module = CATALOG.get("waaree-bin-21-630")
    for n in fuses:
        rated = CATALOG.get(n["spec"]).rated_current
        assert 1.5 * module.isc < rated < 2.4 * module.isc and rated <= module.max_series_fuse and n["attributes"]["fused_poles"] == 2
    strings = central["network_autobuild"]["design"]["strings"]
    lines = table(central["bom"]["files"][0], "BOM")
    links = sum(ln["QUANTITY"] for ln in lines if (ln["CATALOG ID"] or "").startswith("mersen") or "FUSE gPV" in ln["ITEM"])
    assert links == 2 * strings, (links, strings)
    assert not any(ln["ITEM"].startswith("STRING FUSE BOX") for ln in lines)


def test_r5_m7_the_export_limit_counts_the_bess_and_a_declared_ppc_clears_it(hybrid):
    (f,) = [f for f in findings(hybrid["no_ppc"], "ratio.export_limit") if f["severity"] == "warning"]
    assert "BESS PCS 7560 kVA" in f["message"] and f["measured"] == 19560 and f["limit"] == 12000
    ppc = findings(hybrid["ppc_validate"], "ratio.export_limit")
    assert ppc and all(x["severity"] == "info" and "declared power plant controller limits the export" in x["message"] for x in ppc)
    assert any("power plant controller measures the export" in a for a in hybrid["ppc"]["assumptions"])
    poi = nodes(hybrid)["poi"]
    assert poi["attributes"]["export_control"] == "ppc" and "EXPORT LIMIT 12000 kW" in poi["title"]


def test_r5_m10_each_string_circuit_has_its_own_pole_pair(rooftop):
    iso = nodes(rooftop)["dciso_b01a"]
    spec = CATALOG.get(iso["spec"])
    assert iso["attributes"]["isolator_ways"] == 10 and spec.poles == 4
    lines = table(rooftop["bom"]["files"][0], "BOM")
    (ln,) = [ln for ln in lines if ln["CATALOG ID"] == spec.id]
    assert ln["QUANTITY"] == 4 * 5 and "10 string circuits" in ln["REMARKS"]


def test_r5_n11_central_inverter_lt_cables_clear_in_the_idt_protection_time(central):
    # run 5: the 52.67 kA LT fault was taken for 1 s because no LT breaker guards the cable
    from unicad.domain.project import Project
    from unicad.rules import run_rules

    project = Project.model_validate(central["project"], context={"catalog": CATALOG})
    feeders = [f for f in run_rules(project, CATALOG, ["cables"]).by_rule("cables.short_circuit") if f.subjects and f.subjects[0].startswith("e_inv_idt")]
    assert feeders
    for f in feeders:
        t = next(q for q in f.inputs if q.name == "t")
        assert t.value == pytest.approx(0.103) and "REF615" in t.source and "VD4" in t.source, t
        assert "sqrt(0.103)" in f.message and any("confirm in the relay settings" in a for a in f.assumptions)


def test_r5_m6_the_bom_leaves_out_the_existing_consumer_panel(rooftop):
    lines = table(rooftop["bom"]["files"][0], "BOM")
    assert not any("CONSUMER LT PANEL" in ln["ITEM"] for ln in lines)
    assert nodes(rooftop)["ltp"]["attributes"]["existing"] is True


def test_r5_m9_fuse_box_to_inverter_links_are_one_row_per_inverter(hybrid):
    rows = [r for r in table(hybrid["cables"]["files"][0], "Cable schedule") if (r["NETWORK EDGE"] or "").startswith("e_fuse_inv")]
    inverters = hybrid["ppc"]["design"]["inverters"]
    assert len(rows) == inverters and all(r["NO. OF RUNS"] == 24 for r in rows), (len(rows), inverters)

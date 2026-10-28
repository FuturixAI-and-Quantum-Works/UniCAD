"""RVUNL Heerapura tool defects over MCP stdio (PLAN 11.2 and 11.4, bench run 1 log.jsonl): the project developer (RV-30), symbol keys checked at
update and listed (RV-M4b), control ports named in network_connect, issue amend, re-running network_autobuild over hand edits, the layout shortfall
carried into later results (RV-M6), the unreadable-SLD issue block (R6), typed ratings through network tools (RV-M5) and company thresholds on
design_strings (N12)."""

import asyncio
import json
from pathlib import Path

from test_mcp import connect

ROOT = Path(__file__).resolve().parents[1]
ACME = ROOT / "profiles" / "samples" / "acme"
PID = "rvunl-heerapura"
MODULE = "waaree-bin-21-615"
INVERTER = "huawei-sun2000-100ktl-m2"
CREATE = {"name": "1.4 MWp / 1.2 MWac Heerapura Solar PV Plant", "client": "RVUNL, Rajasthan", "segment": "utility", "site": "Heerapura, Jaipur, Rajasthan",
          "grid_voltage_v": 415, "project_id": PID, "state": "Rajasthan", "ambient_min_c": 2, "ambient_max_c": 47, "dc_ac_ratio_target": 1.17}  # fmt: skip
FAULT = {"project_id": PID, "fault_levels": [{"voltage_v": 415, "fault_ka": 31.5, "clearing_s": 3, "source": "brief busbar rating"}]}
BRIEF = [{"count": 4, "mppts": [[18, 18]] * 7}, {"count": 3, "mppts": [[18, 18]] * 6 + [[18]]}, {"count": 1, "mppts": [[18, 19]] * 6 + [[18]]}]
BUILD = {"project_id": PID, "module": MODULE, "inverter": INVERTER, "inverter_strings": BRIEF, "data_logger": True,
         "relay": {"functions": ["50", "51", "67", "67N", "27", "59", "50N", "51N"], "protocol": "IEC 61850"}}  # fmt: skip
ISSUE = {"project_id": PID, "issued_for": "information", "rev": "A", "drawn": "A. Sharma", "date": "2026-10-29"}
SITE_150X100 = "# boundary heerapura_plot_ASSUMED_150x100\n75.7187891, 26.8866084\n75.7202998, 26.8866084\n75.7202998, 26.8875082\n75.7187891, 26.8875082\n"


def run(tmp_path, fn, timeout=240):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await fn(c, tmp_path / "ws")

    asyncio.run(asyncio.wait_for(go(), timeout))


def test_developer_is_a_project_party(tmp_path):
    async def check(c, ws):
        made = await c.call("project_create", **CREATE, developer="Truere Knight Pvt. Ltd.")
        assert made["changed"]
        got = await c.call("project_get", project_id=PID, full=True)
        assert got["developer"] == "Truere Knight Pvt. Ltd." and got["project"]["developer"] == {"name": "Truere Knight Pvt. Ltd."}
        r = await c.call("project_parties", project_id=PID, developer_code="TKPL", developer_address="Jaipur")
        assert r["parties"]["developer"] == {"name": "Truere Knight Pvt. Ltd.", "code": "TKPL", "address": "Jaipur"}
        cleared = await c.call("project_parties", project_id=PID, clear_developer=True)
        assert cleared["parties"]["developer"] is None
        assert "pass developer" in await c.fail("project_parties", project_id=PID, developer_code="TKPL")

    run(tmp_path, check)


def test_symbols_are_checked_at_update_and_listed(tmp_path):
    """log.jsonl call 54: network_add_node(symbol='datalogger') was accepted and drawings_render failed later."""

    async def check(c, ws):
        await c.call("project_create", **CREATE)
        bad = await c.fail("network_add_node", project_id=PID, id="dlog", kind="meter", title="DATA LOGGER", symbol="datalogger")
        assert "unknown symbol 'datalogger'" in bad and "inst.datalogger" in bad and "network_symbols" in bad
        listed = await c.call("network_symbols", query="logger")
        assert {"key": "inst.datalogger", "title": "Data logger / SCADA box", "category": listed["symbols"][0]["category"]} in listed["symbols"]
        await c.call("network_add_node", project_id=PID, id="dlog", kind="meter", title="DATA LOGGER", symbol="inst.datalogger", ports=["sig"])
        assert "unknown symbol" in await c.fail("network_update", project_id=PID, id="dlog", fields={"symbol": "logger"})
        tools = {t.name: t for t in (await c.session.list_tools()).tools}
        assert "'sig' and 'sec'" in tools["network_connect"].description and "network_symbols" in tools["network_update"].description

    run(tmp_path, check)


def test_typed_ratings_through_network_tools(tmp_path):
    """log.jsonl calls 62-70: busbar, CT/PT cores and relay functions went into free attributes and titles that are never drawn."""

    async def check(c, ws):
        await c.call("project_create", **CREATE)
        await c.call("network_add_node", project_id=PID, id="relay", kind="relay", relay={"functions": ["50", "51", "67N"], "protocol": "IEC 61850"})
        await c.call("network_add_node", project_id=PID, id="ct", kind="ct", voltage_v=415, ct={"primary_a": 2500, "secondary_a": 1, "cores": [{"accuracy_class": "0.2S", "burden_va": 15}]})
        await c.call("network_add_node", project_id=PID, id="acdb", kind="acdb", voltage_v=415)
        await c.call("network_update", project_id=PID, id="acdb", fields={"busbar": {"rating_a": 2500, "material": "al", "phases": "3P+N", "fault_ka": 31.5, "fault_s": 3}})
        nodes = {n["id"]: n for n in (await c.call("project_get", project_id=PID, full=True))["project"]["network"]["nodes"]}
        assert nodes["acdb"]["busbar"] == {"rating_a": 2500.0, "material": "al", "phases": "3P+N", "fault_ka": 31.5, "fault_s": 3.0}
        assert nodes["ct"]["ct"]["cores"] == [{"accuracy_class": "0.2S", "burden_va": 15.0}] and nodes["relay"]["relay"]["functions"] == ["50", "51", "67N"]
        assert "only valid on" in await c.fail("network_update", project_id=PID, id="acdb", fields={"relay": {"functions": ["50"]}})

    run(tmp_path, check)


def test_issue_amend_and_long_remarks(tmp_path):
    """The bench run needed Rev 2 and Rev 3 to fix Rev 1's remark, truncated in the title block."""

    async def check(c, ws):
        await c.call("project_create", **CREATE)
        long = "ISSUED FOR INFORMATION; ACCB MOVED OUT OF ARRAY, PLOT RESIZED, SINGLE-LINE DETAILS"
        first = await c.call("project_issue", **ISSUE, remarks=long)
        assert any("remarks are" in p["message"] for p in first["problems"])
        fixed = await c.call("project_issue", **ISSUE, remarks="ACCB MOVED, PLOT RESIZED", amend=True)
        assert fixed["changed"] and fixed["revisions"] == ["A"] and not fixed["problems"]
        assert "amend=true" in await c.fail("project_issue", **ISSUE, remarks="OTHER")
        await c.call("project_issue", **(ISSUE | {"rev": "B"}))
        assert "only the latest" in await c.fail("project_issue", **ISSUE, remarks="X", amend=True)
        project = (await c.call("project_get", project_id=PID, full=True))["project"]
        assert [r["remarks"] for r in project["revisions"]] == ["ACCB MOVED, PLOT RESIZED", "ISSUED FOR INFORMATION"]

    run(tmp_path, check)


def test_rebuild_reports_and_keeps_hand_edits(tmp_path):
    """build2.py redid ~30 hand edits after every network_autobuild because a re-run discarded them silently."""

    async def check(c, ws):
        await c.call("project_create", **CREATE)
        await c.call("project_design_basis", **FAULT)
        first = await c.call("network_autobuild", **BUILD)
        assert first["template"] == "utility_lt" and "hand_edits" not in first and first["design"]["string_lengths"] == {"18": 102, "19": 6}
        await c.call("network_update", project_id=PID, id="acdb", fields={"title": "ACCB: 8 IN / 1 OUT, IP65 OUTDOOR"})
        await c.call("network_update", project_id=PID, id="acdb_brk", fields={"long_time_setting_a": 1300})
        again = await c.call("network_autobuild", **BUILD)
        assert again["hand_edits"]["since_revision"] == first["revision"] and not again["hand_edits"]["kept"]
        assert {x["id"] for x in again["hand_edits"]["edits"]["nodes"]["changed"]} == {"acdb", "acdb_brk"}
        assert any("keep_hand_edits=true" in p["message"] for p in again["problems"])
        kept = await c.call("network_autobuild", **BUILD, keep_hand_edits=True)
        assert kept["hand_edits"]["since_revision"] == first["revision"] and len(kept["hand_edits"]["reapplied"]) == 2
        nodes = {n["id"]: n for n in (await c.call("project_get", project_id=PID, full=True))["project"]["network"]["nodes"]}
        assert nodes["acdb"]["title"] == "ACCB: 8 IN / 1 OUT, IP65 OUTDOOR" and nodes["acdb_brk"]["long_time_setting_a"] == 1300
        nothing = await c.call("network_autobuild", **BUILD)
        assert "hand_edits" not in nothing

    run(tmp_path, check)


def test_unreadable_sld_render_blocks_issue(tmp_path):
    """Run 6 R6-m7: SLD 001 rendered readable: false at scale 0.84 on A0, and nothing stopped its issue."""

    async def check(c, ws):
        await c.call("project_create", **CREATE)
        await c.call("project_design_basis", **FAULT)
        built = await c.call("network_autobuild", **BUILD)
        assert built["rules"]["error"] == 0, built["findings"]
        clean = await c.call("validate", project_id=PID)
        assert clean["gates"]["approval"]["allowed"]
        out = ws / PID / "drawings" / f"rev-{built['revision']:03d}"
        out.mkdir(parents=True)
        sheet = {"id": "sld", "kind": "sld", "number": "UDEMO-RVUNL-RH-EE-001", "title": "SINGLE LINE DIAGRAM", "paper": "A0", "readable": False, "scale": 0.84}
        (out / "index.json").write_text(json.dumps({"project_id": PID, "sheets": [sheet]}), encoding="utf-8")
        v = await c.call("validate", project_id=PID)
        found = [f for f in v["findings"] if f["rule_id"] == "drawings.readable_sld"]
        assert found and found[0]["severity"] == "error" and "0.84" in found[0]["message"] and not v["gates"]["approval"]["allowed"]
        refused = await c.fail("project_issue", **(ISSUE | {"issued_for": "approval", "rev": "0"}))
        assert "drawings.readable_sld" in refused

    run(tmp_path, check)


def test_layout_shortfall_is_carried_into_autobuild_and_validate(tmp_path):
    """log.jsonl: layout_place on the 150 x 100 m plot returned target_reached false (800 kWp of 1371.6) and every later result had 0 errors."""

    async def check(c, ws):
        await c.call("project_create", **CREATE)
        site = tmp_path / "site_heerapura_150x100.csv"
        site.write_text(SITE_150X100, encoding="utf-8")
        await c.call("site_import", project_id=PID, file_path=str(site), format="coords", coordinate_system="lonlat")
        await c.call("site_set_setbacks", project_id=PID, boundary_m=5)
        placed = await c.call("layout_place", project_id=PID, module="waaree-bin-21-635", mounting="fixed_tilt", tilt_deg=26, table_rows=2, table_cols=18,
                              orientation="portrait", modules_per_string=18, strings_per_inverter=10, target_dc_kwp=1371.6, n_blocks=12, half_tables=False,
                              road_every_rows=6, switchyard_x=12, switchyard_y=50)  # fmt: skip
        assert placed["target_reached"] is False
        v = await c.call("validate", project_id=PID, families=["layout"])
        assert [f["rule_id"] for f in v["findings"]] == ["layout.shortfall"] and v["counts"]["error"] == 1
        await c.call("project_design_basis", **FAULT)
        built = await c.call("network_autobuild", project_id=PID, template="utility_lt", module="waaree-bin-21-635", inverter=INVERTER, modules_per_string=18, strings_per_inverter=10)
        assert "layout.shortfall" in [f["rule_id"] for f in built["findings"]]

    run(tmp_path, check)


def test_design_strings_reads_the_company_thresholds(tmp_path):
    """N12: design_strings sized against the default isc_factor while validate used the company's."""

    async def check(c, ws):
        await c.call("project_create", **CREATE)
        base = await c.call("design_strings", project_id=PID, module=MODULE, inverter=INVERTER)
        assert base["strings_per_mppt_max"] == 2 and "company_thresholds" not in base
        await c.call("company_onboard", dxf_path=str(ACME / "acme-company.dxf"), answers=str(ACME / "answers.yaml"))
        await c.call("company_assign", project_id=PID, profile_id="acme")
        await c.call("company_update", profile_id="acme", fields={"thresholds": {"isc_factor": {"value": 1.3, "source": "Acme DS-04 rev D"}}})
        company = await c.call("design_strings", project_id=PID, module=MODULE, inverter=INVERTER)
        assert company["strings_per_mppt_max"] == 1 and company["company_thresholds"]["values"]["isc_factor"] == 1.3
        given = await c.call("design_strings", project_id=PID, module=MODULE, inverter=INVERTER, inverter_strings=BRIEF)
        assert not given["given_strings"]["ok"] and any("2 strings on one MPPT" in p for p in given["given_strings"]["problems"])

    run(tmp_path, check)

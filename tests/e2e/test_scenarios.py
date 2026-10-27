"""The close-e2e scenarios replayed over MCP stdio, from project_create to the issued set: a C&I rooftop on the Acme profile (Pune),
a 40 MWac utility plant (Rajasthan) and a 20 MWac PV + 10 MW / 20 MWh BESS hybrid (Kutch). Slow (several minutes, LibreCAD and
LibreDWG); run with UNICAD_E2E=1."""

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import ezdxf
import openpyxl
import pymupdf
import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

from unicad.drafting import dxfrules

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
SITES = ROOT / "fixtures" / "sites"
TIMINGS = Path(os.environ.get("UNICAD_E2E_TIMINGS", "/tmp/opencode/e2e-timings.jsonl"))

ROOFTOP = {
    "create": {
        "name": "800 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop",
        "site": "Shree Ganesh Textiles factory roof, MIDC Chakan, Pune", "grid_voltage_v": 415, "state": "Maharashtra",
        "address": "MIDC Chakan, Pune, Maharashtra", "utility": "MSEDCL", "client_code": "SGT", "project_id": "sgt-chakan",
        "ambient_min_c": 5, "ambient_max_c": 45, "altitude_m": 650, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.2, "power_factor": 0.99,
    },
    "profile": "acme",
    "site": SITES / "rooftop_midc_pune.kml",
    "setbacks": {"roof_edge_m": 1.0, "obstacle_m": 0.6},
    "basis": {
        "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
        "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1},
        "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5},
    },
    "place": {"module": "waaree-bin-08-580", "mounting": "rooftop_flush", "table_rows": 1, "table_cols": 19, "half_tables": False, "n_blocks": 1,
              "modules_per_string": 19, "strings_per_inverter": 12, "target_dc_kwp": 793.44},
    "autobuild": {"template": "ci_rooftop", "ac_kw": 660, "module": "waaree-bin-08-580", "inverter": "sungrow-sg110cx", "modules_per_string": 19,
                  "strings_per_inverter": 12, "connection": "lt_panel"},
    "lps": {"method": "ese", "level": "II", "ese_delta_t_us": 60, "mast_height_m": 5},
}  # fmt: skip

UTILITY = {
    "create": {
        "name": "40 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Phalodi, Rajasthan",
        "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPN", "client_code": "TRL", "project_id": "thar-40mw", "ambient_min_c": 2, "ambient_max_c": 48,
        "altitude_m": 230, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.01, "dc_ac_ratio_target": 1.2, "power_factor": 0.95,
    },
    "profile": None,
    "site": SITES / "ground_mount_rajasthan.kml",
    "setbacks": {"boundary_m": 10, "exclusion_m": 10},
    "basis": {
        "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPN 33 kV fault level (brief)"}],
        "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1, "surface_resistivity_ohm_m": 3000, "surface_layer_m": 0.15,
                     "switchyard_m": [120, 100]},
        "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
    },
    "place": {"module": "waaree-bin-21-635", "mounting": "fixed_tilt", "tilt_deg": 25, "table_rows": 2, "table_cols": 28, "road_every_rows": 12,
              "target_dc_kwp": 48646.08, "n_blocks": 6, "modules_per_string": 28, "strings_per_inverter": 24},
    "autobuild": {"template": "utility_string", "module": "waaree-bin-21-635", "inverter": "sungrow-sg350hx", "modules_per_string": 28, "inverters_per_block": 20},
    "lps": {"method": "ese", "level": "III", "ese_delta_t_us": 60, "mast_height_m": 6},
}  # fmt: skip


HYBRID = {
    "create": {
        "name": "Kutch Hybrid 20 MWac PV + 10 MW 20 MWh BESS", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch, Gujarat",
        "grid_voltage_v": 33000, "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": "kutch-hybrid", "ambient_min_c": 5, "ambient_max_c": 46,
        "altitude_m": 20, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.1, "power_factor": 0.95,
    },
    "profile": None,
    "site": SITES / "site_plan_kutch.dxf",
    "site_origin": {"origin_lon": 69.8, "origin_lat": 23.45},
    "setbacks": {"boundary_m": 10, "exclusion_m": 10},
    "basis": {
        "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
        "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1, "surface_resistivity_ohm_m": 3000, "surface_layer_m": 0.1,
                     "switchyard_m": [60, 45]},
        "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
    },
    "place": {"module": "vikram-solar-hypersol-m10r-144-590-wp", "mounting": "fixed_tilt", "tilt_deg": 23, "table_rows": 2, "modules_per_string": 27,
              "strings_per_inverter": 24, "n_blocks": 3, "target_dc_kwp": 21792.24, "road_every_rows": 12},
    "autobuild": {"template": "hybrid", "ac_kw": 20064, "module": "vikram-solar-hypersol-m10r-144-590-wp", "inverter": "sungrow-sg350hx", "modules_per_string": 27,
                  "strings_per_inverter": 24, "inverters_per_block": 19, "bess_kw": 10000, "bess_kwh": 20000, "pcs": "sungrow-st5015kwh-2500kw-2h",
                  "battery": "sungrow-st5015kwh", "export_limit_kw": 30000},
    "lps": {"method": "ese", "level": "III", "ese_delta_t_us": 60, "mast_height_m": 6},
}  # fmt: skip


class Client:
    def __init__(self, session: ClientSession, label: str) -> None:
        self.session, self.label = session, label

    async def call(self, tool: str, /, **args):
        start = time.monotonic()
        res = await self.session.call_tool(tool, args)
        secs = time.monotonic() - start
        TIMINGS.parent.mkdir(parents=True, exist_ok=True)
        with TIMINGS.open("a") as f:
            f.write(json.dumps({"label": self.label, "tool": tool, "secs": round(secs, 2), "error": res.is_error}) + "\n")
        assert not res.is_error, f"{tool}: {res.content[0].text}"
        return res.structured_content


@asynccontextmanager
async def connect(workspace: Path, label: str):
    env = {**os.environ, "UNICAD_WORKSPACE": str(workspace), "DISPLAY": os.environ.get("DISPLAY", ":0")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "unicad"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield Client(session, label)


async def scenario(c: Client, s: dict) -> dict:
    pid = (await c.call("project_create", **s["create"]))["project_id"]
    if s["profile"]:
        await c.call("company_assign", project_id=pid, profile_id=s["profile"])
    await c.call("project_design_basis", project_id=pid, **s["basis"])
    await c.call("site_import", project_id=pid, file_path=str(s["site"]), **s.get("site_origin", {}))
    await c.call("site_set_setbacks", project_id=pid, **s["setbacks"])
    await c.call("layout_place", project_id=pid, **s["place"])
    await c.call("network_autobuild", project_id=pid, **s["autobuild"])
    await c.call("earthing_design", project_id=pid)
    await c.call("lps_design", project_id=pid, **s["lps"])
    run = {"checked": await c.call("validate", project_id=pid)}
    run["drawn"] = await c.call("drawings_render", project_id=pid, kinds=["all"], formats=["dxf", "pdf", "dwg"])
    run["cables"] = await c.call("cable_schedule", project_id=pid, format="xlsx")
    run["bom"] = await c.call("bom", project_id=pid, format="xlsx")
    run["earth"] = await c.call("earthing_summary", project_id=pid)
    run["project"] = (await c.call("project_get", project_id=pid, full=True))["project"]
    return run


def check(run: dict) -> None:
    checked, drawn, cables, bom = run["checked"], run["drawn"], run["cables"], run["bom"]
    errors = [f for f in checked.get("findings", []) if f.get("severity") == "error"]
    assert checked["counts"]["error"] == 0, errors
    sheets = drawn["sheets"]
    kinds = {s["kind"] for s in sheets}
    assert {"register", "sld", "layout", "earthing", "lps", "strings", "cable_schedule", "bom"} <= kinds, kinds
    rows = [r for s in sheets if s["kind"] == "register" for r in s.get("rows", [])]
    assert [r["number"] for r in rows] == [s["number"] for s in sheets]
    assert len({s["number"] for s in sheets}) == len(sheets)
    with pymupdf.open(drawn["set_pdf"]) as pdf:
        assert pdf.page_count == len(sheets) == drawn["sheet_count"]
    for s in sheets:
        assert s["files"]["pdf"] and Path(s["files"]["pdf"]).is_file(), s["number"]
        assert s["files"]["dwg"] and Path(s["files"]["dwg"]).is_file(), s["number"]
        assert dxfrules.violations(ezdxf.readfile(s["files"]["dxf"])) == [], s["number"]
    for out in (cables, bom):
        files = out.get("files") or [out.get("path")]
        assert files and all(str(f).endswith(".xlsx") and Path(f).is_file() for f in files), out
    check_string_cables_are_each_strings_own_run(run)
    check_bom_carries_the_earthing_and_lps_design(run)


def table(path: str, sheet: str) -> list[dict]:
    ws = openpyxl.load_workbook(path, read_only=True)[sheet]
    head, _units, *body = list(ws.iter_rows(values_only=True))
    return [dict(zip(head, r, strict=False)) for r in body if r[0] is not None]


def check_string_cables_are_each_strings_own_run(run: dict) -> None:
    """A string's cable is its own routed run, not the worst case of its template. Hand bound: for strings spread evenly over a block with the
    inverter or combiner station inside it, the mean run is half the farthest run (each axis: mean |x - x0| <= half the span); allow 40 % more
    for roads, blocks that are not rectangles and stations off the middle. The old schedule gave every string the farthest run."""
    sheet = table(run["cables"]["files"][0], "Cable schedule")
    edges = {e["id"]: e for e in run["project"]["network"]["edges"]}
    strings = [r for r in sheet if r["SERVICE"] == "dc" and r["NETWORK EDGE"] in edges and edges[r["NETWORK EDGE"]]["src"].startswith("str")]
    assert strings
    by_edge: dict[str, list[dict]] = {}
    for r in strings:
        by_edge.setdefault(r["NETWORK EDGE"], []).append(r)
    total_dc = 0.0
    for edge, rows in by_edge.items():
        worst = edges[edge]["length_m"]
        runs = [(r["LENGTH POSITIVE POLARITY"] + r["LENGTH NEGATIVE POLARITY"]) / 2 for r in rows]
        total_dc += sum(r["CABLE TO SUPPLY"] for r in rows)
        assert max(runs) <= worst + 0.05, edge
        if len(rows) > 12:
            assert len({round(x, 1) for x in runs}) > 3, f"{edge}: every string has the same length"
            assert sum(runs) / len(runs) <= 0.7 * worst, f"{edge}: mean run {sum(runs) / len(runs):.1f} m against the farthest {worst} m"
        assert {r["LENGTH SOURCE"] for r in rows} == {"estimated"}, edge
    ordered = sum(ln["QUANTITY"] for ln in table(run["bom"]["files"][0], "BOM") if ln["CATEGORY"] == "CABLES")
    worst_total = sum(2 * len(rows) * edges[edge]["length_m"] for edge, rows in by_edge.items())
    assert total_dc <= 0.7 * worst_total
    assert ordered >= total_dc, "the BOM orders at least the string cable of the schedule"


def check_bom_carries_the_earthing_and_lps_design(run: dict) -> None:
    lines = table(run["bom"]["files"][0], "BOM")
    earth, lps = run["earth"]["earthing"], run["earth"]["lps"]
    pits = {q["item"].split(".")[1]: q["quantity"] for q in earth["quantities"] if q["item"].startswith("pit.")}
    flats = {q["description"]: q["quantity"] for q in earth["quantities"] if q["item"].startswith("flat.")}
    assert pits and flats
    got_pits = {ln["ITEM"].rsplit(" - ", 1)[1]: ln["QUANTITY"] for ln in lines if ln["CATEGORY"] == "EARTHING" and ln["ITEM"].startswith("EARTH PIT")}
    got_flats = {ln["ITEM"]: ln["QUANTITY"] for ln in lines if ln["CATEGORY"] == "EARTHING" and "MM GS FLAT" in ln["ITEM"]}
    assert got_pits == pits and got_flats == flats
    assert not any(ln["ITEM"] == "MAIN EARTH GRID" for ln in lines)
    terminals = [ln for ln in lines if ln["CATEGORY"] == "LIGHTNING PROTECTION"]
    assert [ln["QUANTITY"] for ln in terminals] == [lps["terminal_count"]] * 2 and lps["terminal_count"] > 0
    assert terminals[0]["ITEM"].startswith("ESE lightning arrester") and terminals[1]["ITEM"].startswith("Mast")


SCENARIOS = [("rooftop", ROOFTOP), ("utility", UTILITY), ("hybrid", HYBRID)]


@pytest.mark.parametrize("name, s", SCENARIOS)
def test_scenario_from_project_create_to_the_issued_set(tmp_path, name, s):
    async def go():
        async with connect(tmp_path / "ws", name) as c:
            return await scenario(c, s)

    check(asyncio.run(asyncio.wait_for(go(), 1800)))

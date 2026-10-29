"""RVUNL Heerapura bench (bench run 2) as a reproducible lever: the 1.4 MWp / 1.2 MWac LT ground plant built over MCP stdio from the brief's numbers
(brief /home/aqua/.claude/orchestrate/unicad/briefs/rvunl-heerapura.md), asserting the brief items: modules and inverters, modules per inverter, strings
linked to the layout, routed cable lengths, no block station details, no MV switchyard on the LT plant, catalog meter and logger, breaker margins and
0 validate errors (bench run 2 defects N3, N4, N5, M8, N7, N11)."""

import asyncio
import json
import math
from pathlib import Path

import pytest

from test_mcp import connect
from unicad.catalog import load_catalog
from unicad.earthing import design_earthing
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parents[1]
PID = "rvunl-heerapura"
MODULE = "avaada-electro-avn66g12g-720"
INVERTER = "solis-s6-gc3p150k07-nv-nd"
STRINGS = [{"count": 4, "mppts": [[18, 18]] * 7}, {"count": 3, "mppts": [[18, 18]] * 6 + [[18]]}, {"count": 1, "mppts": [[18, 19]] * 6 + [[18]]}]
PER_INVERTER = [252] * 4 + [234] * 3 + [240]
RELAY = {"functions": ["50", "51", "67", "67N", "27", "59", "50N", "51N"], "protocol": "IEC 61850"}
CREATE = {"name": "1.4 MWp / 1.2 MWac Heerapura, Jaipur, Rajasthan", "client": "RVUNL, Rajasthan", "segment": "utility", "site": "Heerapura, Jaipur, Rajasthan",
          "grid_voltage_v": 415, "project_id": PID, "state": "Rajasthan", "developer": "Truere Knight Pvt. Ltd.", "ambient_min_c": 2, "ambient_max_c": 47,
          "dc_ac_ratio_target": 1.17}  # fmt: skip
SITE = "# boundary heerapura_plot_ASSUMED_200x140\n75.718534, 26.886427\n75.720554, 26.886427\n75.720554, 26.887689\n75.718534, 26.887689\n"
PLACE = {"project_id": PID, "module": MODULE, "tilt_deg": 25, "table_rows": 2, "table_cols": 18, "inverter_strings": STRINGS}
BUILD = {"project_id": PID, "template": "utility_lt", "module": MODULE, "inverter": INVERTER, "inverter_strings": STRINGS, "data_logger": True, "relay": RELAY}


def bench(tmp: Path) -> dict:
    out: dict = {}

    async def go():
        async with connect(tmp / "ws") as c:
            await c.call("project_create", **CREATE)
            site = tmp / "site.csv"
            site.write_text(SITE, encoding="utf-8")
            await c.call("site_import", project_id=PID, file_path=str(site), format="coords", coordinate_system="lonlat")
            await c.call("site_set_setbacks", project_id=PID, boundary_m=5)
            await c.call("project_design_basis", project_id=PID, fault_levels=[{"voltage_v": 415, "fault_ka": 31.5, "clearing_s": 3, "source": "brief: ACCB busbar fault level"}])
            out["place"] = await c.call("layout_place", **PLACE)
            out["build"] = await c.call("network_autobuild", **BUILD)
            out["summary"] = await c.call("layout_summary", project_id=PID)
            out["replace"] = await c.call("layout_place", **PLACE)
            out["summary2"] = await c.call("layout_summary", project_id=PID)
            out["rebuild"] = await c.call("network_autobuild", **BUILD)
            out["earthing"] = await c.call("earthing_design", project_id=PID, soil_resistivity=100, fault_ka=31.5, clearing_s=1)
            out["lps"] = await c.call("lps_design", project_id=PID, method="ese", level="III", ese_delta_t_us=60, mast_height_m=6)
            out["validate"] = await c.call("validate", project_id=PID)
            out["project"] = (await c.call("project_get", project_id=PID, full=True))["project"]

    asyncio.run(asyncio.wait_for(go(), 600))
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    return bench(tmp_path_factory.mktemp("rvunl"))


@pytest.fixture(scope="module")
def project(run):
    return parse_project(json.dumps(run["project"]), load_catalog(ROOT / "catalog"))


def test_blocks_hold_each_inverters_modules_and_strings_are_linked(run):
    place = run["place"]
    assert place["metrics"]["modules"] == 1950 and place["metrics"]["dc_kwp"] == 1404.0 and place["metrics"]["unstrung_modules"] == 0
    assert [b["modules"] for b in place["blocks"]] == PER_INVERTER
    design = run["rebuild"]["design"]
    assert design["instances"]["inverter"] == 8 and design["instances"]["pv_string"] == 108 and design["dc_kwp"] == 1404.0
    assert design["strings_linked"] == 108 and run["summary"]["strings_linked"] == 108
    assert not [b for b in run["rebuild"]["blockers"] if "NOT LINKED" in b or "UNLINKED" in b]


def test_19_module_strings_lie_on_19_module_tables(project):
    layout = project.array_layout
    tables = layout.table_by_id
    for a in layout.strings:
        n = project.network.expand().node(a.string).modules_in_series
        assert len(a.slots()) == n
        if n == 19:
            assert tables[a.spans[0].table].cols == 19


def test_cable_lengths_are_routed_on_the_layout(run, project):
    assumptions = " ".join(run["rebuild"]["assumptions"])
    assert run["rebuild"]["design"]["routed_edges"] >= 10
    assert "placeholders" not in assumptions or not any(f"{k} " in assumptions.split("placeholders")[-1][:200] for k in ("inv_accb", "accb_ltp", "string 30"))
    lengths = {e.id: e.length_m for e in project.network.edges if e.id.startswith("e_inv_brk")}
    assert len(lengths) == 5 and len(set(lengths.values())) > 1 and all(v != 60.0 for v in lengths.values())


def test_relayout_keeps_the_lt_panels_and_the_stations(run):
    first, again = run["summary"], run["summary2"]
    assert {p["tag"] for p in again["lt_panels"]} == {"ACCB-01", "POI-01"}
    assert again["field_equipment"]["inverter"] == 8 and again["strings_linked"] == 108
    assert [b["modules"] for b in run["place"]["blocks"]] == [b["modules"] for b in run["replace"]["blocks"]]
    moved = [math.dist((a["station"]["x"], a["station"]["y"]), (b["station"]["x"], b["station"]["y"])) for a, b in zip(run["place"]["blocks"], run["replace"]["blocks"])]
    assert max(moved) < 2.0, moved
    stations = [(b["station"]["x"], b["station"]["y"]) for b in run["replace"]["blocks"]]
    assert len(set(stations)) == len(stations)
    assert [(p["tag"], p["x"], p["y"]) for p in first["lt_panels"]] == [(p["tag"], p["x"], p["y"]) for p in again["lt_panels"]]
    assert "kept from the previous layout" in again["lt_panels"][0]["basis"]


def test_no_block_station_details_with_inverters_in_the_field(run, project):
    assert run["summary"]["block_stations_are_equipment"] is False
    d = design_earthing(project)
    assert [g.name for g in d.layout.stations if g.kind == "station"] == []


def test_lt_plant_has_no_mv_switchyard(run, project):
    texts = " ".join(run["place"]["assumptions"] + [p["message"] for p in run["place"].get("problems", [])])
    assert "60 x 45" not in texts and "MV switchyard at" not in texts and "no MV switchyard" in texts
    e = run["earthing"]["earthing"]
    assert not [w for w in e["warnings"] if "switchyard" in w.lower()] and "switchyard_grid" not in e
    touch = [f for f in run["validate"]["findings"] if f["rule_id"] == "earthing.touch_step"]
    assert touch == []


def test_meter_and_logger_are_catalog_parts(run, project):
    nodes = project.network.node_by_id
    assert nodes["solar_meter"].spec.startswith("secure-meters-elite-440") and nodes["dlog"].spec == "solis-s3-logger"
    assert not [f for f in run["validate"]["findings"] if f["rule_id"] == "structure.missing_spec"]


def test_breakers_break_the_stated_fault_with_the_reason(run, project):
    catalog = load_catalog(ROOT / "catalog")
    for n in project.network.nodes:
        if n.kind.value == "breaker":
            assert catalog.get(n.spec).breaking_capacity_ka >= 31.5
    assert any("31.5 kA" in a and "breaking capacity" in a for a in run["rebuild"]["assumptions"])


def test_validates_without_errors(run):
    assert run["validate"]["counts"]["error"] == 0, run["validate"]["findings"]


def test_earth_conductors_run_along_the_axes_and_the_ring_closes(project):
    d = design_earthing(project)
    for r in d.layout.runs:
        if r.role.value in ("equipment", "main"):
            for a, b in zip(r.points, r.points[1:]):
                assert min(abs(a.x - b.x), abs(a.y - b.y)) < 0.5, f"{r.role.value} conductor {r.links} runs diagonally: {a} -> {b}"
    ring = [r for r in d.layout.runs if r.links == ("array ring",) and len(r.points) == 5]
    assert len(ring) == 1 and (ring[0].points[0].x, ring[0].points[0].y) == (ring[0].points[-1].x, ring[0].points[-1].y)

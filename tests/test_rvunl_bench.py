"""RVUNL Heerapura bench (bench run 2) as a reproducible lever: the 1.4 MWp / 1.2 MWac LT ground plant built over MCP stdio from the brief's numbers
(brief /home/aqua/.claude/orchestrate/unicad/briefs/rvunl-heerapura.md), asserting the brief items: modules and inverters, modules per inverter, strings
linked to the layout, routed cable lengths, no block station details, no MV switchyard on the LT plant, catalog meter and logger, breaker margins and
0 validate errors (bench run 2 defects N3, N4, N5, M8, N7, N11)."""

import asyncio
import json
import math
import re
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
            out["earthing_again"] = await c.call("earthing_design", project_id=PID, surface_resistivity=3000, surface_layer_m=0.1)
            out["earthing_kept"] = await c.call("earthing_design", project_id=PID)
            out["validate"] = await c.call("validate", project_id=PID)
            out["explain_pass"] = await c.call("explain_finding", project_id=PID, rule_id="vdrop.ac", subject="inva")
            out["explain_none"] = await c.call("explain_finding", project_id=PID, rule_id="cables.ampacity", subject="poi")
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


def test_lightning_masts_stand_outside_the_array(run, project):
    from unicad.lps import design_lps
    from unicad.lps.design import array_area

    lps = run["lps"]["lps"]
    assert lps["uncovered_count"] == 0 and lps["terminal_count"] >= 2 and all(t["shade_m2"] == 0 for t in lps["terminals"])
    assert any("terminals on the perimeter" in a for a in lps["assumptions"])
    assert "MV switchyard" not in {c["class"] for c in lps["coverage"]}
    area = array_area(project)
    from shapely.geometry import Point as SPoint

    assert all(not area.contains(SPoint(t["x"], t["y"])) for t in lps["terminals"])
    inside = design_lps(project, project.design_basis.lps.model_copy(update={"placement": "array"}))
    assert len(inside.terminals) == 1 and inside.terminals[0].shade_m2 >= 0


def test_explain_finding_on_a_passed_rule_gives_the_margin(run):
    ok = run["explain_pass"]
    assert ok["passed"] is True and ok["margins"] and ok["margins"][0]["limit"] == 2.0 and ok["margins"][0]["margin"] > 0
    assert run["explain_none"]["passed"] is True and "passed" in run["explain_none"]["message"]


def test_earthing_inputs_say_where_they_come_from(run):
    kept = run["earthing_kept"]
    assert kept["inputs"]["surface_resistivity_ohm_m"] == {"value": 3000.0, "from": "kept from the design basis (an earlier call or project_design_basis)"}
    assert "surface_layer_m" in kept["inputs_kept"] and run["earthing_again"]["inputs"]["surface_layer_m"]["from"] == "this call"


def test_block_boundaries_are_orthogonal(project):
    for b in project.array_layout.blocks:
        pts = list(b.boundary)
        diagonal = [(a, c) for a, c in zip(pts, pts[1:] + pts[:1]) if min(abs(a.x - c.x), abs(a.y - c.y)) > 0.05]
        assert diagonal == [], f"{b.name} boundary has diagonal edges {diagonal}"


CABLES = {"string": {"cable": "polycab-h1z2z2-k-6mm2"}, "inv_accb": {"cable": "polycab-lt-xlpe-al-3.5c-240-arm"},
          "accb_ltp": {"cable": "polycab-lt-xlpe-al-3.5c-300-arm", "runs": 7}}  # fmt: skip


@pytest.fixture(scope="module")
def stated(tmp_path_factory) -> dict:
    """The brief's cables and 50 kA breakers stated to network_autobuild, the ACCB on the west edge as on bench run 2 (switchyard_x/y 10, 60), then at
    the electrical centre."""
    tmp = tmp_path_factory.mktemp("rvunl-stated")
    out: dict = {}

    async def go():
        async with connect(tmp / "ws") as c:
            await c.call("project_create", **CREATE)
            site = tmp / "site.csv"
            site.write_text(SITE, encoding="utf-8")
            await c.call("site_import", project_id=PID, file_path=str(site), format="coords", coordinate_system="lonlat")
            await c.call("site_set_setbacks", project_id=PID, boundary_m=5)
            await c.call("project_design_basis", project_id=PID, fault_levels=[{"voltage_v": 415, "fault_ka": 31.5, "clearing_s": 3, "source": "brief: ACCB busbar fault level"}])
            await c.call("layout_place", **PLACE, switchyard_x=10, switchyard_y=60)
            for at in ("layout", "electrical_centre"):
                out[at] = await c.call("network_autobuild", **BUILD, cables=CABLES, breaking_ka=50, accb_at=at)
                out[at + "_project"] = (await c.call("project_get", project_id=PID, full=True))["project"]
            note = next(a for a in out["electrical_centre"]["assumptions"] if "layout_place switchyard_x=" in a)
            x, y = re.search(r"switchyard_x=(\d+), switchyard_y=(\d+)", note).groups()
            await c.call("layout_place", **PLACE, switchyard_x=float(x), switchyard_y=float(y))
            out["pad"] = await c.call("network_autobuild", **BUILD, cables=CABLES, breaking_ka=50)

    asyncio.run(asyncio.wait_for(go(), 600))
    return out


def test_stated_cables_are_kept_and_the_breakers_break_50_ka(stated):
    catalog = load_catalog(ROOT / "catalog")
    edges = {e["id"]: e for e in stated["layout_project"]["network"]["edges"]}
    assert {edges[i]["cable"] for i in edges if i.startswith("e_str") and edges[i].get("cable")} == {"polycab-h1z2z2-k-6mm2"}
    assert {(edges[i]["cable"], edges[i].get("runs", 1)) for i in edges if i.startswith("e_inv_brk")} == {("polycab-lt-xlpe-al-3.5c-240-arm", 1)}
    assert (edges["e_ct_poi"]["cable"], edges["e_ct_poi"]["runs"]) == ("polycab-lt-xlpe-al-3.5c-300-arm", 7)
    for n in stated["layout_project"]["network"]["nodes"]:
        if n["kind"] == "breaker":
            assert catalog.get(n["spec"]).breaking_capacity_ka >= 50, n
    assert any(a.startswith("Cables as stated (inv_accb)") for a in stated["layout"]["assumptions"])


def test_far_inverters_over_the_drop_limit_are_named_with_the_clients_choices(stated):
    far = next(b for b in stated["layout"]["blockers"] if b.startswith("Stated cables exceed the voltage drop limit"))
    assert "INV-08" in far and "exceeds vdrop_ac_max 2 %" in far
    assert "accb_at='electrical_centre'" in far and "vdrop_ac_max" in far and "group the far inverters" in far
    lengths = {at: sum(e["length_m"] * e.get("count", 1) for e in stated[at + "_project"]["network"]["edges"] if e["id"].startswith("e_inv_brk")) for at in ("layout", "electrical_centre")}
    assert lengths["electrical_centre"] < lengths["layout"]
    failing = {at: [f["subjects"][0] for f in stated[at]["findings"] if f["rule_id"] == "vdrop.ac" and f["severity"] == "error"] for at in ("layout", "electrical_centre")}
    assert len(failing["electrical_centre"]) < len(failing["layout"])
    assert any("electrical centre of the 8 field inverters" in a for a in stated["electrical_centre"]["assumptions"])


def test_an_accb_pad_at_the_electrical_centre_holds_the_brief_cable_within_2_percent(stated):
    assert [f["message"] for f in stated["pad"]["findings"] if f["rule_id"] == "vdrop.ac" and f["severity"] == "error"] == []
    assert not [b for b in stated["pad"]["blockers"] if "voltage drop" in b]


STRINGS3 = [{"count": 4, "mppts": [[18, 18]] * 7}, {"count": 3, "mppts": [[18, 18]] * 6 + [[18]]}, {"count": 1, "mppts": [[19, 19]] * 3 + [[18, 18]] * 3 + [[18]]}]
SMALL3 = "lon,lat\n75.7186380,26.8866068\n75.7204509,26.8866068\n75.7204509,26.8875098\n75.7186380,26.8875098\n"
SITE3 = "lon,lat\n75.7185373,26.8865617\n75.7205516,26.8865617\n75.7205516,26.8875550\n75.7185373,26.8875550\n"
BASIS3 = {"ambient_min_c": 2, "ambient_max_c": 48, "altitude_m": 431, "vdrop_dc_max": 0.02, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.17, "power_factor": 0.99,
          "fault_levels": [{"voltage_v": 415, "fault_ka": 31.5, "clearing_s": 0.1, "source": "client brief: ACCB busbar fault level"}],
          "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 31.5, "fault_clearing_s": 1.0}}  # fmt: skip
PLACE3 = {"project_id": PID, "module": MODULE, "tilt_deg": 25, "table_rows": 2, "orientation": "portrait", "inverter_strings": STRINGS3}
CABLES3 = {"string": {"cable": "polycab-h1z2z2-k-6mm2"}, "inv_accb": {"cable": "kei-lt-xlpe-al-3.5c-240-arm", "runs": 1},
           "accb_ltp": {"cable": "kei-lt-xlpe-al-3.5c-300-arm", "runs": 7}}  # fmt: skip
BUILD3 = {"project_id": PID, "template": "utility_lt", "module": MODULE, "inverter": INVERTER, "inverter_strings": STRINGS3, "relay": RELAY, "data_logger": True,
          "breaking_ka": 50, "cables": CABLES3}  # fmt: skip


@pytest.fixture(scope="module")
def run3(tmp_path_factory) -> dict:
    """Bench run 3: the 200 x 110 m assumed plot, 5 m setback, the brief's XLPE cables and 50 kA breakers, the ACCB pad at local (95, 44)."""
    tmp = tmp_path_factory.mktemp("rvunl-run3")
    out: dict = {}

    async def go():
        async with connect(tmp / "ws") as c:
            await c.call("project_create", **CREATE)
            await c.call("project_design_basis", project_id=PID, **BASIS3)
            small = tmp / "small.csv"
            small.write_text(SMALL3, encoding="utf-8")
            await c.call("site_import", project_id=PID, file_path=str(small), format="coords", coordinate_system="lonlat")
            await c.call("site_set_setbacks", project_id=PID, boundary_m=5)
            out["too_small"] = await c.fail("layout_place", **PLACE3)
            site = tmp / "site.csv"
            site.write_text(SITE3, encoding="utf-8")
            await c.call("site_import", project_id=PID, file_path=str(site), format="coords", coordinate_system="lonlat", replace=True)
            await c.call("site_set_setbacks", project_id=PID, boundary_m=5)
            out["place"] = await c.call("layout_place", **PLACE3, switchyard_x=95.0, switchyard_y=44.0)
            out["build"] = await c.call("network_autobuild", **BUILD3, accb_at="electrical_centre")
            for placement in ("perimeter", "auto"):
                out[f"lps_{placement}"] = await c.call("lps_design", project_id=PID, method="ese", level="III", ese_delta_t_us=60, mast_height_m=6, placement=placement)
            out["project"] = (await c.call("project_get", project_id=PID, full=True))["project"]
            out["acb_window"] = await c.call("validate", project_id=PID)
            await c.call("network_update", project_id=PID, id="acdb_brk", fields={"long_time_setting_a": 2000})
            out["acb_window_low"] = await c.call("validate", project_id=PID)
            await c.call("network_update", project_id=PID, id="acdb_brk", fields={"long_time_setting_a": 2300})
            issue = {"project_id": PID, "issued_for": "approval", "rev": "0", "drawn": "A. Sharma", "checked": "R. Meena", "approved": "V. Singh", "date": "2026-11-02"}
            out["issue"] = await c.call("project_issue", **issue)
            out["issue_again"] = await c.call("project_issue", **issue)
            await c.call("network_update", project_id=PID, id="acdb_brk", fields={"long_time_setting_a": 2310})
            out["issue_changed"] = await c.fail("project_issue", **issue)
            out["stale_validate"] = await c.call("validate", project_id=PID)
            out["stale_get"] = await c.call("project_get", project_id=PID)
            out["stale_render"] = await c.call("drawings_render", project_id=PID, kinds=["register"], formats=["dxf"])
            out["amend"] = await c.call("project_issue", **issue, amend=True)
            out["amended_validate"] = await c.call("validate", project_id=PID)
            out["amended_get"] = await c.call("project_get", project_id=PID)
            pvc = {**CABLES3, "inv_accb": {"cable": "kei-lt-pvc-al-3.5c-240-arm", "runs": 1}, "accb_ltp": {"cable": "kei-lt-pvc-al-3.5c-300-arm", "runs": 7}}
            out["pvc_build"] = await c.call("network_autobuild", **{**BUILD3, "cables": pvc}, accb_at="electrical_centre")
            out["pvc_validate"] = await c.call("validate", project_id=PID, include_info=True)
            out["pvc_explain"] = await c.call("explain_finding", project_id=PID, rule_id="cables.ampacity", subject="e_ct_poi")
            out["pvc_explain_drop"] = await c.call("explain_finding", project_id=PID, rule_id="vdrop.ac", subject="e_ct_poi")

    asyncio.run(asyncio.wait_for(go(), 600))
    return out


@pytest.fixture(scope="module")
def project3(run3):
    return parse_project(json.dumps(run3["project"]), load_catalog(ROOT / "catalog"))


def test_perimeter_masts_stand_outside_the_array_and_the_note_matches_them(run3, project3):
    """NEW-2 / M7: on the 200 x 110 m plot placement='perimeter' stood the one ESE in a hole of the array (90.0, 41.5) shading 4.7 m2 of tables
    while the note said no mast shades them; 'auto' must take the perimeter there too."""
    from shapely.geometry import Point as SPoint
    from shapely.geometry import Polygon

    from unicad.lps.design import array_area

    area = array_area(project3)
    filled = Polygon(area.exterior) if area.geom_type == "Polygon" else area
    for placement in ("perimeter", "auto"):
        lps = run3[f"lps_{placement}"]["lps"]
        assert lps["uncovered_count"] == 0
        assert all(not filled.contains(SPoint(t["x"], t["y"])) for t in lps["terminals"]), (placement, lps["terminals"])
        notes = " ".join(lps["assumptions"])
        assert "terminals on the perimeter" in notes, placement
        shaded = sum(t["shade_m2"] for t in lps["terminals"])
        assert ("no mast shades the tables" in notes) == (shaded == 0), (placement, shaded, notes)


def test_a_pvc_plant_has_its_ac_drop_and_withstand_checked_at_pvc_ratings(run3):
    """NEW-3: with the KEI PVC cables vdrop.ac was 'not evaluated' (info, no resistance), cables.short_circuit had 'no cited k' (info) and the
    ampacity used the XLPE 90 degC temperature column, so validate passed a plant whose AC drop was never checked."""
    findings = run3["pvc_validate"]["findings"]
    paths = [f for f in findings if f["rule_id"] == "vdrop.ac" and f["subjects"][0].startswith("inv")]
    assert paths and all("not evaluated" not in f["message"] and "cannot be checked" not in f["message"] for f in paths)
    assert all(f["measured"] is not None for f in paths)
    assert "Conductor resistance taken at 70 degC (catalog r_ohm_per_km_70c)" in json.dumps(run3["pvc_explain_drop"])
    sc = [f for f in findings if f["rule_id"] == "cables.short_circuit" and f["subjects"] and f["subjects"][0].startswith("e_")]
    assert sc and not [f for f in sc if "cannot be checked" in f["message"] or "no cited" in f["message"]]
    assert any("/ 76 =" in f["message"] for f in sc)
    amp = run3["pvc_explain"]
    text = json.dumps(amp)
    assert "PVC 70 degC" in text and "XLPE 90 degC" not in text
    assert run3["pvc_validate"]["counts"]["error"] > 0


def test_a_cable_without_a_catalogued_resistance_is_a_validate_error(project3):
    from unicad.rules import run_rules

    catalog = load_catalog(ROOT / "catalog")
    bare = catalog.get("kei-lt-pvc-al-3.5c-240-arm").model_copy(update={"id": "bare-pvc-240", "r_ohm_per_km_70c": None})
    net = project3.network
    edges = tuple(e.model_copy(update={"cable": bare}) if e.id.startswith("e_inv_brk") else e for e in net.edges)
    p = project3.model_copy(update={"network": net.model_copy(update={"edges": edges})})
    errors = [f for f in run_rules(p, catalog).findings if f.rule_id == "vdrop.ac" and f.severity.value == "error"]
    assert errors and all("cannot be checked" in f.message and "no r_ohm_per_km_70c in the catalog" in f.message for f in errors)


def test_a_changed_model_is_not_the_issued_revision_until_amend_reissues_it(run3):
    """NEW-4: after the Rev 0 issue the plot, layout and network changed; project_issue(amend=true) with the same values returned changed:false and
    the new content rendered as the same Rev 0 without a word."""
    first = run3["issue"]
    assert first["changed"] and first["issue"]["content_sha"] and first["issue"]["model_revision"] is not None
    assert run3["issue_again"]["changed"] is False
    assert "has changed since" in run3["issue_changed"] and "amend=true" in run3["issue_changed"] and "new revision mark" in run3["issue_changed"]
    stale = [f for f in run3["stale_validate"]["findings"] if f["rule_id"] == "drawings.issued_revision"]
    assert len(stale) == 1 and stale[0]["severity"] == "warning" and "not the issued Rev 0" in stale[0]["message"]
    assert run3["stale_get"]["issue"]["model_is_issued_content"] is False
    assert run3["stale_render"]["warnings"][0].startswith("NOT THE ISSUED REVISION")
    amend = run3["amend"]
    assert amend["changed"] is True and amend["issue"]["content_sha"] != first["issue"]["content_sha"]
    assert amend["issue"]["model_revision"] > first["issue"]["model_revision"] and "re-issued with the current content" in amend["content"]
    assert any("now stands for model revision" in p["message"] for p in amend["problems"])
    assert not [f for f in run3["amended_validate"]["findings"] if f["rule_id"] == "drawings.issued_revision"]
    assert run3["amended_get"]["issue"]["model_is_issued_content"] is True


def test_the_acb_findings_state_the_passing_ir_window(run3):
    """S6: with the brief's 7R x 300 sq.mm Al XLPE the outgoing ACB passes only for 2295.5 A <= Ir <= 2338.8 A; bench run 3 found it by sweeping Ir."""
    window = "2295.5 A <= Ir <= 2338.8 A"
    high = [f for f in run3["acb_window"]["findings"] if f["rule_id"] == "cables.protection" and f["subjects"][0] == "acdb_brk"]
    low = [f for f in run3["acb_window_low"]["findings"] if f["rule_id"] == "protection.rated_current" and f["subjects"][0] == "acdb_brk"]
    assert len(high) == 1 and window in high[0]["message"] and "set long_time_setting_a" in high[0]["message"]
    assert len(low) == 1 and window in low[0]["message"]


def test_tool_messages_count_inverters_and_strings_not_templates(run3):
    """NEW-9 / NEW-10: layout_place said 'fewer than the 112 strings' for the 108 of the brief, and the logger note said '6 devices (the 5 inverters and
    the meter)' for 8 inverters (it counted inverter templates)."""
    small = run3["too_small"]
    assert "the 108 strings of inverter_strings" in small and "112 the 108 strings" in small and "odd string count take a whole table position" in small
    logger = next(a for a in run3["build"]["assumptions"] if a.startswith("Data logger"))
    assert "9 devices of the daisy chain (the 8 inverters and the meter" in logger
    assert any("the 108 strings lie each in the layout block of its inverter's number" in a for a in run3["build"]["assumptions"])

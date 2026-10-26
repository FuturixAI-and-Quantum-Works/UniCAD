import json
import math
import re
from collections import Counter
from pathlib import Path

import openpyxl
import pytest
from test_routing import CATALOG, fixture, ground_project

from unicad.domain.network import NodeKind
from unicad.domain.project import Project
from unicad.routing import RouteAssumptions, estimate_routes
from unicad.rules import run_rules
from unicad.rules.vdrop import segment_drop
from unicad.schedules import LengthMode, build_bom, build_schedule, write_bom_xlsx, write_schedule_xlsx
from unicad.schedules.bom import CABLES

STRING_ID = re.compile(r"^INV-\d\d-DCFB-\d\d-R\d{3}-\d{4}-IP\d\d-4 sqmm$")


def drops(project: Project) -> dict[str, float]:
    report = run_rules(project, CATALOG, ["vdrop"])
    return {f.subjects[0]: f.measured for f in report.findings if f.rule_id in ("vdrop.dc", "vdrop.ac") and len(f.subjects) == 1}


@pytest.mark.parametrize("name", ["rooftop_500kwp", "utility_50mwac"])
def test_percent_drop_per_row_equals_the_rules_engine_per_edge_value(name):
    p = fixture(name)
    expected = drops(p)
    schedule = build_schedule(p, CATALOG)
    checked = set()
    for r in schedule.rows:
        if r.edge in expected:
            assert r.vd_pct == pytest.approx(expected[r.edge], abs=6e-4), r.cable_id
            checked.add(r.edge)
    assert checked == set(expected) and len(checked) >= 5


@pytest.mark.parametrize("name", ["rooftop_500kwp", "utility_50mwac"])
def test_design_current_and_ampacity_equal_the_rules_engine(name):
    p = fixture(name)
    report = run_rules(p, CATALOG, ["cables"])
    findings = {f.subjects[0]: f for f in report.findings if f.rule_id == "cables.ampacity" and f.measured is not None}
    rows = {r.edge: r for r in build_schedule(p, CATALOG).rows}
    assert findings
    for edge, f in findings.items():
        assert rows[edge].ib_a == pytest.approx(f.measured, abs=0.006)
        assert rows[edge].iz_a == pytest.approx(f.limit, abs=0.006)


def test_string_row_matches_the_hand_calculation():
    p = fixture("rooftop_500kwp")
    row = next(r for r in build_schedule(p, CATALOG).rows if r.edge == "e_str")
    mod, cable = CATALOG.get(p.network.node("str").spec), CATALOG.get("polycab-h1z2z2-k-4mm2")
    assert (row.length_pos_m, row.length_neg_m, row.runs) == (25.0, 25.0, 1)
    vd = 100 * 2 * 25 * mod.imp * cable.r_ohm_per_km_90c / 1000 / (18 * mod.vmp)
    loss = 2 * mod.imp**2 * cable.r_ohm_per_km_90c * 25 / 1000
    assert row.vd_pct == pytest.approx(vd) and row.loss_w == pytest.approx(loss)
    assert row.ib_a == pytest.approx(1.25 * mod.isc)
    assert row.callout == "1C x 4 sq.mm Cu XLPO" and row.laying == "tray"


def test_every_cabled_edge_instance_is_scheduled_once_with_a_unique_id():
    for name in ("rooftop_500kwp", "utility_50mwac"):
        p = fixture(name)
        schedule = build_schedule(p, CATALOG)
        cabled = [e for e in p.network.edges if e.is_power and e.cable is not None]
        expected = sum(p.network.instance_count(e.dst_node if p.network.instance_count(e.dst_node) > p.network.instance_count(e.src_node) else e.src_node) for e in cabled)
        assert len(schedule.rows) == expected
        assert len({r.cable_id for r in schedule.rows}) == len(schedule.rows)
        assert [r.sl for r in schedule.rows] == list(range(1, len(schedule.rows) + 1))


def test_cable_ids_follow_the_positional_scheme():
    roof = [r for r in build_schedule(fixture("rooftop_500kwp"), CATALOG).rows if r.is_string]
    assert len(roof) == 50 and all(STRING_ID.match(r.cable_id) for r in roof)
    assert roof[0].cable_id == "INV-01-DCFB-01-R001-0001-IP01-4 sqmm" and roof[0].to_tag == "DCFB-01" and roof[0].from_tag == "STR-01-01"
    assert roof[-1].cable_id.startswith("INV-05-DCFB-05-") and roof[-1].cable_id.endswith("-IP10-4 sqmm")
    utility = {r.instance: r for r in build_schedule(fixture("utility_50mwac"), CATALOG).rows if r.is_string}
    r = utility["e_str@3.2.5.7"]
    assert r.cable_id == "INV-03-2-SCB-03-2-05-IP07-4 sqmm" and r.to_tag == "SCB-03-2-05" and r.from_tag == "STR-03-2-05-07"
    assert len(utility) == 4320


def test_gaps_name_the_cable_less_connections_the_rules_engine_flags():
    p = fixture("rooftop_500kwp")
    flagged = {f.subjects[0] for f in run_rules(p, CATALOG, ["cables"]).by_rule("cables.missing")}
    gaps = build_schedule(p, CATALOG).gaps
    assert flagged == {"e_ct_poi", "e_ltp_load"}
    assert all(any(f"edge {e} " in g for g in gaps) for e in flagged)


def test_estimated_mode_uses_layout_lengths_and_the_rules_engine_for_the_drop():
    p = fixture("rooftop_500kwp")
    schedule = build_schedule(p, CATALOG, LengthMode.ESTIMATED)
    est = estimate_routes(p, CATALOG, RouteAssumptions(), {"e_str"})
    strings = [r for r in schedule.rows if r.is_string]
    assert {r.length_source for r in strings} == {"estimated"}
    edge = p.network.edge_by_id["e_str"]
    for r in strings:
        i = est.instances[r.instance]
        assert (r.length_pos_m, r.length_neg_m) == (i.positive_m, i.negative_m)
        assert r.length_pos_m >= i.straight_pos_m > 0 and r.length_neg_m >= i.straight_neg_m > 0
        assert r.vd_pct == pytest.approx(segment_drop(p, CATALOG, edge.model_copy(update={"length_m": (r.length_pos_m + r.length_neg_m) / 2})).value)
    assert len({round(r.length_pos_m, 3) for r in strings}) > 3
    assert {r.length_source for r in schedule.rows if r.edge == "e_inv_mccb"} == {"network"}
    assert schedule.template_lengths["e_str"].count == 50
    assert any("Manhattan" in a for a in schedule.assumptions)


def test_auto_mode_estimates_only_what_the_network_leaves_out():
    p = ground_project()
    data = p.model_dump(mode="json")
    for e in data["network"]["edges"]:
        if e["id"] in ("e_scb_inv", "e_icog_bus"):
            e.pop("length_m")
    stripped = Project.model_validate(data, context={"catalog": CATALOG})
    auto = build_schedule(stripped, CATALOG)
    sources = {(r.edge, r.length_source) for r in auto.rows}
    assert ("e_str", "network") in sources and ("e_scb_inv", "estimated") in sources and ("e_icog_bus", "estimated") in sources
    network_only = build_schedule(stripped, CATALOG, LengthMode.NETWORK)
    assert {r.length_source for r in network_only.rows if r.edge == "e_scb_inv"} == {"none"}
    assert any("e_scb_inv" in g and "no length" in g for g in network_only.gaps)
    assert all(r.vd_pct is None and r.loss_w is None for r in network_only.rows if r.edge == "e_scb_inv")


def test_cable_metres_count_every_run_and_loaded_conductor():
    s = build_schedule(fixture("rooftop_500kwp"), CATALOG)
    by_edge = {r.edge: r for r in s.rows}
    assert by_edge["e_str"].cable_metres == 2 * 25.0
    assert by_edge["e_dcfb_iso"].cable_metres == 10 * 2 * 5.0
    assert by_edge["e_inv_mccb"].cable_metres == 30.0
    assert by_edge["e_acb_ct"].cable_metres == 3 * 3 * 60.0
    assert s.totals()["cable_m"] == pytest.approx(sum(r.cable_metres for r in s.rows))


def test_bom_modules_equal_the_network_expansion():
    for name in ("rooftop_500kwp", "utility_50mwac"):
        p = fixture(name)
        expanded = p.network.expand()
        modules = sum(n.modules_in_series for n in expanded.by_kind(NodeKind.PV_STRING))
        bom = build_bom(p, CATALOG, build_schedule(p, CATALOG))
        assert bom.qty("PV MODULES") == modules
        assert bom.summary()["modules"] == modules
        assert bom.qty("INVERTERS AND PCS") == len(expanded.by_kind(NodeKind.INVERTER))
        assert bom.qty("COMBINER BOXES") == len(expanded.by_kind(NodeKind.SCB, NodeKind.DC_FUSE_BOX))
    assert modules == 116640


def test_bom_cable_metres_carry_the_wastage_and_terminations_are_counted_per_end():
    p = fixture("rooftop_500kwp")
    s = build_schedule(p, CATALOG)
    bom = build_bom(p, CATALOG, s, wastage_pct=10)
    routed = Counter()
    for r in s.rows:
        routed[r.cable_ref] += r.cable_metres
    for ref, net in routed.items():
        assert bom.qty(CABLES, ref) == math.ceil(net * 1.1)
    assert build_bom(p, CATALOG, s, wastage_pct=0).qty(CABLES, "polycab-h1z2z2-k-4mm2") == 2500
    pairs = next(ln for ln in bom.lines if ln.item.startswith("MC4"))
    assert pairs.qty == 50
    lugs = {ln.item: ln.qty for ln in bom.lines if ln.item.startswith("Cable lug")}
    assert lugs["Cable lug, Cu, 4 sq.mm"] == 100
    assert lugs["Cable lug, Al, 95 sq.mm"] == 5 * 2 * 4
    with pytest.raises(ValueError):
        build_bom(p, CATALOG, s, wastage_pct=-1)


def test_xlsx_has_one_row_per_cable_a_frozen_header_and_a_units_row(tmp_path):
    p = fixture("rooftop_500kwp")
    schedule = build_schedule(p, CATALOG)
    path = write_schedule_xlsx(schedule, tmp_path / "cs.xlsx")
    ws = openpyxl.load_workbook(path)["Cable schedule"]
    assert ws.max_row == 2 + len(schedule.rows) and ws.freeze_panes == "A3"
    header = [c.value for c in ws[1]]
    units = [c.value for c in ws[2]]
    assert header[:2] == ["SL.NO", "CABLE IDENTIFICATION NO."]
    assert units[header.index("LENGTH POSITIVE POLARITY")] == "m" and units[header.index("VOLTAGE DROP")] == "%"
    first = {h: c.value for h, c in zip(header, ws[3], strict=True)}
    assert first["CABLE IDENTIFICATION NO."] == schedule.rows[0].cable_id
    assert first["VOLTAGE DROP"] == schedule.rows[0].vd_pct and first["FROM"] == schedule.rows[0].from_tag
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Cable schedule", "Basis"]


def test_estimated_schedule_workbook_adds_the_template_lengths(tmp_path):
    schedule = build_schedule(fixture("rooftop_500kwp"), CATALOG, LengthMode.ESTIMATED)
    wb = openpyxl.load_workbook(write_schedule_xlsx(schedule, tmp_path / "cs.xlsx"))
    assert "Template lengths" in wb.sheetnames
    row = [c.value for c in wb["Template lengths"][2]]
    assert row[0] == "e_str" and row[1] == 50 and row[2] <= row[3] <= row[4]


def test_bom_workbook_lists_every_line(tmp_path):
    p = fixture("utility_50mwac")
    bom = build_bom(p, CATALOG, build_schedule(p, CATALOG))
    ws = openpyxl.load_workbook(write_bom_xlsx(bom, tmp_path / "bom.xlsx"))["BOM"]
    assert ws.max_row == 2 + len(bom.lines)
    cats = {ws.cell(row=r, column=2).value for r in range(3, ws.max_row + 1)}
    assert {"PV MODULES", "CABLES", "CABLE ACCESSORIES", "TRANSFORMERS"} <= cats


def test_a_project_without_cables_has_an_empty_schedule():
    data = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "rooftop_500kwp.json").read_text(encoding="utf-8"))
    for e in data["network"]["edges"]:
        e.pop("cable", None)
    p = Project.model_validate(data, context={"catalog": CATALOG})
    s = build_schedule(p, CATALOG)
    assert not s.rows and s.totals()["cable_m"] == 0
    assert any("has no cable" in g for g in s.gaps)

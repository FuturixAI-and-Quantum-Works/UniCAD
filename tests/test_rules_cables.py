"""Worked examples for cable sizing, overload coordination, voltage drop and central-inverter totals. Expected values are computed from catalog data with the formula written out."""

import math

import pytest
from test_rules import CATALOG, edge, fixture, node, variant

from unicad.domain.network import Laying
from unicad.domain.project import IssuedFor
from unicad.rules import Severity, explain, issue_gate, run_rules
from unicad.rules.cables import NO_DERATING, PAIR_FALLBACK, table_ampacity

ADANI = CATALOG.get("adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp")
VIKRAM = CATALOG.get("vikram-solar-hypersol-m10r-144-580-wp")
C4_95 = CATALOG.get("polycab-lt-xlpe-al-4c-95-arm")
C1_400 = CATALOG.get("polycab-lt-xlpe-al-1c-400-arm")
DC4 = CATALOG.get("polycab-h1z2z2-k-4mm2")
DC6 = CATALOG.get("polycab-h1z2z2-k-6mm2")
SG3125 = CATALOG.get("sungrow-sg3125hv-30")
I_INV = 110e3 / (math.sqrt(3) * 415)  # SUN2000-100KTL-M2 ac_max_kva at 415 V = 153.03 A
SIN = math.sqrt(1 - 0.99**2)


def findings(project, rule_id):
    return run_rules(project, CATALOG).by_rule(rule_id)


def by_subject(project, rule_id, severity=None):
    return {f.subjects: f for f in findings(project, rule_id) if severity is None or f.severity is severity}


def set_edge(edge_id, **fields):
    def edit(data):
        edge(data, edge_id).update(fields)

    return edit


# ---------------- cables.ampacity ----------------


def test_ampacity_column_follows_laying_and_circuit():
    assert table_ampacity(C4_95, Laying.TRAY, 3)[:2] == (216, "ampacity_air")
    assert table_ampacity(C4_95, Laying.BURIED_DIRECT, 3)[:2] == (197, "ampacity_ground")
    assert table_ampacity(C4_95, Laying.BURIED_DUCT, 3)[:2] == (164, "ampacity_duct")
    assert table_ampacity(C4_95, Laying.CONDUIT, 3)[:2] == (164, "ampacity_duct")
    # single-core: three-cable circuit for 3-phase, two-cable rating for DC / 1-phase
    assert table_ampacity(C1_400, Laying.AIR, 3)[:2] == (589, "ampacity_air")
    assert table_ampacity(C1_400, Laying.AIR, 1)[:2] == (647, "ampacity_2x1c.air")
    value, field, notes = table_ampacity(DC4, Laying.AIR, 1)
    assert (value, field) == (55, "ampacity_air") and PAIR_FALLBACK in notes
    assert table_ampacity(DC4, Laying.BURIED_DIRECT, 1)[0] is None


def test_rooftop_cables_within_ampacity_with_derating_info():
    p = fixture("rooftop_500kwp")
    fs = by_subject(p, "cables.ampacity")
    assert not [f for f in fs.values() if f.severity is not Severity.INFO]
    # inverter cable: Ib = 153.03 A <= Iz = 216 A x 1.0 (no derating given) x 1 run
    inv = fs[("e_inv_mccb",)]
    assert inv.measured == pytest.approx(I_INV, abs=0.01) and inv.limit == 216
    assert "derating not specified" in inv.message and NO_DERATING in inv.assumptions
    # ACDB outgoing: 5 inverters = 765.2 A <= 589 A x 3 runs = 1767 A
    assert fs[("e_acb_ct",)].measured == pytest.approx(5 * I_INV, abs=0.01) and fs[("e_acb_ct",)].limit == 3 * 589
    # DC edges give derating 1.0 explicitly, so they pass silently
    assert ("e_str",) not in fs and ("e_dcfb_iso",) not in fs


def test_undersized_inverter_cable_is_an_error():
    p = variant("rooftop_500kwp", set_edge("e_inv_mccb", cable="polycab-lt-xlpe-al-4c-50-arm"))
    (f,) = [f for f in findings(p, "cables.ampacity") if f.severity is Severity.ERROR]
    # 4C 50 Al XLPE in air: 138 A < Ib 153.03 A
    assert f.subjects == ("e_inv_mccb",)
    assert (f.measured, f.limit, f.unit) == (pytest.approx(I_INV, abs=0.01), 138, "A")
    assert "INV-01..INV-05" in f.message and "4C x 50 sq.mm Al XLPE ARM" in f.message


def test_derating_and_laying_change_the_limit():
    # buried_duct uses the duct column 164 A; derating 0.8 gives 131.2 A < 153.03 A
    p = variant("rooftop_500kwp", set_edge("e_inv_mccb", laying="buried_duct", derating=0.8))
    (f,) = [f for f in findings(p, "cables.ampacity") if f.subjects == ("e_inv_mccb",)]
    assert f.severity is Severity.ERROR and f.limit == pytest.approx(164 * 0.8)
    values = {q.name: q.value for q in f.inputs}
    assert values["I_table"] == 164 and values["k_derating"] == 0.8 and values["runs"] == 1


def test_dc_string_current_uses_1_25_isc():
    def edit(data):
        edge(data, "e_dcfb_iso")["runs"] = 1

    # e_str: Ib = 1.25 x 13.65 = 17.06 A <= 55 A; DCFB -> isolator on one 6 sq.mm run carries 10 strings = 170.6 A > 70 A
    p = variant("rooftop_500kwp", edit)
    (f,) = [f for f in findings(p, "cables.ampacity") if f.severity is Severity.ERROR]
    assert f.subjects == ("e_dcfb_iso",)
    assert f.measured == pytest.approx(10 * 1.25 * ADANI.isc, abs=0.01) == pytest.approx(170.63, abs=0.01)
    assert f.limit == DC6.ampacity_air == 70
    assert any("1.25 x Isc" in a for a in f.assumptions)


def test_cable_without_rating_for_its_laying_warns():
    (f,) = [f for f in findings(fixture("utility_50mwac"), "cables.ampacity") if f.severity is Severity.WARNING]
    assert f.subjects == ("e_scb_inv",) and "no ground ampacity" in f.message


def test_utility_lv_cable_uses_inverter_voltage_when_zone_is_undeclared():
    p = variant("utility_50mwac", lambda data: node(data, "inv_acspd").pop("voltage_v"))
    (f,) = [f for f in findings(p, "cables.ampacity") if f.subjects == ("e_inv_idt",)]
    # SC 4400 UP: 4400 kVA / (sqrt(3) x 660 V) = 3849.0 A <= 8 runs x 589 A
    assert f.measured == pytest.approx(4400e3 / (math.sqrt(3) * 660), abs=0.01) and f.limit == 8 * 589
    assert any("rated AC voltage" in a for a in f.assumptions)


# ---------------- cables.protection ----------------


def test_breaker_rating_above_cable_ampacity_is_an_error():
    # MCCB In 200 A protecting a 4C 70 Al XLPE cable rated 175 A in air: Ib 153 <= In 200 but In > Iz
    p = variant("rooftop_500kwp", set_edge("e_inv_mccb", cable="polycab-lt-xlpe-al-4c-70-arm"))
    (f,) = findings(p, "cables.protection")
    assert f.subjects == ("inv_mccb", "e_inv_mccb") and f.severity is Severity.ERROR
    assert (f.measured, f.limit) == (200, 175)
    assert f.clause == "IS 732 / IEC 60364-4-43"
    assert not findings(p, "protection.rated_current")
    assert not findings(fixture("rooftop_500kwp"), "cables.protection")


# ---------------- cables.missing ----------------


def test_missing_cable_or_length_names_the_edge():
    p = variant("rooftop_500kwp", set_edge("e_inv_mccb", cable=None, length_m=None))
    fs = by_subject(p, "cables.missing")
    assert "e_inv_mccb" in fs[("e_inv_mccb",)].message and "no cable or length_m" in fs[("e_inv_mccb",)].message
    p = variant("rooftop_500kwp", set_edge("e_str", length_m=None))
    assert "no length_m" in by_subject(p, "cables.missing")[("e_str",)].message
    # taps to SPDs and busbar links inside panels are exempt; a link given a laying is a cable run
    fs = by_subject(fixture("rooftop_500kwp"), "cables.missing")
    assert set(fs) == {("e_ct_poi",), ("e_ltp_load",)}
    assert set(by_subject(fixture("utility_50mwac"), "cables.missing")) == {("e_ct_poi",)}
    p = variant("utility_50mwac", set_edge("e_icog_bus", cable=None, length_m=None))
    assert ("e_icog_bus",) in by_subject(p, "cables.missing")


# ---------------- vdrop ----------------


def _dc_string_drop(length):
    return 100 * 2 * length * ADANI.imp * DC4.r_ohm_per_km_90c / 1000 / (18 * ADANI.vmp)


def _ac_drop(length, current, cable, runs=1):
    return 100 * math.sqrt(3) * length * current / runs * (cable.r_ohm_per_km_90c * 0.99 + cable.x_ohm_per_km * SIN) / 1000 / 415


def test_rooftop_dc_voltage_drop_segments_and_total():
    fs = by_subject(fixture("rooftop_500kwp"), "vdrop.dc")
    # string cable: 2 x 25 m x 12.91 A x 6.4903 ohm/km / 1000 / (18 x 43.05 V) = 0.541 %
    assert fs[("e_str",)].measured == pytest.approx(_dc_string_drop(25), abs=1e-3) == pytest.approx(0.541, abs=1e-3)
    # DCFB -> isolator: 10 strings on 10 runs, so per run 12.91 A over 5 m of 6 sq.mm
    per_run = 100 * 2 * 5 * ADANI.imp * DC6.r_ohm_per_km_90c / 1000 / (18 * ADANI.vmp)
    assert fs[("e_dcfb_iso",)].measured == pytest.approx(per_run, abs=1e-3)
    total = fs[("str", "inv")]
    assert total.severity is Severity.INFO and total.limit == 2.0
    expected = _dc_string_drop(25) + per_run + 100 * 2 * 3 * ADANI.imp * DC6.r_ohm_per_km_90c / 1000 / (18 * ADANI.vmp)
    assert total.measured == pytest.approx(expected, abs=1e-3) == pytest.approx(0.656, abs=1e-3)


def test_rooftop_ac_voltage_drop_total():
    fs = by_subject(fixture("rooftop_500kwp"), "vdrop.ac")
    seg = _ac_drop(30, I_INV, C4_95)
    assert fs[("e_inv_mccb",)].measured == pytest.approx(seg, abs=1e-3) == pytest.approx(0.797, abs=1e-3)
    expected = seg + _ac_drop(60, 5 * I_INV, C1_400, runs=3) + _ac_drop(5, 5 * I_INV, C1_400, runs=3)
    total = fs[("inv", "poi")]
    assert total.severity is Severity.INFO
    assert total.measured == pytest.approx(expected, abs=1e-3) == pytest.approx(1.573, abs=1e-3)
    assert any("not counted" in a and "e_mccb_acdb" in a for a in total.assumptions)
    assert any("90 degC" in a for a in total.assumptions)


def test_long_cables_exceed_the_design_basis_limits():
    def edit(data):
        edge(data, "e_str")["length_m"] = 100.0
        edge(data, "e_inv_mccb")["length_m"] = 60.0

    p = variant("rooftop_500kwp", edit)
    report = run_rules(p, CATALOG)
    errors = {f.rule_id: f for f in report.findings if f.severity is Severity.ERROR}
    assert set(errors) == {"vdrop.dc", "vdrop.ac"}
    # DC: 4 x 0.541 % (100 m string cable) + 0.072 % + 0.043 % = 2.28 % > 2 %
    tail = 100 * 2 * 8 * ADANI.imp * DC6.r_ohm_per_km_90c / 1000 / (18 * ADANI.vmp)
    assert errors["vdrop.dc"].measured == pytest.approx(_dc_string_drop(100) + tail, abs=1e-3) == pytest.approx(2.28, abs=0.01)
    assert errors["vdrop.dc"].limit == 2.0 and errors["vdrop.dc"].unit == "%"
    # AC: 2 x 0.797 % (60 m) + 0.716 % + 0.060 % = 2.37 % > 2 %
    assert errors["vdrop.ac"].measured == pytest.approx(2 * _ac_drop(30, I_INV, C4_95) + _ac_drop(60, 5 * I_INV, C1_400, 3) + _ac_drop(5, 5 * I_INV, C1_400, 3), abs=1e-3)
    assert not issue_gate(report, IssuedFor.APPROVAL).allowed
    ex = explain(errors["vdrop.ac"], CATALOG)
    assert ex.sources["design_basis.vdrop_ac_max"] == "design_basis.vdrop_ac_max"


def test_utility_vdrop_skips_mv_segments_without_cables():
    fs = by_subject(variant("utility_50mwac", set_edge("e_idt_vcb", cable=None, length_m=None)), "vdrop.ac")
    total = fs[("inv", "poi")]
    assert total.limit == 1.0 and total.severity is Severity.INFO
    assert any("e_idt_vcb" in a for a in total.assumptions)
    dc = by_subject(fixture("utility_50mwac"), "vdrop.dc")[("str", "inv")]
    # string: 2 x 30 x 13.56 x 6.4903 / 1000 / (27 x 42.8); SCB main: 24 strings on 2 runs over 150 m of 240 sq.mm
    v = 27 * VIKRAM.vmp
    expected = 100 * (2 * 30 * VIKRAM.imp * DC4.r_ohm_per_km_90c + 2 * 150 * 24 * VIKRAM.imp / 2 * 0.1042) / 1000 / v
    assert dc.measured == pytest.approx(expected, abs=1e-3)


# ---------------- central inverters (per-MPPT fields absent) ----------------


def _sg3125(scbs=15):
    def edit(data):
        node(data, "inv")["spec"] = SG3125.id
        data["network"]["groups"] = [g | {"count": scbs} if g["id"] == "scb_sec" else g for g in data["network"]["groups"]]

    return edit


def test_central_inverter_without_per_mppt_fields_uses_totals():
    assert SG3125.inputs_per_mppt is None and SG3125.max_short_circuit_current_per_mppt is None
    report = run_rules(variant("utility_50mwac", _sg3125()), CATALOG)
    print("\n" + report.render())
    assert not report.by_rule("pv_strings.inputs")  # 15 SCB inputs <= 18
    assert not report.by_rule("pv_strings.isc_per_mppt")  # 360 x 1.25 x 14.2 = 6390 A <= 10000 A
    (w,) = report.by_rule("pv_strings.operating_current")
    # 360 strings x Imp 13.56 A = 4881.6 A > max_dc_input_current 3997 A
    assert w.severity is Severity.WARNING and w.measured == pytest.approx(360 * VIKRAM.imp) and w.limit == 3997
    assert "max_dc_input_current" in w.formula


def test_central_inverter_totals_flag_too_many_inputs():
    report = run_rules(variant("utility_50mwac", _sg3125(scbs=20)), CATALOG)
    (f,) = report.by_rule("pv_strings.inputs")
    assert (f.measured, f.limit) == (20, 18) and f.formula == "dc_inputs <= dc_input_count"
    # 480 strings x 1.25 x 14.2 A = 8520 A <= 10000 A
    assert not report.by_rule("pv_strings.isc_per_mppt")
    report = run_rules(variant("utility_50mwac", _sg3125(scbs=24)), CATALOG)
    (isc,) = report.by_rule("pv_strings.isc_per_mppt")
    assert isc.measured == pytest.approx(576 * 1.25 * VIKRAM.isc) and isc.limit == 10000

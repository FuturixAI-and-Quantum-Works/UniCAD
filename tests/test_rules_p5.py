"""Worked examples for cable short-circuit withstand and breaking capacity against a declared fault level. Expected values are computed with the formula written out."""

import math

import pytest
from test_rules import CATALOG, edge, fixture, variant
from test_rules_cables import findings

from unicad.domain.project import FaultLevel
from unicad.rules import Severity, explain, run_rules
from unicad.rules.thresholds import K_XLPE_AL, K_XLPE_CU

MV = 33000.0


def with_levels(*levels: dict):
    def edit(data):
        data["design_basis"]["fault_levels"] = list(levels)

    return edit


def both(*edits):
    def edit(data):
        for e in edits:
            e(data)

    return edit


def level(ka: float, clearing_s: float | None = 1.0, voltage_v: float = MV, source: str = "fault study rev B") -> dict:
    out = {"voltage_v": voltage_v, "fault_ka": ka, "source": source}
    return out | ({"clearing_s": clearing_s} if clearing_s is not None else {})


def set_edge(edge_id, **fields):
    def edit(data):
        edge(data, edge_id).update(fields)

    return edit


def sc(project, edge_id):
    return next(f for f in findings(project, "cables.short_circuit") if f.subjects == (edge_id,))


def test_constants_are_the_cited_xlpe_values():
    assert (K_XLPE_CU.value, K_XLPE_AL.value) == (143.0, 94.0)
    assert "KEI Technical Handbook" in K_XLPE_AL.assumption and "IS 7098" in K_XLPE_CU.assumption


def test_without_fault_levels_one_info_names_the_unchecked_voltages():
    fs = findings(fixture("utility_50mwac"), "cables.short_circuit")
    rest = [f for f in fs if f.subjects == ()]
    assert len(rest) == 1 and rest[0].severity is Severity.INFO
    assert "33000 V" in rest[0].message and "660 V" not in rest[0].message and "design_basis.fault_levels" in rest[0].message
    # the 660 V zone is fed by the IDT, so its cables are checked against the transformer-fed current even without a declared level
    f = sc(fixture("utility_50mwac"), "e_inv_idt")
    assert {q.name: q for q in f.inputs}["I_fault"].source.startswith("transformer-fed")


def test_aluminium_xlpe_3c_120_fails_a_25_ka_one_second_fault_on_the_33kv_bus():
    p = variant("utility_50mwac", with_levels(level(25)))
    f = sc(p, "e_icog_bus")
    need = 25000 * math.sqrt(1.0) / 94
    assert f.severity is Severity.ERROR and (f.measured, f.limit, f.unit) == (120, pytest.approx(need, abs=0.01), "mm2")
    assert "cannot withstand the 25 kA fault at 33000 V" in f.message and "11.3 kA for 1 s" in f.message
    assert f.clause == "IS 732 / IEC 60364-4-43" and f.formula == "A >= I sqrt(t) / k"
    inputs = {q.name: q for q in f.inputs}
    assert inputs["k"].value == 94 and inputs["k"].source == "threshold.k_xlpe_al" and inputs["t"].value == 1.0
    assert inputs["I_fault"].source == "design_basis.fault_levels (fault study rev B)"
    assert any(a.startswith("Adiabatic constant k of an aluminium") for a in f.assumptions)
    assert run_rules(p, CATALOG).blocking


def test_a_lower_fault_passes_with_an_info_that_carries_the_working():
    f = sc(variant("utility_50mwac", with_levels(level(10))), "e_icog_bus")
    assert f.severity is Severity.INFO and "withstands the 10 kA fault" in f.message and (f.measured, f.limit) == (120, pytest.approx(10000 / 94, abs=0.01))


def test_clearing_time_scales_the_required_area_with_its_square_root():
    f = sc(variant("utility_50mwac", with_levels(level(20, clearing_s=0.25))), "e_icog_bus")
    assert f.limit == pytest.approx(20000 * 0.5 / 94, abs=0.01) and f.severity is Severity.INFO


def test_parallel_runs_add_their_areas():
    two = both(with_levels(level(25)), set_edge("e_icog_bus", runs=2))
    three = both(with_levels(level(25)), set_edge("e_icog_bus", runs=3))
    assert sc(variant("utility_50mwac", two), "e_icog_bus").severity is Severity.ERROR
    ok = sc(variant("utility_50mwac", three), "e_icog_bus")
    assert ok.severity is Severity.INFO and ok.measured == 360


def test_copper_uses_k_143():
    cu = both(with_levels(level(25)), set_edge("e_icog_bus", cable="havells-ht-xlpe-cu-3c-120-33kv-arm"))
    f = sc(variant("utility_50mwac", cu), "e_icog_bus")
    assert f.severity is Severity.ERROR and f.limit == pytest.approx(25000 / 143, abs=0.01)
    fine = both(with_levels(level(25)), set_edge("e_icog_bus", cable="havells-ht-xlpe-cu-3c-185-33kv-arm"))
    assert sc(variant("utility_50mwac", fine), "e_icog_bus").severity is Severity.INFO


def test_clearing_time_falls_back_to_the_earthing_basis_then_to_one_second():
    def earthing(data):
        data["design_basis"]["earthing"] = {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 0.5}

    f = sc(variant("utility_50mwac", both(earthing, with_levels(level(25, clearing_s=None)))), "e_icog_bus")
    assert f.limit == pytest.approx(25000 * math.sqrt(0.5) / 94, abs=0.01)
    assert any("design_basis.earthing.fault_clearing_s (0.5 s)" in a for a in f.assumptions)
    g = sc(variant("utility_50mwac", with_levels(level(25, clearing_s=None))), "e_icog_bus")
    assert g.limit == pytest.approx(25000 / 94, abs=0.01) and any("taken as 1 s" in a for a in g.assumptions)


def test_low_voltage_levels_match_by_zone_voltage():
    p = variant("utility_50mwac", with_levels(level(60, voltage_v=660.0, source="IDT impedance")))
    f = sc(p, "e_inv_idt")
    assert f.severity is Severity.INFO and f.measured == 4000 and f.limit == pytest.approx(60000 / 94, abs=0.01)
    assert not [x for x in findings(p, "cables.short_circuit") if x.subjects == ("e_icog_bus",)]
    rest = [x for x in findings(p, "cables.short_circuit") if x.subjects == ()]
    assert len(rest) == 1 and "33000 V" in rest[0].message and "660 V" not in rest[0].message


def test_dc_cables_and_pvc_are_not_checked():
    pvc = next(c for c in CATALOG.filter(kind="cable") if c.insulation == "pvc" and c.service == "lv_ac" and c.cores == 4 and c.size_mm2 == 95 and c.armoured)
    p = variant("rooftop_500kwp", both(with_levels(level(36, voltage_v=415.0)), set_edge("e_inv_mccb", cable=pvc.id)))
    fs = {f.subjects: f for f in findings(p, "cables.short_circuit")}
    assert not any(s and s[0] in ("e_str", "e_dcfb_iso", "e_iso_inv") for s in fs)
    assert fs[("e_inv_mccb",)].severity is Severity.INFO and "no cited short-circuit constant k for AL PVC" in fs[("e_inv_mccb",)].message
    assert fs[("e_acb_ct",)].severity is Severity.INFO and "withstands" in fs[("e_acb_ct",)].message


def test_explain_resolves_the_rule_and_its_k_source():
    p = variant("utility_50mwac", with_levels(level(25)))
    e = explain(sc(p, "e_icog_bus"), CATALOG)
    assert e.rule_id == "cables.short_circuit" and "adiabatic" in e.docs and e.sources["threshold.k_xlpe_al"] == "threshold.k_xlpe_al"


def test_breaking_capacity_below_the_fault_level_is_an_error():
    """Worked example: the fixture's 20 kA VD4 incomers on a 25 kA bus fail; the 25 kA outgoing VCB passes; Icw must cover it too."""
    p = variant("utility_50mwac", with_levels(level(25)))
    fs = {f.subjects: f for f in findings(p, "protection.fault_level") if f.subjects}
    assert fs[("icog_vcb",)].severity is Severity.ERROR and (fs[("icog_vcb",)].measured, fs[("icog_vcb",)].limit) == (20, 25)
    assert fs[("aux_vcb",)].severity is Severity.ERROR
    assert fs[("out_vcb",)].severity is Severity.INFO and "covers the 25 kA fault level at 33000 V" in fs[("out_vcb",)].message
    assert fs[("out_vcb",)].formula == "Icu >= I_fault; Icw >= I_fault"


def test_without_a_fault_level_one_info_lists_the_unchecked_devices():
    fs = findings(fixture("utility_50mwac"), "protection.fault_level")
    assert len(fs) == 1 and fs[0].severity is Severity.INFO and fs[0].subjects == () and "33000 V" in fs[0].message


def test_duplicate_voltages_are_rejected():
    with pytest.raises(Exception, match="twice"):
        variant("utility_50mwac", with_levels(level(25), level(31.5)))
    assert FaultLevel(voltage_v=415, fault_ka=36).clearing_s is None

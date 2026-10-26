"""Worked examples per rule family. Each expected value is computed in the test from catalog data with the formula written out."""

import math

import pytest
from test_rules import CATALOG, drop_edges, drop_layout, fixture, node, raw, rooftop_fixed, series, variant, with_specs

from unicad.domain.project import Project
from unicad.rules import Severity, design_current, run_rules
from unicad.rules.pv import cell_temp_max, cell_temp_min, string_voltage

ADANI = CATALOG.get("adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp")
VIKRAM = CATALOG.get("vikram-solar-hypersol-m10r-144-580-wp")
HUAWEI = CATALOG.get("huawei-sun2000-100ktl-m2")
SMA = CATALOG.get("sma-sc-4400-up")


def findings(project, rule_id, catalog=CATALOG):
    return run_rules(project, catalog).by_rule(rule_id)


def mini(nodes, edges, name="rooftop_500kwp") -> Project:
    data = raw(name)
    drop_layout(data)
    data["sheets"] = []
    data["network"] = {"nodes": nodes, "edges": edges}
    return Project.model_validate(data, context={"catalog": CATALOG})


def n(id, kind, **kw):
    return {"id": id, "tag": kw.pop("tag", id.upper()), "kind": kind, **kw}


def e(id, src, dst, service="lv_ac", **kw):
    return {"id": id, "src": src, "dst": dst, "service": service, **kw}


# ---------------- thermal model and string voltages ----------------


def test_noct_cell_temperature_rooftop():
    # Tcell = Tamb_max + (NOCT - 20) / 800 x G = 45 + (45 - 20) / 800 x 1000
    p = fixture("rooftop_500kwp")
    assert cell_temp_max(p.design_basis, ADANI).value == pytest.approx(45 + 25 / 800 * 1000) == pytest.approx(76.25)


def test_pvsyst_cell_temperature_utility():
    # eta = Pmax / (L x W x 1000) = 580 / (2.278 x 1.134 x 1000); Tcell = 50 + 0.9 x 1000 x (1 - eta) / 29
    p = fixture("utility_50mwac")
    eta = 580 / (2.278 * 1.134 * 1000)
    expected = 50 + 0.9 * 1000 * (1 - eta) / 29
    assert expected == pytest.approx(74.067, abs=0.001)
    assert cell_temp_max(p.design_basis, VIKRAM).value == pytest.approx(expected)


def test_string_voltages_at_design_temperatures():
    p = fixture("rooftop_500kwp")
    cold, hot = cell_temp_min(p.design_basis), cell_temp_max(p.design_basis, ADANI)
    # Voc cold = 18 x 51.5 x (1 - 0.0024 x (10 - 25)) = 960.37 V; Vmp hot uses gamma_Pmax = -0.32 %/degC
    assert string_voltage(ADANI, 18, "voc", cold).value == pytest.approx(18 * 51.5 * 1.036) == pytest.approx(960.372)
    assert string_voltage(ADANI, 18, "vmp", hot).value == pytest.approx(18 * 43.05 * (1 - 0.0032 * 51.25)) == pytest.approx(647.816, abs=0.001)
    assert string_voltage(ADANI, 18, "vmp", cold).value == pytest.approx(18 * 43.05 * 1.048)


# ---------------- pv_strings ----------------


def test_utility_voc_limit_is_min_of_inverter_and_module():
    # 28 x 50.9 x (1 + 0.0026 x 20) = 1499.31 V <= 1500 V passes; 29 modules give 1552.86 V
    assert not findings(variant("utility_50mwac", series(28)), "pv_strings.voc_max")
    (f,) = findings(variant("utility_50mwac", series(29)), "pv_strings.voc_max")
    assert f.measured == pytest.approx(29 * 50.9 * 1.052, abs=0.01) == pytest.approx(1552.86, abs=0.01)
    assert f.limit == min(SMA.dc_max_voltage, VIKRAM.max_system_voltage) == 1500


def test_short_rooftop_string_falls_below_mppt_min_and_start_voltage():
    # Vmp hot per module = 43.05 x (1 - 0.0032 x 51.25) = 35.990 V; Voc hot = 51.5 x (1 - 0.0024 x 51.25) = 45.166 V
    vmp_hot, voc_hot = 43.05 * (1 - 0.0032 * 51.25), 51.5 * (1 - 0.0024 * 51.25)
    p5 = variant("rooftop_500kwp", series(5))
    (w,) = findings(p5, "pv_strings.vmp_min")
    assert w.severity is Severity.WARNING and w.measured == pytest.approx(5 * vmp_hot, abs=0.01) and w.limit == HUAWEI.mppt_min
    assert not findings(p5, "pv_strings.start_voltage")  # 5 x 45.166 = 225.8 V >= 200 V
    (err,) = findings(variant("rooftop_500kwp", series(4)), "pv_strings.start_voltage")
    assert err.severity is Severity.ERROR and err.measured == pytest.approx(4 * voc_hot, abs=0.01) and err.limit == 200


def test_utility_start_voltage_and_mppt_window():
    # Tcell_max = 74.067 degC; Voc hot = 50.9 x (1 - 0.0026 x 49.067) = 44.407 V; 25 x 44.407 = 1110.2 V < start 1112 V
    t = 50 + 0.9 * 1000 * (1 - 580 / (2.278 * 1.134 * 1000)) / 29
    p = variant("utility_50mwac", series(25))
    (err,) = findings(p, "pv_strings.start_voltage")
    assert err.measured == pytest.approx(25 * 50.9 * (1 - 0.0026 * (t - 25)), abs=0.01) == pytest.approx(1110.17, abs=0.01)
    (w,) = findings(p, "pv_strings.vmp_min")
    assert w.measured == pytest.approx(25 * 42.8 * (1 - 0.003 * (t - 25)), abs=0.01)
    assert w.limit == SMA.mppt_min
    # Vmp cold = 42.8 x (1 + 0.003 x 20) = 45.368 V; 30 x 45.368 = 1361.0 V > 1325 V
    (hi,) = findings(variant("utility_50mwac", series(30)), "pv_strings.vmp_max")
    assert hi.severity is Severity.WARNING and hi.measured == pytest.approx(30 * 45.368, abs=0.01) and hi.limit == 1325


def test_overloaded_inverter_inputs_current_and_ratio():
    def edit(data):
        node(data, "str")["count"] = 25

    report = run_rules(variant("rooftop_500kwp", edit), CATALOG)
    print("\n" + report.render())
    # 25 strings on 10 MPPT x 2 inputs = 20
    (inp,) = report.by_rule("pv_strings.inputs")
    assert (inp.measured, inp.limit) == (25, 20)
    # strings per MPPT = ceil(25 / 10) = 3; 3 x 1.25 x 13.65 = 51.19 A > 40 A
    (isc,) = report.by_rule("pv_strings.isc_per_mppt")
    assert isc.measured == pytest.approx(3 * 1.25 * 13.65, abs=0.01) and isc.limit == 40
    # 3 x Imp 12.91 = 38.73 A > 30 A
    (op,) = report.by_rule("pv_strings.operating_current")
    assert op.measured == pytest.approx(3 * 12.91) and op.limit == 30
    # reverse current (3 - 1) x 1.25 x 13.65 = 34.1 A > 30 A, but the DC fuse box holds string fuses
    assert not report.by_rule("pv_strings.string_fuses")
    # DC/AC = 25 x 18 x 555 / 1000 / 100 = 2.4975
    (r,) = report.by_rule("ratio.dc_ac_inverter")
    assert r.measured == pytest.approx(25 * 18 * 555 / 1000 / 100)


def test_parallel_strings_without_fuses_are_an_error():
    def edit(data):
        node(data, "str")["count"] = 25
        data["network"]["nodes"] = [x for x in data["network"]["nodes"] if x["id"] != "dcfb"]
        drop_edges(data, "e_dcfb_spd", "e_dcfb_iso")
        next(x for x in data["network"]["edges"] if x["id"] == "e_str")["dst"] = "dciso:in"
        data["network"]["edges"].append({"id": "e_iso_spd", "src": "dciso:out", "dst": "dcspd:line", "service": "dc"})

    (f,) = findings(variant("rooftop_500kwp", edit), "pv_strings.string_fuses")
    assert f.severity is Severity.ERROR
    assert f.measured == pytest.approx(2 * 1.25 * 13.65, abs=0.01) and f.limit == ADANI.max_series_fuse == 30
    assert "no string fuse" in f.message


def test_utility_combiner_is_assumed_to_hold_string_fuses():
    (f,) = findings(fixture("utility_50mwac"), "pv_strings.string_fuses")
    # 15 SCBs x 24 strings on one MPPT: (360 - 1) x 1.25 x 14.2 = 6372.25 A
    assert f.severity is Severity.INFO and f.measured == pytest.approx(359 * 1.25 * 14.2)
    assert any("scb is assumed to contain one fuse per string" in a for a in f.assumptions)


def test_utility_current_per_mppt():
    report = run_rules(fixture("utility_50mwac"), CATALOG)
    # 360 x 1.25 x 14.2 = 6390 A <= 8400 A; 360 x 13.56 = 4881.6 A > 4750 A (clipping warning)
    assert not report.by_rule("pv_strings.isc_per_mppt")
    (op,) = report.by_rule("pv_strings.operating_current")
    assert op.measured == pytest.approx(360 * 13.56) and op.limit == SMA.max_input_current_per_mppt


def test_missing_noct_skips_hot_checks_with_a_warning():
    bare = ADANI.model_copy(update={"id": "test-module-no-noct", "noct_c": None})
    cat = with_specs(bare)

    def edit(data):
        node(data, "str")["spec"] = bare.id

    report = run_rules(variant("rooftop_500kwp", edit, cat), cat)
    (f,) = report.by_rule("pv_strings.hot_cell")
    assert f.severity is Severity.WARNING and f.subjects == ("str", "inv")
    assert not report.by_rule("pv_strings.vmp_min") and not report.by_rule("pv_strings.start_voltage")


# ---------------- ratio ----------------


def test_dc_ac_ratio_outside_band():
    def edit(data):
        data["design_basis"]["dc_ac_ratio_target"] = 1.3

    report = run_rules(variant("rooftop_500kwp", edit), CATALOG)
    # per inverter 10 x 18 x 555 W = 99.9 kWp / 100 kVA = 0.999; band 1.3 x 0.9 .. 1.3 x 1.1 = 1.17 .. 1.43
    (inv,) = report.by_rule("ratio.dc_ac_inverter")
    (plant,) = report.by_rule("ratio.dc_ac_plant")
    for f in (inv, plant):
        assert f.severity is Severity.WARNING and f.measured == pytest.approx(0.999) and f.limit == pytest.approx(1.17)
    assert not run_rules(fixture("rooftop_500kwp"), CATALOG).by_rule("ratio.dc_ac_plant")


def test_utility_plant_ratio_inside_band():
    # 6 x 2 x 15 x 24 = 4320 strings x 27 x 580 W = 67651.2 kWp over 12 x 4400 = 52800 kVA = 1.2813, inside 1.17 .. 1.43
    from unicad.rules.ratio import plant_capacity

    dc, ac = plant_capacity(fixture("utility_50mwac"), CATALOG)
    assert dc == pytest.approx(4320 * 27 * 0.580) and ac == 52800
    assert not findings(fixture("utility_50mwac"), "ratio.dc_ac_plant")


def test_export_limit_on_poi():
    def edit(data):
        node(data, "poi")["attributes"] = {"export_limit_kva": 400}

    (f,) = findings(variant("rooftop_500kwp", edit), "ratio.export_limit")
    assert (f.measured, f.limit, f.subjects) == (500, 400, ("poi",))


# ---------------- structure ----------------


def test_cut_cable_leaves_ports_open_and_sources_stranded():
    def edit(data):
        drop_edges(data, "e_ct_poi")

    report = run_rules(variant("rooftop_500kwp", edit), CATALOG)
    open_ports = {f.message for f in report.by_rule("structure.unconnected_port")}
    assert any("CT-02 (ct) port 'out'" in m for m in open_ports) and any("POI-01 (poi_grid) port 'in'" in m for m in open_ports)
    (stranded,) = report.by_rule("structure.path_to_poi")
    assert stranded.subjects == ("str",) and stranded.severity is Severity.ERROR


def test_network_without_poi():
    p = mini([n("pv", "pv_string", spec=ADANI.id, modules_in_series=18), n("ld", "load")], [e("e1", "pv:out", "ld:in", "dc")])
    (f,) = findings(p, "structure.path_to_poi")
    assert f.message == "network has no poi_grid node"


def test_inverter_on_wrong_voltage_zone():
    p = mini(
        [n("pv", "pv_string", spec=ADANI.id, modules_in_series=18), n("inv", "inverter", spec=HUAWEI.id), n("poi", "poi_grid", voltage_v=230.0)],
        [e("e1", "pv:out", "inv:dc", "dc"), e("e2", "inv:ac", "poi:in")],
    )
    (f,) = findings(p, "structure.inverter_ac_voltage")
    # |230 - 400| / 400 = 0.425 > 0.05
    assert f.severity is Severity.ERROR and f.measured == pytest.approx(0.425) and f.limit == 0.05


def test_transformer_hv_winding_on_wrong_zone():
    tx = CATALOG.get("is1180-33kv-100kva-dyn11")
    p = mini(
        [n("ld", "load", voltage_v=415.0), n("tr", "aux_transformer", spec=tx.id), n("poi", "poi_grid", voltage_v=11000.0)],
        [e("e1", "poi:in", "tr:hv", "mv_ac"), e("e2", "tr:lv", "ld:in")],
    )
    fs = {f.message.split(":")[0]: f for f in findings(p, "structure.transformer_voltage")}
    # HV: |11000 - 33000| / 33000 = 0.667 -> error; LV: |415 - 433| / 433 = 0.0416 -> info
    assert fs["TR HV winding"].severity is Severity.ERROR and fs["TR HV winding"].measured == pytest.approx(22000 / 33000, abs=1e-4)
    assert fs["TR LV winding"].severity is Severity.INFO and fs["TR LV winding"].measured == pytest.approx(18 / 433, abs=1e-4)


def test_missing_spec_and_string_length():
    p = mini([n("pv", "pv_string"), n("inv", "inverter"), n("poi", "poi_grid")], [e("e1", "pv:out", "inv:dc", "dc"), e("e2", "inv:ac", "poi:in")])
    fs = findings(p, "structure.missing_spec")
    assert {(f.subjects, f.severity) for f in fs} == {(("pv",), Severity.ERROR), (("inv",), Severity.ERROR)}
    assert any("modules_in_series" in f.message for f in fs)


# ---------------- protection ----------------


def test_rooftop_breaker_ratings_against_inverter_current():
    report = run_rules(fixture("rooftop_500kwp"), CATALOG)
    i_inv = 110 * 1000 / (math.sqrt(3) * 415)  # ac_max_kva at the 415 V zone = 153.03 A
    fs = {f.subjects[0]: f for f in report.by_rule("protection.rated_current")}
    assert fs["inv_mccb"].limit == pytest.approx(1.25 * i_inv, abs=0.01) == pytest.approx(191.29, abs=0.01)
    assert fs["inv_mccb"].measured == 160
    assert fs["acdb_acb"].limit == pytest.approx(1.25 * 5 * i_inv, abs=0.01) == pytest.approx(956.45, abs=0.01)
    assert not run_rules(variant("rooftop_500kwp", rooftop_fixed), CATALOG).by_rule("protection.rated_current")


def test_design_current_hook():
    p = fixture("rooftop_500kwp")
    assert design_current(p, CATALOG, "inv_mccb").value == pytest.approx(110e3 / (math.sqrt(3) * 415))
    # DC isolator carries 10 strings: 10 x 1.25 x 13.65 = 170.625 A
    assert design_current(p, CATALOG, "dciso").value == pytest.approx(170.625)
    u = fixture("utility_50mwac")
    assert design_current(u, CATALOG, "aux_vcb") is None  # load feeder, not on the export path
    # 12 x 4400 kVA at 33 kV = 923.8 A
    assert design_current(u, CATALOG, "out_vcb").value == pytest.approx(12 * 4400e3 / (math.sqrt(3) * 33000))


def test_breaking_capacity_missing():
    base = CATALOG.get("lk-dsine-dn1-250-c-200a-4p")
    no_ka = base.model_copy(update={"id": "test-mccb-no-ka", "breaking_capacity_ka": None})
    cat = with_specs(no_ka)

    def edit(data):
        node(data, "inv_mccb")["spec"] = no_ka.id

    (f,) = findings(variant("rooftop_500kwp", edit, cat), "protection.breaking_capacity", cat)
    assert f.subjects == ("inv_mccb",) and f.severity is Severity.ERROR


def test_dc_spd_rated_below_string_voc():
    def edit(data):
        node(data, "dcspd")["spec"] = "finder-7p.23.9.750.x020"

    (f,) = findings(variant("rooftop_500kwp", edit), "protection.rated_voltage")
    # 750 V < 18 x 51.5 x 1.036 = 960.37 V
    assert f.measured == 750 and f.limit == pytest.approx(960.37, abs=0.01)


def test_ac_device_rated_below_zone_voltage():
    low = CATALOG.get("lk-dsine-dn1-250-c-200a-4p").model_copy(update={"id": "test-mccb-240v", "rated_voltage": 240.0})
    cat = with_specs(low)

    def edit(data):
        node(data, "inv_mccb")["spec"] = low.id

    (f,) = findings(variant("rooftop_500kwp", edit, cat), "protection.rated_voltage", cat)
    assert (f.measured, f.limit) == (240, 415)


def test_single_pole_device_needs_phase_voltage_only():
    def edit(data):
        node(data, "inv_mccb")["spec"] = "schneider-ic60n-c63-1p"  # 240 V >= 415 / sqrt(3) = 239.6 V

    assert not findings(variant("rooftop_500kwp", edit), "protection.rated_voltage")


def test_spd_and_earthing_presence():
    def edit(data):
        data["network"]["nodes"] = [x for x in data["network"]["nodes"] if x["id"] not in ("dcspd", "ac_spd")]
        drop_edges(data, "e_dcfb_spd", "e_ct_spd", "pe_inv")

    report = run_rules(variant("rooftop_500kwp", edit), CATALOG)
    assert [(f.subjects, f.severity) for f in report.by_rule("protection.spd_dc")] == [(("inv",), Severity.WARNING)]
    assert [(f.subjects, f.severity) for f in report.by_rule("protection.spd_ac")] == [(("inv",), Severity.WARNING)]
    earth = {f.subjects: f.severity for f in report.by_rule("protection.earthing")}
    assert earth == {("inv",): Severity.ERROR, ("ltp",): Severity.WARNING}


def _with_string_fuse(spec_id):
    def edit(data):
        data["network"]["nodes"].append({"id": "sfuse", "tag": "F-{0:02d}-{1:02d}", "kind": "fuse", "spec": spec_id, "count": 10, "group": "inv_sec"})
        next(x for x in data["network"]["edges"] if x["id"] == "e_str")["dst"] = "sfuse:in"
        data["network"]["edges"].append({"id": "e_fuse", "src": "sfuse:out", "dst": "dcfb:in", "service": "dc"})

    return edit


def test_string_fuse_rating_window():
    # In >= 1.25 x 13.65 = 17.06 A and In <= max_series_fuse 30 A
    (low,) = findings(variant("rooftop_500kwp", _with_string_fuse("eaton-bussmann-pv-15a10f")), "protection.string_fuse_rating")
    assert low.measured == 15 and low.limit == pytest.approx(1.25 * 13.65, abs=0.01)
    assert not findings(variant("rooftop_500kwp", _with_string_fuse("eaton-bussmann-pv-20a10f")), "protection.string_fuse_rating")
    big = CATALOG.get("eaton-bussmann-pv-20a10f").model_copy(update={"id": "test-gpv-32a", "rated_current": 32.0})
    cat = with_specs(big)
    (high,) = findings(variant("rooftop_500kwp", _with_string_fuse(big.id), cat), "protection.string_fuse_rating", cat)
    assert (high.measured, high.limit) == (32, 30)

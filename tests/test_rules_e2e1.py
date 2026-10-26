"""Rules regressions from the first end-to-end verifier run: transformer-fed LV faults, missing breaker specs, grouping of parallel runs."""

import pytest

from unicad.design import autobuild
from unicad.domain.network import Laying, NodeKind
from unicad.domain.project import FaultLevel
from unicad.rules import Severity, explain, run_rules
from unicad.rules.faults import prospective_fault
from unicad.rules.grouping import grouping
from test_design import CATALOG, UTIL_MOD, blank

SG350 = "sungrow-sg350hx"


@pytest.fixture(scope="module")
def string800():
    return autobuild(blank("utility_50mwac"), CATALOG, {"template": "utility_string", "ac_kw": 20000, "module": UTIL_MOD, "inverter": SG350}).project


def with_node(project, node_id, **update):
    nodes = tuple(n.model_copy(update=update) if n.id == node_id else n for n in project.network.nodes)
    return project.model_copy(update={"network": project.network.model_copy(update={"nodes": nodes})})


def test_m11_lv_bus_fault_is_computed_from_the_idt_winding_and_the_hv_fault_level(string800):
    """10 x 352 kVA on a 3520 kVA, 7 % winding at 800 V: 3520 / (sqrt(3) x 0.8 x 0.07) = 36.3 kA on an infinite bus, a little less behind a 25 kA 33 kV grid."""
    f = prospective_fault(string800, CATALOG, "acdb_brka")
    assert f.where == "transformer-fed" and f.ka == pytest.approx(3520 / (3**0.5 * 0.8 * 0.07) / 1000, abs=0.01)
    assert any("infinite bus" in a for a in f.assumptions)
    basis = string800.design_basis.model_copy(update={"fault_levels": (FaultLevel(voltage_v=33000, fault_ka=25, clearing_s=1),)})
    grid = prospective_fault(string800.model_copy(update={"design_basis": basis}), CATALOG, "acdb_brka")
    s_sc = 3**0.5 * 33 * 25
    assert grid.ka == pytest.approx(3520 / (3**0.5 * 0.8 * (0.07 + 3.52 / s_sc)) / 1000, abs=0.01) and grid.ka < f.ka


def test_m11_a_breaker_below_the_transformer_fed_fault_is_an_error(string800):
    weak = with_node(string800, "inv_brkaa", spec="abb-tmax-t5-v-400a-3p")
    fs = [f for f in run_rules(weak, CATALOG, ["protection"]).by_rule("protection.fault_level") if "inv_brkaa" in f.subjects]
    assert len(fs) == 1 and fs[0].severity is Severity.ERROR and "transformer-fed" in fs[0].message and fs[0].limit == pytest.approx(36.29, abs=0.01)
    assert "S / (sqrt(3) V" in explain(fs[0], CATALOG).formula
    clean = [f for f in run_rules(string800, CATALOG, ["protection"]).by_rule("protection.fault_level") if f.severity is Severity.ERROR]
    assert clean == []


def test_m11_a_breaker_without_a_spec_is_an_error_not_a_silent_gap(string800):
    bare = with_node(string800, "acdb_brka", spec=None)
    fs = run_rules(bare, CATALOG, ["structure"]).by_rule("structure.missing_spec")
    assert [(f.severity, f.subjects) for f in fs if "acdb_brka" in f.subjects] == [(Severity.ERROR, ("acdb_brka",))]


def test_m22_parallel_runs_take_a_cited_group_rating_factor():
    r = autobuild(blank("rooftop_500kwp"), CATALOG, {"template": "ci_rooftop", "ac_kw": 660, "module": "waaree-bin-08-580", "inverter": "sungrow-sg110cx"})
    net = r.project.network
    multi = [e for e in net.edges if e.runs > 1 and e.service.value == "lv_ac" and e.cable]
    assert multi
    fs = {f.subjects: f for f in run_rules(r.project, CATALOG, ["cables"]).by_rule("cables.ampacity")}
    for e in multi:
        f = fs[(e.id,)]
        k = next(q for q in f.inputs if q.name == "k_derating")
        assert k.value < 1 and "KEI Technical Handbook" in k.source, (e.id, k)
        assert f.limit == pytest.approx(next(q.value for q in f.inputs if q.name == "I_table") * k.value * e.runs, abs=0.01)
    assert r.errors == ()


def test_group_factor_falls_back_to_a_stated_assumption_where_the_handbook_has_no_row():
    cable = CATALOG.get("polycab-lt-xlpe-al-4c-300-arm")
    assert grouping(cable, Laying.TRAY, 1) is None
    assert grouping(cable, Laying.TRAY, 4).factor == 0.75 and "next" not in grouping(cable, Laying.TRAY, 4).source
    assert grouping(cable, Laying.BURIED_DIRECT, 7).factor is None and "stops at 6" in grouping(cable, Laying.BURIED_DIRECT, 7).source
    assert grouping(cable, Laying.CONDUIT, 2).factor is None and "assumption" in grouping(cable, Laying.CONDUIT, 2).source
    assert grouping(cable, Laying.TRENCH, 3, dc=True).factor is None


def test_inverter_breakers_that_must_be_acbs_are_tagged_acb(string800):
    for n in string800.network.by_kind(NodeKind.BREAKER):
        device = CATALOG.get(n.spec).device
        assert n.tag.split("-")[0] == device.upper(), (n.id, n.tag, device)

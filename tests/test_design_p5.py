"""Autobuild against a declared fault level, and with cable lengths routed on the array layout."""

import importlib.util
import math
from pathlib import Path

import pytest

from unicad.arrays import PlacementSpec, place_array
from unicad.catalog.store import load_catalog
from unicad.design import autobuild
from unicad.domain.catalog import CableSpec, ProtectionSpec
from unicad.domain.network import NodeKind
from unicad.domain.project import DesignBasis, FaultLevel
from unicad.ingest.site_import import import_site
from unicad.rules import Severity, run_rules
from unicad.routing import estimate_routes
from unicad.schedules import LengthMode, build_schedule
from test_design import CATALOG, CENTRAL, UTIL_MOD, blank, instances

ROOT = Path(__file__).resolve().parents[1]
GROUND_MODULE = "vikram-solar-hypersol-m10r-144-580-wp"


def with_levels(project, *levels: FaultLevel):
    basis = project.design_basis.model_copy(update={"fault_levels": levels})
    return project.model_copy(update={"design_basis": basis})


def central(project, **intent):
    return autobuild(project, CATALOG, {"template": "utility_central", "ac_kw": 20000, "module": UTIL_MOD, "inverter": CENTRAL} | intent)


# ---------------- fault level drives breaker and cable selection ----------------


def test_vcbs_are_chosen_at_or_above_the_declared_fault_level():
    plain = central(blank("utility_50mwac"))
    assert any("Breaking capacities are not checked against a fault level" in a for a in plain.assumptions)
    assert CATALOG.get(plain.project.network.node("icog").spec).breaking_capacity_ka == 20

    r = central(with_levels(blank("utility_50mwac"), FaultLevel(voltage_v=33000, fault_ka=31.5, clearing_s=1.0, source="fault study")))
    assert r.errors == () and r.blockers == ()
    for node_id in ("icog", "aux_vcb", "out_vcb"):
        vcb = CATALOG.get(r.project.network.node(node_id).spec)
        assert isinstance(vcb, ProtectionSpec) and vcb.breaking_capacity_ka == 31.5 and vcb.short_time_withstand_ka == 31.5
    assert any("Breaking capacity at 33000 V is chosen at or above the 31.5 kA fault level" in a for a in r.assumptions)
    assert not any("Breaking capacities are not checked" in a for a in r.assumptions)
    report = run_rules(r.project, CATALOG)
    assert not [f for f in report.by_rule("protection.fault_level") if f.severity is Severity.ERROR]
    assert {f.severity for f in report.by_rule("protection.fault_level")} == {Severity.INFO}


def test_a_fault_level_no_catalog_breaker_covers_is_a_blocker():
    r = central(with_levels(blank("utility_50mwac"), FaultLevel(voltage_v=33000, fault_ka=63, clearing_s=1.0)))
    assert any("breaking capacity >= 63 kA" in b for b in r.blockers)


def test_mv_cables_are_sized_for_the_fault_withstand():
    base = central(blank("utility_50mwac"))
    faulted = central(with_levels(blank("utility_50mwac"), FaultLevel(voltage_v=33000, fault_ka=25, clearing_s=1.0)))
    ref = CATALOG.get(base.project.network.edge_by_id["e_auxvcb_tr"].cable)
    got = CATALOG.get(faulted.project.network.edge_by_id["e_auxvcb_tr"].cable)
    assert isinstance(ref, CableSpec) and ref.size_mm2 < 25000 / 94 <= got.size_mm2 * faulted.project.network.edge_by_id["e_auxvcb_tr"].runs
    assert faulted.errors == ()
    assert not [f for f in run_rules(faulted.project, CATALOG).by_rule("cables.short_circuit") if f.severity is Severity.ERROR]
    assert any("I sqrt(t) / k" in a and "25 kA" in a for a in faulted.assumptions)


# ---------------- routed lengths ----------------


@pytest.fixture(scope="module")
def ground():
    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry
    spec = PlacementSpec(module=GROUND_MODULE, table_rows=2, table_cols=27, tilt_deg=25, n_blocks=2, modules_per_string=27, road_every_rows=12, target_dc_kwp=144 * 27 * 0.58 * 1.1)
    placed = place_array(site, spec, CATALOG.get(GROUND_MODULE))
    return site, placed


def ground_build(ground, **intent):
    site, placed = ground
    base = blank("utility_50mwac")
    base = base.model_copy(update={"site": base.site.model_copy(update={"geometry": site})})
    return autobuild(base, CATALOG, {"template": "utility_central", "module": GROUND_MODULE, "inverter": CENTRAL, "modules_per_string": 27} | intent, layout=placed)


def test_autobuild_replaces_placeholder_lengths_with_routed_ones(ground):
    r = ground_build(ground)
    net = r.project.network
    est = estimate_routes(r.project, CATALOG).templates()
    string_edges = [e for e in net.edges if e.id.startswith("e_str_")]
    assert string_edges and all(e.length_m == round(est[e.id].maximum_m, 1) and e.length_m != 30.0 for e in string_edges)
    scb_edges = [e for e in net.edges if e.id.startswith("e_scb_inv")]
    assert all(e.length_m == round(est[e.id].maximum_m, 1) for e in scb_edges)
    assert r.summary["routed_edges"] == len(string_edges) + len(scb_edges) + sum(1 for e in net.edges if e.id.startswith("e_icog_bus"))
    assert any(a.startswith("Cable route lengths are routed on the array layout") and "string" in a and "scb_inv" in a for a in r.assumptions)
    assert not any(a.startswith("Cable route lengths are design placeholders") for a in r.assumptions)
    left = next(a for a in r.assumptions if a.startswith("Cable route lengths still design placeholders"))
    assert "inv_idt 15 m" in left and "string" not in left.split(":")[1]


def test_given_lengths_are_never_replaced(ground):
    r = ground_build(ground, lengths={"string": 40.0})
    assert all(e.length_m == 40.0 for e in r.project.network.edges if e.id.startswith("e_str_"))
    assert any(e.id.startswith("e_scb_inv") and e.length_m != 150.0 for e in r.project.network.edges)


def test_layout_blocks_and_their_lv_sections_carry_one_block_number(ground):
    """close-e2e-1 m19: layout blocks were tagged BLOCK-01-01..BLOCK-01-06."""
    tops = [g.tag for g in ground_build(ground).project.network.groups if g.within is None and g.tag.startswith("BLOCK")]
    assert tops == ["BLOCK-01", "BLOCK-02"]
    net = autobuild(blank("utility_50mwac"), CATALOG, {"template": "utility_string", "ac_kw": 7040, "module": UTIL_MOD, "inverter": "sungrow-sg350hx",
                                                        "modules_per_string": 28, "inverters_per_block": 20}).project.network  # fmt: skip
    sections = [g.tag for g in net.groups if g.within is not None and net.group_by_id[g.within].tag == "BLOCK-01" and g.count == 1]
    assert sections == ["BLOCK-01 SECTION A", "BLOCK-01 SECTION B"]


def test_without_a_layout_lengths_stay_placeholders():
    r = central(blank("utility_50mwac"))
    assert "routed_edges" not in r.summary and r.project.network.edge_by_id["e_str"].length_m == 30.0
    assert any(a.startswith("Cable route lengths are design placeholders") for a in r.assumptions)


def test_ground_demo_dc_voltage_drop_fails_on_the_fixture_cables_and_passes_after_routed_sizing(ground):
    spec = importlib.util.spec_from_file_location("build_schedule_samples", ROOT / "tools" / "build_schedule_samples.py")
    samples = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(samples)
    before = build_schedule(samples.ground_demo(CATALOG), CATALOG, LengthMode.AUTO)
    worst = dict(before.vd_summary)["DC VD, WORST PATH"]
    assert float(worst.split(" %")[0]) > 1.0, worst  # the sample fills from the switchyard since close-e2e-3 B9, so its fixture cables now fit

    r = ground_build(ground)
    assert r.errors == () and r.blockers == ()
    limit = 100 * r.project.design_basis.vdrop_dc_max
    after = build_schedule(r.project, CATALOG, LengthMode.AUTO)
    summary = dict(after.vd_summary)["DC VD, WORST PATH"]
    value = float(summary.split(" %")[0])
    assert "exceeds" not in summary and value <= limit
    paths = [f for f in run_rules(r.project, CATALOG).by_rule("vdrop.dc") if f.message.startswith("DC voltage drop from")]
    assert paths and all(f.severity is Severity.INFO and f.measured <= limit for f in paths)
    strings = {CATALOG.get(r.project.network.edge_by_id[e].cable).size_mm2 for e in r.project.network.edge_by_id if e.startswith("e_str_")}
    assert min(strings) >= 4


def test_central_inverters_stay_at_their_block_stations(ground):
    """close-e2e-2 N3: only string inverters move into the field; a central inverter fed by SCBs stays at its block station."""
    r = ground_build(ground)
    lay = r.project.array_layout
    assert lay.switchyard is not None and not [e for e in lay.equipment if e.kind == "inverter"]
    assert not any(a.startswith("String inverters stand in the field") for a in r.assumptions)

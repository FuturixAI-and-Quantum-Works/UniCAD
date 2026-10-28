"""Automatic network design: string sizing, topology templates, autobuild against the rules, and layout linking."""

import json
from collections import Counter
from pathlib import Path

import pytest

from unicad.arrays import PlacementSpec, place_array
from unicad.catalog.store import load_catalog
from unicad.design import DesignError, autobuild, size_strings
from unicad.domain.network import ElectricalNetwork, NodeKind
from unicad.domain.catalog import TransformerSpec
from unicad.domain.project import Project, Segment
from unicad.domain.site import Setbacks
from unicad.ingest.site_import import import_site
from unicad.rules import Severity, run_rules
from unicad.rules.topology import zone_at
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "projects"
CATALOG = load_catalog()
ROOF_MOD = "adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp"
ROOF_INV = "huawei-sun2000-100ktl-m2"
UTIL_MOD = "vikram-solar-hypersol-m10r-144-580-wp"
CENTRAL = "sma-sc-4400-up"


def fixture(name: str) -> Project:
    return parse_project((FIXTURES / f"{name}.json").read_text(encoding="utf-8"), catalog=CATALOG)


def blank(name: str, **update) -> Project:
    """The fixture's client, site and design basis with no network, layout or sheets."""
    p = fixture(name)
    update = {k: Segment(v) if k == "segment" else v for k, v in update.items()}
    return p.model_copy(update={"network": ElectricalNetwork(), "array_layout": None, "sheets": (), **update})


def chain(net: ElectricalNetwork, start: str) -> list[str]:
    return [net.node(n).kind.value for n in net.path_to_poi(start)]


def instances(net: ElectricalNetwork, kind: NodeKind) -> int:
    return sum(net.instance_count(n.id) for n in net.by_kind(kind))


def assert_clean(r) -> None:
    errors = [f"{f.rule_id}: {f.message}" for f in r.errors]
    assert errors == [], errors
    again = run_rules(r.project, CATALOG)
    assert again.counts == r.counts


# ---------------- string sizing ----------------


def test_size_strings_finds_the_longest_string_the_voltage_rules_accept():
    basis = fixture("rooftop_500kwp").design_basis
    d = size_strings(CATALOG.get(ROOF_MOD), CATALOG.get(ROOF_INV), basis)
    assert d.modules_per_string == d.modules_per_string_max and d.binding["modules_per_string_max"] == "voc_max"
    assert d.voltages.voc_cold_v <= 1100 < d.voltages.voc_cold_v / d.modules_per_string * (d.modules_per_string + 1)
    assert d.margins["voc_max_v"] > 0 and d.margins["vmp_min_v"] > 0
    assert d.strings_per_mppt_max == 2 and d.strings_per_inverter_max == 20
    assert abs(d.dc_ac_ratio - basis.dc_ac_ratio_target) <= 0.1 * basis.dc_ac_ratio_target

    def voc_errors(n: int) -> list:
        data = json.loads((FIXTURES / "rooftop_500kwp.json").read_text(encoding="utf-8"))
        for node in data["network"]["nodes"]:
            if node["id"] == "str":
                node["modules_in_series"] = n
        data.pop("array_layout")
        p = Project.model_validate(data, context={"catalog": CATALOG})
        return run_rules(p, CATALOG, ["pv_strings"]).by_rule("pv_strings.voc_max")

    assert voc_errors(d.modules_per_string) == ()
    assert voc_errors(d.modules_per_string + 1)


def test_size_strings_rejects_a_requested_length_outside_the_range():
    basis = fixture("utility_50mwac").design_basis
    d = size_strings(CATALOG.get(UTIL_MOD), CATALOG.get(CENTRAL), basis)
    assert (d.modules_per_string_min, d.modules_per_string_max) == (27, 28)
    assert d.strings_per_mppt_max is None and d.binding["strings_per_inverter_max"] == "max_dc_short_circuit_current"
    assert size_strings(CATALOG.get(UTIL_MOD), CATALOG.get(CENTRAL), basis, 27).modules_per_string == 27
    with pytest.raises(DesignError, match="outside the allowed range 27..28"):
        size_strings(CATALOG.get(UTIL_MOD), CATALOG.get(CENTRAL), basis, 29)


# ---------------- templates reach 0 rule errors ----------------

TEMPLATE_CASES = {
    "residential": ("rooftop_500kwp", {"segment": "residential"}, {"template": "residential", "ac_kw": 10, "module": ROOF_MOD}),
    "ci_rooftop_lt": ("rooftop_500kwp", {}, {"template": "ci_rooftop", "ac_kw": 500, "module": ROOF_MOD, "inverter": ROOF_INV}),
    "ci_rooftop_feeder": ("rooftop_500kwp", {}, {"template": "ci_rooftop", "ac_kw": 300, "module": ROOF_MOD, "inverter": ROOF_INV, "connection": "dedicated_feeder"}),
    "ci_rooftop_ht": ("rooftop_500kwp", {}, {"template": "ci_rooftop", "ac_kw": 2000, "grid_voltage_v": 11000, "module": ROOF_MOD, "inverter": ROOF_INV}),
    "utility_string": ("utility_50mwac", {}, {"template": "utility_string", "ac_kw": 20000, "module": UTIL_MOD, "inverter": "sungrow-sg110cx"}),
    "utility_central": ("utility_50mwac", {}, {"template": "utility_central", "ac_kw": 50000, "module": UTIL_MOD, "inverter": CENTRAL}),
    "bess_ac": ("utility_50mwac", {"segment": "bess"}, {"template": "bess_ac", "ac_kw": 20000, "bess_kwh": 40000}),
    "bess_dc": ("utility_50mwac", {"segment": "bess"}, {"template": "bess_dc", "ac_kw": 26400, "module": UTIL_MOD, "inverter": CENTRAL, "bess_kw": 6000, "bess_kwh": 12000}),
    "hybrid": ("utility_50mwac", {"segment": "hybrid"}, {"template": "hybrid", "ac_kw": 30000, "module": UTIL_MOD, "inverter": CENTRAL, "bess_kw": 10000, "bess_kwh": 20000}),
}


@pytest.fixture(scope="module")
def built():
    return {k: autobuild(blank(f, **u), CATALOG, i) for k, (f, u, i) in TEMPLATE_CASES.items()}


@pytest.mark.parametrize("case", list(TEMPLATE_CASES))
def test_every_template_builds_a_rule_clean_network(built, case):
    r = built[case]
    assert_clean(r)
    warnings = sorted({f.rule_id for f in r.findings if f.severity is Severity.WARNING})
    assert set(warnings) <= {"cables.missing", "ratio.dc_ac_inverter", "ratio.dc_ac_plant", "pv_strings.operating_current"}, warnings
    net = r.project.network
    assert net.unconnected_ports() == ()
    for kind in (NodeKind.INVERTER, NodeKind.PCS):
        for n in net.by_kind(kind):
            assert any(e.dst == "ep:pe" for e in net.edges_at(n.id)), f"{n.id} not earthed"
    assert r.assumptions and all(isinstance(a, str) for a in r.assumptions)


def test_bess_and_hybrid_put_storage_behind_its_own_transformer_on_the_mv_bus(built):
    for case in ("bess_ac", "hybrid"):
        net = built[case].project.network
        assert chain(net, "bat") == ["battery", "pcs", "breaker", "transformer", "vcb", "ct", "vcb", "busbar", "vcb", "ct", "poi_grid"]
        tr = net.node("bess_tr").spec
        assert isinstance(tr, TransformerSpec) and tr.lv_kv == (0.69, 0.69) and tr.rated_kva == 2 * 2520
    hybrid = built["hybrid"].project.network
    assert chain(hybrid, "str")[:5] == ["pv_string", "fuse", "scb", "inverter", "transformer"]
    assert {e.src_node for e in hybrid.edges if e.dst == "mv_bus:bus"} == {"bf_vcb", "bf_vcb_bess"}


def test_dc_coupled_storage_reaches_the_inverter_through_a_dc_dc_converter(built):
    net = built["bess_dc"].project.network
    assert chain(net, "bat")[:3] == ["battery", "dc_dc_converter", "inverter"]
    dcdc = net.node("dcdc")
    assert dcdc.kind is NodeKind.DC_DC_CONVERTER and dcdc.symbol_key(CATALOG) == "inv.optimiser" and dcdc.attributes["rated_kw"] > 0
    assert dcdc.port_names == ("in", "out")
    assert not net.by_kind(NodeKind.DC_FUSE_BOX) or all(n.id != "dcdc" for n in net.by_kind(NodeKind.DC_FUSE_BOX))
    battery_side, bus_side = zone_at(net, "dcdc", "in"), zone_at(net, "dcdc", "out")
    assert battery_side is not None and bus_side is not None and battery_side != bus_side and "bat" in battery_side.node_ids
    assert any("dc_dc_converter node" in a for a in built["bess_dc"].assumptions)


def test_residential_three_phase_picks_a_real_ten_kilowatt_inverter(built):
    r = built["residential"]
    assert r.blockers == ()
    inv = r.project.network.node("inv").spec
    assert CATALOG.get(inv).phases == 3 and CATALOG.get(inv).ac_rated_kva == 10
    assert chain(r.project.network, "str") == ["pv_string", "dc_fuse_box", "dc_isolator", "inverter", "breaker", "acdb", "breaker", "meter", "breaker", "lt_panel", "meter", "poi_grid"]


def test_residential_single_phase_builds_with_a_real_one_phase_inverter_at_zero_errors():
    r = autobuild(blank("rooftop_500kwp", segment="residential"), CATALOG, {"template": "residential", "ac_kw": 5, "module": ROOF_MOD, "grid_voltage_v": 230})
    assert_clean(r)
    assert r.blockers == () and r.project.design_basis.grid.phases == 1
    spec = CATALOG.get(r.project.network.node("inv").spec)
    assert (spec.phases, spec.ac_rated_kva, spec.ac_voltage) == (1, 5, 230)
    assert any("1-phase string inverter that takes the module and covers 5 kW" in a for a in r.assumptions)
    assert chain(r.project.network, "str") == ["pv_string", "dc_fuse_box", "dc_isolator", "inverter", "breaker", "acdb", "breaker", "meter", "breaker", "lt_panel", "meter", "poi_grid"]
    assert r.project.network.node("inv_brk").spec.startswith("schneider-ic60n") or CATALOG.get(r.project.network.node("inv_brk").spec).rated_voltage >= 230


def test_residential_inverter_is_the_smallest_that_covers_the_load_and_takes_the_module():
    picks = {kw: CATALOG.get(autobuild(blank("rooftop_500kwp", segment="residential"), CATALOG, {"template": "residential", "ac_kw": kw, "module": ROOF_MOD, "grid_voltage_v": 230}).project.network.node("inv").spec) for kw in (3, 6)}
    assert picks[3].ac_rated_kva == 3 and picks[6].ac_rated_kva == 6
    assert all(p.phases == 1 for p in picks.values())
    gap = autobuild(blank("rooftop_500kwp", segment="residential"), CATALOG, {"template": "residential", "ac_kw": 0.4, "module": ROOF_MOD, "grid_voltage_v": 230})
    assert any("catalog gap" in b for b in gap.blockers)


def test_ht_rooftop_steps_up_through_a_catalog_transformer_and_rmu(built):
    net = built["ci_rooftop_ht"].project.network
    assert chain(net, "str") == ["pv_string", "dc_fuse_box", "dc_isolator", "inverter", "breaker", "acdb", "breaker", "ct", "transformer", "rmu", "ct", "vcb", "busbar", "vcb", "ct", "poi_grid"]
    assert net.node("tr").spec == "is1180-11kv-2500kva-dyn11"
    assert net.node("net_meter").spec == "generic-meter-net-ct_pt-0.5s"


def test_800v_string_inverters_build_with_a_project_idt_and_1150v_breakers():
    r = autobuild(blank("utility_50mwac"), CATALOG, {"template": "utility_string", "ac_kw": 20000, "module": UTIL_MOD, "inverter": "sungrow-sg350hx"})
    assert r.errors == ()
    net = r.project.network
    idt = net.node("idt").spec
    assert isinstance(idt, TransformerSpec) and idt.lv_kv == (0.8, 0.8) and idt.hv_kv == 33 and idt.manufacturer == "Project-specific"
    for brk in ("inv_brkaa", "inv_brkba", "acdb_brka", "acdb_brkb"):
        assert CATALOG.get(net.node(brk).spec).rated_voltage >= 800
    assert any("project-specific inline spec (idt-7040kva-33-0.8-0.8-ynd11d11-project)" in a and "BHEL PS 439-1304" in a for a in r.assumptions)
    # close-e2e-3 n23: the 800 V ACCB takes the three-pole DEHNguard M WE 600 (Uc 600 V >= 1.1 x 800 / sqrt 3), not a single-pole unit or a blocker
    assert not any("ACCB-SPD" in b for b in r.blockers), r.blockers
    assert {net.node(n).spec for n in ("acdb_spda", "acdb_spdb")} == {"dehn-dg-se-h-1000-fm-3x"}


def test_block_lv_buses_are_capped_so_every_breaker_has_a_catalog_spec():
    """B3: 19 x SG350HX (800 V) on one bus need a ~6 kA ACB beyond the 4000 A catalog maximum; the block splits into two LV windings of at most 12."""
    r = autobuild(blank("utility_50mwac"), CATALOG, {"template": "utility_string", "ac_kw": 20000, "module": UTIL_MOD, "inverter": "sungrow-sg350hx"})
    net = r.project.network
    assert [n.id for n in net.by_kind(NodeKind.BREAKER) if n.spec is None] == []
    secs = [g for g in net.groups if g.id.startswith("inv_sec")]
    assert sorted(g.count for g in secs) == [9, 10]
    assert any("12 inverters at most per bus" in a and "4000" not in a for a in r.assumptions)
    with pytest.raises(DesignError, match="inverters_per_block 30 is above the 24 one block takes"):
        autobuild(blank("utility_50mwac"), CATALOG, {"template": "utility_string", "ac_kw": 20000, "module": UTIL_MOD, "inverter": "sungrow-sg350hx", "inverters_per_block": 30})


def test_unsupported_intents_say_what_blocks():
    with pytest.raises(DesignError, match="needs ac_kw, dc_kwp or a layout"):
        autobuild(blank("utility_50mwac"), CATALOG, {"template": "utility_central"})


def test_large_hybrid_is_not_blocked_by_the_outgoing_vcb_rating():
    big = autobuild(blank("utility_50mwac", segment="hybrid"), CATALOG, {"template": "hybrid", "ac_kw": 50000, "module": UTIL_MOD, "inverter": CENTRAL, "bess_kw": 20000})
    assert big.blockers == () and big.errors == ()
    vcb = CATALOG.get(big.project.network.node("out_vcb").spec)
    assert (vcb.rated_voltage, vcb.rated_current) == (36000, 1600) and vcb.rated_current >= 1.25 * 70000 / (3**0.5 * 33)


# ---------------- comparable to the hand fixtures ----------------


def test_rooftop_500kw_matches_the_hand_fixture_chain_and_counts(built):
    hand, auto = fixture("rooftop_500kwp").network, built["ci_rooftop_lt"].project.network
    assert chain(auto, "str") == chain(hand, "str")
    for kind in (NodeKind.INVERTER, NodeKind.DC_ISOLATOR, NodeKind.DC_SPD, NodeKind.METER, NodeKind.CT, NodeKind.LT_PANEL):
        assert instances(auto, kind) == instances(hand, kind), kind
    assert abs(instances(auto, NodeKind.PV_STRING) - instances(hand, NodeKind.PV_STRING)) <= 0.2 * instances(hand, NodeKind.PV_STRING)
    assert auto.node("acdb_brk").spec == hand.node("acdb_acb").spec == "schneider-mtz2-n1-1000a-4p"
    assert auto.node("inv_brk").spec == hand.node("inv_mccb").spec


def test_utility_50mwac_matches_the_hand_fixture_chain_and_counts(built):
    hand, auto = fixture("utility_50mwac").network, built["utility_central"].project.network
    assert chain(auto, "str") == ["pv_string", "fuse", *chain(hand, "str")[1:]]
    for kind in (NodeKind.INVERTER, NodeKind.TRANSFORMER, NodeKind.AUX_TRANSFORMER, NodeKind.METER, NodeKind.LIGHTNING_ARRESTER, NodeKind.VCB, NodeKind.CT, NodeKind.RELAY):
        assert instances(auto, kind) == instances(hand, kind), kind
    assert abs(instances(auto, NodeKind.SCB) - instances(hand, NodeKind.SCB)) <= 0.2 * instances(hand, NodeKind.SCB)
    idt = auto.node("idt").spec
    assert (idt.rated_kva, idt.lv_kv, idt.vector_group) == (8800, (0.66, 0.66), "YNd11d11")


# ---------------- layout ----------------


@pytest.fixture(scope="module")
def rajasthan():
    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry
    site = site.model_copy(update={"setbacks": Setbacks(boundary_m=10, exclusion_m=5)})
    spec = PlacementSpec(module=UTIL_MOD, tilt_deg=25, table_cols=28, road_every_rows=12, block_dc_kwp=11000, target_dc_kwp=22000, latitude_deg=27.0, modules_per_string=28)
    return place_array(site, spec, CATALOG.get(UTIL_MOD))


def test_autobuild_maps_every_placed_string_to_one_network_string(rajasthan):
    r = autobuild(blank("utility_50mwac"), CATALOG, {"template": "utility_central", "module": UTIL_MOD, "inverter": CENTRAL}, layout=rajasthan)
    assert_clean(r)
    net, layout = r.project.network, r.project.array_layout
    network_strings = [i for n in net.by_kind(NodeKind.PV_STRING) for i in net.instance_ids(n.id)]
    assert len(layout.strings) == len(rajasthan.layout.strings) == len(network_strings)
    assert sorted(a.string for a in layout.strings) == sorted(network_strings)
    assert Counter(a.spans for a in layout.strings) == Counter(a.spans for a in rajasthan.layout.strings)
    assert len(layout.blocks) == len(rajasthan.layout.blocks) == instances(net, NodeKind.TRANSFORMER)
    assert r.summary["dc_kwp"] == pytest.approx(rajasthan.metrics.dc_kwp)
    assert all(n.modules_in_series == 28 for n in net.by_kind(NodeKind.PV_STRING))

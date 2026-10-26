import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from unicad.catalog.store import load_catalog
from unicad.domain.network import KIND_SYMBOLS, ElectricalNetwork, NetworkError, NodeKind
from unicad.domain.project import Project
from unicad.drafting.symbols import REGISTRY

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "projects"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rooftop(catalog) -> Project:
    return Project.model_validate(raw("rooftop_500kwp"), context={"catalog": catalog})


@pytest.fixture(scope="module")
def utility(catalog) -> Project:
    return Project.model_validate(raw("utility_50mwac"), context={"catalog": catalog})


def net(nodes, edges=(), groups=()) -> ElectricalNetwork:
    return ElectricalNetwork.model_validate({"nodes": list(nodes), "edges": list(edges), "groups": list(groups)})


def n(id, kind, **kw):
    return {"id": id, "tag": kw.pop("tag", id.upper()), "kind": kind, **kw}


def e(id, src, dst, service="lv_ac", **kw):
    return {"id": id, "src": src, "dst": dst, "service": service, **kw}


CHAIN = [n("pv", "pv_string"), n("inv", "inverter"), n("poi", "poi_grid")]
CHAIN_EDGES = [e("e1", "pv:out", "inv:dc", "dc"), e("e2", "inv:ac", "poi:in")]


def rejects(match: str, *args, **kw):
    with pytest.raises(ValidationError, match=match):
        net(*args, **kw)


def test_fixtures_validate_and_resolve_against_the_catalog(rooftop, utility, catalog):
    for p in (rooftop, utility):
        p.check_catalog(catalog)
        assert not p.network.unconnected_ports()
    assert rooftop.network.instance_count("inv") == 5
    assert rooftop.network.instance_count("str") == 50
    assert utility.network.instance_count("inv") == 12
    assert utility.network.instance_count("str") == 6 * 2 * 15 * 24


def test_fixture_capacities_follow_from_catalog_specs(rooftop, utility, catalog):
    def dc_kwp(p: Project) -> float:
        s = p.network.node("str")
        return p.network.instance_count("str") * s.modules_in_series * catalog.get(s.spec).pmax / 1000

    def ac_kva(p: Project) -> float:
        return p.network.instance_count("inv") * catalog.get(p.network.node("inv").spec).ac_rated_kva

    assert dc_kwp(rooftop) == pytest.approx(499.5)
    assert ac_kva(rooftop) == 500
    assert ac_kva(utility) == 52800
    assert dc_kwp(utility) / ac_kva(utility) == pytest.approx(1.28, abs=0.01)


def test_duplicate_id_is_named():
    rejects("duplicate id 'inv'", CHAIN + [n("inv", "inverter", tag="INV-2")], CHAIN_EDGES)


def test_edge_to_missing_node_is_named():
    rejects("edge 'e3' dst references unknown node 'ghost'", CHAIN, CHAIN_EDGES + [e("e3", "inv:ac", "ghost:in")])


def test_dangling_port_is_named():
    rejects(r"edge 'e1' dst: dangling port inv:in", CHAIN, [e("e1", "pv:out", "inv:in", "dc"), CHAIN_EDGES[1]])


def test_cycle_is_named_with_its_path():
    nodes = [n("a", "busbar"), n("b", "breaker"), n("c", "breaker")]
    edges = [e("ab", "a:bus", "b:in"), e("bc", "b:out", "c:in"), e("ca", "c:out", "a:bus")]
    rejects("power flow cycle: a -> b -> c -> a", nodes, edges)


def test_earth_and_control_edges_do_not_count_as_power_flow():
    nodes = CHAIN + [n("ep", "earth_electrode"), n("ct", "ct"), n("m", "meter", ports=["sig"])]
    edges = [e("e1", "pv:out", "inv:dc", "dc"), e("e2", "inv:ac", "ct:in"), e("e3", "ct:out", "poi:in"),
             e("pe", "inv:pe", "ep:pe", "earth"), e("sig", "ct:sec", "m:sig", "control")]
    g = net(nodes, edges)
    assert g.downstream("inv") == ("ct", "poi")
    rejects("earth connections use port 'pe'", nodes, edges + [e("pe2", "inv:ac", "ep:pe", "earth")])
    rejects("control port inv:ac", nodes, edges + [e("sig2", "inv:ac", "m:sig", "control")])


def test_unknown_catalog_id_is_named(catalog):
    nodes = [n("pv", "pv_string", spec="no-such-module"), *CHAIN[1:]]
    g = net(nodes, CHAIN_EDGES)
    with pytest.raises(NetworkError, match="node 'pv' references unknown catalog id 'no-such-module'"):
        g.check_catalog(catalog)
    with pytest.raises(ValidationError, match="unknown catalog id 'no-such-module'"):
        ElectricalNetwork.model_validate(g.model_dump(), context={"catalog": catalog})


def test_catalog_spec_must_fit_the_node_kind(catalog):
    g = net([n("pv", "pv_string"), n("inv", "inverter", spec="adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp"), CHAIN[2]], CHAIN_EDGES)
    assert g.catalog_problems(catalog) == ["node 'inv' (inverter) cannot use module spec 'adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp'"]
    g = net([*CHAIN, n("vcb", "vcb", spec="schneider-ic60n-c16-3p")], [CHAIN_EDGES[0], e("e2", "inv:ac", "vcb:in"), e("e3", "vcb:out", "poi:in")])
    assert g.catalog_problems(catalog) == ["node 'vcb' (vcb) cannot use mcb device 'schneider-ic60n-c16-3p'"]
    g = net(CHAIN, [{**CHAIN_EDGES[0], "cable": "huawei-sun2000-100ktl-m2"}, CHAIN_EDGES[1]])
    assert g.catalog_problems(catalog) == ["edge 'e1' cable 'huawei-sun2000-100ktl-m2' is a inverter, not a cable"]


def test_galvanically_joined_nodes_must_agree_on_voltage():
    nodes = [n("pv", "pv_string"), n("inv", "inverter"), n("b", "breaker", voltage_v=415), n("poi", "poi_grid", voltage_v=33000)]
    rejects(r"nodes \['b', 'poi'\] are galvanically connected but declare voltages \[415.0, 33000.0\]",
            nodes, [CHAIN_EDGES[0], e("e2", "inv:ac", "b:in"), e("e3", "b:out", "poi:in")])


GROUPED = dict(
    groups=[{"id": "blk", "tag": "B-{0}", "count": 3}, {"id": "sec", "tag": "S-{0}-{1}", "count": 2, "within": "blk"}],
    nodes=[n("pv", "pv_string", tag="PV-{0}-{1}-{2}", group="sec", count=4), n("inv", "inverter", tag="INV-{0}-{1}", group="sec"),
           n("tx", "transformer", tag="TX-{0}", group="blk"), n("poi", "poi_grid")],
    edges=[e("e1", "pv:out", "inv:dc", "dc"), e("e2", "inv:ac", "tx:lv"), e("e3", "tx:hv", "poi:in", "mv_ac")],
)


def test_group_expansion_wires_each_instance_to_its_own_parent():
    g = net(**GROUPED)
    x = g.expand()
    assert len(x.by_kind("pv_string")) == 24
    assert {n.tag for n in x.by_kind("transformer")} == {"TX-1", "TX-2", "TX-3"}
    assert x.node("pv@3.2.4").tag == "PV-3-2-4"
    assert x.path_to_poi("pv@3.2.4") == ("pv@3.2.4", "inv@3.2", "tx@3", "poi")
    assert len(x.upstream("tx@2")) == 2 + 8
    assert {e.id for e in x.edges_at("inv@1.1")} == {"e1@1.1.1", "e1@1.1.2", "e1@1.1.3", "e1@1.1.4", "e2@1.1"}
    assert g.instance_ids("tx") == ("tx@1", "tx@2", "tx@3")


def test_count_one_groups_are_enclosures_that_do_not_repeat():
    groups = GROUPED["groups"] + [{"id": "panel", "tag": "HT PANEL"}]
    nodes = GROUPED["nodes"][:3] + [n("bus", "busbar", group="panel"), n("poi", "poi_grid")]
    edges = GROUPED["edges"][:2] + [e("e3", "tx:hv", "bus:bus", "mv_ac"), e("e4", "bus:bus", "poi:in", "mv_ac")]
    x = net(nodes, edges, groups).expand()
    assert x.downstream("tx@1") == ("bus", "poi")


def test_group_rules_name_the_offender():
    bad_tag = copy.deepcopy(GROUPED)
    bad_tag["nodes"][1]["tag"] = "INV-{0}"
    rejects(r"node 'inv': tag 'INV-\{0\}' must use exactly the placeholders", **bad_tag)
    siblings = copy.deepcopy(GROUPED)
    siblings["groups"].append({"id": "other", "tag": "O-{0}", "count": 2})
    siblings["nodes"][2]["group"] = "other"
    siblings["nodes"][2]["tag"] = "TX-{0}"
    rejects("edge 'e2' joins 'inv' .* and 'tx' .* which repeat independently", **siblings)
    many_parents = copy.deepcopy(GROUPED)
    many_parents["nodes"][2]["count"] = 2
    many_parents["nodes"][2]["tag"] = "TX-{0}-{1}"
    rejects("'tx' sits in an enclosing group and must have count 1", **many_parents)


def test_queries(utility):
    g = utility.network
    assert {n.id for n in g.by_kind(NodeKind.VCB)} == {"icog_vcb", "aux_vcb", "out_vcb"}
    assert g.path_to_poi("scb") == ("scb", "inv", "idt", "icog_vcb", "mv_bus", "out_vcb", "out_ct", "poi")
    with pytest.raises(NetworkError, match="'aux_load' has no downstream path to a poi_grid"):
        g.path_to_poi("aux_load")
    assert g.upstream("scb") == ("str",)
    assert "poi" in g.downstream("str")
    block = g.subtree("idt")
    assert {n.id for n in block.nodes} == {"idt", "inv", "scb", "str"}
    assert {gr.id for gr in block.groups} == {"block", "inv_sec", "scb_sec"}
    zones = {z.voltage_v: z for z in g.voltage_zones() if z.voltage_v}
    assert {"mv_bus", "icog_vcb", "out_ct", "poi"} <= set(zones[33000.0].node_ids)


def test_symbols_come_from_node_spec_or_kind(utility, catalog):
    assert set(KIND_SYMBOLS.values()) <= REGISTRY.keys()
    g = utility.network
    assert g.node("inv").symbol_key(catalog) == "inv.central"
    assert g.node("aux_tr").symbol_key(catalog) == "tx.power_2w"
    assert g.node("icog_vcb").symbol_key(catalog) == "sw.vcb"
    assert g.node("mv_bus").symbol_key(catalog) is None


def test_layout_rejects_double_booked_slots_and_unknown_strings(catalog):
    data = raw("rooftop_500kwp")
    data["array_layout"]["strings"][1]["spans"] = data["array_layout"]["strings"][0]["spans"]
    with pytest.raises(ValidationError, match=r"slot \(0,0\) of table 'T01' is assigned to both 'str@1.1' and 'str@1.2'"):
        Project.model_validate(data)
    data = raw("rooftop_500kwp")
    data["array_layout"]["strings"][0]["string"] = "str@9.1"
    with pytest.raises(ValidationError, match="layout string 'str@9.1' is not an instance of 'str'"):
        Project.model_validate(data)
    data = raw("rooftop_500kwp")
    data["array_layout"]["strings"][0]["spans"][0]["n"] = 19
    with pytest.raises(ValidationError, match=r"slot \(0,18\) is outside table 'T01'"):
        Project.model_validate(data)


def test_layout_strings_cover_every_string_instance(rooftop):
    layout = rooftop.array_layout
    assert {a.string for a in layout.strings} == set(rooftop.network.instance_ids("str"))
    assert all(len(a.slots()) == 18 for a in layout.strings)


def test_sheet_scope_must_reference_the_network():
    data = raw("utility_50mwac")
    data["sheets"][1]["scope"]["nodes"] = ["idt-99"]
    with pytest.raises(ValidationError, match="sheet 'sld_blk' scope references unknown node or group 'idt-99'"):
        Project.model_validate(data)


def test_models_are_frozen(rooftop):
    with pytest.raises(ValidationError):
        rooftop.name = "x"
    with pytest.raises(ValidationError, match="Extra inputs"):
        Project.model_validate({**raw("rooftop_500kwp"), "colour": "red"})


# ---------------- inline transformer specs ----------------


def inline_idt(data: dict) -> dict:
    return next(n for n in data["network"]["nodes"] if n["id"] == "idt")["spec"]


def test_committed_fixtures_are_what_the_generator_writes(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "tools" / "build_project_fixtures.py"), str(tmp_path)], check=True, capture_output=True)
    for name in ("rooftop_500kwp", "utility_50mwac"):
        assert (tmp_path / f"{name}.json").read_bytes() == (FIXTURES / f"{name}.json").read_bytes(), f"{name} is stale: rerun tools/build_project_fixtures.py"


def test_utility_idt_is_an_inline_spec_matched_to_its_inverter(utility, catalog):
    spec = utility.network.node("idt").spec
    inv = catalog.get(utility.network.node("inv").spec)
    assert spec.kind == "transformer" and spec.id not in {e.id for e in catalog.entries}
    assert spec.lv_kv == (inv.ac_voltage / 1000,) * 2
    assert spec.winding_kva == (4400, 4400) and sum(spec.winding_kva) == spec.rated_kva
    assert (spec.vector_group, spec.impedance_pct, spec.impedance_base_kva, spec.lv_lv_impedance_min_pct) == ("YNd11d11", 7.0, 4400, 11.0)
    assert (spec.tap_changer, spec.tap_range_pct, spec.tap_step_pct) == ("octc", (-10.0, 10.0), 2.5)
    assert spec.source.kind == "manual" and "PS 439-1304" in spec.source.ref
    assert utility.network.node("idt").symbol_key(catalog) == "tx.power_3w"
    assumptions = " ".join(utility.design_basis.assumptions)
    assert spec.id in assumptions and "PS 439-1304" in assumptions and "%Z 7 %" in assumptions


def test_inline_spec_survives_a_json_round_trip(utility, catalog):
    from unicad.store import canonical_json, parse_project

    again = parse_project(canonical_json(utility), catalog=catalog)
    assert again.network.node("idt").spec == utility.network.node("idt").spec


def test_inline_spec_is_only_valid_on_transformer_nodes(catalog):
    data = raw("utility_50mwac")
    spec = inline_idt(data)
    next(x for x in data["network"]["nodes"] if x["id"] == "mv_bus")["spec"] = spec
    with pytest.raises(ValidationError, match="inline spec is only valid on transformer nodes"):
        Project.model_validate(data, context={"catalog": catalog})


def test_inline_spec_id_must_not_shadow_a_catalog_id(catalog):
    data = raw("utility_50mwac")
    inline_idt(data)["id"] = "is1180-33kv-100kva-dyn11"
    with pytest.raises(ValidationError, match="also a catalog id"):
        Project.model_validate(data, context={"catalog": catalog})


def test_inline_spec_is_validated_like_a_catalog_spec(catalog):
    data = raw("utility_50mwac")
    inline_idt(data)["winding_kva"] = [4400, 5000]
    with pytest.raises(ValidationError, match="winding_kva sums to more than rated_kva"):
        Project.model_validate(data, context={"catalog": catalog})
    data = raw("utility_50mwac")
    inline_idt(data)["lv_kv"] = [0.66]
    with pytest.raises(ValidationError, match="one rating per lv_kv winding"):
        Project.model_validate(data, context={"catalog": catalog})


def test_rules_resolve_the_inline_spec(catalog):
    from unicad.rules import explain, run_rules

    clean = run_rules(Project.model_validate(raw("utility_50mwac"), context={"catalog": catalog}), catalog)
    assert not [f for f in clean.by_rule("structure.transformer_voltage") if f.subjects == ("idt",)]
    assert not clean.by_rule("structure.missing_spec") and clean.counts["error"] == 0

    data = raw("utility_50mwac")
    inline_idt(data)["lv_kv"] = [0.8, 0.8]
    report = run_rules(Project.model_validate(data, context={"catalog": catalog}), catalog)
    (f,) = [f for f in report.by_rule("structure.transformer_voltage") if f.subjects == ("idt",)]
    spec_id = inline_idt(data)["id"]
    assert f.severity.value == "error" and "LV winding" in f.message and "800" in f.message
    assert any(q.source == spec_id for q in f.inputs)
    assert explain(f, catalog).sources[spec_id] == spec_id

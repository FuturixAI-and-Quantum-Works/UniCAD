"""RVUNL Heerapura modelling gaps (PLAN 11.2, bench run 1): mixed string lengths and uneven modules per inverter (RV-34), the LT ground-mount template
(RV-S7), breaker long-time settings (RV-A6), typed node ratings (RV-M5) and the layout shortfall rule (RV-M6). The brief: 1950 modules on 8 inverters,
inverters 1-4 with 252 modules, 5-7 with 234, inverter 8 with 240 (18 x 1 and 19 x 1 per MPPT), 7 MPPTs, 2 strings per MPPT, an ACCB into the client's
415 V LT panel with CT, PT, numerical relay 50/51/67/67N/27/59/50N/51N (IEC 61850), meter and an RS-485 data logger."""

from collections import Counter
from pathlib import Path

import pytest

from unicad.catalog.store import load_catalog
from unicad.design import DesignError, autobuild
from unicad.design.strings import InverterStrings, check_inverter_strings
from unicad.domain.layout import ArrayLayout, LayoutBlock, Mounting, Orientation, Point, Table
from unicad.domain.network import ElectricalNetwork, Laying, Node, NodeKind
from unicad.domain.project import FaultLevel, Project, Segment
from unicad.rules import Severity, run_rules
from unicad.rules.topology import string_feeds
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog()
MODULE = "waaree-bin-21-615"
BENCH_MODULE = "waaree-bin-21-635"
INVERTER = "huawei-sun2000-100ktl-m2"
ACB_2500 = "schneider-mtz2-h1-2500a-4p"
AL_300 = "polycab-lt-xlpe-al-3.5c-300-arm"
RELAY = {"functions": ["50", "51", "67", "67N", "27", "59", "50N", "51N"], "protocol": "IEC 61850"}
BRIEF = [
    {"count": 4, "mppts": [[18, 18]] * 7},
    {"count": 3, "mppts": [[18, 18]] * 6 + [[18]]},
    {"count": 1, "mppts": [[18, 19]] * 6 + [[18]]},
]


def heerapura(**basis) -> Project:
    """The bench run's design basis (assumption A3: ambient 2 / 47 degC, 31.5 kA for 3 s at 415 V) on an LT utility project with no network."""
    p = parse_project((ROOT / "fixtures" / "projects" / "rooftop_500kwp.json").read_text(encoding="utf-8"), catalog=CATALOG)
    b = p.design_basis.model_copy(update={"ambient_min_c": 2.0, "ambient_max_c": 47.0, "dc_ac_ratio_target": 1.17,
                                          "fault_levels": (FaultLevel(voltage_v=415, fault_ka=31.5, clearing_s=3, source="brief busbar rating"),), **basis})  # fmt: skip
    return p.model_copy(update={"id": "rvunl-heerapura", "network": ElectricalNetwork(), "array_layout": None, "sheets": (), "segment": Segment.UTILITY, "design_basis": b})


@pytest.fixture(scope="module")
def built():
    return autobuild(heerapura(), CATALOG, {"module": MODULE, "inverter": INVERTER, "inverter_strings": BRIEF, "relay": RELAY, "data_logger": True})


def strings_of(net: ElectricalNetwork, inverter_tag: str) -> Counter:
    exp = net.expand()
    inv = next(n.id for n in exp.nodes if n.tag == inverter_tag)
    return Counter(exp.node(s).modules_in_series for s in exp.upstream(inv) if exp.node(s).kind is NodeKind.PV_STRING)


def test_brief_strings_are_built_per_inverter_with_their_lengths(built):
    net = built.project.network
    assert built.template == "utility_lt"
    assert built.summary["inverters"] == 8 and built.summary["string_lengths"] == {18: 102, 19: 6}
    assert built.summary["dc_kwp"] == pytest.approx(1950 * 0.615)
    assert strings_of(net, "INV-01") == {18: 14} and strings_of(net, "INV-04") == {18: 14}
    assert strings_of(net, "INV-05") == {18: 13} and strings_of(net, "INV-07") == {18: 13}
    assert strings_of(net, "INV-08") == {18: 7, 19: 6}
    s19 = next(n for n in net.nodes if n.modules_in_series == 19)
    assert s19.mppt == (1, 2, 3, 4, 5, 6) and s19.tag.startswith("STR19-08")
    s18 = next(n for n in net.nodes if n.modules_in_series == 18 and n.group == s19.group)
    assert s18.mppt == (1, 2, 3, 4, 5, 6, 7)


def test_every_length_is_checked_for_voc_at_the_coldest_cell(built):
    g = built.given_strings
    by = {x.modules_per_string: x for x in g.lengths}
    assert set(by) == {18, 19} and all(x.fits for x in by.values())
    assert by[19].voltages.voc_cold_v > by[18].voltages.voc_cold_v and by[19].margins["voc_max_v"] >= 0
    report = run_rules(built.project, CATALOG, ["pv_strings"])
    assert not [f for f in report.findings if f.severity is Severity.ERROR]
    feeds = [f for f in string_feeds(built.project, CATALOG) if f.inverter.id == "invx08"]
    assert {f.n_series for f in feeds} == {18, 19}
    assert all(f.mppt_inputs == (2, 2, 2, 2, 2, 2, 1) and f.inputs_per_mppt == 2 for f in feeds)


def test_a_length_over_the_voltage_limit_is_named():
    p = heerapura()
    module, inverter = CATALOG.get(MODULE), CATALOG.get(INVERTER)
    too_long = check_inverter_strings(module, inverter, p.design_basis, [InverterStrings(count=1, mppts=((18, 22),))])
    assert not too_long.ok and any("22 modules" in x for x in too_long.problems)
    with pytest.raises(DesignError, match="22 modules"):
        autobuild(p, CATALOG, {"module": MODULE, "inverter": INVERTER, "inverter_strings": [{"count": 1, "mppts": [[18, 22]]}]})
    wide = check_inverter_strings(module, inverter, p.design_basis, [InverterStrings(count=1, mppts=((18, 18, 18),))])
    assert any("3 strings on one MPPT" in x for x in wide.problems)


def test_bench_utility_string_call_on_an_lt_grid_names_the_lt_template():
    """log.jsonl call 37: network_autobuild(template=utility_string, grid_voltage_v=415) failed with 'no catalog 0.415 kV transformer is rated >= 110 kVA'."""
    intent = {"template": "utility_string", "dc_kwp": 1371.6, "module": BENCH_MODULE, "inverter": INVERTER, "modules_per_string": 18, "strings_per_inverter": 10, "grid_voltage_v": 415}
    with pytest.raises(DesignError, match="utility_lt"):
        autobuild(heerapura(), CATALOG, intent)


def test_lt_template_feeds_the_client_panel_without_transformer_or_net_meter(built):
    net = built.project.network
    kinds = Counter(n.kind for n in net.nodes)
    assert NodeKind.TRANSFORMER not in kinds and not any("NET METER" in (n.title or "") for n in net.nodes)
    path = [net.node(n).kind.value for n in net.path_to_poi("invx08")]
    assert path == ["inverter", "breaker", "acdb", "breaker", "ct", "poi_grid"]
    relay = next(n for n in net.nodes if n.kind is NodeKind.RELAY)
    assert relay.relay.functions == tuple(RELAY["functions"]) and relay.relay.protocol == "IEC 61850"
    assert kinds[NodeKind.PT] == 1
    links = [e for e in net.expand().edges if e.dst_node == "dlog" and e.service.value == "control"]
    assert len(links) == 9, "one RS-485 link per inverter and one from the meter"
    errors = [f"{f.rule_id}: {f.message}" for f in built.errors]
    assert errors == []


def with_outgoing(built, ir: float | None, runs: int = 7) -> Project:
    """The brief's outgoing: 2500 A ACB and 7R x 3.5C x 300 sq.mm Al to the client's panel, 60 m in tray (bench assumption A6)."""
    net = built.project.network
    nodes = [n.model_copy(update={"spec": ACB_2500, "long_time_setting_a": ir}) if n.id == "acdb_brk" else n for n in net.nodes]
    edges = [e.model_copy(update={"cable": AL_300, "runs": runs, "length_m": 60.0, "laying": Laying.TRAY, "bands": ()}) if e.id == "e_ct_poi" else e for e in net.edges]
    data = built.project.model_dump(mode="json")
    data["network"] = ElectricalNetwork(groups=net.groups, nodes=tuple(nodes), edges=tuple(edges)).model_dump(mode="json")
    return Project.model_validate(data, context={"catalog": CATALOG})


def errors_of(p: Project, *rules: str) -> list[str]:
    return [f.message for f in run_rules(p, CATALOG).findings if f.severity is Severity.ERROR and f.rule_id in rules]


def test_ir_below_the_frame_lets_the_brief_cable_pass_on_its_merits(built):
    frame = with_outgoing(built, None)
    assert errors_of(frame, "cables.protection"), "In 2500 A exceeds 7R x 300 sq.mm Al"
    set_low = with_outgoing(built, 2000.0)
    assert errors_of(set_low, "cables.protection", "protection.rated_current", "protection.trip_setting") == []
    set_high = with_outgoing(built, 2400.0)
    msgs = errors_of(set_high, "cables.protection")
    assert msgs and all("Ir 2400 A (frame 2500 A)" in m for m in msgs)
    below_range = with_outgoing(built, 900.0)
    msgs = errors_of(below_range, "protection.trip_setting", "protection.rated_current")
    assert any("outside the trip unit's setting range 1000 A .. 2500 A" in m for m in msgs)
    assert any("set to Ir 900 A" in m for m in msgs)


def test_ratings_are_typed_and_kept_to_their_kinds():
    ct = Node(id="ct", tag="CT-01", kind="ct", ct={"primary_a": 2500, "secondary_a": 1, "cores": [{"accuracy_class": "0.2S", "burden_va": 15}, {"accuracy_class": "PS"}]})
    assert ct.ct.ratio == "2500/1 A" and ct.ct.cores[1].accuracy_class == "PS"
    pt = Node(id="pt", tag="PT-01", kind="pt", pt={"primary_v": 415, "secondary_v": 110, "phase_to_neutral": True})
    assert pt.pt.ratio == "0.415 kV/V3 / 110 V/V3"
    bus = Node(id="bus", tag="ACCB-01", kind="acdb", busbar={"rating_a": 2500, "material": "al", "phases": "3P+N", "fault_ka": 31.5, "fault_s": 3})
    assert bus.busbar.fault_s == 3
    spd = Node(id="spd", tag="SPD-01", kind="ac_spd", spd={"spd_type": "2", "imax_ka": 40, "in_ka": 20})
    assert spd.spd.imax_ka == 40
    with pytest.raises(ValueError, match="ct is only valid on ct"):
        Node(id="b", tag="B", kind="breaker", ct={"primary_a": 100, "secondary_a": 5})
    with pytest.raises(ValueError, match="long_time_setting_a is only valid on breaker"):
        Node(id="m", tag="M", kind="meter", long_time_setting_a=100)
    with pytest.raises(ValueError):
        Node(id="r", tag="R", kind="relay", relay={"functions": ["overcurrent"]})
    with pytest.raises(ValueError, match="mppt lists 2 MPPT numbers for a count of 3"):
        Node(id="s", tag="S-{0:02d}", kind="pv_string", count=3, mppt=[1, 2])


def layout_of(tables: int, target: float) -> ArrayLayout:
    """`tables` 2 x 18 portrait tables of 635 Wp (22.86 kWp each), as the bench run placed them."""
    ts = tuple(
        Table(id=f"T{i:03d}", mounting=Mounting.FIXED_TILT, origin=Point(x=0.0, y=8.0 * i), azimuth_deg=180, tilt_deg=26, rows=2, cols=18,
              orientation=Orientation.PORTRAIT, module=BENCH_MODULE, slot_width_m=1.15, slot_height_m=2.4)
        for i in range(tables)
    )  # fmt: skip
    return ArrayLayout(blocks=(LayoutBlock(id="B01", name="Block-01", tables=ts),), target_dc_kwp=target)


def test_layout_shortfall_beyond_the_tolerance_is_an_error():
    """Bench run: a 150 x 100 m plot held 800 kWp (35 tables) of the 1371.6 kWp target and validate still gave 0 errors."""
    p = heerapura()
    short = p.model_copy(update={"array_layout": layout_of(35, 1371.6)})
    found = [f for f in run_rules(short, CATALOG, ["layout"]).findings if f.rule_id == "layout.shortfall"]
    assert len(found) == 1 and found[0].severity is Severity.ERROR and "800.1 kWp" in found[0].message
    full = p.model_copy(update={"array_layout": layout_of(60, 1371.6)})
    assert run_rules(full, CATALOG, ["layout"]).findings == ()
    one_table_short = p.model_copy(update={"array_layout": layout_of(59, 1371.6)})
    assert run_rules(one_table_short, CATALOG, ["layout"]).findings == ()

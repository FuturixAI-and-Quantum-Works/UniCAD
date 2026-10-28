import json
from pathlib import Path

import pytest

from unicad.catalog.store import Catalog, load_catalog
from unicad.domain.project import IssuedFor, Project
from unicad.rules import REGISTRY, Severity, explain, families, issue_gate, run_rules
from unicad.rules.docs import DOCS_PATH, render_docs
from unicad.store import parse_project

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "projects"
CATALOG = load_catalog()
ROOFTOP_MCCB_SMALL = "lk-dsine-dn1-250-c-160a-4p"
ROOFTOP_ACB_SMALL = "schneider-mtz2-n1-800a-4p"


def raw(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def fixture(name: str) -> Project:
    return parse_project((FIXTURES / f"{name}.json").read_text(encoding="utf-8"), catalog=CATALOG)


def variant(name: str, edit, catalog: Catalog = CATALOG) -> Project:
    data = raw(name)
    edit(data)
    return Project.model_validate(data, context={"catalog": catalog})


def node(data: dict, node_id: str) -> dict:
    return next(n for n in data["network"]["nodes"] if n["id"] == node_id)


def drop_edges(data: dict, *ids: str) -> None:
    data["network"]["edges"] = [e for e in data["network"]["edges"] if e["id"] not in ids]


def drop_layout(data: dict) -> None:
    data.pop("array_layout", None)
    for s in data.get("sheets", []):
        s.get("scope", {}).pop("nodes", None)


def series(n: int):
    def edit(data):
        node(data, "str")["modules_in_series"] = n

    return edit


def rooftop_broken(data: dict) -> None:
    """The rooftop as first drafted: breakers sized for inverter rated current without the continuous-duty factor."""
    node(data, "inv_mccb")["spec"] = ROOFTOP_MCCB_SMALL
    node(data, "acdb_acb")["spec"] = ROOFTOP_ACB_SMALL


def edge(data: dict, edge_id: str) -> dict:
    return next(e for e in data["network"]["edges"] if e["id"] == edge_id)


def summary(report) -> set[tuple[str, str, tuple[str, ...]]]:
    return {(f.rule_id, f.severity.value, f.subjects) for f in report.findings}


def with_specs(*specs) -> Catalog:
    return Catalog(CATALOG.entries + tuple(specs))


def test_rooftop_fixture_is_a_valid_design():
    report = run_rules(fixture("rooftop_500kwp"), CATALOG)
    print("\n" + report.render())
    assert {(r, sev, subj) for r, sev, subj in summary(report) if sev != "info"} == {
        ("cables.missing", "warning", ("e_ct_poi",)),
        ("cables.missing", "warning", ("e_ltp_load",)),
    }
    assert report.counts["error"] == 0 and not report.blocking
    gate = issue_gate(report, IssuedFor.APPROVAL)
    assert gate.allowed and not gate.watermark and gate.reasons == ()
    assert {f.rule_id for f in report.findings if f.severity is Severity.INFO} == {
        "cables.ampacity", "cables.short_circuit", "protection.fault_level", "structure.inverter_ac_voltage", "vdrop.ac", "vdrop.dc",
    }  # fmt: skip


def test_rooftop_broken_breakers_block_approval():
    report = run_rules(variant("rooftop_500kwp", rooftop_broken), CATALOG)
    print("\n" + report.render())
    errors = {f.subjects: (f.measured, f.limit) for f in report.findings if f.severity is Severity.ERROR}
    assert errors == {("inv_mccb",): (160, pytest.approx(191.29, abs=0.01)), ("acdb_acb",): (800, pytest.approx(956.45, abs=0.01))}
    gate = issue_gate(report, IssuedFor.APPROVAL)
    assert not gate.allowed and len(gate.reasons) == 2


def test_utility_fixture_report():
    report = run_rules(fixture("utility_50mwac"), CATALOG)
    print("\n" + report.render())
    assert {(r, sev, subj) for r, sev, subj in summary(report) if sev != "info"} == {
        ("pv_strings.operating_current", "warning", ("str", "inv")),
        ("cables.ampacity", "warning", ("e_scb_inv",)),
        ("cables.missing", "warning", ("e_ct_poi",)),
    }
    # 8 x 3 x 1C Al in a trench, KEI single core table B 0.81: 400 sq.mm gives 8 x 589 x 0.81 = 3816.7 A < Ib 3849 A, so the fixture builder sizes up to the next size
    (idt,) = [f for f in report.by_rule("cables.ampacity") if f.subjects == ("e_inv_idt",)]
    assert idt.severity is Severity.INFO and idt.measured <= idt.limit and "1C x 500 sq.mm" in idt.message and report.counts["error"] == 0
    assert ("pv_strings.string_fuses", "info", ("str", "inv", "scb")) in summary(report)


def test_findings_are_sorted_errors_first():
    report = run_rules(fixture("utility_50mwac"), CATALOG)
    order = {"error": 0, "warning": 1, "info": 2}
    keys = [(order[f.severity.value], f.rule_id, f.subjects) for f in report.findings]
    assert keys == sorted(keys)


def test_too_many_modules_in_series_names_string_inverter_and_voltages():
    p = variant("rooftop_500kwp", series(21))
    report = run_rules(p, CATALOG)
    print("\n" + report.render())
    (f,) = report.by_rule("pv_strings.voc_max")
    # Voc at Tmin = N x Voc x (1 + beta/100 x (Tmin - 25)) = 21 x 51.5 x (1 - 0.0024 x (10 - 25)) = 21 x 53.354
    expected = 21 * 51.5 * (1 + (-0.24 / 100) * (10 - 25))
    assert expected == pytest.approx(1120.434)
    assert f.severity is Severity.ERROR
    assert f.subjects == ("str", "inv")
    assert f.measured == pytest.approx(expected, abs=0.01)
    assert f.limit == 1100.0 and f.unit == "V"
    for text in ("STR-01-01", "INV-01", "SUN2000-100KTL-M2", "1120.4 V", "1100 V"):
        assert text in f.message
    assert f.clause == "IEC 62548"


def test_issue_gate_blocks_approval_and_construction_on_errors_but_allows_proposal():
    report = run_rules(variant("rooftop_500kwp", series(21)), CATALOG)
    for purpose in (IssuedFor.APPROVAL, IssuedFor.CONSTRUCTION, IssuedFor.AS_BUILT):
        gate = issue_gate(report, purpose)
        assert not gate.allowed
        assert any(r.startswith("pv_strings.voc_max [str, inv]") for r in gate.reasons)
    gate = issue_gate(report, "proposal")
    assert gate.allowed and gate.watermark
    assert issue_gate(report, IssuedFor.INFORMATION).allowed


def test_family_filter_runs_only_that_family():
    report = run_rules(variant("rooftop_500kwp", rooftop_broken), CATALOG, families=["protection"])
    assert report.rules and all(r.startswith("protection.") for r in report.rules)
    assert {f.rule_id.split(".")[0] for f in report.findings} == {"protection"}
    with pytest.raises(ValueError, match="unknown rule families"):
        run_rules(fixture("rooftop_500kwp"), CATALOG, families=["nope"])


def test_run_rules_needs_a_project_that_resolves_against_the_catalog():
    data = raw("rooftop_500kwp")
    node(data, "inv")["spec"] = "no-such-inverter"
    p = Project.model_validate(data)
    with pytest.raises(ValueError, match="no-such-inverter"):
        run_rules(p, CATALOG)


def test_registry_shape():
    assert families() == ("structure", "pv_strings", "ratio", "protection", "cables", "vdrop", "earthing", "lps")
    for r in REGISTRY.values():
        assert r.id.startswith(r.family + ".")
        assert r.title and r.docs, r.id


def test_rules_docs_are_current():
    assert DOCS_PATH.read_text(encoding="utf-8") == render_docs(), "docs/rules.md is stale: uv run python -m unicad.rules.docs > docs/rules.md"


def test_explain_gives_formula_inputs_and_catalog_sources():
    report = run_rules(variant("rooftop_500kwp", series(21)), CATALOG)
    (f,) = report.by_rule("pv_strings.voc_max")
    ex = explain(f, CATALOG)
    assert ex.title == REGISTRY["pv_strings.voc_max"].title and ex.docs
    assert "N x Voc x (1 + beta_Voc / 100 x (Tcell - 25))" in ex.formula
    values = {q.name: q.value for q in ex.inputs}
    assert values["N"] == 21 and values["Voc"] == 51.5 and values["beta_Voc"] == -0.24 and values["Tamb_min"] == 10.0
    assert values["dc_max_voltage"] == 1100.0
    assert "Adani_ElanShine_TOPCon_G2G_GenII.pdf" in ex.sources["adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp"]
    assert "Huawei_SUN2000-100KTL-M2_DS.pdf" in ex.sources["huawei-sun2000-100ktl-m2"]
    assert ex.sources["design_basis.ambient_min_c"] == "design_basis.ambient_min_c"
    assert any("ambient_min_c" in a for a in ex.assumptions)


def test_report_serialises_counts_and_blocking():
    dumped = run_rules(variant("rooftop_500kwp", rooftop_broken), CATALOG).model_dump(mode="json")
    assert dumped["blocking"] is True
    assert dumped["counts"]["error"] == 2
    assert dumped["findings"][0]["severity"] == "error"


def test_unverified_thresholds_are_flagged_as_assumptions():
    report = run_rules(variant("rooftop_500kwp", rooftop_broken), CATALOG)
    assert report.by_rule("protection.rated_current")
    for f in report.by_rule("protection.rated_current"):
        assert any("1.25 x maximum inverter current" in a for a in f.assumptions)
    clauses = {r.clause for r in REGISTRY.values()} - {None}
    assert clauses == {"IEC 62548", "IS 732", "IS 3043", "IS 732 / IEC 60364-4-43", "IEEE 80", "IS/IEC 62305; NF C 17-102 for ESE"}

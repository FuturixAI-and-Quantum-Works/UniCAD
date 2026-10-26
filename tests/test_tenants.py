"""Profile v2: loading v1 files, validation, thresholds that change rule outcomes, numbering and storage."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from test_rules import CATALOG, fixture, variant

from unicad.drafting.sheets.model import CompanyProfile, Numbering, PaperSize, TemplateRef, TitleBlockChoice
from unicad.rules import explain, run_rules
from unicad.rules.thresholds import BY_NAME, DC_AC_BAND, ISC_FACTOR, Setting, using
from unicad.sets.numbering import Numberer
from unicad.tenants import find_profile, list_profiles, load_profile, save_profile
from unicad.tenants.store import check_id, thresholds_of, to_json

ROOT = Path(__file__).resolve().parents[1]
BASE = {"name": "X Co", "short_code": "XCO"}


def test_v1_profile_still_loads_unchanged():
    p = CompanyProfile.load(ROOT / "profiles" / "default.json")
    assert p.version == 1 and p.name == "UniCAD Demo EPC"
    assert p.title_block.kind == "builtin" and not p.thresholds and not p.symbol_overrides and not p.layer_map
    assert p.drawing_number(client="CIL", project="202109", discipline="electrical", seq=4) == "UDEMO-CIL-202109-EE-004"
    assert p.logo is not None and p.logo.is_file()


def test_v1_file_cannot_carry_v2_blocks():
    with pytest.raises(ValidationError, match="need profile version 2"):
        CompanyProfile.model_validate({**BASE, "version": 1, "thresholds": {}})
    assert CompanyProfile.model_validate({**BASE, "thresholds": {"dc_ac_band": {"value": 0.2, "source": "DS"}}}).version == 2


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"thresholds": {"nonsense": {"value": 1, "source": "x"}}}, "unknown thresholds"),
        ({"thresholds": {"dc_ac_band": {"value": 3, "source": "x"}}}, "outside the accepted range"),
        ({"thresholds": {"isc_factor": {"value": 0.5, "source": "x"}}}, "outside the accepted range"),
        ({"thresholds": {"isc_factor": {"value": 1.25, "source": ""}}}, "at least 1 character"),
        ({"title_block": {"kind": "imported"}}, "at least one paper size"),
        ({"numbering": {"start": {"civil": 0}}}, "at least 1"),
        ({"layer_map": {"G-SYMBOL": {"name": "BAD:NAME"}}}, "DXF forbids"),
        ({"symbol_overrides": {"pv.module": {"dxf": "x.dxf"}}}, "block"),
    ],
)
def test_v2_blocks_are_validated(patch, message):
    with pytest.raises(ValidationError, match=message):
        CompanyProfile.model_validate({**BASE, "version": 2, **patch})


def test_every_rule_threshold_can_be_set_with_a_source():
    all_set = {name: {"value": t.default_value, "source": f"company value for {name}"} for name, t in BY_NAME.items()}
    p = CompanyProfile.model_validate({**BASE, "thresholds": all_set})
    assert set(p.threshold_settings()) == set(BY_NAME) == {t.name for t in BY_NAME.values()}


def test_thresholds_read_defaults_and_restore_them():
    assert DC_AC_BAND.value == 0.10 and "illustrative band" in DC_AC_BAND.assumption
    with using({"dc_ac_band": Setting(0.2, "Acme DS-04")}):
        assert DC_AC_BAND.value == 0.2 and DC_AC_BAND.assumption.startswith("Company standard: Acme DS-04")
        assert ISC_FACTOR.value == 1.25
    assert DC_AC_BAND.value == 0.10
    with pytest.raises(ValueError, match="outside the accepted range"), using({"dc_ac_band": Setting(5, "x")}):
        pass


def _low_ratio_project():
    def edit(data):
        data["design_basis"]["dc_ac_ratio_target"] = 1.3

    return variant("rooftop_500kwp", edit)


def test_changing_a_profile_threshold_changes_the_rules_outcome():
    p = _low_ratio_project()
    default = run_rules(p, CATALOG)
    assert len(default.by_rule("ratio.dc_ac_plant")) == 1 and len(default.by_rule("ratio.dc_ac_inverter")) == 1

    wide = CompanyProfile.model_validate({**BASE, "thresholds": {"dc_ac_band": {"value": 0.25, "source": "Acme DS-04 clause 3.2"}}})
    relaxed = run_rules(p, CATALOG, thresholds=wide.threshold_settings())
    assert not relaxed.by_rule("ratio.dc_ac_plant") and not relaxed.by_rule("ratio.dc_ac_inverter")

    tight = CompanyProfile.model_validate({**BASE, "thresholds": {"dc_ac_band": {"value": 0.02, "source": "Acme DS-04 clause 3.2"}}})
    (f,) = run_rules(p, CATALOG, thresholds=tight.threshold_settings()).by_rule("ratio.dc_ac_plant")
    assert f.limit == pytest.approx(1.3 * 0.98)
    assert DC_AC_BAND.value == 0.10


def test_findings_cite_the_company_source_instead_of_the_generic_assumption():
    p = _low_ratio_project()
    (generic,) = run_rules(p, CATALOG).by_rule("ratio.dc_ac_plant")
    assert any("illustrative band" in a for a in generic.assumptions)

    mine = {"dc_ac_band": Setting(0.02, "Acme DS-04 clause 3.2: +/-2 % of target")}
    (f,) = run_rules(p, CATALOG, thresholds=mine).by_rule("ratio.dc_ac_plant")
    assert any("Acme DS-04 clause 3.2: +/-2 % of target" in a for a in f.assumptions)
    assert not any("illustrative band" in a for a in f.assumptions)
    assert explain(f).assumptions == f.assumptions


def test_a_company_isc_factor_reaches_string_and_protection_rules():
    base = run_rules(fixture("rooftop_500kwp"), CATALOG)
    odd = run_rules(fixture("rooftop_500kwp"), CATALOG, thresholds={"isc_factor": Setting(2.0, "Acme DS-04 clause 4.1")})
    key = lambda r: [(f.rule_id, f.message) for f in r.findings if "x Isc" in f.message or "k_Isc" in str(f.inputs)]  # noqa: E731
    assert key(base) != key(odd)
    assert any(q.name == "k_Isc" and q.value == 2.0 for f in odd.findings for q in f.inputs)


def test_clearing_time_default_is_company_settable():
    from test_rules_p5 import level, with_levels

    p = variant("utility_50mwac", with_levels(level(25, clearing_s=None)))
    f1 = next(f for f in run_rules(p, CATALOG).findings if f.rule_id == "cables.short_circuit" and f.subjects)
    f2 = next(f for f in run_rules(p, CATALOG, thresholds={"fault_clearing_default": Setting(0.5, "Acme protection study PS-9")}).findings if f.rule_id == "cables.short_circuit" and f.subjects)
    assert any("Acme protection study PS-9" in a for a in f2.assumptions) and not any("Acme" in a for a in f1.assumptions)


def test_unknown_threshold_is_refused_by_the_runner():
    with pytest.raises(ValueError, match="unknown thresholds"):
        run_rules(fixture("rooftop_500kwp"), CATALOG, thresholds={"nope": Setting(1, "x")})


def test_drawing_numbers_count_per_discipline_when_asked():
    shared = CompanyProfile.model_validate({**BASE, "discipline_codes": {"electrical": "EE", "civil": "CE"}})
    assert shared.next_number([], client="C", project="P", discipline="electrical").endswith("EE-001")
    assert shared.next_number(["XCO-C-P-EE-001", "XCO-C-P-CE-002"], client="C", project="P", discipline="civil").endswith("CE-003")

    own = CompanyProfile.model_validate({**BASE, "discipline_codes": {"electrical": "EE", "civil": "CE"}, "numbering": {"per_discipline": True, "start": {"civil": 101}}})
    issued = ["XCO-C-P-EE-001", "XCO-C-P-EE-002"]
    assert own.next_number(issued, client="C", project="P", discipline="civil") == "XCO-C-P-CE-101"
    assert own.next_number(issued, client="C", project="P", discipline="electrical") == "XCO-C-P-EE-003"
    assert own.next_number([*issued, "XCO-C-P-CE-101"], client="C", project="P", discipline="civil") == "XCO-C-P-CE-102"


def test_numberer_hands_out_per_discipline_sequences():
    p = CompanyProfile.model_validate({**BASE, "discipline_codes": {"electrical": "EE", "civil": "CE"}, "numbering": {"per_discipline": True, "start": {"civil": 101}}})
    n = Numberer.for_project(fixture("rooftop_500kwp").model_copy(update={"sheets": ()}), p)
    got = [n.fresh(), n.fresh(), n.fresh("civil"), n.fresh()]
    assert [g.rsplit("-", 2)[1:] for g in got] == [["EE", "001"], ["EE", "002"], ["CE", "101"], ["EE", "003"]]


def test_save_and_load_round_trip_in_a_tenant_workspace(tmp_path):
    ws = tmp_path / "tenant-a"
    v1 = load_profile("default", ws)
    assert v1.version == 1
    edited = v1.model_copy(update={"version": 2, "thresholds": {"dc_ac_band": json_setting(0.2)}})
    path = save_profile(edited, "default", ws)
    assert path == ws / "profiles" / "default.json"
    again = load_profile("default", ws)
    assert again.version == 2 and again.thresholds["dc_ac_band"].value == 0.2
    assert again.logo is not None and again.logo.is_file()
    assert find_profile("default", ws)[0] == "workspace" and find_profile("default", tmp_path / "tenant-b")[0] == "bundled"
    assert thresholds_of("default", ws)["dc_ac_band"].value == 0.2 and thresholds_of("default", tmp_path / "tenant-b") == {}
    assert thresholds_of("no-such-profile", ws) == {}
    assert {i.id: i.origin for i in list_profiles(ws)}["default"] == "workspace"


def json_setting(value: float):
    from unicad.drafting.sheets.model import ThresholdSetting

    return ThresholdSetting(value=value, source="Acme DS-04")


def test_saved_profile_json_is_minimal_and_reloads_equal(tmp_path):
    p = CompanyProfile.model_validate({**BASE, "version": 2, "title_block": TitleBlockChoice(kind="imported", templates={PaperSize.A3: TemplateRef(dxf=tmp_path / "profiles" / "x" / "tb.dxf", block="B")}), "numbering": Numbering(per_discipline=True)})
    data = to_json(p, (tmp_path / "profiles").resolve())
    assert data["title_block"]["templates"]["A3"]["dxf"] == "x/tb.dxf" and data["version"] == 2
    assert "thresholds" not in data and "symbol_overrides" not in data
    assert CompanyProfile.model_validate(data).anchored(tmp_path / "profiles") == p


@pytest.mark.parametrize("bad", ["../etc", "Acme", "a b", "", "x" * 41, "-x"])
def test_profile_ids_cannot_escape_the_profile_directory(bad):
    with pytest.raises(ValueError, match="profile id"):
        check_id(bad)


def test_findings_unchanged_without_a_profile():
    p = fixture("rooftop_500kwp")
    plain = run_rules(p, CATALOG).model_dump()
    assert plain == run_rules(p, CATALOG, thresholds={}).model_dump() == run_rules(p, CATALOG, thresholds=None).model_dump()

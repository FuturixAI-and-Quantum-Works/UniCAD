"""Onboarding a company from its DXF, the imported title block, company symbols and layer map, and sheets that honour them."""

import json
from datetime import date
from pathlib import Path

import ezdxf
import pytest
from test_rules import CATALOG, fixture

from unicad.drafting import dxfrules
from unicad.drafting.sheets import samples
from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.layermap import apply_layer_map
from unicad.drafting.sheets.model import CompanyProfile, PaperSize, SymbolOverride, TemplateRef
from unicad.drafting.sheets.render import render_sheet
from unicad.drafting.sheets.symbol_override import SymbolOverrideError, apply_symbol_overrides, check_override
from unicad.drafting.sheets.template import KNOWN_PLACEHOLDERS, parse_template
from unicad.drafting.sheets.text import text_width
from unicad.drafting.sld.sheet import dxf_text
from unicad.drafting.symbols import REGISTRY, pin_at
from unicad.drafting.symbols.library.kit import LAYER
from unicad.sets import render_set
from unicad.tenants.answers import parse_answers
from unicad.tenants.onboard import onboard
from unicad.tenants.samples import ANSWERS, company_drawing, write_sample
from unicad.tenants.store import load_profile

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "profiles" / "samples" / "acme"


@pytest.fixture(scope="module")
def acme(tmp_path_factory):
    ws = tmp_path_factory.mktemp("tenant")
    report = onboard(SAMPLE / "acme-company.dxf", parse_answers(SAMPLE / "answers.yaml"), ws)
    assert report.ok, report.failed
    return ws, report, load_profile("acme", ws)


def acme_dxf(tmp_path) -> Path:
    path = tmp_path / "acme.dxf"
    path.write_text(dxf_text(company_drawing()), encoding="utf-8")
    return path


def test_shipped_sample_files_are_what_the_generator_writes(tmp_path):
    dxf, answers = write_sample(tmp_path)
    assert dxf.read_text(encoding="utf-8") == (SAMPLE / "acme-company.dxf").read_text(encoding="utf-8")
    assert answers.read_text(encoding="utf-8") == (SAMPLE / "answers.yaml").read_text(encoding="utf-8") == ANSWERS


def test_acme_onboarding_reports_what_it_found_defaulted_and_validated(acme):
    ws, report, profile = acme
    assert report.ok and report.saved and report.path == ws / "profiles" / "acme.json"
    found = "\n".join(report.found)
    for expected in ("title block ACME_TB_A3 for A3: 24 placeholders", "title block ACME_TB_A1 for A1", "logo: block ACME_LOGO", "symbol pv.module", "symbol inst.kwh", "threshold dc_ac_band = 0.15", "layer map: G-SYMBOL -> ACME-SYM"):
        assert expected in found
    defaulted = "\n".join(report.defaulted)
    assert "text_style" in defaulted and "thresholds: UniCAD defaults for" in defaulted and "voltage_tolerance" in defaulted
    assert profile.version == 2 and profile.name == "Acme Solar EPC Pvt. Ltd."
    assert profile.title_block.kind == "imported" and set(profile.title_block.templates) == {PaperSize.A3, PaperSize.A1}
    assert profile.logo is not None and profile.logo.is_file() and profile.logo.parent == ws / "profiles" / "acme"
    assert profile.layer(profile.layers and next(iter(profile.layers))).name.startswith("ACME-")
    assert profile.drawing_number(client="SUN", project="R500", discipline="civil", seq=101) == "ACME/SUN/R500/CV/101"
    assert profile.thresholds["dc_ac_band"].value == 0.15 and "DS-04" in profile.thresholds["dc_ac_band"].source


def test_the_bundled_acme_profile_is_what_onboarding_produces(acme):
    ws, _, _ = acme
    assert json.loads((ROOT / "profiles" / "acme.json").read_text(encoding="utf-8")) == json.loads((ws / "profiles" / "acme.json").read_text(encoding="utf-8"))
    assert (ROOT / "profiles" / "acme" / "company.dxf").read_bytes() == (ws / "profiles" / "acme" / "company.dxf").read_bytes()
    assert load_profile("acme").title_block.kind == "imported"


def test_title_block_placeholders_and_boxes_are_read_from_the_dxf():
    for block, width in (("ACME_TB_A3", 115.0), ("ACME_TB_A1", 161.0)):
        t = parse_template(TemplateRef(dxf=SAMPLE / "acme-company.dxf", block=block))
        assert not t.problems and t.extents.width == pytest.approx(width) and not t.missing_required
        assert {"DRAWING_NO", "TITLE", "REV", "SHEET", "DRAWN", "LOGO", "ISSUED_APPROVAL"} <= t.names <= KNOWN_PLACEHOLDERS
        cell = {f.name: f.box for f in t.fields}
        assert cell["DRAWING_NO"].width == pytest.approx(width) and cell["REV"].x1 == pytest.approx(width)
        assert cell["TITLE"].height > cell["LOCATION"].height > 0


def test_a_title_block_drawn_on_a_layer_instead_of_a_block_works(tmp_path):
    src = ezdxf.readfile(SAMPLE / "acme-company.dxf")
    flat = ezdxf.new("R2018", setup=["linetypes"])
    dxfrules.prepare(flat)
    for layer in src.layers:
        if layer.dxf.name not in flat.layers:
            flat.layers.add(layer.dxf.name, color=7, lineweight=25)
    for e in src.blocks.get("ACME_TB_A3"):
        flat.modelspace().add_entity(e.copy())
    path = tmp_path / "flat.dxf"
    flat.saveas(path)
    t = parse_template(TemplateRef(dxf=path))
    assert not t.problems and "DRAWING_NO" in t.names


def test_onboarding_fails_on_a_title_block_without_the_required_placeholders(tmp_path):
    doc = company_drawing()
    block = doc.blocks.get("ACME_TB_A3")
    for e in list(block):
        if e.dxftype() == "TEXT" and e.dxf.text in ("{REV}", "{DRAWING_NO}"):
            block.delete_entity(e)
        elif e.dxftype() == "TEXT" and e.dxf.text == "{TITLE}":
            e.dxf.text = "{TITEL}"
    path = tmp_path / "bad.dxf"
    path.write_text(dxf_text(doc), encoding="utf-8")
    report = onboard(path, parse_answers(SAMPLE / "answers.yaml"), tmp_path / "ws")
    failed = "\n".join(report.failed)
    assert not report.ok and not report.saved and not (tmp_path / "ws" / "profiles" / "acme.json").exists()
    assert "unknown placeholder {TITEL}" in failed and "missing required placeholder {DRAWING_NO}" in failed and "missing required placeholder {REV}" in failed
    assert "for A1" in "\n".join(report.found) and report.profile is not None and set(report.profile.title_block.templates) == {PaperSize.A1}

    partial = onboard(path, parse_answers(SAMPLE / "answers.yaml"), tmp_path / "ws", accept_partial=True)
    assert partial.saved and load_profile("acme", tmp_path / "ws").title_block.templates.keys() == {PaperSize.A1}


def test_onboarding_without_a_title_block_or_logo_defaults_to_the_builtin_ones(tmp_path):
    empty = ezdxf.new("R2018")
    empty.layers.add("MY-FRAME", color=3, lineweight=50)
    path = tmp_path / "bare.dxf"
    empty.saveas(path)
    report = onboard(path, parse_answers({"id": "bare", "name": "Bare EPC", "short_code": "BARE"}), tmp_path / "ws")
    assert report.ok and report.profile is not None
    d = "\n".join(report.defaulted)
    assert "title block: UniCAD's built-in title block" in d and "logo: none found" in d
    assert any("sheet frame layer: MY-FRAME (matched by name)" in f for f in report.found)
    assert report.profile.title_block.kind == "builtin" and report.profile.logo is None


def test_onboarding_reports_bad_answers_without_saving(tmp_path):
    answers = parse_answers(
        {
            "id": "oops", "name": "Oops EPC", "short_code": "OOPS",
            "layers": {"frame": "NO-SUCH-LAYER"},
            "layer_map": {"G-SYMBOL": "NOT-THERE"},
            "symbols": {"pv.module": {"block": "MISSING"}, "no.such": {"block": "ACME_PV_MODULE"}},
            "thresholds": {"dc_ac_band": {"value": 7, "source": "x"}, "bogus": {"value": 1, "source": "x"}},
            "logo_block": "NOPE",
        }
    )
    report = onboard(SAMPLE / "acme-company.dxf", answers, tmp_path / "ws")
    failed = "\n".join(report.failed)
    for expected in ("NO-SUCH-LAYER", "NOT-THERE", "has no block 'MISSING'", "unknown symbol 'no.such'", "threshold dc_ac_band", "threshold bogus", "logo block 'NOPE'"):
        assert expected in failed
    assert not report.saved


def test_answers_reject_unknown_keys_and_bad_ids():
    with pytest.raises(ValueError, match="extra|Extra"):
        parse_answers({"id": "a", "name": "A", "short_code": "AA", "colour": "red"})
    with pytest.raises(ValueError, match="profile id"):
        parse_answers({"id": "../x", "name": "A", "short_code": "AA"})
    assert parse_answers(SAMPLE / "answers.yaml").numbering.per_discipline


def render(profile, paper=PaperSize.A3, **updates):
    spec = samples.rooftop_500kwp(paper)
    spec = spec.model_copy(update={"title_block": spec.title_block.model_copy(update=updates)}) if updates else spec
    doc = ezdxf.new("R2018", setup=["linetypes"])
    return doc, render_sheet(doc, spec, profile)


def texts(doc) -> list[str]:
    return [e.dxf.text for e in doc.modelspace().query("TEXT")]


@pytest.mark.parametrize("paper", [PaperSize.A3, PaperSize.A1, PaperSize.A2, PaperSize.A0, PaperSize.A4])
def test_imported_title_block_is_filled_and_passes_the_writer_rules(acme, paper):
    _, _, profile = acme
    doc, r = render(profile, paper, drawing_number="ACME/SUN/R500/EL/001")
    t = texts(doc)
    assert "ACME/SUN/R500/EL/001" in t and "SINGLE LINE DIAGRAM - LT NET METERING" in t and "Acme Solar EPC Pvt. Ltd." in t and "1 of 6" in t
    assert paper.value in t and "12.10.2026" in t
    assert not [s for s in t if "{" in s], "placeholders must not be drawn"
    assert dxfrules.violations(doc) == []
    assert r.warnings == ()
    assert {"ACME-TB", "ACME-TB-OUT", "ACME-TB-TXT"} <= {la.dxf.name for la in doc.layers}
    assert "G-TITLE" not in {e.dxf.layer for e in doc.modelspace()}
    ticks = [e for e in doc.modelspace().query("LINE") if e.dxf.layer == "ACME-TITLE" and e.dxf.get("lineweight") == 50]
    assert len(ticks) >= 2


def test_imported_title_block_replaces_the_builtin_one(acme):
    _, _, profile = acme
    doc_acme, _ = render(profile)
    doc_default, _ = render(CompanyProfile.load(ROOT / "profiles" / "default.json"))
    assert "Acme Solar EPC Pvt. Ltd." in texts(doc_acme) and "UniCAD Demo EPC" not in texts(doc_acme)
    assert "UniCAD Demo EPC" in texts(doc_default) and "Acme Solar EPC Pvt. Ltd." not in texts(doc_default)


def test_text_shrinks_to_fit_inside_each_placeholder_box(acme):
    _, _, profile = acme
    long = "VERY LONG UNBROKEN-" + "X" * 70
    doc, r = render(
        profile,
        client="A MUCH LONGER OWNER NAME " * 5,
        project_title=("EXTREMELY DETAILED PROJECT TITLE " * 12).strip(),
        drawing_title=long,
        drawing_number="ACME/" + "9" * 40,
        location=long,
        loa_no=long,
        drawn_by=long,
        scale="1:" + "5" * 30,
    )
    assert any("truncated" in w for w in r.warnings)
    by_handle = {c.handle: c.cell for c in r.text_cells}
    checked = 0
    for e in doc.modelspace().query("TEXT"):
        cell = by_handle.get(e.dxf.handle)
        if cell is None:
            continue
        h = e.dxf.height
        x, y = e.dxf.insert.x, e.dxf.insert.y
        w = text_width(e.dxf.text, h, e.dxf.get("width", 1.0))
        assert cell.contains(Region(x, y - 0.3 * h, x + w, y + h)), e.dxf.text
        checked += 1
    assert checked > 20
    assert dxfrules.violations(doc) == []


def test_text_is_drawn_inside_the_cells_of_the_title_block(acme):
    _, _, profile = acme
    doc, r = render(profile, PaperSize.A1)
    tb = [c for c in r.text_cells if c.cell.y0 >= r.column.y0 - 1e-6 and c.cell.y1 <= r.column.y0 + 167.3 + 1e-6 and c.cell.x0 >= r.column.x0 - 1e-6 and c.cell.x1 <= r.column.x1 + 1e-6]
    assert tb and all(r.column.contains(c.cell) for c in tb)


def test_a_missing_placeholder_is_reported_not_silent(acme, tmp_path):
    _, _, profile = acme
    src = company_drawing()
    for name in ("ACME_TB_A3", "ACME_TB_A1"):
        block = src.blocks.get(name)
        for e in list(block):
            if e.dxftype() == "TEXT" and e.dxf.text == "{CONSULTANT}":
                block.delete_entity(e)
    path = tmp_path / "nocons.dxf"
    path.write_text(dxf_text(src), encoding="utf-8")
    p = profile.model_copy(update={"title_block": profile.title_block.model_copy(update={"templates": {k: t.model_copy(update={"dxf": path}) for k, t in profile.title_block.templates.items()}})})
    _, r = render(p, consultant="GRIDLINE POWER CONSULTANTS PVT. LTD.")
    assert any("no placeholder for CONSULTANT" in w for w in r.warnings)


def test_an_unusable_template_falls_back_to_the_builtin_title_block(acme, tmp_path):
    _, _, profile = acme
    bad = tmp_path / "gone.dxf"
    p = profile.model_copy(update={"title_block": profile.title_block.model_copy(update={"templates": {k: t.model_copy(update={"dxf": bad}) for k, t in profile.title_block.templates.items()}})})
    doc, r = render(p)
    assert any("built-in one drawn" in w for w in r.warnings)
    assert "Acme Solar EPC Pvt. Ltd." in texts(doc) and "OWNER" in texts(doc)


def test_company_symbol_pins_land_on_the_unicad_pins(acme):
    _, _, profile = acme
    ov = profile.symbol_overrides["pv.module"]
    prepared = check_override("pv.module", ov)
    assert prepared.scale == pytest.approx(20 / 22) and prepared.residual < 1e-9

    doc = ezdxf.new("R2018", setup=["linetypes"])
    assert apply_symbol_overrides(doc, profile) == []
    block = doc.blocks.get(REGISTRY["pv.module"].block_name)
    ends = {(round(p.x, 3), round(p.y, 3)) for e in block.query("LINE") for p in (e.dxf.start, e.dxf.end)}
    for pin in REGISTRY["pv.module"].pins:
        assert (round(pin.x, 3), round(pin.y, 3)) in ends
        assert (pin.x, pin.y) == pin_at("pv.module", pin.name, (0, 0))
    assert {e.dxf.layer for e in block if e.dxftype() != "ATTDEF"} == {LAYER}
    assert any(e.dxftype() == "ATTDEF" for e in block)
    assert not block.query("POINT")


def test_fitted_symbol_must_touch_every_pin(acme):
    _, _, profile = acme
    ov = profile.symbol_overrides["inst.kwh"]
    prepared = check_override("inst.kwh", ov)
    assert prepared.residual < 0.5 and prepared.scale == pytest.approx(10 / 12)


def test_symbol_overrides_that_do_not_match_are_refused_with_the_reason(acme):
    _, _, profile = acme
    dxf = profile.symbol_overrides["pv.module"].dxf
    with pytest.raises(SymbolOverrideError, match="marks no pins"):
        check_override("pv.module", SymbolOverride(dxf=dxf, block="ACME_KWH_METER"))
    with pytest.raises(SymbolOverrideError, match="different shape"):
        check_override("pv.module", SymbolOverride(dxf=dxf, block="ACME_PV_MODULE", pins="fit"))
    with pytest.raises(SymbolOverrideError, match=r"does not have|not mark"):
        check_override("inst.kwh", SymbolOverride(dxf=dxf, block="ACME_PV_MODULE"))
    with pytest.raises(SymbolOverrideError, match="unknown symbol"):
        check_override("nope.nope", SymbolOverride(dxf=dxf, block="ACME_PV_MODULE"))
    with pytest.raises(SymbolOverrideError, match="has no block"):
        check_override("pv.module", SymbolOverride(dxf=dxf, block="NOPE"))


def test_a_broken_symbol_override_is_skipped_at_render_time(acme, tmp_path):
    _, _, profile = acme
    p = profile.model_copy(update={"symbol_overrides": {"pv.module": SymbolOverride(dxf=profile.symbol_overrides["pv.module"].dxf, block="NOPE")}})
    doc, r = render(p)
    assert any("company symbol for pv.module not used" in w for w in r.warnings)
    assert dxfrules.violations(doc) == []


def test_layer_map_moves_every_entity_to_the_company_layer(acme):
    _, _, profile = acme
    doc = ezdxf.new("R2018", setup=["linetypes"])
    doc.layers.add("G-SYMBOL", color=7, lineweight=25)
    doc.layers.add("G-UNMAPPED", color=7, lineweight=25)
    doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "G-SYMBOL"})
    doc.modelspace().add_line((0, 0), (1, 2), dxfattribs={"layer": "G-UNMAPPED"})
    blk = doc.blocks.new("B")
    blk.add_line((0, 0), (1, 0), dxfattribs={"layer": "G-SYMBOL"})
    assert apply_layer_map(doc, profile) == []
    assert {e.dxf.layer for e in doc.modelspace()} == {"ACME-SYM", "G-UNMAPPED"} and {e.dxf.layer for e in blk} == {"ACME-SYM"}
    assert "G-SYMBOL" not in doc.layers
    assert doc.layers.get("ACME-SYM").dxf.color == 3 and doc.layers.get("ACME-SYM").dxf.lineweight == 25


def rooftop_sld_set(profile, out: Path, formats=("dxf",)):
    project = fixture("rooftop_500kwp").model_copy(update={"sheets": ()})
    return render_set(project, out, formats=formats, profile=profile, catalog=CATALOG)


def test_rooftop_sld_set_carries_the_acme_title_block_numbering_symbols_and_layers(acme, tmp_path):
    _, _, profile = acme
    index = rooftop_sld_set(profile, tmp_path / "set")
    (sheet,) = index.sheets
    assert sheet["number"] == "ACME/DIPL/R500/EL/001" and not any("title block" in w for w in sheet["warnings"])
    doc = ezdxf.readfile(tmp_path / "set" / sheet["files"]["dxf"])
    t = texts(doc)
    assert "ACME/DIPL/R500/EL/001" in t and "Acme Solar EPC Pvt. Ltd." in t
    layers = {e.dxf.layer for e in doc.modelspace()} | {e.dxf.layer for b in doc.blocks if not b.name.startswith("*") for e in b}
    assert "ACME-SYM" in layers and "G-SYMBOL" not in layers and "G-SYMBOL" not in {la.dxf.name for la in doc.layers}
    meter = doc.blocks.get(REGISTRY["inst.kwh"].block_name)
    assert any(e.dxftype() == "TEXT" and e.dxf.text == "Wh" for e in meter)
    assert dxfrules.violations(doc) == []


def test_two_renders_with_the_acme_profile_are_byte_identical(acme, tmp_path):
    _, _, profile = acme
    a = rooftop_sld_set(profile, tmp_path / "a").sheets[0]["files"]["dxf"]
    b = rooftop_sld_set(profile, tmp_path / "b").sheets[0]["files"]["dxf"]
    assert (tmp_path / "a" / a).read_bytes() == (tmp_path / "b" / b).read_bytes()


def test_existing_sheet_numbers_are_kept_when_a_company_switches_profile(acme, tmp_path):
    _, _, profile = acme
    project = fixture("rooftop_500kwp")
    number = project.sheets[0].number
    index = render_set(project, tmp_path / "set", formats=("dxf",), profile=profile, catalog=CATALOG)
    assert index.sheets[0]["number"] == number


def test_title_block_carries_a_date_in_the_company_format(acme):
    _, _, profile = acme
    doc, _ = render(profile, drawn_date=date(2026, 3, 4))
    assert "04.03.2026" in texts(doc)


def test_old_default_numbers_do_not_confuse_the_acme_pattern(acme):
    _, _, profile = acme
    from unicad.sets.numbering import Numberer

    numbers = Numberer.for_project(fixture("rooftop_500kwp"), profile)
    assert numbers.fresh().startswith("ACME/")


def test_onboarding_twice_gives_the_same_profile(tmp_path):
    answers = parse_answers(SAMPLE / "answers.yaml")
    first = onboard(SAMPLE / "acme-company.dxf", answers, tmp_path)
    saved = first.path.read_text(encoding="utf-8")
    again = onboard(tmp_path / "profiles" / "acme" / "company.dxf", answers, tmp_path)
    assert again.ok and again.path.read_text(encoding="utf-8") == saved
    assert first.found[1:] == again.found[1:]

from pathlib import Path

import pytest

from unicad.domain.catalog import CellTech, InverterSpec, ModuleSpec
from unicad.ingest import (
    IncompleteSpec,
    inverter_from_ond,
    inverter_to_ond,
    module_from_pan,
    module_to_pan,
    parse_pvsyst,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ingest"


def test_parse_real_pan_structure():
    f = parse_pvsyst(FIXTURES / "real_ET-M772BH550GL.PAN")
    assert f.kind == "pvModule" and f.version == "7.2"
    assert f.commercial.text("Manufacturer") == "ET SOLAR"
    assert f.root.number("Voc") == 49.9
    profile = f.root.children["PVObject_IAM"].children["IAMProfile"]
    assert profile.numbers("Point_5") == [50.0, 0.98]


def test_parse_real_ond_with_bom_and_nested_blocks():
    f = parse_pvsyst((FIXTURES / "real_CPS_SCH275KTL-DO-US-800.OND").read_bytes())
    assert f.kind == "pvGInverter"
    conv = f.root.children["Converter"]
    assert conv.number("PInEffMax") == 150000.0
    assert conv.children["ProfilPIOV2"].numbers("Point_5") == [75795.9, 75000.0]
    assert f.root.number("NbMPPT") == 12
    assert f.root.number("TanPhiMin") == -0.75
    assert f.commercial.children["Remarks, Count"].fields["Str_1"].startswith("Protection")


def test_not_a_pvsyst_file():
    with pytest.raises(ValueError, match="PVObject_"):
        parse_pvsyst("hello=world\n")


def test_real_pan_to_module_spec():
    spec = module_from_pan(FIXTURES / "real_ET-M772BH550GL.PAN", tech=CellTech.MONO_PERC)
    assert (spec.manufacturer, spec.model) == ("ET SOLAR", "ET-M772BH550GL")
    assert (spec.pmax, spec.voc, spec.isc, spec.vmp, spec.imp) == (550.0, 49.9, 14.0, 41.96, 13.11)
    assert (spec.length, spec.width, spec.cells) == (2278.0, 1134.0, 144)
    assert spec.temp_coeff_pmax == -0.34
    assert spec.temp_coeff_voc == pytest.approx(-128.0 / (10 * 49.9))
    assert spec.temp_coeff_isc == pytest.approx(7.28 / (10 * 14.0))
    assert spec.bifacial and spec.bifaciality == 0.7
    assert spec.max_system_voltage == 1500
    assert spec.source.kind == "pan_ond" and "DataSource" not in spec.source.ref and "Manufacturer 2021" in spec.source.ref
    assert spec.id == "et-solar-et-m772bh550gl"


def test_mono_pan_needs_a_cell_technology():
    with pytest.raises(IncompleteSpec) as e:
        module_from_pan(FIXTURES / "synthetic_module_mono_1500V.PAN")
    assert e.value.missing == ["tech"]
    assert e.value.partial["pmax"] == 580.0


def test_incomplete_pan_lists_every_missing_field_and_invents_none():
    with pytest.raises(IncompleteSpec) as e:
        module_from_pan(FIXTURES / "synthetic_module_no_dimensions.PAN")
    assert e.value.missing == ["length", "width", "cells", "tech"]
    assert e.value.partial["length"] is None


def test_pan_round_trip():
    original = module_from_pan(FIXTURES / "synthetic_module_mono_1500V.PAN", tech=CellTech.TOPCON)
    again = module_from_pan(module_to_pan(original), tech=CellTech.TOPCON)
    a, b = original.model_dump(exclude={"source"}), again.model_dump(exclude={"source"})
    assert a.keys() == b.keys()
    for k in a:
        assert b[k] == pytest.approx(a[k]), k


def test_real_pan_round_trip_preserves_every_spec_field():
    original = module_from_pan(FIXTURES / "real_ET-M772BH550GL.PAN", tech=CellTech.MONO_PERC)
    again = module_from_pan(module_to_pan(original), tech=CellTech.MONO_PERC)
    for k, v in original.model_dump(exclude={"source"}).items():
        assert getattr(again, k) == pytest.approx(v), k


def test_thin_film_and_poly_technology_map_without_override():
    text = (FIXTURES / "synthetic_module_mono_1500V.PAN").read_text()
    assert module_from_pan(text.replace("mtSiMono", "mtSiPoly")).tech == CellTech.POLY
    assert module_from_pan(text.replace("mtSiMono", "mtCdTe")).tech == CellTech.THIN_FILM


def test_real_ond_to_inverter_spec():
    spec = inverter_from_ond(FIXTURES / "real_CPS_SCH275KTL-DO-US-800.OND", topology="string")
    assert (spec.manufacturer, spec.model) == ("ChintPower", "CPS SCH275KTL-DO/US-800")
    assert (spec.ac_rated_kva, spec.ac_voltage, spec.phases) == (250.0, 800.0, 3)
    assert spec.ac_max_kva == pytest.approx(3**0.5 * 800 * 199 / 1000)
    assert (spec.dc_max_voltage, spec.mppt_min, spec.mppt_max) == (1500, 500, 1500)
    assert (spec.mppt_count, spec.dc_input_count, spec.max_dc_input_current) == (12, 36, 360.0)
    assert spec.max_input_current_per_mppt is None
    assert spec.max_efficiency == pytest.approx(0.9901)
    assert spec.source.kind == "pan_ond"


def test_ond_without_topology_is_incomplete():
    with pytest.raises(IncompleteSpec) as e:
        inverter_from_ond(FIXTURES / "synthetic_inverter_string_1100V.OND")
    assert e.value.missing == ["topology"]


def test_ond_round_trip():
    original = inverter_from_ond(FIXTURES / "synthetic_inverter_string_1100V.OND", topology="string")
    again = inverter_from_ond(inverter_to_ond(original), topology="string")
    a, b = original.model_dump(exclude={"source"}), again.model_dump(exclude={"source"})
    for k in a:
        assert b[k] == pytest.approx(a[k]), k


def test_ond_round_trip_of_single_mppt_central_inverter():
    from unicad.catalog import load_catalog

    spec = load_catalog().get("sungrow-sg3125hv-mv-20")
    again = inverter_from_ond(inverter_to_ond(spec), topology="central")
    assert again.max_dc_input_current == spec.max_dc_input_current == again.max_input_current_per_mppt
    assert again.ac_max_kva == pytest.approx(spec.ac_max_kva)


def test_wrong_file_kind_rejected():
    with pytest.raises(ValueError, match="pvModule"):
        module_from_pan(FIXTURES / "synthetic_inverter_string_1100V.OND")
    with pytest.raises(ValueError, match="pvGInverter"):
        inverter_from_ond(FIXTURES / "real_ET-M772BH550GL.PAN")


def test_types():
    assert isinstance(module_from_pan(FIXTURES / "real_ET-M772BH550GL.PAN", tech=CellTech.TOPCON), ModuleSpec)
    assert isinstance(inverter_from_ond(FIXTURES / "synthetic_inverter_string_1100V.OND", topology="string"), InverterSpec)


RAW = Path(__file__).resolve().parent.parent / "references" / "catalog-sources" / "raw" / "datasheets"
EXTRACTED = Path(__file__).resolve().parent.parent / "references" / "catalog-sources" / "extracted"
needs_raw = pytest.mark.skipif(not RAW.is_dir(), reason="raw datasheets are not checked out")


def csv_row(name: str, model: str) -> dict[str, str]:
    import csv

    with open(EXTRACTED / name, newline="", encoding="utf-8") as f:
        return next(r for r in csv.DictReader(f) if r["model"] == model)


@pytest.fixture(scope="module")
def waaree():
    from unicad.ingest.datasheet import extract_text_and_tables

    return extract_text_and_tables(RAW / "modules" / "Waaree_Elite_BiN-08-565-600_144_TOPCon.pdf")


@needs_raw
def test_module_datasheet_fields_match_csv(waaree):
    from unicad.ingest.datasheet import draft_spec

    row = csv_row("module_datasheets.csv", "BiN-08-580")
    fields, missing, warnings = draft_spec("module", waaree.candidates, row="BiN-08-580")
    expected = {
        "pmax": "pmax_w", "vmp": "vmp_v", "imp": "imp_a", "voc": "voc_v", "isc": "isc_a",
        "temp_coeff_isc": "alpha_isc_pct_per_c", "temp_coeff_voc": "beta_voc_pct_per_c", "temp_coeff_pmax": "gamma_pmax_pct_per_c",
        "length": "length_mm", "width": "width_mm", "cells": "cells", "max_system_voltage": "max_system_v", "max_series_fuse": "max_series_fuse_a",
    }
    for field, col in expected.items():
        assert fields[field] == float(row[col]), field
    assert fields["bifaciality"] == 0.8
    assert warnings and "tech not set" in warnings[0] and "topcon" in warnings[0]


@needs_raw
def test_draft_never_fills_what_the_sheet_does_not_state(waaree):
    from unicad.ingest.datasheet import draft_spec

    fields, missing, _ = draft_spec("module", waaree.candidates, row="BiN-08-580")
    assert set(missing) == {"manufacturer", "model", "tech"}
    assert not set(missing) & set(fields)


@needs_raw
def test_multi_model_sheet_is_ambiguous_without_a_row(waaree):
    from unicad.ingest.datasheet import draft_spec

    fields, missing, warnings = draft_spec("module", waaree.candidates)
    assert "pmax" not in fields and "pmax" in missing
    assert any(w.startswith("pmax is ambiguous, 8 distinct values") for w in warnings)


@needs_raw
def test_every_waaree_table_row_matches_csv(waaree):
    import csv

    from unicad.ingest.datasheet import draft_spec

    with open(EXTRACTED / "module_datasheets.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["source_file"].endswith("Waaree_Elite_BiN-08-565-600_144_TOPCon.pdf")]
    assert len(rows) == 8
    for r in rows:
        fields, _, _ = draft_spec("module", waaree.candidates, row=r["model"])
        assert [fields[k] for k in ("pmax", "vmp", "imp", "voc", "isc")] == [float(r[c]) for c in ("pmax_w", "vmp_v", "imp_a", "voc_v", "isc_a")], r["model"]


@needs_raw
def test_inverter_datasheet_fields_match_csv():
    from unicad.ingest.datasheet import draft_spec, extract_text_and_tables

    ex = extract_text_and_tables(RAW / "inverters" / "Sungrow_SG110CX-P2_DS_V3.pdf")
    row = csv_row("inverter_datasheets.csv", "SG110CX-P2")
    fields, missing, warnings = draft_spec("inverter", ex.candidates)
    assert fields["dc_max_voltage"] == float(row["vdc_max_v"])
    assert (fields["mppt_min"], fields["mppt_max"]) == (float(row["mppt_low_v"]), float(row["mppt_high_v"]))
    assert fields["mppt_count"] == int(row["mppt_count"]) and fields["inputs_per_mppt"] == int(row["strings_per_mppt"])
    assert fields["ac_rated_kva"] == float(row["ac_rated_kw"])
    assert fields["max_dc_input_current"] == 360.0
    assert "ac_voltage" in missing and any("ac_voltage is ambiguous" in w for w in warnings)
    assert {"topology", "phases"} <= set(missing)


@needs_raw
def test_inverter_per_mppt_fields_from_second_datasheet():
    from unicad.ingest.datasheet import draft_spec, extract_text_and_tables

    ex = extract_text_and_tables(RAW / "inverters" / "Huawei_SUN2000-100KTL-M2_DS.pdf")
    row = csv_row("inverter_datasheets.csv", "SUN2000-100KTL-M2")
    fields, missing, _ = draft_spec("inverter", ex.candidates)
    assert fields["max_input_current_per_mppt"] == float(row["idc_max_per_mppt_a"])
    assert fields["max_short_circuit_current_per_mppt"] == float(row["isc_max_per_mppt_a"])
    assert (fields["mppt_min"], fields["mppt_max"], fields["start_voltage"]) == (float(row["mppt_low_v"]), float(row["mppt_high_v"]), float(row["start_voltage_v"]))
    assert fields["ac_rated_kva"] == float(row["ac_rated_kw"]) and fields["ac_max_kva"] == float(row["ac_rated_kva"])
    assert "max_input_current_per_mppt or max_dc_input_current" not in missing


@needs_raw
def test_label_first_table_gives_one_value_per_power_class():
    import csv

    from unicad.ingest.datasheet import extract_text_and_tables

    ex = extract_text_and_tables(RAW / "modules" / "Adani_ElanShine_TOPCon_G2G_GenII.pdf")
    with open(EXTRACTED / "module_datasheets.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if "Adani_ElanShine" in r["source_file"]]
    pmax = [c for c in ex.candidates if c.label.startswith("Peak power, Pmax")]
    assert [c.value for c in pmax] == [float(r["pmax_w"]) for r in rows]
    voc = [c for c in ex.candidates if c.label.startswith("Open circuit voltage, Voc") and c.unit == "V" and c.column is not None][:7]
    assert [c.value for c in voc] == [float(r["voc_v"]) for r in rows]
    assert all(c.page == 2 for c in pmax)


@needs_raw
def test_image_only_pdf_is_reported_not_guessed():
    from unicad.ingest.datasheet import draft_spec, extract_text_and_tables

    ex = extract_text_and_tables(RAW / "modules" / "Premier_PE-144HC_TOPCon_DualGlass_560-590.pdf")
    assert ex.image_only_pages and not ex.candidates
    assert draft_spec("module", ex.candidates).fields == {}


def test_unknown_kind_rejected():
    from unicad.ingest.datasheet import draft_spec

    with pytest.raises(ValueError, match="unsupported kind"):
        draft_spec("transformer", [])

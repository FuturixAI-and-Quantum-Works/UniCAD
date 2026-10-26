"""Catalog families added for residential inverters, 800 V-class LT breakers, 36 kV high-current VCBs and 11 kV instrument transformers and arresters."""

import csv
import json
from pathlib import Path

import pytest

from unicad.catalog import Catalog, load_catalog
from unicad.domain.catalog import InverterSpec, ProtectionSpec

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "references" / "catalog-sources" / "extracted"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(ROOT / "catalog")


def residential_rows() -> list[dict[str, str]]:
    with open(EXTRACTED / "inverter_residential_datasheets.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_every_residential_row_is_catalogued_with_its_citation(catalog):
    rows = residential_rows()
    assert len(rows) == 26
    for line, r in enumerate(rows, start=2):
        spec = catalog.get(f"{r['manufacturer']}-{r['model']}".lower().replace(" ", "-"))
        assert isinstance(spec, InverterSpec) and spec.topology == "string" and spec.symbol == "inv.string"
        assert (spec.phases, spec.ac_rated_kva, spec.ac_max_kva, spec.ac_voltage) == (int(r["phases"]), float(r["ac_rated_kw"]), float(r["ac_max_kva"]), float(r["ac_voltage_v"]))
        assert (spec.dc_max_voltage, spec.mppt_min, spec.mppt_max, spec.start_voltage) == (float(r["vdc_max_v"]), float(r["mppt_low_v"]), float(r["mppt_high_v"]), float(r["start_voltage_v"]))
        assert spec.source.kind == "datasheet" and f"{r['source_file']} p.{r['pdf_page']}" in spec.source.ref
        assert f"inverter_residential_datasheets.csv row {line}" in spec.source.ref


def test_residential_inverters_cover_one_to_ten_kilowatts_in_both_phases(catalog):
    resi = [i for i in catalog.filter(kind="inverter") if isinstance(i, InverterSpec) and i.ac_rated_kva <= 10]
    one = sorted({i.ac_rated_kva for i in resi if i.phases == 1})
    three = sorted({i.ac_rated_kva for i in resi if i.phases == 3})
    assert one[0] == 1 and one[-1] == 6 and {3, 5} <= set(one)
    assert three[0] == 3 and three[-1] == 10 and {5, 6, 10} <= set(three)
    assert {i.manufacturer for i in resi} >= {"Growatt", "Solis", "Sungrow", "Polycab", "Havells"}
    assert all(i.ac_voltage == 230 for i in resi if i.phases == 1) and all(i.ac_voltage == 400 for i in resi if i.phases == 3)


def test_uneven_trackers_store_totals_only(catalog):
    sg = catalog.get("sungrow-sg10rt")
    assert (sg.mppt_count, sg.inputs_per_mppt, sg.dc_input_count) == (2, 1, 3)
    assert (sg.max_input_current_per_mppt, sg.max_short_circuit_current_per_mppt) == (None, None)
    assert (sg.max_dc_input_current, sg.max_dc_short_circuit_current) == (37.5, 48)
    single = catalog.get("solis-s6-gr1p3k-m")
    assert (single.mppt_count, single.inputs_per_mppt, single.dc_input_count, single.max_dc_input_current) == (1, 2, 2, 19)


def test_vcb_36kv_high_current_ladder_cites_the_ordering_table(catalog):
    for in_a, code in ((1600, "16"), (2000, "20"), (2500, "25")):
        for isc, suffix in ((25, "25"), (31.5, "31"), (40, "40")):
            v = catalog.get(f"abb-vd4-36kv-{in_a}a-{isc:g}ka")
            assert isinstance(v, ProtectionSpec) and v.model == f"VD4 36.{code}.{suffix}"
            assert (v.rated_voltage, v.breaking_capacity_ka, v.short_time_withstand_ka, v.short_time_duration_s) == (36000, isc, isc, 3)
            assert "ABB_VD4_12-36kV_VCB_TK520.pdf p.19" in v.source.ref


def test_800v_class_lt_breakers(catalog):
    h10 = [catalog.get(f"schneider-mtz2-h10-{a}a-3p") for a in (800, 1000, 1250, 1600, 2000, 2500, 3200, 4000)]
    assert all(b.device == "acb" and (b.rated_voltage, b.poles, b.breaking_capacity_ka, b.short_time_withstand_ka) == (1150, 3, 50, 50) for b in h10)
    assert "MasterPact_MTZ_selection_0614CT1701_thirdparty.pdf p.12" in h10[0].source.ref
    t5 = catalog.get("abb-tmax-t5-v-400a-3p")
    assert (t5.device, t5.rated_voltage, t5.breaking_capacity_ka, t5.poles) == ("mccb", 1150, 12, 3)
    assert catalog.get("abb-tmax-t5-v-400a-4p").poles == 4
    t6 = catalog.get("abb-tmax-t6-l-800a-3p")
    assert (t6.rated_voltage, t6.breaking_capacity_ka) == (1000, 12)
    mccb_800 = [p for p in catalog.filter(kind="protection") if p.device in ("mccb", "acb") and p.rated_voltage >= 800]
    assert min(p.rated_current for p in mccb_800) == 100 and max(p.rated_current for p in mccb_800) == 4000


def test_11kv_instrument_transformers_and_arrester_cite_the_purchaser_specification(catalog):
    ct = catalog.get("generic-ct-11kv-apdcl-0.5-5p10")
    assert (ct.device, ct.rated_voltage, ct.ratio, ct.short_time_withstand_ka, ct.short_time_duration_s) == ("ct", 12000, None, 25, 3)
    assert "0.5" in ct.accuracy_class and "5P10" in ct.accuracy_class
    pt = catalog.get("generic-pt-11kv-apdcl-0.5-100va")
    assert (pt.device, pt.rated_voltage, pt.ratio, pt.burden_va) == ("pt", 12000, "11/0.11 kV", 100)
    la = catalog.get("generic-la-11kv-apdcl-ur9-10ka")
    assert (la.device, la.rated_voltage, la.nominal_discharge_current_ka, la.surge_counter) == ("lightning_arrester", 9000, 10, None)
    for spec in (ct, pt, la):
        assert "APDCL_" in spec.source.ref and "protection_p5.json" in spec.source.ref


def test_p5_extract_files_are_the_catalog_inputs():
    p5 = json.loads((EXTRACTED / "protection_p5.json").read_text())
    assert set(p5) == {"_about", "acb_mtz2_h10", "mccb_tmax_1150v", "ct_11kv", "pt_11kv", "la_11kv"}
    report = (ROOT / "catalog" / "REPORT.md").read_text()
    assert "33 kV inverter-duty transformers with an 800 V LV winding: gap" in report

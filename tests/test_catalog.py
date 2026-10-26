import csv
import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from unicad.catalog import Catalog, load_catalog
from unicad.catalog.store import KIND_FILES
from unicad.domain.catalog import EquipmentSpec

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"
EXTRACTED = ROOT / "references" / "catalog-sources" / "extracted"
ADAPTER = TypeAdapter(EquipmentSpec)


def read_csv(name: str) -> list[dict[str, str]]:
    with open(EXTRACTED / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_builder():
    spec = importlib.util.spec_from_file_location("build_catalog", ROOT / "tools" / "build_catalog.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_catalog"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(CATALOG)


def raw_entries() -> list[tuple[str, dict]]:
    return [(kind, e) for kind, name in KIND_FILES.items() for e in json.loads((CATALOG / name).read_text())]


def test_every_entry_validates_and_matches_its_file():
    entries = raw_entries()
    assert len(entries) > 1000
    for kind, raw in entries:
        spec = ADAPTER.validate_python(raw)
        assert spec.kind == kind
        assert spec.source.ref and spec.source.retrieved


def test_ids_unique_across_kinds():
    ids = [e["id"] for _, e in raw_entries()]
    assert len(ids) == len(set(ids))


def test_files_sorted_by_id():
    for name in KIND_FILES.values():
        ids = [e["id"] for e in json.loads((CATALOG / name).read_text())]
        assert ids == sorted(ids), name


def test_every_kind_is_populated(catalog):
    for kind in KIND_FILES:
        assert catalog.filter(kind=kind), kind


def pick(rows: list[dict[str, str]], seed: int) -> dict[str, str]:
    return random.Random(seed).choice(rows)


def test_module_matches_datasheet_row(catalog):
    rows = [r for r in read_csv("module_datasheets.csv") if r["gamma_pmax_pct_per_c"] and r["length_mm"]]
    row = pick(rows, 1)
    (spec,) = [m for m in catalog.filter(kind="module", manufacturer=row["manufacturer"]) if m.model == row["model"]]
    assert (spec.pmax, spec.voc, spec.isc, spec.vmp, spec.imp) == tuple(
        float(row[k]) for k in ("pmax_w", "voc_v", "isc_a", "vmp_v", "imp_a")
    )
    assert spec.length == float(row["length_mm"])
    assert spec.temp_coeff_pmax == float(row["gamma_pmax_pct_per_c"])


def test_inverter_matches_datasheet_row(catalog):
    rows = [r for r in read_csv("inverter_datasheets.csv") if r["idc_max_per_mppt_a"] and r["isc_max_per_mppt_a"] and r["strings_per_mppt"]]
    row = pick(rows, 2)
    (spec,) = [i for i in catalog.filter(kind="inverter", manufacturer=row["manufacturer"]) if i.model == row["model"]]
    assert spec.dc_max_voltage == float(row["vdc_max_v"])
    assert (spec.mppt_min, spec.mppt_max) == (float(row["mppt_low_v"]), float(row["mppt_high_v"]))
    assert spec.max_input_current_per_mppt == float(row["idc_max_per_mppt_a"])
    assert spec.ac_voltage == float(row["ac_voltage_v"])


def test_transformer_matches_is1180_row(catalog):
    row = pick([r for r in read_csv("transformer_is1180_ratings_losses.csv") if r["phase"] == "3"], 3)
    specs = [t for t in catalog.filter(kind="transformer") if t.rated_kva == float(row["kva"]) and t.hv_kv == 11]
    assert len(specs) == 1
    assert specs[0].impedance_pct == float(row["impedance_pct"])
    assert specs[0].lv_kv == (0.433,)


def test_cable_matches_kei_row(catalog):
    rows = [
        r for r in read_csv("kei_lt_cables.csv")
        if r["insulation"] == "XLPE" and r["arrangement"] == "multicore" and r["armour_scope"] == "armoured only"
        and r["cores"] != "3.5" and r["min_wires"].isdigit() and r["rac_temp_c"] == "90"
    ]
    row = pick(rows, 4)
    candidates = [
        c for c in catalog.filter(kind="cable", manufacturer="KEI")
        if c.insulation == "xlpe" and c.armoured and c.conductor.value == row["material"].lower()
        and c.cores == int(row["cores"]) and c.size_mm2 == float(row["size_mm2"])
    ]
    assert len(candidates) == 1
    spec = candidates[0]
    assert (spec.ampacity_ground, spec.ampacity_air) == (float(row["ground_a"]), float(row["air_a"]))
    assert spec.r_ohm_per_km_90c == float(row["r_ac_ohm_km"])
    assert spec.voltage_grade == "1.1 kV"


def test_solar_cable_matches_polycab_row(catalog):
    row = pick(read_csv("polycab_solar_dc_en50618.csv"), 5)
    (spec,) = [c for c in catalog.filter(kind="cable", manufacturer="Polycab") if c.service == "dc_solar" and c.size_mm2 == float(row["size_mm2"])]
    assert spec.ampacity_air == float(row["single_free_air_a"])
    assert spec.voltage_grade == "1.5 kV DC"
    assert spec.r_ohm_per_km_90c > float(row["r_dc20_ohm_km"])


def test_gpv_fuse_ladder_matches_json(catalog):
    ladder = json.loads((EXTRACTED / "protection_ladders.json").read_text())["gpv_fuse"]
    fuses = [p for p in catalog.filter(kind="protection", manufacturer="Eaton Bussmann") if p.device == "fuse_gpv"]
    assert sorted(f.rated_current for f in fuses) == sorted(float(a) for a in ladder["rated_current_a"])
    assert {f.rated_voltage for f in fuses} == {float(ladder["rated_voltage_vdc"])}


def test_battery_matches_bess_row(catalog):
    row = pick([r for r in read_csv("bess_pcs_datasheets.csv") if r["energy_kwh"]], 6)
    (spec,) = catalog.filter(kind="battery", manufacturer=row["manufacturer"])
    assert spec.energy_kwh == float(row["energy_kwh"])
    assert (spec.voltage_min, spec.voltage_max) == (float(row["dc_voltage_min_v"]), float(row["dc_voltage_max_v"]))


def test_get_by_id(catalog):
    spec = catalog.get(catalog.filter(kind="meter")[0].id)
    assert spec.kind == "meter"
    with pytest.raises(KeyError):
        catalog.get("no-such-id")


def test_filter_by_kind_manufacturer_and_power(catalog):
    modules = catalog.filter(kind="module")
    assert {m.kind for m in modules} == {"module"}
    waaree = catalog.filter(kind="module", manufacturer="waaree")
    assert waaree and all(m.manufacturer == "Waaree" for m in waaree)
    band = catalog.filter(kind="module", min_power=580, max_power=600)
    assert band and all(580 <= m.pmax <= 600 for m in band)
    assert len(band) < len(modules)
    assert catalog.filter(kind="module", min_power=10_000) == []
    assert catalog.filter(kind="cable", min_power=1) == []
    kva = catalog.filter(kind="transformer", min_power=1000, max_power=1000)
    assert kva and all(t.rated_kva == 1000 for t in kva)


def test_search_requires_every_term(catalog):
    hits = catalog.search("waaree 600")
    assert hits and all("waaree" in h.id and "600" in h.id for h in hits)
    narrow = catalog.search("sungrow pcs")
    assert narrow and {h.kind for h in narrow} == {"pcs"} and all(h.manufacturer == "Sungrow" for h in narrow)
    assert catalog.search("waaree zzzzzz") == []
    assert catalog.search("") == []
    assert len(catalog.search("kei", limit=3)) == 3
    assert [h.id for h in catalog.search("schneider mtz2 h1")] == sorted(h.id for h in catalog.search("schneider mtz2 h1"))


def test_search_matches_device_type(catalog):
    hits = catalog.search("spd_dc", limit=10)
    assert hits and all(h.device == "spd_dc" for h in hits)


def test_duplicate_ids_rejected(catalog):
    first = catalog.entries[0]
    with pytest.raises(ValueError, match="duplicate"):
        Catalog((first, first))


def test_build_is_idempotent_and_committed_catalog_is_fresh(tmp_path):
    builder = load_builder()
    builder.build(tmp_path / "a")
    builder.build(tmp_path / "b")
    names = [*KIND_FILES.values(), "REPORT.md"]
    for name in names:
        first = (tmp_path / "a" / name).read_bytes()
        assert first == (tmp_path / "b" / name).read_bytes(), name
        assert first == (CATALOG / name).read_bytes(), f"{name} is stale: rerun tools/build_catalog.py"


def cable_fields(**over):
    base = dict(
        id="x", manufacturer="m", model="m", source={"kind": "manual", "ref": "r"}, service="lv_ac", conductor="al", cores=3,
        size_mm2=300, insulation="xlpe", armoured=True, voltage_grade="1.1 kV",
    )
    return {**base, **over}


def test_three_and_a_half_core_cable_callout_and_import(catalog):
    spec = ADAPTER.validate_python({**cable_fields(reduced_neutral=True), "kind": "cable"})
    assert spec.callout() == "3.5C x 300 sq.mm Al XLPE ARM"
    assert ADAPTER.validate_python({**cable_fields(cores=4), "kind": "cable"}).callout() == "4C x 300 sq.mm Al XLPE ARM"
    polycab = catalog.get("polycab-lt-xlpe-al-3.5c-300-arm")
    assert (polycab.cores, polycab.reduced_neutral, polycab.neutral_size_mm2) == (3, True, None)
    assert polycab.callout() == "3.5C x 300 sq.mm Al XLPE ARM"
    kei = catalog.get("kei-lt-xlpe-al-3.5c-300-arm")
    assert kei.neutral_size_mm2 == 150


def test_three_and_a_half_core_rows_match_polycab_csv(catalog):
    rows = [r for r in read_csv("polycab_lt_ampacity.csv") if r["cores_in_weight_table"] == "3.5"]
    assert len(rows) == 73
    row = pick(rows, 7)
    spec = catalog.get(f"polycab-lt-xlpe-{row['material'].lower()}-3.5c-{row['size_mm2']}-{'arm' if row['armoured'] == 'True' else 'unarm'}")
    assert (spec.ampacity_ground, spec.ampacity_duct, spec.ampacity_air) == (float(row["ground_a"]), float(row["duct_a"]), float(row["air_a"]))


@pytest.mark.parametrize("over", [
    dict(reduced_neutral=True, cores=4),
    dict(neutral_size_mm2=150),
    dict(reduced_neutral=True, neutral_size_mm2=300),
    dict(ampacity_2x1c={"air": 10}),
])
def test_cable_core_rules_reject_inconsistent_specs(over):
    with pytest.raises(ValueError):
        ADAPTER.validate_python({**cable_fields(**over), "kind": "cable"})


def test_single_core_cable_keeps_both_circuit_ampacities(catalog):
    rows = {r["arrangement"]: r for r in read_csv("polycab_lt_ampacity.csv")
            if r["cores_in_weight_table"] == "1" and r["armoured"] == "True" and r["material"] == "Cu" and r["size_mm2"] == "10"}
    spec = catalog.get("polycab-lt-xlpe-cu-1c-10-arm")
    for arrangement, got in (("3x1c", (spec.ampacity_air, spec.ampacity_ground, spec.ampacity_duct)),
                             ("2x1c", (spec.ampacity_2x1c.air, spec.ampacity_2x1c.ground, spec.ampacity_2x1c.duct))):
        assert got == tuple(float(rows[arrangement][k]) for k in ("air_a", "ground_a", "duct_a"))
    assert spec.ampacity_2x1c.ground > spec.ampacity_ground
    multicore = catalog.get("polycab-lt-xlpe-cu-3c-25-arm")
    assert multicore.ampacity_2x1c is None


def test_no_cable_is_dropped_for_core_count_or_arrangement():
    report = (CATALOG / "REPORT.md").read_text()
    assert "3.5-core" not in report and "2x1c arrangement" not in report


def inverter_fields(**over):
    base = dict(
        id="x", manufacturer="m", model="m", source={"kind": "manual", "ref": "r"}, topology="central", ac_rated_kva=3000, ac_max_kva=3000,
        ac_voltage=600, phases=3, dc_max_voltage=1500, mppt_min=875, mppt_max=1300, mppt_count=1,
    )
    return {**base, **over}


def test_single_mppt_inverter_derives_per_mppt_values_from_totals():
    spec = ADAPTER.validate_python({**inverter_fields(max_dc_input_current=4178, dc_input_count=21), "kind": "inverter"})
    assert (spec.max_input_current_per_mppt, spec.inputs_per_mppt) == (4178, 21)
    assert spec.max_short_circuit_current_per_mppt is None


def test_multi_mppt_inverter_never_splits_totals():
    spec = ADAPTER.validate_python({**inverter_fields(mppt_count=2, max_dc_input_current=3997), "kind": "inverter"})
    assert spec.max_input_current_per_mppt is None and spec.max_dc_input_current == 3997


def test_inverter_needs_some_dc_current():
    with pytest.raises(ValueError, match="max_input_current_per_mppt or max_dc_input_current"):
        ADAPTER.validate_python({**inverter_fields(), "kind": "inverter"})


def test_central_inverters_imported_from_datasheet_totals(catalog):
    rows = {r["model"]: r for r in read_csv("inverter_datasheets.csv")}
    hv30 = catalog.get("sungrow-sg3125hv-30")
    assert (hv30.max_dc_input_current, hv30.max_dc_short_circuit_current, hv30.dc_input_count) == (3997, 10000, 18)
    assert hv30.mppt_count == int(rows["SG3125HV-30"]["mppt_count"]) and hv30.max_input_current_per_mppt is None
    mv20 = catalog.get("sungrow-sg3125hv-mv-20")
    assert mv20.topology == "central" and mv20.max_input_current_per_mppt == 4178


def test_1500v_gpv_fuse_ladder_matches_mersen_json(catalog):
    ladder = json.loads((EXTRACTED / "protection_ladders.json").read_text())["gpv_fuse_1500v"]
    fuses = [p for p in catalog.filter(kind="protection", manufacturer="Mersen") if p.device == "fuse_gpv"]
    assert sorted(f.rated_current for f in fuses) == sorted(float(a) for a in ladder["rated_current_a"])
    assert {(f.rated_voltage, f.breaking_capacity_ka) for f in fuses} == {(1500.0, 50.0)}
    assert catalog.get("mersen-hp15m10r").model == "HP15M10R"


def test_mv_switchgear_entries(catalog):
    vcbs = [p for p in catalog.filter(kind="protection") if p.device == "vcb"]
    assert {(v.rated_current, v.breaking_capacity_ka) for v in vcbs} == {
        (630, 16), (630, 20), (1250, 16), (1250, 20), (1250, 25), (1250, 31.5), (1250, 40),
        (1600, 25), (1600, 31.5), (1600, 40), (2000, 25), (2000, 31.5), (2000, 40), (2500, 25), (2500, 31.5), (2500, 40),
    }
    assert all(v.rated_voltage == 36000 and v.poles == 3 and v.short_time_duration_s == 3 for v in vcbs)
    rmu = catalog.get("generic-rmu-36kv-630a-25ka")
    assert (rmu.rated_current, rmu.breaking_capacity_ka, rmu.symbol) == (630, 25, "sw.rmu")
    ct = catalog.get("generic-ct-33kv-200-100-50-1-1a")
    assert ct.ratio == "200-100-50/1-1 A" and "0.5S" in ct.accuracy_class and "5P20" in ct.accuracy_class and ct.symbol == "tx.ct"
    la = catalog.get("abb-polim-d-24-ur30kv")
    assert (la.rated_voltage, la.nominal_discharge_current_ka, la.symbol) == (30000, 10, "prot.lightning_arrester")


def test_report_skip_counts_dropped_for_closed_gaps():
    report = (CATALOG / "REPORT.md").read_text()
    assert "datasheet gives DC input totals only" not in report.split("## Notes")[0]
    assert "| cable | 3.5-core" not in report

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
    fuses = [p for p in catalog.filter(kind="protection") if p.device == "fuse_gpv"]
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

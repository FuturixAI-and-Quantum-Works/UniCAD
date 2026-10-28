"""Catalog gaps of PLAN.md 11.1 that a datasheet could close: 36 kV fuse-switch, 1500 V ~500 A DC switch, equipment envelopes."""

import csv
from pathlib import Path

import pytest
from pydantic import ValidationError

from unicad.catalog import Catalog, load_catalog
from unicad.domain.catalog import Dimensions, ProtectionSpec

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "references" / "catalog-sources" / "extracted"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(ROOT / "catalog")


def test_36_kv_fuse_switch_comes_from_the_safeplus_f_module_page(catalog):
    f = catalog.get("abb-safeplus-36-f-module-200a")
    assert isinstance(f, ProtectionSpec) and f.device == "sfu" and f.symbol == "sw.switch_fuse"
    assert (f.rated_voltage, f.rated_current, f.poles, f.breaking_capacity_ka) == (36000, 200, 3, 20)
    assert "ABB_SafeRing_SafePlus_36kV_catalogue_1YVA000080_RevB.pdf p.13" in f.source.ref and "limited by the HV fuse-links" in f.source.ref
    assert [s.id for s in catalog.filter("protection") if isinstance(s, ProtectionSpec) and s.device == "sfu" and s.rated_voltage >= 33000] == [f.id]


def test_a_1500_v_dc_switch_of_about_500_a_exists_and_cites_the_otdc_m_pages(catalog):
    sw = catalog.get("abb-otdc500fv11")
    assert (sw.device, sw.rated_voltage, sw.rated_current, sw.poles) == ("dc_isolator", 1500, 500, 2)
    assert (sw.short_time_withstand_ka, sw.short_time_duration_s) == (10, 1)
    assert "p.46" in sw.source.ref and "technical-data table on pdf p.42" in sw.source.ref and "DC-PV2 rating is 500 A" in sw.source.ref
    big = sorted(s.rated_current for s in catalog.filter("protection") if isinstance(s, ProtectionSpec) and s.device == "dc_isolator" and s.rated_voltage >= 1500)
    assert big[-1] == 1000 and 500 in big and 400 in big


def test_every_otdc_m_row_is_catalogued_with_its_order_code(catalog):
    with open(EXTRACTED / "abb_otdc_switch_disconnectors.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["series"] == "M"]
    assert len(rows) == 11
    for r in rows:
        s = catalog.get(f"abb-{r['type'].lower()}")
        assert (s.rated_current, s.rated_voltage, s.poles) == (float(r["dc21b_a"]), 1500, int(r["poles"]))


def test_envelopes_of_an_inverter_station_and_a_bess_container(catalog):
    station = catalog.get("sungrow-sg3125hv-mv-20")
    assert station.dimensions == Dimensions(width=6058, height=2896, depth=2438)
    for id_ in ("sungrow-st5015kwh", "sungrow-st5015kwh-2500kw-2h", "sungrow-st5015kwh-1250kw-4h"):
        assert catalog.get(id_).dimensions == Dimensions(width=6058, height=2896, depth=2438)
    string = catalog.get("solis-s6-gc3p150k07-nv-nd")
    assert (string.dimensions.width, string.dimensions.height, string.dimensions.depth) == (1035.5, 869, 396)
    assert catalog.get("sungrow-sg110cx").dimensions is None


def test_dimensions_must_be_positive():
    with pytest.raises(ValidationError):
        Dimensions(width=0, height=1, depth=1)

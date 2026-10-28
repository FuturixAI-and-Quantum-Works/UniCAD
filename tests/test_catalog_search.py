"""catalog_search says where the catalog's range ends when a filter matches nothing (RVUNL run, RV-m5)."""

from pathlib import Path

import pytest

from unicad.catalog import Catalog, load_catalog
from unicad.mcp.tools.catalog import search_catalog

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def full() -> Catalog:
    return load_catalog(ROOT / "catalog")


def subset(full: Catalog, kind: str, lo: float, hi: float) -> Catalog:
    from unicad.catalog.store import power

    return Catalog(tuple(e for e in full.entries if e.kind == kind and lo <= (power(e) or 0) <= hi))


def test_module_above_the_range_names_the_largest_module(full):
    modules = subset(full, "module", 500, 635)
    r = search_catalog(modules, kind="module", min_power=650)
    assert r["total"] == 0 and r["rows"] == []
    assert "largest module is 635 Wp" in r["note"]
    assert r["range"] == {"kind": "module", "unit": "W", "min": 520.0, "max": 635.0, "count": len(modules)}
    assert r["nearest_below"]["power"] == 635 and r["nearest_above"] is None


def test_inverter_gap_names_both_neighbours(full):
    inverters = Catalog(tuple(e for e in full.entries if e.kind == "inverter" and e.id in {"sungrow-sg110cx", "sungrow-sg350hx", "huawei-sun2000-100ktl-m2"}))
    r = search_catalog(inverters, kind="inverter", min_power=120, max_power=200)
    assert r["total"] == 0
    assert r["nearest_below"]["power"] == 110 and r["nearest_above"]["power"] == 352
    assert "no inverter between 120 and 200 kVA" in r["note"] and "nearest below is 110 kVA" in r["note"] and "nearest above is 352 kVA" in r["note"]


def test_below_the_range_names_the_smallest(full):
    r = search_catalog(subset(full, "module", 500, 635), kind="module", max_power=100)
    assert "smallest module is 520 Wp" in r["note"] and r["nearest_above"]["power"] == 520


def test_manufacturer_with_no_part_in_range_still_reports_the_manufacturer_range(full):
    r = search_catalog(full, kind="module", manufacturer="waaree", min_power=10_000)
    assert r["total"] == 0 and "largest module is" in r["note"] and "Waaree" in r["note"]


def test_kind_without_a_power_rating_reports_the_count(full):
    r = search_catalog(full, kind="cable", text="no-such-cable-word")
    assert r["total"] == 0 and "cable" in r["note"] and str(len([e for e in full.entries if e.kind == "cable"])) in r["note"]
    assert "range" not in r


def test_matching_search_carries_no_note(full):
    r = search_catalog(full, kind="module", min_power=600)
    assert r["total"] > 0 and "note" not in r and "range" not in r


def test_mixed_kinds_name_each_kinds_range(full):
    r = search_catalog(full, min_power=1e9)
    assert r["total"] == 0
    assert "module" in r["note"] and "inverter" in r["note"] and "transformer" in r["note"]

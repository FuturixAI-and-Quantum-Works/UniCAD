import csv
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from unicad.catalog import Catalog, load_catalog
from unicad.domain.catalog import CableSpec, ProtectionSpec, TransformerSpec

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "references" / "catalog-sources" / "extracted"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(ROOT / "catalog")


def rows(name: str) -> list[dict[str, str]]:
    with open(EXTRACTED / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def transformer_fields(**over):
    base = dict(id="t", manufacturer="m", model="m", source={"kind": "manual", "ref": "r"}, rated_kva=8800, hv_kv=33, lv_kv=(0.66, 0.66), vector_group="YNd11d11", impedance_pct=7)
    return {**base, **over}


def test_ht_cables_match_havells_rows(catalog):
    src = rows("havells_ht_cables.csv")
    assert {(r["voltage_grade"], r["cores"]) for r in src} == {("6.35/11 kV", "1"), ("6.35/11 kV", "3"), ("19/33 kV", "1"), ("19/33 kV", "3")}
    hv = [c for c in catalog.filter(kind="cable") if c.service == "mv_ac"]
    assert len(hv) == sum(2 for r in src if 50 <= float(r["size_mm2"]) <= (400 if r["cores"] == "3" else 630))
    r = next(r for r in src if (r["voltage_grade"], r["cores"], r["size_mm2"]) == ("19/33 kV", "3", "120"))
    al = catalog.get("havells-ht-xlpe-al-3c-120-33kv-arm")
    assert isinstance(al, CableSpec) and al.armoured and al.insulation == "xlpe" and al.voltage_grade == "19/33 kV"
    assert (al.r_ohm_per_km_90c, al.x_ohm_per_km) == (float(r["r_ac90_al"]), float(r["x_ohm_km"]))
    assert (al.ampacity_ground, al.ampacity_duct, al.ampacity_air) == (float(r["al_ground_a"]), float(r["al_duct_a"]), float(r["al_air_a"]))
    cu = catalog.get("havells-ht-xlpe-cu-3c-120-33kv-arm")
    assert cu.r_ohm_per_km_90c == float(r["r_ac90_cu"]) and cu.ampacity_ground == float(r["cu_ground_a"])
    assert "Havells_Industrial_Cables_Range_Catalogue.pdf" in al.source.ref and "havells_ht_cables.csv row" in al.source.ref


def test_ht_cable_values_are_physical(catalog):
    for c in catalog.filter(kind="cable"):
        if c.service != "mv_ac":
            continue
        assert c.voltage_grade in ("6.35/11 kV", "19/33 kV") and c.cores in (1, 3)
        assert c.ampacity_duct < c.ampacity_ground < c.ampacity_air
        assert c.r_ohm_per_km_90c > 0 and 0.08 < c.x_ohm_per_km < 0.2


def test_ot_ac_isolators_match_abb_rows(catalog):
    src = rows("abb_ot_switch_disconnectors.csv")
    assert len(src) == 22
    for r in src:
        spec = catalog.get(f"abb-{r['type'].lower()}")
        assert isinstance(spec, ProtectionSpec) and spec.device == "ac_isolator" and spec.symbol == "sw.isolator"
        assert (spec.rated_current, spec.poles, spec.rated_voltage) == (float(r["ac22a_a"]), int(r["poles"]), 415)
        assert spec.breaking_capacity_ka is None
    assert catalog.get("abb-ot1000e04p").short_time_withstand_ka == 50
    assert catalog.get("abb-ot1600e04p").short_time_withstand_ka is None


def test_otdc_dc_isolators_match_abb_rows(catalog):
    src = rows("abb_otdc_switch_disconnectors.csv")
    assert {(r["voltage_vdc"], r["poles"]) for r in src} == {("1000", "2"), ("1000", "4"), ("1500", "2"), ("1500", "4")}
    for r in src:
        spec = catalog.get(f"abb-{r['type'].lower()}")
        assert spec.device == "dc_isolator" and spec.symbol == "dc.isolator"
        assert (spec.rated_current, spec.rated_voltage, spec.poles) == (float(r["dc21b_a"]), float(r["voltage_vdc"]), int(r["poles"]))
    assert "DC-PV2 rating is 100 A" in catalog.get("abb-otdc250gv11").source.ref


def test_ac_spd_pt_and_arrester_entries(catalog):
    for id, poles, uc in (("finder-7p.23.8.275.x020", 3, 275), ("finder-7p.25.8.275.x020", 4, 275), ("dehn-dg-se-ci-440-fm", 1, 440)):
        spd = catalog.get(id)
        assert (spd.device, spd.spd_type, spd.poles, spd.rated_voltage, spd.symbol) == ("spd_ac", "2", poles, uc, "prot.spd_ac")
    assert catalog.get("finder-7p.23.9.500.x015").rated_voltage == 1500
    pt = catalog.get("generic-pt-33kv-wbsedcl-0.5-3p-100va")
    assert (pt.device, pt.rated_voltage, pt.symbol) == ("pt", 36000, "tx.pt")
    assert "0.5" in pt.accuracy_class and "3P" in pt.accuracy_class and pt.burden_va == 100
    la = catalog.get("generic-la-36kv-ur30-20ka-counter")
    assert (la.rated_voltage, la.nominal_discharge_current_ka, la.surge_counter) == (30000, 20, True)
    assert catalog.get("abb-polim-d-24-ur30kv").surge_counter is None


def test_small_33kv_transformers_follow_is1180_table_1(catalog):
    for kva in (16, 25, 63):
        t = catalog.get(f"is1180-33kv-{kva}kva-dyn11")
        assert (t.hv_kv, t.lv_kv, t.vector_group, t.impedance_pct) == (33, (0.433,), "Dyn11", 4.5)
        assert "Amd 4 Table 1" in t.source.ref


def test_idt_citations_cover_the_values_the_fixture_borrows():
    cited = {r["rating_mva"]: r for r in rows("idt_impedance_citations.csv")}
    r = cited["5.5"]
    assert (r["vector_group"], r["impedance_hv_to_lv1_lv2_pct"], r["lv1_lv2_pct_min"], r["tap_changer"], r["tap_range_pct"], r["tap_step_pct"]) == ("YNd11d11", "7", "11", "octc", "-10..10", "2.5")
    assert (cited["2.7"]["impedance_hv_to_lv1_lv2_pct"], cited["2.7"]["vector_group"]) == ("6.25", "Dyn11yn11")


def test_transformer_spec_additions_are_validated():
    spec = TransformerSpec(**transformer_fields(winding_kva=(4400, 4400), tap_changer="octc", tap_range_pct=(-10, 10), tap_step_pct=2.5))
    assert spec.symbol == "tx.power_3w" and spec.winding_kva == (4400, 4400)
    assert TransformerSpec(**transformer_fields()).winding_kva is None
    for over, match in (
        ({"winding_kva": (4400,)}, "one rating per lv_kv winding"),
        ({"winding_kva": (5000, 5000)}, "more than rated_kva"),
        ({"tap_range_pct": (-10, 10)}, "needs tap_changer and tap_step_pct"),
        ({"tap_changer": "octc", "tap_step_pct": 2.5, "tap_range_pct": (2, 10)}, "principal tap inside"),
        ({"lv_lv_impedance_min_pct": 0}, "greater than 0"),
    ):
        with pytest.raises(ValidationError, match=match):
            TransformerSpec(**transformer_fields(**over))


def test_catalog_json_for_new_kinds_round_trips(catalog):
    raw = json.loads((ROOT / "catalog" / "protection.json").read_text(encoding="utf-8"))
    assert next(e for e in raw if e["id"] == "generic-la-36kv-ur30-20ka-counter")["surge_counter"] is True

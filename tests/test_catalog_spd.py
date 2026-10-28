"""SPD ratings: In and Imax are different numbers and the drawings print both (RVUNL run, RV-m6)."""

import json
from pathlib import Path

import pytest

from unicad.catalog import Catalog, load_catalog
from unicad.domain.catalog import ProtectionSpec
from unicad.drafting.sld import annotate

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "references" / "catalog-sources" / "extracted"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(ROOT / "catalog")


def test_rvunl_spd_keeps_in_20_ka_and_gains_imax_40_ka_from_the_finder_page(catalog):
    spd = catalog.get("finder-7p.25.8.275.x020")
    assert spd.nominal_discharge_current_ka == 20 and spd.max_discharge_current_ka == 40
    assert "Finder_S7P_SPD_catalogue.pdf p.9" in spd.source.ref


def test_every_finder_spd_carries_in_and_imax_as_the_catalogue_page_gives_them(catalog):
    ac = json.loads((EXTRACTED / "protection_phase2.json").read_text(encoding="utf-8"))["spd_ac_finder"]["variants"]
    dc = json.loads((EXTRACTED / "protection_ladders.json").read_text(encoding="utf-8"))["spd_pv_dc"]["variants"]
    for v in ac:
        s = catalog.get("finder-" + v["code"].lower())
        assert (s.nominal_discharge_current_ka, s.max_discharge_current_ka) == (v["in_ka"], v["imax_ka"])
    for v in dc:
        s = catalog.get("finder-" + v["code"].lower())
        assert (s.nominal_discharge_current_ka, s.max_discharge_current_ka) == (v["in_ka"], v["imax_ka"])


def test_imax_never_below_in():
    with pytest.raises(ValueError, match="Imax"):
        ProtectionSpec(id="x", manufacturer="m", model="m", source={"kind": "manual", "ref": "r"}, device="spd_ac", rated_voltage=275, poles=3,
                       nominal_discharge_current_ka=20, max_discharge_current_ka=10)


@pytest.mark.parametrize("spd_id, text", [
    ("finder-7p.25.8.275.x020", "4P, TYPE 2, 275 V, In/Imax 20/40 kA"),
    ("finder-7p.23.9.500.x015", "3P, TYPE 2, 1.5 kV DC, In/Imax 15/40 kA"),
])
def test_sld_text_prints_both_discharge_currents(catalog, spd_id, text):
    spec = catalog.get(spd_id)
    assert annotate.PROTECTION_TEXT[spec.device][1](spec) == text


def test_spd_without_imax_prints_only_what_the_datasheet_gave(catalog):
    spec = catalog.get("finder-7p.25.8.275.x020").model_copy(update={"max_discharge_current_ka": None})
    assert annotate.PROTECTION_TEXT["spd_ac"][1](spec) == "4P, TYPE 2, 275 V, In 20 kA"

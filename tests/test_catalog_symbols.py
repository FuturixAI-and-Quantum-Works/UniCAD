import pytest

from unicad.catalog.store import load_catalog
from unicad.domain.catalog import PROTECTION_SYMBOLS, InverterSpec, MeterSpec
from unicad.drafting.symbols import REGISTRY


def test_every_catalog_entry_draws_with_a_registered_symbol():
    missing = {(spec.id, spec.symbol) for spec in load_catalog().entries if spec.symbol not in REGISTRY}
    assert not missing


@pytest.mark.parametrize(
    "keys",
    [
        PROTECTION_SYMBOLS.values(),
        [InverterSpec.symbol_for({"topology": t}) for t in ("string", "central", "micro", "hybrid")],
        [MeterSpec.symbol_for({"function": f}) for f in ("net", "generation", "abt", "check")],
    ],
)
def test_derived_symbol_tables_only_name_registered_symbols(keys):
    assert set(keys) <= REGISTRY.keys()

"""Wave-5 follow-ups: SLD system parameters with the export limit and PPC, the stacked overall SLD aux feeder orientation, BESS units on the LPS sheet."""

import json
from pathlib import Path

from unicad.catalog.store import load_catalog
from unicad.drafting.sld.annotate import system_parameters
from unicad.store.files import parse_project

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog()


def _utility(poi_attributes: dict):
    data = json.loads((ROOT / "fixtures" / "projects" / "utility_50mwac.json").read_text())
    for n in data["network"]["nodes"]:
        if n["id"] == "poi":
            n["attributes"] = {**n.get("attributes", {}), **poi_attributes}
    return parse_project(json.dumps(data), CATALOG)


def test_sld_system_parameters_show_the_export_limit_and_the_ppc_only_when_set():
    rows = dict(system_parameters(_utility({}), CATALOG))
    assert "EXPORT LIMIT" not in rows and "EXPORT CONTROL" not in rows
    rows = dict(system_parameters(_utility({"export_limit_kva": 50000.0}), CATALOG))
    assert rows["EXPORT LIMIT"] == "50.000 MW AT POI-01" and "EXPORT CONTROL" not in rows
    rows = dict(system_parameters(_utility({"export_limit_kva": 50000.0, "export_control": "ppc"}), CATALOG))
    assert rows["EXPORT CONTROL"] == "POWER PLANT CONTROLLER (PPC) AT POI-01"
    assert list(rows).index("EXPORT LIMIT") < list(rows).index("GRID CONNECTION")

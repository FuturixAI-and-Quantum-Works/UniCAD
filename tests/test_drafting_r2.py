"""Drafting defects of verifier run 6 (R6-m9, R6-m3, R5-m4, R6-m2, R6-m5, R6-m7, n21, m5)."""

import json
from pathlib import Path

import pytest

from unicad.catalog.store import load_catalog
from unicad.domain.network import NodeKind
from unicad.drafting.sld import annotate
from unicad.drafting.string_diagram import plan_types
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_catalog()


def rooftop_with_isolator_ways(ways: int, spec: str = "abb-otdc100gv22"):
    data = json.loads((ROOT / "fixtures" / "projects" / "rooftop_500kwp.json").read_text(encoding="utf-8"))
    for n in data["network"]["nodes"]:
        if n["id"] == "dciso":
            n["spec"] = spec
            n["attributes"] = {"isolator_ways": ways}
    return parse_project(json.dumps(data), CATALOG)


def test_r6_m9_sld_shows_every_four_pole_isolator_the_bom_counts():
    p = rooftop_with_isolator_ways(10)
    node = p.network.node("dciso")
    texts = annotate.node_texts(p.network, node, CATALOG)
    assert texts["RATING"].startswith("5 x ") and "4P" in texts["RATING"]


def test_r6_m9_a_single_circuit_isolator_keeps_its_plain_rating():
    p = rooftop_with_isolator_ways(1, "abb-otdc100gv11")
    texts = annotate.node_texts(p.network, p.network.node("dciso"), CATALOG)
    assert " x " not in texts["RATING"].split(",")[0]


def test_r6_m9_string_diagram_lists_the_isolator_devices():
    p = rooftop_with_isolator_ways(10)
    types, _ = plan_types(p, CATALOG)
    devices = [d for t in types for d in t.devices]
    assert any("DC-ISO" in d and "5 x 4P" in d for d in devices), devices

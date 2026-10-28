"""Hybrid earthing order: switchyard_m belongs to the design basis, set before layout_place and network_autobuild, not at earthing_design."""

import gzip

from test_design import CATALOG
from test_sets import ROOT
from unicad.design.placement import YARD_UNKNOWN, place_equipment
from unicad.mcp.instructions import INSTRUCTIONS
from unicad.mcp.prompts import BESS, UTILITY
from unicad.store import parse_project


def kutch(with_yard: bool):
    p = parse_project(gzip.decompress((ROOT / "fixtures" / "projects" / "kutch_hybrid.json.gz").read_bytes()).decode("utf-8"), CATALOG)
    e = p.design_basis.earthing
    assert e is not None and e.switchyard_m is not None
    if with_yard:
        return p
    return p.model_copy(update={"design_basis": p.design_basis.model_copy(update={"earthing": e.model_copy(update={"switchyard_m": None})})})


def test_a_hybrid_with_the_yard_size_in_the_design_basis_needs_no_rebuild_note():
    _, notes = place_equipment(kutch(True), CATALOG)
    assert YARD_UNKNOWN not in notes


def test_a_hybrid_without_it_is_told_to_set_it_before_layout_and_autobuild():
    _, notes = place_equipment(kutch(False), CATALOG)
    text = next(n for n in notes if n == YARD_UNKNOWN)
    assert "before layout_place and network_autobuild" in text


def test_the_instructions_and_prompts_put_switchyard_m_in_the_design_basis_step():
    assert INSTRUCTIONS.index("switchyard_m") < INSTRUCTIONS.index("3. site_import")
    assert UTILITY.index("switchyard_m") < UTILITY.index("6. layout_place")
    assert BESS.index("switchyard_m") < BESS.index("5. network_autobuild")

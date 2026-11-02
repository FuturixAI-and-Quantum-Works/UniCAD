"""t3-draw: earthing note 2 states what was checked for the plant at hand, the layout roads note, the RVUNL SLD fill and the
catalogued data logger on the SLD and BOM."""

from pathlib import Path

import pytest
from test_design_rvunl import CATALOG

from unicad.domain.project import Project
from unicad.drafting.earthing_plan import earthing_notes
from unicad.earthing import design_earthing

DATA = Path(__file__).resolve().parent / "data"


def bench2() -> Project:
    return Project.model_validate_json((DATA / "rvunl_heerapura_bench2.json").read_text(encoding="utf-8"))


# ---------------- earthing note 2 (item 2) ----------------


def test_lt_plant_earthing_note_names_the_lt_check_not_a_switchyard_grid():
    d = design_earthing(bench2())
    assert d.lt_plant and d.grid is None
    notes = earthing_notes(d)
    assert notes[1].startswith("EARTHING INSTALLATION SHALL CONFORM TO IS 3043."), notes[1]
    assert "SWITCHYARD EARTH GRID" not in notes[1] and "CHECKED AS PER IEEE 80" not in notes[1], notes[1]
    assert "AUTOMATIC DISCONNECTION OF SUPPLY" in notes[1] and "IEC 60364-4-41" in notes[1], notes[1]
    assert not [n for n in notes if "SWITCHYARD EARTH GRID CHECKED" in n]
    assert sum("LT PLANT" in n and "IEEE 80" in n for n in notes) == 1, "said once, not again as an assumption"


@pytest.fixture(scope="module")
def kutch():
    import gzip

    from unicad.store import parse_project

    root = Path(__file__).resolve().parents[1]
    return parse_project(gzip.decompress((root / "fixtures" / "projects" / "kutch_hybrid.json.gz").read_bytes()).decode("utf-8"), CATALOG)


def test_mv_switchyard_earthing_note_states_the_ieee80_result(kutch):
    d = design_earthing(kutch, catalog=CATALOG)
    assert d.grid is not None
    note = earthing_notes(d)[1]
    assert note.startswith("EARTHING INSTALLATION SHALL CONFORM TO IS 3043. MV SWITCHYARD EARTH GRID"), note
    assert f"{d.grid_spacing_m:g} M MESH" in note and "IEEE 80" in note, note
    ok = d.grid.gpr <= d.grid.e_touch or (d.grid.e_mesh <= d.grid.e_touch and d.grid.e_s <= d.grid.e_step)
    assert ("WITHIN THE TOLERABLE TOUCH AND STEP VOLTAGES" in note) == ok, note

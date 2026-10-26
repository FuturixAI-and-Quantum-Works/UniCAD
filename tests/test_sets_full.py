"""Complete drawing sets: drawing-number blocks, the register against the set it lists, kinds a project cannot draw yet."""

from pathlib import Path

import ezdxf
import pytest

from test_design import CATALOG
from test_sets import PROFILE, fixture
from unicad.domain.project import SheetKind
from unicad.drafting.sld import SldOptions
from unicad.earthing.samples import pune
from unicad.sets import ALL_KINDS, SldBuilder, plan_set, render_set
from unicad.sets.numbering import Numberer


@pytest.fixture(scope="module")
def roof():
    return pune()[0]


@pytest.fixture(scope="module")
def roof_set(roof, tmp_path_factory):
    out = tmp_path_factory.mktemp("roof")
    return out, render_set(roof.model_copy(update={"sheets": ()}), out, kinds=ALL_KINDS, formats=("dxf",), profile=PROFILE, catalog=CATALOG)


def _texts(path: Path) -> set[str]:
    return {e.dxf.text for e in ezdxf.readfile(path).modelspace().query("TEXT")}


def test_fresh_numbers_come_from_one_block_per_kind_and_lps_follows_earthing(roof):
    n = Numberer.for_project(roof.model_copy(update={"sheets": ()}), PROFILE)
    got = {k: n.fresh(k) for k in ("register", "sld", "layout", "earthing", "lps", "strings", "cable_schedule", "bom")}
    assert {k: v.rsplit("-", 1)[1] for k, v in got.items()} == {
        "register": "000", "sld": "001", "layout": "101", "earthing": "301", "lps": "302", "strings": "401", "cable_schedule": "501", "bom": "601",
    }  # fmt: skip
    assert n.fresh("sld").endswith("-002") and n.fresh("lps").endswith("-303")
    alone = Numberer.for_project(roof.model_copy(update={"sheets": ()}), PROFILE)
    assert alone.fresh("lps").endswith("-351")


def test_fresh_numbers_skip_the_project_numbers_and_use_a_kind_discipline_code(roof):
    used = Numberer.for_project(roof, PROFILE)
    assert {s.number for s in roof.sheets} <= used.used and used.fresh("sld") not in {s.number for s in roof.sheets}
    civil = PROFILE.model_copy(update={"discipline_codes": {**PROFILE.discipline_codes, "layout": "CE"}})
    n = Numberer.for_project(roof.model_copy(update={"sheets": ()}), civil)
    assert n.fresh("layout").endswith("-CE-101") and n.fresh("sld").endswith("-EE-001")


def test_the_complete_set_has_every_kind_with_unique_numbers_in_block_order(roof_set):
    _, index = roof_set
    kinds = [s["kind"] for s in index.sheets]
    assert list(dict.fromkeys(kinds)) == list(ALL_KINDS) and index.warnings == ()
    numbers = [s["number"] for s in index.sheets]
    assert len(set(numbers)) == len(numbers) and numbers == sorted(numbers)
    assert numbers[0].endswith("-000") and index.sheets[0]["sheet_kind"] == SheetKind.REGISTER.value
    for kind in set(kinds):
        mine = [s for s in index.sheets if s["kind"] == kind]
        assert [s["kind_sheet_of"] for s in mine] == [f"{i} of {len(mine)}" for i in range(1, len(mine) + 1)]
    scales = {s["kind"]: s["drawing_scale"] for s in index.sheets}
    assert all(scales[k].startswith("1:") for k in ("layout", "earthing", "lps")) and scales["sld"] == "NTS"


def test_the_register_lists_every_sheet_with_its_number_title_paper_and_scale(roof_set):
    out, index = roof_set
    reg = index.sheets[0]
    rows = reg["rows"]
    assert [(r["number"], r["kind"], r["paper"], r["scale"]) for r in rows] == [
        (s["number"], s["kind"], s["paper"], s["drawing_scale"]) for s in index.sheets
    ]
    texts = _texts(out / reg["files"]["dxf"])
    assert {s["number"] for s in index.sheets} <= texts
    assert "TOTAL SHEETS IN SET" in texts and str(len(index.sheets)) in texts
    assert {"DRAWING REGISTER", "SINGLE LINE DIAGRAMS", "EARTHING LAYOUT", "STRING WIRING DIAGRAMS", "BILL OF MATERIALS"} <= texts


def test_each_sheet_title_block_carries_its_set_number_and_sheet_of_its_kind(roof_set):
    out, index = roof_set
    for s in index.sheets:
        texts = _texts(out / s["files"]["dxf"])
        assert s["number"] in texts, s["number"]
        assert s["kind_sheet_of"] in texts, (s["number"], s["kind_sheet_of"])


def test_kinds_the_project_cannot_draw_are_skipped_with_the_reason(tmp_path):
    p = fixture("utility_50mwac")
    index = render_set(p, tmp_path, kinds=("register", "sld", "layout", "earthing", "lps"), formats=("dxf",), profile=PROFILE, catalog=CATALOG)
    assert [s["kind"] for s in index.sheets] == ["register", "sld", "sld"]
    assert any(w.startswith("layout: not drawn: ") and ("layout_place" in w or "site_import" in w) for w in index.warnings)
    assert any(w.startswith("earthing: not drawn: ") and "earthing inputs" in w for w in index.warnings)
    assert [r["number"] for r in index.sheets[0]["rows"]] == [s["number"] for s in index.sheets]


def test_stored_numbers_are_reused_and_extra_pages_take_fresh_block_numbers():
    p = fixture("rooftop_500kwp")
    planned = dict(plan_set(p, ("sld", "strings", "cable_schedule"), PROFILE, CATALOG))
    stored = {s.kind: s.number for s in p.sheets}
    assert planned["sld"][0].number == stored[SheetKind.SLD]
    assert planned["strings"][0].number == stored[SheetKind.STRING_DIAGRAM]
    schedule = [s.number for s in planned["cable_schedule"]]
    assert schedule[0] == stored[SheetKind.CABLE_SCHEDULE] and all(n.rsplit("-", 1)[1].startswith("5") for n in schedule[1:])


def test_split_sld_pieces_are_numbered_right_after_the_sheet_they_come_from():
    p = fixture("utility_50mwac").model_copy(update={"sheets": ()})
    sheets = SldBuilder(SldOptions(min_scale=2.0, max_scale=3.0)).plan(p, PROFILE, CATALOG, Numberer.for_project(p, PROFILE))
    seqs = [int(s.number.rsplit("-", 1)[1]) for s in sheets]
    assert len(sheets) > 2 and seqs == list(range(1, len(sheets) + 1))

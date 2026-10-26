"""Complete drawing sets: drawing-number blocks, the register against the set it lists, kinds a project cannot draw yet."""

import re
from pathlib import Path

import ezdxf
import pytest

from test_design import CATALOG
from test_sets import PROFILE, fixture
from unicad.domain.project import Sheet, SheetKind
from unicad.drafting.sheets.model import Numbering
from unicad.drafting.sld import SldOptions
from unicad.earthing.samples import pune
from unicad.sets import ALL_KINDS, RegisterBuilder, SldBuilder, plan_set, render_set
from unicad.sets.numbering import Numberer
from unicad.sets.render import load_profile


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
    assert {s.number for s in roof.sheets} <= set(used.issued) and used.fresh("sld") not in {s.number for s in roof.sheets}
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


ACME = load_profile("acme")


def test_a_discipline_start_moves_every_kind_block_with_it(roof):
    p = PROFILE.model_copy(update={"numbering": Numbering(per_discipline=True, start={"electrical": 1001, "civil": 101}), "discipline_codes": {**PROFILE.discipline_codes, "civil": "CE"}})
    n = Numberer.for_project(roof.model_copy(update={"sheets": ()}), p)
    got = {k: n.fresh(k).rsplit("-", 1)[1] for k in ("register", "sld", "layout", "earthing", "lps", "strings", "cable_schedule", "bom", "civil")}
    assert got == {
        "register": "1000", "sld": "1001", "layout": "1101", "earthing": "1301", "lps": "1302", "strings": "1401", "cable_schedule": "1501", "bom": "1601", "civil": "101",
    }  # fmt: skip
    without = PROFILE.model_copy(update={"numbering": Numbering(per_discipline=False, start={"electrical": 1001})})
    assert Numberer.for_project(roof.model_copy(update={"sheets": ()}), without).fresh("layout").endswith("-101")


@pytest.fixture(scope="module")
def acme_set(roof, tmp_path_factory):
    out = tmp_path_factory.mktemp("acme")
    return out, render_set(roof.model_copy(update={"sheets": (), "company_profile": "acme"}), out, kinds=ALL_KINDS, formats=("dxf",), profile=ACME, catalog=CATALOG)


def test_the_acme_set_numbers_every_kind_from_the_same_blocks_under_its_pattern(acme_set):
    _, index = acme_set
    numbers = [s["number"] for s in index.sheets]
    assert all(re.fullmatch(r"ACME/[^/]+/[^/]+/EL/\d{3}", n) for n in numbers), numbers
    first = {}
    for s in index.sheets:
        first.setdefault(s["kind"], s["number"].rsplit("/", 1)[1])
    assert first == {"register": "000", "sld": "001", "layout": "101", "earthing": "301", "lps": "302", "strings": "401", "cable_schedule": "501", "bom": "601"}


def test_the_company_layer_map_applies_to_every_sheet_kind(acme_set):
    out, index = acme_set
    moved = set()
    for s in index.sheets:
        doc = ezdxf.readfile(out / s["files"]["dxf"])
        layers = {e.dxf.layer for space in (doc.modelspace(), *doc.blocks) for e in space}
        assert not layers & set(ACME.layer_map), (s["kind"], layers & set(ACME.layer_map))
        if layers & {spec.name for spec in ACME.layer_map.values()}:
            moved.add(s["kind"])
    assert moved - {"sld"}, moved


THAR_SHEETS = (  # the 34 sheets of drawings_render(kinds=[every kind but the register]) in close-e2e-1 scenario B
    ('sld', 'sld', 'OVERALL SINGLE LINE DIAGRAM (PART 1 OF 4)', 'A0', 'NTS'),
    ('sld', 'sld_detail', 'OVERALL SINGLE LINE DIAGRAM (PART 2 OF 4)', 'A0', 'NTS'),
    ('sld', 'sld_detail', 'OVERALL SINGLE LINE DIAGRAM (PART 3 OF 4)', 'A0', 'NTS'),
    ('sld', 'sld_detail', 'OVERALL SINGLE LINE DIAGRAM (PART 4 OF 4)', 'A3', 'NTS'),
    ('sld', 'sld_detail', 'TYPICAL STRING INVERTER BLOCK (20 X 352 kVA) SLD (BLOCK-01-01)', 'A1', 'NTS'),
    ('sld', 'sld_detail', 'TYPICAL STRING INVERTER BLOCK (19 X 352 kVA) SLD (BLOCK-01-02)', 'A2', 'NTS'),
    ('sld', 'sld_detail', 'TYPICAL STRING INVERTER BLOCK (19 X 352 kVA) SLD (BLOCK-01-03)', 'A2', 'NTS'),
    ('sld', 'sld_detail', 'TYPICAL STRING INVERTER BLOCK (20 X 352 kVA) SLD (BLOCK-01-04)', 'A1', 'NTS'),
    ('sld', 'sld_detail', 'TYPICAL STRING INVERTER BLOCK (19 X 352 kVA) SLD (BLOCK-01-05)', 'A2', 'NTS'),
    ('sld', 'sld_detail', 'TYPICAL STRING INVERTER BLOCK (19 X 352 kVA) SLD (BLOCK-01-06)', 'A2', 'NTS'),
    ('layout', 'layout', 'SOLAR PV ARRAY LAYOUT', 'A1', '1:2500'),
    ('earthing', 'earthing', 'EARTHING LAYOUT', 'A1', '1:2500'),
    ('lps', 'lps', 'LIGHTNING ARRESTER (LA) LAYOUT', 'A1', '1:2500'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-01-01 .. INV-01-19', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-01-20', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-02-01 .. INV-02-18', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-02-19', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-03-01 .. INV-03-18', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-03-19', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-04-01 .. INV-04-19', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-04-20', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-05-01 .. INV-05-18', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-05-19', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-06-01 .. INV-06-18', 'A1', 'NTS'),
    ('strings', 'string_diagram', 'STRING WIRING DIAGRAM : INVERTER INV-06-19', 'A1', 'NTS'),
    ('cable_schedule', 'cable_schedule', 'DC CABLE SCHEDULE (PART 1 OF 4)', 'A3', 'NTS'),
    ('cable_schedule', 'cable_schedule', 'DC CABLE SCHEDULE (PART 2 OF 4)', 'A3', 'NTS'),
    ('cable_schedule', 'cable_schedule', 'DC CABLE SCHEDULE (PART 3 OF 4)', 'A3', 'NTS'),
    ('cable_schedule', 'cable_schedule', 'DC CABLE SCHEDULE (PART 4 OF 4)', 'A3', 'NTS'),
    ('cable_schedule', 'cable_schedule', 'AC AND MV CABLE SCHEDULE (PART 1 OF 3)', 'A3', 'NTS'),
    ('cable_schedule', 'cable_schedule', 'AC AND MV CABLE SCHEDULE (PART 2 OF 3)', 'A3', 'NTS'),
    ('cable_schedule', 'cable_schedule', 'AC AND MV CABLE SCHEDULE (PART 3 OF 3)', 'A3', 'NTS'),
    ('bom', 'bom', 'BILL OF MATERIALS (PART 1 OF 2)', 'A3', 'NTS'),
    ('bom', 'bom', 'BILL OF MATERIALS (PART 2 OF 2)', 'A3', 'NTS'),
)



def test_the_register_of_a_34_sheet_utility_set_draws_the_pages_it_planned():
    """close-e2e-1 B1: drawings_render(['all']) on the 40 MWac project failed with 'planned 2 register pages, drew 1'."""
    project = fixture("utility_50mwac").model_copy(update={"sheets": (), "revisions": ()})
    numbers = Numberer.for_project(project, PROFILE)
    others = [Sheet(id=f"s{i}", number=numbers.fresh(k), kind=SheetKind(sk), title=t, paper=p, scale="NTS")
              for i, (k, sk, t, p, _) in enumerate(THAR_SHEETS)]  # fmt: skip
    owner = {s.id: k for s, (k, *_) in zip(others, THAR_SHEETS)}
    planned = RegisterBuilder().plan(project, PROFILE, CATALOG, numbers, others, kinds=owner)
    drawn = [s.model_copy(update={"scale": sc}) for s, (*_, sc) in zip(others, THAR_SHEETS)]
    built = RegisterBuilder().build(project, planned, [*planned, *drawn], PROFILE, CATALOG, kinds=owner | {s.id: "register" for s in planned})
    listed = [r["number"] for r in built[0].meta["rows"] if r["kind"] == "register"]
    assert listed == [b.sheet.number for b in built] and len(built) == len(planned)
    assert [r["number"] for r in built[0].meta["rows"] if r["kind"] != "register"] == [s.number for s in drawn]
    if len(built) > 1:
        assert [b.sheet.title for b in built] == [f"DRAWING REGISTER (PART {k} OF {len(built)})" for k in range(1, len(built) + 1)]


def test_a_register_planned_on_more_pages_than_it_needs_drops_the_spare_pages():
    project = fixture("utility_50mwac").model_copy(update={"sheets": (), "revisions": ()})
    numbers = Numberer.for_project(project, PROFILE)
    one = RegisterBuilder().plan(project, PROFILE, CATALOG, numbers)
    two = (one[0].model_copy(update={"id": "register_1", "title": "DRAWING REGISTER (PART 1 OF 2)"}),
           one[0].model_copy(update={"id": "register_2", "number": numbers.fresh("register"), "title": "DRAWING REGISTER (PART 2 OF 2)"}))  # fmt: skip
    built = RegisterBuilder().build(project, two, two, PROFILE, CATALOG)
    assert [(b.sheet.id, b.sheet.number, b.sheet.title) for b in built] == [("register", one[0].number, "DRAWING REGISTER")]
    assert [(r["number"], r["title"]) for r in built[0].meta["rows"]] == [(one[0].number, "DRAWING REGISTER")]

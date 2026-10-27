import os
import subprocess
from datetime import date
from pathlib import Path

import ezdxf
from ezdxf import bbox
import pymupdf
import pytest
from pydantic import ValidationError

from unicad.drafting.sheets import samples
from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.model import (
    CompanyProfile,
    IssuedFor,
    LayerRole,
    PaperSize,
    Revision,
    SheetSpec,
)
from unicad.drafting.sheets.render import render_sheet
from unicad.drafting.sheets.text import text_width

ROOT = Path(__file__).resolve().parent.parent
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))

PAPERS = list(PaperSize)
SPECS = {"utility": samples.utility_100mwac, "rooftop": samples.rooftop_500kwp}


@pytest.fixture(scope="module")
def profile() -> CompanyProfile:
    return CompanyProfile.load(ROOT / "profiles" / "default.json")


def render(spec: SheetSpec, profile: CompanyProfile):
    doc = ezdxf.new("R2018", setup=["linetypes"])
    return doc, render_sheet(doc, spec, profile)


def stress_spec(paper: PaperSize) -> SheetSpec:
    base = samples.utility_100mwac(paper)
    long = "VERY LONG UNBROKEN-" + "X" * 80
    tb = base.title_block.model_copy(
        update={
            "client": "A MUCH LONGER OWNER NAME " * 6,
            "project_title": ("EXTREMELY DETAILED PROJECT TITLE " * 20).strip(),
            "drawing_title": long,
            "drawing_number": "UDEMO-" + "9" * 60,
            "location": long,
            "loa_no": long,
            "drawn_by": long,
            "scale": "1:" + "5" * 40,
        }
    )
    return base.model_copy(
        update={
            "title_block": tb,
            "legend": tuple((f"category.some_long_symbol_key_{i}", "LEGEND TEXT " * 12) for i in range(40)),
            "notes": tuple(f"NOTE {i}: " + "SOME LONG NOTE TEXT " * 15 for i in range(30)),
            "parameters": tuple((f"PARAMETER LABEL NUMBER {i} " * 3, "VALUE " * 20) for i in range(30)),
            "revisions": tuple(
                Revision(rev=f"{i:02d}", date=date(2026, 1, 1), description="CHANGE " * 30)
                for i in range(25)
            ),
        }
    )


def text_box(e) -> Region:
    h = e.dxf.height
    w = text_width(e.dxf.text, h, e.dxf.get("width", 1.0))
    x, y = e.dxf.insert.x, e.dxf.insert.y
    return Region(x, y - 0.3 * h, x + w, y + h)


def line_boxes(doc):
    for e in doc.modelspace().query("LINE"):
        s, t = e.dxf.start, e.dxf.end
        yield Region(min(s.x, t.x), min(s.y, t.y), max(s.x, t.x), max(s.y, t.y))


def test_default_profile_validates_and_numbers_drawings(profile):
    assert profile.name == "UniCAD Demo EPC"
    assert profile.logo is not None and profile.logo.is_file()
    assert profile.drawing_number(client="CIL", project="202109", discipline="electrical", seq=4) == (
        "UDEMO-CIL-202109-EE-004"
    )
    assert profile.layer(LayerRole.FRAME).name == "G-FRAME"


def test_profile_fills_default_layers_when_map_is_partial():
    p = CompanyProfile.model_validate(
        {"name": "X", "short_code": "XX", "layers": {"frame": {"name": "MY-FRAME", "colour": 3}}}
    )
    assert p.layer(LayerRole.FRAME).name == "MY-FRAME"
    assert p.layer(LayerRole.TITLE).name == "G-TITLE"
    assert p.layer(LayerRole.TEXT).name == "G-TEXT"


@pytest.mark.parametrize(
    "patch",
    [
        {"drawing_number_pattern": "{company}-{nonsense}-{seq:03d}"},
        {"drawing_number_pattern": "{company}-{client}"},
        {"drawing_number_pattern": "{company}-{seq:d"},
        {"short_code": "lower"},
        {"layers": {"frame": {"name": "BAD:NAME"}}},
        {"layers": {"frame": {"name": "OK", "colour": 0}}},
        {"layers": {"mystery": {"name": "OK"}}},
        {"unknown_field": 1},
    ],
)
def test_profile_rejects_bad_input(patch):
    with pytest.raises(ValidationError):
        CompanyProfile.model_validate({"name": "X", "short_code": "XX", **patch})


def test_sheet_spec_rejects_paper_mismatch_and_bad_sheet_numbers():
    spec = samples.rooftop_500kwp(PaperSize.A3)
    with pytest.raises(ValidationError):
        SheetSpec.model_validate(spec.model_dump() | {"paper": "A1"})
    bad_tb = spec.title_block.model_dump() | {"sheet_no": 9, "sheet_count": 3}
    with pytest.raises(ValidationError):
        type(spec.title_block).model_validate(bad_tb)


@pytest.mark.parametrize("paper", PAPERS)
@pytest.mark.parametrize("kind", SPECS)
def test_sheet_renders_clean_for_every_paper_size(paper, kind, profile):
    doc, result = render(SPECS[kind](paper), profile)
    assert not doc.audit().errors
    assert not doc.audit().fixes
    w, h = paper.size_mm
    inner = result.frame
    assert Region(0, 0, w, h).contains(inner)
    assert result.drawing_area.width > 0.4 * w
    assert result.drawing_area.height > 0.6 * h


@pytest.mark.parametrize("paper", PAPERS)
@pytest.mark.parametrize("kind", [*SPECS, "stress"])
def test_every_text_lies_inside_its_cell(paper, kind, profile):
    spec = stress_spec(paper) if kind == "stress" else SPECS[kind](paper)
    doc, result = render(spec, profile)
    texts = {e.dxf.handle: e for e in doc.modelspace().query("TEXT")}
    assert len(texts) == len(result.text_cells) > 20
    for tc in result.text_cells:
        box = text_box(texts[tc.handle])
        assert tc.cell.contains(box, tol=1e-6), (texts[tc.handle].dxf.text, box, tc.cell)


@pytest.mark.parametrize("paper", PAPERS)
@pytest.mark.parametrize("kind", [*SPECS, "stress"])
def test_drawing_area_is_clear_of_border_and_right_column(paper, kind, profile):
    spec = stress_spec(paper) if kind == "stress" else SPECS[kind](paper)
    doc, result = render(spec, profile)
    area = result.drawing_area
    assert result.frame.contains(area)
    assert not area.overlaps(result.column)
    for box in line_boxes(doc):
        assert not box.overlaps(area), f"sheet furniture {box} intrudes into the drawing area"
    for e in doc.modelspace().query("TEXT"):
        assert not text_box(e).overlaps(area)


def test_drawing_area_grows_with_paper_and_leaves_room_for_a_diagram(profile):
    areas = {p: render(samples.rooftop_500kwp(p), profile)[1].drawing_area for p in PAPERS}
    widths = [areas[p].width for p in (PaperSize.A4, PaperSize.A3, PaperSize.A2, PaperSize.A1, PaperSize.A0)]
    assert widths == sorted(widths)
    assert areas[PaperSize.A4].width > 150 and areas[PaperSize.A4].height > 150


def test_legend_slots_match_entries_and_sit_in_the_column(profile):
    spec = samples.rooftop_500kwp(PaperSize.A3)
    _, result = render(spec, profile)
    assert [s.key for s in result.legend_slots] == [k for k, _ in spec.legend]
    for slot in result.legend_slots:
        assert result.column.contains(slot.cell)
        assert slot.cell.contains(Region(*slot.centre, *slot.centre))
        assert slot.cell.width > 5 and slot.cell.height > 3
    ys = [s.centre[1] for s in result.legend_slots]
    assert ys == sorted(ys, reverse=True)
    for a, b in zip(result.legend_slots, result.legend_slots[1:], strict=False):
        assert not a.cell.overlaps(b.cell)


def test_overfull_sheet_reports_what_it_dropped_instead_of_overflowing(profile):
    _, result = render(stress_spec(PaperSize.A4), profile)
    assert any("legend" in w for w in result.warnings)
    assert any("revisions" in w for w in result.warnings)
    assert 0 < len(result.legend_slots) < 40


def test_revision_table_keeps_the_newest_when_it_overflows(profile):
    doc, result = render(stress_spec(PaperSize.A3), profile)
    revs = {e.dxf.text for e in doc.modelspace().query("TEXT")}
    assert "24" in revs and "00" not in revs
    assert any("earliest revisions" in w for w in result.warnings)


def test_title_block_carries_every_field(profile):
    spec = samples.utility_100mwac(PaperSize.A1)
    doc, _ = render(spec, profile)
    text = "\n".join(e.dxf.text for e in doc.modelspace().query("TEXT"))
    tb = spec.title_block
    for needle in (
        tb.client,
        tb.consultant,
        tb.drawing_number,
        tb.location,
        tb.loa_no,
        profile.name,
        "4 of 18",
        "A1",
        "AKS",
        "16.09.26",
        "DRAWING ISSUED FOR",
        *[i.value for i in IssuedFor],
    ):
        assert needle in text, needle


def test_exactly_one_issued_for_box_is_ticked(profile):
    for option in IssuedFor:
        spec = samples.rooftop_500kwp(PaperSize.A3)
        spec = spec.model_copy(update={"title_block": spec.title_block.model_copy(update={"issued_for": option})})
        doc, _ = render(spec, profile)
        ticks = [
            e
            for e in doc.modelspace().query("LINE[layer=='G-TITLE']")
            if e.dxf.lineweight == 50 and e.dxf.start.x != e.dxf.end.x and e.dxf.start.y != e.dxf.end.y
        ]
        assert len(ticks) == 2


def test_rendering_is_deterministic(profile):
    def dump(doc):
        return [
            (e.dxftype(), e.dxf.layer, e.dxf.all_existing_dxf_attribs().get("text", ""), str(e.dxf.all_existing_dxf_attribs()))
            for e in doc.modelspace()
        ]

    spec = samples.utility_100mwac(PaperSize.A2)
    assert dump(render(spec, profile)[0]) == dump(render(spec, profile)[0])


def test_layers_text_style_and_units_follow_the_profile(profile):
    custom = profile.model_copy(
        update={
            "layers": {
                LayerRole.FRAME: profile.layer(LayerRole.FRAME).model_copy(update={"name": "A-FRM", "colour": 1}),
                LayerRole.TITLE: profile.layer(LayerRole.TITLE).model_copy(update={"name": "A-TTL"}),
                LayerRole.TEXT: profile.layer(LayerRole.TEXT).model_copy(update={"name": "A-TXT"}),
            },
            "text_style": "unicad",
        }
    )
    doc, _ = render(samples.rooftop_500kwp(PaperSize.A3), custom)
    used = {e.dxf.layer for e in doc.modelspace()}
    assert {"A-FRM", "A-TTL", "A-TXT"} <= used
    assert not used & {"G-FRAME", "G-TITLE", "G-TEXT"}
    assert doc.layers.get("A-FRM").dxf.color == 1
    assert {e.dxf.style for e in doc.modelspace().query("TEXT")} == {"unicad"}
    assert doc.header["$INSUNITS"] == 4


def test_logo_block_is_imported_and_scaled_into_the_title_block(profile):
    doc, result = render(samples.rooftop_500kwp(PaperSize.A3), profile)
    inserts = list(doc.modelspace().query("INSERT"))
    assert len(inserts) == 1 and inserts[0].dxf.name == "LOGO_UDEMO"
    assert inserts[0].dxf.xscale == pytest.approx(inserts[0].dxf.yscale)
    box = bbox.extents([inserts[0]])
    assert result.column.contains(Region(box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y))
    assert box.size.x > 5


def test_missing_logo_falls_back_to_a_monogram(profile):
    p = profile.model_copy(update={"logo": None})
    doc, _ = render(samples.rooftop_500kwp(PaperSize.A3), p)
    assert not list(doc.modelspace().query("INSERT"))
    assert "UDEMO" in {e.dxf.text for e in doc.modelspace().query("TEXT")}


def test_unknown_linetype_is_rejected(profile):
    bad = profile.model_copy(
        update={
            "layers": {
                LayerRole.FRAME: profile.layer(LayerRole.FRAME).model_copy(update={"linetype": "NOPE"}),
                LayerRole.TITLE: profile.layer(LayerRole.TITLE),
                LayerRole.TEXT: profile.layer(LayerRole.TEXT),
            }
        }
    )
    with pytest.raises(ValueError, match="NOPE"):
        render(samples.rooftop_500kwp(PaperSize.A3), bad)


@pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")
@pytest.mark.parametrize("paper", [PaperSize.A4, PaperSize.A3, PaperSize.A0])
def test_librecad_exports_the_sheet(paper, profile, tmp_path):
    doc, _ = render(samples.utility_100mwac(paper), profile)
    dxf = tmp_path / "sheet.dxf"
    doc.saveas(dxf)
    w, h = paper.size_mm
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    pdf, png = tmp_path / "sheet.pdf", tmp_path / "sheet.png"
    for args in (
        ["dxf2pdf", "-a", "-p", f"{w:g}x{h:g}", "-o", str(pdf), str(dxf)],
        ["dxf2png", "-r", "3600x2550", "-o", str(png), str(dxf)],
    ):
        done = subprocess.run([str(LIBRECAD), *args], env=env, capture_output=True, timeout=120)
        assert done.returncode == 0, done.stderr.decode()
    page = pymupdf.open(pdf)[0]
    assert page.rect.width == pytest.approx(w / 25.4 * 72, abs=2)
    assert page.rect.height == pytest.approx(h / 25.4 * 72, abs=2)
    assert png.stat().st_size > 10_000


def test_legend_shows_the_real_symbol_instead_of_a_placeholder(profile):
    import ezdxf

    from unicad.drafting.legend import legend_drawer
    from unicad.drafting.sheets.render import render_sheet
    from unicad.drafting.sheets.samples import rooftop_500kwp
    from unicad.drafting.symbols import REGISTRY

    spec = rooftop_500kwp()
    doc = ezdxf.new("R2018")
    result = render_sheet(doc, spec, profile, legend_drawer(doc.modelspace()))
    inserted = {e.dxf.name for e in doc.modelspace().query("INSERT")}
    texts = {e.dxf.text for e in doc.modelspace().query("TEXT")}
    for slot in result.legend_slots:
        assert REGISTRY[slot.key].block_name in inserted
        assert slot.key not in texts


@pytest.mark.parametrize("paper", [PaperSize.A1, PaperSize.A3])
def test_a_long_location_wraps_in_the_title_block_instead_of_truncating(paper, profile):
    place = "SHREE GANESH TEXTILES FACTORY ROOF, GAT NO 123 BEHIND THE OLD MIDC WATER TANK, MIDC CHAKAN PHASE II, TALUKA KHED, DISTRICT PUNE, MAHARASHTRA, INDIA 410501"
    spec = samples.rooftop_500kwp(paper)
    spec = spec.model_copy(update={"title_block": spec.title_block.model_copy(update={"location": place})})
    doc, result = render(spec, profile)
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    start = next(i for i, t in enumerate(texts) if place.startswith(t) and t != place or t == place)
    shown, i = "", start
    while len(shown) < len(place) and i < len(texts):
        shown = f"{shown} {texts[i]}".strip()
        i += 1
    assert shown == place and not any(t.endswith("...") for t in texts) and result.warnings == ()


def test_a_location_naming_a_place_twice_is_drawn_once(profile):
    spec = samples.rooftop_500kwp(PaperSize.A3)
    spec = spec.model_copy(update={"title_block": spec.title_block.model_copy(update={"location": "PUNE, MIDC CHAKAN, PUNE, MAHARASHTRA, MAHARASHTRA"})})
    doc, _ = render(spec, profile)
    texts = " ".join(e.dxf.text for e in doc.modelspace().query("TEXT"))
    assert "PUNE, MIDC CHAKAN, MAHARASHTRA" in texts and "MAHARASHTRA, MAHARASHTRA" not in texts


def test_default_title_block_has_a_date_cell_and_names_only_in_the_signoff_cells(profile):
    """B6: the DRAWN cell holds the drafter's name and the issue date has its own DATE cell."""
    spec = samples.rooftop_500kwp(PaperSize.A3)
    tb = spec.title_block.model_copy(update={"drawn_by": "A. Kumar", "checked_date": None, "approved_date": None})
    doc, _ = render(spec.model_copy(update={"title_block": tb}), profile)
    texts = {e.dxf.text: e for e in doc.modelspace().query("TEXT")}
    assert "DATE" in texts and "A. Kumar" in texts
    when = tb.drawn_date.strftime(profile.date_format)
    dates = [e for e in doc.modelspace().query("TEXT") if e.dxf.text == when]
    label, name = texts["DATE"], texts["A. Kumar"]
    signoff = [e for e in dates if abs(e.dxf.insert.y - name.dxf.insert.y) < 1.0]
    assert len(signoff) == 1 and signoff[0].dxf.insert.x > label.dxf.insert.x - 0.5 > name.dxf.insert.x

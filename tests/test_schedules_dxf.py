import os
import subprocess
from pathlib import Path

import ezdxf
import pymupdf
import pytest
from test_routing import CATALOG, fixture

from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, PaperSize, SheetSpec, TitleBlockData
from unicad.drafting.tables import Column, TableSection, TableSpec, render_tables
from unicad.schedules import LengthMode, build_bom, build_schedule
from unicad.schedules.dxf import load_profile, render_bom, render_schedule, schedule_tables

ROOT = Path(__file__).resolve().parents[1]
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
needs_librecad = pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")
PROFILE = CompanyProfile.load(ROOT / "profiles" / "default.json")
ALLOWED = {"LINE", "TEXT", "SOLID", "INSERT", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE", "HATCH"}


def sheet(paper: PaperSize):
    def make(page: int, pages: int, part: int) -> SheetSpec:
        tb = TitleBlockData(client="C", project_title="P", location="L", drawing_title="T", drawing_number="X-1", paper=paper, sheet_no=page, sheet_count=pages, issued_for=IssuedFor.INFORMATION)
        return SheetSpec(paper=paper, title_block=tb)

    return make


def table(n_rows: int, breaks: bool = False) -> TableSpec:
    cols = (Column("NO"), Column("ID", "left"), Column("LEN\n(m)", group="G"), Column("NOTE", "left", wrap=True))
    rows = tuple((str(i), f"CABLE-{i:04d}", f"{i / 7:.2f}", "a remark that is long enough to need wrapping in a narrow column " * (i % 3)) for i in range(n_rows))
    half = n_rows // 2
    return TableSpec(cols, (TableSection("FIRST", rows[:half]), TableSection("SECOND", rows[half:], page_break_before=breaks)), (("BASIS", "something"),))


def texts(doc) -> list[str]:
    return [e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT"]


def test_table_paginates_with_the_heading_repeated_and_pages_numbered_x_of_y():
    r = render_tables([(table(200), PaperSize.A3)], PROFILE, sheet(PaperSize.A3))
    assert r.pages > 2 and len(r.docs) == r.pages
    for i, doc in enumerate(r.docs, start=1):
        t = texts(doc)
        assert f"{i} of {r.pages}" in t
        assert "ID" in t and "LEN" in t and "G" in t
    ids = [t for doc in r.docs for t in texts(doc) if t.startswith("CABLE-")]
    assert ids == [f"CABLE-{i:04d}" for i in range(200)]
    assert any(t.endswith("(CONT.)") for doc in r.docs for t in texts(doc))


def test_a_section_with_page_break_before_starts_a_page():
    r = render_tables([(table(20, breaks=True), PaperSize.A3)], PROFILE, sheet(PaperSize.A3))
    assert r.pages == 2
    assert "SECOND" in texts(r.docs[1]) and "SECOND" not in texts(r.docs[0])


def test_columns_are_fitted_to_the_content_and_fill_the_sheet_width():
    r = render_tables([(table(30), PaperSize.A3)], PROFILE, sheet(PaperSize.A3))
    plan = r.plans[0]
    assert sum(plan.widths) == pytest.approx(plan.area.width)
    assert plan.widths[1] > plan.widths[0] * 2


def test_tables_use_only_lines_text_and_solids_in_millimetres():
    r = render_tables([(table(60), PaperSize.A1)], PROFILE, sheet(PaperSize.A1))
    for doc in r.docs:
        assert doc.header["$INSUNITS"] == 4 and doc.header["$DIMTXT"] > 0
        kinds = {e.dxftype() for e in doc.modelspace()}
        assert not kinds & {"MTEXT", "ACAD_TABLE", "IMAGE", "MULTILEADER", "XLINE"}
        assert kinds <= ALLOWED
        used = {e.dxf.layer for e in doc.modelspace()}
        assert used and all(doc.layers.get(name).dxf.lineweight > 0 for name in used)


def test_every_page_fits_the_drawing_area():
    r = render_tables([(table(200), PaperSize.A3)], PROFILE, sheet(PaperSize.A3))
    plan = r.plans[0]
    for k, items in enumerate(plan.pages):
        used = plan.header_h + sum(i.height for i in items) + (plan.preamble.height + 2 * plan.text_h if k == 0 and plan.preamble else 0)
        assert used <= plan.area.height + 1e-6


def test_schedule_tables_summarise_strings_per_combiner_for_big_projects_and_list_them_for_small_ones():
    big = build_schedule(fixture("utility_50mwac"), CATALOG)
    small = build_schedule(fixture("rooftop_500kwp"), CATALOG)
    (_, dc_big), (_, ac_big) = schedule_tables(big)
    (_, dc_small), _ = schedule_tables(small)
    string_rows = lambda spec: sum(len(s.rows) for s in spec.sections if "STRING" in (s.title or ""))
    assert string_rows(dc_big) == 180 and string_rows(dc_small) == 50
    assert sum(len(s.rows) for s in schedule_tables(big, "full")[0][1].sections if "STRING" in (s.title or "")) == 4320
    assert string_rows(schedule_tables(small, "summary")[0][1]) == 5
    assert ac_big.row_count == 38
    with pytest.raises(ValueError):
        schedule_tables(small, "everything")


def test_summarised_utility_schedule_breaks_pages_per_block():
    (_, dc), _ = schedule_tables(build_schedule(fixture("utility_50mwac"), CATALOG))
    strings = [s for s in dc.sections if "STRING" in s.title]
    assert [s.title.rsplit(" - ", 1)[1] for s in strings] == [f"BLOCK-0{i}" for i in range(1, 7)]
    assert [s.page_break_before for s in strings] == [False] + [True] * 5
    assert all(len(s.rows) == 30 for s in strings)


def test_layout_blocks_take_their_block_from_the_network_group_not_the_instance_index():
    """close-e2e-1 follow-up: INV-06-03 of a layout block (a count-1 group) was put in B01 from its instance path."""
    from test_design import blank
    from test_design_p5 import GROUND_MODULE
    from unicad.arrays import PlacementSpec, place_array
    from unicad.design import autobuild
    from unicad.ingest.site_import import import_site

    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry
    spec = PlacementSpec(module=GROUND_MODULE, table_rows=2, table_cols=28, tilt_deg=25, modules_per_string=28, strings_per_inverter=24, road_every_rows=12,
                         block_dc_kwp=1560, target_dc_kwp=6240)  # fmt: skip
    base = blank("utility_50mwac")
    base = base.model_copy(update={"site": base.site.model_copy(update={"geometry": site})})
    p = autobuild(base, CATALOG, {"template": "utility_string", "module": GROUND_MODULE, "inverter": "sungrow-sg350hx"}, layout=place_array(site, spec, CATALOG.get(GROUND_MODULE))).project
    schedule = build_schedule(p, CATALOG)
    strings = [r for r in schedule.rows if r.is_string]
    assert {r.block for r in strings} == {f"BLOCK-0{i}" for i in range(1, 7)}
    assert all(r.cable_id.startswith(f"INV-{r.block[-2:]}-") for r in strings)
    (_, dc), _ = schedule_tables(schedule)
    titled = [s.title.rsplit(" - ", 1)[-1] for s in dc.sections if "STRING" in s.title]
    assert titled == [f"BLOCK-0{i}" for i in range(1, 7)]


def test_schedule_pages_carry_the_project_title_block_and_continuous_numbering():
    p = fixture("utility_50mwac")
    s = build_schedule(p, CATALOG)
    r, titles = render_schedule(p, s, load_profile(p.company_profile))
    assert titles == ["DC CABLE SCHEDULE", "AC AND MV CABLE SCHEDULE"] and r.parts[-1] == 1 and r.parts[0] == 0
    assert f"{r.pages} of {r.pages}" in texts(r.docs[-1]) and f"1 of {r.pages}" in texts(r.docs[0])
    assert "AC AND MV CABLE SCHEDULE" in texts(r.docs[-1]) and "DC CABLE SCHEDULE" in texts(r.docs[0])
    assert not [w for w in r.warnings if "truncated" in w]


def test_unknown_company_profile_lists_where_it_looked():
    with pytest.raises(KeyError, match="no company profile 'nope'"):
        load_profile("nope")


@needs_librecad
def test_librecad_prints_every_schedule_page(tmp_path):
    p = fixture("rooftop_500kwp")
    s = build_schedule(p, CATALOG, LengthMode.ESTIMATED)
    r, _ = render_schedule(p, s, load_profile(p.company_profile))
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    for i, doc in enumerate(r.docs):
        dxf = tmp_path / f"p{i}.dxf"
        doc.saveas(dxf)
        assert ezdxf.readfile(dxf).header["$INSUNITS"] == 4
        pdf = tmp_path / f"p{i}.pdf"
        subprocess.run([str(LIBRECAD), "dxf2pdf", "-s", "1", "-c", "-p", "420x297", "-o", str(pdf), str(dxf)], env=env, capture_output=True, timeout=180, check=True)
        page = pymupdf.open(pdf)[0]
        assert abs(page.rect.width - 420 * 72 / 25.4) < 2 and len(page.get_drawings()) > 100


def test_bom_pages_render_without_truncation():
    p = fixture("utility_50mwac")
    r = render_bom(p, build_bom(p, CATALOG, build_schedule(p, CATALOG)), load_profile(p.company_profile))
    assert r.pages == 2 and not [w for w in r.warnings if "truncated" in w]
    flat = [t for doc in r.docs for t in texts(doc)]
    assert "PV MODULES" in flat and "116,640" in flat and "BILL OF MATERIALS" in flat


def test_a_section_band_is_drawn_only_where_the_section_changes():
    """B8: per-block templates of the same equipment pair and cable (a 9-block utility plant) list their rows under one section band,
    not a band above every row."""
    import importlib.util

    from unicad.domain.network import Service
    from unicad.schedules.dxf import _sections

    spec = importlib.util.spec_from_file_location("build_string_diagram_samples", Path(__file__).resolve().parents[1] / "tools" / "build_string_diagram_samples.py")
    samples = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(samples)
    p = samples.ground_central(CATALOG)
    schedule = build_schedule(p, CATALOG)
    sections = _sections(schedule, frozenset(Service) - {Service.DC}, detail=True)
    titles = [s.title for s in sections]
    assert len(titles) == len(set(titles)), titles
    rows = [r for s in schedule.sections if s.service is not Service.DC for r in s.rows]
    assert sum(len(s.rows) for s in sections) == len(rows)

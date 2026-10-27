"""String diagrams: types, accounting of every string instance, labels against the cable schedule, module numbering against the layout, DXF rules."""

import importlib.util
import os
import re
import subprocess
from collections import Counter
from dataclasses import replace
from itertools import accumulate
from pathlib import Path

import ezdxf
import pymupdf
import pytest
from test_design import CATALOG, fixture

from unicad.domain.network import NodeKind
from unicad.drafting import dxfrules
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.string_diagram import build_string_diagram_sheets, plan_types
from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.string_diagram import build, draw, parameters_for
from unicad.drafting.string_diagram.build import fit_pages
from unicad.drafting.string_diagram.draw import shown_modules
from unicad.schedules import LengthMode, build_schedule

ROOT = Path(__file__).resolve().parents[1]
PROFILE = CompanyProfile.load(ROOT / "profiles" / "default.json")
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
needs_librecad = pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")
spec = importlib.util.spec_from_file_location("build_string_diagram_samples", ROOT / "tools" / "build_string_diagram_samples.py")
samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(samples)


@pytest.fixture(scope="module")
def projects():
    return {
        "rooftop": fixture("rooftop_500kwp"),
        "utility": fixture("utility_50mwac"),
        "central": samples.ground_central(CATALOG),
        "string": samples.ground_string(CATALOG),
    }


@pytest.fixture(scope="module")
def built(projects):
    return {k: build_string_diagram_sheets(p, CATALOG, PROFILE) for k, p in projects.items()}


def all_strings(project) -> set[str]:
    net = project.network
    return {i for n in net.by_kind(NodeKind.PV_STRING) for i in net.instance_ids(n.id)}


def drawn_texts(result) -> list[str]:
    return [e.dxf.text for e in result.doc.modelspace().query("TEXT")]


# ---------------- what is drawn ----------------


def test_the_rooftop_fixture_gets_one_a3_sheet_for_its_five_identical_inverters(built):
    (r,) = built["rooftop"]
    assert r.paper is PaperSize.A3 and r.warnings == ()
    assert r.instances == tuple(f"inv@{i}" for i in range(1, 6)) and len(r.strings) == 10
    texts = drawn_texts(r)
    assert "TYPICAL FOR INV-01..INV-05 (5 NOS) : SHOWN FOR INV-01" in texts
    assert [f"STR-01-{i:02d} : 18 x 555 Wp" in texts for i in range(1, 11)] == [True] * 10
    assert texts.count("...18 NOS...") == 10
    assert "DCFB-01 DC FUSE BOX" in texts and any(t.startswith("DC-ISO-01: DC ISOLATOR 250 A 1000 V") for t in texts)


def test_ground_projects_get_a1_sheets_and_the_utility_fixture_without_layout_still_draws(built):
    assert {r.paper for k in ("central", "string", "utility") for r in built[k]} == {PaperSize.A1}
    assert all(r.warnings == () for rs in built.values() for r in rs)
    (utility,) = built["utility"]
    assert utility.sink_template == "scb" and len(utility.strings) == 24
    assert not any(t.startswith("TABLE POSITIONS") for t in drawn_texts(utility))


def test_one_sheet_per_distinct_type_and_numbers_are_unique(built):
    for rs in built.values():
        assert len({r.number for r in rs}) == len(rs) and len({r.sink_template for r in rs}) == len(rs)
    assert [r.sink_template for r in built["central"]] == ["scb_b01aa", "scb_b01ab", "scb_b02aa", "scb_b02ab"]


def test_a_project_without_strings_has_no_sheets():
    blank = fixture("rooftop_500kwp").model_copy(update={"network": fixture("rooftop_500kwp").network.model_copy(update={"nodes": (), "edges": (), "groups": ()}), "sheets": ()})
    assert build_string_diagram_sheets(blank, CATALOG, PROFILE) == []


def test_long_chains_show_three_modules_a_break_and_two_modules():
    assert shown_modules(28) == [1, 2, 3, 27, 28] and shown_modules(6) == [1, 2, 3, 5, 6]
    assert shown_modules(5) == [1, 2, 3, 4, 5] and shown_modules(1) == [1]


def test_each_chain_inserts_five_module_symbols(built):
    (r,) = built["rooftop"]
    modules = [e for e in r.doc.modelspace().query("INSERT") if e.dxf.name == "UC_PV_MODULE" and round(e.dxf.rotation) == 90]
    assert len(modules) == 5 * len(r.strings)


# ---------------- every string instance is accounted for ----------------


@pytest.mark.parametrize("name", ["rooftop", "utility", "central", "string"])
def test_every_string_instance_is_drawn_or_stood_for_exactly_once(projects, built, name):
    covered = Counter(s for r in built[name] for s in r.covers)
    assert set(covered) == all_strings(projects[name])
    assert max(covered.values()) == 1


@pytest.mark.parametrize("name", ["rooftop", "central", "string"])
def test_types_group_the_instances_of_one_template(projects, name):
    types, warnings = plan_types(projects[name], CATALOG)
    assert warnings == []
    net = projects[name].network
    for t in types:
        assert [m.instance for m in t.members] == list(net.instance_ids(t.sink))
        assert len({len(m.strings) for m in t.members}) == 1
        assert t.lines[0].instance == t.representative.strings[0]
        assert [ln.instance for ln in t.lines] == list(t.representative.strings)


def test_a_type_too_big_for_one_page_continues_on_the_next_with_nothing_lost(projects):
    (t,) = plan_types(projects["utility"], CATALOG)[0]
    th = metrics_for(PaperSize.A1).th
    area = metrics_for(PaperSize.A1).drawing_area
    short = Region(area.x0, area.y0, area.x1, area.y0 + 260)
    pages = fit_pages(t, short, th)
    assert len(pages) > 1
    assert [ln.instance for p in pages for ln in p.lines] == [ln.instance for ln in t.lines]
    assert [p.first for p in pages] == [0, *accumulate(len(p.lines) for p in pages[:-1])]
    for p in pages:
        w, h = draw.diagram_size(t, p.lines, p.u)
        assert w <= short.width and h <= short.height


def test_pages_of_a_split_type_cover_every_string_once(projects, monkeypatch):
    monkeypatch.setattr(build, "metrics_for", lambda paper: replace(metrics_for(paper), drawing_area=Region(21.9, 21.9, 658.1, 300.0)))
    rs = build_string_diagram_sheets(projects["utility"], CATALOG, PROFILE)
    assert len(rs) > 1 and all(r.sink_template == "scb" for r in rs) and all(r.warnings == () for r in rs)
    assert Counter(s for r in rs for s in r.covers) == Counter(all_strings(projects["utility"]))
    assert [s for r in rs for s in r.strings] == [f"str@1.1.1.{i}" for i in range(1, 25)]
    assert any("PAGE 2 OF" in t for t in drawn_texts(rs[1]))


# ---------------- labels match the cable schedule ----------------


@pytest.mark.parametrize("name", ["rooftop", "central", "string"])
def test_drawn_cable_ids_are_the_schedule_ids(projects, built, name):
    rows = {r.src: r for r in build_schedule(projects[name], CATALOG, LengthMode.NETWORK).rows if r.is_string}
    types, _ = plan_types(projects[name], CATALOG)
    sheet_texts = {t for r in built[name] for t in drawn_texts(r)}
    checked = 0
    for t in types:
        assert all(s in rows for m in t.members for s in m.strings)
        for ln in t.lines:
            assert ln.cable_id == rows[ln.instance].cable_id and ln.cable_id in sheet_texts
            checked += 1
    assert checked >= 10


def test_the_string_cable_label_is_the_schedule_callout(projects, built):
    schedule = build_schedule(projects["rooftop"], CATALOG, LengthMode.NETWORK)
    row = next(r for r in schedule.rows if r.is_string)
    (r,) = built["rooftop"]
    assert f"{row.cores}C x {row.size_mm2:g} sq.mm Cu (+) / (-), {row.length_pos_m:g} m" in drawn_texts(r)


def test_scb_output_cable_ids_and_inverter_inputs_are_the_schedule_ones(projects, built):
    rows = build_schedule(projects["central"], CATALOG, LengthMode.NETWORK).rows
    by_src = {r.src: r for r in rows if r.edge.startswith("e_scb_inv")}
    types, _ = plan_types(projects["central"], CATALOG)
    sheet_texts = {t for r in built["central"] for t in drawn_texts(r)}
    for t in types:
        assert t.output is not None and t.output.cable_id in sheet_texts
        for m in t.members:
            row = by_src[m.instance]
            assert m.destination.split(" ")[0] == row.to_tag
        assert t.output.cable_id == by_src[t.representative.instance].cable_id
        assert [m.destination for m in t.members][:2] == [f"{by_src[m.instance].to_tag} IP{i:02d}" for i, m in enumerate(t.members[:2], start=1)]


# ---------------- MPPT / input allocation ----------------


def test_rooftop_strings_take_one_mppt_each_in_order(projects):
    (t,) = plan_types(projects["rooftop"], CATALOG)[0]
    assert t.mppt.mppt_count == 10 and t.mppt.inputs_per_mppt == 1 and not t.mppt.overflow
    assert [ln.mppt for ln in t.lines] == list(range(1, 11)) and [ln.ip for ln in t.lines] == [f"IP{i:02d}" for i in range(1, 11)]
    assert t.fuse is None


def test_string_inverter_strings_pair_up_on_mppts_and_input_numbers_follow_the_schedule(projects):
    types, _ = plan_types(projects["string"], CATALOG)
    first = types[0]
    net = projects["string"].network.expand()
    n = sum(1 for e in net.edges if e.is_power and e.dst_node == first.representative.instance and net.node(e.src_node).kind is NodeKind.PV_STRING)
    assert n > 2 and n % 2 == 0
    assert first.mppt.inputs_per_mppt == 2 and first.mppt.used == n // 2
    assert [ln.mppt for ln in first.lines] == [i // 2 + 1 for i in range(n)]
    assert [int(ln.ip[2:]) for ln in first.lines] == list(range(1, n + 1))
    assert first.fuse is None and dict(parameters_for(first))["STRING FUSE"] == "NONE (AT MOST 2 PER MPPT)"


def test_scb_strings_get_a_proposed_fuse_within_the_module_limit(projects):
    types, _ = plan_types(projects["central"], CATALOG)
    for t in types:
        assert t.fuse is not None and not t.fuse.modelled
        assert 1.25 * t.module.isc <= t.fuse.rating_a <= t.module.max_series_fuse
        assert dict(parameters_for(t))["STRING FUSE"] == f"{t.fuse.rating_a:g} A GPV (PROPOSED)"


# ---------------- the table position plan ----------------


def plan_cells(result) -> dict[tuple[str, int, int], str]:
    out = {}
    for p in result.texts:
        m = re.fullmatch(r"plan (\S+) (\d+),(\d+)", p.tag)
        if m:
            out[(m[1], int(m[2]), int(m[3]))] = p.text
    return out


@pytest.mark.parametrize("name", ["rooftop", "central", "string"])
def test_module_numbers_in_the_plan_follow_the_string_assignment_slot_order(projects, built, name):
    project, layout = projects[name], projects[name].array_layout
    types, _ = plan_types(project, CATALOG)
    checked = 0
    for r in built[name]:
        cells = plan_cells(r)
        t = next(t for t in types if t.sink == r.sink_template)
        for s in r.strings:
            slots = layout.string_slots(s)
            n = len(slots)
            assert n == next(ln.modules for ln in t.lines if ln.instance == s)
            for k, (table, row, col) in enumerate(slots, start=1):
                assert cells[(table, row, col)] == (f"{k}+" if k == 1 else f"{n}-" if k == n else str(k))
                checked += 1
    assert checked > 100


def test_plan_marks_both_ends_of_every_string_and_table_positions_are_in_the_summary(projects, built):
    (r,) = built["rooftop"]
    cells = plan_cells(r)
    assert sum(v.endswith("+") for v in cells.values()) == 10 and sum(v.endswith("-") for v in cells.values()) == 10
    texts = drawn_texts(r)
    assert "T01 R1 C1-18" in texts and "T05 R2 C1-18" in texts and "T01  R001-0001" in texts


# ---------------- DXF rules, determinism, LibreCAD ----------------


def test_every_sheet_passes_the_dxf_writer_rules(built):
    for name, rs in built.items():
        for r in rs:
            assert dxfrules.violations(r.doc) == [], (name, r.key)
            assert {lt.dxf.name.upper() for lt in r.doc.linetypes} >= {"DASHED2"}
            assert all(layer.dxf.linetype.upper() in {"CONTINUOUS", "DASHED2"} for layer in r.doc.layers if layer.dxf.name.startswith("E-STR"))


def test_saved_files_pass_the_rules_and_are_byte_identical_on_a_rerun(projects, tmp_path):
    a = build_string_diagram_sheets(projects["rooftop"], CATALOG, PROFILE, tmp_path / "a")
    b = build_string_diagram_sheets(projects["rooftop"], CATALOG, PROFILE, tmp_path / "b")
    assert [p.read_bytes() for p in (r.path for r in a)] == [p.read_bytes() for p in (r.path for r in b)]
    assert dxfrules.violations(ezdxf.readfile(a[0].path)) == []
    assert a[0].path.name == "rooftop-500kwp-strings-inv.dxf"


def test_no_text_overlaps_or_leaves_the_drawing_area(built):
    for rs in built.values():
        for r in rs:
            boxes = [p.box for p in r.texts]
            assert all(r.drawing_area.contains(b, tol=1e-3) for b in boxes)
            assert not [1 for i, a in enumerate(boxes) for b in boxes[i + 1 :] if a.overlaps(b, tol=1e-3)]
            assert min(p.box.y1 - p.box.y0 for p in r.texts) / 1.3 >= 1.4


@needs_librecad
def test_librecad_prints_the_sheet_at_the_paper_size(built, tmp_path):
    (r,) = built["rooftop"]
    dxf = tmp_path / "s.dxf"
    r.doc.saveas(dxf)
    pdf = tmp_path / "s.pdf"
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run([str(LIBRECAD), "dxf2pdf", "-s", "1", "-c", "-p", "420x297", "-o", str(pdf), str(dxf)], check=True, capture_output=True, env=env, timeout=300)
    page = pymupdf.open(pdf)[0]
    assert abs(page.rect.width - 420 / 25.4 * 72) < 2 and abs(page.rect.height - 297 / 25.4 * 72) < 2
    assert len(page.get_drawings()) > 5000


def test_a_type_that_does_not_fit_its_paper_moves_to_a_larger_one(projects, monkeypatch):
    real = metrics_for
    monkeypatch.setattr(build, "metrics_for", lambda paper: real(paper) if paper is PaperSize.A1 else replace(real(paper), drawing_area=Region(0, 0, 60, 60)))
    (r,) = build_string_diagram_sheets(projects["rooftop"], CATALOG, PROFILE)
    assert r.paper is PaperSize.A1 and r.warnings == ()


def test_a_type_that_fits_no_paper_names_the_diagram_and_what_to_check(projects, monkeypatch):
    monkeypatch.setattr(build, "metrics_for", lambda paper: replace(metrics_for(paper), drawing_area=Region(0, 0, 60, 60)))
    with pytest.raises(build.DoesNotFit, match=r"string wiring diagram INVERTER INV-01\.\.INV-05.*even on A0; check the inverter's inputs per MPPT"):
        build_string_diagram_sheets(projects["rooftop"], CATALOG, PROFILE)

import io
import os
import shutil
import stat
from pathlib import Path

import ezdxf
import pymupdf
import pytest
from PIL import Image

from unicad.render import (
    BackendUnavailable,
    DwgConverter,
    DwgMismatch,
    DwgResult,
    DwgUnavailable,
    InvalidDrawing,
    LibreCadRenderer,
    RenderFailed,
    RenderTimeout,
    ScaleMode,
    Sheet,
    available_converter,
    locate_librecad,
    paper_size_mm,
    preview,
    unavailable_reason,
)
from unicad.render import librecad as librecad_module
from unicad.render.backend import PT_PER_MM, merge_pdfs
from unicad.render.dwg import entity_counts
from unicad.render.preview import pdf_to_png

PAPERS = ["A4", "A3", "A2", "A1", "A0"]


def _librecad_ready() -> str | None:
    try:
        locate_librecad()
    except BackendUnavailable as exc:
        return str(exc)
    if not os.environ.get("DISPLAY") and not shutil.which("xvfb-run"):
        return "no DISPLAY and no xvfb-run"
    return None


needs_librecad = pytest.mark.skipif(_librecad_ready() is not None, reason=_librecad_ready() or "")
needs_dwg = pytest.mark.skipif(available_converter() is None, reason=unavailable_reason() or "")


def write_sheet(path: Path, paper: str = "A3", *, units: int = 4) -> Path:
    w, h = paper_size_mm(paper)
    doc = ezdxf.new("R2018", setup=["linetypes"])
    doc.units = units
    doc.layers.get("0").dxf.lineweight = 35
    msp = doc.modelspace()
    msp.add_lwpolyline([(5, 5), (w - 5, 5), (w - 5, h - 5), (5, h - 5)], close=True)
    msp.add_solid([(100, 50), (110, 50), (100, 60), (110, 60)], dxfattribs={"color": 1})
    msp.add_text("MARKER", height=5, dxfattribs={"insert": (120, 52)})
    msp.add_circle((60, 40), 8)
    doc.saveas(path)
    return path


def fake_binary(tmp_path: Path, body: str, name: str = "fake-librecad") -> Path:
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture
def sheet_a3(tmp_path):
    return write_sheet(tmp_path / "a3.dxf", "A3")


@pytest.fixture
def with_display(monkeypatch):
    monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":99"))


@needs_librecad
@pytest.mark.parametrize("paper", PAPERS)
def test_pdf_page_matches_requested_paper_within_a_point(paper, tmp_path):
    dxf = write_sheet(tmp_path / f"{paper}.dxf", paper)
    pdf = LibreCadRenderer().to_pdf(dxf, tmp_path / f"{paper}.pdf", paper)
    w, h = paper_size_mm(paper)
    with pymupdf.open(pdf) as doc:
        assert len(doc) == 1
        assert doc[0].rect.width == pytest.approx(w * PT_PER_MM, abs=1.0)
        assert doc[0].rect.height == pytest.approx(h * PT_PER_MM, abs=1.0)


@needs_librecad
def test_custom_paper_size_in_mm(tmp_path):
    dxf = write_sheet(tmp_path / "c.dxf", "A4")
    pdf = LibreCadRenderer().to_pdf(dxf, tmp_path / "c.pdf", (250.0, 180.0))
    with pymupdf.open(pdf) as doc:
        assert doc[0].rect.width == pytest.approx(250 * PT_PER_MM, abs=1.0)
        assert doc[0].rect.height == pytest.approx(180 * PT_PER_MM, abs=1.0)


@needs_librecad
@pytest.mark.parametrize("mode", list(ScaleMode))
def test_every_scale_mode_produces_the_requested_page(mode, sheet_a3, tmp_path):
    pdf = LibreCadRenderer().to_pdf(sheet_a3, tmp_path / "m.pdf", "A3", mode)
    with pymupdf.open(pdf) as doc:
        assert doc[0].rect.width == pytest.approx(420 * PT_PER_MM, abs=1.0)


@needs_librecad
def test_actual_mode_prints_at_one_to_one_with_origin_at_lower_left(sheet_a3, tmp_path):
    pdf = LibreCadRenderer().to_pdf(sheet_a3, tmp_path / "a.pdf", "A3", ScaleMode.ACTUAL)
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        red = [d for d in page.get_drawings() if d.get("fill") and d["fill"][0] > 0.9 and d["fill"][1] < 0.1]
        assert red, "red SOLID not found in the PDF"
        rect = red[0]["rect"]
        height = page.rect.height
        assert rect.x0 / PT_PER_MM == pytest.approx(100, abs=0.5)
        assert rect.x1 / PT_PER_MM == pytest.approx(110, abs=0.5)
        assert (height - rect.y1) / PT_PER_MM == pytest.approx(50, abs=0.5)
        assert (height - rect.y0) / PT_PER_MM == pytest.approx(60, abs=0.5)


@needs_librecad
def test_png_long_side_equals_requested_pixels(sheet_a3, tmp_path):
    png = LibreCadRenderer().to_png(sheet_a3, tmp_path / "o.png", "A3", px=1800)
    assert max(Image.open(png).size) == pytest.approx(1800, abs=1)


@needs_librecad
def test_svg_export_is_written(sheet_a3, tmp_path):
    svg = LibreCadRenderer().to_svg(sheet_a3, tmp_path / "o.svg")
    assert svg.read_text().lstrip().startswith("<?xml")
    assert "<svg" in svg.read_text()


@needs_librecad
def test_sheet_set_merges_one_page_per_sheet_in_order(tmp_path):
    papers = ["A4", "A3", "A1"]
    sheets = [Sheet(write_sheet(tmp_path / f"s{i}.dxf", p), p, f"E-{i + 1:03d}") for i, p in enumerate(papers)]
    result = LibreCadRenderer().build_set(sheets, tmp_path / "set.pdf")
    with pymupdf.open(result.pdf) as doc:
        assert len(doc) == 3
        for page, paper in zip(doc, papers):
            assert page.rect.width == pytest.approx(paper_size_mm(paper)[0] * PT_PER_MM, abs=1.0)
        assert [t[1] for t in doc.get_toc()] == ["E-001", "E-002", "E-003"]
    assert [p.width_mm for p in result.pages] == [297.0, 420.0, 841.0]


def test_empty_sheet_set_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        LibreCadRenderer().build_set([], tmp_path / "x.pdf")


def test_merge_pdfs_preserves_page_sizes(tmp_path):
    parts = []
    for i, size in enumerate([(200, 100), (300, 150)]):
        doc = pymupdf.open()
        doc.new_page(width=size[0], height=size[1])
        parts.append(tmp_path / f"p{i}.pdf")
        doc.save(parts[-1])
    merge_pdfs(parts, [None, None], tmp_path / "m.pdf")
    with pymupdf.open(tmp_path / "m.pdf") as merged:
        assert [(p.rect.width, p.rect.height) for p in merged] == [(200, 100), (300, 150)]


@needs_librecad
def test_region_preview_has_requested_long_side_and_aspect(sheet_a3, tmp_path):
    result = preview(sheet_a3, (50, 20, 150, 70), px=1000, paper="A3", out=tmp_path / "r.png")
    assert (result.width_px, result.height_px) == pytest.approx((1000, 500), abs=1)
    assert Image.open(tmp_path / "r.png").size == (result.width_px, result.height_px)
    assert result.region == pytest.approx((50, 20, 150, 70), abs=0.1)
    assert result.px_per_mm == pytest.approx(10, abs=0.05)
    assert not result.warnings


@needs_librecad
def test_region_preview_shows_the_marker_where_the_drawing_puts_it(sheet_a3):
    result = preview(sheet_a3, (90, 40, 130, 70), px=800, paper="A3")
    img = Image.open(io.BytesIO(result.png)).convert("RGB")
    red = [(x, y) for x in range(img.width) for y in range(img.height) if img.getpixel((x, y)) == (255, 0, 0)]
    assert red
    xs, ys = [p[0] for p in red], [p[1] for p in red]
    scale = result.px_per_mm
    assert min(xs) / scale == pytest.approx(10, abs=0.5)
    assert max(xs) / scale == pytest.approx(20, abs=0.5)
    assert (img.height - max(ys)) / scale == pytest.approx(10, abs=0.5)
    assert (img.height - min(ys)) / scale == pytest.approx(20, abs=0.5)


@needs_librecad
def test_full_sheet_preview_warns_that_small_text_is_unreadable(tmp_path):
    dxf = write_sheet(tmp_path / "a0.dxf", "A0")
    result = preview(dxf, None, px=1600, paper="A0")
    assert max(result.width_px, result.height_px) == pytest.approx(1600, abs=1)
    assert any("pass a region" in w for w in result.warnings)


@needs_librecad
def test_preview_infers_the_smallest_paper_from_extents(sheet_a3):
    result = preview(sheet_a3, None, px=900)
    assert result.region[2] == pytest.approx(420, abs=1)
    assert result.region[3] == pytest.approx(297, abs=1)


@needs_librecad
def test_preview_grid_draws_labelled_lines(sheet_a3):
    plain = preview(sheet_a3, (90, 40, 130, 70), px=800, paper="A3")
    gridded = preview(sheet_a3, (90, 40, 130, 70), px=800, paper="A3", grid=True)
    assert gridded.png != plain.png
    assert Image.open(io.BytesIO(gridded.png)).size == Image.open(io.BytesIO(plain.png)).size


@needs_librecad
def test_preview_from_pdf_matches_preview_from_dxf(sheet_a3, tmp_path):
    pdf = LibreCadRenderer().to_pdf(sheet_a3, tmp_path / "p.pdf", "A3")
    from_pdf = preview(pdf, (50, 20, 150, 70), px=600)
    from_dxf = preview(sheet_a3, (50, 20, 150, 70), px=600, paper="A3")
    assert from_pdf.png == from_dxf.png


def test_preview_region_outside_page_is_rejected(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=200, height=100)
    pdf = tmp_path / "p.pdf"
    doc.save(pdf)
    with pytest.raises(ValueError, match="outside"):
        pdf_to_png(pdf, (500, 500, 600, 600))
    with pytest.raises(ValueError, match="x1 > x0"):
        pdf_to_png(pdf, (10, 10, 5, 20))
    with pytest.raises(ValueError, match="page 3"):
        pdf_to_png(pdf, page=3)


def test_unknown_paper_name_lists_the_valid_ones():
    with pytest.raises(ValueError, match="A0"):
        paper_size_mm("A9")
    assert paper_size_mm("a3") == (420.0, 297.0)


def test_missing_binary_is_reported_with_its_source(tmp_path, monkeypatch):
    monkeypatch.delenv("LIBRECAD", raising=False)
    with pytest.raises(BackendUnavailable, match="not found or not executable"):
        LibreCadRenderer(tmp_path / "nope").to_pdf(tmp_path / "x.dxf", tmp_path / "x.pdf", "A4")


def test_binary_from_environment_variable_is_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRECAD", str(tmp_path / "missing"))
    with pytest.raises(BackendUnavailable, match="LIBRECAD"):
        locate_librecad()


def test_missing_display_without_xvfb_is_a_clear_error(tmp_path, monkeypatch):
    dxf = write_sheet(tmp_path / "s.dxf", "A4")
    binary = fake_binary(tmp_path, "exit 0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(librecad_module.shutil, "which", lambda name: None)
    with pytest.raises(BackendUnavailable, match="DISPLAY is unset and xvfb-run is not installed"):
        LibreCadRenderer(binary).to_pdf(dxf, tmp_path / "o.pdf", "A4")


def test_missing_display_wraps_in_xvfb_run_when_available(tmp_path, monkeypatch):
    log = tmp_path / "argv.txt"
    xvfb = fake_binary(tmp_path, f'echo "$@" > {log}; exit 1', "fake-xvfb-run")
    binary = fake_binary(tmp_path, "exit 0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(librecad_module.shutil, "which", lambda name: str(xvfb) if name == "xvfb-run" else None)
    dxf = write_sheet(tmp_path / "s.dxf", "A4")
    with pytest.raises(RenderFailed):
        LibreCadRenderer(binary).to_pdf(dxf, tmp_path / "o.pdf", "A4")
    assert log.read_text().startswith(f"-a {binary} dxf2pdf")


def test_garbage_dxf_is_rejected_before_launching_librecad(tmp_path, with_display):
    bad = tmp_path / "bad.dxf"
    bad.write_text("garbage\n")
    marker = tmp_path / "launched"
    binary = fake_binary(tmp_path, f"touch {marker}")
    with pytest.raises(InvalidDrawing, match="not a readable DXF"):
        LibreCadRenderer(binary).to_pdf(bad, tmp_path / "o.pdf", "A4")
    assert not marker.exists()


def test_missing_dxf_is_rejected(tmp_path, with_display):
    binary = fake_binary(tmp_path, "exit 0")
    with pytest.raises(InvalidDrawing, match="not found"):
        LibreCadRenderer(binary).to_pdf(tmp_path / "gone.dxf", tmp_path / "o.pdf", "A4")


@pytest.mark.parametrize("units, pattern", [(6, r"INSUNITS=6.*1000x"), (0, r"INSUNITS=0")])
def test_non_mm_insunits_is_rejected_because_librecad_rescales_the_page(units, pattern, tmp_path, with_display):
    dxf = write_sheet(tmp_path / "u.dxf", "A4", units=units)
    binary = fake_binary(tmp_path, "exit 0")
    with pytest.raises(InvalidDrawing, match=pattern):
        LibreCadRenderer(binary).to_pdf(dxf, tmp_path / "o.pdf", "A4")


def test_image_entities_are_rejected_because_librecad_segfaults(tmp_path, with_display):
    dxf = write_sheet(tmp_path / "i.dxf", "A4")
    doc = ezdxf.readfile(dxf)
    idef = doc.add_image_def(filename=str(tmp_path / "x.png"), size_in_pixel=(10, 10))
    doc.modelspace().add_image(idef, insert=(10, 10), size_in_units=(10, 10))
    doc.saveas(dxf)
    with pytest.raises(InvalidDrawing, match="IMAGE.*segfaults"):
        LibreCadRenderer(fake_binary(tmp_path, "exit 0")).to_pdf(dxf, tmp_path / "o.pdf", "A4")


def test_hung_librecad_is_killed_at_the_timeout(tmp_path, with_display):
    dxf = write_sheet(tmp_path / "s.dxf", "A4")
    pidfile = tmp_path / "pid"
    binary = fake_binary(tmp_path, f"echo $$ > {pidfile}\nsleep 30")
    with pytest.raises(RenderTimeout, match="killed"):
        LibreCadRenderer(binary, timeout=0.5).to_pdf(dxf, tmp_path / "o.pdf", "A4")
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_exit_zero_without_output_is_a_failure(tmp_path, with_display):
    dxf = write_sheet(tmp_path / "s.dxf", "A4")
    binary = fake_binary(tmp_path, "echo 'Cannot open display' >&2; exit 0")
    with pytest.raises(RenderFailed, match="produced no o.pdf"):
        LibreCadRenderer(binary).to_pdf(dxf, tmp_path / "o.pdf", "A4")


def test_nonzero_exit_includes_filtered_stderr(tmp_path, with_display):
    dxf = write_sheet(tmp_path / "s.dxf", "A4")
    binary = fake_binary(tmp_path, "echo 'qt.qpa noise' >&2; echo 'xcb: could not connect to display' >&2; exit 1")
    with pytest.raises(RenderFailed, match=r"is DISPLAY reachable\?.*could not connect") as err:
        LibreCadRenderer(binary).to_pdf(dxf, tmp_path / "o.pdf", "A4")
    assert "qt.qpa" not in str(err.value)


def test_stale_output_does_not_mask_a_failed_run(tmp_path, with_display):
    dxf = write_sheet(tmp_path / "s.dxf", "A4")
    out = tmp_path / "o.pdf"
    out.write_bytes(b"%PDF-stale")
    binary = fake_binary(tmp_path, "exit 0")
    with pytest.raises(RenderFailed):
        LibreCadRenderer(binary).to_pdf(dxf, out, "A4")


def test_wrong_page_size_from_librecad_is_caught(tmp_path, with_display):
    dxf = write_sheet(tmp_path / "s.dxf", "A4")
    out = tmp_path / "o.pdf"
    doc = pymupdf.open()
    doc.new_page(width=5000, height=4000)
    donor = tmp_path / "donor.pdf"
    doc.save(donor)
    binary = fake_binary(tmp_path, f"cp {donor} {out}")
    with pytest.raises(RenderFailed, match="page is 5000.0x4000.0 pt"):
        LibreCadRenderer(binary).to_pdf(dxf, out, "A4")


def test_dwg_mismatch_report_lists_only_differing_types():
    result = DwgResult(Path("x.dwg"), "libredwg", "R2000", {"LINE": 5, "HATCH": 2, "TEXT": 1}, {"LINE": 5, "TEXT": 1, "ARC": 1})
    assert result.mismatches == {"ARC": (0, 1), "HATCH": (2, 0)}
    assert not result.verified


def test_libredwg_cannot_write_newer_versions(tmp_path, monkeypatch):
    monkeypatch.setattr("unicad.render.dwg._oda_available", lambda: False)
    monkeypatch.setattr("unicad.render.dwg._libredwg_tool", lambda name: tmp_path / name)
    dxf = write_sheet(tmp_path / "s.dxf", "A4")
    with pytest.raises(DwgUnavailable, match="R12.*R14.*R2000"):
        DwgConverter("libredwg").to_dwg(dxf, tmp_path / "o.dwg", "R2018")


def test_no_converter_gives_a_precise_reason(monkeypatch):
    monkeypatch.setattr("unicad.render.dwg._oda_available", lambda: False)
    monkeypatch.setattr("unicad.render.dwg._libredwg_tool", lambda name: None)
    assert available_converter() is None
    assert "LIBREDWG_BIN" in unavailable_reason()
    with pytest.raises(DwgUnavailable, match="no DWG converter"):
        DwgConverter().name


@needs_dwg
def test_dwg_round_trip_preserves_entity_counts(tmp_path):
    dxf = write_sheet(tmp_path / "s.dxf", "A3")
    result = DwgConverter().to_dwg(dxf, tmp_path / "s.dwg", "R2000")
    assert result.dwg.stat().st_size > 1000
    assert result.verified
    assert result.source_counts == entity_counts(dxf)
    assert result.source_counts["LWPOLYLINE"] == 1
    assert result.source_counts["SOLID"] == 1


@needs_dwg
def test_dwg_of_garbage_dxf_fails_cleanly(tmp_path):
    bad = tmp_path / "bad.dxf"
    bad.write_text("garbage\n")
    with pytest.raises(RenderFailed):
        DwgConverter().to_dwg(bad, tmp_path / "bad.dwg")
    assert not (tmp_path / "bad.dwg").exists()


@needs_dwg
def test_strict_dwg_conversion_raises_when_readback_differs(tmp_path, monkeypatch):
    dxf = write_sheet(tmp_path / "s.dxf", "A3")
    converter = DwgConverter()
    real = converter._readback_counts

    def drop_circle(dwg):
        counts = dict(real(dwg))
        counts.pop("CIRCLE")
        return counts

    monkeypatch.setattr(converter, "_readback_counts", drop_circle)
    with pytest.raises(DwgMismatch, match=r"CIRCLE"):
        converter.to_dwg(dxf, tmp_path / "s.dwg")
    lenient = converter.to_dwg(dxf, tmp_path / "s2.dwg", strict=False)
    assert lenient.mismatches == {"CIRCLE": (1, 0)}

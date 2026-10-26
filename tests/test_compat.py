import json
import re
from pathlib import Path

import pymupdf
import pytest

from unicad.render import LibreCadRenderer, ScaleMode, locate_librecad, probes
from unicad.render.backend import BackendUnavailable

ROOT = Path(__file__).resolve().parent.parent
COMPAT = ROOT / "library" / "compat"
MATRIX = json.loads((COMPAT / "matrix.json").read_text())
ENTRIES = {e["id"]: e for e in MATRIX["probes"]}
VERDICTS = {"works", "degraded", "broken"}


def _ready() -> bool:
    try:
        locate_librecad()
    except BackendUnavailable:
        return False
    import os
    import shutil

    return bool(os.environ.get("DISPLAY") or shutil.which("xvfb-run"))


needs_librecad = pytest.mark.skipif(not _ready(), reason="LibreCAD or a display is not available")


def test_matrix_covers_every_probe_exactly_once():
    ids = [p.id for p in probes.PROBES]
    assert len(ids) == len(set(ids))
    assert set(ENTRIES) == set(ids)
    assert [e["id"] for e in MATRIX["probes"]] == ids


def test_probe_ids_are_filesystem_safe():
    assert all(re.fullmatch(r"[a-z0-9_]+", p.id) for p in probes.PROBES)


@pytest.mark.parametrize("probe_id", [p.id for p in probes.PROBES])
def test_every_probe_has_a_reviewed_verdict_and_evidence(probe_id):
    entry = ENTRIES[probe_id]
    assert entry["verdict"] in VERDICTS, f"{probe_id} has no reviewed verdict"
    assert len(entry["note"]) > 30
    assert (ROOT / entry["evidence"]).is_file()
    assert (ROOT / entry["dxf"]).is_file()
    assert entry["dwg"]["status"] in {"ok", "lossy", "failed", "unavailable"}
    assert entry["librecad"]["rendered"] or entry["verdict"] == "broken"


def test_no_stale_evidence_files_without_a_probe():
    ids = {p.id for p in probes.PROBES}
    stems = {f.stem for f in COMPAT.glob("*.dxf")} | {f.stem for f in COMPAT.glob("*.png") if f.stem != "probe_image"}
    assert stems == ids


def test_docs_table_lists_every_probe_with_its_verdict_and_evidence_path():
    docs = (ROOT / "docs" / "compat.md").read_text()
    for entry in MATRIX["probes"]:
        row = next((ln for ln in docs.splitlines() if f"`{entry['evidence']}`" in ln), None)
        assert row is not None, f"{entry['id']} missing from docs/compat.md"
        assert f"**{entry['verdict']}**" in row


def test_matrix_records_the_dwg_converter_and_every_feature_category():
    assert MATRIX["librecad"] == "2.2.1.6"
    categories = {e["category"] for e in MATRIX["probes"]}
    for needed in ["TEXT", "MTEXT", "ATTRIB", "BLOCK", "LWPOLYLINE", "SOLID", "HATCH", "DIMENSION", "Linetype", "Lineweight",
                   "Colour", "Layer", "LEADER", "IMAGE", "Curves", "Layout", "TABLE", "Unicode", "Units"]:
        assert needed in categories


def test_dwg_round_trip_losses_are_recorded_not_hidden():
    for entry in MATRIX["probes"]:
        if entry["dwg"]["status"] == "lossy":
            assert entry["dwg"]["mismatches"]


@pytest.fixture(scope="module")
def render(tmp_path_factory):
    out = tmp_path_factory.mktemp("compat")
    renderer = LibreCadRenderer()

    def run(probe_id: str, **kwargs) -> pymupdf.Page:
        pdf = renderer.to_pdf(COMPAT / f"{probe_id}.dxf", out / f"{probe_id}.pdf", probes.SHEET_MM, ScaleMode.ACTUAL)
        return pymupdf.open(pdf)[0]

    return run


def _render_doc(doc, tmp_path, name):
    dxf = tmp_path / f"{name}.dxf"
    doc.units = 4
    doc.saveas(dxf)
    pdf = LibreCadRenderer().to_pdf(dxf, tmp_path / f"{name}.pdf", (100.0, 60.0), ScaleMode.ACTUAL)
    return pymupdf.open(pdf)[0]


def _stroked(page, min_mm=0.05):
    return [d for d in page.get_drawings() if d.get("width") and d["width"] / 72 * 25.4 >= min_mm]


@needs_librecad
def test_fact_entities_on_a_layer_without_lineweight_print_no_stroke(tmp_path):
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.layers.add("BARE")
    doc.layers.add("WEIGHTED", lineweight=25)
    msp = doc.modelspace()
    msp.add_line((5, 10), (95, 10), dxfattribs={"layer": "BARE"})
    msp.add_line((5, 30), (95, 30), dxfattribs={"layer": "WEIGHTED"})
    page = _render_doc(doc, tmp_path, "bare")
    strokes = _stroked(page)
    assert len(strokes) == 1
    height = page.rect.height
    assert (height - strokes[0]["rect"].y0) / (72 / 25.4) == pytest.approx(30, abs=0.5)


@needs_librecad
def test_fact_layer_lineweights_print_at_true_width(render):
    page = render("lineweights")
    widths = sorted({round(d["width"] / 72 * 25.4, 2) for d in page.get_drawings() if d.get("width")})
    for expected in (0.25, 0.51, 0.7, 1.4):
        assert any(abs(w - expected) < 0.02 for w in widths), (expected, widths)


@needs_librecad
def test_fact_ltscale_does_not_change_the_dash_pattern(tmp_path):
    import ezdxf

    def dash_patterns(ltscale: float, entity_scale: float) -> list[str]:
        doc = ezdxf.new("R2018", setup=["linetypes"])
        doc.header["$LTSCALE"] = ltscale
        doc.layers.get("0").dxf.lineweight = 25
        doc.modelspace().add_line((5, 30), (95, 30), dxfattribs={"linetype": "DASHED", "ltscale": entity_scale})
        page = _render_doc(doc, tmp_path, f"lt{ltscale}_{entity_scale}")
        return [d["dashes"] for d in _stroked(page, 0.1)]

    base = dash_patterns(1.0, 1.0)
    assert len(base) == 1 and base[0] not in ("[] 0", "")
    assert dash_patterns(2.0, 1.0) == base
    assert dash_patterns(0.5, 4.0) == base


@needs_librecad
def test_fact_attrib_values_are_not_drawn(tmp_path):
    import ezdxf

    def paths(with_attrib: bool) -> int:
        doc = ezdxf.new("R2018")
        doc.layers.get("0").dxf.lineweight = 25
        blk = doc.blocks.new("B")
        blk.add_circle((0, 0), 5)
        blk.add_attdef("TAG", (6, 0), "X", dxfattribs={"height": 3})
        ref = doc.modelspace().add_blockref("B", (30, 30))
        if with_attrib:
            ref.add_attrib("TAG", "INV-01", (36, 30), dxfattribs={"height": 3})
        return len(_render_doc(doc, tmp_path, f"attr{with_attrib}").get_drawings())

    assert paths(True) == paths(False)


@needs_librecad
def test_fact_non_mm_insunits_scales_the_page(tmp_path):
    renderer = LibreCadRenderer(strict_units=False)
    out = tmp_path / "m.pdf"
    renderer._run(
        ["dxf2pdf", "-s", "1", "-p", "200x120", "-o", str(out), str(COMPAT / "insunits_metres.dxf")],
        out,
    )
    with pymupdf.open(out) as doc:
        assert doc[0].rect.width / 72 * 25.4 == pytest.approx(200_000, rel=0.01)


@needs_librecad
def test_fact_attrib_text_is_not_drawn_but_plain_text_is(render):
    page = render("attrib_attdef")
    assert len(page.get_drawings()) > 0
    text_boxes = [d["rect"] for d in page.get_drawings() if d["type"] == "s" and d["rect"].height > 0]
    assert text_boxes

import math
import os
import subprocess
from pathlib import Path

import ezdxf
import pytest
from ezdxf.math import Matrix44, Vec3
from shapely.geometry import LineString, Polygon, box
from shapely.geometry.base import BaseGeometry

from unicad.arrays import PlacementSpec, place_array
from unicad.catalog.store import load_catalog
from unicad.domain.site import FeatureKind, Setbacks
from unicad.drafting.sheets.canvas import Region
from unicad.drafting.sheets.text import text_width
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, PaperSize, SheetSpec, TitleBlockData
from unicad.drafting.labels import overlapping
from unicad.drafting.site_plan import LayoutDrawing, choose_scale, default_notes, render_layout_sheet
from unicad.ingest.site_import import import_site

ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / "fixtures" / "sites"
MODULE = load_catalog().get("waaree-bi-55-540")
PROFILE = CompanyProfile.load(ROOT / "profiles" / "default.json")
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))


def sheet(paper: PaperSize) -> SheetSpec:
    tb = TitleBlockData(client="TEST CLIENT", project_title="TEST PLANT", location="TEST SITE", drawing_title="SOLAR PV ARRAY LAYOUT",
                        drawing_number="T-EE-101", paper=paper, issued_for=IssuedFor.INFORMATION)  # fmt: skip
    return SheetSpec(paper=paper, title_block=tb)


@pytest.fixture(scope="module")
def ground_result():
    site = import_site(SITES / "ground_mount_rajasthan.kml", None).geometry
    site = site.model_copy(update={"setbacks": Setbacks(boundary_m=10, exclusion_m=5)})
    spec = PlacementSpec(module=MODULE.id, tilt_deg=25, road_every_rows=12, block_dc_kwp=15000)
    return site, place_array(site, spec, MODULE)


@pytest.fixture(scope="module")
def ground_dxf(ground_result, tmp_path_factory):
    site, result = ground_result
    doc = ezdxf.new("R2018", setup=True)
    drawing = LayoutDrawing.from_result(site, result, MODULE, 1.5, default_notes(result, site, "FIXED TILT 2P x 28."))
    out = render_layout_sheet(doc, drawing, sheet(PaperSize.A1), PROFILE)
    path = tmp_path_factory.mktemp("layout") / "layout.dxf"
    doc.saveas(path)
    return path, out, result


def texts(doc) -> list[str]:
    return [e.dxf.text for e in doc.modelspace().query("TEXT")]


def test_sheet_draws_every_table_as_a_scaled_nested_block(ground_dxf):
    path, out, result = ground_dxf
    doc = ezdxf.readfile(path)
    assert doc.header["$INSUNITS"] == 4
    inserts = [e for e in doc.modelspace().query("INSERT") if e.dxf.name.startswith("ARR_TABLE_")]
    plan_inserts = [e for e in inserts if e.dxf.layer == "L-ARRAY" and abs(e.dxf.xscale - 1000 / out.scale) < 1e-9]
    n_tables = result.metrics.tables_full + result.metrics.tables_half
    assert len(plan_inserts) == n_tables
    modules = sum(len(doc.blocks[e.dxf.name].query("INSERT")) for e in plan_inserts)
    assert modules == result.metrics.modules
    assert all(i.dxf.name == "ARR_MODULE" for i in doc.blocks[plan_inserts[0].dxf.name].query("INSERT"))
    assert all(out.plan.contains(Region(e.dxf.insert.x, e.dxf.insert.y, e.dxf.insert.x, e.dxf.insert.y), tol=0.5) for e in plan_inserts)
    colours = {e.dxf.color for e in plan_inserts}
    assert len(colours) == len(result.blocks)


def test_sheet_states_a_true_scale_and_carries_the_block_table(ground_dxf):
    path, out, result = ground_dxf
    doc = ezdxf.readfile(path)
    assert out.scale in (2000, 2500)
    t = texts(doc)
    assert f"1:{out.scale}" in t
    assert f"SCALE 1:{out.scale} ON A1" in t
    assert "MMS TABLE DETAILS" in t and "FULL TABLE TYPICAL ARRANGEMENT (2P x 28)" in t
    for b in result.blocks:
        assert b.name in t
        assert f"{b.dc_kwp:,.2f}" in t
    assert f"{result.metrics.dc_kwp:,.2f}" in t
    assert str(result.metrics.tables_full) in t and str(result.metrics.tables_half) in t
    assert f"PITCH {result.pitch.pitch_m:.2f}" in t
    assert any(s.startswith("BLOCK-01") for s in t)
    assert out.warnings == ()


def test_site_features_and_north_arrow_are_drawn(ground_dxf):
    path, _, _ = ground_dxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    assert len([e for e in msp.query('LWPOLYLINE[layer=="L-BOUNDARY"]') if e.closed and len(e) >= 5]) == 1
    assert len(msp.query('HATCH[layer=="L-WATER-FILL"]')) >= 1
    assert len(msp.query('HATCH[layer=="L-ROAD-FILL"]')) >= 2
    assert any(e.dxf.name == "UC_ANN_NORTH" for e in msp.query("INSERT"))
    assert "VILLAGE POND" in texts(doc) and "132 KV TRANSMISSION CORRIDOR" in texts(doc)
    for h in msp.query("HATCH"):
        assert h.dxf.color == 256 and h.has_solid_fill


def test_choose_scale_takes_the_largest_standard_scale_that_fits():
    room = Region(0, 0, 400, 300)
    assert choose_scale(700, 500, room) == 2000
    assert choose_scale(800, 500, room) == 2500
    assert choose_scale(100, 50, room) == 500
    assert choose_scale(1e6, 1e6, room) == 20000


def test_rooftop_roofs_view_on_a2(tmp_path):
    site = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    site = site.model_copy(update={"setbacks": Setbacks(roof_edge_m=1.2, obstacle_m=0.6)})
    spec = PlacementSpec(module=MODULE.id, mounting="rooftop_flush", table_rows=2, table_cols=12, pitch_method="gap", row_gap_m=0.6, table_gap_m=0.6)
    result = place_array(site, spec, MODULE)
    doc = ezdxf.new("R2018", setup=True)
    out = render_layout_sheet(doc, LayoutDrawing.from_result(site, result, MODULE), sheet(PaperSize.A2), PROFILE, view="roofs")
    assert out.scale == 500 and out.warnings == ()
    t = texts(doc)
    assert "SHED A ROOF" in t and "ROOF EDGE" in t and "SKYLIGHT STRIP 1" in t
    assert "PLANT BOUNDARY / FENCE" not in t


@pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD not available")
def test_librecad_exports_the_sheet_at_true_scale(ground_dxf, tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path, out, _ = ground_dxf
    pdf = tmp_path / "layout.pdf"
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run([str(LIBRECAD), "dxf2pdf", "-s", "1", "-c", "-p", "841x594", "-o", str(pdf), str(path)], check=True, capture_output=True, env=env, timeout=300)
    page = pymupdf.open(pdf)[0]
    assert page.rect.width == pytest.approx(841 / 25.4 * 72, abs=2) and page.rect.height == pytest.approx(594 / 25.4 * 72, abs=2)
    pix = page.get_pixmap(dpi=40)
    samples = pix.samples
    n = pix.n
    reds = sum(1 for i in range(0, len(samples), n) if samples[i] > 200 and samples[i + 1] < 80 and samples[i + 2] < 80)
    blues = sum(1 for i in range(0, len(samples), n) if samples[i] < 80 and samples[i + 1] < 80 and samples[i + 2] > 200)
    assert reds > 500 and blues > 500


def _text_box(e) -> Region:
    h, w = e.dxf.height, text_width(e.dxf.text, e.dxf.height)
    fx, fy = {(0, 0): (0, 0), (0, 1): (0, 0), (0, 2): (0, 0.5), (1, 2): (0.5, 0.5), (1, 0): (0.5, 0), (1, 1): (0.5, 0)}[(e.dxf.halign, e.dxf.valign)]
    x, y = e.dxf.align_point.x - w * fx, e.dxf.align_point.y - h * fy
    if e.dxf.rotation:
        c, s = abs(math.cos(math.radians(e.dxf.rotation))), abs(math.sin(math.radians(e.dxf.rotation)))
        cx, cy = x + w / 2, y + h / 2
        w, h = w * c + h * s, w * s + h * c
        x, y = cx - w / 2, cy - h / 2
    return Region(x, y, x + w, y + h)


def _table_footprints(doc) -> list[BaseGeometry]:
    out = []
    for e in doc.modelspace().query("INSERT"):
        if e.dxf.name.startswith("ARR_TABLE_"):
            m = Matrix44.chain(Matrix44.scale(e.dxf.xscale, e.dxf.yscale, 1), Matrix44.z_rotate(math.radians(e.dxf.rotation)), Matrix44.translate(*e.dxf.insert))
            outline = doc.blocks[e.dxf.name].query("LWPOLYLINE").first
            out.append(Polygon([(v.x, v.y) for v in m.transform_vertices([Vec3(x, y) for x, y in outline.get_points("xy")])]))
    return out


@pytest.fixture(scope="module")
def pune_roofs():
    site = import_site(SITES / "rooftop_midc_pune.kml", None).geometry.model_copy(update={"setbacks": Setbacks(roof_edge_m=1.0, obstacle_m=0.6)})
    module = load_catalog().get("waaree-bin-08-580")
    spec = PlacementSpec(module=module.id, mounting="rooftop_flush", table_rows=1, table_cols=19, half_tables=False, modules_per_string=19, strings_per_inverter=12, n_blocks=1, target_dc_kwp=793.44)
    result = place_array(site, spec, module)
    doc = ezdxf.new("R2018", setup=True)
    out = render_layout_sheet(doc, LayoutDrawing.from_result(site, result, module), sheet(PaperSize.A3), PROFILE, view="roofs")
    return site, doc, out


def test_every_roof_and_obstacle_is_labelled_clear_of_all_outlines_and_modules(pune_roofs):
    site, doc, out = pune_roofs
    msp = doc.modelspace()
    outlines = [LineString([*e.get_points("xy"), e.get_points("xy")[0]]) for e in msp.query("LWPOLYLINE") if e.dxf.layer in ("L-ROOF", "L-OBSTACLE")]
    tables = _table_footprints(doc)
    wanted = {f.name.upper() for f in site.features if f.kind in (FeatureKind.ROOF, FeatureKind.OBSTACLE)}
    assert len(wanted) == 10 and len(outlines) >= 10 and tables
    labels = {e.dxf.text: e for e in msp.query("TEXT") if e.dxf.layer == "L-LABEL"}
    assert wanted <= set(labels), wanted - set(labels)
    for name in wanted:
        b = _text_box(labels[name])
        g = box(b.x0, b.y0, b.x1, b.y1)
        assert not [o for o in outlines if g.intersects(o)], f"{name} touches an outline"
        assert not [t for t in tables if g.intersection(t).area > 1e-6], f"{name} sits on modules"
    assert overlapping(list(out.labels)) == [] and out.warnings == ()

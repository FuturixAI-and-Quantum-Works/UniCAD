"""Build the array layout samples in library/layout/ from the site fixtures: DXF, LibreCAD PDF and a PNG of the PDF.

Per sample this writes <name>.dxf, <name>.pdf (LibreCAD dxf2pdf -s 1 -c, true scale), <name>.png (PDF rasterised with
PyMuPDF), and <name>.json with the placement metrics. Set LIBRECAD to the AppRun/binary.
"""

import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import ezdxf
import pymupdf

from unicad.arrays import PlacementResult, PlacementSpec, place_array
from unicad.catalog.store import load_catalog
from unicad.domain.site import FeatureKind, LonLat, Setbacks, SiteGeometry
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, PaperSize, Revision, SheetSpec, TitleBlockData
from unicad.drafting.site_plan import LayoutDrawing, default_notes, render_layout_sheet
from unicad.ingest.site_import import import_site

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "library" / "layout"
FIXTURES = ROOT / "fixtures" / "sites"
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
PNG_WIDTH_PX = {PaperSize.A0: 7200, PaperSize.A1: 5400, PaperSize.A2: 4500, PaperSize.A3: 3600, PaperSize.A4: 3600}
MODULE = "waaree-bi-55-540"


def sheet_spec(paper: PaperSize, title: str, number: str, location: str) -> SheetSpec:
    tb = TitleBlockData(
        client="DEMO RENEWABLES PVT. LTD.", project_title="SOLAR PV POWER PLANT (SYNTHETIC SAMPLE SITE)", location=location,
        drawing_title=title, drawing_number=number, revision="00", scale="NTS", paper=paper, drawn_by="UC", checked_by="", approved_by="",
        drawn_date=date(2026, 10, 27), issued_for=IssuedFor.INFORMATION,
    )  # fmt: skip
    return SheetSpec(paper=paper, title_block=tb, revisions=(Revision(rev="00", date=date(2026, 10, 27), description="FIRST ISSUE (GENERATED SAMPLE)", by="UC"),))


def spec_text(spec: PlacementSpec) -> str:
    o = spec.orientation.value[0].upper()
    if spec.mounting.is_tracker:
        return f"SINGLE-AXIS TRACKER {spec.table_rows}{o} x {spec.table_cols}, AXIS {spec.axis_azimuth_deg:g} DEG FROM GRID NORTH."
    return f"{spec.mounting.value.replace('_', ' ').upper()} TABLE {spec.table_rows}{o} x {spec.table_cols}, TILT {spec.tilt_deg:g} DEG, FACING AZIMUTH {spec.azimuth_deg:g} DEG."


def librecad(*args: str) -> None:
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run([str(LIBRECAD), *args], check=True, capture_output=True, env=env, timeout=300)


def export(dxf: Path, paper: PaperSize) -> None:
    w, h = paper.size_mm
    pdf = dxf.with_suffix(".pdf")
    librecad("dxf2pdf", "-s", "1", "-c", "-p", f"{w:g}x{h:g}", "-o", str(pdf), str(dxf))
    page = pymupdf.open(pdf)[0]
    if abs(page.rect.width - w / 25.4 * 72) > 2 or abs(page.rect.height - h / 25.4 * 72) > 2:
        raise RuntimeError(f"{pdf.name}: page is {page.rect.width:.0f} x {page.rect.height:.0f} pt, expected {paper.value}")
    page.get_pixmap(dpi=round(PNG_WIDTH_PX[paper] / (w / 25.4))).save(dxf.with_suffix(".png"))


def build(name: str, site: SiteGeometry, spec: PlacementSpec, paper: PaperSize, title: str, location: str, view: str = "site",
          result: PlacementResult | None = None) -> PlacementResult:  # fmt: skip
    module = load_catalog().get(spec.module)
    t = time.perf_counter()
    result = result or place_array(site, spec, module)
    elapsed = time.perf_counter() - t
    doc = ezdxf.new("R2018", setup=True)
    profile = CompanyProfile.load(ROOT / "profiles" / "default.json")
    drawing = LayoutDrawing.from_result(site, result, module, spec.table_gap_m, default_notes(result, site, spec_text(spec)))
    sheet = render_layout_sheet(doc, drawing, sheet_spec(paper, title, f"UDEMO-DEMO-{name.upper()[:10]}-EE-101", location), profile, view=view)  # type: ignore[arg-type]
    dxf = OUT / f"{name}.dxf"
    doc.saveas(dxf)
    for w in sheet.warnings:
        print(f"  warning: {w}")
    m = result.metrics
    print(f"{name}: {m.dc_kwp:,.1f} kWp, {m.tables_full} full + {m.tables_half} half tables, {len(result.blocks)} blocks, "
          f"pitch {m.pitch_m} m, GCR {m.gcr}, balance {m.block_balance:.4f}, placed in {elapsed:.2f} s, 1:{sheet.scale} on {paper.value}")  # fmt: skip
    summary = {
        "metrics": m.model_dump(), "pitch": result.pitch.model_dump(mode="json"), "scale": f"1:{sheet.scale}", "paper": paper.value,
        "blocks": [b.model_dump(mode="json") for b in result.blocks], "assumptions": list(result.assumptions), "warnings": list(result.warnings),
        "target_reached": result.target_reached, "placement_seconds": round(elapsed, 3),
    }  # fmt: skip
    (OUT / f"{name}.json").write_text(json.dumps(summary, indent=1) + "\n")
    if LIBRECAD.exists():
        export(dxf, paper)
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if not LIBRECAD.exists():
        print(f"LibreCAD not found at {LIBRECAD}; writing DXF only", file=sys.stderr)
    ground = import_site(FIXTURES / "ground_mount_rajasthan.kml", None).geometry
    ground = ground.model_copy(update={"setbacks": Setbacks(boundary_m=10, exclusion_m=5)})
    fixed = PlacementSpec(module=MODULE, table_rows=2, table_cols=28, tilt_deg=25, road_every_rows=12, road_width_m=6, block_dc_kwp=15000)
    loc = "PHALODI, JODHPUR, RAJASTHAN (SYNTHETIC)"
    build("ground-fixed-A1", ground, fixed, PaperSize.A1, "SOLAR PV ARRAY LAYOUT - FIXED TILT 2P x 28", loc)
    build("ground-fixed-A0", ground, fixed.model_copy(update={"target_dc_kwp": 60000.0, "fill": "nearest", "fill_from": {"x": 500, "y": 400}}),
          PaperSize.A0, "SOLAR PV ARRAY LAYOUT - 60 MWp FIXED TILT", loc)  # fmt: skip
    tracker = PlacementSpec(module=MODULE, mounting="tracker_1p", table_rows=1, table_cols=56, half_cols=28, road_every_rows=20, road_width_m=6, n_blocks=5)
    build("ground-tracker-A1", ground, tracker, PaperSize.A1, "SOLAR PV ARRAY LAYOUT - SINGLE AXIS TRACKER 1P x 56", loc)
    roof = import_site(FIXTURES / "rooftop_midc_pune.kml", None).geometry
    roof = roof.model_copy(update={"setbacks": Setbacks(roof_edge_m=1.2, obstacle_m=0.6)})
    flush = PlacementSpec(module=MODULE, mounting="rooftop_flush", table_rows=2, table_cols=12, half_cols=6, pitch_method="gap", row_gap_m=0.6,
                          table_gap_m=0.6, walkway_every_tables=4, walkway_width_m=1.2, modules_per_string=24)  # fmt: skip
    loc = "MIDC CHAKAN, PUNE, MAHARASHTRA (SYNTHETIC)"
    result = build("rooftop-flush-A2", roof, flush, PaperSize.A2, "ROOFTOP SOLAR PV MODULE LAYOUT - ROOFS", loc, view="roofs")
    build("rooftop-flush-A3", roof, flush, PaperSize.A3, "ROOFTOP SOLAR PV MODULE LAYOUT - PLOT", loc, result=result)
    kutch = import_site(FIXTURES / "site_plan_kutch.dxf", None, origin=LonLat(lon=69.8, lat=23.45)).geometry
    kutch = kutch.with_features(f.model_copy(update={"width_m": 6.0}) if f.kind is FeatureKind.ROAD and f.width_m is None else f for f in kutch.features)
    build("kutch-fixed-A1", kutch.model_copy(update={"setbacks": Setbacks(boundary_m=10, exclusion_m=5)}),
          fixed.model_copy(update={"tilt_deg": 22, "block_dc_kwp": 8000.0}), PaperSize.A1, "SOLAR PV ARRAY LAYOUT - FIXED TILT 2P x 28",
          "KUTCH, GUJARAT (SYNTHETIC; ACCESS ROAD WIDTH 6 m ADDED FOR THE SAMPLE)")  # fmt: skip


if __name__ == "__main__":
    main()

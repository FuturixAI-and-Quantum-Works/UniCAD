"""Build the earthing and LA layout samples in library/earthing/: Rajasthan ground mount (A1 and A0 earthing, A1 LA) and Pune
rooftop (A2 earthing, A3 LA). Per sample: <name>.dxf, LibreCAD <name>.pdf (dxf2pdf -s 1 -c), <name>.png, and <name>.json with
the design summary and quantities. Set LIBRECAD to the AppRun/binary."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import ezdxf
import pymupdf

from unicad.catalog.store import load_catalog
from unicad.drafting.earthing_plan import earthing_notes, lps_notes, render_earthing_sheet, render_lps_sheet
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, PaperSize, Revision, SheetSpec, TitleBlockData
from unicad.earthing import design_earthing, summary
from unicad.earthing.samples import pune, rajasthan
from unicad.lps import coverage_points

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "library" / "earthing"
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
PNG_WIDTH_PX = {PaperSize.A0: 7200, PaperSize.A1: 5400, PaperSize.A2: 4500, PaperSize.A3: 3600, PaperSize.A4: 3600}


def sheet_spec(paper: PaperSize, title: str, number: str, location: str, notes: tuple[str, ...]) -> SheetSpec:
    tb = TitleBlockData(
        client="DEMO RENEWABLES PVT. LTD.", project_title="SOLAR PV POWER PLANT (SYNTHETIC SAMPLE SITE)", location=location,
        drawing_title=title, drawing_number=number, revision="00", scale="NTS", paper=paper, drawn_by="UC", drawn_date=date(2026, 10, 27),
        issued_for=IssuedFor.INFORMATION,
    )  # fmt: skip
    return SheetSpec(paper=paper, title_block=tb, notes=notes, revisions=(Revision(rev="00", date=date(2026, 10, 27), description="FIRST ISSUE (GENERATED SAMPLE)", by="UC"),))


def export(dxf: Path, paper: PaperSize) -> None:
    w, h = paper.size_mm
    pdf = dxf.with_suffix(".pdf")
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run([str(LIBRECAD), "dxf2pdf", "-s", "1", "-c", "-p", f"{w:g}x{h:g}", "-o", str(pdf), str(dxf)], check=True, capture_output=True, env=env, timeout=600)
    page = pymupdf.open(pdf)[0]
    if abs(page.rect.width - w / 25.4 * 72) > 2 or abs(page.rect.height - h / 25.4 * 72) > 2:
        raise RuntimeError(f"{pdf.name}: page is {page.rect.width:.0f} x {page.rect.height:.0f} pt, expected {paper.value}")
    page.get_pixmap(dpi=round(PNG_WIDTH_PX[paper] / (w / 25.4))).save(dxf.with_suffix(".png"))


def write(name: str, doc, paper: PaperSize, warnings) -> Path:
    dxf = OUT / f"{name}.dxf"
    doc.saveas(dxf)
    for w in warnings:
        print(f"  warning: {w}")
    if LIBRECAD.exists():
        export(dxf, paper)
    return dxf


def build(key: str, project, loc: str, earthing_papers: tuple[PaperSize, ...], lps_paper: PaperSize) -> dict:
    catalog = load_catalog()
    profile = CompanyProfile.load(ROOT / "profiles" / "default.json")
    d = design_earthing(project)
    for paper in earthing_papers:
        doc = ezdxf.new("R2018", setup=True)
        spec = sheet_spec(paper, "EARTHING LAYOUT", f"UDEMO-DEMO-{key.upper()}-EE-301", loc, earthing_notes(d))
        s = render_earthing_sheet(doc, project, catalog, d, spec, profile)
        write(f"{key}-earthing-{paper.value}", doc, paper, s.warnings)
        print(f"{key}-earthing-{paper.value}: 1:{s.scale}")
    doc = ezdxf.new("R2018", setup=True)
    spec = sheet_spec(lps_paper, "LIGHTNING ARRESTER (LA) LAYOUT", f"UDEMO-DEMO-{key.upper()}-EE-302", loc, lps_notes(d.lps))
    s = render_lps_sheet(doc, project, catalog, d.lps, spec, profile)
    write(f"{key}-lps-{lps_paper.value}", doc, lps_paper, s.warnings)
    ok, n = coverage_points(d.lps)
    out = summary(d) | {"lps": {"method": d.lps.method, "level": d.lps.level, "radius_m": d.lps.radius_m, "terminals": len(d.lps.terminals), "tables_covered": ok, "tables": n, "uncovered": len(d.lps.uncovered)}}
    out.pop("sources")
    (OUT / f"{key}.json").write_text(json.dumps(out, indent=1) + "\n")
    q = {x["description"]: f"{x['quantity']:g} {x['unit']}" for x in out["quantities"]}
    print(f"{key}: {out['pits']} pits {out['pits_by_role']}, {out['resistance_ohm']} ohm (target {out['target_ohm']}), "
          f"{len(d.lps.terminals)} terminals R={d.lps.radius_m} m, tables covered {ok}/{n}, unbonded {len(d.unbonded)}")  # fmt: skip
    for k, v in q.items():
        print(f"    {v:>12}  {k}")
    return out


def main() -> None:
    ezdxf.options.write_fixed_meta_data_for_testing = True
    OUT.mkdir(parents=True, exist_ok=True)
    if not LIBRECAD.exists():
        print(f"LibreCAD not found at {LIBRECAD}; writing DXF only", file=sys.stderr)
    build("rajasthan", rajasthan()[0], "PHALODI, JODHPUR, RAJASTHAN (SYNTHETIC)", (PaperSize.A1, PaperSize.A0), PaperSize.A1)
    build("pune", pune()[0], "MIDC CHAKAN, PUNE, MAHARASHTRA (SYNTHETIC)", (PaperSize.A2,), PaperSize.A3)


if __name__ == "__main__":
    main()

"""Build the string diagram samples in library/strings/: the 500 kWp rooftop fixture (A3), and two ground projects on the Rajasthan site
laid out with place_array and built with autobuild (A1), one with string combiner boxes and central inverters, one with string inverters.

Per sample this writes
  <sample>-<type>[-<page>].dxf   one sheet per inverter or SCB type
  <sample>-<type>[-<page>].png   the sheet printed through LibreCAD and rasterised with pdftoppm
  <sample>-strings.pdf           all sheets of the sample in one PDF
  <sample>-strings.json          the sheets with their numbers, the instances each stands for, and per instance its strings and tables

Set LIBRECAD to the AppRun/binary; defaults to /tmp/opencode/proto/squashfs-root/AppRun.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from unicad.arrays import PlacementSpec, place_array
from unicad.catalog.store import Catalog, load_catalog
from unicad.design import autobuild
from unicad.domain.network import ElectricalNetwork
from unicad.domain.project import Project, Segment
from unicad.domain.site import Setbacks
from unicad.drafting.sheets.model import CompanyProfile
from unicad.drafting.string_diagram import build_string_diagram_sheets, plan_types
from unicad.ingest.site_import import import_site
from unicad.render import LibreCadRenderer, Sheet
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "library" / "strings"
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
PNG_DPI = {"A0": 110, "A1": 120, "A2": 200, "A3": 150, "A4": 200}
GROUND_MODULE = "vikram-solar-hypersol-m10r-144-580-wp"
CENTRAL_INVERTER = "sma-sc-4400-up"
STRING_INVERTER = "sungrow-sg350hx"


def fixture(catalog: Catalog, name: str) -> Project:
    return parse_project((ROOT / "fixtures" / "projects" / f"{name}.json").read_text(encoding="utf-8"), catalog=catalog)


def blank(catalog: Catalog, name: str, **update) -> Project:
    p = fixture(catalog, name)
    update = {k: Segment(v) if k == "segment" else v for k, v in update.items()}
    return p.model_copy(update={"network": ElectricalNetwork(), "array_layout": None, "sheets": (), **update})


def rooftop(catalog: Catalog) -> Project:
    return fixture(catalog, "rooftop_500kwp")


def ground_central(catalog: Catalog) -> Project:
    """About 22 MWp on the Rajasthan site: 28-module strings from place_array, 23 strings per SCB, 4400 kVA central inverters."""
    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry.model_copy(update={"setbacks": Setbacks(boundary_m=10, exclusion_m=5)})
    spec = PlacementSpec(module=GROUND_MODULE, tilt_deg=25, table_cols=28, road_every_rows=12, block_dc_kwp=11000, target_dc_kwp=22000, latitude_deg=27.0, modules_per_string=28)
    placed = place_array(site, spec, catalog.get(GROUND_MODULE))
    return autobuild(blank(catalog, "utility_50mwac"), catalog, {"template": "utility_central", "module": GROUND_MODULE, "inverter": CENTRAL_INVERTER}, layout=placed).project


def ground_string(catalog: Catalog) -> Project:
    """About 6 MWp on the Rajasthan site: 28-module strings, 24 strings per 352 kVA string inverter, four inverters to a block."""
    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry.model_copy(update={"setbacks": Setbacks(boundary_m=10, exclusion_m=5)})
    spec = PlacementSpec(
        module=GROUND_MODULE, tilt_deg=25, table_cols=28, road_every_rows=12, block_dc_kwp=1560, target_dc_kwp=6240, latitude_deg=27.0, modules_per_string=28, strings_per_inverter=24
    )
    placed = place_array(site, spec, catalog.get(GROUND_MODULE))
    return autobuild(blank(catalog, "utility_50mwac"), catalog, {"template": "utility_string", "module": GROUND_MODULE, "inverter": STRING_INVERTER}, layout=placed).project


SAMPLES = {"rooftop_500kwp": rooftop, "ground_central": ground_central, "ground_string": ground_string}


def librecad(*args: str) -> None:
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run([str(LIBRECAD), *args], check=True, capture_output=True, env=env, timeout=600)


def index(project: Project, catalog: Catalog, sheets) -> dict:
    types = {t.sink: t for t in plan_types(project, catalog)[0]}
    out = []
    for r in sheets:
        t = types[r.sink_template]
        out.append({
            "sheet": r.number, "title": r.title, "paper": r.paper.value, "file": r.path.name if r.path else None, "warnings": list(r.warnings),
            "type": r.sink_template, "typical_for": t.typical, "shown_for": t.representative.tag, "strings_drawn": len(r.strings), "strings_covered": len(r.covers),
            "instances": [{"tag": m.tag, "strings": len(m.strings), "tables": list(m.tables), "to": m.destination} for m in t.members],
        })  # fmt: skip
    return {"project": project.id, "sheets": out}


def main() -> None:
    catalog = load_catalog()
    profile = CompanyProfile.load(ROOT / "profiles" / "default.json")
    OUT.mkdir(parents=True, exist_ok=True)
    if not LIBRECAD.exists():
        print(f"LibreCAD not found at {LIBRECAD}; writing DXF only")
    for name, make in SAMPLES.items():
        project = make(catalog)
        sheets = build_string_diagram_sheets(project, catalog, profile, OUT / name)
        for old in (OUT / name).glob("*.png"):
            old.unlink()
        pdf_sheets = []
        for r in sheets:
            assert r.path is not None
            print(f"{name}/{r.path.name}: {r.number} {r.paper.value} u={r.u:.2f} {len(r.strings)} strings drawn, covers {len(r.covers)}; warnings {list(r.warnings)}")
            pdf_sheets.append(Sheet(r.path, r.paper.value, r.title))
        if LIBRECAD.exists():
            pdf = LibreCadRenderer(binary=LIBRECAD).build_set(pdf_sheets, OUT / name / f"{name}-strings.pdf").pdf
            if shutil.which("pdftoppm"):
                for i, r in enumerate(sheets, start=1):
                    subprocess.run(["pdftoppm", "-png", "-r", str(PNG_DPI[r.paper.value]), "-f", str(i), "-l", str(i), "-singlefile", str(pdf), str(r.path.with_suffix(""))], check=True)
        (OUT / name / f"{name}-strings.json").write_text(json.dumps(index(project, catalog, sheets), indent=1) + "\n")


if __name__ == "__main__":
    main()

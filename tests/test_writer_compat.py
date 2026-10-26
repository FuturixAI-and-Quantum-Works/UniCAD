"""Every drafting writer obeys the DXF writer rules of docs/compat.md."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

from unicad.arrays import PlacementSpec, place_array
from unicad.catalog.store import load_catalog
from unicad.domain.site import Setbacks
from unicad.drafting import dxfrules
from unicad.drafting.legend import legend_drawer
from unicad.drafting.sheets import samples
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, PaperSize, SheetSpec, TitleBlockData
from unicad.drafting.sheets.render import render_sheet
from unicad.drafting.site_plan import LayoutDrawing, default_notes, render_layout_sheet
from unicad.drafting.sld import render_sld
from unicad.drafting.sld.sheet import dxf_text
from unicad.drafting.symbols.catalogue import build_catalogue
from unicad.ingest.site_import import import_site
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def profile():
    return CompanyProfile.load(ROOT / "profiles" / "default.json")


def _sld(fixture: str, catalog, profile):
    project = parse_project((ROOT / "fixtures" / "projects" / f"{fixture}.json").read_text(encoding="utf-8"), catalog)
    return render_sld(project, "sld", profile, catalog)[0]


def _sheet(profile):
    doc = ezdxf.new("R2018", setup=["linetypes"])
    render_sheet(doc, samples.utility_100mwac(PaperSize.A1), profile, legend_drawer(doc.modelspace()))
    return doc


def _site_plan(profile):
    module = load_catalog().get("waaree-bi-55-540")
    site = import_site(ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml", None).geometry.model_copy(update={"setbacks": Setbacks(boundary_m=10, exclusion_m=5)})
    result = place_array(site, PlacementSpec(module=module.id, tilt_deg=25, road_every_rows=12, block_dc_kwp=15000), module)
    tb = TitleBlockData(client="TEST CLIENT", project_title="TEST PLANT", location="TEST SITE", drawing_title="SOLAR PV ARRAY LAYOUT",
                        drawing_number="T-EE-101", paper=PaperSize.A1, issued_for=IssuedFor.INFORMATION)  # fmt: skip
    doc = ezdxf.new("R2018", setup=True)
    drawing = LayoutDrawing.from_result(site, result, module, 1.5, default_notes(result, site, "FIXED TILT 2P x 28."))
    render_layout_sheet(doc, drawing, SheetSpec(paper=PaperSize.A1, title_block=tb), profile)
    return doc


@pytest.mark.parametrize("name", ["rooftop_sld", "utility_sld", "symbol_library", "sample_sheet", "site_plan"])
def test_writer_output_follows_the_compat_rules(name, catalog, profile):
    doc = {
        "rooftop_sld": lambda: _sld("rooftop_500kwp", catalog, profile),
        "utility_sld": lambda: _sld("utility_50mwac", catalog, profile),
        "symbol_library": lambda: build_catalogue(columns=5, groups=2),
        "sample_sheet": lambda: _sheet(profile),
        "site_plan": lambda: _site_plan(profile),
    }[name]()
    assert dxfrules.violations(doc) == []
    assert doc.header["$INSUNITS"] == 4
    assert doc.header["$DIMTXT"] >= 1.5 and doc.header["$DIMASZ"] >= 1.5
    used = {e.dxf.layer for e in doc.modelspace()}
    assert all(doc.layers.get(n).dxf.lineweight >= dxfrules.MIN_LINEWEIGHT for n in used)


def test_site_plan_dxf_bytes_are_deterministic(profile):
    first = dxf_text(_site_plan(profile))
    assert dxf_text(_site_plan(profile)) == first
    script = (
        "import hashlib, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
        "import test_writer_compat as t\n"
        "from unicad.drafting.sheets.model import CompanyProfile\n"
        "from unicad.drafting.sld.sheet import dxf_text\n"
        f"profile = CompanyProfile.load(t.ROOT / 'profiles' / 'default.json')\n"
        "print(hashlib.sha256(dxf_text(t._site_plan(profile)).encode()).hexdigest())\n"
    )
    digests = set()
    for seed in ("1", "2"):
        done = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, timeout=300)
        assert done.returncode == 0, done.stderr
        digests.add(done.stdout.strip())
    assert digests == {hashlib.sha256(first.encode()).hexdigest()}


def test_the_checker_flags_each_rule():
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    doc.layers.add("NO-LW")
    doc.styles.add("ARIAL", font="arial.ttf")
    msp = doc.modelspace()
    msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "NO-LW"})
    msp.add_xline((0, 0), (1, 0))
    msp.add_text("Ω", dxfattribs={"style": "standard"})
    msp.add_text("A", dxfattribs={"style": "ARIAL"})
    msp.add_text("FIELD_NAME", dxfattribs={"style": "standard"})
    msp.add_mtext("\\H2.5;BIG", dxfattribs={"style": "standard"})
    msp.add_hatch().set_pattern_fill("ANSI32", scale=0.25)
    doc.layouts.get("Layout1").add_text("ON PAPER")
    found = "\n".join(dxfrules.violations(doc))
    for expected in ("$INSUNITS is 6", "header $DIMGAP", "layer 'NO-LW' has lineweight", "forbidden entity XLINE",
                     "non-ASCII text in style 'standard'", "style 'arial', which LibreCAD has no font for",
                     "draws an underscore", "inline codes", "hatch pattern ANSI32", "paperspace layout 'Layout1'"):
        assert expected in found, expected

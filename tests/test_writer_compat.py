"""Every drafting writer obeys the DXF writer rules of docs/compat.md."""

from pathlib import Path

import ezdxf
import pytest

from unicad.catalog.store import load_catalog
from unicad.drafting import dxfrules
from unicad.drafting.legend import legend_drawer
from unicad.drafting.sheets import samples
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sheets.render import render_sheet
from unicad.drafting.sld import render_sld
from unicad.drafting.symbols.catalogue import build_catalogue
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


@pytest.mark.parametrize("name", ["rooftop_sld", "utility_sld", "symbol_library", "sample_sheet"])
def test_writer_output_follows_the_compat_rules(name, catalog, profile):
    doc = {
        "rooftop_sld": lambda: _sld("rooftop_500kwp", catalog, profile),
        "utility_sld": lambda: _sld("utility_50mwac", catalog, profile),
        "symbol_library": lambda: build_catalogue(columns=5, groups=2),
        "sample_sheet": lambda: _sheet(profile),
    }[name]()
    assert dxfrules.violations(doc) == []
    assert doc.header["$INSUNITS"] == 4
    assert doc.header["$DIMTXT"] >= 1.5 and doc.header["$DIMASZ"] >= 1.5
    used = {e.dxf.layer for e in doc.modelspace()}
    assert all(doc.layers.get(n).dxf.lineweight >= dxfrules.MIN_LINEWEIGHT for n in used)


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

"""t3-draw: earthing note 2 states what was checked for the plant at hand, the layout roads note, the RVUNL SLD fill and the
catalogued data logger on the SLD and BOM."""

from pathlib import Path

import pytest
from test_design_rvunl import CATALOG

from unicad.domain.project import Project
from unicad.drafting.earthing_plan import earthing_notes
from unicad.earthing import design_earthing

DATA = Path(__file__).resolve().parent / "data"


def bench2() -> Project:
    return Project.model_validate_json((DATA / "rvunl_heerapura_bench2.json").read_text(encoding="utf-8"))


# ---------------- earthing note 2 (item 2) ----------------


def test_lt_plant_earthing_note_names_the_lt_check_not_a_switchyard_grid():
    d = design_earthing(bench2())
    assert d.lt_plant and d.grid is None
    notes = earthing_notes(d)
    assert notes[1].startswith("EARTHING INSTALLATION SHALL CONFORM TO IS 3043."), notes[1]
    assert "SWITCHYARD EARTH GRID" not in notes[1] and "CHECKED AS PER IEEE 80" not in notes[1], notes[1]
    assert "AUTOMATIC DISCONNECTION OF SUPPLY" in notes[1] and "IEC 60364-4-41" in notes[1], notes[1]
    assert not [n for n in notes if "SWITCHYARD EARTH GRID CHECKED" in n]
    assert sum("LT PLANT" in n and "IEEE 80" in n for n in notes) == 1, "said once, not again as an assumption"


@pytest.fixture(scope="module")
def kutch():
    import gzip

    from unicad.store import parse_project

    root = Path(__file__).resolve().parents[1]
    return parse_project(gzip.decompress((root / "fixtures" / "projects" / "kutch_hybrid.json.gz").read_bytes()).decode("utf-8"), CATALOG)


def test_mv_switchyard_earthing_note_states_the_ieee80_result(kutch):
    d = design_earthing(kutch, catalog=CATALOG)
    assert d.grid is not None
    note = earthing_notes(d)[1]
    assert note.startswith("EARTHING INSTALLATION SHALL CONFORM TO IS 3043. MV SWITCHYARD EARTH GRID"), note
    assert f"{d.grid_spacing_m:g} M MESH" in note and "IEEE 80" in note, note
    ok = d.grid.gpr <= d.grid.e_touch or (d.grid.e_mesh <= d.grid.e_touch and d.grid.e_s <= d.grid.e_step)
    assert ("WITHIN THE TOLERABLE TOUCH AND STEP VOLTAGES" in note) == ok, note


# ---------------- layout roads note (item 3) ----------------


def _layout_sheet(project: Project, tmp_path: Path) -> tuple[list[str], str]:
    import ezdxf

    from unicad.sets import render_set

    index = render_set(project, tmp_path, kinds=["layout"], formats=("dxf",), catalog=CATALOG).as_dict(absolute=True)
    sheet = index["sheets"][0]
    text = " ".join(e.dxf.text for e in ezdxf.readfile(sheet["files"]["dxf"]).modelspace().query("TEXT"))
    return sheet["warnings"], text


def test_layout_without_roads_says_nothing_about_internal_roads(tmp_path):
    warnings, text = _layout_sheet(bench2(), tmp_path)
    assert not [w for w in warnings if "road" in w], warnings
    assert "INTERNAL ROADS" not in text


def test_layout_with_road_gaps_says_it_once_as_a_note(kutch, tmp_path):
    warnings, text = _layout_sheet(kutch, tmp_path)
    assert not [w for w in warnings if "roads are not stored" in w], warnings
    assert text.count("INTERNAL ROADS PLANNED") == 1


# ---------------- catalogued data logger on the SLD and BOM (item 5) ----------------


def test_autobuilt_logger_prints_its_catalog_spec_on_the_sld_and_bom(tmp_path):
    import ezdxf
    from test_design_rvunl import BRIEF, RELAY, heerapura
    from test_drafting_t1 import INVERTER, MODULE

    from unicad.design import autobuild
    from unicad.sets import render_set

    p = autobuild(heerapura(), CATALOG, {"module": MODULE, "inverter": INVERTER, "inverter_strings": BRIEF, "relay": RELAY, "data_logger": True}).project
    assert p.network.node("dlog").spec == "solis-s3-logger"
    index = render_set(p, tmp_path, kinds=["sld", "bom"], formats=("dxf",), catalog=CATALOG).as_dict(absolute=True)

    def texts(kind: str) -> list[str]:
        out = []
        for s in index["sheets"]:
            if s["kind"] == kind:
                msp = ezdxf.readfile(s["files"]["dxf"]).modelspace()
                out += [v.dxf.text for e in msp for v in (e.virtual_entities() if e.dxftype() == "INSERT" else [e]) if v.dxftype() == "TEXT"]
        return out

    sld = texts("sld")
    at = sld.index("DL-01")
    assert "4 x RS-485, 1 x ETHERNET, 60 DEVICES" in sld[at : at + 4], sld[at : at + 4]
    assert "RATING TO BE SPECIFIED" not in sld[at : at + 4]
    assert not [w for s in index["sheets"] for w in s["warnings"] if "'dlog'" in w]
    assert "Solis S3-Logger, data logger, 4 x RS-485" in texts("bom")


def test_a_logger_left_without_spec_names_the_catalogued_logger(tmp_path):
    from unicad.sets import render_set

    index = render_set(bench2(), tmp_path, kinds=["sld"], formats=("dxf",), catalog=CATALOG).as_dict(absolute=True)
    warns = [w for s in index["sheets"] for w in s["warnings"] if "'dlog'" in w]
    assert len(warns) == 1 and "the catalog has solis-s3-logger for this symbol" in warns[0], warns


# ---------------- RVUNL LT SLD fills its paper (item 4) ----------------


@pytest.fixture(scope="module")
def bench_sld_sheet():
    from unicad.drafting.sld.sheet import fit_layout
    from unicad.sets.render import plan_set
    from unicad.tenants.store import drawing_profile

    p = bench2()
    sheets = dict(plan_set(p, ["sld"], drawing_profile(p.company_profile), CATALOG))["sld"]
    assert len(sheets) == 1
    s = sheets[0]
    layout, opts, scale = fit_layout(p, CATALOG, s.paper, None)
    return s, layout, opts, scale


def test_rvunl_lt_sld_fits_a1_at_full_size_and_fills_it(bench_sld_sheet):
    from unicad.drafting.sheets.model import PaperSize
    from unicad.drafting.sheets.render import metrics_for

    s, layout, opts, scale = bench_sld_sheet
    assert s.paper == "A1", s.paper
    assert scale >= 1.0 >= opts.min_scale
    area = metrics_for(PaperSize(s.paper)).drawing_area
    fill = (scale * (layout.extent.width + 2 * opts.margin) / area.width, scale * (layout.extent.height + 2 * opts.margin) / area.height)
    assert min(fill) >= 0.95, fill
    rows = [h.text for h in layout.headings]
    assert len(rows) == 3, rows
    bottom = [f for f in layout.frames if f.box.y1 < layout.extent.y0 + 0.4 * layout.extent.height]
    assert sum(f.box.width for f in bottom) >= 0.75 * layout.extent.width, "the last row is no strip at the left of an empty sheet"


def test_rvunl_logger_sits_with_the_metering_not_in_a_repeated_inverter_frame(bench_sld_sheet):
    _, layout, _, _ = bench_sld_sheet
    dl = next(x for x in layout.symbols if x.node_id == "dlog")
    meter = next(x for x in layout.symbols if x.node_id == "solar_meter")
    repeated = [f for f in layout.frames if f.multiplicity > 1]
    assert repeated
    assert not [f for f in repeated if f.box.overlaps(dl.body)], "DL-01 drawn inside an (x N) frame reads as N loggers"
    assert abs(dl.body.y1 - meter.body.y1) < 1 and 0 < dl.body.x0 - meter.body.x1 < 60, "drawn on the line of the meter, next to it"


# ---------------- MMS table details with given strings (item 6, bench run 3 NEW-1) ----------------


def bench3() -> Project:
    """The RVUNL plant as bench run 3 issued it (rev 42): one block per inverter, tables as long as each string (2P x 18, 2P x 19, 2P x 9)."""
    return Project.model_validate_json((DATA / "rvunl_heerapura_bench3.json").read_text(encoding="utf-8"))


def test_mms_table_counts_full_and_half_by_each_tables_own_string(tmp_path):
    import ezdxf

    from unicad.sets import render_set

    index = render_set(bench3(), tmp_path, kinds=["layout"], formats=("dxf",), catalog=CATALOG).as_dict(absolute=True)
    msp = ezdxf.readfile(index["sheets"][0]["files"]["dxf"]).modelspace()
    texts = [e for e in msp.query("TEXT")]
    total = next(e for e in texts if e.dxf.text == "TOTAL" and abs(e.dxf.insert.y - min(x.dxf.insert.y for x in texts if x.dxf.text == "TOTAL")) < 1e-6)
    row = sorted((e for e in texts if abs(e.dxf.insert.y - total.dxf.insert.y) < 0.5), key=lambda e: e.dxf.insert.x)
    assert [e.dxf.text for e in row][:4] == ["TOTAL", "52", "4", "56"], [e.dxf.text for e in row]
    words = " ".join(e.dxf.text for e in texts)
    assert "FULL TABLE TYPICAL ARRANGEMENT (2P x 18)" in words and "HALF TABLE (2P x 9)" in words
    assert "TABLES 2P x 18 (49 NOS), 2P x 19 (3 NOS)" in words


# ---------------- BOM prints the typed ratings (item 7, bench run 3 N2) ----------------


def test_bom_items_without_catalog_part_carry_their_typed_ratings():
    from unicad.schedules import build_schedule
    from unicad.schedules.bom import build_bom

    p = bench3()
    items = {ln.item for ln in build_bom(p, CATALOG, build_schedule(p, CATALOG)).lines}
    ct = next(i for i in items if i.startswith("ACCB OUTGOING CT"))
    assert "2500/1 A" in ct and "CL 0.2S 15 VA METERING" in ct and "CL PS PROTECTION" in ct, ct
    pt = next(i for i in items if i.startswith("ACCB BUS PT"))
    assert "110 V" in pt and "CL 3P 15 VA PROTECTION" in pt, pt
    relay = next(i for i in items if i.startswith("NUMERICAL PROTECTION RELAY"))
    assert "67N" in relay and "IEC 61850" in relay, relay
    accb = next(i for i in items if i.startswith("LT AC COMBINER BOX"))
    assert "2500 A" in accb and "31.5 kA 3 s" in accb, accb

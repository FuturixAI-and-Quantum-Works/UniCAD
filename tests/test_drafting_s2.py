"""Drafting defects of the RVUNL Heerapura bench run (RV-*) and verifier run 6 (R6-*), reproduced on the RVUNL project as the run left it."""

import json
import re
from pathlib import Path

import pytest

from unicad.catalog.store import load_catalog
from unicad.domain.project import Party, Project
from unicad.sets import render_set
from unicad.sets.render import plan_set
from unicad.sets.registry import builder
from unicad.tenants.store import drawing_profile, load_profile

DATA = Path(__file__).resolve().parent / "data"
CATALOG = load_catalog()


def rvunl(**changes) -> Project:
    p = Project.model_validate_json((DATA / "rvunl_heerapura.json").read_text(encoding="utf-8"))
    return p.model_copy(update=changes) if changes else p


def built(project: Project, kind: str, profile=None):
    profile = profile or drawing_profile(project.company_profile)
    planned = dict(plan_set(project, [kind], profile, CATALOG))[kind]
    return builder(kind).build(project, planned, planned, profile, CATALOG)


def words(doc) -> str:
    """Every TEXT on the sheet, joined with spaces, so a phrase wrapped over lines still reads as one."""
    return re.sub(r"\s+", " ", " ".join(e.dxf.text for e in doc.modelspace().query("TEXT")))


@pytest.fixture(scope="module")
def project():
    return rvunl()


@pytest.fixture(scope="module")
def sheets(project, tmp_path_factory):
    out = tmp_path_factory.mktemp("rvunl")
    render_set(project, out, kinds=["register", "sld", "layout", "earthing", "lps", "strings", "cable_schedule", "bom"], formats=("dxf",), catalog=CATALOG)
    import ezdxf

    index = json.loads((out / "index.json").read_text())
    return {s["number"]: (s["kind"], words(ezdxf.readfile(out / s["files"]["dxf"]))) for s in index["sheets"]}


# ---------------- RV-M2: no EPC profile prints a neutral title block ----------------


def test_rv_m2_a_project_without_an_epc_profile_names_no_demo_company(sheets):
    for number, (_, text) in sheets.items():
        assert "UniCAD Demo EPC" not in text and "Pune" not in text and "example-epc" not in text, number
        assert "EPC COMPANY: NOT SET" in text, number


def test_rv_m2_an_assigned_profile_still_prints_its_company():
    p = rvunl(company_profile="acme")
    text = words(built(p, "sld", load_profile("acme"))[0].doc)
    assert "EPC COMPANY: NOT SET" not in text and load_profile("acme").name.upper()[:10] in text.upper()


def test_rv_m2_the_server_asks_for_the_epc_profile_up_front():
    from unicad.mcp.instructions import INSTRUCTIONS

    step0 = INSTRUCTIONS.split("\n0. ", 1)[1].split("\n1. ", 1)[0]
    assert "ask" in step0.lower() and "EPC COMPANY: NOT SET" in step0


# ---------------- RV-30 / RV-31: developer and site coordinates ----------------


class WithDeveloper(Project):
    developer: Party | None = None


def test_rv_30_the_developer_is_in_the_title_block_and_the_register():
    p = WithDeveloper.model_validate({**rvunl().model_dump(mode="json"), "developer": {"name": "Truere Knight Pvt. Ltd."}})
    for kind in ("sld", "register", "layout", "bom"):
        text = words(built(p, kind)[0].doc)
        assert "DEVELOPER" in text and "TRUERE KNIGHT PVT. LTD." in text, kind


def test_rv_31_site_coordinates_print_in_the_title_block_location(sheets):
    for number, (_, text) in sheets.items():
        assert "LAT 26.887058 N LONG 75.719544 E" in text, number


# ---------------- RV-M1: assumptions on the sheets they drive, all of them on the register ----------------


def test_rv_m1_each_sheet_prints_the_assumptions_that_drive_it(sheets):
    by_kind = {}
    for _, (kind, text) in sheets.items():
        by_kind.setdefault(kind, text)
    assert "ASSUMED INPUTS" in by_kind["earthing"] and "SOIL RESISTIVITY 100 OHM-M" in by_kind["earthing"]
    assert "LIGHTNING PROTECTION LEVEL III" in by_kind["lps"]
    assert "HEERAPURA PLOT ASSUMED 180X120" in by_kind["layout"]
    assert "FAULT LEVEL 31.5 kA / 3 s AT 415 V" in by_kind["sld"]
    assert "LENGTH ASSUMED" in by_kind["cable_schedule"]
    assert "SOIL RESISTIVITY" not in by_kind["sld"] and "FAULT LEVEL" not in by_kind["lps"]


def test_rv_m1_the_register_lists_every_assumption_of_the_set(sheets):
    register = next(t for k, t in sheets.values() if k == "register")
    for phrase in ("SOIL RESISTIVITY 100 OHM-M", "LIGHTNING PROTECTION LEVEL III", "HEERAPURA PLOT ASSUMED 180X120", "FAULT LEVEL 31.5 kA",
                   "LENGTH ASSUMED", "CELL TEMPERATURE USES THE MODULE NOCT"):  # fmt: skip
        assert phrase in register, phrase


# ---------------- RV-m3: long revision remarks wrap ----------------


def test_rv_m3_a_long_revision_remark_wraps_instead_of_truncating(sheets, project):
    long = next(r.remarks for r in project.revisions if len(r.remarks) > 55)
    assert "SINGLE-LINE DETAILS ADDED" in long.upper()
    sld = next(t for k, t in sheets.values() if k == "sld")
    assert "SINGLE-LINE DETAILS ADDED" in sld
    warnings = [w for b in built(project, "sld") for w in b.warnings]
    assert not any("truncated" in w for w in warnings), warnings


# ---------------- RV-m4: the BOM states the client-scope equipment it leaves out ----------------


def test_rv_m4_the_bom_names_client_scope_items_left_out(sheets):
    bom = " ".join(t for k, t in sheets.values() if k == "bom")
    assert "NOT IN THIS BOM" in bom and "POI-01 CLIENT MAIN BOARD PANEL" in bom


# ---------------- 11.6: the company layer map on every sheet kind, the schedule and BOM exports included ----------------


def test_the_layer_map_applies_to_exported_schedule_and_bom_pages():
    from unicad.drafting.sheets.model import LayerSpec
    from unicad.schedules import LengthMode, build_bom, build_schedule
    from unicad.schedules.dxf import render_bom, render_schedule

    p = rvunl()
    profile = load_profile("default").model_copy(update={"version": 2, "layer_map": {"G-TEXT": LayerSpec(name="CO-TABLE-TEXT")}})
    schedule = build_schedule(p, CATALOG, LengthMode.AUTO)
    docs = [*render_schedule(p, schedule, profile)[0].docs, *render_bom(p, build_bom(p, CATALOG, schedule), profile).docs]
    for doc in docs:
        layers = {e.dxf.layer for e in doc.modelspace()}
        assert "G-TEXT" not in layers and "CO-TABLE-TEXT" in layers

"""The company_* tools over MCP: onboarding, editing, assigning a profile to a project, and what the rules and drawings then do."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client
from PIL import Image
from test_http import Server, run
from test_mcp import Client, connect
from test_rules import CATALOG, variant

from unicad.store import ProjectStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "profiles" / "samples" / "acme"
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))


def low_ratio():
    def edit(data):
        data["design_basis"]["dc_ac_ratio_target"] = 1.3
        data["id"] = "roof-low-ratio"
        data["sheets"] = []

    return variant("rooftop_500kwp", edit)


@asynccontextmanager
async def connect_with_librecad(workspace: Path):
    env = {**os.environ, "UNICAD_WORKSPACE": str(workspace), "DISPLAY": os.environ.get("DISPLAY", ":0"), "LIBRECAD": str(LIBRECAD)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "unicad"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        yield Client(s)


def session(tmp_path, fn, connector=connect):
    ws = tmp_path / "ws"
    ProjectStore(ws).save(low_ratio())

    async def go():
        async with connector(ws) as c:
            await fn(c, ws)

    asyncio.run(asyncio.wait_for(go(), 180))


def onboard_args():
    return {"dxf_path": str(SAMPLE / "acme-company.dxf"), "answers": str(SAMPLE / "answers.yaml")}


def test_onboard_assign_validate_and_update_thresholds(tmp_path):
    async def check(c, ws):
        listed = await c.call("company_list")
        assert [p["profile_id"] for p in listed["profiles"] if p["origin"] == "bundled"] == ["acme", "default"]

        before = await c.call("validate", project_id="roof-low-ratio", families=["ratio"])
        assert "company_thresholds" not in before and before["counts"]["warning"] == 2

        r = await c.call("company_onboard", **onboard_args())
        assert r["ok"] and r["saved"] and Path(r["path"]) == ws / "profiles" / "acme.json"
        assert any("title block ACME_TB_A3" in f for f in r["found"]) and r["failed"] == []
        assert r["profile"]["numbering"]["example"] == "ACME/CLIENT/PROJECT/EL/001"

        got = await c.call("company_get", profile_id="acme")
        by = {t["name"]: t for t in got["thresholds"]}
        assert by["dc_ac_band"]["origin"] == "company" and by["dc_ac_band"]["value"] == 0.15 and "DS-04" in by["dc_ac_band"]["source"]
        assert by["k_xlpe_cu"]["origin"] == "default" and len(got["thresholds"]) == 12
        assert got["origin"] == "workspace" and got["problems"] == [] and set(got["title_block"]["papers"]) == {"A1", "A3"}

        assert (await c.call("validate", project_id="roof-low-ratio", families=["ratio"]))["counts"]["warning"] == 2
        a = await c.call("company_assign", project_id="roof-low-ratio", profile_id="acme")
        assert a["changed"] and a["profile_id"] == "acme"
        v = await c.call("validate", project_id="roof-low-ratio", families=["ratio"])
        assert v["company_thresholds"] == {"profile": "acme", "values": {"dc_ac_band": 0.15, "isc_factor": 1.25}}
        assert v["counts"]["warning"] == 2

        u = await c.call("company_update", profile_id="acme", fields={"thresholds": {"dc_ac_band": {"value": 0.25, "source": "Acme DS-04 rev C"}}})
        assert {t["name"]: t["value"] for t in u["thresholds"]}["dc_ac_band"] == 0.25
        v = await c.call("validate", project_id="roof-low-ratio", families=["ratio"])
        assert v["counts"]["warning"] == 0 and v["company_thresholds"]["values"]["dc_ac_band"] == 0.25

        await c.call("company_update", profile_id="acme", fields={"thresholds": {"dc_ac_band": None}})
        v = await c.call("validate", project_id="roof-low-ratio", families=["ratio"])
        assert v["counts"]["warning"] == 2 and v["company_thresholds"]["values"] == {"isc_factor": 1.25}

    session(tmp_path, check)


def test_update_refuses_changes_that_do_not_validate(tmp_path):
    async def check(c, ws):
        await c.call("company_onboard", **onboard_args())
        text = await c.fail("company_update", profile_id="acme", fields={"thresholds": {"dc_ac_band": {"value": 9, "source": "x"}}})
        assert "outside the accepted range" in text
        assert "unknown thresholds" in await c.fail("company_update", profile_id="acme", fields={"thresholds": {"nonsense": {"value": 1, "source": "x"}}})
        text = await c.fail("company_update", profile_id="acme", fields={"symbol_overrides": {"pv.module": {"block": "NOPE"}}})
        assert "breaks the profile" in text and "NOPE" in text
        assert "extra" in (await c.fail("company_update", profile_id="acme", fields={"colour": "red"})).lower()
        assert (await c.call("company_get", profile_id="acme"))["thresholds"][2]["value"] == 0.15
        assert "no company profile" in await c.fail("company_get", profile_id="nobody")
        assert "profile id" in await c.fail("company_get", profile_id="../x")

    session(tmp_path, check)


def test_updating_a_bundled_profile_copies_it_into_the_workspace(tmp_path):
    async def check(c, ws):
        assert (await c.call("company_get", profile_id="default"))["origin"] == "bundled"
        u = await c.call("company_update", profile_id="default", fields={"address": "New address, Pune", "thresholds": {"stc_temp": {"value": 25, "source": "unchanged"}}})
        assert u["origin"] == "workspace" and u["address"] == "New address, Pune" and u["version"] == 2
        saved = json.loads((ws / "profiles" / "default.json").read_text(encoding="utf-8"))
        assert saved["version"] == 2 and saved["name"] == "UniCAD Demo EPC" and saved["logo"].endswith("demo-logo.dxf")
        assert (await c.call("company_get", project_id="roof-low-ratio"))["address"] == "New address, Pune"

    session(tmp_path, check)


def test_onboard_reports_failures_and_saves_nothing(tmp_path):
    async def check(c, ws):
        r = await c.call("company_onboard", dxf_path=str(SAMPLE / "acme-company.dxf"), answers={"id": "oops", "name": "Oops", "short_code": "OOPS", "symbols": {"pv.module": {"block": "MISSING"}}})
        assert not r["ok"] and not r["saved"] and "has no block 'MISSING'" in "\n".join(r["failed"])
        assert not (ws / "profiles" / "oops.json").exists()
        r = await c.call("company_onboard", dxf_path=str(SAMPLE / "acme-company.dxf"), answers={"id": "oops", "name": "Oops", "short_code": "OOPS", "symbols": {"pv.module": {"block": "MISSING"}}}, accept_partial=True)
        assert r["saved"] and (ws / "profiles" / "oops.json").is_file()
        assert "cannot read" in "\n".join((await c.call("company_onboard", dxf_path=str(tmp_path / "nope.dxf"), answers={"id": "x", "name": "X", "short_code": "XX"}))["failed"])
        assert "short_code" in await c.fail("company_onboard", dxf_path=str(SAMPLE / "acme-company.dxf"), answers={"id": "x", "name": "X", "short_code": "x"})

    session(tmp_path, check)


def test_drawings_use_the_assigned_company_profile(tmp_path):
    async def check(c, ws):
        await c.call("company_onboard", **onboard_args())
        await c.call("company_assign", project_id="roof-low-ratio", profile_id="acme")
        r = await c.call("drawings_render", project_id="roof-low-ratio", formats=["dxf"])
        (sheet,) = r["sheets"]
        assert sheet["number"].startswith("ACME/")
        assert "Acme Solar EPC Pvt. Ltd." in Path(sheet["files"]["dxf"]).read_text(encoding="utf-8")

    session(tmp_path, check)


@pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")
def test_preview_sheet_draws_the_title_block(tmp_path):
    async def check(c, ws):
        await c.call("company_onboard", **onboard_args())
        r = await c.call("company_preview_sheet", profile_id="acme", paper="A3", px=1400)
        png = Path(r["png"])
        assert png.is_file() and png.is_relative_to(ws / "profiles" / "acme" / "preview") and r["title_block"] == "imported"
        assert r["drawing_number"].startswith("ACME/") and [w for w in r["warnings"] if "mm text" not in w] == []
        assert max(Image.open(png).size) == 1400
        crop = await c.call("company_preview_sheet", profile_id="acme", paper="A3", region=[290, 5, 415, 130], px=1200)
        assert Path(crop["png"]).is_file() and crop["region"] == pytest.approx([290.0, 5.0, 415.0, 130.0], abs=0.01)

    session(tmp_path, check, connect_with_librecad)


def test_tenants_have_their_own_profiles(tmp_path_factory):
    server = Server(tmp_path_factory.mktemp("tenants") / "ws", "--tenants")
    try:
        async def go():
            async with server.session("acme-co") as a, server.session("globex") as g:
                await a.call("company_onboard", **onboard_args())
                names = lambda r: {p["profile_id"]: p["origin"] for p in r["profiles"]}  # noqa: E731
                return names(await a.call("company_list")), names(await g.call("company_list"))

        mine, theirs = run(go())
    finally:
        server.stop()
    assert mine["acme"] == "workspace" and mine["default"] == "bundled" and theirs["acme"] == "bundled"
    assert (server.workspace / "acme-co" / "profiles" / "acme.json").is_file()
    assert not (server.workspace / "globex" / "profiles" / "acme.json").exists()

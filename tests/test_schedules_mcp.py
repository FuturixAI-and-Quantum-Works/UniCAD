import asyncio
import os
from pathlib import Path

import openpyxl
import pymupdf
import pytest
from test_mcp import connect
from test_routing import CATALOG, fixture, ground_project

from unicad.mcp import Config, build_server
from unicad.schedules import build_schedule
from unicad.store import ProjectStore

LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
needs_librecad = pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")


def run(tmp_path, fn, *projects):
    ws = tmp_path / "ws"
    store = ProjectStore(ws)
    for p in projects:
        store.save(p)

    async def go():
        async with connect(ws) as c:
            await fn(c)

    asyncio.run(asyncio.wait_for(go(), 240))


def test_tools_are_listed_and_documented(tmp_path):
    async def check(c):
        tools = {t.name for t in (await c.session.list_tools()).tools}
        assert {"cable_schedule", "bom"} <= tools
        docs = (await c.session.read_resource("unicad://docs/tools")).contents[0].text
        assert "### `cable_schedule`" in docs and "### `bom`" in docs

    run(tmp_path, check)


def test_cable_schedule_xlsx_default_location_and_summary(tmp_path):
    async def check(c):
        r = await c.call("cable_schedule", project_id="rooftop-500kwp")
        expected = build_schedule(fixture("rooftop_500kwp"), CATALOG)
        assert r["format"] == "xlsx" and r["rows"] == len(expected.rows) == 67
        path = Path(r["files"][0])
        assert path == (tmp_path / "ws" / "rooftop-500kwp" / "exports" / "rooftop-500kwp-cable-schedule.xlsx").resolve()
        assert openpyxl.load_workbook(path)["Cable schedule"].max_row == 2 + r["rows"]
        assert r["totals"]["cable_m"] == expected.totals()["cable_m"]
        assert r["length_sources"] == {"network": 67}
        assert "DC VD, WORST PATH" in r["voltage_drop"] and r["voltage_drop"]["AC VD, WORST PATH"].startswith("1.57 %")
        assert sorted(s["rows"] for s in r["sections"]) == [1, 1, 5, 5, 5, 50]
        assert len(r["gaps"]) == 2

    run(tmp_path, check, fixture("rooftop_500kwp"))


def test_estimated_lengths_are_reported_with_their_template_aggregates(tmp_path):
    async def check(c):
        r = await c.call("cable_schedule", project_id="rooftop-500kwp", lengths="estimated", drop_per_end_m=2.5, termination_m=1)
        assert r["length_sources"]["estimated"] == 50 and r["length_sources"]["network"] == 17
        t = r["template_lengths"]["e_str"]
        assert t["instances"] == 50 and t["min_m"] <= t["mean_m"] <= t["max_m"]
        assert any("2.5 m of vertical drop and 1 m" in a for a in r["assumptions"])
        base = await c.call("cable_schedule", project_id="rooftop-500kwp", lengths="estimated")
        assert r["totals"]["cable_m"] == pytest.approx(base["totals"]["cable_m"] + 50 * 2 * 2.0)

    run(tmp_path, check, fixture("rooftop_500kwp"))


def test_switchyard_position_is_taken_from_the_call(tmp_path):
    async def check(c):
        near = await c.call("cable_schedule", project_id="ground", out_dir=str(tmp_path / "a"))
        far = await c.call("cable_schedule", project_id="ground", lengths="estimated", switchyard_x=5000, switchyard_y=0, out_dir=str(tmp_path / "b"))
        assert far["totals"]["cable_m_mv_ac"] > near["totals"]["cable_m_mv_ac"] + 5000
        assert "pass switchyard_x and switchyard_y together" in await c.fail("cable_schedule", project_id="ground", switchyard_x=1)

    run(tmp_path, check, ground_project().model_copy(update={"id": "ground"}))


def test_dxf_pages_are_written_one_file_each(tmp_path):
    async def check(c):
        dxf = await c.call("cable_schedule", project_id="utility-50mwac", format="dxf")
        assert dxf["pages"] == len(dxf["files"]) == 12 and all(f.endswith(".dxf") and Path(f).is_file() for f in dxf["files"])
        assert dxf["layout_warnings"] == []

    run(tmp_path, check, fixture("utility_50mwac"))


@needs_librecad
def test_pdf_has_one_page_per_sheet(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", os.environ.get("DISPLAY", ":0"))
    ProjectStore(tmp_path / "ws").save(fixture("rooftop_500kwp"))

    async def go():
        server = build_server(Config(workspace=tmp_path / "ws"))
        return await server.call_tool("cable_schedule", {"project_id": "rooftop-500kwp", "format": "pdf", "lengths": "estimated"})

    result = asyncio.run(asyncio.wait_for(go(), 240)).structured_content
    assert result["pages"] == 3 and len(result["files"]) == 1
    assert len(pymupdf.open(result["files"][0])) == 3


def test_bom_xlsx_and_dxf(tmp_path):
    async def check(c):
        r = await c.call("bom", project_id="rooftop-500kwp", wastage_pct=10)
        assert r["summary"]["modules"] == 900 and r["summary"]["inverters"] == 5
        ws = openpyxl.load_workbook(r["files"][0])["BOM"]
        assert ws.max_row == 2 + r["summary"]["lines"]
        assert any("10 %" in a for a in r["assumptions"])
        d = await c.call("bom", project_id="rooftop-500kwp", format="dxf", out_dir=str(tmp_path / "out"))
        assert d["pages"] == 1 and Path(d["files"][0]).name == "rooftop-500kwp-bom-p01.dxf"

    run(tmp_path, check, fixture("rooftop_500kwp"))


def test_errors_are_clear(tmp_path):
    async def check(c):
        assert "no cabled power connections" in await c.fail("cable_schedule", project_id="bare")
        assert "no project" in await c.fail("bom", project_id="missing")

    from unicad.domain.project import Project

    data = fixture("rooftop_500kwp").model_dump(mode="json")
    data["id"] = "bare"
    for e in data["network"]["edges"]:
        e.pop("cable", None)
    run(tmp_path, check, Project.model_validate(data, context={"catalog": CATALOG}))

"""The complete drawing set over MCP stdio, the way an agent builds it: a stored rooftop fixture and a ground plant built from scratch,
each given earthing and lightning inputs with project_design_basis, then drawings_render(kinds=["all"])."""

import asyncio
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from test_sets import connect

ROOT = Path(__file__).resolve().parents[1]
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
spec = importlib.util.spec_from_file_location("build_full_set_samples", ROOT / "tools" / "build_full_set_samples.py")
samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(samples)

EVERY_KIND = ["register", "sld", "layout", "earthing", "lps", "strings", "cable_schedule", "bom"]


def pdf_pages(path: Path) -> int:
    out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True).stdout
    return int(next(x.split()[-1] for x in out.splitlines() if x.startswith("Pages")))


def check_set(r: dict) -> None:
    kinds = [s["kind"] for s in r["sheets"]]
    assert list(dict.fromkeys(kinds)) == EVERY_KIND, (kinds, r["warnings"])
    assert not [w for w in r["warnings"] if "not drawn" in w]
    numbers = [s["number"] for s in r["sheets"]]
    assert len(set(numbers)) == len(numbers) == r["sheet_count"]
    reg = r["sheets"][0]
    assert [(x["number"], x["title"].upper() == s["title"].upper() or x["title"]) for x, s in zip(reg["rows"], r["sheets"], strict=True)] == [
        (s["number"], True) for s in r["sheets"]
    ]
    assert Path(r["set_pdf"]).is_file() and pdf_pages(Path(r["set_pdf"])) == r["sheet_count"]
    for s in r["sheets"]:
        assert Path(s["files"]["dxf"]).is_file() and Path(s["files"]["pdf"]).is_file()


@pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")
def test_rooftop_fixture_with_site_layout_and_earthing_inputs_renders_the_complete_set(tmp_path):
    ws = tmp_path / "ws"
    pid = samples.store_fixture(ws, "rooftop_500kwp")

    async def run():
        async with connect(ws) as c:
            before = await c.call("drawings_render", project_id=pid, kinds=["all"], formats=["dxf"])
            assert {"layout: not drawn", "earthing: not drawn", "lps: not drawn"} <= {w.split(":")[0] + ": not drawn" for w in before["warnings"]}
            await samples.rooftop(c, pid)
            r = await c.call("drawings_render", project_id=pid, kinds=["all"], formats=["dxf", "pdf"])
            check_set(r)
            numbers = {s["kind"]: s["number"] for s in r["sheets"]}
            assert numbers["sld"].endswith("-001") and numbers["register"].endswith("-000") and numbers["earthing"].endswith("-301")
            shot = await c.call("drawings_preview", project_id=pid, sheet=r["sheets"][0]["number"], px=600)
            assert Path(shot["png"]).is_file()

    asyncio.run(asyncio.wait_for(run(), 600))


@pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")
def test_ground_plant_built_from_scratch_renders_the_complete_set_numbered_by_block(tmp_path):
    async def run():
        async with connect(tmp_path / "ws") as c:
            pid = await samples.utility(c)
            r = await c.call("drawings_render", project_id=pid, kinds=["all"], formats=["dxf", "pdf"])
            check_set(r)
            first = {}
            for s in r["sheets"]:
                first.setdefault(s["kind"], int(s["number"].rsplit("-", 1)[1]))
            assert first == {"register": 0, "sld": 1, "layout": 101, "earthing": 301, "lps": 302, "strings": 401, "cable_schedule": 501, "bom": 601}
            sld = [int(s["number"].rsplit("-", 1)[1]) for s in r["sheets"] if s["kind"] == "sld"]
            assert sld == list(range(1, len(sld) + 1))

    asyncio.run(asyncio.wait_for(run(), 900))


def test_project_design_basis_sets_and_clears_only_the_parts_given(tmp_path):
    async def run():
        async with connect(tmp_path / "ws") as c:
            pid = (await c.call("project_create", name="Basis test", client="C", segment="utility", site="S", grid_voltage_v=33000))["project_id"]
            r = await c.call("project_design_basis", project_id=pid, **samples.GROUND_BASIS)
            assert r["changed"] and r["design_basis"]["earthing"]["soil_resistivity_ohm_m"] == 50.0 and r["design_basis"]["lps"]["level"] == "III"
            assert r["design_basis"]["fault_levels"][0]["voltage_v"] == 33000
            again = await c.call("project_design_basis", project_id=pid, **samples.GROUND_BASIS)
            assert not again["changed"]
            cleared = await c.call("project_design_basis", project_id=pid, clear=["lps"])
            assert cleared["design_basis"]["lps"] is None and cleared["design_basis"]["earthing"] is not None
            assert "both given and cleared" in await c.fail("project_design_basis", project_id=pid, lps=samples.GROUND_BASIS["lps"], clear=["lps"])
            await c.fail("project_design_basis", project_id=pid, lps={"level": "V"})

    asyncio.run(asyncio.wait_for(run(), 120))

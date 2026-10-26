"""Drawing sets: SLD sheet planning, cross-references between sheets, set rendering and the drawings MCP tools."""

import asyncio
import json
import os
import sys
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from test_design import CATALOG, CENTRAL, UTIL_MOD, blank
from mcp import ClientSession, StdioServerParameters, stdio_client
from test_mcp import Client
from unicad.design import autobuild
from unicad.domain.network import SIGNAL_PORTS, NodeKind
from unicad.domain.project import SheetKind
from unicad.drafting.sheets.model import CompanyProfile
from unicad.drafting.sld import SldOptions
from unicad.drafting.sld.xref import partition
from unicad.sets import SldBuilder, plan_sld_set, render_set
from unicad.sets.numbering import Numberer
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parents[1]
LIBRECAD = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun"))
PROFILE = CompanyProfile.load(ROOT / "profiles" / "default.json")
FLOOR = SldOptions().min_scale


def fixture(name: str):
    return parse_project((ROOT / "fixtures" / "projects" / f"{name}.json").read_text(encoding="utf-8"), CATALOG)


@pytest.fixture(scope="module")
def projects():
    out = {f: fixture(f) for f in ("utility_50mwac", "rooftop_500kwp")}
    cases = {
        "utility_central": {"template": "utility_central", "ac_kw": 50000, "module": UTIL_MOD, "inverter": CENTRAL},
        "utility_string": {"template": "utility_string", "ac_kw": 20000, "module": UTIL_MOD, "inverter": "sungrow-sg110cx"},
    }
    for name, intent in cases.items():
        out[name] = autobuild(blank("utility_50mwac"), CATALOG, intent).project
    return out


@pytest.fixture(scope="module")
def built(projects):
    out = {}
    for name, p in projects.items():
        sheets = plan_sld_set(p, PROFILE, CATALOG)
        out[name] = (sheets, SldBuilder().build(p, sheets, sheets, PROFILE, CATALOG))
    return out


def _power_nodes(net):
    return {n.id for n in net.nodes if n.kind is not NodeKind.EARTH_ELECTRODE and any(p not in SIGNAL_PORTS for p in n.port_names)}


def test_utility_set_is_an_overall_sld_and_a_block_detail_with_the_project_numbers(built):
    sheets, _ = built["utility_50mwac"]
    assert [(s.id, s.kind, s.number, s.paper) for s in sheets] == [
        ("sld", SheetKind.SLD, "UDEMO-DRL-U50-EE-001", "A0"),
        ("sld_block", SheetKind.SLD_DETAIL, "UDEMO-DRL-U50-EE-002", "A1"),
    ]
    assert sheets[0].title == "OVERALL SINGLE LINE DIAGRAM 50 MWac"
    assert sheets[1].scope.nodes == ("inv_sec",) and "BLOCK-01..BLOCK-06" in sheets[1].title


def test_rooftop_stays_one_a3_sheet(built, projects):
    sheets, done = built["rooftop_500kwp"]
    assert [(s.id, s.paper, s.number) for s in sheets] == [("sld", "A3", "UDEMO-DIPL-RT500-EE-001")]
    assert done[0].meta["cut_edges"] == [] and done[0].meta["cross_refs"] == []
    assert set(done[0].meta["nodes"]) >= _power_nodes(projects["rooftop_500kwp"].network)


@pytest.mark.parametrize("name", ["utility_50mwac", "utility_central", "utility_string", "rooftop_500kwp"])
def test_every_node_is_drawn_on_exactly_one_sheet_and_every_sheet_is_readable(built, projects, name):
    sheets, done = built[name]
    net = projects[name].network
    owned = Counter(n for b in done for n in b.meta["nodes"])
    assert set(owned) >= _power_nodes(net) and max(owned.values()) == 1
    for b in done:
        assert b.scale >= FLOOR, (name, b.sheet.id, b.scale)
        assert b.meta["readable"], (name, b.sheet.id)
        assert not [w for w in b.warnings if "did not fit" in w or "drawn without a frame" in w], (name, b.sheet.id, b.warnings)
    if name.startswith("utility"):
        assert len(sheets) >= 2 and {s.kind for s in sheets} == {SheetKind.SLD, SheetKind.SLD_DETAIL}


@pytest.mark.parametrize("name", ["utility_50mwac", "utility_central", "utility_string"])
def test_every_cut_edge_is_a_pair_of_markers_naming_each_other(built, name):
    sheets, done = built[name]
    number = {s.id: s.number for s in sheets}
    marks: dict[str, list[tuple[str, str]]] = {}
    for b in done:
        assert b.meta["cut_edges"], b.sheet.id
        refs = {r["edge"]: r for r in b.meta["cross_refs"]}
        assert set(refs) == set(b.meta["cut_edges"]), b.sheet.id
        for r in refs.values():
            assert r["to_sheet"] and r["to_sheet"] != b.sheet.number and r["text"].startswith(("TO ", "FROM "))
            marks.setdefault(r["edge"], []).append((number[b.sheet.id], r["to_sheet"]))
    for edge, ends in marks.items():
        assert len(ends) == 2, edge
        (a, to_a), (b, to_b) = ends
        assert (to_a, to_b) == (b, a), edge


def test_markers_print_the_target_sheet_number_and_the_tag_beyond(built):
    _, done = built["utility_50mwac"]
    texts = {b.sheet.id: {e.dxf.text for e in b.doc.modelspace().query("TEXT")} | {e.dxf.text for blk in b.doc.blocks for e in blk.query("TEXT")}
             for b in done}
    assert {"SH. UDEMO-DRL-U50-EE-002", "FROM INV-01-1..INV-01-2"} <= texts["sld"]
    assert {"SH. UDEMO-DRL-U50-EE-001", "TO IDT-01"} <= texts["sld_block"]
    assert "1 of 2" in texts["sld"] and "2 of 2" in texts["sld_block"]


def test_partition_follows_the_nearest_root_towards_the_grid(projects):
    p = projects["utility_50mwac"]
    parts = {x.sheet_id: x for x in partition(p.network, p.sheets[:2])}
    assert {"idt", "inv", "scb", "str", "icog_la", "blk_aux", "blk_aux_load"} <= parts["sld_blk"].nodes
    assert {"icog_vcb", "mv_bus", "poi", "abt_main"} <= parts["sld"].nodes
    assert parts["sld"].cut_edges == parts["sld_blk"].cut_edges == ("e_idt_vcb",)
    assert parts["sld"].xrefs == {"e_idt_vcb": "UDEMO-DRL-U50-EE-002"}


def test_a_sheet_unreadable_on_a0_is_split_and_the_pieces_still_pair_up(projects):
    p = projects["utility_50mwac"]
    tight = SldOptions(min_scale=2.0, max_scale=3.0)
    sheets = SldBuilder(tight).plan(p, PROFILE, CATALOG, Numberer.for_project(p, PROFILE))
    assert len(sheets) > 2 and len({s.number for s in sheets}) == len(sheets)
    parts = partition(p.network, sheets)
    owned = Counter(n for x in parts for n in x.nodes)
    assert max(owned.values()) == 1 and set(owned) >= _power_nodes(p.network)
    cut = Counter(e for x in parts for e in x.cut_edges)
    assert cut and set(cut.values()) == {2}
    assert all(x.xrefs[e] != s.number for s, x in zip(sheets, parts) for e in x.cut_edges)


def test_set_dxf_and_index_are_byte_identical_across_runs(projects, tmp_path):
    p = projects["utility_50mwac"]
    a = render_set(p, tmp_path / "a", formats=("dxf",), profile=PROFILE, catalog=CATALOG)
    b = render_set(p, tmp_path / "b", formats=("dxf",), profile=PROFILE, catalog=CATALOG)
    names = sorted(x.name for x in (tmp_path / "a").iterdir())
    assert names == sorted(x.name for x in (tmp_path / "b").iterdir())
    assert names == ["01-UDEMO-DRL-U50-EE-001.dxf", "02-UDEMO-DRL-U50-EE-002.dxf", "index.json"]
    for n in names:
        assert (tmp_path / "a" / n).read_bytes() == (tmp_path / "b" / n).read_bytes(), n
    index = json.loads((tmp_path / "a" / "index.json").read_text())
    assert [s["sheet_of"] for s in index["sheets"]] == ["1 of 2", "2 of 2"] and a.as_dict() == b.as_dict()
    again = render_set(p, tmp_path / "a", formats=("dxf",), profile=PROFILE, catalog=CATALOG)
    assert again.as_dict() == a.as_dict() and sorted(x.name for x in (tmp_path / "a").iterdir()) == names


@asynccontextmanager
async def connect(workspace: Path):
    """The MCP server over stdio with this environment (LibreCAD needs DISPLAY and LIBRECAD)."""
    env = {**os.environ, "UNICAD_WORKSPACE": str(workspace), "DISPLAY": os.environ.get("DISPLAY", ":0"), "LIBRECAD": str(LIBRECAD)}
    params = StdioServerParameters(command=sys.executable, args=["-m", "unicad"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield Client(session)


@pytest.mark.skipif(not LIBRECAD.exists(), reason="LibreCAD binary not available")
def test_drawings_render_and_preview_over_stdio(tmp_path):
    from unicad.store import ProjectStore

    ws = tmp_path / "ws"
    ProjectStore(ws).save(fixture("utility_50mwac"))

    async def run():
        async with connect(ws) as c:
            r = await c.call("drawings_render", project_id="utility-50mwac", formats=["dxf", "pdf"])
            assert r["sheet_count"] == 2 and Path(r["set_pdf"]).is_file()
            for s in r["sheets"]:
                assert Path(s["files"]["dxf"]).is_file() and Path(s["files"]["pdf"]).is_file() and s["files"]["dwg"] is None
                assert s["cross_refs"] and all(x["to_sheet"] for x in s["cross_refs"])
            assert "unknown drawing kinds" in await c.fail("drawings_render", project_id="utility-50mwac", kinds=["nonsense"])
            shot = await c.call("drawings_preview", project_id="utility-50mwac", sheet="UDEMO-DRL-U50-EE-002", region=[0, 0, 300, 200], px=800)
            png = Path(shot["png"])
            assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and max(shot["width_px"], shot["height_px"]) == 800
            whole = await c.call("drawings_preview", project_id="utility-50mwac", sheet="sld")
            assert Path(whole["png"]).is_file() and whole["region"][2] > 1000

    asyncio.run(asyncio.wait_for(run(), 240))

"""One scheme of tags, cable ids, dates and locations across the sheets of a set."""

import asyncio
import importlib.util
import os
import re
import sys
from datetime import date
from pathlib import Path

import ezdxf
import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client
from test_design import CATALOG
from test_mcp import Client

from unicad.domain.network import ElectricalNetwork, NodeKind
from unicad.domain.project import IssuedFor, IssueRevision, Project
from unicad.drafting.sheets.model import CompanyProfile
from unicad.drafting.sheets.titleblock import compose_location
from unicad.drafting.string_diagram import plan_types
from unicad.earthing.samples import pune
from unicad.rules.topology import instance_tag
from unicad.schedules import LengthMode, build_schedule
from unicad.schedules.dxf import lists_every_string
from unicad.schedules.ids import SCHEME, SCHEME_FIELDS
from unicad.sets import render_set

ROOT = Path(__file__).resolve().parents[1]
PROFILE = CompanyProfile.load(ROOT / "profiles" / "default.json")
spec = importlib.util.spec_from_file_location("build_string_diagram_samples", ROOT / "tools" / "build_string_diagram_samples.py")
samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(samples)
spec = importlib.util.spec_from_file_location("build_full_set_samples", ROOT / "tools" / "build_full_set_samples.py")
full_samples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(full_samples)

KINDS = ("register", "sld", "strings", "cable_schedule")
DATE_TEXT = re.compile(r"^\d\d\.\d\d\.\d\d$")
NAMES = ["rooftop", "central", "string"]
WITH_SLD = ["rooftop"]


def texts_of(path: Path) -> list[str]:
    doc = ezdxf.readfile(path)
    out = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    for block in doc.blocks:
        if not block.name.startswith("*"):
            out += [e.dxf.text for e in block.query("TEXT")]
    return out


def render(project: Project, out: Path, kinds=KINDS):
    """The index of a rendered set and, per sheet kind, the texts of each of its sheets."""
    index = render_set(project.model_copy(update={"sheets": ()}), out, kinds=kinds, formats=("dxf",), profile=PROFILE, catalog=CATALOG)
    by_kind: dict[str, list[list[str]]] = {}
    for s in index.sheets:
        by_kind.setdefault(s["kind"], []).append(texts_of(out / s["files"]["dxf"]))
    return index, by_kind


def string_instances(net: ElectricalNetwork) -> list[str]:
    return [i for n in net.by_kind(NodeKind.PV_STRING) for i in net.instance_ids(n.id)]


def inverter_downstream(net: ElectricalNetwork, expanded: ElectricalNetwork, instance: str) -> str:
    """Tag of the first inverter or PCS on the power path downstream of `instance`, found by walking the expanded network."""
    succ: dict[str, list[str]] = {}
    for e in expanded.edges:
        if e.is_power:
            succ.setdefault(e.src_node, []).append(e.dst_node)
    seen, queue = set(), [instance]
    while queue:
        node = queue.pop(0)
        if node in seen:
            continue
        seen.add(node)
        if net.node(node.partition("@")[0]).kind in (NodeKind.INVERTER, NodeKind.PCS):
            return instance_tag(net, node)
        queue += succ.get(node, [])
    raise AssertionError(f"no inverter downstream of {instance}")


@pytest.fixture(scope="module")
def projects():
    return {"rooftop": pune()[0], "central": samples.ground_central(CATALOG), "string": samples.ground_string(CATALOG)}


@pytest.fixture(scope="module")
def sets(projects, tmp_path_factory):
    kinds = {"central": ("register", "strings", "cable_schedule"), "string": ("register", "strings", "cable_schedule")}
    return {name: render(p, tmp_path_factory.mktemp(name), kinds.get(name, KINDS)) for name, p in projects.items()}


# ---------------- cable ids carry the network tags ----------------


@pytest.mark.parametrize("name", NAMES)
def test_every_string_cable_id_starts_with_the_tag_of_the_inverter_it_feeds(projects, name):
    p = projects[name]
    expanded = p.network.expand()
    rows = {r.src: r for r in build_schedule(p, CATALOG, LengthMode.NETWORK).rows if r.is_string}
    assert set(rows) == set(string_instances(p.network))
    combiners = {n.id for n in p.network.nodes if n.kind in (NodeKind.SCB, NodeKind.DC_FUSE_BOX)}
    for inst, row in rows.items():
        inverter = inverter_downstream(p.network, expanded, inst)
        assert row.cable_id.startswith(f"{inverter}-"), (row.cable_id, inverter)
        assert row.from_tag == instance_tag(p.network, inst)
        if row.dst.partition("@")[0] in combiners:
            assert row.cable_id.startswith(f"{inverter}-{row.to_tag}-"), (row.cable_id, row.to_tag)


@pytest.mark.parametrize("name", NAMES)
def test_string_cable_ids_are_unique_and_end_in_the_documented_fields(projects, name):
    ids = [r.cable_id for r in build_schedule(projects[name], CATALOG, LengthMode.NETWORK).rows if r.is_string]
    assert len(set(ids)) == len(ids) >= 50
    assert all(re.search(r"(-R\d{3}-\d{4})?-IP\d\d-\d+(\.\d+)? sqmm$", i) for i in ids)


def test_the_scheme_names_inverter_and_combiner_tags_not_indices():
    assert "inverter tag, combiner tag" in SCHEME_FIELDS and SCHEME_FIELDS in SCHEME
    assert "B01-I1-S02" not in SCHEME


def _single_section(net: ElectricalNetwork, group_id: str, index: int) -> ElectricalNetwork:
    """`net` with repeating group `group_id` cut to one section that keeps the tags of its `index`-th copy, as a remainder inverter has."""

    def fix(tag: str) -> str:
        def sub(m: re.Match) -> str:
            i, spec = int(m.group(1)), m.group(2) or ""
            return format(index, spec[1:]) if i == 0 else f"{{{i - 1}{spec}}}"

        return re.sub(r"\{(\d+)(:[^}]*)?\}", sub, tag)

    return ElectricalNetwork(
        groups=tuple(g.model_copy(update={"count": 1, "tag": fix(g.tag)}) if g.id == group_id else g for g in net.groups),
        nodes=tuple(n.model_copy(update={"tag": fix(n.tag)}) if n.group == group_id else n for n in net.nodes),
        edges=net.edges,
    )


def test_inverters_with_a_single_string_index_keep_their_own_tag_in_the_ids(projects):
    """A remainder inverter section (count 1, fixed tag) numbers its strings with one index; its ids carry its own tag, not 01."""
    p = projects["string"]
    sections = [g for g in p.network.groups if g.count > 1 and any(n.group == g.id for n in p.network.by_kind(NodeKind.INVERTER))]
    section = sections[-1]
    net = _single_section(p.network, section.id, section.count)
    inverter = next(n for n in net.by_kind(NodeKind.INVERTER) if n.group == section.id)
    strings = next(n for n in net.by_kind(NodeKind.PV_STRING) if n.group == section.id)
    assert "{" not in inverter.tag and inverter.tag.endswith(f"-{section.count:02d}")
    rows = [r for r in build_schedule(p.model_copy(update={"network": net}), CATALOG, LengthMode.NETWORK).rows if r.is_string and r.src.partition("@")[0] == strings.id]
    assert len(rows) >= 2 and all(r.cable_id.startswith(f"{inverter.tag}-") for r in rows)
    assert all(r.from_tag.startswith(f"{strings.tag.partition('{')[0]}") for r in rows)


# ---------------- the same tags on every sheet ----------------


@pytest.mark.parametrize("name", NAMES)
def test_string_diagrams_label_every_drawn_string_with_its_network_tag(projects, sets, name):
    _, by_kind = sets[name]
    types, _ = plan_types(projects[name], CATALOG)
    drawn = {t for page in by_kind["strings"] for t in page}
    tags = [ln.tag for t in types for ln in t.lines]
    assert len(tags) >= 10 and all(any(tag == t or t.startswith(f"{tag} : ") for t in drawn) for tag in tags)
    assert not any(re.match(r"STRING S\d\d", t) or re.fullmatch(r"S\d\d", t) for t in drawn)
    assert all(any(t.tag_range in x for x in drawn) for t in types)
    assert not any(" .. " in t for t in drawn)


@pytest.mark.parametrize("name", NAMES)
def test_cable_schedule_sheets_list_the_string_tags_the_network_defines(projects, sets, name):
    p = projects[name]
    _, by_kind = sets[name]
    schedule = build_schedule(p, CATALOG)
    rows = [r for r in schedule.rows if r.is_string]
    assert {r.src for r in rows} == set(string_instances(p.network))
    shown = {t for page in by_kind["cable_schedule"] for t in page}
    if lists_every_string(schedule, "auto"):
        assert {r.from_tag for r in rows} <= shown
    else:
        by_dst: dict[str, list] = {}
        for r in rows:
            by_dst.setdefault(r.dst, []).append(r)
        for group in by_dst.values():
            assert any(group[0].from_tag in t and group[-1].from_tag in t for t in shown), (group[0].from_tag, group[-1].from_tag)


@pytest.mark.parametrize("name", WITH_SLD)
def test_the_sld_and_string_diagrams_start_each_inverter_range_at_the_same_tag(projects, sets, name):
    net = projects[name].network
    _, by_kind = sets[name]
    sld = {t for page in by_kind["sld"] for t in page}
    strings = {t for page in by_kind["strings"] for t in page}
    for node in net.by_kind(NodeKind.INVERTER):
        first = instance_tag(net, net.instance_ids(node.id)[0])
        if any(first in t for t in strings):
            assert any(t == first or t.startswith(f"{first}..") for t in sld), (node.id, first)


@pytest.mark.parametrize("name", WITH_SLD)
def test_each_string_range_the_sld_draws_starts_at_a_tag_the_schedule_lists(projects, sets, name):
    net = projects[name].network
    _, by_kind = sets[name]
    sld = {t for page in by_kind["sld"] for t in page}
    ranges = [t for t in sld if re.fullmatch(r"STR-[\w-]+\.\.STR-[\w-]+", t)]
    listed = {r.from_tag for r in build_schedule(projects[name], CATALOG).rows if r.is_string}
    for node in net.by_kind(NodeKind.PV_STRING):
        first = instance_tag(net, net.instance_ids(node.id)[0])
        drawn = [t for t in ranges if t.startswith(f"{first}..")]
        if drawn:
            assert first in listed and drawn[0].split("..")[1] in listed


# ---------------- one date for every sheet ----------------


def test_a_project_without_revisions_dates_no_sheet_and_the_register_lists_none(projects, tmp_path):
    index, by_kind = render(projects["rooftop"].model_copy(update={"revisions": ()}), tmp_path)
    assert {t for pages in by_kind.values() for page in pages for t in page if DATE_TEXT.match(t)} == set()
    assert {r["date"] for r in index.sheets[0]["rows"]} == {"-"}


def test_the_latest_project_revision_dates_every_sheet_and_the_register_row(projects, tmp_path):
    revisions = (
        IssueRevision(rev="00", date=date(2026, 3, 2), remarks="first issue"),
        IssueRevision(rev="01", date=date(2026, 9, 14), remarks="owner comments", issued_for=IssuedFor.APPROVAL),
    )
    index, by_kind = render(projects["rooftop"].model_copy(update={"revisions": revisions}), tmp_path)
    stamp = date(2026, 9, 14).strftime(PROFILE.date_format)
    for kind, pages in by_kind.items():
        assert all(stamp in page for page in pages), kind
    assert {r["date"] for r in index.sheets[0]["rows"]} == {stamp}


# ---------------- one place name, wrapped not truncated ----------------


def test_compose_location_names_each_place_once():
    assert compose_location("Shree Ganesh Textiles factory roof, MIDC Chakan, Pune", "MIDC Chakan, Pune, Maharashtra", "Maharashtra") == (
        "Shree Ganesh Textiles factory roof, MIDC Chakan, Pune, Maharashtra"
    )
    assert compose_location("", None, "Rajasthan") == "Rajasthan"
    assert compose_location("A, a", "B") == "A, B"


def test_every_sheet_title_block_shows_the_whole_location_once(projects, tmp_path):
    p = projects["rooftop"]
    site = p.site.model_copy(update={"name": "Shree Ganesh Textiles factory roof, MIDC Chakan, Pune", "address": "MIDC Chakan, Pune, Maharashtra", "state": "Maharashtra"})
    _, by_kind = render(p.model_copy(update={"site": site}), tmp_path)
    expected = "SHREE GANESH TEXTILES FACTORY ROOF, MIDC CHAKAN, PUNE, MAHARASHTRA"
    for kind, pages in by_kind.items():
        for page in pages:
            assert expected in " ".join(page), kind
            assert not any(t.count("MIDC CHAKAN") > 1 or t.count("MAHARASHTRA") > 1 for t in page), kind
            assert not any(t.endswith("...") and not t.startswith("...") for t in page), kind


# ---------------- server stderr ----------------


def test_the_server_keeps_ezdxf_bookkeeping_off_stderr(tmp_path):
    ws = tmp_path / "ws"
    pid = full_samples.store_fixture(ws, "rooftop_500kwp")
    log = tmp_path / "stderr.txt"

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "unicad"], env={**os.environ, "UNICAD_WORKSPACE": str(ws)})
        with log.open("w") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                r = await Client(session).call("drawings_render", project_id=pid, kinds=["sld", "strings"], formats=["dxf"])
                assert r["sheet_count"] == 2

    asyncio.run(asyncio.wait_for(run(), 300))
    assert "dictionary" not in log.read_text()


def test_the_sld_parameters_and_the_bom_count_the_strings_the_network_defines(projects, tmp_path):
    p = projects["rooftop"]
    index, by_kind = render(p, tmp_path, ("sld", "bom"))
    strings = len(string_instances(p.network))
    in_series = p.network.node(p.network.by_kind(NodeKind.PV_STRING)[0].id).modules_in_series
    assert f"{strings} x {in_series} MODULES" in {t for page in by_kind["sld"] for t in page}
    assert f"{strings} STRINGS OF {in_series}" in {t for page in by_kind["bom"] for t in page}

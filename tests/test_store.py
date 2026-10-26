import json
from pathlib import Path

import pytest

from unicad.catalog.store import load_catalog
from unicad.domain.project import Project
from unicad.store import FieldChange, ProjectStore, canonical_json, diff, parse_project

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "projects"
NAMES = ["rooftop_500kwp", "utility_50mwac"]


def fixture_text(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text(encoding="utf-8")


def fixture(name: str) -> Project:
    return parse_project(fixture_text(name))


def edited(project: Project, edit) -> Project:
    data = project.model_dump(mode="json")
    edit(data)
    return Project.model_validate(data)


@pytest.fixture
def store(tmp_path) -> ProjectStore:
    return ProjectStore(tmp_path)


@pytest.mark.parametrize("name", NAMES)
def test_fixture_round_trips_byte_identically(store, tmp_path, name):
    text = fixture_text(name)
    p = parse_project(text, load_catalog())
    assert canonical_json(p) == text
    rev = store.save(p)
    assert rev == 1
    assert (tmp_path / p.id / "revisions" / "1.json").read_text(encoding="utf-8") == text
    assert (tmp_path / p.id / "project.json").read_text(encoding="utf-8") == text
    assert store.load(p.id) == p
    assert canonical_json(store.load(p.id)) == text


def test_save_is_idempotent(store, tmp_path):
    p = fixture("rooftop_500kwp")
    assert store.save(p) == 1
    before = sorted(x.name for x in (tmp_path / p.id).rglob("*"))
    assert store.save(parse_project(fixture_text("rooftop_500kwp"))) == 1
    assert sorted(x.name for x in (tmp_path / p.id).rglob("*")) == before
    assert store.revisions(p.id) == (1,)


def test_changed_content_gets_a_new_revision_and_old_ones_stay_readable(store):
    p1 = fixture("rooftop_500kwp")
    p2 = p1.model_copy(update={"name": "Renamed"})
    assert store.save(p1) == 1
    assert store.save(p2) == 2
    assert store.save(p2) == 2
    assert store.load(p1.id, 1) == p1
    assert store.load(p1.id).name == "Renamed"
    assert store.list() == (p1.id,)


def test_save_heals_a_stale_mirror_without_a_new_revision(store, tmp_path):
    p = fixture("utility_50mwac")
    store.save(p)
    mirror = tmp_path / p.id / "project.json"
    mirror.write_text("{ torn write", encoding="utf-8")
    assert store.save(p) == 1
    assert mirror.read_text(encoding="utf-8") == fixture_text("utility_50mwac")
    assert not list((tmp_path / p.id).rglob(".tmp-*"))


def test_snapshot_labels_are_idempotent_and_loadable(store):
    p = fixture("rooftop_500kwp")
    store.save(p)
    assert store.snapshot(p.id, "R0") == 1
    assert store.snapshot(p.id, "R0") == 1
    store.save(p.model_copy(update={"name": "v2"}))
    with pytest.raises(ValueError, match="snapshot 'R0' already names revision 1, not 2"):
        store.snapshot(p.id, "R0")
    assert store.load(p.id, "R0") == p
    assert store.snapshots(p.id) == {"R0": 1}


def test_missing_project_and_revision_are_reported(store):
    with pytest.raises(KeyError, match="no project 'nope'"):
        store.load("nope")
    store.save(fixture("rooftop_500kwp"))
    with pytest.raises(KeyError, match="has no revision 7"):
        store.load("rooftop-500kwp", 7)
    with pytest.raises(ValueError, match="invalid project id"):
        store.load("../etc")


def bump_inverters_and_earth_the_lt_panel(data: dict) -> None:
    net = data["network"]
    next(g for g in net["groups"] if g["id"] == "inv_sec")["count"] = 6
    net["edges"].append({"id": "pe_ltp", "src": "ltp:pe", "dst": "ep:pe", "service": "earth"})


def test_diff_reports_quantity_change_and_added_edge(store):
    p1 = fixture("rooftop_500kwp")
    p2 = edited(p1, bump_inverters_and_earth_the_lt_panel)
    assert p2.network.instance_count("inv") == 6
    store.save(p1)
    store.save(p2)
    d = store.diff(p1.id, 1, 2)
    assert d.edges.added == ("pe_ltp",)
    assert d.edges.removed == () and d.edges.changed == ()
    assert [(c.id, c.fields) for c in d.groups.changed] == [("inv_sec", (FieldChange(path="count", before=5, after=6),))]
    assert d.nodes.empty and d.sheets.empty and not d.fields


def test_diff_reports_node_quantity_and_field_changes():
    p1 = fixture("utility_50mwac")

    def edit(data):
        strings = next(n for n in data["network"]["nodes"] if n["id"] == "str")
        strings["count"] = 22
        strings["modules_in_series"] = 28
        data["network"]["nodes"] = [n for n in data["network"]["nodes"] if n["id"] != "out_la"]
        data["network"]["edges"] = [e for e in data["network"]["edges"] if e["id"] != "e_ct_la"]
        data["design_basis"]["power_factor"] = 0.9

    d = diff(p1, edited(p1, edit))
    (strings,) = d.nodes.changed
    assert strings.id == "str"
    assert set(strings.fields) == {FieldChange(path="count", before=24, after=22), FieldChange(path="modules_in_series", before=27, after=28)}
    assert d.nodes.removed == ("out_la",)
    assert d.edges.removed == ("e_ct_la",)
    assert d.fields == (FieldChange(path="design_basis.power_factor", before=0.95, after=0.9),)
    assert diff(p1, p1).empty


def test_revision_files_are_plain_project_json(store, tmp_path):
    p = fixture("rooftop_500kwp")
    store.save(p)
    assert json.loads((tmp_path / p.id / "revisions" / "1.json").read_text())["id"] == p.id

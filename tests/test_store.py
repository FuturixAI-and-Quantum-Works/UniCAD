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


SITES = Path(__file__).resolve().parents[1] / "fixtures" / "sites"


def with_site(project: Project) -> Project:
    from unicad.ingest.site_import import import_site

    geometry = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    return project.model_copy(update={"site": project.site.model_copy(update={"geometry": geometry, "crs": geometry.crs})})


def edit_feature(project: Project, feature_id: str, **changes) -> Project:
    geometry = project.site.geometry
    features = tuple(f.model_copy(update=changes) if f.id == feature_id else f for f in geometry.features)
    return project.model_copy(update={"site": project.site.model_copy(update={"geometry": geometry.model_copy(update={"features": features})})})


def test_site_diff_is_per_feature_with_a_summarised_outline():
    from unicad.domain.site import Point

    p1 = with_site(fixture("rooftop_500kwp"))
    ahu = p1.site.geometry.feature_by_id["rooftop-ahu-1"]
    moved = tuple(Point(x=q.x + 3, y=q.y) for q in ahu.polygon)
    p2 = edit_feature(edit_feature(p1, "rooftop-ahu-1", polygon=moved), "shed-a-roof", height_m=11.0)
    geometry = p2.site.geometry.with_features(f for f in p2.site.geometry.features if f.id != "chimney-stack")
    p2 = p2.model_copy(update={"site": p2.site.model_copy(update={"geometry": geometry})})

    d = diff(p1, p2)
    assert d.site_features.removed == ("chimney-stack",) and d.site_features.added == ()
    changed = {c.id: c.fields for c in d.site_features.changed}
    assert set(changed) == {"rooftop-ahu-1", "shed-a-roof"}
    assert changed["shed-a-roof"] == (FieldChange(path="height_m", before=9.5, after=11.0),)
    (outline,) = changed["rooftop-ahu-1"]
    assert outline.path == "geometry"
    assert outline.before["points"] == outline.after["points"] == 4 and outline.before["area_m2"] == outline.after["area_m2"] == 30.0
    assert [a - b for a, b in zip(outline.after["bounds"], outline.before["bounds"])] == [3, 0, 3, 0]
    assert not d.fields and d.nodes.empty and not d.empty
    assert "polygon" not in d.model_dump_json()


def test_site_diff_reports_added_features_and_frame_changes_apart():
    p1 = with_site(fixture("rooftop_500kwp"))
    geometry = p1.site.geometry
    p2 = p1.model_copy(update={"site": p1.site.model_copy(update={"geometry": geometry.model_copy(update={"setbacks": geometry.setbacks.model_copy(update={"roof_edge_m": 1.5})})})})
    d = diff(p1, p2)
    assert d.site_features.empty
    assert d.fields == (FieldChange(path="site.geometry.setbacks.roof_edge_m", before=0, after=1.5),)

    bare = fixture("rooftop_500kwp")
    d = diff(bare, p1)
    assert len(d.site_features.added) == 11 and d.site_features.removed == ()
    (frame,) = (f for f in d.fields if f.path == "site.geometry")
    assert frame.before is None and set(frame.after) == {"crs", "origin", "setbacks"}
    assert max(len(f.model_dump_json()) for f in d.fields) < 400


def layout_of(*blocks: tuple[str, int], strings: tuple[str, ...] = ()):
    from unicad.domain.layout import ArrayLayout, LayoutBlock, Mounting, Orientation, Point, SlotSpan, StringAssignment, Table

    def table(block: str, i: int, x: float = 0.0) -> Table:
        return Table(
            id=f"{block}-T{i:02d}", mounting=Mounting.ROOF_FLUSH, origin=Point(x=x, y=i * 5.0), azimuth_deg=180, tilt_deg=10, rows=2, cols=10,
            orientation=Orientation.PORTRAIT, module="m", slot_width_m=1.1, slot_height_m=2.3,
        )  # fmt: skip

    return ArrayLayout(
        blocks=tuple(
            LayoutBlock(id=b, name=b, tables=tuple(table(b, i) for i in range(n)), station=Point(x=1.0, y=2.0)) for b, n in blocks
        ),
        strings=tuple(StringAssignment(string="str", spans=(SlotSpan(table=s, row=0, col=0, n=10),)) for s in strings),
    )


def test_layout_diff_is_summarised_per_block():
    from unicad.domain.layout import Point

    base = fixture("rooftop_500kwp")
    p1 = base.model_copy(update={"array_layout": layout_of(("B01", 4), ("B02", 3))})
    after = layout_of(("B01", 4), ("B03", 2), strings=("B01-T00",))
    moved = after.blocks[0].tables[1].model_copy(update={"origin": Point(x=9.0, y=9.0)})
    blocks = (after.blocks[0].model_copy(update={"tables": (after.blocks[0].tables[0], moved, *after.blocks[0].tables[2:]), "station": Point(x=5.0, y=2.0)}), after.blocks[1])
    p2 = base.model_copy(update={"array_layout": after.model_copy(update={"blocks": blocks})})

    d = diff(p1, p2)
    assert d.layout_blocks.added == ("B03",) and d.layout_blocks.removed == ("B02",)
    (b01,) = d.layout_blocks.changed
    assert b01.id == "B01"
    assert set(b01.fields) == {
        FieldChange(path="strings", before=0, after=1),
        FieldChange(path="station.x", before=1.0, after=5.0),
        FieldChange(path="tables_modified", before=0, after=1),
    }
    assert FieldChange(path="array_layout.strings_linked", before=0, after=1) in d.fields
    assert "origin" not in d.model_dump_json()

    from_nothing = diff(base, p1)
    assert from_nothing.layout_blocks.added == ("B01", "B02") and from_nothing.layout_blocks.changed == ()
    assert diff(p1, p1).empty


def test_table_count_change_in_a_block_is_reported_as_counts():
    base = fixture("rooftop_500kwp")
    p1 = base.model_copy(update={"array_layout": layout_of(("B01", 4))})
    p2 = base.model_copy(update={"array_layout": layout_of(("B01", 6))})
    (b01,) = diff(p1, p2).layout_blocks.changed
    assert set(b01.fields) == {FieldChange(path="tables", before=4, after=6), FieldChange(path="modules", before=80, after=120)}


def test_sheet_diff_is_a_register_row_change():
    from unicad.domain.project import IssueRevision, Sheet, SheetKind

    base = fixture("rooftop_500kwp")
    sheet = Sheet(id="sld-1", number="PRJ-E-001", kind=SheetKind.SLD, title="SINGLE LINE DIAGRAM", paper="A2")
    other = Sheet(id="loc-1", number="PRJ-E-002", kind=SheetKind.LAYOUT, title="LAYOUT", paper="A1")
    issue = IssueRevision(rev="R0", date="2026-01-05", remarks="Issued for approval")
    p1 = base.model_copy(update={"sheets": (sheet, other)})
    p2 = base.model_copy(update={"sheets": (other, sheet.model_copy(update={"number": "PRJ-E-010", "revisions": (issue,)}))})
    d = diff(p1, p2)
    (change,) = d.sheets.changed
    assert change.id == "sld-1"
    assert [f.path for f in change.fields] == ["number", "revisions"]
    assert change.fields[0] == FieldChange(path="number", before="PRJ-E-001", after="PRJ-E-010")
    assert change.fields[1].after[0]["remarks"] == "Issued for approval"
    assert diff(p1, p1.model_copy(update={"sheets": (other, sheet)})).empty

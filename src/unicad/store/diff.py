from typing import Any

from pydantic import BaseModel, ConfigDict

from unicad.domain.layout import ArrayLayout, LayoutBlock
from unicad.domain.project import Project
from unicad.domain.site import SiteFeature, shape_of

Json = Any


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldChange(_Frozen):
    """`path` is dotted from the item root, list positions as integers; a missing side is None."""

    path: str
    before: Json = None
    after: Json = None


class ItemChange(_Frozen):
    id: str
    fields: tuple[FieldChange, ...]


class CollectionDiff(_Frozen):
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[ItemChange, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


class ProjectDiff(_Frozen):
    nodes: CollectionDiff
    edges: CollectionDiff
    groups: CollectionDiff
    sheets: CollectionDiff
    site_features: CollectionDiff = CollectionDiff()
    layout_blocks: CollectionDiff = CollectionDiff()
    fields: tuple[FieldChange, ...]

    @property
    def empty(self) -> bool:
        parts = (self.nodes, self.edges, self.groups, self.sheets, self.site_features, self.layout_blocks)
        return not self.fields and all(c.empty for c in parts)


def _dump(project: Project) -> dict:
    return project.model_dump(mode="json")


def _field_changes(a: Json, b: Json, path: str = "") -> list[FieldChange]:
    if a == b:
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in list(a) + [k for k in b if k not in a]:
            out += _field_changes(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _field_changes(x, y, f"{path}.{i}" if path else str(i))
        return out
    return [FieldChange(path=path, before=a, after=b)]


def _collection(a: list[dict], b: list[dict]) -> CollectionDiff:
    return _items(a, b, _field_changes)


def _register_row(a: dict, b: dict) -> tuple[FieldChange, ...]:
    """Sheet register change: one entry per top-level sheet field, so a revised issue list is one change, not a path per element."""
    return tuple(FieldChange(path=k, before=a.get(k), after=b.get(k)) for k in list(a) + [k for k in b if k not in a] if a.get(k) != b.get(k))


def _items(a: list[dict], b: list[dict], changes) -> CollectionDiff:
    before, after = {x["id"]: x for x in a}, {x["id"]: x for x in b}
    changed = tuple(ItemChange(id=i, fields=tuple(fc)) for i in before if i in after and (fc := changes(before[i], after[i])))
    return CollectionDiff(
        added=tuple(i for i in after if i not in before),
        removed=tuple(i for i in before if i not in after),
        changed=changed,
    )


def _rings(f: dict) -> dict:
    shape = shape_of(SiteFeature.model_validate(f))
    size = {"length_m": round(shape.length, 2)} if f.get("polyline") else {"area_m2": round(shape.area, 2)}
    points = len(f["polyline"] or f["polygon"])
    return {"points": points, "holes": len(f.get("holes") or ()), "bounds": [round(v, 2) for v in shape.bounds]} | size


def _site_feature(a: dict, b: dict) -> list[FieldChange]:
    """Feature attributes by name; the outline is one `geometry` entry (point count and area or length) instead of every vertex."""
    rings = ("polygon", "holes", "polyline")
    out = _field_changes({k: v for k, v in a.items() if k not in rings}, {k: v for k, v in b.items() if k not in rings})
    if any(a.get(k) != b.get(k) for k in rings):
        out.append(FieldChange(path="geometry", before=_rings(a), after=_rings(b)))
    return out


def _block_row(block: LayoutBlock, strings_by_table: dict[str, int]) -> dict:
    full = max((t.cols for t in block.tables), default=0)
    return {
        "tables": len(block.tables),
        "half_tables": sum(1 for t in block.tables if t.cols < full),
        "modules": sum(t.slot_count for t in block.tables),
        "strings": sum(strings_by_table.get(t.id, 0) for t in block.tables),
        "station": None if block.station is None else {"x": block.station.x, "y": block.station.y},
        "boundary_points": len(block.boundary),
    }


def _layout_blocks(a: ArrayLayout | None, b: ArrayLayout | None) -> CollectionDiff:
    """Layout change per block: table, module and string counts, station and boundary; tables whose position or size changed are counted."""
    rows, tables = {}, {}
    for key, layout in (("a", a), ("b", b)):
        layout = layout or ArrayLayout()
        owner = {}
        for s in layout.strings:
            owner[s.spans[0].table] = owner.get(s.spans[0].table, 0) + 1
        rows[key] = [{"id": blk.id} | _block_row(blk, owner) for blk in layout.blocks]
        tables[key] = {blk.id: {t.id: t for t in blk.tables} for blk in layout.blocks}

    def changes(x: dict, y: dict) -> list[FieldChange]:
        out = _field_changes(x, y)
        ta, tb = tables["a"][x["id"]], tables["b"][y["id"]]
        moved = sum(1 for i in ta.keys() & tb.keys() if ta[i] != tb[i])
        if moved:
            out.append(FieldChange(path="tables_modified", before=0, after=moved))
        return out

    return _items(rows["a"], rows["b"], changes)


def _strings_linked(layout: ArrayLayout | None) -> int:
    return 0 if layout is None else len(layout.strings)


def diff(a: Project, b: Project) -> ProjectDiff:
    """Structured change from `a` to `b`. Items are matched by id; order changes alone are ignored.

    Site features are matched by id with the outline summarised; the layout is compared per block; sheets as register rows."""
    da, db = _dump(a), _dump(b)
    na, nb = da.pop("network", {}), db.pop("network", {})
    sa, sb = da.pop("sheets", []), db.pop("sheets", [])
    da.pop("array_layout", None)
    db.pop("array_layout", None)
    feats = []
    for d in (da, db):
        geometry = d["site"].get("geometry")
        feats.append(geometry.pop("features", []) if geometry else [])
    fields = _field_changes(da, db)
    linked = (_strings_linked(a.array_layout), _strings_linked(b.array_layout))
    if linked[0] != linked[1]:
        fields.append(FieldChange(path="array_layout.strings_linked", before=linked[0], after=linked[1]))
    return ProjectDiff(
        nodes=_collection(na.get("nodes", []), nb.get("nodes", [])),
        edges=_collection(na.get("edges", []), nb.get("edges", [])),
        groups=_collection(na.get("groups", []), nb.get("groups", [])),
        sheets=_items(sa, sb, _register_row),
        site_features=_items(feats[0], feats[1], _site_feature),
        layout_blocks=_layout_blocks(a.array_layout, b.array_layout),
        fields=tuple(fields),
    )

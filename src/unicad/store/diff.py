from typing import Any

from pydantic import BaseModel, ConfigDict

from unicad.domain.project import Project

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
    fields: tuple[FieldChange, ...]

    @property
    def empty(self) -> bool:
        return not self.fields and all(c.empty for c in (self.nodes, self.edges, self.groups, self.sheets))


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
    before, after = {x["id"]: x for x in a}, {x["id"]: x for x in b}
    changed = tuple(
        ItemChange(id=i, fields=tuple(fc)) for i in before if i in after and (fc := _field_changes(before[i], after[i]))
    )
    return CollectionDiff(
        added=tuple(i for i in after if i not in before),
        removed=tuple(i for i in before if i not in after),
        changed=changed,
    )


def diff(a: Project, b: Project) -> ProjectDiff:
    """Structured change from `a` to `b`. Items are matched by id; order changes alone are ignored."""
    da, db = _dump(a), _dump(b)
    na, nb = da.pop("network", {}), db.pop("network", {})
    sa, sb = da.pop("sheets", []), db.pop("sheets", [])
    return ProjectDiff(
        nodes=_collection(na.get("nodes", []), nb.get("nodes", [])),
        edges=_collection(na.get("edges", []), nb.get("edges", [])),
        groups=_collection(na.get("groups", []), nb.get("groups", [])),
        sheets=_collection(sa, sb),
        fields=tuple(_field_changes(da, db)),
    )

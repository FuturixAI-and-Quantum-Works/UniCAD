"""File-based project store.

Layout under the workspace root:

    <project_id>/project.json         copy of the head revision
    <project_id>/revisions/<n>.json   immutable snapshots, n = 1, 2, ...
    <project_id>/snapshots.json       {label: n}

Revision files are the source of truth; project.json is rewritten from the head whenever it
drifts, so a crash between the two writes heals on the next save.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from unicad.domain.project import Project
from unicad.store.diff import ProjectDiff, diff

if TYPE_CHECKING:
    from unicad.catalog.store import Catalog

_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
RevRef = int | str | None


def canonical_json(project: Project) -> str:
    return json.dumps(project.model_dump(mode="json", exclude_defaults=True), indent=2, ensure_ascii=False) + "\n"


def parse_project(text: str, catalog: "Catalog | None" = None) -> Project:
    return Project.model_validate_json(text, context={"catalog": catalog} if catalog is not None else None)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_temp(directory: Path, data: bytes) -> Path:
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return Path(tmp)


def atomic_write(path: Path, data: bytes) -> None:
    tmp = _write_temp(path.parent, data)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _create_exclusive(path: Path, data: bytes) -> bool:
    """Write `path` atomically only if it does not exist yet. Returns False if another writer got there first."""
    tmp = _write_temp(path.parent, data)
    try:
        os.link(tmp, path)
    except FileExistsError:
        return False
    finally:
        tmp.unlink()
    _fsync_dir(path.parent)
    return True


class ProjectStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_id):
            raise ValueError(f"invalid project id {project_id!r}")
        return self.root / project_id

    def _rev_path(self, project_id: str, n: int) -> Path:
        return self._dir(project_id) / "revisions" / f"{n}.json"

    def list(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(sorted(p.name for p in self.root.iterdir() if (p / "revisions").is_dir() and self.revisions(p.name)))

    def revisions(self, project_id: str) -> tuple[int, ...]:
        d = self._dir(project_id) / "revisions"
        if not d.is_dir():
            return ()
        return tuple(sorted(int(p.stem) for p in d.glob("*.json") if p.stem.isdigit()))

    def head(self, project_id: str) -> int | None:
        revs = self.revisions(project_id)
        return revs[-1] if revs else None

    def save(self, project: Project) -> int:
        """Store `project` as a new revision unless it equals the head. Returns the head revision number."""
        data = canonical_json(project).encode()
        d = self._dir(project.id)
        (d / "revisions").mkdir(parents=True, exist_ok=True)
        while True:
            head = self.head(project.id)
            if head is not None and self._rev_path(project.id, head).read_bytes() == data:
                break
            n = (head or 0) + 1
            if _create_exclusive(self._rev_path(project.id, n), data):
                head = n
                break
        mirror = d / "project.json"
        if not mirror.exists() or mirror.read_bytes() != data:
            atomic_write(mirror, data)
        return head

    def _resolve(self, project_id: str, rev: RevRef) -> int:
        if rev is None:
            head = self.head(project_id)
            if head is None:
                raise KeyError(f"no project {project_id!r} in {self.root}")
            return head
        if isinstance(rev, str):
            labels = self.snapshots(project_id)
            if rev not in labels:
                raise KeyError(f"project {project_id!r} has no snapshot {rev!r}")
            return labels[rev]
        if rev not in self.revisions(project_id):
            raise KeyError(f"project {project_id!r} has no revision {rev}")
        return rev

    def read_text(self, project_id: str, rev: RevRef = None) -> str:
        return self._rev_path(project_id, self._resolve(project_id, rev)).read_text(encoding="utf-8")

    def load(self, project_id: str, rev: RevRef = None, catalog: "Catalog | None" = None) -> Project:
        return parse_project(self.read_text(project_id, rev), catalog)

    def snapshots(self, project_id: str) -> dict[str, int]:
        p = self._dir(project_id) / "snapshots.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def snapshot(self, project_id: str, label: str, rev: int | None = None) -> int:
        """Name a revision (the head by default). Re-applying the same label to the same revision is a no-op."""
        if not _LABEL.fullmatch(label):
            raise ValueError(f"invalid snapshot label {label!r}")
        n = self._resolve(project_id, rev)
        labels = self.snapshots(project_id)
        if label in labels:
            if labels[label] != n:
                raise ValueError(f"snapshot {label!r} already names revision {labels[label]}, not {n}")
            return n
        labels[label] = n
        atomic_write(self._dir(project_id) / "snapshots.json", (json.dumps(labels, indent=2, sort_keys=True) + "\n").encode())
        return n

    def diff(self, project_id: str, a: RevRef, b: RevRef = None) -> ProjectDiff:
        return diff(self.load(project_id, a), self.load(project_id, b))

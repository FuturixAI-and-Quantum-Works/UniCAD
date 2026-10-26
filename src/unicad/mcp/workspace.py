import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from unicad.catalog.store import Catalog, load_catalog
from unicad.domain.network import Edge, Group, Node
from unicad.domain.project import Project
from unicad.mcp.config import Config
from unicad.store import ProjectStore

Problem = dict[str, str]


@dataclass(frozen=True)
class Edit:
    project: Project
    touched: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)
    problems: tuple[Problem, ...] = ()


class Workspace:
    """Everything a tool module needs: the project store, the catalog, and one lock serialising edits."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = ProjectStore(config.workspace)
        self._catalog: Catalog | None = None
        self.lock = threading.RLock()

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = load_catalog(self.config.catalog)
        return self._catalog

    def resolve(self, project_id: str, ref: int | str | None) -> int:
        """Revision number for a number, a numeric string, a snapshot label, or None for the head."""
        if isinstance(ref, str) and ref.isdigit():
            ref = int(ref)
        if ref is None:
            head = self.store.head(project_id)
            if head is None:
                raise KeyError(f"no project {project_id!r} in {self.config.workspace}; project_list shows what exists")
            return head
        if isinstance(ref, str):
            labels = self.store.snapshots(project_id)
            if ref not in labels:
                raise KeyError(f"project {project_id!r} has no snapshot {ref!r}; snapshots: {sorted(labels)}")
            return labels[ref]
        if ref not in self.store.revisions(project_id):
            raise KeyError(f"project {project_id!r} has no revision {ref}; revisions: {list(self.store.revisions(project_id))}")
        return ref

    def load(self, project_id: str, ref: int | str | None = None) -> tuple[Project, int]:
        rev = self.resolve(project_id, ref)
        return self.store.load(project_id, rev, self.catalog), rev

    def rebuild(
        self,
        project: Project,
        groups: list[Group | dict] | None = None,
        nodes: list[Node | dict] | None = None,
        edges: list[Edge | dict] | None = None,
    ) -> Project:
        """`project` with the given network collections replaced, validated against the domain rules and the catalog."""
        data = project.model_dump(mode="json")
        for key, items in (("groups", groups), ("nodes", nodes), ("edges", edges)):
            if items is not None:
                data["network"][key] = [i if isinstance(i, dict) else i.model_dump(mode="json") for i in items]
        return Project.model_validate(data, context={"catalog": self.catalog})

    def mutate(self, project_id: str, change: Callable[[Project], Edit]) -> dict[str, Any]:
        """Load the head, apply `change`, save (a no-op when nothing differs) and report the outcome."""
        with self.lock:
            project, before = self.load(project_id)
            edit = change(project)
            revision = self.store.save(edit.project)
            return {
                "project_id": project_id,
                "revision": revision,
                "changed": revision != before,
                **edit.extra,
                "summary": counts(edit.project),
                "problems": list(edit.problems),
            }


def counts(project: Project) -> dict[str, int]:
    net = project.network
    return {"groups": len(net.groups), "nodes": len(net.nodes), "edges": len(net.edges)}

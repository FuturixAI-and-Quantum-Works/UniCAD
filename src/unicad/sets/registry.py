"""Sheet builders by drawing kind. A builder plans its sheets of a set and draws them; the set renderer
(`unicad.sets.render`) numbers, files and indexes whatever the builders return.

To add a drawing kind, implement `SheetBuilder` and call `register`. A builder may also define `missing(project, catalog) -> str | None`
(why it draws nothing for this project, reported as a set warning) and `summary = True` (planned and drawn after every other kind,
with the other kinds' sheets: `plan(..., others)` and `build(..., drawing_set=<drawn sheets>, kinds=<sheet id -> kind>)`)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ezdxf.document import Drawing

from unicad.catalog.store import Catalog
from unicad.domain.project import Project, Sheet
from unicad.drafting.sheets.model import CompanyProfile
from unicad.sets.numbering import Numberer


@dataclass(frozen=True)
class BuiltSheet:
    sheet: Sheet
    doc: Drawing
    scale: float | None = None
    """Scale the content was drawn at on the sheet (1.0 = full size), when the kind has one."""
    warnings: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    """Kind-specific facts for the set index, JSON-serialisable."""


class SheetBuilder(Protocol):
    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer) -> tuple[Sheet, ...]: ...

    def build(
        self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile, catalog: Catalog | None
    ) -> list[BuiltSheet]:
        """Draw `sheets` (this builder's plan); `drawing_set` is the sheets 'sheet x of y' counts (the kind's own sheets)."""
        ...


BUILDERS: dict[str, SheetBuilder] = {}


def register(kind: str, builder: SheetBuilder) -> None:
    BUILDERS[kind] = builder


def builder(kind: str) -> SheetBuilder:
    try:
        return BUILDERS[kind]
    except KeyError:
        raise KeyError(f"no sheet builder for drawing kind {kind!r}; known kinds: {sorted(BUILDERS)}") from None

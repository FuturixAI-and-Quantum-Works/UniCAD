"""Checks on the last rendered drawing set. The tools that run the rules pass the set's index in with `rendered`; without one these rules find nothing."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from unicad.catalog.store import Catalog
from unicad.domain.project import Project
from unicad.rules.model import Finding, Quantity, Severity
from unicad.rules.registry import rule


@dataclass(frozen=True)
class RenderIndex:
    """The index.json drawings_render wrote, and the project revision it rendered."""

    revision: int
    index: Mapping[str, Any]


_active: ContextVar[RenderIndex | None] = ContextVar("unicad_render_index", default=None)


@contextmanager
def rendered(index: RenderIndex | None) -> Iterator[None]:
    token = _active.set(index)
    try:
        yield
    finally:
        _active.reset(token)


@rule("drawings.readable_sld", "Rendered SLD sheets are at a readable scale")
def readable_sld(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """drawings_render fits each SLD page to its paper and marks it `readable: false` (with a red banner) when the drawing had to shrink below the
    smallest scale at which text stays legible. Such a sheet cannot be issued: split the SLD (sheet scopes), give it larger paper, or shorten long titles,
    then render again. The rule reads the index of the last render; it is stale when the project changed after that render, so render again before issue."""
    r = _active.get()
    if r is None:
        return
    for s in r.index.get("sheets", ()):
        if s.get("kind") not in ("sld", "sld_detail") or s.get("readable", True):
            continue
        scale = s.get("scale")
        yield Finding(
            rule_id="drawings.readable_sld",
            severity=Severity.ERROR,
            subjects=(str(s.get("id")),),
            message=(
                f"sheet {s.get('number')} ({s.get('title')}) was rendered at revision {r.revision} below the readable scale"
                f"{f' (drawn at {scale:g} x)' if isinstance(scale, int | float) else ''} on {s.get('paper')}: split the SLD into more sheets (sheet scopes), "
                "use larger paper or shorter titles, and render again"
            ),
            measured=float(scale) if isinstance(scale, int | float) else None,
            unit="x",
            inputs=(Quantity(name="render_revision", value=r.revision, source="drawings index.json"),),
        )

"""The string wiring diagrams of a set: one sheet (or more) per distinct inverter or SCB type, drawn by `unicad.drafting.string_diagram`."""

from collections.abc import Sequence
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.project import Project, Sheet, SheetKind
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sld.annotate import prose
from unicad.drafting.string_diagram import build_string_diagram_sheets, plan_types
from unicad.drafting.string_diagram.build import paper_and_pages
from unicad.sets.common import default_paper, safe_id
from unicad.sets.numbering import Numberer
from unicad.sets.registry import BuiltSheet, register


def _paper(project: Project) -> str:
    pool = [s for s in project.sheets if s.kind is SheetKind.STRING_DIAGRAM]
    return pool[0].paper if pool else default_paper(project, "A1", "A3")


@dataclass(frozen=True)
class StringsBuilder:
    def missing(self, project: Project, catalog: Catalog | None) -> str | None:
        if catalog is None or not plan_types(project, catalog)[0]:
            return "no pv strings feed an inverter or SCB with a catalog spec; run network_autobuild"
        return None

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer) -> tuple[Sheet, ...]:
        if catalog is None:
            return ()
        types, _ = plan_types(project, catalog)
        paper, per_type = paper_and_pages(types, PaperSize(_paper(project)))
        out = []
        for t, pages in zip(types, per_type, strict=True):
            for k in range(1, len(pages) + 1):
                old = numbers.reuse(SheetKind.STRING_DIAGRAM.value)
                suffix = f"-{k}" if len(pages) > 1 else ""
                title = f"STRING WIRING DIAGRAM : {t.label} {t.tag_range}" + (f" ({k}/{len(pages)})" if len(pages) > 1 else "")
                out.append(Sheet(id=safe_id(f"strings_{t.sink}{suffix}"), number=old.number if old else numbers.fresh("strings"),
                                 kind=SheetKind.STRING_DIAGRAM, title=prose(title), paper=paper.value, revisions=old.revisions if old else ()))  # fmt: skip
        return tuple(out)

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None) -> list[BuiltSheet]:  # fmt: skip
        if not sheets:
            return []
        numbered = project.model_copy(update={"sheets": tuple(sheets)})
        results = build_string_diagram_sheets(numbered, catalog, profile)
        if [r.number for r in results] != [s.number for s in sheets]:
            raise RuntimeError(f"string diagram sheets {[r.number for r in results]} differ from the plan {[s.number for s in sheets]}")
        return [
            BuiltSheet(s, r.doc, None, r.warnings, {"type": r.sink_template, "instances": len(r.instances), "strings_drawn": len(r.strings), "strings_covered": len(r.covers)})
            for s, r in zip(sheets, results, strict=True)
        ]


register("strings", StringsBuilder())

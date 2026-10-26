"""The drawing register of a set: sheet 000, listing every sheet of the set (itself included) with the numbers, papers and scales
the other builders gave them. Planned after and drawn after every other kind, so its rows are final."""

from collections.abc import Sequence
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.project import Project, Sheet, SheetKind
from unicad.drafting.register import NOTES, RegisterRow, register_table, set_totals
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sld.annotate import prose
from unicad.drafting.tables import render_tables
from unicad.schedules.dxf import SheetContext
from unicad.sets.common import issued_for, latest, location
from unicad.sets.numbering import Numberer
from unicad.sets.registry import BuiltSheet, register
from unicad.sets.schedules import table_pages

PAPERS = ("A3", "A4")


def _kind_of(sheet: Sheet, kinds: dict[str, str]) -> str:
    return kinds.get(sheet.id, sheet.kind.value)


def rows_for(project: Project, profile: CompanyProfile, drawing_set: Sequence[tuple[str, Sheet]]) -> list[RegisterRow]:
    rows = []
    for kind, s in drawing_set:
        rev = latest(project, s)
        rows.append(RegisterRow(
            kind=kind, number=s.number, title=prose(s.title), paper=s.paper, scale=s.scale, rev=rev.rev if rev else "00",
            issued_for=issued_for(rev).value, date=rev.date.strftime(profile.date_format) if rev else "-",
        ))  # fmt: skip
    return rows


def preamble(project: Project) -> tuple[tuple[str, str], ...]:
    rev = project.revisions[-1] if project.revisions else None
    out = [("PROJECT", prose(project.name)), ("CLIENT", prose(project.client.name)), ("LOCATION", prose(location(project)) or "-")]
    if project.design_basis.grid.utility:
        out.append(("UTILITY", prose(project.design_basis.grid.utility)))
    out.append(("SET REVISION", f"{rev.rev} - {prose(rev.remarks)}" if rev else "00 - NOT YET ISSUED"))
    return tuple(out)


@dataclass(frozen=True)
class RegisterBuilder:
    summary: bool = True
    """Planned once every other kind is planned, drawn once every other kind is drawn (`drawing_set` then holds the drawn sheets)."""

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer,
             others: Sequence[Sheet] = ()) -> tuple[Sheet, ...]:  # fmt: skip
        old = numbers.reuse(SheetKind.REGISTER.value)
        paper = old.paper if old and old.paper in PAPERS else "A3"
        n = 1
        while True:
            fake = [RegisterRow("x", "X" * 24, "X" * 40, "A0", "1:1000", "00", "INFORMATION", "00.00.00")] * (len(others) + n)
            need = table_pages(register_table(fake, preamble(project)), PaperSize(paper))
            if need <= n:
                break
            n = need
        numbers_ = [old.number if old else numbers.fresh("register")] + [numbers.fresh("register") for _ in range(n - 1)]
        title = "DRAWING REGISTER"
        return tuple(
            Sheet(id="register" if n == 1 else f"register_{k}", number=num, kind=SheetKind.REGISTER,
                  title=title + (f" (PART {k} OF {n})" if n > 1 else ""), paper=paper, revisions=old.revisions if old else ())
            for k, num in enumerate(numbers_, start=1)
        )  # fmt: skip

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None, kinds: dict[str, str] | None = None) -> list[BuiltSheet]:  # fmt: skip
        rows = rows_for(project, profile, [(_kind_of(s, kinds or {}), s) for s in drawing_set])
        spec = register_table(rows, preamble(project))
        paper = PaperSize(sheets[0].paper)
        params = set_totals(rows)

        def page_spec(page: int, pages: int, part: int):
            s = sheets[page - 1]
            return SheetContext(project, profile, s, paper, NOTES, params).spec(s.title, page, pages, 0)

        render = render_tables([(spec, paper)], profile, page_spec)
        if render.pages != len(sheets):
            raise RuntimeError(f"planned {len(sheets)} register pages, drew {render.pages}")
        meta = {"rows": [r.__dict__ for r in rows]}
        return [BuiltSheet(s, doc, None, render.warnings if i == 0 else (), meta if i == 0 else {}) for i, (s, doc) in enumerate(zip(sheets, render.docs))]


register("register", RegisterBuilder())

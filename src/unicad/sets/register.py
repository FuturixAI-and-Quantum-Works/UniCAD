"""The drawing register of a set: sheet 000, listing every sheet of the set (itself included) with the numbers, papers and scales
the other builders gave them. Planned after and drawn after every other kind, so its rows are final."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from unicad.catalog.store import Catalog
from unicad.domain.project import Project, Sheet, SheetKind
from unicad.drafting.project_fields import developer
from unicad.drafting.register import NOTES, RegisterRow, register_table, set_totals
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sld.annotate import prose
from unicad.drafting.tables import render_tables
from unicad.schedules.dxf import SheetContext
from unicad.sets.common import dated, finish, issued_for, latest, location
from unicad.sets.numbering import Numberer
from unicad.sets.registry import BuiltSheet, register
from unicad.sets.schedules import table_pages

PAPERS = ("A3", "A4")
TITLE = "DRAWING REGISTER"
SCALED = frozenset({SheetKind.LAYOUT, SheetKind.EARTHING, SheetKind.LPS})


def _kind_of(sheet: Sheet, kinds: dict[str, str]) -> str:
    return kinds.get(sheet.id, sheet.kind.value)


def _parts(sheets: Sequence[Sheet]) -> tuple[Sheet, ...]:
    """The register pages with their ids and titles for a register of len(sheets) pages."""
    n = len(sheets)
    return tuple(
        s.model_copy(update={"id": "register" if n == 1 else f"register_{k}", "title": TITLE + (f" (PART {k} OF {n})" if n > 1 else "")})
        for k, s in enumerate(sheets, start=1)
    )


def rows_for(project: Project, profile: CompanyProfile, drawing_set: Sequence[tuple[str, Sheet]], drawn_on: date | None = None) -> list[RegisterRow]:
    rows = []
    for kind, s in drawing_set:
        rev = latest(project, s)
        rows.append(RegisterRow(
            kind=kind, number=s.number, title=prose(s.title), paper=s.paper, scale=s.scale, rev=rev.rev if rev else "00",
            issued_for=issued_for(rev).value, date=d.strftime(profile.date_format) if (d := rev.date if rev else drawn_on) else "-",
        ))  # fmt: skip
    return rows


def preamble(project: Project) -> tuple[tuple[str, str], ...]:
    rev = project.revisions[-1] if project.revisions else None
    out = [("PROJECT", prose(project.name)), ("CLIENT", prose(project.client.name))]
    if dev := developer(project):
        out.append(("DEVELOPER", dev))
    out.append(("LOCATION", prose(location(project)) or "-"))
    if project.design_basis.grid.utility:
        out.append(("UTILITY", prose(project.design_basis.grid.utility)))
    out.append(("SET REVISION", f"{rev.rev} - {prose(rev.remarks)}" if rev else "00 - NOT YET ISSUED"))
    return tuple(out)


@dataclass(frozen=True)
class RegisterBuilder:
    summary: bool = True
    """Planned once every other kind is planned, drawn once every other kind is drawn (`drawing_set` then holds the drawn sheets)."""

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer,
             others: Sequence[Sheet] = (), kinds: dict[str, str] | None = None, drawn_on: date | None = None) -> tuple[Sheet, ...]:  # fmt: skip
        old = numbers.reuse(SheetKind.REGISTER.value)
        paper = old.paper if old and old.paper in PAPERS else "A3"
        base = Sheet(id="register", number=old.number if old else numbers.fresh("register"), kind=SheetKind.REGISTER, title=TITLE, paper=paper,
                     revisions=old.revisions if old else ())  # fmt: skip
        # A plan sheet's scale is only known once drawn; a wide one keeps the estimate on the safe side (build drops spare pages).
        listed = [(_kind_of(s, kinds or {}), s.model_copy(update={"scale": "1:10000"}) if s.kind in SCALED else s) for s in others]
        n = 1
        while (need := self._pages(project, profile, _parts([base] * n), listed, drawn_on)) > n:
            n = need
        numbers_ = [base.number] + [numbers.continuation(base.number, k) for k in range(1, n)]
        return _parts([base.model_copy(update={"number": num}) for num in numbers_])

    @staticmethod
    def _pages(project: Project, profile: CompanyProfile, mine: Sequence[Sheet], listed: Sequence[tuple[str, Sheet]], drawn_on: date | None) -> int:
        rows = rows_for(project, profile, [("register", s) for s in mine] + list(listed), drawn_on)
        return table_pages(register_table(rows, preamble(project)), PaperSize(mine[0].paper))

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None, kinds: dict[str, str] | None = None, drawn_on: date | None = None) -> list[BuiltSheet]:  # fmt: skip
        """Draws on the planned pages; when the drawn set lists in fewer pages than planned, the spare register pages are dropped."""
        planned = {s.id for s in sheets}
        paper = PaperSize(sheets[0].paper)
        k = len(sheets)
        while True:
            mine = _parts(sheets[:k])
            listed: list[tuple[str, Sheet]] = []
            for s in drawing_set:
                if s.id == sheets[0].id:
                    listed += [("register", x) for x in mine]
                elif s.id not in planned:
                    listed.append((_kind_of(s, kinds or {}), s))
            rows = rows_for(project, profile, listed, drawn_on)
            spec = register_table(rows, preamble(project))
            pages = table_pages(spec, paper)
            if pages == k:
                break
            if pages > k:
                raise RuntimeError(f"the drawing register needs {pages} pages but only {k} were planned for this set")
            k = pages
        params = set_totals(rows)

        def page_spec(page: int, pages: int, part: int):
            s = mine[page - 1]
            return finish(dated(SheetContext(project, profile, s, paper, NOTES, params).spec(s.title, page, pages, 0), drawn_on), project, "register")

        render = render_tables([(spec, paper)], profile, page_spec)
        meta = {"rows": [r.__dict__ for r in rows]}
        return [BuiltSheet(s, doc, None, render.warnings if i == 0 else (), meta if i == 0 else {}) for i, (s, doc) in enumerate(zip(mine, render.docs))]


register("register", RegisterBuilder())

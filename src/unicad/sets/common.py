"""Title block and paper choices shared by the set builders that draw from the stored project."""

from collections.abc import Sequence
from datetime import date

from unicad.domain.project import IssueRevision, Project, Segment, Sheet
from unicad.drafting.sheets.model import IssuedFor, PaperSize, Revision, SheetSpec, TitleBlockData
from unicad.drafting.assumptions import sheet_assumptions
from unicad.drafting.project_fields import location as project_location, project_fields
from unicad.drafting.sld.annotate import prose

LARGE_SEGMENTS = frozenset({Segment.UTILITY, Segment.BESS, Segment.HYBRID})


def revisions_of(project: Project, sheet: Sheet) -> tuple[IssueRevision, ...]:
    return sheet.revisions or project.revisions


def latest(project: Project, sheet: Sheet) -> IssueRevision | None:
    revs = revisions_of(project, sheet)
    return revs[-1] if revs else None


def issued_for(rev: IssueRevision | None) -> IssuedFor:
    return IssuedFor[rev.issued_for.name] if rev and rev.issued_for else IssuedFor.INFORMATION


def location(project: Project) -> str:
    return project_location(project)


def sheet_spec(
    project: Project, sheet: Sheet, sheet_no: int, sheet_count: int, *, scale: str | None = None, notes: Sequence[str] = (),
    legend: Sequence[tuple[str, str]] = (), parameters: Sequence[tuple[str, str]] = (), drawn_on: date | None = None, kind: str | None = None,
) -> SheetSpec:  # fmt: skip
    """Company sheet for `sheet` of the set: number, title, paper and revisions from the sheet (else the project's revisions); dated
    `drawn_on` while there is no revision; carrying the assumed inputs of drawing kind `kind`."""
    paper = PaperSize(sheet.paper)
    rev = latest(project, sheet)
    tb = TitleBlockData(
        **project_fields(project), drawing_title=prose(sheet.title), drawing_number=sheet.number, revision=rev.rev if rev else "00", scale=scale or sheet.scale,
        sheet_no=sheet_no, sheet_count=max(sheet_no, sheet_count), paper=paper, drawn_by=(rev.drawn or "") if rev else "",
        checked_by=(rev.checked or "") if rev else "", approved_by=(rev.approved or "") if rev else "", drawn_date=rev.date if rev else drawn_on,
        issued_for=issued_for(rev),
    )  # fmt: skip
    revs = tuple(
        Revision(rev=r.rev, date=r.date, description=prose(r.remarks), by=r.drawn or "", checked=r.checked or "", approved=r.approved or "")
        for r in revisions_of(project, sheet)
    )
    return SheetSpec(paper=paper, title_block=tb, revisions=revs, notes=tuple(notes), legend=tuple(legend), parameters=tuple(parameters),
                     assumptions=sheet_assumptions(project, kind) if kind else ())


def finish(spec: SheetSpec, project: Project, kind: str) -> SheetSpec:
    """A sheet drawn by the schedule tables (`unicad.schedules.dxf.SheetContext`) with the project fields and assumed inputs every set sheet carries."""
    return spec.model_copy(update={"title_block": spec.title_block.model_copy(update=project_fields(project)), "assumptions": sheet_assumptions(project, kind)})


def dated(spec: SheetSpec, drawn_on: date | None) -> SheetSpec:
    """`spec` dated `drawn_on` when its sheet has no revision date yet."""
    if spec.title_block.drawn_date is not None or drawn_on is None:
        return spec
    return spec.model_copy(update={"title_block": spec.title_block.model_copy(update={"drawn_date": drawn_on})})


def default_paper(project: Project, large: str, small: str) -> str:
    return large if project.segment in LARGE_SEGMENTS else small


def safe_id(text: str) -> str:
    out = "".join(c if c.isalnum() or c in "_.-" else "_" for c in text)
    return out if out[:1].isalnum() else f"s{out}"

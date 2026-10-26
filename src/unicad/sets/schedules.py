"""Cable schedule and bill of materials pages of a set: the table pages of `unicad.schedules.dxf`, one drawing number per page."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.project import Project, Sheet, SheetKind
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.tables import TableSpec, render_tables
from unicad.drafting.tables.draw import ROW_PAD_Y
from unicad.drafting.tables.plan import plan_table
from unicad.schedules import LengthMode, Schedule, build_bom, build_schedule
from unicad.schedules.bom import Bom
from unicad.schedules.dxf import NOTE_SOURCE, NOTE_SUMMARY, NOTE_VALUES, NOTE_VD, SheetContext, bom_table, lists_every_string, schedule_tables
from unicad.schedules.ids import SCHEME
from unicad.sets.numbering import Numberer
from unicad.sets.registry import BuiltSheet, register

_last: list[tuple[Project, Catalog, Schedule]] = []


def schedule_of(project: Project, catalog: Catalog) -> Schedule:
    """The project's cable schedule (lengths stated by the network, the rest estimated from the layout), kept for the next call."""
    if _last and _last[0][0] is project and _last[0][1] is catalog:
        return _last[0][2]
    s = build_schedule(project, catalog, LengthMode.AUTO)
    _last[:] = [(project, catalog, s)]
    return s


def table_pages(spec: TableSpec, paper: PaperSize) -> int:
    m = metrics_for(paper)
    return len(plan_table(spec, m.drawing_area, m.th, m.pad_x, ROW_PAD_Y * m.th).pages)


def _paper(project: Project, kind: SheetKind, profile: CompanyProfile) -> PaperSize:
    old = next((s for s in project.sheets if s.kind is kind), None)
    return PaperSize(old.paper if old else profile.default_paper.value)


def _plan_pages(parts: Sequence[tuple[str, TableSpec]], paper: PaperSize, numbers: Numberer, kind: SheetKind, block: str, sid: str) -> tuple[Sheet, ...]:
    plan = []
    for title, spec in parts:
        n = table_pages(spec, paper)
        plan += [title + (f" (PART {k} OF {n})" if n > 1 else "") for k in range(1, n + 1)]
    out = []
    for i, title in enumerate(plan, start=1):
        old = numbers.reuse(kind.value)
        out.append(Sheet(id=f"{sid}_{i}" if len(plan) > 1 else sid, number=old.number if old else numbers.fresh(block), kind=kind, title=title,
                         paper=paper.value, revisions=old.revisions if old else ()))  # fmt: skip
    return tuple(out)


def _draw(project: Project, profile: CompanyProfile, sheets: Sequence[Sheet], parts: Sequence[TableSpec], notes: tuple[str, ...],
          params: tuple[tuple[str, str], ...], meta: Callable[[int], dict]) -> list[BuiltSheet]:  # fmt: skip
    paper = PaperSize(sheets[0].paper)

    def spec(page: int, pages: int, part: int):
        s = sheets[page - 1]
        return SheetContext(project, profile, s, paper, notes, params).spec(s.title, page, pages, 0)

    render = render_tables([(p, paper) for p in parts], profile, spec)
    if render.pages != len(sheets):
        raise RuntimeError(f"planned {len(sheets)} table pages, drew {render.pages}")
    return [BuiltSheet(s, doc, None, render.warnings if i == 0 else (), meta(i)) for i, (s, doc) in enumerate(zip(sheets, render.docs))]


@dataclass(frozen=True)
class ScheduleBuilder:
    detail: str = "auto"

    def missing(self, project: Project, catalog: Catalog | None) -> str | None:
        if catalog is None or not schedule_of(project, catalog).rows:
            return "the network has no cabled power connections to schedule"
        return None

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer) -> tuple[Sheet, ...]:
        if self.missing(project, catalog):
            return ()
        parts = schedule_tables(schedule_of(project, catalog), self.detail)
        return _plan_pages(parts, _paper(project, SheetKind.CABLE_SCHEDULE, profile), numbers, SheetKind.CABLE_SCHEDULE, "cable_schedule", "cables")

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None) -> list[BuiltSheet]:  # fmt: skip
        if not sheets:
            return []
        schedule = schedule_of(project, catalog)
        totals = schedule.totals()
        params = (
            ("CABLES SCHEDULED", f"{totals['rows']:.0f}"),
            ("CABLE TO SUPPLY", f"{totals['cable_m'] / 1000:,.1f} km"),
            ("TOTAL POWER LOSS", f"{totals['loss_w'] / 1000:,.1f} kW"),
            *[(k, v.split(" (")[0]) for k, v in schedule.vd_summary if k.endswith("WORST PATH")],
        )
        notes = (NOTE_VALUES, NOTE_SOURCE, NOTE_VD, *(() if lists_every_string(schedule, self.detail) else (NOTE_SUMMARY,)), SCHEME.upper())
        parts = [spec for _, spec in schedule_tables(schedule, self.detail)]
        meta = {"rows": len(schedule.rows), "gaps": len(schedule.gaps)}
        return _draw(project, profile, sheets, parts, notes, params, lambda i: meta if i == 0 else {})


@dataclass(frozen=True)
class BomBuilder:
    def _bom(self, project: Project, catalog: Catalog) -> Bom:
        return build_bom(project, catalog, schedule_of(project, catalog))

    def missing(self, project: Project, catalog: Catalog | None) -> str | None:
        if catalog is None or not project.network.nodes:
            return "the project has no network; run network_autobuild"
        return None

    def plan(self, project: Project, profile: CompanyProfile, catalog: Catalog | None, numbers: Numberer) -> tuple[Sheet, ...]:
        if self.missing(project, catalog):
            return ()
        parts = [("BILL OF MATERIALS", bom_table(self._bom(project, catalog)))]
        return _plan_pages(parts, _paper(project, SheetKind.BOM, profile), numbers, SheetKind.BOM, "bom", "bom")

    def build(self, project: Project, sheets: Sequence[Sheet], drawing_set: Sequence[Sheet], profile: CompanyProfile,
              catalog: Catalog | None) -> list[BuiltSheet]:  # fmt: skip
        if not sheets:
            return []
        bom = self._bom(project, catalog)
        s = bom.summary()
        params = (("MODULES", f"{s['modules']:,.0f}"), ("INVERTERS", f"{s['inverters']:,.0f}"), ("CABLE ORDERED", f"{s['cable_m_ordered'] / 1000:,.1f} km"))
        notes = tuple(a.upper() for a in bom.assumptions)
        built = _draw(project, profile, sheets, [bom_table(bom)], notes, params, lambda i: {"lines": len(bom.lines)} if i == 0 else {})
        return [b if i else BuiltSheet(b.sheet, b.doc, b.scale, tuple(dict.fromkeys([*b.warnings, *bom.warnings])), b.meta) for i, b in enumerate(built)]


register("cable_schedule", ScheduleBuilder())
register("bom", BomBuilder())

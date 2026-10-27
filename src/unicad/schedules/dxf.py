"""Cable schedule and bill of materials as company-sheet DXF pages."""

import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from unicad.domain.network import Service
from unicad.domain.project import Project, Sheet, SheetKind
from unicad.drafting.sheets.model import CompanyProfile, IssuedFor, PaperSize, Revision, SheetSpec, TitleBlockData
from unicad.drafting.sheets.titleblock import compose_location
from unicad.drafting.tables import Column, TableRender, TableSection, TableSpec, render_tables
from unicad.schedules.bom import CATEGORY_ORDER, Bom
from unicad.schedules.ids import SCHEME, indices
from unicad.schedules.model import CableRow, Schedule, Section

PROFILE_DIR = Path(__file__).resolve().parents[3] / "profiles"
DETAIL_ROW_LIMIT = 150
REMARK_CHARS = 40
NOTE_VALUES = "VALUES ARE INDICATIVE; CABLE LENGTHS SHALL BE CONFIRMED BY MEASUREMENT AT SITE BEFORE LAYING."
NOTE_SOURCE = "LENGTH SOURCE: NET = LENGTH STATED IN THE NETWORK, EST = ESTIMATED FROM THE ARRAY LAYOUT (MANHATTAN ROUTE PLUS DROPS AND TERMINATION ALLOWANCE)."
NOTE_SUMMARY = "STRING ROWS ARE SUMMARISED PER COMBINER: LENGTHS, VOLTAGE DROP, IB AND IZ ARE THE WORST OF THE STRINGS AND POWER LOSS IS THEIR SUM; THE XLSX LISTS EVERY STRING."
NOTE_VD = "VOLTAGE DROP AND POWER LOSS ARE AT 90 DEG C CONDUCTOR RESISTANCE FROM THE CATALOG; IB AND IZ ARE FOR ALL PARALLEL RUNS TOGETHER."


def load_profile(name: str) -> CompanyProfile:
    dirs = [Path(d) for d in os.environ.get("UNICAD_PROFILES", "").split(os.pathsep) if d] + [PROFILE_DIR]
    for d in dirs:
        if (path := d / f"{name}.json").is_file():
            return CompanyProfile.load(path)
    raise KeyError(f"no company profile {name!r}; looked in {[str(d) for d in dirs]}")


def _num(value: float | None, places: int = 2) -> str:
    return "-" if value is None else f"{value:.{places}f}"


def _remarks(row: CableRow) -> str:
    """Length source, then the network's remark when it is short enough for the cell; the XLSX carries it in full."""
    source = {"network": "NET", "estimated": "EST"}.get(row.length_source, "NO LENGTH")
    flags = ["NO Iz" if "ampacity" in f else "NO VD" for f in row.flags]
    return " ".join(p for p in (source, *flags, row.remarks.upper() if len(row.remarks) <= REMARK_CHARS else "") if p)


def _laying(row: CableRow) -> str:
    return (row.laying or "-").replace("_", " ").upper()


def _ip(row: CableRow) -> str:
    return f"IP{indices(row.src)[-1]:02d}"


def _summarise(rows: list[CableRow]) -> list[tuple[str, ...]]:
    """One display row per combiner for string cables: lengths, drop and Ib/Iz are the worst of the strings, power loss their sum."""
    groups: dict[str, list[CableRow]] = defaultdict(list)
    for r in rows:
        groups[r.dst].append(r)
    out = []
    for group in groups.values():
        first = group[0]
        lengths = [r.length_pos_m for r in group if r.length_pos_m is not None]
        negs = [r.length_neg_m for r in group if r.length_neg_m is not None]
        drops = [r.vd_pct for r in group if r.vd_pct is not None]
        losses = [r.loss_w for r in group if r.loss_w is not None]
        ident = f"{first.stem}-{_ip(first)}..{_ip(group[-1])}-{first.size_mm2:g} sqmm"
        out.append(
            (
                ident, f"{first.from_tag}..{group[-1].from_tag}", first.to_tag, first.callout, str(first.runs),
                _num(max(lengths) if lengths else None), _num(max(negs) if negs else None), _laying(first),
                _num(first.ib_a, 1), _num(first.iz_a, 1), _num(max(drops) if drops else None), _num(sum(losses) if losses else None),
                f"MAX OF {len(group)}. {_remarks(first)}",
            )
        )  # fmt: skip
    return out


def _dc_row(r: CableRow) -> tuple[str, ...]:
    return (
        r.cable_id, r.from_tag, r.to_tag, r.callout, str(r.runs), _num(r.length_pos_m), _num(r.length_neg_m), _laying(r),
        _num(r.ib_a, 1), _num(r.iz_a, 1), _num(r.vd_pct), _num(r.loss_w), _remarks(r),
    )  # fmt: skip


def _ac_row(r: CableRow) -> tuple[str, ...]:
    return (
        r.cable_id, r.from_tag, r.to_tag, r.callout, str(r.runs), _num(r.length_pos_m), _laying(r),
        _num(r.ib_a, 1), _num(r.iz_a, 1), _num(r.vd_pct), _num(r.loss_w), _remarks(r),
    )  # fmt: skip


DC_COLUMNS = (
    Column("CABLE IDENTIFICATION NO.", "left"),
    Column("FROM", "left"),
    Column("TO", "left"),
    Column("CABLE", "left"),
    Column("NO. OF\nRUNS"),
    Column("POSITIVE\nPOLARITY", group="LENGTH (m)"),
    Column("NEGATIVE\nPOLARITY", group="LENGTH (m)"),
    Column("LAYING"),
    Column("Ib\n(A)"),
    Column("Iz\n(A)"),
    Column("VOLTAGE\nDROP (%)"),
    Column("POWER\nLOSS (W)"),
    Column("REMARKS", "left", wrap=True),
)
AC_COLUMNS = (
    Column("CABLE IDENTIFICATION NO.", "left"),
    Column("FROM", "left"),
    Column("TO", "left"),
    Column("CABLE", "left"),
    Column("NO. OF\nRUNS"),
    Column("LENGTH\n(m)"),
    Column("LAYING"),
    Column("Ib\n(A)"),
    Column("Iz\n(A)"),
    Column("VOLTAGE\nDROP (%)"),
    Column("POWER\nLOSS (W)"),
    Column("REMARKS", "left", wrap=True),
)


def _numbered(columns: tuple[Column, ...]) -> tuple[Column, ...]:
    return (Column("SL.NO"), *columns)


def _sections(schedule: Schedule, services: frozenset[Service], detail: bool) -> list[TableSection]:
    """Table sections of the given services, numbered continuously. Strings are summarised per combiner unless `detail`. On a plant of
    several blocks each DC block starts a page."""
    out: list[TableSection] = []
    sl = 0
    blocks = {r.block for sec in schedule.sections if sec.service is Service.DC for r in sec.rows}
    split = len(blocks) > 1 and "" not in blocks
    prev = None
    for sec in schedule.sections:
        if sec.service not in services:
            continue
        for block, rows in _by_block(sec, split):
            body = _summarise(rows) if rows[0].is_string and not detail else [_row_cells(r) for r in rows]
            numbered = tuple((str(sl + i + 1), *cells) for i, cells in enumerate(body))
            sl += len(body)
            title = sec.title if block is None else f"{sec.title} - {block}"
            out.append(TableSection(title, numbered, page_break_before=block is not None and prev is not None and block != prev))
            prev = block
    return out


def _row_cells(r: CableRow) -> tuple[str, ...]:
    return _dc_row(r) if r.is_dc else _ac_row(r)


def _by_block(sec: Section, split: bool) -> list[tuple[str | None, list[CableRow]]]:
    """Rows of a DC section split by block (in order of first appearance) when the plant has several blocks; other sections stay whole."""
    if sec.service is not Service.DC or not split:
        return [(None, list(sec.rows))]
    blocks = list(dict.fromkeys(r.block for r in sec.rows))
    return [(b, [r for r in sec.rows if r.block == b]) for b in blocks]


def _formula(text: str) -> str:
    return text.split(". ", 1)[0]


def _preamble(schedule: Schedule, dc: bool) -> tuple[tuple[str, str], ...]:
    keep = ("MODULE", "DC CABLES", "DC VOLTAGE DROP") if dc else ("LV AC CABLES", "MV AC CABLES", "AC VOLTAGE DROP")
    rows = [(k, _formula(v) if k.endswith("VOLTAGE DROP") else v) for k, v in schedule.basis if k in keep]
    rows += [(k, v) for k, v in schedule.vd_summary if k.startswith("DC VD, WORST") == dc and k.endswith("WORST PATH")]
    return tuple(rows)


def lists_every_string(schedule: Schedule, detail: str) -> bool:
    if detail not in ("auto", "full", "summary"):
        raise ValueError(f"detail must be auto, full or summary, not {detail!r}")
    strings = sum(1 for r in schedule.rows if r.is_string)
    return detail == "full" or (detail == "auto" and strings <= DETAIL_ROW_LIMIT)


def schedule_tables(schedule: Schedule, detail: str = "auto") -> list[tuple[str, TableSpec]]:
    """(drawing title, table) for the DC schedule and, when there are AC rows, the AC and MV schedule."""
    full = lists_every_string(schedule, detail)
    out = []
    dc = _sections(schedule, frozenset({Service.DC}), full)
    if dc:
        out.append(("DC CABLE SCHEDULE", TableSpec(_numbered(DC_COLUMNS), tuple(dc), _preamble(schedule, True))))
    ac = _sections(schedule, frozenset({Service.LV_AC, Service.MV_AC, Service.HV_AC}), True)
    if ac:
        has_mv = any(r.service in (Service.MV_AC, Service.HV_AC) for r in schedule.rows)
        out.append(("AC AND MV CABLE SCHEDULE" if has_mv else "AC CABLE SCHEDULE", TableSpec(_numbered(AC_COLUMNS), tuple(ac), _preamble(schedule, False))))
    return out


def bom_table(bom: Bom) -> TableSpec:
    cols = (
        Column("SL.NO"),
        Column("ITEM", "left", wrap=True),
        Column("CATALOG ID", "left"),
        Column("QUANTITY"),
        Column("UNIT"),
        Column("REMARKS", "left", wrap=True),
    )
    sections, sl = [], 0
    grouped = bom.by_category()
    for cat in CATEGORY_ORDER:
        lines = grouped.get(cat)
        if not lines:
            continue
        rows = []
        for ln in lines:
            sl += 1
            rows.append((str(sl), ln.item, ln.catalog_id or "-", f"{ln.qty:,.0f}", ln.unit.upper(), ln.remarks.upper()))
        sections.append(TableSection(cat, tuple(rows)))
    return TableSpec(cols, tuple(sections))


@dataclass(frozen=True)
class SheetContext:
    project: Project
    profile: CompanyProfile
    sheet: Sheet | None
    paper: PaperSize
    notes: tuple[str, ...]
    parameters: tuple[tuple[str, str], ...]

    def spec(self, title: str, page: int, pages: int, seq: int) -> SheetSpec:
        p, sheet = self.project, self.sheet
        revs = (sheet.revisions if sheet and sheet.revisions else p.revisions)
        latest = revs[-1] if revs else None
        issued = IssuedFor[latest.issued_for.name] if latest and latest.issued_for else IssuedFor.INFORMATION
        site = p.site
        number = sheet.number if sheet else self.profile.drawing_number(client=p.client.code or "CLIENT", project=p.id.upper(), discipline="electrical", seq=seq)
        tb = TitleBlockData(
            client=p.client.name.upper(), project_title=p.name.upper(), location=compose_location(site.name, site.address, site.state).upper(),
            drawing_title=title, drawing_number=number, revision=latest.rev if latest else "00", scale="NTS", sheet_no=page, sheet_count=pages,
            paper=self.paper, drawn_by=(latest.drawn or "") if latest else "", checked_by=(latest.checked or "") if latest else "",
            approved_by=(latest.approved or "") if latest else "", drawn_date=latest.date if latest else None, issued_for=issued,
        )  # fmt: skip
        return SheetSpec(
            paper=self.paper, title_block=tb, notes=self.notes, parameters=self.parameters,
            revisions=tuple(Revision(rev=r.rev, date=r.date, description=r.remarks.upper(), by=r.drawn or "", checked=r.checked or "", approved=r.approved or "") for r in revs),
        )  # fmt: skip


def _sheet_of(project: Project, kind: SheetKind) -> Sheet | None:
    return next((s for s in project.sheets if s.kind is kind), None)


def render_schedule(project: Project, schedule: Schedule, profile: CompanyProfile, detail: str = "auto", paper: str | None = None) -> tuple[TableRender, list[str]]:
    """Pages of the cable schedule, and the drawing title of each table part."""
    parts = schedule_tables(schedule, detail)
    if not parts:
        raise ValueError("the schedule has no cables to draw")
    sheet = _sheet_of(project, SheetKind.CABLE_SCHEDULE)
    size = PaperSize(paper or (sheet.paper if sheet else profile.default_paper.value))
    totals = schedule.totals()
    params = (
        ("CABLES SCHEDULED", f"{totals['rows']:.0f}"),
        ("CABLE TO SUPPLY", f"{totals['cable_m'] / 1000:,.1f} km"),
        ("TOTAL POWER LOSS", f"{totals['loss_w'] / 1000:,.1f} kW"),
        *[(k, v.split(" (")[0]) for k, v in schedule.vd_summary if k.endswith("WORST PATH")],
    )
    notes = (NOTE_VALUES, NOTE_SOURCE, NOTE_VD, *(() if lists_every_string(schedule, detail) else (NOTE_SUMMARY,)), SCHEME.upper())
    ctx = SheetContext(project, profile, sheet, size, notes, params)
    titles = [t for t, _ in parts]
    render = render_tables([(spec, size) for _, spec in parts], profile, lambda page, pages, part: ctx.spec(titles[part], page, pages, 90 + part))
    return render, titles


def render_bom(project: Project, bom: Bom, profile: CompanyProfile, paper: str | None = None) -> TableRender:
    sheet = _sheet_of(project, SheetKind.BOM)
    size = PaperSize(paper or (sheet.paper if sheet else profile.default_paper.value))
    s = bom.summary()
    params = (("MODULES", f"{s['modules']:,.0f}"), ("INVERTERS", f"{s['inverters']:,.0f}"), ("CABLE ORDERED", f"{s['cable_m_ordered'] / 1000:,.1f} km"))
    ctx = SheetContext(project, profile, sheet, size, tuple(a.upper() for a in bom.assumptions), params)
    return render_tables([(bom_table(bom), size)], profile, lambda page, pages, part: ctx.spec("BILL OF MATERIALS", page, pages, 95))

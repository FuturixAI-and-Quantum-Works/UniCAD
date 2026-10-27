"""Render a project's drawing set: per-sheet DXF, PDF and DWG, one merged PDF, and a JSON index."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from unicad.catalog.store import Catalog, load_catalog
from unicad.domain.project import Project, Sheet
from unicad.drafting.sheets.layermap import apply_layer_map
from unicad.drafting.sheets.model import CompanyProfile
from unicad.drafting.sld.sheet import save_deterministic
from unicad.render import DwgConverter, LibreCadRenderer, RenderError, Renderer
from unicad.render import Sheet as RenderSheet
from unicad.sets.numbering import Numberer
from unicad.sets.registry import BuiltSheet, builder

PROFILES = Path(__file__).resolve().parents[3] / "profiles"
Format = Literal["dxf", "pdf", "dwg"]
INDEX = "index.json"
SET_PDF = "set.pdf"


def load_profile(name: str, root: Path = PROFILES) -> CompanyProfile:
    path = root / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no company profile {name!r} in {root}")
    return CompanyProfile.load(path)


def plan_set(project: Project, kinds: Sequence[str], profile: CompanyProfile, catalog: Catalog | None) -> list[tuple[str, tuple[Sheet, ...]]]:
    """Sheets of each kind in `kinds` order. Kinds whose builder summarises the set (the register) are planned last, knowing the rest."""
    numbers = Numberer.for_project(project, profile)
    planned: dict[str, tuple[Sheet, ...]] = {}
    for k in kinds:
        if not getattr(builder(k), "summary", False):
            planned[k] = builder(k).plan(project, profile, catalog, numbers)
    others = tuple(s for sheets in planned.values() for s in sheets)
    owner = {s.id: k for k, sheets in planned.items() for s in sheets}
    for k in kinds:
        if getattr(builder(k), "summary", False):
            planned[k] = builder(k).plan(project, profile, catalog, numbers, others, kinds=owner)
    return [(k, planned[k]) for k in kinds]


def missing_kinds(project: Project, kinds: Sequence[str], catalog: Catalog | None) -> list[str]:
    """Why each requested kind with a `missing` check draws nothing for this project."""
    out = []
    for k in kinds:
        check = getattr(builder(k), "missing", None)
        if check is not None and (why := check(project, catalog)):
            out.append(f"{k}: not drawn: {why}")
    return out


def _file_stem(i: int, sheet: Sheet) -> str:
    return f"{i:02d}-{re.sub(r'[^A-Za-z0-9._-]+', '_', sheet.number)}"


def _clear_previous(out_dir: Path) -> None:
    """Delete the files an earlier render listed in `out_dir`'s index, so a changed plan leaves no stale sheets."""
    index = out_dir / INDEX
    if not index.is_file():
        return
    try:
        old = json.loads(index.read_text(encoding="utf-8"))
    except ValueError:
        return
    names = [old.get("set_pdf")] + [f for s in old.get("sheets", []) for f in s.get("files", {}).values()]
    for name in names:
        if name and Path(name).name == name:
            (out_dir / name).unlink(missing_ok=True)
    index.unlink()


@dataclass(frozen=True)
class SetIndex:
    out_dir: Path
    project_id: str
    sheets: tuple[dict[str, Any], ...]
    set_pdf: Path | None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self, absolute: bool = False) -> dict[str, Any]:
        def path(rel: str | None) -> str | None:
            return None if rel is None else str(self.out_dir / rel) if absolute else rel

        return {
            "project_id": self.project_id,
            "sheet_count": len(self.sheets),
            "set_pdf": path(SET_PDF if self.set_pdf else None),
            "sheets": [s | {"files": {k: path(v) for k, v in s["files"].items()}} for s in self.sheets],
            "warnings": list(self.warnings),
        }


def render_set(
    project: Project,
    out_dir: Path,
    *,
    kinds: Sequence[str] = ("sld",),
    formats: Sequence[Format] = ("dxf", "pdf"),
    profile: CompanyProfile | None = None,
    catalog: Catalog | None = None,
    renderer: Renderer | None = None,
    converter: DwgConverter | None = None,
) -> SetIndex:
    """Plan, draw and file the sheets of `kinds` into `out_dir`. DXF is always written (PDF and DWG are made
    from it). A missing DWG converter is reported as a warning, not an error. The index lists every sheet
    with its number, 'sheet x of y', paper, drawing scale, files and kind-specific facts (SLD cross-references)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous(out_dir)
    catalog = catalog if catalog is not None else load_catalog()
    profile = profile if profile is not None else load_profile(project.company_profile)
    planned = plan_set(project, kinds, profile, catalog)
    warnings: list[str] = missing_kinds(project, kinds, catalog)
    done: dict[str, list[BuiltSheet]] = {}
    for kind, sheets in planned:
        if not getattr(builder(kind), "summary", False):
            done[kind] = builder(kind).build(project, sheets, sheets, profile, catalog)
    for kind, sheets in planned:
        if getattr(builder(kind), "summary", False):
            final: list[Sheet] = []
            for k, s in planned:
                final += [b.sheet for b in done[k]] if k in done else list(s)
            owner = {s.id: k for k, ss in planned for s in ss}
            done[kind] = builder(kind).build(project, sheets, final, profile, catalog, kinds=owner)
    built = [(kind, b) for kind, _ in planned for b in done[kind]]
    per_kind = {kind: len(done[kind]) for kind, _ in planned}
    seen: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    for i, (kind, b) in enumerate(built, start=1):
        seen[kind] = seen.get(kind, 0) + 1
        stem = _file_stem(i, b.sheet)
        warnings += apply_layer_map(b.doc, profile)
        dxf = out_dir / f"{stem}.dxf"
        save_deterministic(b.doc, dxf)
        entries.append({
            "id": b.sheet.id,
            "kind": kind,
            "sheet_kind": b.sheet.kind.value,
            "number": b.sheet.number,
            "title": b.sheet.title,
            "paper": b.sheet.paper,
            "sheet_no": i,
            "sheet_of": f"{i} of {len(built)}",
            "kind_sheet_of": f"{seen[kind]} of {per_kind[kind]}",
            "scale": None if b.scale is None else round(b.scale, 3),
            "drawing_scale": b.sheet.scale,
            "scope": list(b.sheet.scope.nodes),
            "files": {"dxf": dxf.name, "pdf": None, "dwg": None},
            "warnings": list(b.warnings),
            **b.meta,
        })
    set_pdf = None
    if "pdf" in formats:
        renderer = renderer or LibreCadRenderer()
        pdf_dir = out_dir / "pdf"
        result = renderer.build_set(
            [RenderSheet(out_dir / e["files"]["dxf"], e["paper"], f"{e['number']}  {e['title']}") for e in entries],
            out_dir / SET_PDF,
            work_dir=pdf_dir,
        )
        set_pdf = result.pdf
        for e, page in zip(entries, result.pages):
            target = out_dir / f"{Path(e['files']['dxf']).stem}.pdf"
            page.pdf.replace(target)
            e["files"]["pdf"] = target.name
        if pdf_dir.is_dir() and not any(pdf_dir.iterdir()):
            pdf_dir.rmdir()
    if "dwg" in formats:
        converter = converter or DwgConverter()
        for e in entries:
            dxf = out_dir / e["files"]["dxf"]
            try:
                result = converter.to_dwg(dxf, dxf.with_suffix(".dwg"), strict=False)
            except RenderError as exc:
                warnings.append(f"{e['number']}: no DWG: {exc}")
                continue
            e["files"]["dwg"] = dxf.with_suffix(".dwg").name
            e["dwg_readback"] = {k: list(v) for k, v in result.mismatches.items()}
            # LibreDWG's dwg2dxf reports extra ATTRIBs on read-back (docs/compat.md, DWG); anything else is a real loss.
            lost = {k: v for k, v in result.mismatches.items() if k != "ATTRIB"}
            if lost:
                warnings.append(f"{e['number']}: DWG reads back with different entity counts {lost}")
    index = SetIndex(out_dir, project.id, tuple(entries), set_pdf, tuple(warnings))
    (out_dir / INDEX).write_text(json.dumps(index.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index


def render_sld_set(project: Project, out_dir: Path, **kw: Any) -> SetIndex:
    return render_set(project, out_dir, kinds=("sld",), **kw)

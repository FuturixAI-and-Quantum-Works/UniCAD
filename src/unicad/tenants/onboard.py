"""Turn a company's DXF and a short questionnaire into a validated company profile, saying what was found, defaulted and refused."""

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.addons import Importer
from ezdxf.document import Drawing

from unicad.drafting.dxfrules import MIN_LINEWEIGHT
from unicad.drafting.sheets.model import (
    CompanyProfile,
    LayerRole,
    LayerSpec,
    Numbering,
    PaperSize,
    SymbolOverride,
    TemplateRef,
    ThresholdSetting,
    TitleBlockChoice,
    default_layers,
)
from unicad.drafting.sheets.symbol_override import SymbolOverrideError, check_override
from unicad.drafting.sheets.template import KNOWN_PLACEHOLDERS, parse_template
from unicad.drafting.sld.sheet import save_deterministic
from unicad.rules.thresholds import BY_NAME, Setting, validate
from unicad.tenants.answers import OnboardingAnswers, TitleSource
from unicad.tenants.check import check_template
from unicad.tenants.store import profiles_dir, save_profile

_PAPER = re.compile(r"(?<![A-Z0-9])(A[0-4])(?![A-Z0-9])")
_ROLE_WORDS = {LayerRole.FRAME: ("FRAME", "BORDER"), LayerRole.TITLE: ("TITLE", "TB"), LayerRole.TEXT: ("TEXT", "ANNO")}
LIBRECAD_LINETYPES = frozenset("CONTINUOUS CENTER CENTER2 CENTERX2 DASHED DASHED2 DASHEDX2 DASHDOT DASHDOT2 DASHDOTX2 DOT DOT2 DOTX2 DIVIDE DIVIDE2 DIVIDEX2 HIDDEN HIDDEN2 HIDDENX2 BORDER BORDER2 BORDERX2".split())
MIN_PLACEHOLDERS = 3


@dataclass
class OnboardingReport:
    profile_id: str
    found: list[str] = field(default_factory=list)
    defaulted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    profile: CompanyProfile | None = None
    path: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def saved(self) -> bool:
        return self.path is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "ok": self.ok,
            "saved": self.saved,
            "path": None if self.path is None else str(self.path),
            "found": self.found,
            "defaulted": self.defaulted,
            "failed": self.failed,
            "warnings": self.warnings,
        }


def _layer_spec(doc: Drawing, name: str, report: OnboardingReport) -> LayerSpec:
    layer = doc.layers.get(name)
    lw = layer.dxf.get("lineweight", -3)
    if lw < MIN_LINEWEIGHT:
        report.warnings.append(f"layer {name}: lineweight {lw} is not printable, 0.25 mm used")
        lw = 25
    lt = layer.dxf.get("linetype", "CONTINUOUS")
    if lt.upper() not in LIBRECAD_LINETYPES:
        report.warnings.append(f"layer {name}: linetype {lt!r} is not one LibreCAD prints, CONTINUOUS used")
        lt = "CONTINUOUS"
    return LayerSpec(name=name, colour=abs(layer.dxf.get("color", 7)) or 7, linetype=lt.upper(), lineweight=lw)


def _roles(doc: Drawing, a: OnboardingAnswers, report: OnboardingReport) -> dict[LayerRole, LayerSpec]:
    layers: dict[LayerRole, LayerSpec] = {}
    names = [layer.dxf.name for layer in doc.layers]
    for role in LayerRole:
        chosen = a.layers.get(role)
        if chosen is not None:
            if chosen not in names:
                report.failed.append(f"layer {chosen!r} for the sheet {role.value} is not in the DXF; layers: {sorted(names)}")
                continue
            layers[role] = _layer_spec(doc, chosen, report)
            report.found.append(f"sheet {role.value} layer: {chosen} (from the answers)")
            continue
        guess = [n for n in names if any(w in re.split(r"[^A-Z0-9]+", n.upper()) or n.upper().endswith(w) for w in _ROLE_WORDS[role])]
        guess = [n for n in guess if n.upper() not in {"0", "DEFPOINTS"}]
        if len(guess) == 1:
            layers[role] = _layer_spec(doc, guess[0], report)
            report.found.append(f"sheet {role.value} layer: {guess[0]} (matched by name)")
        else:
            default = default_layers()[role]
            report.defaulted.append(f"sheet {role.value} layer: {default.name}" + (f" (candidates {guess} - name one under layers.{role.value})" if guess else ""))
    return layers


def _layer_map(doc: Drawing, a: OnboardingAnswers, report: OnboardingReport) -> dict[str, LayerSpec]:
    out: dict[str, LayerSpec] = {}
    for src, name in sorted(a.layer_map.items()):
        if name not in doc.layers:
            report.failed.append(f"layer_map {src} -> {name!r}: the DXF has no such layer")
            continue
        out[src] = _layer_spec(doc, name, report)
        report.found.append(f"layer map: {src} -> {name}")
    used = {s.name for s in out.values()} | {a.layers[r] for r in a.layers}
    free = sorted(layer.dxf.name for layer in doc.layers if layer.dxf.name not in used and layer.dxf.name not in {"0", "Defpoints"})
    if free:
        report.found.append(f"company layers not used by the profile: {', '.join(free)}")
    return out


def _paper_of(name: str) -> PaperSize | None:
    m = _PAPER.search(re.sub(r"_", " ", name.upper()))
    return PaperSize(m.group(1)) if m else None


def _title_blocks(doc: Drawing, dxf: Path, a: OnboardingAnswers, report: OnboardingReport) -> dict[PaperSize, TemplateRef]:
    refs: dict[PaperSize, TemplateRef] = {}
    explicit = {p: (TitleSource(block=s) if isinstance(s, str) else s) for p, s in a.title_blocks.items()}
    for paper, src in explicit.items():
        if (src.block is None) == (src.layer is None):
            report.failed.append(f"title_blocks.{paper.value}: give exactly one of block or layer")
            continue
        refs[paper] = TemplateRef(dxf=dxf, block=src.block, layer=src.layer)
    if not explicit:
        for block in doc.blocks:
            if block.name.startswith("*") or len(block) == 0:
                continue
            try:
                t = parse_template(TemplateRef(dxf=dxf, block=block.name))
            except (ValueError, OSError):
                continue
            if len(t.fields) < MIN_PLACEHOLDERS or not {f.name for f in t.fields} & KNOWN_PLACEHOLDERS:
                continue
            paper = _paper_of(block.name)
            if paper is None:
                report.failed.append(f"block {block.name!r} has title block placeholders but no paper size (A0..A4) in its name; list it under title_blocks")
            elif paper in refs:
                report.failed.append(f"blocks {refs[paper].block!r} and {block.name!r} are both title blocks for {paper.value}; name one under title_blocks")
            else:
                refs[paper] = TemplateRef(dxf=dxf, block=block.name)
    good: dict[PaperSize, TemplateRef] = {}
    for paper, ref in sorted(refs.items()):
        try:
            problems, facts = check_template(paper, ref)
        except Exception as exc:
            problems, facts = [f"title block for {paper.value}: {exc}"], []
        report.failed += problems
        report.found += facts
        if not problems:
            good[paper] = ref
    return good


def _logo(doc: Drawing, a: OnboardingAnswers, out: Path, report: OnboardingReport) -> Path | None:
    names = [b.name for b in doc.blocks if not b.name.startswith("*")]
    name = a.logo_block or next((n for n in names if "LOGO" in n.upper()), None)
    if name is None:
        report.defaulted.append("logo: none found, the short code is drawn in a box")
        return None
    if name not in doc.blocks:
        report.failed.append(f"logo block {name!r} is not in the DXF; blocks: {sorted(names)}")
        return None
    block = doc.blocks.get(name)
    entities = [e for e in block if e.dxftype() not in ("ATTDEF", "ATTRIB")]
    target = ezdxf.new("R2018", setup=["linetypes"])
    importer = Importer(doc, target)
    importer.import_entities(entities, target.modelspace())
    importer.finalize()
    for layer in target.layers:
        if layer.dxf.get("lineweight", -3) < MIN_LINEWEIGHT:
            layer.dxf.lineweight = 25
    if not len(target.modelspace()):
        report.failed.append(f"logo block {name!r} holds nothing drawable")
        return None
    path = out / "logo.dxf"
    save_deterministic(target, path)
    report.found.append(f"logo: block {name} ({len(entities)} entities)" + ("" if a.logo_block else " (matched by name)"))
    return path


def _numbering(a: OnboardingAnswers, report: OnboardingReport) -> dict[str, Any]:
    n = a.numbering
    out: dict[str, Any] = {"numbering": Numbering(per_discipline=n.per_discipline, start=n.start)} if (n.per_discipline or n.start) else {}
    if n.pattern:
        out["drawing_number_pattern"] = n.pattern
        report.found.append(f"drawing number pattern: {n.pattern}")
    else:
        report.defaulted.append("drawing number pattern: {company}-{client}-{project}-{discipline}-{seq:03d}")
    if n.disciplines:
        out["discipline_codes"] = n.disciplines
        report.found.append("discipline codes: " + ", ".join(f"{k}={v}" for k, v in n.disciplines.items()))
    else:
        report.defaulted.append("discipline codes: none, the discipline name is used in the number")
    if n.per_discipline:
        report.found.append("sequences: one per discipline" + (f", starting {n.start}" if n.start else ""))
    else:
        report.defaulted.append("sequences: one counter for the whole project")
    return out


def _symbols(dxf: Path, a: OnboardingAnswers, report: OnboardingReport) -> dict[str, SymbolOverride]:
    out: dict[str, SymbolOverride] = {}
    for key, s in sorted(a.symbols.items()):
        ov = SymbolOverride(dxf=(s.dxf.expanduser().resolve() if s.dxf else dxf), block=s.block, pins=s.pins)
        try:
            prepared = check_override(key, ov)
        except (SymbolOverrideError, OSError, ezdxf.DXFError) as exc:
            report.failed.append(f"symbol {key}: {exc}")
            continue
        out[key] = ov
        report.found.append(f"symbol {key}: block {s.block} ({s.pins} pins) scaled {prepared.scale:.3f}, pins within {prepared.residual:.2f} mm")
        report.warnings += prepared.warnings
    if not a.symbols:
        report.defaulted.append("symbols: UniCAD's own symbols")
    return out


def _thresholds(a: OnboardingAnswers, report: OnboardingReport) -> dict[str, ThresholdSetting]:
    out: dict[str, ThresholdSetting] = {}
    for name, t in sorted(a.thresholds.items()):
        try:
            validate({name: Setting(t.value, t.source)})
        except ValueError as exc:
            report.failed.append(f"threshold {name}: {exc}")
            continue
        out[name] = t
        report.found.append(f"threshold {name} = {t.value:g} (default {BY_NAME[name].default_value:g})")
    left = sorted(set(BY_NAME) - set(out))
    if left:
        report.defaulted.append("thresholds: UniCAD defaults for " + ", ".join(left))
    return out


def onboard(dxf_path: Path, answers: OnboardingAnswers, workspace: Path, *, accept_partial: bool = False) -> OnboardingReport:
    """Build and save `answers.id` in the tenant's profile directory.

    Pieces that fail validation are left out and listed under `failed`; the profile is only saved when nothing failed, or with `accept_partial`."""
    report = OnboardingReport(answers.id)
    dxf = Path(dxf_path).expanduser().resolve()
    try:
        doc = ezdxf.readfile(dxf)
    except (OSError, ezdxf.DXFError) as exc:
        report.failed.append(f"cannot read {dxf}: {exc}")
        return report
    report.found.append(f"{dxf.name}: {len([layer for layer in doc.layers])} layers, {len([b for b in doc.blocks if not b.name.startswith('*')])} blocks")
    assets = profiles_dir(workspace) / answers.id
    assets.mkdir(parents=True, exist_ok=True)
    company_dxf = assets / "company.dxf"
    if dxf != company_dxf.resolve():
        shutil.copyfile(dxf, company_dxf)
    company_dxf = company_dxf.resolve()

    fields: dict[str, Any] = {"name": answers.name, "short_code": answers.short_code, "address": answers.address}
    report.found.append(f"company: {answers.name} ({answers.short_code})")
    for key in ("confidentiality_note", "date_format", "default_paper", "text_style", "text_font", "roles", "revision_marks"):
        value = getattr(answers, key)
        if value is not None:
            fields[key] = value
            report.found.append(f"{key}: from the answers")
        else:
            report.defaulted.append(f"{key}: UniCAD default")
    layers = _roles(doc, answers, report)
    if layers:
        fields["layers"] = layers
    layer_map = _layer_map(doc, answers, report)
    if layer_map:
        fields["layer_map"] = layer_map
    fields |= _numbering(answers, report)
    templates = _title_blocks(doc, company_dxf, answers, report)
    if templates:
        fields["title_block"] = TitleBlockChoice(kind="imported", templates=templates)
    else:
        report.defaulted.append("title block: UniCAD's built-in title block")
    if (logo := _logo(doc, answers, assets, report)) is not None:
        fields["logo"] = logo.resolve()
    if symbols := _symbols(company_dxf, answers, report):
        fields["symbol_overrides"] = symbols
    if thresholds := _thresholds(answers, report):
        fields["thresholds"] = thresholds
    try:
        profile = CompanyProfile(version=2, **fields)
    except ValueError as exc:
        report.failed.append(f"profile does not validate: {exc}")
        return report
    report.profile = profile
    if report.ok or accept_partial:
        report.path = save_profile(profile, answers.id, workspace)
    return report

"""A company profile described for people and agents: the effective thresholds, numbering, title blocks and symbols."""

from pathlib import Path
from typing import Any

from unicad.drafting.sheets.model import CompanyProfile
from unicad.rules.thresholds import ALL


def thresholds_view(profile: CompanyProfile) -> list[dict[str, Any]]:
    out = []
    for t in ALL:
        mine = profile.thresholds.get(t.name)
        out.append(
            {
                "name": t.name,
                "value": t.default_value if mine is None else mine.value,
                "unit": t.unit,
                "origin": "default" if mine is None else "company",
                "default": t.default_value,
                "source": t.default_assumption if mine is None else mine.source,
            }
        )
    return out


def _example_number(profile: CompanyProfile) -> str:
    return profile.drawing_number(client="CLIENT", project="PROJECT", discipline="electrical", seq=profile.numbering.start.get("electrical", 1) if profile.numbering.per_discipline else 1)


def describe(profile: CompanyProfile, profile_id: str, origin: str, path: Path) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "origin": origin,
        "path": str(path),
        "version": profile.version,
        "name": profile.name,
        "short_code": profile.short_code,
        "address": profile.address,
        "default_paper": profile.default_paper.value,
        "logo": None if profile.logo is None else str(profile.logo),
        "numbering": {
            "pattern": profile.drawing_number_pattern,
            "example": _example_number(profile),
            "discipline_codes": dict(profile.discipline_codes),
            "per_discipline": profile.numbering.per_discipline,
            "start": dict(profile.numbering.start),
        },
        "title_block": {
            "kind": profile.title_block.kind,
            "papers": {p.value: {"dxf": str(t.dxf), "block": t.block, "layer": t.layer} for p, t in sorted(profile.title_block.templates.items())},
        },
        "sheet_layers": {r.value: s.name for r, s in profile.layers.items()},
        "layer_map": {src: s.name for src, s in sorted(profile.layer_map.items())},
        "symbol_overrides": {k: {"dxf": str(o.dxf), "block": o.block, "pins": o.pins} for k, o in sorted(profile.symbol_overrides.items())},
        "thresholds": thresholds_view(profile),
        "signatory_labels": profile.roles.model_dump(),
        "date_format": profile.date_format,
        "revision_marks": profile.revision_marks.model_dump(),
    }

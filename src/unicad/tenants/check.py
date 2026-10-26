"""Whether a profile's files exist and its imported pieces are usable."""

import ezdxf

from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sheets.symbol_override import SymbolOverrideError, check_override
from unicad.drafting.sheets.template import parse_template

MAX_HEIGHT_SHARE = 0.6


def check_template(paper: PaperSize, ref) -> tuple[list[str], list[str]]:
    """(problems, facts) for one imported title block."""
    try:
        t = parse_template(ref)
    except (ValueError, OSError, ezdxf.DXFError) as exc:
        return [f"title block for {paper.value}: {exc}"], []
    problems = [f"title block {ref.block or ref.dxf.name} for {paper.value}: {p}" for p in t.problems]
    column = metrics_for(paper).column
    scale = column.width / t.extents.width
    height = t.extents.height * scale
    if height > MAX_HEIGHT_SHARE * column.height:
        problems.append(f"title block {ref.block or ref.dxf.name} for {paper.value} would be {height:.0f} mm tall at the {column.width:.0f} mm column width, over {MAX_HEIGHT_SHARE:.0%} of the {column.height:.0f} mm column")
    facts = [f"title block {ref.block or ref.dxf.name} for {paper.value}: {len(t.fields)} placeholders, {t.extents.width:g} x {t.extents.height:g} mm drawn {scale:.2f}x on the sheet"]
    facts += [f"title block {ref.block or ref.dxf.name} ignored {s}" for s in t.skipped]
    return problems, facts


def check_profile(profile: CompanyProfile) -> tuple[list[str], list[str]]:
    """(problems, facts) found by reading every file the profile points to."""
    problems: list[str] = []
    facts: list[str] = []
    if profile.logo is not None and not profile.logo.is_file():
        problems.append(f"logo file {profile.logo} does not exist")
    for paper, ref in sorted(profile.title_block.templates.items()):
        if not ref.dxf.is_file():
            problems.append(f"title block file {ref.dxf} for {paper.value} does not exist")
            continue
        p, f = check_template(paper, ref)
        problems += p
        facts += f
    for key, ov in sorted(profile.symbol_overrides.items()):
        if not ov.dxf.is_file():
            problems.append(f"symbol file {ov.dxf} for {key} does not exist")
            continue
        try:
            prepared = check_override(key, ov)
        except (SymbolOverrideError, OSError, ezdxf.DXFError) as exc:
            problems.append(f"symbol {key}: {exc}")
        else:
            facts.append(f"symbol {key}: block {ov.block} scaled {prepared.scale:.3f}, pins within {prepared.residual:.2f} mm")
    return problems, facts

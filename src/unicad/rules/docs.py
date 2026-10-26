"""Render docs/rules.md from the registry: `uv run python -m unicad.rules.docs > docs/rules.md`."""

from pathlib import Path

from unicad.rules import REGISTRY, families
from unicad.rules.registry import ALL_SEGMENTS
from unicad.rules.thresholds import ALL as THRESHOLDS

DOCS_PATH = Path(__file__).resolve().parents[3] / "docs" / "rules.md"


def render_docs() -> str:
    lines = [
        "# Engineering rules",
        "",
        "Generated from the rule registry by `uv run python -m unicad.rules.docs > docs/rules.md`. Do not edit.",
        "",
        "Every rule is a pure function of the project and the catalog that yields findings (`error`, `warning`, `info`).",
        "A sheet cannot be issued for approval, construction or as-built while any `error` finding exists;",
        "proposal and information issues are allowed with a NOT FOR CONSTRUCTION watermark.",
        "Clause references name the standard only; clause numbers are added once verified against the standard text.",
        "",
        "## Thresholds",
        "",
        "| Name | Value | Basis |",
        "|---|---|---|",
    ]
    for t in THRESHOLDS:
        lines.append(f"| `{t.name}` | {t.value:g} {t.unit} | {t.assumption} |".replace("  |", " |"))
    for fam in families():
        lines += ["", f"## {fam}", ""]
        for r in REGISTRY.values():
            if r.family != fam:
                continue
            segs = "all" if r.applies_to == ALL_SEGMENTS else ", ".join(sorted(s.value for s in r.applies_to))
            lines += [f"### `{r.id}`: {r.title}", "", f"Standard: {r.clause or 'none (design practice)'}. Segments: {segs}.", "", r.docs, ""]
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    print(render_docs(), end="")

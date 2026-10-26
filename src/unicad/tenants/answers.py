"""The short questionnaire that goes with a company DXF."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from unicad.drafting.sheets.model import LayerRole, PaperSize, RevisionMarks, SignatoryRoles, ThresholdSetting
from unicad.tenants.store import check_id


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TitleSource(_Strict):
    block: str | None = None
    layer: str | None = Field(default=None, description="Modelspace layer holding the title block, when it is not a block")


class SymbolAnswer(_Strict):
    block: str
    pins: Literal["named", "fit"] = "named"
    dxf: Path | None = Field(default=None, description="Another DXF holding the block; default the company DXF")


class NumberingAnswer(_Strict):
    pattern: str | None = None
    disciplines: dict[str, str] = Field(default_factory=dict, description="Discipline name -> the code in the number, e.g. electrical: EE")
    per_discipline: bool = False
    start: dict[str, int] = Field(default_factory=dict)


class OnboardingAnswers(_Strict):
    id: str = Field(description="Profile id (file name): a-z, 0-9, '-' and '_'")
    name: str = Field(min_length=1)
    short_code: str = Field(pattern=r"^[A-Z0-9]{2,10}$")
    address: str = ""
    confidentiality_note: str | None = None
    date_format: str | None = None
    default_paper: PaperSize | None = None
    text_style: str | None = None
    text_font: str | None = None
    roles: SignatoryRoles | None = None
    numbering: NumberingAnswer = NumberingAnswer()
    logo_block: str | None = Field(default=None, description="Block holding the logo; default the block whose name contains LOGO")
    title_blocks: dict[PaperSize, TitleSource | str] = Field(default_factory=dict, description="Title block per paper; default every block with title block placeholders and a paper size in its name")
    layers: dict[LayerRole, str] = Field(default_factory=dict, description="Company layer for the sheet frame, title block and text")
    layer_map: dict[str, str] = Field(default_factory=dict, description="UniCAD layer name -> company layer")
    symbols: dict[str, SymbolAnswer] = Field(default_factory=dict, description="UniCAD symbol key -> company block")
    thresholds: dict[str, ThresholdSetting] = Field(default_factory=dict)
    revision_marks: RevisionMarks | None = None

    @field_validator("id")
    @classmethod
    def _legal_id(cls, v: str) -> str:
        return check_id(v)


def parse_answers(source: Path | str | dict[str, Any]) -> OnboardingAnswers:
    """Answers from a mapping, a YAML or JSON file, or YAML or JSON text."""
    if isinstance(source, dict):
        return OnboardingAnswers.model_validate(source)
    text = Path(source).expanduser().read_text(encoding="utf-8") if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and Path(source).expanduser().is_file()) else str(source)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("answers must be a mapping of question to answer (YAML or JSON); a single line is taken as a file path and there is no such file")
    return OnboardingAnswers.model_validate(data)

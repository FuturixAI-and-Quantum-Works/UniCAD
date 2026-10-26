import json
import string
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ILLEGAL_LAYER_CHARS = set('<>/\\":;?*|=`')


class PaperSize(StrEnum):
    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"

    @property
    def size_mm(self) -> tuple[float, float]:
        return _LANDSCAPE_MM[self]

    @property
    def width(self) -> float:
        return self.size_mm[0]

    @property
    def height(self) -> float:
        return self.size_mm[1]


_LANDSCAPE_MM: dict[PaperSize, tuple[float, float]] = {
    PaperSize.A0: (1189.0, 841.0),
    PaperSize.A1: (841.0, 594.0),
    PaperSize.A2: (594.0, 420.0),
    PaperSize.A3: (420.0, 297.0),
    PaperSize.A4: (297.0, 210.0),
}


class IssuedFor(StrEnum):
    PROPOSAL = "Proposal"
    INFORMATION = "Information"
    APPROVAL = "Approval"
    CONSTRUCTION = "Construction"
    AS_BUILT = "As built"


class LayerRole(StrEnum):
    FRAME = "frame"
    TITLE = "title"
    TEXT = "text"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LayerSpec(_Frozen):
    name: str = Field(min_length=1, max_length=255)
    colour: int = Field(default=7, ge=1, le=255, description="ACI colour index")
    linetype: str = "CONTINUOUS"
    lineweight: int = Field(default=25, description="hundredths of a mm, as in DXF group 370")

    @field_validator("name")
    @classmethod
    def _legal_name(cls, v: str) -> str:
        bad = _ILLEGAL_LAYER_CHARS & set(v)
        if bad:
            raise ValueError(f"layer name contains characters DXF forbids: {''.join(sorted(bad))}")
        return v

    @field_validator("lineweight")
    @classmethod
    def _dxf_lineweight(cls, v: int) -> int:
        valid = {-3, -2, -1, 0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100,
                 106, 120, 140, 158, 200, 211}
        if v not in valid:
            raise ValueError(f"{v} is not a DXF lineweight")
        return v


def default_layers() -> dict[LayerRole, LayerSpec]:
    return {
        LayerRole.FRAME: LayerSpec(name="G-FRAME", lineweight=50),
        LayerRole.TITLE: LayerSpec(name="G-TITLE", lineweight=25),
        LayerRole.TEXT: LayerSpec(name="G-TEXT", lineweight=25),
    }


class SignatoryRoles(_Frozen):
    drawn: str = "DRAWN"
    checked: str = "CHECKED"
    approved: str = "APPROVED"


DRAWING_NUMBER_FIELDS = frozenset({"company", "client", "project", "discipline", "seq"})


class CompanyProfile(_Frozen):
    name: str = Field(min_length=1)
    short_code: str = Field(pattern=r"^[A-Z0-9]{2,10}$")
    logo: Path | None = Field(default=None, description="DXF imported as a block into the title block")
    address: str = ""
    confidentiality_note: str | None = None
    drawing_number_pattern: str = "{company}-{client}-{project}-{discipline}-{seq:03d}"
    discipline_codes: dict[str, str] = Field(default_factory=dict)
    default_paper: PaperSize = PaperSize.A3
    text_style: str = Field(default="standard", min_length=1)
    text_font: str = Field(default="standard", min_length=1)
    date_format: str = "%d.%m.%y"
    layers: dict[LayerRole, LayerSpec] = Field(default_factory=default_layers)
    roles: SignatoryRoles = SignatoryRoles()

    @field_validator("layers", mode="before")
    @classmethod
    def _fill_default_layers(cls, v: object) -> object:
        if isinstance(v, dict):
            merged: dict[object, object] = dict(default_layers())
            merged.update({LayerRole(k): val for k, val in v.items()})
            return merged
        return v

    @field_validator("drawing_number_pattern")
    @classmethod
    def _pattern_is_formattable(cls, v: str) -> str:
        names = {name for _, name, _, _ in string.Formatter().parse(v) if name is not None}
        unknown = names - DRAWING_NUMBER_FIELDS
        if unknown:
            raise ValueError(
                f"unknown fields {sorted(unknown)}; allowed: {sorted(DRAWING_NUMBER_FIELDS)}"
            )
        if "seq" not in names:
            raise ValueError("pattern must contain {seq} or two sheets would share a number")
        try:
            v.format(company="X", client="X", project="X", discipline="X", seq=1)
        except (ValueError, IndexError, KeyError, TypeError) as e:
            raise ValueError(f"pattern cannot be formatted: {e}") from e
        return v

    @model_validator(mode="after")
    def _discipline_codes_nonempty(self) -> Self:
        empty = [k for k, c in self.discipline_codes.items() if not c]
        if empty:
            raise ValueError(f"empty discipline code for {empty}")
        return self

    def layer(self, role: LayerRole) -> LayerSpec:
        return self.layers[role]

    def drawing_number(self, *, client: str, project: str, discipline: str, seq: int) -> str:
        return self.drawing_number_pattern.format(
            company=self.short_code,
            client=client,
            project=project,
            discipline=self.discipline_codes.get(discipline, discipline),
            seq=seq,
        )

    @classmethod
    def load(cls, path: Path) -> Self:
        profile = cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if profile.logo is not None and not profile.logo.is_absolute():
            profile = profile.model_copy(update={"logo": (path.parent / profile.logo).resolve()})
        return profile


Text = Annotated[str, Field(min_length=1)]


class TitleBlockData(_Frozen):
    client: Text
    consultant: str | None = None
    project_title: Text
    location: Text
    loa_no: str | None = Field(default=None, description="LOA / PO number and date")
    drawing_title: Text
    drawing_number: Text
    revision: Text = "00"
    scale: Text = "NTS"
    sheet_no: int = Field(default=1, ge=1)
    sheet_count: int = Field(default=1, ge=1)
    paper: PaperSize
    drawn_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    drawn_date: date | None = None
    checked_date: date | None = None
    approved_date: date | None = None
    issued_for: IssuedFor

    @model_validator(mode="after")
    def _sheet_in_range(self) -> Self:
        if self.sheet_no > self.sheet_count:
            raise ValueError(f"sheet {self.sheet_no} of {self.sheet_count}")
        return self


class Revision(_Frozen):
    rev: Text
    date: date
    description: Text
    by: str = ""
    checked: str = ""
    approved: str = ""


class SheetSpec(_Frozen):
    paper: PaperSize
    title_block: TitleBlockData
    revisions: tuple[Revision, ...] = ()
    legend: tuple[tuple[str, str], ...] = Field(
        default=(), description="(symbol key, description) in display order"
    )
    notes: tuple[str, ...] = ()
    parameters: tuple[tuple[str, str], ...] = Field(
        default=(), description="(label, value) rows of the system parameters table"
    )

    @model_validator(mode="after")
    def _paper_agrees(self) -> Self:
        if self.title_block.paper != self.paper:
            raise ValueError(
                f"title block says {self.title_block.paper} but sheet paper is {self.paper}"
            )
        return self

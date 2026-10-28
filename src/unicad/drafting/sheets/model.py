import json
import re
import string
from collections.abc import Iterable, Mapping
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from unicad.rules.thresholds import Setting

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


EPC_NOT_SET = "EPC COMPANY: NOT SET"
EPC_NOT_SET_HINT = "NO EPC COMPANY PROFILE IS ASSIGNED TO THIS PROJECT: ONBOARD THE EPC COMPANY AND ASSIGN ITS PROFILE BEFORE ISSUE."
DRAWING_NUMBER_FIELDS = frozenset({"company", "client", "project", "discipline", "seq"})
PROFILE_VERSION = 2
_V2_FIELDS = ("thresholds", "symbol_overrides", "layer_map", "title_block", "numbering", "revision_marks")


class ThresholdSetting(_Frozen):
    value: float
    source: str = Field(min_length=1, description="Why the company uses this value; findings quote it instead of the generic assumption")


class SymbolOverride(_Frozen):
    dxf: Path = Field(description="Company symbol DXF")
    block: str = Field(min_length=1, description="Block in that DXF that replaces the UniCAD symbol")
    pins: Literal["named", "fit"] = Field(
        default="named",
        description="named: the block holds POINTs on layers PIN_<pin name>; the block is scaled and moved so they land on the "
        "UniCAD pins. fit: the block's extents are fitted to the UniCAD symbol's box and every UniCAD pin must then touch its geometry",
    )


class TemplateRef(_Frozen):
    dxf: Path
    block: str | None = Field(default=None, description="Block holding the title block; omit to use modelspace")
    layer: str | None = Field(default=None, description="With modelspace: only entities on this layer")


class TitleBlockChoice(_Frozen):
    kind: Literal["builtin", "imported"] = "builtin"
    templates: dict[PaperSize, TemplateRef] = Field(default_factory=dict, description="Imported title block per paper size")

    @model_validator(mode="after")
    def _imported_has_templates(self) -> Self:
        if self.kind == "imported" and not self.templates:
            raise ValueError("kind 'imported' needs at least one paper size in templates")
        return self


class Numbering(_Frozen):
    per_discipline: bool = Field(default=False, description="Each discipline counts its own sequence instead of one counter for the project")
    start: dict[str, int] = Field(default_factory=dict, description="First sequence number per discipline name (default 1)")

    @field_validator("start")
    @classmethod
    def _positive(cls, v: dict[str, int]) -> dict[str, int]:
        bad = [k for k, n in v.items() if n < 1]
        if bad:
            raise ValueError(f"start numbers must be at least 1: {bad}")
        return v


class RevisionMarks(_Frozen):
    style: Literal["triangle", "cloud"] = "triangle"
    stamp_text: str | None = Field(default=None, description="Stamp the company puts on revised sheets, e.g. 'REVISED'")


def pattern_regex(pattern: str) -> re.Pattern[str]:
    out = []
    for literal, name, _, _ in string.Formatter().parse(pattern):
        out.append(re.escape(literal))
        if name is not None:
            out.append(r"(?P<seq>\d+)" if name == "seq" else rf"(?P<{name}>.+?)")
    return re.compile("".join(out) + "$")


class CompanyProfile(_Frozen):
    version: int = Field(default=1, ge=1, le=PROFILE_VERSION, description="Profile format; 1 files have none of the version 2 blocks")
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
    thresholds: dict[str, ThresholdSetting] = Field(default_factory=dict, description="Rule thresholds by name (unicad.rules.thresholds)")
    symbol_overrides: dict[str, SymbolOverride] = Field(default_factory=dict, description="UniCAD symbol key -> company block")
    layer_map: dict[str, LayerSpec] = Field(default_factory=dict, description="UniCAD layer name -> the company layer that replaces it")
    title_block: TitleBlockChoice = TitleBlockChoice()
    numbering: Numbering = Numbering()
    revision_marks: RevisionMarks = RevisionMarks()

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

    @field_validator("thresholds")
    @classmethod
    def _known_thresholds(cls, v: dict[str, ThresholdSetting]) -> dict[str, ThresholdSetting]:
        from unicad.rules.thresholds import validate

        validate({k: _setting(t) for k, t in v.items()})
        return v

    @model_validator(mode="after")
    def _discipline_codes_nonempty(self) -> Self:
        empty = [k for k, c in self.discipline_codes.items() if not c]
        if empty:
            raise ValueError(f"empty discipline code for {empty}")
        return self

    @model_validator(mode="after")
    def _v1_has_no_v2_blocks(self) -> Self:
        if self.version < 2 and (used := [f for f in _V2_FIELDS if f in self.model_fields_set]):
            raise ValueError(f"{used} need profile version 2")
        return self

    def without_company(self) -> Self:
        """This profile with no company identity on the sheets: the title block says the EPC company is not set instead of naming the
        demo company. Numbering, layers and symbols are kept so drawing numbers stay stable once the real profile is assigned."""
        return self.model_copy(update={"name": EPC_NOT_SET, "address": EPC_NOT_SET_HINT, "logo": None, "confidentiality_note": None})

    @property
    def company_set(self) -> bool:
        return self.name != EPC_NOT_SET

    def threshold_settings(self) -> "dict[str, Setting]":
        return {k: _setting(t) for k, t in self.thresholds.items()}

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

    def next_number(self, issued: Iterable[str], *, client: str, project: str, discipline: str) -> str:
        """The next unused drawing number for `discipline`, after the numbers in `issued` that follow the pattern."""
        rx = pattern_regex(self.drawing_number_pattern)
        code = self.discipline_codes.get(discipline, discipline)
        seqs = [
            int(m.group("seq"))
            for n in issued
            if (m := rx.match(n)) and (not self.numbering.per_discipline or "discipline" not in m.groupdict() or m.group("discipline") == code)
        ]
        first = self.numbering.start.get(discipline, 1) if self.numbering.per_discipline else 1
        seq = max(seqs) + 1 if seqs else first
        return self.drawing_number(client=client, project=project, discipline=discipline, seq=max(seq, first))

    @model_validator(mode="before")
    @classmethod
    def _default_version(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and "version" not in data:
            return {**data, "version": 2 if any(f in data for f in _V2_FIELDS) else 1}
        return data

    def anchored(self, base: Path) -> Self:
        """The profile with every relative file path resolved against `base`."""

        def at(p: Path) -> Path:
            return p if p.is_absolute() else (base / p).resolve()

        return self.model_copy(
            update={
                "logo": None if self.logo is None else at(self.logo),
                "symbol_overrides": {k: o.model_copy(update={"dxf": at(o.dxf)}) for k, o in self.symbol_overrides.items()},
                "title_block": self.title_block.model_copy(update={"templates": {p: t.model_copy(update={"dxf": at(t.dxf)}) for p, t in self.title_block.templates.items()}}),
            }
        )

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8"))).anchored(path.parent)


def _setting(t: ThresholdSetting) -> "Setting":
    from unicad.rules.thresholds import Setting

    return Setting(t.value, t.source)


Text = Annotated[str, Field(min_length=1)]


class TitleBlockData(_Frozen):
    client: Text
    developer: str | None = Field(default=None, description="The project developer, when it is not the owner")
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
    assumptions: tuple[str, ...] = Field(default=(), description="Inputs the sheet is drawn from that are assumed, not cited; drawn as their own block")

    @model_validator(mode="after")
    def _paper_agrees(self) -> Self:
        if self.title_block.paper != self.paper:
            raise ValueError(
                f"title block says {self.title_block.paper} but sheet paper is {self.paper}"
            )
        return self

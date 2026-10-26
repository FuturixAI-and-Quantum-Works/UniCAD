"""The project model: the single source of truth every drawing renders from."""

from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from unicad.domain.layout import ArrayLayout
from unicad.domain.network import ElectricalNetwork, NetworkError, NodeKind
from unicad.domain.site import SiteGeometry

if TYPE_CHECKING:
    from unicad.catalog.store import Catalog

ProjectId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
Fraction = Annotated[float, Field(gt=0, lt=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Segment(StrEnum):
    RESIDENTIAL = "residential"
    CI_ROOFTOP = "ci_rooftop"
    CARPORT = "carport"
    UTILITY = "utility"
    BESS = "bess"
    HYBRID = "hybrid"


class Party(_Frozen):
    name: str
    code: str | None = None
    address: str | None = None


class Site(_Frozen):
    name: str
    address: str | None = None
    state: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    crs: str | None = None
    geometry: SiteGeometry | None = None


class PvsystThermal(_Frozen):
    """PVsyst constant/wind thermal loss factors, W/m²K and W/m²K/(m/s)."""

    model: Literal["pvsyst"]
    uc: float = Field(gt=0)
    uv: float = Field(ge=0)
    absorptance: float = Field(default=0.9, gt=0, le=1)


class NoctThermal(_Frozen):
    """NOCT model; `noct_c` None means take it from the module spec."""

    model: Literal["noct"]
    noct_c: float | None = None


CellTempModel = Annotated[PvsystThermal | NoctThermal, Field(discriminator="model")]


class GridConnection(_Frozen):
    voltage_v: float = Field(gt=0)
    frequency_hz: float = Field(gt=0)
    phases: Literal[1, 3] = 3
    utility: str | None = None


class DesignBasis(_Frozen):
    """Site and grid inputs to the rules engine. `assumptions` names every value not taken from a cited source."""

    ambient_min_c: float
    ambient_max_c: float
    cell_temp: CellTempModel
    altitude_m: float = Field(ge=0)
    grid: GridConnection
    vdrop_dc_max: Fraction
    vdrop_ac_max: Fraction
    dc_ac_ratio_target: float = Field(gt=0)
    power_factor: float = Field(gt=0, le=1)
    assumptions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ambient_order(self) -> Self:
        if self.ambient_min_c >= self.ambient_max_c:
            raise ValueError(f"ambient_min_c {self.ambient_min_c} must be below ambient_max_c {self.ambient_max_c}")
        return self


class IssuedFor(StrEnum):
    PROPOSAL = "proposal"
    INFORMATION = "information"
    APPROVAL = "approval"
    CONSTRUCTION = "construction"
    AS_BUILT = "as_built"


class IssueRevision(_Frozen):
    rev: str
    date: date
    remarks: str
    issued_for: IssuedFor | None = None
    drawn: str | None = None
    checked: str | None = None
    approved: str | None = None


class SheetKind(StrEnum):
    SLD = "sld"
    SLD_DETAIL = "sld_detail"
    STRING_DIAGRAM = "string_diagram"
    LAYOUT = "layout"
    CABLE_SCHEDULE = "cable_schedule"
    EARTHING = "earthing"
    LPS = "lps"
    BOM = "bom"
    REGISTER = "register"


Paper = Literal["A0", "A1", "A2", "A3", "A4"]


class Region(_Frozen):
    """Axis-aligned window in site metres."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not (self.x_min < self.x_max and self.y_min < self.y_max):
            raise ValueError("region needs x_min < x_max and y_min < y_max")
        return self


class SheetScope(_Frozen):
    """What a sheet shows: network nodes or groups (with what feeds them), a site region, or the whole project when empty."""

    nodes: tuple[str, ...] = ()
    region: Region | None = None


class Sheet(_Frozen):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
    number: str
    kind: SheetKind
    title: str
    paper: Paper
    scale: str = "NTS"
    scope: SheetScope = SheetScope()
    revisions: tuple[IssueRevision, ...] = ()


class Project(_Frozen):
    id: ProjectId
    name: str
    client: Party
    site: Site
    design_basis: DesignBasis
    company_profile: str
    segment: Segment
    network: ElectricalNetwork = ElectricalNetwork()
    array_layout: ArrayLayout | None = None
    sheets: tuple[Sheet, ...] = ()
    revisions: tuple[IssueRevision, ...] = ()

    @model_validator(mode="after")
    def _cross_refs(self, info: ValidationInfo) -> Self:
        problems = []
        for what in ("id", "number"):
            values = [getattr(s, what) for s in self.sheets]
            problems += [f"duplicate sheet {what} {v!r}" for v in sorted({v for v in values if values.count(v) > 1})]
        net = self.network
        for s in self.sheets:
            for ref in s.scope.nodes:
                if ref not in net.node_by_id and ref not in net.group_by_id:
                    problems.append(f"sheet {s.id!r} scope references unknown node or group {ref!r}")
        if self.array_layout is not None:
            problems += self._layout_problems(self.array_layout)
        catalog = (info.context or {}).get("catalog") if info is not None else None
        if not problems and catalog is not None:
            problems = self.catalog_problems(catalog, network=False)
        if problems:
            raise ValueError("invalid project: " + "; ".join(problems))
        return self

    def _layout_problems(self, layout: ArrayLayout) -> list[str]:
        problems = []
        net = self.network
        instances: dict[str, set[str]] = {}
        for a in layout.strings:
            node = net.node_by_id.get(a.string.split("@", 1)[0])
            if node is None or node.kind is not NodeKind.PV_STRING:
                problems.append(f"layout string {a.string!r} is not a pv_string in the network")
            elif "@" in a.string and a.string not in instances.setdefault(node.id, set(net.instance_ids(node.id))):
                problems.append(f"layout string {a.string!r} is not an instance of {node.id!r}")
        return problems

    def catalog_problems(self, catalog: "Catalog", network: bool = True) -> list[str]:
        problems = self.network.catalog_problems(catalog) if network else []
        if self.array_layout is not None:
            for t in self.array_layout.table_by_id.values():
                try:
                    kind = catalog.get(t.module).kind
                except KeyError:
                    problems.append(f"table {t.id!r} references unknown catalog id {t.module!r}")
                    continue
                if kind != "module":
                    problems.append(f"table {t.id!r} module {t.module!r} is a {kind}, not a module")
        return problems

    def check_catalog(self, catalog: "Catalog") -> None:
        if problems := self.catalog_problems(catalog):
            raise NetworkError("catalog references: " + "; ".join(problems))

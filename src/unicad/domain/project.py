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


LightningProtectionLevel = Literal["I", "II", "III", "IV"]


class EarthingBasis(_Frozen):
    """Earthing design inputs. `fault_current_ka` is the earth fault current the conductors and electrodes carry for `fault_clearing_s`.

    Electrode defaults (40 mm x 3 m pipe) follow the CIL/Refex sample drawing and are reported as assumptions by the design.
    """

    soil_resistivity_ohm_m: float = Field(gt=0)
    fault_current_ka: float | None = Field(default=None, gt=0)
    fault_clearing_s: float | None = Field(default=None, gt=0)
    target_resistance_ohm: float | None = Field(default=None, gt=0)
    electrode_length_m: float = Field(default=3.0, gt=0)
    electrode_diameter_mm: float = Field(default=40.0, gt=0)
    burial_depth_m: float = Field(default=0.6, gt=0)
    corrosion_allowance: float = Field(default=0.0, ge=0, lt=5)
    tables_per_pit: int = Field(default=20, gt=0)
    surface_resistivity_ohm_m: float | None = Field(default=None, gt=0)
    surface_layer_m: float | None = Field(default=None, gt=0)
    switchyard_m: tuple[float, float] | None = None


class LpsBasis(_Frozen):
    """Lightning protection inputs. `ese_radius_m` comes from the user or the terminal datasheet; else it is computed by NF C 17-102
    from `ese_delta_t_us` and `mast_height_m`. Conventional air terminals use the rolling sphere of `level`."""

    level: LightningProtectionLevel
    method: Literal["ese", "rod"] = "ese"
    ese_radius_m: float | None = Field(default=None, gt=0)
    ese_delta_t_us: float | None = Field(default=None, gt=0, le=60)
    mast_height_m: float | None = Field(default=None, gt=0)
    protected_height_m: float | None = Field(default=None, ge=0)
    placement: Literal["auto", "array", "perimeter"] = Field(
        default="auto", description="ground plants: 'perimeter' keeps every terminal outside the array (boundary strip, roads) so no mast shades the tables; "
        "'array' lets terminals stand in row gaps (fewest masts); 'auto' takes the perimeter when it covers every target, else the array"
    )


class FaultLevel(_Frozen):
    """Prospective three-phase symmetrical short-circuit current at the busbars of one system voltage, from the fault study or the utility.

    `clearing_s` is the time the protection takes to clear it; when omitted the rules use `earthing.fault_clearing_s` if the project has
    one, else 1 s and say so."""

    voltage_v: float = Field(gt=0, description="line-to-line system voltage the level applies to, e.g. 415, 800 or 33000")
    fault_ka: float = Field(gt=0, description="prospective symmetrical short-circuit current, kA rms")
    clearing_s: float | None = Field(default=None, gt=0)
    source: str | None = Field(default=None, description="where the level comes from, e.g. 'DISCOM feasibility letter' or 'fault study rev B'")


class BessRating(_Frozen):
    """The storage the network was built with and the size the brief asked for (set by network_autobuild), so drawings and schedules can state both."""

    built_kva: float = Field(gt=0, description="PCS kVA built: whole PCS units")
    built_kwh: float = Field(gt=0, description="battery energy built, kWh: whole battery units")
    brief_kw: float | None = Field(default=None, gt=0, description="storage power the brief asked for, kW")
    brief_kwh: float | None = Field(default=None, gt=0, description="storage energy the brief asked for, kWh")

    @property
    def kw_deviation(self) -> float | None:
        return self.built_kva / self.brief_kw - 1 if self.brief_kw else None

    @property
    def kwh_deviation(self) -> float | None:
        return self.built_kwh / self.brief_kwh - 1 if self.brief_kwh else None

    def text(self) -> str:
        """e.g. '10.08 MVA / 20.06 MWh built against the brief's 9 MW / 18 MWh (+12.0 % / +11.4 %)'."""
        built = f"{self.built_kva / 1000:g} MVA / {self.built_kwh / 1000:g} MWh built"
        if self.brief_kw is None and self.brief_kwh is None:
            return built
        brief = " / ".join(x for x in (f"{self.brief_kw / 1000:g} MW" if self.brief_kw else "", f"{self.brief_kwh / 1000:g} MWh" if self.brief_kwh else "") if x)
        dev = " / ".join(f"{d:+.1%}".replace("%", " %") for d in (self.kw_deviation, self.kwh_deviation) if d is not None)
        return f"{built} against the brief's {brief} ({dev})"


DesignInput = Literal[
    "ambient_min_c", "ambient_max_c", "altitude_m", "vdrop_dc_max", "vdrop_ac_max", "dc_ac_ratio_target", "power_factor", "grid.voltage_v",
    "earthing.soil_resistivity_ohm_m", "earthing.fault_current_ka", "earthing.fault_clearing_s", "lps.level", "lps.ese_delta_t_us", "lps.mast_height_m",
]  # fmt: skip
"""Design-basis inputs that can be marked as assumed."""


class AssumedInput(_Frozen):
    """One input marked as assumed, as a drawing prints it: `field` is the model path (design_basis.ambient_max_c, array_layout.tilt_deg)."""

    field: str
    label: str
    value: str


class DesignBasis(_Frozen):
    """Site and grid inputs to the rules engine. `assumptions` names every value not taken from a cited source; `assumed` lists the inputs the user or
    agent set themselves and marks as assumed (e.g. an ambient read off a map rather than the client's data)."""

    ambient_min_c: float
    ambient_max_c: float
    cell_temp: CellTempModel
    altitude_m: float = Field(ge=0)
    grid: GridConnection
    vdrop_dc_max: Fraction
    vdrop_ac_max: Fraction
    dc_ac_ratio_target: float = Field(gt=0)
    power_factor: float = Field(gt=0, le=1)
    earthing: EarthingBasis | None = None
    lps: LpsBasis | None = None
    fault_levels: tuple[FaultLevel, ...] = ()
    bess: BessRating | None = Field(default=None, description="storage built and asked for; set by network_autobuild")
    assumptions: tuple[str, ...] = ()
    assumed: tuple[DesignInput, ...] = Field(default=(), description="inputs marked as assumed (project_design_basis assumed=[...]); drawings print them as assumed")

    @model_validator(mode="after")
    def _ambient_order(self) -> Self:
        if self.ambient_min_c >= self.ambient_max_c:
            raise ValueError(f"ambient_min_c {self.ambient_min_c} must be below ambient_max_c {self.ambient_max_c}")
        return self

    @model_validator(mode="after")
    def _assumed_set(self) -> Self:
        missing = [k for k in self.assumed if k.split(".")[0] in ("earthing", "lps") and not getattr(self, k.split(".")[0])]
        if missing:
            raise ValueError(f"assumed lists {missing}, which the design basis does not hold yet: set them first")
        return self

    @model_validator(mode="after")
    def _one_level_per_voltage(self) -> Self:
        volts = [f.voltage_v for f in self.fault_levels]
        if len(set(volts)) != len(volts):
            raise ValueError("fault_levels lists a system voltage twice")
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
    model_revision: int | None = Field(default=None, description="project revision whose content this issue is; set by project_issue")
    content_sha: str | None = Field(default=None, description="fingerprint of the issued content (unicad.store.issue.content_sha); set by project_issue")


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
    developer: Party | None = Field(default=None, description="project developer, when it is not the client (e.g. the RESCO or IPP building the plant for the client)")
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

    def assumed_inputs(self) -> tuple[AssumedInput, ...]:
        """Every input marked as assumed (design_basis.assumed, array_layout.assumed) with its current value, for the drawings' ASSUMED blocks."""
        db, out = self.design_basis, []
        for k in db.assumed:
            out.append(AssumedInput(field=f"design_basis.{k}", label=DESIGN_INPUT_LABELS[k], value=_basis_value(db, k)))
        lay = self.array_layout
        if lay is not None and lay.assumed:
            t = next(iter(lay.table_by_id.values()), None)
            for k in lay.assumed:
                out.append(AssumedInput(field=f"array_layout.{k}", label=LAYOUT_INPUT_LABELS[k], value=_layout_value(lay, t, k)))
        return tuple(out)

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


DESIGN_INPUT_LABELS = {
    "ambient_min_c": "minimum ambient temperature", "ambient_max_c": "maximum ambient temperature", "altitude_m": "site altitude",
    "vdrop_dc_max": "DC voltage drop limit", "vdrop_ac_max": "AC voltage drop limit", "dc_ac_ratio_target": "DC/AC ratio target", "power_factor": "design power factor",
    "grid.voltage_v": "grid connection voltage", "earthing.soil_resistivity_ohm_m": "soil resistivity",
    "earthing.fault_current_ka": "earth fault current", "earthing.fault_clearing_s": "earth fault clearing time", "lps.level": "lightning protection level",
    "lps.ese_delta_t_us": "ESE early streamer advance", "lps.mast_height_m": "lightning mast height",
}  # fmt: skip
LAYOUT_INPUT_LABELS = {
    "tilt_deg": "module tilt", "azimuth_deg": "module azimuth", "mounting": "mounting", "orientation": "module orientation", "rows": "modules across a table",
    "cols": "modules along a table",
}  # fmt: skip


def _basis_value(db: DesignBasis, k: str) -> str:
    head, _, tail = k.partition(".")
    v = getattr(getattr(db, head), tail) if tail else getattr(db, head)
    if v is None:
        return "not set"
    unit = {"ambient_min_c": " degC", "ambient_max_c": " degC", "altitude_m": " m", "grid.voltage_v": " V", "earthing.soil_resistivity_ohm_m": " ohm-m",
            "earthing.fault_current_ka": " kA", "earthing.fault_clearing_s": " s", "lps.ese_delta_t_us": " us", "lps.mast_height_m": " m"}.get(k, "")  # fmt: skip
    if k in ("vdrop_dc_max", "vdrop_ac_max"):
        return f"{100 * v:g} %"
    return f"{v:g}{unit}" if isinstance(v, int | float) else str(v)


def _layout_value(lay: ArrayLayout, t, k: str) -> str:
    if t is None:
        return "not set"
    if k == "cols":
        return f"{max(x.cols for x in lay.table_by_id.values())}"
    v = getattr(t, k)
    return f"{v:g} deg" if k in ("tilt_deg", "azimuth_deg") else str(getattr(v, "value", v))

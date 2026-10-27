"""What to place and how: the agent-facing placement intent, and the result it yields."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from unicad.domain.layout import ArrayLayout, Mounting, Orientation, Point
from unicad.domain.site import Setbacks


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MountingKind(StrEnum):
    FIXED_TILT = "fixed_tilt"
    TRACKER_1P = "tracker_1p"
    ROOFTOP_FLUSH = "rooftop_flush"
    ROOFTOP_TILTED = "rooftop_tilted"
    CARPORT = "carport"

    @property
    def mounting(self) -> Mounting:
        return {
            MountingKind.FIXED_TILT: Mounting.FIXED_TILT,
            MountingKind.TRACKER_1P: Mounting.SINGLE_AXIS_TRACKER,
            MountingKind.ROOFTOP_FLUSH: Mounting.ROOF_FLUSH,
            MountingKind.ROOFTOP_TILTED: Mounting.FIXED_TILT,
            MountingKind.CARPORT: Mounting.CARPORT,
        }[self]

    @property
    def on_roof(self) -> bool:
        return self in (MountingKind.ROOFTOP_FLUSH, MountingKind.ROOFTOP_TILTED)

    @property
    def is_tracker(self) -> bool:
        return self is MountingKind.TRACKER_1P


class PitchMethod(StrEnum):
    EXPLICIT = "explicit"
    GCR = "gcr"
    GAP = "gap"
    SOLSTICE = "solstice"


class FillOrder(StrEnum):
    SWITCHYARD = "switchyard"
    ROWS = "rows"
    NEAREST = "nearest"


ROOF_TABLE_GAP_M = 0.6
ROOF_TABLE_GAP = (
    f"gap between tables along a row on a roof: {ROOF_TABLE_GAP_M:g} m, a cleaning and maintenance walkway between tables (common C&I rooftop practice, "
    "not from a cited source; set table_gap_m, or walkway_every_tables for wider walkways every N tables)"
)
DEFAULTS_ARE_ASSUMPTIONS = {
    "module_gap_m": "gap between modules on a table",
    "table_gap_m": "gap between tables along a row",
    "tracker_max_rotation_deg": "tracker rotation limit",
}


class PlacementSpec(_Frozen):
    """Array placement intent. Distances in metres, angles in degrees; azimuth is where the modules face, clockwise from grid north (south = 180).

    Pitch (row to row, along the row normal): `explicit` uses `pitch_m`; `gcr` divides the collector slope length by `gcr`;
    `gap` adds `row_gap_m` to the row's plan depth; `solstice` is the smallest pitch with no row-on-row shading between
    `shade_start_hour` and `shade_end_hour` apparent solar time on the winter solstice at the site latitude.
    A tracker's rows run along `axis_azimuth_deg` (0 = north-south axis) and its pitch is axis to axis.
    """

    mounting: MountingKind = MountingKind.FIXED_TILT
    module: str
    table_rows: int = Field(default=2, gt=0, description="modules across the table (2 for 2P)")
    table_cols: int = Field(default=28, gt=0, description="modules along the table")
    orientation: Orientation = Orientation.PORTRAIT
    half_tables: bool = True
    half_cols: int | None = Field(default=None, gt=0, description="modules along a half table; default table_cols // 2")
    tilt_deg: float = Field(default=0.0, ge=0, le=60)
    azimuth_deg: float = Field(default=180.0, ge=0, lt=360)
    axis_azimuth_deg: float = Field(default=0.0, ge=0, lt=180)
    tracker_max_rotation_deg: float = Field(default=60.0, gt=0, le=90)
    pitch_method: PitchMethod = PitchMethod.SOLSTICE
    pitch_m: float | None = Field(default=None, gt=0)
    gcr: float | None = Field(default=None, gt=0, lt=1)
    row_gap_m: float | None = Field(default=None, ge=0)
    shade_start_hour: float = Field(default=9.0, ge=4, le=12)
    shade_end_hour: float = Field(default=15.0, ge=12, le=20)
    latitude_deg: float | None = Field(default=None, ge=-90, le=90, description="default: the site origin latitude")
    module_gap_m: float = Field(default=0.02, ge=0)
    table_gap_m: float = Field(default=1.5, ge=0)
    road_every_rows: int | None = Field(default=None, gt=0)
    road_width_m: float = Field(default=6.0, gt=0)
    walkway_every_tables: int | None = Field(default=None, gt=0)
    row_walkway_every_rows: int | None = Field(default=None, gt=0, description="leave a maintenance walkway after every N rows; on roofs default 2 when the row gap is narrower than a walkway")
    row_walkway_m: float = Field(default=0.6, gt=0, description="clear width of the walkway between rows")
    walkway_width_m: float = Field(default=4.0, gt=0)
    setbacks: Setbacks | None = None
    regions: Literal["auto", "ground", "roofs"] = "auto"
    target_dc_kwp: float | None = Field(default=None, gt=0)
    fill: FillOrder = Field(default=FillOrder.ROWS, description="with a target: rows fills the front rows first; nearest fills closest to fill_from; switchyard (layout_place's default) grows the array from the switchyard (given, marked on the site, or the transmission line), else rows")
    fill_from: Point | None = None
    n_blocks: int | None = Field(default=None, gt=0)
    block_dc_kwp: float | None = Field(default=None, gt=0)
    modules_per_string: int | None = Field(default=None, gt=0)
    strings_per_inverter: int | None = Field(default=None, gt=0)
    whole_inverters: bool = Field(default=True, description="with strings_per_inverter: size every block to whole inverters, leaving out the remainder tables")
    station: Point | None = Field(default=None, description="inverter station of every block, local metres; default on the block (on a roof for rooftop arrays)")
    switchyard: Point | None = Field(default=None, description="MV switchyard (main LT panel on an LT plant), local metres; default a marked site area, else derived")
    switchyard_m: tuple[float, float] | None = Field(default=None, description="switchyard length x width, to keep a derived switchyard clear of tables")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems = []
        needs = {PitchMethod.EXPLICIT: "pitch_m", PitchMethod.GCR: "gcr", PitchMethod.GAP: "row_gap_m"}
        if (field := needs.get(self.pitch_method)) and getattr(self, field) is None:
            problems.append(f"pitch_method {self.pitch_method.value!r} needs {field}")
        if self.half_cols is not None and self.half_cols >= self.table_cols:
            problems.append(f"half_cols {self.half_cols} must be below table_cols {self.table_cols}")
        if self.fill is FillOrder.NEAREST and self.fill_from is None:
            problems.append("fill 'nearest' needs fill_from")
        if self.n_blocks is not None and self.block_dc_kwp is not None:
            problems.append("give n_blocks or block_dc_kwp, not both")
        if self.mounting.is_tracker and self.tilt_deg:
            problems.append("a tracker's tilt is its rotation; leave tilt_deg at 0")
        if problems:
            raise ValueError("invalid placement spec: " + "; ".join(problems))
        if self.mounting.on_roof and "table_gap_m" not in self.model_fields_set:
            object.__setattr__(self, "table_gap_m", ROOF_TABLE_GAP_M)
        return self

    @property
    def half_table_cols(self) -> int:
        return self.half_cols or max(1, self.table_cols // 2)

    @property
    def table_azimuth_deg(self) -> float:
        """Facing direction stored on each Table; for a tracker, square to its axis so that rows run along the axis."""
        return (self.axis_azimuth_deg + 90.0) % 360 if self.mounting.is_tracker else self.azimuth_deg

    def assumptions(self) -> list[str]:
        out = [ROOF_TABLE_GAP if f == "table_gap_m" and self.mounting.on_roof else f"{label}: {getattr(self, f)} (default, not from a cited source)"
               for f, label in DEFAULTS_ARE_ASSUMPTIONS.items() if f not in self.model_fields_set and (f != "tracker_max_rotation_deg" or self.mounting.is_tracker)]
        if self.road_every_rows and "road_width_m" not in self.model_fields_set:
            out.append(f"internal road width: {self.road_width_m} m (default, not from a cited source)")
        if self.walkway_every_tables and "walkway_width_m" not in self.model_fields_set:
            out.append(f"walkway width: {self.walkway_width_m} m (default, not from a cited source)")
        return out


class TableGeometry(_Frozen):
    """Plan dimensions of one table type: `length_m` along the row, `depth_m` across it, `slope_m` the collector width on the slope."""

    cols: int
    length_m: float
    depth_m: float
    slope_m: float


class PitchInfo(_Frozen):
    method: PitchMethod
    pitch_m: float
    note: str
    shade_angle_deg: float | None = None
    shade_hour: float | None = None


class BlockSummary(_Frozen):
    id: str
    name: str
    tables_full: int
    tables_half: int
    modules: int
    dc_kwp: float
    station: Point | None


class RoadLine(_Frozen):
    points: tuple[Point, ...]
    width_m: float


class PlacementMetrics(_Frozen):
    tables_full: int
    tables_half: int
    modules: int
    dc_kwp: float
    module_wp: float
    pitch_m: float
    gcr: float
    buildable_m2: float
    area_used_m2: float
    strings: int
    unstrung_modules: int
    block_balance: float = Field(description="(largest - smallest) / mean block DC capacity")


class PlacementResult(_Frozen):
    layout: ArrayLayout
    metrics: PlacementMetrics
    pitch: PitchInfo
    full_table: TableGeometry
    half_table: TableGeometry | None
    blocks: tuple[BlockSummary, ...]
    roads: tuple[RoadLine, ...] = ()
    target_reached: bool | None = None
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Volts = Annotated[float, Field(gt=0, description="V")]
Amps = Annotated[float, Field(gt=0, description="A")]
Watts = Annotated[float, Field(gt=0, description="W")]
KVA = Annotated[float, Field(gt=0, description="kVA")]
Mm = Annotated[float, Field(gt=0, description="mm")]
Mm2 = Annotated[float, Field(gt=0, description="mm²")]
PctPerC = Annotated[float, Field(description="%/°C")]


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["datasheet", "cec", "pan_ond", "catalog", "standard", "manual"]
    ref: str
    retrieved: str | None = None


class Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    manufacturer: str
    model: str
    symbol: str
    source: Source


class CellTech(StrEnum):
    MONO_PERC = "mono_perc"
    TOPCON = "topcon"
    HJT = "hjt"
    IBC = "ibc"
    POLY = "poly"
    THIN_FILM = "thin_film"


class ModuleSpec(Spec):
    kind: Literal["module"] = "module"
    symbol: str = "pv.module"
    pmax: Watts
    voc: Volts
    isc: Amps
    vmp: Volts
    imp: Amps
    temp_coeff_voc: PctPerC
    temp_coeff_isc: PctPerC
    temp_coeff_pmax: PctPerC
    noct_c: float | None = None
    max_system_voltage: Volts = 1500
    max_series_fuse: Amps
    length: Mm
    width: Mm
    cells: int = Field(gt=0)
    tech: CellTech
    bifacial: bool = False
    bifaciality: float | None = Field(default=None, ge=0, le=1)
    almm_listed: bool | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "ModuleSpec":
        if not (self.vmp < self.voc and self.imp < self.isc):
            raise ValueError("expected Vmp < Voc and Imp < Isc")
        if abs(self.vmp * self.imp - self.pmax) / self.pmax > 0.03:
            raise ValueError(f"Vmp*Imp={self.vmp * self.imp:.1f} W disagrees with Pmax={self.pmax} W by >3%")
        return self


class InverterSpec(Spec):
    kind: Literal["inverter"] = "inverter"
    symbol: str = "inv.string"
    topology: Literal["string", "central", "micro", "hybrid"]
    ac_rated_kva: KVA
    ac_max_kva: KVA
    ac_voltage: Volts
    phases: Literal[1, 3]
    dc_max_voltage: Volts
    mppt_min: Volts
    mppt_max: Volts
    start_voltage: Volts | None = None
    mppt_count: int = Field(gt=0)
    inputs_per_mppt: int = Field(gt=0)
    max_input_current_per_mppt: Amps
    max_short_circuit_current_per_mppt: Amps
    max_efficiency: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def _window(self) -> "InverterSpec":
        if not (self.mppt_min < self.mppt_max <= self.dc_max_voltage):
            raise ValueError("expected mppt_min < mppt_max <= dc_max_voltage")
        if self.ac_max_kva < self.ac_rated_kva:
            raise ValueError("ac_max_kva below ac_rated_kva")
        return self


class BatterySpec(Spec):
    kind: Literal["battery"] = "battery"
    symbol: str = "bess.container"
    chemistry: Literal["lfp", "nmc", "lead_acid"]
    energy_kwh: float = Field(gt=0)
    nominal_voltage: Volts
    voltage_min: Volts
    voltage_max: Volts
    max_continuous_current: Amps


class PCSSpec(Spec):
    kind: Literal["pcs"] = "pcs"
    symbol: str = "inv.pcs"
    rated_kva: KVA
    ac_voltage: Volts
    dc_voltage_min: Volts
    dc_voltage_max: Volts


class TransformerSpec(Spec):
    kind: Literal["transformer"] = "transformer"
    symbol: str = "tx.two_winding"
    rated_kva: KVA
    hv_kv: float = Field(gt=0)
    lv_kv: tuple[float, ...] = Field(min_length=1)
    vector_group: str
    impedance_pct: float = Field(gt=0, lt=30)
    cooling: Literal["ONAN", "ONAF", "AN", "AF", "KNAN"] = "ONAN"
    standard: str = "IS 1180 / IS 2026"


class Conductor(StrEnum):
    CU = "cu"
    AL = "al"


class CableSpec(Spec):
    kind: Literal["cable"] = "cable"
    symbol: str = "ann.cable_tag"
    service: Literal["dc_solar", "lv_ac", "mv_ac", "earthing", "control"]
    conductor: Conductor
    cores: int = Field(gt=0)
    size_mm2: Mm2
    insulation: Literal["xlpe", "pvc", "epr", "xlpo"]
    armoured: bool
    voltage_grade: str
    ampacity_air: Amps | None = None
    ampacity_ground: Amps | None = None
    r_ohm_per_km_90c: float | None = Field(default=None, gt=0)
    x_ohm_per_km: float | None = Field(default=None, ge=0)

    def callout(self) -> str:
        return f"{self.cores}C x {self.size_mm2:g} sq.mm {self.conductor.value.capitalize()} {self.insulation.upper()}{' ARM' if self.armoured else ''}"


class ProtectionSpec(Spec):
    kind: Literal["protection"] = "protection"
    device: Literal["mcb", "mccb", "acb", "vcb", "rccb", "fuse_gpv", "fuse_hrc", "dc_isolator", "ac_isolator", "spd_dc", "spd_ac", "sfu"]
    rated_current: Amps | None = None
    rated_voltage: Volts
    poles: int = Field(gt=0, le=4)
    breaking_capacity_ka: float | None = Field(default=None, gt=0)
    curve: str | None = None
    spd_type: Literal["1", "2", "1+2", "3"] | None = None


class MeterSpec(Spec):
    kind: Literal["meter"] = "meter"
    symbol: str = "inst.energy_meter"
    function: Literal["net", "generation", "abt", "check"]
    accuracy_class: str
    connection: Literal["direct", "ct", "ct_pt"]


EquipmentSpec = Annotated[
    ModuleSpec | InverterSpec | BatterySpec | PCSSpec | TransformerSpec | CableSpec | ProtectionSpec | MeterSpec,
    Field(discriminator="kind"),
]

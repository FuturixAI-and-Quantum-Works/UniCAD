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

    @model_validator(mode="before")
    @classmethod
    def _default_symbol(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("symbol"):
            return {**data, "symbol": cls.symbol_for(data)}
        return data

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        raise NotImplementedError


class CellTech(StrEnum):
    MONO_PERC = "mono_perc"
    TOPCON = "topcon"
    HJT = "hjt"
    IBC = "ibc"
    POLY = "poly"
    THIN_FILM = "thin_film"


class ModuleSpec(Spec):
    kind: Literal["module"] = "module"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return "pv.module"

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
    max_series_fuse: Amps | None = None
    length: Mm
    width: Mm
    cells: int = Field(gt=0)
    tech: CellTech
    bifacial: bool = False
    bifaciality: float | None = Field(default=None, ge=0, le=1)
    almm_listed: bool | None = None
    almm_ref: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "ModuleSpec":
        if not (self.vmp < self.voc and self.imp < self.isc):
            raise ValueError("expected Vmp < Voc and Imp < Isc")
        if abs(self.vmp * self.imp - self.pmax) / self.pmax > 0.03:
            raise ValueError(f"Vmp*Imp={self.vmp * self.imp:.1f} W disagrees with Pmax={self.pmax} W by >3%")
        return self


class InverterSpec(Spec):
    kind: Literal["inverter"] = "inverter"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return {"string": "inv.string", "central": "inv.central", "micro": "inv.micro", "hybrid": "inv.pcs"}[data["topology"]]

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
    inputs_per_mppt: int | None = Field(default=None, gt=0)
    max_input_current_per_mppt: Amps | None = None
    max_short_circuit_current_per_mppt: Amps | None = None
    dc_input_count: int | None = Field(default=None, gt=0)
    max_dc_input_current: Amps | None = None
    max_dc_short_circuit_current: Amps | None = None
    max_efficiency: float | None = Field(default=None, gt=0, le=1)
    ac_terminal_max_mm2: float | None = Field(default=None, gt=0, description="largest AC cable per pole the output terminals take (sq.mm), from the datasheet; None when it is silent")
    ac_terminal_cables_per_pole: int | None = Field(default=None, gt=0, description="most AC cables per pole the output terminals take, from the datasheet; None when it is silent")

    @model_validator(mode="before")
    @classmethod
    def _single_mppt_totals(cls, data: object) -> object:
        """With one MPPT the datasheet totals are the per-MPPT values, so each side fills the other. With several, nothing is split."""
        if not isinstance(data, dict) or data.get("mppt_count") != 1:
            return data
        out = dict(data)
        for per_mppt, total in (
            ("inputs_per_mppt", "dc_input_count"),
            ("max_input_current_per_mppt", "max_dc_input_current"),
            ("max_short_circuit_current_per_mppt", "max_dc_short_circuit_current"),
        ):
            if out.get(per_mppt) is None:
                out[per_mppt] = out.get(total)
            if out.get(total) is None:
                out[total] = out.get(per_mppt)
        return out

    @model_validator(mode="after")
    def _window(self) -> "InverterSpec":
        if not (self.mppt_min < self.mppt_max <= self.dc_max_voltage):
            raise ValueError("expected mppt_min < mppt_max <= dc_max_voltage")
        if self.max_input_current_per_mppt is None and self.max_dc_input_current is None:
            raise ValueError("expected max_input_current_per_mppt or max_dc_input_current")
        if self.ac_max_kva < self.ac_rated_kva:
            raise ValueError("ac_max_kva below ac_rated_kva")
        return self


class BatterySpec(Spec):
    kind: Literal["battery"] = "battery"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return "bat.bess"

    chemistry: Literal["lfp", "nmc", "lead_acid"]
    energy_kwh: float = Field(gt=0)
    nominal_voltage: Volts
    voltage_min: Volts
    voltage_max: Volts
    max_continuous_current: Amps | None = None


class PCSSpec(Spec):
    kind: Literal["pcs"] = "pcs"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return "inv.pcs"

    rated_kva: KVA
    ac_voltage: Volts
    dc_voltage_min: Volts
    dc_voltage_max: Volts


class TransformerSpec(Spec):
    kind: Literal["transformer"] = "transformer"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return "tx.power_3w" if len(data.get("lv_kv", ())) > 1 else "tx.power_2w"

    rated_kva: KVA
    hv_kv: float = Field(gt=0)
    lv_kv: tuple[float, ...] = Field(min_length=1)
    vector_group: str
    impedance_pct: float = Field(gt=0, lt=30)
    cooling: Literal["ONAN", "ONAF", "AN", "AF", "KNAN"] = "ONAN"
    standard: str = "IS 1180 / IS 2026"
    winding_kva: tuple[KVA, ...] | None = Field(default=None, description="rating of each LV winding, in lv_kv order; omitted when the windings share rated_kva")
    impedance_base_kva: KVA | None = Field(default=None, description="kVA base impedance_pct is stated on; rated_kva when omitted. HV to one LV winding for multi-winding units")
    lv_lv_impedance_min_pct: float | None = Field(default=None, gt=0, description="minimum impedance between two LV windings, only when the source states it")
    tap_changer: Literal["octc", "oltc"] | None = Field(default=None, description="tap changer on the HV winding")
    tap_range_pct: tuple[float, float] | None = Field(default=None, description="(lowest, highest) HV tap in % of rated voltage, e.g. (-10, 10)")
    tap_step_pct: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _windings(self) -> "TransformerSpec":
        if self.winding_kva is not None:
            if len(self.winding_kva) != len(self.lv_kv):
                raise ValueError("winding_kva needs one rating per lv_kv winding")
            if sum(self.winding_kva) > self.rated_kva * 1.0001:
                raise ValueError("winding_kva sums to more than rated_kva")
        if self.tap_range_pct is not None:
            if self.tap_changer is None or self.tap_step_pct is None:
                raise ValueError("tap_range_pct needs tap_changer and tap_step_pct")
            if not self.tap_range_pct[0] <= 0 <= self.tap_range_pct[1] or self.tap_range_pct[0] == self.tap_range_pct[1]:
                raise ValueError("tap_range_pct must be (lowest, highest) with the principal tap inside")
        return self


class Conductor(StrEnum):
    CU = "cu"
    AL = "al"


class Ampacity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    air: Amps | None = None
    ground: Amps | None = None
    duct: Amps | None = None


class CableSpec(Spec):
    kind: Literal["cable"] = "cable"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return "ann.cable_tag"

    service: Literal["dc_solar", "lv_ac", "mv_ac", "earthing", "control"]
    conductor: Conductor
    cores: int = Field(gt=0, description="full-size cores; a 3.5C cable has cores=3 and reduced_neutral=True")
    reduced_neutral: bool = False
    neutral_size_mm2: Mm2 | None = Field(default=None, description="only when the source states it")
    size_mm2: Mm2
    insulation: Literal["xlpe", "pvc", "epr", "xlpo"]
    armoured: bool
    voltage_grade: str
    ampacity_air: Amps | None = Field(default=None, description="multicore cable; for single-core, a circuit of three cables")
    ampacity_ground: Amps | None = None
    ampacity_duct: Amps | None = None
    ampacity_2x1c: Ampacity | None = Field(default=None, description="single-core only: circuit of two cables (1-phase or DC pair)")
    r_ohm_per_km_90c: float | None = Field(default=None, gt=0)
    x_ohm_per_km: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _cores(self) -> "CableSpec":
        if self.reduced_neutral and self.cores != 3:
            raise ValueError("reduced_neutral applies to 3.5C cables, which have 3 full-size cores")
        if self.neutral_size_mm2 is not None and not (self.reduced_neutral and self.neutral_size_mm2 < self.size_mm2):
            raise ValueError("neutral_size_mm2 needs reduced_neutral and a size below size_mm2")
        if self.ampacity_2x1c is not None and self.cores != 1:
            raise ValueError("ampacity_2x1c applies to single-core cables")
        return self

    @property
    def core_label(self) -> str:
        return "3.5" if self.reduced_neutral else str(self.cores)

    def callout(self) -> str:
        return f"{self.core_label}C x {self.size_mm2:g} sq.mm {self.conductor.value.capitalize()} {self.insulation.upper()}{' ARM' if self.armoured else ''}"


PROTECTION_SYMBOLS = {
    "mcb": "sw.mcb",
    "mccb": "sw.mccb",
    "acb": "sw.acb",
    "vcb": "sw.vcb",
    "rccb": "sw.rccb",
    "fuse_gpv": "dc.fuse",
    "fuse_hrc": "sw.hrc_fuse",
    "dc_isolator": "dc.isolator",
    "ac_isolator": "sw.isolator",
    "spd_dc": "dc.spd",
    "spd_ac": "prot.spd_ac",
    "sfu": "sw.switch_fuse",
    "rmu": "sw.rmu",
    "ct": "tx.ct",
    "pt": "tx.pt",
    "lightning_arrester": "prot.lightning_arrester",
}


class ProtectionSpec(Spec):
    kind: Literal["protection"] = "protection"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return PROTECTION_SYMBOLS[data["device"]]

    device: Literal["mcb", "mccb", "acb", "vcb", "rccb", "fuse_gpv", "fuse_hrc", "dc_isolator", "ac_isolator", "spd_dc", "spd_ac", "sfu", "rmu", "ct", "pt", "lightning_arrester"]
    rated_current: Amps | None = None
    rated_voltage: Volts
    poles: int = Field(gt=0, le=4)
    breaking_capacity_ka: float | None = Field(default=None, gt=0)
    short_time_withstand_ka: float | None = Field(default=None, gt=0, description="rated short-time withstand current")
    short_time_duration_s: float | None = Field(default=None, gt=0)
    ratio: str | None = Field(default=None, description="CT or PT ratio as published, e.g. 200-100/1-1 A")
    accuracy_class: str | None = None
    burden_va: float | None = Field(default=None, gt=0)
    nominal_discharge_current_ka: float | None = Field(default=None, gt=0, description="In (8/20 us) of an SPD, or the nominal discharge current of an arrester")
    max_discharge_current_ka: float | None = Field(default=None, gt=0, description="Imax (8/20 us) of an SPD, the single-shot surge it survives; distinct from and not below In")
    curve: str | None = None
    spd_type: Literal["1", "2", "1+2", "3"] | None = None
    surge_counter: bool | None = Field(default=None, description="lightning arrester supplied with a discharge counter; None when the source is silent")
    terminal_max_mm2: float | None = Field(default=None, gt=0, description="largest cable per pole the terminals take (sq.mm); None when the source is silent")
    terminal_cables_per_pole: int | None = Field(default=None, gt=0, description="most cables per pole the terminals take")
    break_time_s: float | None = Field(default=None, gt=0, description="total breaking time (opening + arcing) of a circuit-breaker, as the datasheet gives it")

    @model_validator(mode="after")
    def _discharge(self) -> "ProtectionSpec":
        if self.max_discharge_current_ka is not None and self.nominal_discharge_current_ka is not None and self.max_discharge_current_ka < self.nominal_discharge_current_ka:
            raise ValueError("Imax (max_discharge_current_ka) is below In (nominal_discharge_current_ka)")
        return self


class MeterSpec(Spec):
    kind: Literal["meter"] = "meter"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return {"net": "inst.net_meter", "generation": "inst.kwh", "abt": "inst.abt", "check": "inst.kwh", "multifunction": "inst.mfm"}[data["function"]]

    function: Literal["net", "generation", "abt", "check", "multifunction"]
    accuracy_class: str
    connection: Literal["direct", "ct", "ct_pt"]
    communication: str | None = Field(default=None, description="serial or network interface the datasheet states, e.g. 'RS-485 Modbus RTU'; None when the meter has none or the source is silent")


class DataLoggerSpec(Spec):
    kind: Literal["logger"] = "logger"

    @classmethod
    def symbol_for(cls, data: dict) -> str:
        return "inst.datalogger"

    rs485_ports: int = Field(gt=0, description="RS-485 ports for inverters, meters and weather stations")
    max_devices: int | None = Field(default=None, gt=0, description="most devices the datasheet lets one logger manage")
    max_devices_per_rs485_port: int | None = Field(default=None, gt=0)
    rs485_protocols: tuple[str, ...] = Field(default=(), description="protocols as the datasheet names them")
    ethernet_ports: int = Field(default=0, ge=0)
    ethernet_protocols: tuple[str, ...] = ()
    ac_supply_v: tuple[Volts, Volts] | None = Field(default=None, description="(lowest, highest) AC auxiliary supply, 50/60 Hz; None when the logger has no AC input")
    dc_supply_v: tuple[Volts, Volts] | None = Field(default=None, description="(lowest, highest) DC auxiliary supply")
    ip_rating: str | None = None
    dimensions_mm: tuple[Mm, Mm, Mm] | None = Field(default=None, description="(length, width, height) as the datasheet gives them")

    @model_validator(mode="after")
    def _ranges(self) -> "DataLoggerSpec":
        for rng in (self.ac_supply_v, self.dc_supply_v):
            if rng is not None and rng[0] > rng[1]:
                raise ValueError("a supply range is (lowest, highest)")
        if self.max_devices is not None and self.max_devices_per_rs485_port is not None and self.max_devices_per_rs485_port > self.max_devices:
            raise ValueError("max_devices_per_rs485_port exceeds max_devices")
        return self


EquipmentSpec = Annotated[
    ModuleSpec | InverterSpec | BatterySpec | PCSSpec | TransformerSpec | CableSpec | ProtectionSpec | MeterSpec | DataLoggerSpec,
    Field(discriminator="kind"),
]

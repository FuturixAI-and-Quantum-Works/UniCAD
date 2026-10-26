"""Pick catalog equipment by rule: the smallest catalogued rating that covers a requirement."""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.catalog import CableSpec, MeterSpec, ProtectionSpec, TransformerSpec
from unicad.design.strings import DesignError
from unicad.domain.network import Laying
from unicad.rules.cables import table_ampacity
from unicad.rules.protection import covers_fault

# Placeholder minimum breaking capacities: no fault study exists at this stage, so these only stop the
# picker from choosing the lowest-duty frame. They are reported as assumptions wherever they are used.
MIN_BREAKING_KA = {"mcb": 10.0, "mccb": 25.0, "acb": 42.0, "vcb": 20.0}
BREAKING_ASSUMPTION = (
    "Breaking capacities are not checked against a fault level (none in design_basis.fault_levels for the device's voltage). The picker takes the lowest catalogued breaking capacity "
    "at or above a placeholder minimum (MCB 10 kA, MCCB 25 kA, ACB 42 kA, VCB 20 kA); confirm against the fault study."
)
SPD_AC_UC_FACTOR = 1.1
SPD_AC_ASSUMPTION = "AC SPDs are chosen with continuous operating voltage Uc >= 1.1 x U0 (phase to earth), common practice for TN systems; not a cited limit."
LV_BREAKER_LADDER = (("mcb", 63.0), ("mccb", 630.0), ("acb", math.inf))


class NoFit(DesignError):
    """No catalog entry meets the requirement."""


def protections(catalog: Catalog, devices: Iterable[str]) -> list[ProtectionSpec]:
    wanted = set(devices)
    return [s for s in catalog.filter("protection") if isinstance(s, ProtectionSpec) and s.device in wanted]


def _breaking_key(spec: ProtectionSpec) -> tuple:
    floor = MIN_BREAKING_KA.get(spec.device, 0.0)
    bc = spec.breaking_capacity_ka or 0.0
    return (0, bc) if bc >= floor else (1, -bc)


def pick_rated(
    catalog: Catalog, devices: Iterable[str], need_a: float, voltage_v: float, poles: Iterable[int], fault_ka: float | None = None
) -> ProtectionSpec:
    """Smallest rated current >= need_a among `devices`, rated for voltage_v, with one of `poles` (first preferred).

    With `fault_ka` only devices whose breaking capacity (and, for vacuum breakers, short-time withstand) covers the fault level qualify, and the
    smallest breaking capacity that does is preferred; without it the lowest capacity at or above the placeholder minimum is."""
    poles = tuple(poles)
    fits = [
        s
        for s in protections(catalog, devices)
        if s.rated_current is not None and s.rated_current >= need_a - 1e-9 and s.rated_voltage >= voltage_v and s.poles in poles
        and (s.device not in MIN_BREAKING_KA or s.breaking_capacity_ka is not None)
    ]
    if fault_ka is not None:
        fits = [s for s in fits if s.device not in MIN_BREAKING_KA or covers_fault(s, fault_ka)]
        if not fits:
            raise NoFit(f"no catalog {'/'.join(devices)} rated >= {need_a:.1f} A at {voltage_v:g} V with {'/'.join(map(str, poles))} poles and breaking capacity >= {fault_ka:g} kA")
    if not fits:
        raise NoFit(f"no catalog {'/'.join(devices)} rated >= {need_a:.1f} A at {voltage_v:g} V with {'/'.join(map(str, poles))} poles")
    if fault_ka is not None:
        return min(fits, key=lambda s: (s.rated_current, poles.index(s.poles), s.breaking_capacity_ka or 0.0, s.id))
    return min(fits, key=lambda s: (s.rated_current, poles.index(s.poles), _breaking_key(s), s.id))


def pick_lv_breaker(catalog: Catalog, need_a: float, voltage_v: float, poles: Iterable[int], fault_ka: float | None = None) -> ProtectionSpec:
    """MCB up to 63 A, MCCB up to 630 A, ACB above (Indian LT panel practice); falls through to the next type when none fits."""
    poles = tuple(poles)
    for device, upto in LV_BREAKER_LADDER:
        if need_a <= upto:
            try:
                return pick_rated(catalog, [device], need_a, voltage_v, poles, fault_ka)
            except NoFit:
                continue
    return pick_rated(catalog, ["acb", "mccb"], need_a, voltage_v, poles, fault_ka)


def pick_by_voltage(catalog: Catalog, device: str, need_v: float, poles: Iterable[int] = (1, 2, 3, 4)) -> ProtectionSpec:
    poles = tuple(poles)
    fits = [s for s in protections(catalog, [device]) if s.rated_voltage >= need_v - 1e-9 and s.poles in poles]
    if not fits:
        raise NoFit(f"no catalog {device} rated >= {need_v:.0f} V")
    return min(fits, key=lambda s: (s.rated_voltage, poles.index(s.poles), s.id))


def pick_ac_spd(catalog: Catalog, line_v: float, phases: int) -> ProtectionSpec:
    u0 = line_v / math.sqrt(3) if phases == 3 else line_v
    return pick_by_voltage(catalog, "spd_ac", SPD_AC_UC_FACTOR * u0, (4, 3, 1) if phases == 3 else (2, 1, 4))


def pick_mv(catalog: Catalog, device: str, system_kv: float) -> ProtectionSpec | None:
    """An MV device (PT, LA) whose catalog entry is meant for this system voltage: rated 1.0..1.2 x system kV, or for LAs Ur in 0.8..1.0 x."""
    lo, hi = (0.8, 1.0) if device == "lightning_arrester" else (1.0, 1.2)
    fits = [s for s in protections(catalog, [device]) if lo * system_kv * 1000 <= s.rated_voltage <= hi * system_kv * 1000 + 1e-6]
    return min(fits, key=lambda s: s.id) if fits else None


def pick_meter(catalog: Catalog, function: str, connection: str) -> MeterSpec:
    fits = [s for s in catalog.filter("meter") if isinstance(s, MeterSpec) and s.function == function and s.connection == connection]
    if not fits:
        raise NoFit(f"no catalog {function} meter with {connection} connection")
    return min(fits, key=lambda s: s.id)


def pick_transformer(catalog: Catalog, need_kva: float, hv_kv: float, lv_kv: float | None = None) -> TransformerSpec:
    fits = [
        s
        for s in catalog.filter("transformer")
        if isinstance(s, TransformerSpec) and s.hv_kv == hv_kv and s.rated_kva >= need_kva - 1e-9 and (lv_kv is None or lv_kv in s.lv_kv)
    ]
    if not fits:
        raise NoFit(f"no catalog transformer {hv_kv:g} kV{f'/{lv_kv:g} kV' if lv_kv else ''} rated >= {need_kva:.0f} kVA")
    return min(fits, key=lambda s: (s.rated_kva, s.id))


def largest_transformer_kva(catalog: Catalog, hv_kv: float) -> float | None:
    sizes = [s.rated_kva for s in catalog.filter("transformer") if isinstance(s, TransformerSpec) and s.hv_kv == hv_kv]
    return max(sizes) if sizes else None


@dataclass(frozen=True)
class CableFamily:
    """Catalog cables an edge may use. `cores` lists the multicore count first; single-core cables run as one circuit per run."""

    name: str
    manufacturer: str
    service: str
    conductor: str
    cores: tuple[int, ...]
    armoured: bool | None = None
    insulation: str | None = None
    voltage_grade: str | None = None
    max_runs: int = 8
    multicore_max_mm2: float = math.inf
    min_mm2: float = 0.0
    max_mm2: float = math.inf

    def cables(self, catalog: Catalog) -> list[CableSpec]:
        out = []
        for c in catalog.filter("cable"):
            if not isinstance(c, CableSpec) or c.manufacturer != self.manufacturer or c.service != self.service or c.conductor.value != self.conductor:
                continue
            if c.cores not in self.cores or not self.min_mm2 <= c.size_mm2 <= self.max_mm2:
                continue
            if self.armoured is not None and c.armoured != self.armoured:
                continue
            if self.insulation is not None and c.insulation != self.insulation:
                continue
            if self.voltage_grade is not None and c.voltage_grade != self.voltage_grade:
                continue
            if c.cores != 1 and c.size_mm2 > self.multicore_max_mm2:
                continue
            out.append(c)
        return out


DC_FAMILY = CableFamily("dc", "Polycab", "dc_solar", "cu", (1,), max_runs=8, min_mm2=4.0)
DC_STRING_FAMILY = CableFamily("dc_string", "Polycab", "dc_solar", "cu", (1,), max_runs=1, min_mm2=4.0)
LV3_FAMILY = CableFamily("lv3", "Polycab", "lv_ac", "al", (4, 1), armoured=True, insulation="xlpe", max_runs=12, multicore_max_mm2=300.0, min_mm2=10.0, max_mm2=400.0)
LV1_FAMILY = CableFamily("lv1", "Polycab", "lv_ac", "al", (2,), armoured=True, insulation="xlpe", max_runs=4, min_mm2=10.0)
DC_STRING_MIN = "String cables are at least 4 sq.mm (design practice for PV string wiring), even where 2.5 sq.mm would carry the current."
LV_MIN = "LT aluminium cables are at least 10 sq.mm (design practice for armoured Al cable)."


def mv_family(kv: float) -> CableFamily:
    grade = "19/33 kV" if kv > 22 else "6.35/11 kV"
    return CableFamily(f"mv{kv:g}", "Havells", "mv_ac", "al", (3, 1), armoured=True, insulation="xlpe", voltage_grade=grade, max_runs=6, multicore_max_mm2=400.0)


@dataclass(frozen=True)
class CableOption:
    cable: CableSpec
    runs: int
    iz: float

    @property
    def conductors(self) -> int:
        return self.cable.cores if self.cable.cores > 1 else 3

    @property
    def cost(self) -> tuple:
        """Fewest parallel runs first, then the smallest size, multicore before single-core."""
        return (self.runs, self.cable.size_mm2, self.cable.cores == 1)

    @property
    def r_eff(self) -> float:
        return (self.cable.r_ohm_per_km_90c or math.inf) / self.runs


def cable_options(catalog: Catalog, family: CableFamily, laying: Laying, phases: int, runs: int | None = None) -> list[CableOption]:
    """Every (cable, runs) with a rating for the laying, cheapest copper first. A fixed `runs` keeps the run count (one string per run)."""
    out = []
    for c in family.cables(catalog):
        base, _, _ = table_ampacity(c, laying, phases)
        if base is None or c.r_ohm_per_km_90c is None:
            continue
        if family.service == "dc_solar" or phases == 1:
            conductors_ok = c.cores in (1, 2)
        else:
            conductors_ok = c.cores in (1, 3, 4)
        if not conductors_ok:
            continue
        for r in [runs] if runs is not None else range(1, family.max_runs + 1):
            out.append(CableOption(c, r, base * r))
    return sorted(out, key=lambda o: (o.cost, o.r_eff, o.cable.id))

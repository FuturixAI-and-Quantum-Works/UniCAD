"""Rating factors for the ambient temperature a cable is installed in, from the cable manufacturer's own tables (the ones its catalog ampacities are
stated against), and the total derating an edge takes: temperature x grouping or installation."""

from dataclasses import dataclass

from unicad.domain.catalog import CableSpec
from unicad.domain.network import Laying
from unicad.rules.grouping import HAVELLS, KEI, MULTICORE, SINGLE_CORE, grouping

POLYCAB_LT = "references/catalog-sources/raw/datasheets/cables/Polycab_LT_Cable_Catalog_V9.pdf"
POLYCAB_DC = "references/catalog-sources/extracted/polycab_solar_dc_en50618_derating.csv (Polycab H1Z2Z2-K datasheet p.2)"


@dataclass(frozen=True)
class TempTable:
    what: str
    where: str
    reference_c: float
    factors: dict[float, float]

    def at(self, t_c: float) -> tuple[float, float] | None:
        """(factor, tabulated temperature) at the next tabulated temperature at or above `t_c`; None above the table."""
        at = min((k for k in self.factors if k >= t_c - 1e-9), default=None)
        return (self.factors[at], at) if at is not None else None


POLYCAB_AIR = TempTable("ambient air temperature, cables in free air, XLPE 90 degC", f"Polycab LT cable catalogue V9 {POLYCAB_LT} p.87", 40.0,
                        {25: 1.14, 30: 1.10, 35: 1.05, 40: 1.00, 45: 0.95, 50: 0.89, 55: 0.84, 60: 0.77})  # fmt: skip
POLYCAB_GROUND = TempTable("ground temperature, cables laid direct or in duct, XLPE 90 degC", f"Polycab LT cable catalogue V9 {POLYCAB_LT} p.87", 30.0,
                           {15: 1.12, 20: 1.08, 25: 1.04, 30: 1.00, 35: 0.96, 40: 0.91, 45: 0.87, 50: 0.82})  # fmt: skip
HAVELLS_AIR = TempTable("ambient air temperature, XLPE", f"Havells industrial cables catalogue {HAVELLS} pdf p.52 table i)", 40.0,
                        {20: 1.20, 25: 1.16, 30: 1.11, 35: 1.06, 40: 1.00, 45: 0.95, 50: 0.88, 55: 0.82})  # fmt: skip
HAVELLS_GROUND = TempTable("ground temperature, XLPE", f"Havells industrial cables catalogue {HAVELLS} pdf p.52 table ii)", 30.0,
                           {15: 1.12, 20: 1.08, 25: 1.04, 30: 1.00, 35: 0.96, 40: 0.91, 45: 0.87, 50: 0.82})  # fmt: skip
KEI_AIR = TempTable("ambient air temperature, XLPE 90 degC", f"KEI Technical Handbook {KEI} p.19 (printed 35) rating factors 1a)", 40.0,
                    {25: 1.16, 30: 1.11, 35: 1.06, 40: 1.00, 45: 0.94, 50: 0.88, 55: 0.81, 60: 0.74})  # fmt: skip
KEI_GROUND = TempTable("ground temperature, cables laid direct, XLPE 90 degC", f"KEI Technical Handbook {KEI} p.19 (printed 35) rating factors 1b)", 30.0,
                       {15: 1.12, 20: 1.08, 25: 1.04, 30: 1.00, 35: 0.96, 40: 0.91, 45: 0.87, 50: 0.82})  # fmt: skip
SOLAR_AIR = TempTable("ambient temperature, EN 50618 solar cable in free air (120 degC conductor)", POLYCAB_DC, 60.0, {60: 1.00, 70: 0.92, 80: 0.84, 90: 0.75})

GROUND_ASSUMED = (
    "The design basis gives no ground temperature: buried cables are rated at the table's reference ground temperature (30 degC, factor 1.0); "
    "assumption, confirm from site data."
)
NO_UPRATING = "Below the table's reference temperature no factor above 1.0 is taken (design choice, conservative)."
BURIED = frozenset({Laying.BURIED_DIRECT, Laying.BURIED_DUCT, Laying.CONDUIT})


def temperature_table(cable: CableSpec, laying: Laying) -> TempTable:
    buried = laying in BURIED
    if cable.service == "dc_solar":
        return SOLAR_AIR
    if cable.manufacturer == "Polycab":
        return POLYCAB_GROUND if buried else POLYCAB_AIR
    if cable.manufacturer == "Havells":
        return HAVELLS_GROUND if buried else HAVELLS_AIR
    return KEI_GROUND if buried else KEI_AIR


@dataclass(frozen=True)
class Derating:
    factor: float
    temperature: float
    group: float
    source: str
    notes: tuple[str, ...] = ()


def derating(cable: CableSpec, laying: Laying, runs: int, dc: bool, ambient_max_c: float | None) -> Derating:
    """Temperature factor at the design maximum ambient (air) or the reference ground temperature (buried), times the group or installation factor
    (rules.grouping). Each part says where it comes from."""
    parts, notes = [], []
    k_t = 1.0
    if ambient_max_c is not None:
        table = temperature_table(cable, laying)
        t = table.reference_c if laying in BURIED and table is not SOLAR_AIR else ambient_max_c
        hit = table.at(t)
        if hit is None:
            k_t = min(table.factors.values())
            parts.append(f"temperature factor {k_t:g}: {t:g} degC is above the table ({table.what}, {table.where}); its last value is used (assumption)")
        else:
            k_t = min(hit[0], 1.0)
            parts.append(f"temperature factor {k_t:g} at {t:g} degC ({hit[1]:g} degC column, {table.what}, {table.where})")
            if hit[0] > 1.0:
                notes.append(NO_UPRATING)
        if laying in BURIED and table is not SOLAR_AIR:
            notes.append(GROUND_ASSUMED)
    g = grouping(cable, laying, runs, dc)
    k_g = g.factor if g is not None and g.factor is not None else 1.0
    if g is not None:
        parts.append(g.source)
    elif laying is Laying.TRENCH and not dc:
        table = (MULTICORE if cable.cores > 1 else SINGLE_CORE)[Laying.TRENCH]
        k_g = table.rows[1][1]
        parts.append(f"installation factor {k_g:g} for one circuit of {table.what}, {table.where}")
    return Derating(round(k_t * k_g, 4), k_t, k_g, "; ".join(parts), tuple(notes))

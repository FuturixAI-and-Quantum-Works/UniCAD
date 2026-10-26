"""Rule thresholds that are not in the design basis. Each is common practice, not a cited limit, and says so in the findings that use it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Threshold:
    name: str
    value: float
    unit: str
    assumption: str


ISC_FACTOR = Threshold(
    "isc_factor",
    1.25,
    "",
    "Worst-case PV current taken as 1.25 x Isc(STC), following IEC 62548 practice; the factor is not verified against the standard text.",
)
CONTINUOUS_FACTOR = Threshold(
    "continuous_factor",
    1.25,
    "",
    "Protective devices on inverter outputs are sized for 1.25 x maximum inverter current (continuous-duty practice; not taken from a cited standard).",
)
DC_AC_BAND = Threshold(
    "dc_ac_band",
    0.10,
    "",
    "DC/AC ratio is accepted within +/-10 % of the design-basis target (illustrative band, not a cited limit).",
)
VOLTAGE_TOLERANCE = Threshold(
    "voltage_tolerance",
    0.05,
    "",
    "Equipment nominal voltages within 5 % of the zone voltage are treated as compatible (illustrative tolerance; confirm the equipment's operating range).",
)
STC_IRRADIANCE = Threshold("stc_irradiance", 1000.0, "W/m2", "Maximum cell temperature is evaluated at 1000 W/m2 plane-of-array irradiance.")
STC_TEMP = Threshold("stc_temp", 25.0, "degC", "Module datasheet values are at STC cell temperature 25 degC.")

K_XLPE_CU = Threshold(
    "k_xlpe_cu",
    143.0,
    "A s^0.5/mm2",
    "Adiabatic constant k of a copper conductor with XLPE insulation (90 degC to 250 degC), 0.143 kA s^0.5/mm2 in the KEI Technical Handbook, short circuit rating table; "
    "the Polycab XLPE catalogue gives the same 1 s ratings (143.1 kA for 1000 mm2). Manufacturer value, standard name IS 7098.",
)
K_XLPE_AL = Threshold(
    "k_xlpe_al",
    94.0,
    "A s^0.5/mm2",
    "Adiabatic constant k of an aluminium conductor with XLPE insulation (90 degC to 250 degC), 0.094 kA s^0.5/mm2 in the KEI Technical Handbook, short circuit rating table "
    "(Polycab's 1 s table gives 94.5 kA for 1000 mm2; the lower value is used). Manufacturer value, standard name IS 7098.",
)
FAULT_CLEARING_DEFAULT = Threshold(
    "fault_clearing_default",
    1.0,
    "s",
    "Fault clearing time taken as 1 s, the duration cable and switchgear short-circuit ratings are tabulated for, when neither the fault level nor the earthing basis gives a clearing time.",
)

ALL = (ISC_FACTOR, CONTINUOUS_FACTOR, DC_AC_BAND, VOLTAGE_TOLERANCE, STC_IRRADIANCE, STC_TEMP, K_XLPE_CU, K_XLPE_AL, FAULT_CLEARING_DEFAULT)

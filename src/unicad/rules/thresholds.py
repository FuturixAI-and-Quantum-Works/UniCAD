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

ALL = (ISC_FACTOR, CONTINUOUS_FACTOR, DC_AC_BAND, VOLTAGE_TOLERANCE, STC_IRRADIANCE, STC_TEMP)

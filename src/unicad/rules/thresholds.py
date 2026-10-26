"""Rule thresholds that are not in the design basis. Each is common practice, not a cited limit, and says so in the findings that use it.

A company can set its own value and source text in its profile; `using` makes those the values every `Threshold` below reads
for the duration of one rule run, so rule code keeps reading `ISC_FACTOR.value` and `ISC_FACTOR.assumption`.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Setting:
    """A company's value for one threshold and the text that justifies it."""

    value: float
    source: str


_active: ContextVar[Mapping[str, Setting]] = ContextVar("unicad_thresholds", default={})


class Threshold:
    def __init__(self, name: str, value: float, unit: str, assumption: str, lo: float | None = None, hi: float | None = None):
        self.name, self.default_value, self.unit, self.default_assumption = name, value, unit, assumption
        self.lo, self.hi = lo, hi

    @property
    def setting(self) -> Setting | None:
        return _active.get().get(self.name)

    @property
    def value(self) -> float:
        s = self.setting
        return self.default_value if s is None else s.value

    @property
    def assumption(self) -> str:
        s = self.setting
        return self.default_assumption if s is None else f"Company standard: {s.source} (value {s.value:g}{' ' + self.unit if self.unit else ''})."

    def check(self, value: float) -> None:
        if (self.lo is not None and value < self.lo) or (self.hi is not None and value > self.hi):
            raise ValueError(f"threshold {self.name!r} = {value:g} is outside the accepted range {self.lo:g}..{self.hi:g}")

    def __repr__(self) -> str:
        return f"Threshold({self.name!r}, {self.value!r})"


ISC_FACTOR = Threshold(
    "isc_factor",
    1.25,
    "",
    "Worst-case PV current taken as 1.25 x Isc(STC), following IEC 62548 practice; the factor is not verified against the standard text.",
    1.0,
    3.0,
)
CONTINUOUS_FACTOR = Threshold(
    "continuous_factor",
    1.25,
    "",
    "Protective devices on inverter outputs are sized for 1.25 x maximum inverter current (continuous-duty practice; not taken from a cited standard).",
    1.0,
    3.0,
)
DC_AC_BAND = Threshold(
    "dc_ac_band",
    0.10,
    "",
    "DC/AC ratio is accepted within +/-10 % of the design-basis target (illustrative band, not a cited limit).",
    0.0,
    1.0,
)
VOLTAGE_TOLERANCE = Threshold(
    "voltage_tolerance",
    0.05,
    "",
    "Equipment nominal voltages within 5 % of the zone voltage are treated as compatible (illustrative tolerance; confirm the equipment's operating range).",
    0.0,
    0.5,
)
STC_IRRADIANCE = Threshold("stc_irradiance", 1000.0, "W/m2", "Maximum cell temperature is evaluated at 1000 W/m2 plane-of-array irradiance.", 100.0, 1500.0)
STC_TEMP = Threshold("stc_temp", 25.0, "degC", "Module datasheet values are at STC cell temperature 25 degC.", 0.0, 60.0)

K_XLPE_CU = Threshold(
    "k_xlpe_cu",
    143.0,
    "A s^0.5/mm2",
    "Adiabatic constant k of a copper conductor with XLPE insulation (90 degC to 250 degC), 0.143 kA s^0.5/mm2 in the KEI Technical Handbook, short circuit rating table; "
    "the Polycab XLPE catalogue gives the same 1 s ratings (143.1 kA for 1000 mm2). Manufacturer value, standard name IS 7098.",
    50.0,
    250.0,
)
K_XLPE_AL = Threshold(
    "k_xlpe_al",
    94.0,
    "A s^0.5/mm2",
    "Adiabatic constant k of an aluminium conductor with XLPE insulation (90 degC to 250 degC), 0.094 kA s^0.5/mm2 in the KEI Technical Handbook, short circuit rating table "
    "(Polycab's 1 s table gives 94.5 kA for 1000 mm2; the lower value is used). Manufacturer value, standard name IS 7098.",
    30.0,
    200.0,
)
FAULT_CLEARING_DEFAULT = Threshold(
    "fault_clearing_default",
    1.0,
    "s",
    "Fault clearing time taken as 1 s, the duration cable and switchgear short-circuit ratings are tabulated for, when neither the fault level nor the earthing basis gives a clearing time.",
    0.01,
    10.0,
)

ALL = (ISC_FACTOR, CONTINUOUS_FACTOR, DC_AC_BAND, VOLTAGE_TOLERANCE, STC_IRRADIANCE, STC_TEMP, K_XLPE_CU, K_XLPE_AL, FAULT_CLEARING_DEFAULT)

BY_NAME = {t.name: t for t in ALL}


def validate(settings: Mapping[str, Setting]) -> None:
    unknown = sorted(set(settings) - set(BY_NAME))
    if unknown:
        raise ValueError(f"unknown thresholds {unknown}; known: {sorted(BY_NAME)}")
    for name, s in settings.items():
        BY_NAME[name].check(s.value)
        if not s.source.strip():
            raise ValueError(f"threshold {name!r} needs a source text")


@contextmanager
def using(settings: Mapping[str, Setting] | None) -> Iterator[None]:
    """Make `settings` the threshold values for everything run inside the block."""
    settings = settings or {}
    validate(settings)
    token = _active.set(dict(settings))
    try:
        yield
    finally:
        _active.reset(token)

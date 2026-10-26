"""String sizing from catalog module and inverter specs and the design basis, using the rules engine's temperature models."""

import math

from pydantic import BaseModel, ConfigDict

from unicad.domain.catalog import InverterSpec, ModuleSpec
from unicad.domain.project import DesignBasis
from unicad.rules.pv import cell_temp_max, cell_temp_min, string_voltage
from unicad.rules.thresholds import ISC_FACTOR


class DesignError(ValueError):
    """The intent cannot be met with the catalog and design basis; the message says what blocks."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StringVoltages(_Frozen):
    voc_cold_v: float
    vmp_cold_v: float
    vmp_hot_v: float | None
    voc_hot_v: float | None
    cell_min_c: float
    cell_max_c: float | None


class StringDesign(_Frozen):
    """Modules per string and strings per inverter for one module/inverter pair.

    `binding` names the constraint that set each quantity; `margins` are limit minus value (positive is inside the limit).
    """

    module: str
    inverter: str
    modules_per_string_min: int
    modules_per_string_max: int
    modules_per_string: int
    voltages: StringVoltages
    strings_per_mppt_max: int | None
    strings_per_inverter_max: int
    strings_per_inverter: int
    dc_kwp_per_inverter: float
    dc_ac_ratio: float
    dc_ac_ratio_target: float
    binding: dict[str, str]
    margins: dict[str, float]
    assumptions: tuple[str, ...] = ()

    @property
    def string_kwp(self) -> float:
        return self.dc_kwp_per_inverter / self.strings_per_inverter


def _v(module: ModuleSpec, n: int, which: str, cell) -> float:
    return string_voltage(module, n, which, cell).value


def _max_n(ok, hi: int = 200) -> int:
    n = 0
    while n < hi and ok(n + 1):
        n += 1
    return n


def _min_n(ok, hi: int = 200) -> int | None:
    return next((n for n in range(1, hi + 1) if ok(n)), None)


def strings_cap(module: ModuleSpec, inverter: InverterSpec) -> tuple[int | None, int, str, list[str]]:
    """(strings per MPPT cap, strings per inverter cap, what binds, notes). Soft operating-current caps bind only when they are below the hard caps."""
    notes: list[str] = []
    isc = ISC_FACTOR.value * module.isc
    per_string = inverter.topology != "central" and inverter.max_short_circuit_current_per_mppt is not None and inverter.inputs_per_mppt is not None
    if per_string:
        caps = {
            "inputs_per_mppt": inverter.inputs_per_mppt,
            "max_short_circuit_current_per_mppt": math.floor(inverter.max_short_circuit_current_per_mppt / isc),
        }
        per_mppt = min(caps.values())
        why = min(caps, key=lambda k: (caps[k], k))
        if per_mppt < 1:
            raise DesignError(f"{inverter.id}: one string of {module.id} exceeds the per-MPPT limit {why} ({caps[why]} strings fit)")
        return per_mppt, per_mppt * inverter.mppt_count, f"{why} x mppt_count", notes
    caps: dict[str, int] = {}
    if inverter.max_dc_short_circuit_current is not None:
        caps["max_dc_short_circuit_current"] = math.floor(inverter.max_dc_short_circuit_current / isc)
    elif inverter.max_dc_input_current is not None:
        caps["max_dc_input_current"] = math.floor(inverter.max_dc_input_current / module.imp)
    if inverter.topology != "central" and inverter.dc_input_count is not None:
        caps["dc_input_count"] = inverter.dc_input_count
    if not caps:
        raise DesignError(f"{inverter.id}: the catalog gives no DC current limit to size strings against")
    notes.append(
        f"{inverter.id} is a central inverter fed through string combiner boxes; strings are limited on its whole-inverter DC current ratings."
        if inverter.topology == "central"
        else f"{inverter.id} gives DC current limits for the whole inverter only; strings are limited on the totals."
    )
    total = min(caps.values())
    return None, total, min(caps, key=lambda k: (caps[k], k)), notes


def operating_cap(module: ModuleSpec, inverter: InverterSpec) -> int | None:
    """Most strings per inverter before the operating current Imp exceeds the inverter's input current rating (a clipping warning, not a limit)."""
    if inverter.topology != "central" and inverter.max_input_current_per_mppt is not None:
        return math.floor(inverter.max_input_current_per_mppt / module.imp) * inverter.mppt_count
    if inverter.max_dc_input_current is not None:
        return math.floor(inverter.max_dc_input_current / module.imp)
    return None


def size_strings(module: ModuleSpec, inverter: InverterSpec, basis: DesignBasis, modules_per_string: int | None = None) -> StringDesign:
    """Longest string inside every voltage limit, and the strings per inverter nearest the design DC/AC target within the current limits.

    Upper bound: N x Voc at the coldest cell <= min(inverter dc_max_voltage, module max_system_voltage) (rule pv_strings.voc_max) and
    N x Vmp there <= mppt_max (pv_strings.vmp_max). Lower bound: N x Vmp at the hottest cell >= mppt_min (pv_strings.vmp_min) and
    N x Voc there >= start_voltage (pv_strings.start_voltage). Temperatures come from rules.pv.cell_temp_min / cell_temp_max.
    """
    cold = cell_temp_min(basis)
    hot = cell_temp_max(basis, module)
    assumptions = list(dict.fromkeys((*cold.assumptions, *(hot.assumptions if hot else ()))))
    v_limit = min(inverter.dc_max_voltage, module.max_system_voltage)
    n_voc = _max_n(lambda n: _v(module, n, "voc", cold) <= v_limit)
    n_mppt_hi = _max_n(lambda n: _v(module, n, "vmp", cold) <= inverter.mppt_max)
    upper = {"voc_max": n_voc, "vmp_max": n_mppt_hi}
    lower: dict[str, int] = {}
    if hot is None:
        assumptions.append("No NOCT for the hot-cell model: the MPPT-minimum and start-voltage bounds are not applied.")
    else:
        if (n := _min_n(lambda n: _v(module, n, "vmp", hot) >= inverter.mppt_min)) is not None:
            lower["vmp_min"] = n
        if inverter.start_voltage is not None and (n := _min_n(lambda n: _v(module, n, "voc", hot) >= inverter.start_voltage)) is not None:
            lower["start_voltage"] = n
    n_max = min(upper.values())
    n_min = max(lower.values(), default=1)
    binding = {"modules_per_string_max": min(upper, key=lambda k: (upper[k], k)), "modules_per_string_min": max(lower, key=lambda k: (lower[k], k)) if lower else "none"}
    if n_min > n_max:
        raise DesignError(
            f"no string length of {module.id} fits {inverter.id}: the voltage limits need at least {n_min} ({binding['modules_per_string_min']}) "
            f"and at most {n_max} ({binding['modules_per_string_max']}) modules in series"
        )
    if modules_per_string is None:
        n = n_max
        binding["modules_per_string"] = "longest string inside the limits"
    elif not n_min <= modules_per_string <= n_max:
        raise DesignError(f"modules_per_string {modules_per_string} is outside the allowed range {n_min}..{n_max} for {module.id} on {inverter.id}")
    else:
        n = modules_per_string
        binding["modules_per_string"] = "requested"

    per_mppt, s_max, why, notes = strings_cap(module, inverter)
    assumptions += notes
    string_kw = n * module.pmax / 1000.0
    target = basis.dc_ac_ratio_target
    s = max(1, min(s_max, round(target * inverter.ac_rated_kva / string_kw)))
    binding["strings_per_inverter_max"] = why
    binding["strings_per_inverter"] = "dc_ac_ratio_target" if s < s_max else why
    clip = operating_cap(module, inverter)
    if clip is not None and s > clip:
        binding["operating_current"] = f"above {clip} strings the inverter current-limits at high irradiance (pv_strings.operating_current warning); kept for the DC/AC target"

    voltages = StringVoltages(
        voc_cold_v=round(_v(module, n, "voc", cold), 2),
        vmp_cold_v=round(_v(module, n, "vmp", cold), 2),
        vmp_hot_v=round(_v(module, n, "vmp", hot), 2) if hot else None,
        voc_hot_v=round(_v(module, n, "voc", hot), 2) if hot else None,
        cell_min_c=round(cold.value, 2),
        cell_max_c=round(hot.value, 2) if hot else None,
    )
    margins = {"voc_max_v": round(v_limit - voltages.voc_cold_v, 2), "vmp_max_v": round(inverter.mppt_max - voltages.vmp_cold_v, 2)}
    if voltages.vmp_hot_v is not None:
        margins["vmp_min_v"] = round(voltages.vmp_hot_v - inverter.mppt_min, 2)
    if voltages.voc_hot_v is not None and inverter.start_voltage is not None:
        margins["start_voltage_v"] = round(voltages.voc_hot_v - inverter.start_voltage, 2)
    margins["strings_per_inverter"] = s_max - s
    return StringDesign(
        module=module.id,
        inverter=inverter.id,
        modules_per_string_min=n_min,
        modules_per_string_max=n_max,
        modules_per_string=n,
        voltages=voltages,
        strings_per_mppt_max=per_mppt,
        strings_per_inverter_max=s_max,
        strings_per_inverter=s,
        dc_kwp_per_inverter=round(s * string_kw, 3),
        dc_ac_ratio=round(s * string_kw / inverter.ac_rated_kva, 4),
        dc_ac_ratio_target=target,
        binding=binding,
        margins=margins,
        assumptions=tuple(dict.fromkeys(assumptions)),
    )

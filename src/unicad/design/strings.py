"""String sizing from catalog module and inverter specs and the design basis, using the rules engine's temperature models."""

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    strings_per_mppt_unfused: int | None = None
    strings_per_inverter_unfused: int | None = None
    string_fuses: str = "not_needed"
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


def unfused_per_mppt(module: ModuleSpec) -> int | None:
    """Most strings in parallel on one MPPT without a fuse per string (pv_strings.string_fuses): two, or more while the reverse current
    (Np - 1) x 1.25 x Isc stays within the module's maximum series fuse rating. None when the module gives no maximum series fuse."""
    if module.max_series_fuse is None:
        return None
    n = 2
    while (n * ISC_FACTOR.value * module.isc) <= module.max_series_fuse and n < 200:
        n += 1
    return n


def fuses_needed(module: ModuleSpec, strings_per_mppt: int) -> bool:
    limit = unfused_per_mppt(module)
    return limit is not None and strings_per_mppt > limit


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
    want = round(target * inverter.ac_rated_kva / string_kw)
    unfused = unfused_per_mppt(module) if per_mppt is not None else None
    s_free = min(s_max, unfused * inverter.mppt_count) if unfused is not None else s_max
    s = max(1, min(s_free, want))
    binding["strings_per_inverter_max"] = why
    binding["strings_per_inverter"] = "dc_ac_ratio_target" if s < s_free else ("string_fuses (unfused limit)" if s_free < s_max else why)
    fuses = "not_needed"
    if unfused is not None and unfused < per_mppt:
        fused_s = max(1, min(s_max, want))
        if fused_s > s_free:
            fuses = "optional"
            assumptions.append(
                f"String fuses: up to {unfused} strings per MPPT ({s_free} per inverter, {s_free * string_kw:.2f} kWp, DC/AC {s_free * string_kw / inverter.ac_rated_kva:.3f}) need no fuse, since the reverse "
                f"current (Np - 1) x {ISC_FACTOR.value:g} x {module.isc:g} A stays within the {module.max_series_fuse:g} A max series fuse of {module.id} (pv_strings.string_fuses). The DC/AC target "
                f"needs {fused_s} per inverter ({-(-fused_s // inverter.mppt_count)} per MPPT): pass strings_per_inverter={fused_s} to network_autobuild for a design with one gPV fuse per string "
                f"in a string fuse box at the inverter (more parts and fuse losses, fewer inverters), or keep {s} without fuses."
            )
    elif unfused is None and per_mppt is not None and per_mppt > 2:
        assumptions.append(f"{module.id} gives no max series fuse: whether strings above two per MPPT need fuses is not checked (pv_strings.string_fuses warns).")
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
        strings_per_mppt_unfused=unfused,
        strings_per_inverter_unfused=s_free if unfused is not None else None,
        string_fuses=fuses,
        dc_kwp_per_inverter=round(s * string_kw, 3),
        dc_ac_ratio=round(s * string_kw / inverter.ac_rated_kva, 4),
        dc_ac_ratio_target=target,
        binding=binding,
        margins=margins,
        assumptions=tuple(dict.fromkeys(assumptions)),
    )


class InverterStrings(_Frozen):
    """`count` identical inverters whose strings are given per MPPT: `mppts[k]` lists the modules per string of each string on MPPT k + 1,
    e.g. [[18, 19], [18, 19], [18]] is two strings (18 and 19 modules) on MPPT 1 and 2 and one of 18 on MPPT 3."""

    count: int = Field(default=1, gt=0)
    mppts: tuple[tuple[Annotated[int, Field(gt=0)], ...], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_empty_mppt(self) -> "InverterStrings":
        if any(not mp for mp in self.mppts):
            raise ValueError("every listed MPPT needs at least one string; leave unused MPPTs off the end of the list")
        return self

    @property
    def strings(self) -> int:
        return sum(len(mp) for mp in self.mppts)

    @property
    def modules(self) -> int:
        return sum(sum(mp) for mp in self.mppts)

    def lengths(self) -> dict[int, tuple[int, ...]]:
        """Modules per string -> the MPPT (1-based) of each string of that length, in MPPT order; the most common length first."""
        out: dict[int, list[int]] = {}
        for k, mp in enumerate(self.mppts, start=1):
            for n in mp:
                out.setdefault(n, []).append(k)
        return {n: tuple(out[n]) for n in sorted(out, key=lambda n: (-len(out[n]), n))}


class LengthCheck(_Frozen):
    """One string length against the inverter's voltage window: the string voltages and margins at the coldest and hottest cell, or why it does not fit."""

    modules_per_string: int
    fits: bool
    voltages: StringVoltages | None = None
    margins: dict[str, float] = {}
    problem: str | None = None


class InverterStringsCheck(_Frozen):
    count: int
    strings: int
    modules: int
    mppts_used: int
    strings_per_mppt_max: int
    dc_kwp: float
    dc_ac_ratio: float
    string_fuses: bool
    problems: tuple[str, ...] = ()


class MixedStringDesign(_Frozen):
    """Strings given per inverter and MPPT (InverterStrings), checked against the module, the inverter and the design basis: every length's Voc at the coldest
    cell and MPPT window (as size_strings), the MPPT count, strings per MPPT against the per-MPPT current limit, and the DC/AC ratio per inverter class."""

    module: str
    inverter: str
    lengths: tuple[LengthCheck, ...]
    inverters: tuple[InverterStringsCheck, ...]
    inverter_count: int
    modules: int
    dc_kwp: float
    ac_kva: float
    dc_ac_ratio: float
    problems: tuple[str, ...]
    assumptions: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def check_inverter_strings(module: ModuleSpec, inverter: InverterSpec, basis: DesignBasis, classes: tuple[InverterStrings, ...] | list[InverterStrings]) -> MixedStringDesign:
    """Check strings given by hand, with per-inverter and per-MPPT string lengths that may differ (e.g. 18 and 19 modules on one inverter)."""
    hot = cell_temp_max(basis, module)
    lengths, problems = [], []
    assumptions = list(dict.fromkeys((*cell_temp_min(basis).assumptions, *(hot.assumptions if hot else ()))))
    for n in sorted({n for c in classes for mp in c.mppts for n in mp}):
        try:
            d = size_strings(module, inverter, basis, n)
        except DesignError as e:
            lengths.append(LengthCheck(modules_per_string=n, fits=False, problem=str(e)))
            problems.append(f"strings of {n} modules: {e}")
            continue
        lengths.append(LengthCheck(modules_per_string=n, fits=True, voltages=d.voltages, margins={k: v for k, v in d.margins.items() if k.endswith("_v")}))
    per_mppt, s_max, why, notes = strings_cap(module, inverter)
    assumptions += notes
    unfused = unfused_per_mppt(module)
    checks = []
    for i, c in enumerate(classes, start=1):
        own = []
        if len(c.mppts) > inverter.mppt_count:
            own.append(f"lists {len(c.mppts)} MPPTs; {inverter.id} has {inverter.mppt_count}")
        widest = max(len(mp) for mp in c.mppts)
        if per_mppt is not None and widest > per_mppt:
            own.append(f"puts {widest} strings on one MPPT; {inverter.id} takes at most {per_mppt} ({why.split(' x ')[0]})")
        if c.strings > s_max:
            own.append(f"has {c.strings} strings; {inverter.id} takes at most {s_max} ({why})")
        kwp = c.modules * module.pmax / 1000
        fused = unfused is not None and widest > unfused
        problems += [f"inverter class {i} ({c.count} x {c.strings} strings) {p}" for p in own]
        checks.append(InverterStringsCheck(
            count=c.count, strings=c.strings, modules=c.modules, mppts_used=len(c.mppts), strings_per_mppt_max=widest, dc_kwp=round(kwp, 3),
            dc_ac_ratio=round(kwp / inverter.ac_rated_kva, 4), string_fuses=fused, problems=tuple(own),
        ))  # fmt: skip
    mixed_mppt = sorted({n for c in classes for mp in c.mppts if len(set(mp)) > 1 for n in mp})
    if mixed_mppt:
        assumptions.append(
            f"Strings of different length ({', '.join(map(str, mixed_mppt))} modules) share an MPPT as the brief gives them: the MPPT runs them at one voltage, so the "
            "shorter strings work off their maximum power point (a mismatch loss the energy yield must account for); each length is checked on its own voltage window."
        )
    n_inv = sum(c.count for c in classes)
    modules = sum(c.count * c.modules for c in classes)
    dc = modules * module.pmax / 1000
    ac = n_inv * inverter.ac_rated_kva
    return MixedStringDesign(
        module=module.id, inverter=inverter.id, lengths=tuple(lengths), inverters=tuple(checks), inverter_count=n_inv, modules=modules,
        dc_kwp=round(dc, 3), ac_kva=round(ac, 3), dc_ac_ratio=round(dc / ac, 4), problems=tuple(problems), assumptions=tuple(dict.fromkeys(assumptions)),
    )  # fmt: skip

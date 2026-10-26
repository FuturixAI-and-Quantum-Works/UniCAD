"""PV string sizing against inverter and module limits."""

from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.project import Project
from unicad.rules.model import Finding, Quantity, Severity
from unicad.rules.pv import cell_temp_max, cell_temp_min, string_voltage
from unicad.rules.registry import rule
from unicad.rules.thresholds import ISC_FACTOR
from unicad.rules.topology import EVEN_MPPT, PASS_THROUGH_NOTE, STRING_FUSE_HOLDERS, label, string_feeds

IEC_62548 = "IEC 62548"
TOTALS_ONLY = "The catalog gives this limit for the whole inverter only (no per-MPPT value), so the check is made on the inverter total."


@rule("pv_strings.voc_max", "String Voc at minimum temperature within DC limits", clause=IEC_62548)
def voc_max(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """N x Voc at the coldest cell temperature must not exceed the lower of the inverter's maximum DC input voltage and the module's maximum system voltage. Exceeding it can destroy the inverter input stage."""
    net, basis = project.network, project.design_basis
    for f in string_feeds(project, catalog):
        v = string_voltage(f.module, f.n_series, "voc", cell_temp_min(basis))
        limit = min(f.spec.dc_max_voltage, f.module.max_system_voltage)
        if v.value <= limit:
            continue
        yield Finding(
            rule_id="pv_strings.voc_max",
            severity=Severity.ERROR,
            subjects=f.subjects(),
            message=f"{f.where(net)}: Voc at {basis.ambient_min_c:g} degC = {v.value:.1f} V exceeds {limit:g} V (min of inverter dc_max_voltage {f.spec.dc_max_voltage:g} V, module max_system_voltage {f.module.max_system_voltage:g} V)",
            measured=round(v.value, 2),
            limit=limit,
            unit="V",
            formula=f"{v.formula} <= min(dc_max_voltage, max_system_voltage)",
            inputs=(*v.inputs, Quantity(name="dc_max_voltage", value=f.spec.dc_max_voltage, unit="V", source=f.spec.id), Quantity(name="max_system_voltage", value=f.module.max_system_voltage, unit="V", source=f.module.id)),
            assumptions=v.assumptions,
        )


def _hot(project: Project, f) -> tuple:
    cell = cell_temp_max(project.design_basis, f.module)
    if cell is None:
        return None, Finding(
            rule_id="pv_strings.hot_cell",
            severity=Severity.WARNING,
            subjects=f.subjects(),
            message=f"{f.where(project.network)}: no NOCT in the design basis or module spec; hot-weather string voltages not checked",
        )
    return cell, None


@rule("pv_strings.hot_cell", "Maximum cell temperature can be computed")
def hot_cell(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """The NOCT thermal model needs a NOCT value from the design basis or the module spec. Without it the hot-weather checks are skipped and this warning is raised."""
    for f in string_feeds(project, catalog):
        _, missing = _hot(project, f)
        if missing is not None:
            yield missing


@rule("pv_strings.vmp_min", "String Vmp at maximum temperature above MPPT minimum", clause=IEC_62548)
def vmp_min(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """N x Vmp at the hottest cell temperature (thermal model at ambient_max_c, 1000 W/m2) should stay at or above the inverter's minimum MPPT voltage, or the inverter leaves MPP tracking on hot days and energy is lost."""
    net = project.network
    for f in string_feeds(project, catalog):
        cell, _ = _hot(project, f)
        if cell is None:
            continue
        v = string_voltage(f.module, f.n_series, "vmp", cell)
        if v.value >= f.spec.mppt_min:
            continue
        yield Finding(
            rule_id="pv_strings.vmp_min",
            severity=Severity.WARNING,
            subjects=f.subjects(),
            message=f"{f.where(net)}: Vmp at {cell.value:.1f} degC cell = {v.value:.1f} V is below MPPT minimum {f.spec.mppt_min:g} V",
            measured=round(v.value, 2),
            limit=f.spec.mppt_min,
            unit="V",
            formula=f"{v.formula} >= mppt_min",
            inputs=(*v.inputs, Quantity(name="mppt_min", value=f.spec.mppt_min, unit="V", source=f.spec.id)),
            assumptions=v.assumptions,
        )


@rule("pv_strings.start_voltage", "String Voc at maximum temperature above inverter start voltage")
def start_voltage(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """An inverter starts when its open-circuit DC voltage exceeds its start voltage. N x Voc at the hottest cell temperature below the start voltage means the inverter may not start: error. Not checked when the catalog has no start voltage."""
    net = project.network
    for f in string_feeds(project, catalog):
        cell, _ = _hot(project, f)
        if cell is None or f.spec.start_voltage is None:
            continue
        v = string_voltage(f.module, f.n_series, "voc", cell)
        if v.value >= f.spec.start_voltage:
            continue
        yield Finding(
            rule_id="pv_strings.start_voltage",
            severity=Severity.ERROR,
            subjects=f.subjects(),
            message=f"{f.where(net)}: Voc at {cell.value:.1f} degC cell = {v.value:.1f} V is below start voltage {f.spec.start_voltage:g} V",
            measured=round(v.value, 2),
            limit=f.spec.start_voltage,
            unit="V",
            formula=f"{v.formula} >= start_voltage",
            inputs=(*v.inputs, Quantity(name="start_voltage", value=f.spec.start_voltage, unit="V", source=f.spec.id)),
            assumptions=v.assumptions,
        )


@rule("pv_strings.vmp_max", "String Vmp at minimum temperature below MPPT maximum", clause=IEC_62548)
def vmp_max(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """N x Vmp at the coldest cell temperature should not exceed the inverter's maximum MPPT voltage, or the inverter clips at the top of its window on cold bright days."""
    net, basis = project.network, project.design_basis
    for f in string_feeds(project, catalog):
        v = string_voltage(f.module, f.n_series, "vmp", cell_temp_min(basis))
        if v.value <= f.spec.mppt_max:
            continue
        yield Finding(
            rule_id="pv_strings.vmp_max",
            severity=Severity.WARNING,
            subjects=f.subjects(),
            message=f"{f.where(net)}: Vmp at {basis.ambient_min_c:g} degC = {v.value:.1f} V is above MPPT maximum {f.spec.mppt_max:g} V",
            measured=round(v.value, 2),
            limit=f.spec.mppt_max,
            unit="V",
            formula=f"{v.formula} <= mppt_max",
            inputs=(*v.inputs, Quantity(name="mppt_max", value=f.spec.mppt_max, unit="V", source=f.spec.id)),
            assumptions=v.assumptions,
        )


def _per_inverter(feeds) -> list[list]:
    by_inv: dict[str, list] = {}
    for f in feeds:
        by_inv.setdefault(f.inverter.id, []).append(f)
    return list(by_inv.values())


def _total_inputs(fs) -> tuple[Quantity, ...]:
    out = [Quantity(name="mppt_count", value=fs[0].spec.mppt_count, source=fs[0].spec.id)]
    for f in fs:
        out.append(Quantity(name=f"strings_per_inverter[{f.string.id}]", value=f.strings_per_inverter, source="network"))
    return tuple(out)


def _strings_text(fs) -> str:
    return " + ".join(f"{f.strings_per_inverter} x {f.string.id}" for f in fs)


def _subjects(fs) -> tuple[str, ...]:
    return (*dict.fromkeys(f.string.id for f in fs), fs[0].inverter.id)


@rule("pv_strings.inputs", "Inverter has enough DC inputs")
def inputs(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Parallel DC inputs into an inverter (strings landing directly, or SCB outputs) must fit mppt_count x inputs_per_mppt. Inverters whose catalog entry gives only a total (central inverters) are checked against dc_input_count."""
    net = project.network
    for fs in _per_inverter(string_feeds(project, catalog)):
        f = fs[0]
        if f.spec.inputs_per_mppt is not None:
            cap = f.spec.mppt_count * f.spec.inputs_per_mppt
            text = f"{f.spec.mppt_count} MPPT x {f.spec.inputs_per_mppt} inputs = {cap}"
            formula = "dc_inputs <= mppt_count x inputs_per_mppt"
            caps = (Quantity(name="mppt_count", value=f.spec.mppt_count, source=f.spec.id), Quantity(name="inputs_per_mppt", value=f.spec.inputs_per_mppt, source=f.spec.id))
        elif f.spec.dc_input_count is not None:
            cap = f.spec.dc_input_count
            text = f"{cap} DC inputs in total"
            formula = "dc_inputs <= dc_input_count"
            caps = (Quantity(name="dc_input_count", value=cap, source=f.spec.id),)
        else:
            continue
        if f.inverter_inputs <= cap:
            continue
        yield Finding(
            rule_id="pv_strings.inputs",
            severity=Severity.ERROR,
            subjects=(f.inverter.id,),
            message=f"inverter {f.inverter.id} ({label(net, f.inverter.id)}, {f.spec.model}) receives {f.inverter_inputs} DC inputs but has {text}",
            measured=f.inverter_inputs,
            limit=cap,
            unit="inputs",
            formula=formula,
            inputs=(Quantity(name="dc_inputs", value=f.inverter_inputs, source="network"), *caps),
        )


def _isc_mppt(f) -> tuple[float, tuple[Quantity, ...]]:
    return f.strings_per_mppt * ISC_FACTOR.value * f.module.isc, (
        *f.layout_inputs(),
        Quantity(name="k_Isc", value=ISC_FACTOR.value, source=f"threshold.{ISC_FACTOR.name}"),
        Quantity(name="Isc", value=f.module.isc, unit="A", source=f.module.id),
    )


@rule("pv_strings.isc_per_mppt", "Short-circuit current per MPPT within inverter rating", clause=IEC_62548)
def isc_per_mppt(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Strings per MPPT x 1.25 x module Isc must not exceed the inverter's maximum short-circuit current per MPPT. When the catalog gives only the whole-inverter value (central inverters), all strings into the inverter x 1.25 x Isc are checked against max_dc_short_circuit_current. Not checked when the catalog has neither."""
    net = project.network
    for fs in _per_inverter(string_feeds(project, catalog)):
        spec = fs[0].spec
        if spec.max_short_circuit_current_per_mppt is None:
            lim = spec.max_dc_short_circuit_current
            if lim is None:
                continue
            i = sum(f.strings_per_inverter * ISC_FACTOR.value * f.module.isc for f in fs)
            if i <= lim:
                continue
            yield Finding(
                rule_id="pv_strings.isc_per_mppt",
                severity=Severity.ERROR,
                subjects=_subjects(fs),
                message=f"inverter {fs[0].inverter.id} ({label(net, fs[0].inverter.id)}, {spec.model}): ({_strings_text(fs)}) strings x {ISC_FACTOR.value:g} x Isc = {i:.1f} A exceeds max DC short-circuit current {lim:g} A",
                measured=round(i, 2),
                limit=lim,
                unit="A",
                formula="sum(strings_per_inverter x k_Isc x Isc) <= max_dc_short_circuit_current",
                inputs=(
                    *_total_inputs(fs),
                    Quantity(name="k_Isc", value=ISC_FACTOR.value, source=f"threshold.{ISC_FACTOR.name}"),
                    *(Quantity(name=f"Isc[{f.string.id}]", value=f.module.isc, unit="A", source=f.module.id) for f in fs),
                    Quantity(name="max_dc_short_circuit_current", value=lim, unit="A", source=spec.id),
                ),
                assumptions=(ISC_FACTOR.assumption, TOTALS_ONLY),
            )
            continue
        for f in fs:
            i, qs = _isc_mppt(f)
            lim = f.spec.max_short_circuit_current_per_mppt
            if i <= lim:
                continue
            yield Finding(
                rule_id="pv_strings.isc_per_mppt",
                severity=Severity.ERROR,
                subjects=f.subjects(),
                message=f"{f.where(net)}: {f.strings_per_mppt} strings per MPPT x {ISC_FACTOR.value:g} x {f.module.isc:g} A = {i:.1f} A exceeds max short-circuit current per MPPT {lim:g} A",
                measured=round(i, 2),
                limit=lim,
                unit="A",
                formula="strings_per_mppt x k_Isc x Isc <= max_short_circuit_current_per_mppt",
                inputs=(*qs, Quantity(name="max_short_circuit_current_per_mppt", value=lim, unit="A", source=f.spec.id)),
                assumptions=(ISC_FACTOR.assumption, EVEN_MPPT),
            )


@rule("pv_strings.operating_current", "Operating current per MPPT within inverter input rating")
def operating_current(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Strings per MPPT x module Imp above the inverter's maximum input current per MPPT means the inverter current-limits (clips) at high irradiance: warning, not a safety limit. When the catalog gives only the whole-inverter value, all strings into the inverter x Imp are checked against max_dc_input_current."""
    net = project.network
    for fs in _per_inverter(string_feeds(project, catalog)):
        spec = fs[0].spec
        if spec.max_input_current_per_mppt is None:
            lim = spec.max_dc_input_current
            i = sum(f.strings_per_inverter * f.module.imp for f in fs)
            if lim is None or i <= lim:
                continue
            yield Finding(
                rule_id="pv_strings.operating_current",
                severity=Severity.WARNING,
                subjects=_subjects(fs),
                message=f"inverter {fs[0].inverter.id} ({label(net, fs[0].inverter.id)}, {spec.model}): ({_strings_text(fs)}) strings x Imp = {i:.1f} A exceeds max DC input current {lim:g} A; the inverter will current-limit",
                measured=round(i, 2),
                limit=lim,
                unit="A",
                formula="sum(strings_per_inverter x Imp) <= max_dc_input_current",
                inputs=(
                    *_total_inputs(fs),
                    *(Quantity(name=f"Imp[{f.string.id}]", value=f.module.imp, unit="A", source=f.module.id) for f in fs),
                    Quantity(name="max_dc_input_current", value=lim, unit="A", source=spec.id),
                ),
                assumptions=(TOTALS_ONLY,),
            )
            continue
        for f in fs:
            i = f.strings_per_mppt * f.module.imp
            lim = f.spec.max_input_current_per_mppt
            if i <= lim:
                continue
            yield Finding(
                rule_id="pv_strings.operating_current",
                severity=Severity.WARNING,
                subjects=f.subjects(),
                message=f"{f.where(net)}: {f.strings_per_mppt} strings per MPPT x Imp {f.module.imp:g} A = {i:.1f} A exceeds max input current per MPPT {lim:g} A; the inverter will current-limit",
                measured=round(i, 2),
                limit=lim,
                unit="A",
                formula="strings_per_mppt x Imp <= max_input_current_per_mppt",
                inputs=(*f.layout_inputs(), Quantity(name="Imp", value=f.module.imp, unit="A", source=f.module.id), Quantity(name="max_input_current_per_mppt", value=lim, unit="A", source=f.spec.id)),
                assumptions=(EVEN_MPPT,),
            )


@rule("pv_strings.string_fuses", "Parallel strings have overcurrent protection where reverse current can exceed the module rating", clause=IEC_62548)
def string_fuses(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """With Np strings in parallel, a faulted string can receive (Np - 1) x 1.25 x Isc from the others. When Np > 2 and that exceeds the module's maximum series fuse rating, each string needs a fuse: error if the path from string to inverter has no fuse or fuse box. An SCB on the path is taken to hold string fuses (info, stated as an assumption)."""
    net = project.network
    for f in string_feeds(project, catalog):
        np_ = f.strings_per_mppt
        if np_ <= 2:
            continue
        reverse = (np_ - 1) * ISC_FACTOR.value * f.module.isc
        qs = (
            *f.layout_inputs(),
            Quantity(name="k_Isc", value=ISC_FACTOR.value, source=f"threshold.{ISC_FACTOR.name}"),
            Quantity(name="Isc", value=f.module.isc, unit="A", source=f.module.id),
        )
        if f.module.max_series_fuse is None:
            yield Finding(
                rule_id="pv_strings.string_fuses",
                severity=Severity.WARNING,
                subjects=f.subjects(),
                message=f"{f.where(net)}: {np_} strings in parallel but the module spec has no max_series_fuse; reverse-current protection not checked",
                inputs=qs,
            )
            continue
        if reverse <= f.module.max_series_fuse:
            continue
        common = {
            "measured": round(reverse, 2),
            "limit": f.module.max_series_fuse,
            "unit": "A",
            "formula": "(Np - 1) x k_Isc x Isc <= max_series_fuse, Np = strings_per_mppt",
            "inputs": (*qs, Quantity(name="max_series_fuse", value=f.module.max_series_fuse, unit="A", source=f.module.id)),
        }
        head = f"{f.where(net)}: reverse current ({np_} - 1) x {ISC_FACTOR.value:g} x {f.module.isc:g} A = {reverse:.1f} A exceeds module max series fuse {f.module.max_series_fuse:g} A"
        holders = [n for n in f.path if net.node(n).kind in STRING_FUSE_HOLDERS]
        if holders:
            continue
        if f.combiner is not None:
            yield Finding(
                rule_id="pv_strings.string_fuses",
                severity=Severity.INFO,
                subjects=(*f.subjects(), f.combiner.id),
                message=f"{head}; string fuses assumed inside {f.combiner.id} ({f.combiner.kind})",
                assumptions=(ISC_FACTOR.assumption, EVEN_MPPT, PASS_THROUGH_NOTE, f"{f.combiner.id} is assumed to contain one fuse per string; model the fuses to have their rating checked."),
                **common,
            )
            continue
        yield Finding(
            rule_id="pv_strings.string_fuses",
            severity=Severity.ERROR,
            subjects=f.subjects(),
            message=f"{head}, and no string fuse is modelled between string and inverter",
            assumptions=(ISC_FACTOR.assumption, EVEN_MPPT, PASS_THROUGH_NOTE),
            **common,
        )

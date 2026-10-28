"""DC/AC ratio per inverter and plant, and AC capacity against the grid connection."""

from collections import defaultdict
from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.network import NodeKind
from unicad.domain.project import Project
from unicad.rules.model import Calc, Finding, Quantity, Severity
from unicad.rules.registry import rule
from unicad.rules.thresholds import DC_AC_BAND
from unicad.rules.topology import GENERATORS, inverter_spec, label, string_feeds

AC_IS_KVA = "AC capacity taken as inverter rated kVA (unity power factor)."
EXPORT_LIMIT_ATTR = "export_limit_kva"


def string_inputs(spec) -> int | None:
    """DC string inputs of an inverter: the catalog total, else MPPTs x inputs per MPPT."""
    if spec.dc_input_count is not None:
        return spec.dc_input_count
    return spec.mppt_count * spec.inputs_per_mppt if spec.inputs_per_mppt is not None else None


def _full(project: Project, catalog: Catalog) -> dict[str, str]:
    """For inverters whose every string input is used, why their DC/AC ratio cannot rise: the largest DC they take."""
    out = {}
    for f in string_feeds(project, catalog):
        cap = string_inputs(f.spec) if f.spec.topology != "central" else None
        if cap is None or f.strings_per_inverter < cap:
            continue
        kwp = cap * f.n_series * f.module.pmax / 1000
        out[f.inverter.id] = (
            f"every one of the {cap} string inputs is used ({cap} x {f.n_series} x {f.module.pmax:g} Wp = {kwp:g} kWp), so the ratio cannot rise with this module "
            "and string length: the band needs longer strings, a higher-power module, an inverter with more DC inputs or a lower dc_ac_ratio_target"
        )
    return out


def _dc_kwp(project: Project, catalog: Catalog) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for f in string_feeds(project, catalog):
        out[f.inverter.id] += f.strings_per_inverter * f.n_series * f.module.pmax / 1000.0
    return out


def _band_finding(rule_id: str, subjects: tuple[str, ...], what: str, ratio: Calc, target: float, why_low: str = "") -> Finding | None:
    lo, hi = target * (1 - DC_AC_BAND.value), target * (1 + DC_AC_BAND.value)
    if lo <= ratio.value <= hi:
        return None
    side, limit = ("below", lo) if ratio.value < lo else ("above", hi)
    why = f"; {why_low}" if why_low and side == "below" else ""
    return Finding(
        rule_id=rule_id,
        severity=Severity.WARNING,
        subjects=subjects,
        message=f"{what}: DC/AC ratio {ratio.value:.3f} is {side} the design band {lo:.3f}..{hi:.3f} (target {target:g}){why}",
        measured=round(ratio.value, 4),
        limit=round(limit, 4),
        unit="",
        formula=f"{ratio.formula}; target x (1 - band) <= ratio <= target x (1 + band)",
        inputs=(*ratio.inputs, Quantity(name="target", value=target, source="design_basis.dc_ac_ratio_target"), Quantity(name="band", value=DC_AC_BAND.value, source=f"threshold.{DC_AC_BAND.name}")),
        assumptions=(*ratio.assumptions, DC_AC_BAND.assumption),
    )


@rule("ratio.dc_ac_inverter", "DC/AC ratio per inverter within the design band")
def dc_ac_inverter(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """DC kWp feeding one inverter (strings x modules in series x Pmax) over its rated AC kVA, compared with the design basis dc_ac_ratio_target. Outside the band is a warning."""
    net = project.network
    dc = _dc_kwp(project, catalog)
    full = _full(project, catalog)
    for inv in net.by_kind(*GENERATORS):
        spec = inverter_spec(catalog, inv)
        if spec is None or inv.id not in dc:
            continue
        ratio = Calc(
            value=dc[inv.id] / spec.ac_rated_kva,
            unit="",
            formula="ratio = sum(strings x N x Pmax) / 1000 / ac_rated_kva",
            inputs=(Quantity(name="P_dc", value=round(dc[inv.id], 3), unit="kWp", source="network"), Quantity(name="ac_rated_kva", value=spec.ac_rated_kva, unit="kVA", source=spec.id)),
            assumptions=(AC_IS_KVA,),
        )
        if (f := _band_finding("ratio.dc_ac_inverter", (inv.id,), f"inverter {inv.id} ({label(net, inv.id)}, {spec.model})", ratio, project.design_basis.dc_ac_ratio_target, full.get(inv.id, ""))) is not None:
            yield f


def plant_capacity(project: Project, catalog: Catalog) -> tuple[float, float]:
    """(DC kWp, AC kVA) of the whole plant from instance counts."""
    net = project.network
    dc = _dc_kwp(project, catalog)
    total_dc = sum(kwp * net.instance_count(inv) for inv, kwp in dc.items())
    total_ac = sum(spec.ac_rated_kva * net.instance_count(inv.id) for inv in net.by_kind(*GENERATORS) if (spec := inverter_spec(catalog, inv)) is not None)
    return total_dc, total_ac


@rule("ratio.dc_ac_plant", "Plant DC/AC ratio within the design band")
def dc_ac_plant(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Total DC kWp over total rated inverter AC kVA, compared with the design basis dc_ac_ratio_target. Outside the band is a warning."""
    dc, ac = plant_capacity(project, catalog)
    if not dc or not ac:
        return
    ratio = Calc(
        value=dc / ac,
        unit="",
        formula="ratio = P_dc_total / S_ac_total",
        inputs=(Quantity(name="P_dc_total", value=round(dc, 3), unit="kWp", source="network"), Quantity(name="S_ac_total", value=ac, unit="kVA", source="network")),
        assumptions=(AC_IS_KVA,),
    )
    full = _full(project, catalog)
    gens = [g.id for g in project.network.by_kind(*GENERATORS) if inverter_spec(catalog, g) is not None]
    why = next(iter(full.values())) if gens and all(g in full for g in gens) else ""
    if (f := _band_finding("ratio.dc_ac_plant", (), f"plant {project.id} ({dc:.1f} kWp / {ac:g} kVA)", ratio, project.design_basis.dc_ac_ratio_target, why)) is not None:
        yield f


BESS_KW_ASKED, BESS_KWH_ASKED = "bess_kw_requested", "bess_kwh_requested"
BESS_INFO_BAND = 0.01


@rule("ratio.bess_intent", "BESS power and energy as asked")
def bess_intent(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """When PCS nodes carry the requested storage size (bess_kw_requested / bess_kwh_requested, set by network_autobuild), the built PCS kVA and battery kWh are
    compared with it, because whole catalog units rarely add up to the request exactly: a difference above 1 % in either is a warning that states it, a smaller one an info finding."""
    net = project.network
    pcs = [n for n in net.by_kind(NodeKind.PCS) if n.spec is not None]
    asked_kw = next((n.attributes.get(BESS_KW_ASKED) for n in pcs if n.attributes.get(BESS_KW_ASKED)), None)
    asked_kwh = next((n.attributes.get(BESS_KWH_ASKED) for n in pcs if n.attributes.get(BESS_KWH_ASKED)), None)
    if not asked_kw and not asked_kwh:
        return
    kw = sum(getattr(catalog.get(n.spec), "rated_kva", 0.0) * net.instance_count(n.id) for n in pcs)
    kwh = sum(getattr(catalog.get(n.spec), "energy_kwh", 0.0) * net.instance_count(n.id) for n in net.by_kind(NodeKind.BATTERY) if n.spec is not None)
    parts, inputs, worst = [], [], 0.0
    for what, got, asked, unit in (("power", kw, asked_kw, "kVA"), ("energy", kwh, asked_kwh, "kWh")):
        if not asked:
            continue
        inputs += [Quantity(name=f"{what}_built", value=round(got, 1), unit=unit, source="network"), Quantity(name=f"{what}_asked", value=asked, unit=unit, source="network_autobuild intent")]
        if abs(got / asked - 1) > 1e-6:
            worst = max(worst, abs(got / asked - 1))
            parts.append(f"{what} {got:g} {unit} against {asked:g} asked ({got / asked - 1:+.1%})")
    if parts:
        yield Finding(
            rule_id="ratio.bess_intent", severity=Severity.WARNING if worst > BESS_INFO_BAND else Severity.INFO, subjects=tuple(n.id for n in pcs[:1]),
            message=f"BESS built with whole catalog units differs from the request: {'; '.join(parts)}; check the project title and the contracted storage size",
            formula="built = n_pcs x rated_kva, n_batteries x energy_kwh", inputs=tuple(inputs),
        )  # fmt: skip


EXPORT_CONTROL_ATTR = "export_control"
PPC = (
    "Export control declared (POI export_control = ppc): a power plant controller measures the export at the point of interconnection and curtails the PV inverters "
    "and the BESS PCS so the export stays within the limit; its make, set-points and the DISCOM's acceptance are to be confirmed (assumption)."
)


def export_sources(project: Project, catalog: Catalog) -> tuple[float, float]:
    """(PV inverter kVA, BESS PCS kVA) installed, from instance counts."""
    net = project.network
    _, pv = plant_capacity(project, catalog)
    pcs = sum(getattr(catalog.get(n.spec), "rated_kva", 0.0) * net.instance_count(n.id) for n in net.by_kind(NodeKind.PCS) if n.spec is not None)
    return pv, pcs


@rule("ratio.export_limit", "Plant AC capacity within the grid connection limit")
def export_limit(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """When a poi_grid node carries an `export_limit_kva` attribute (sanctioned or contracted export capacity), the AC capacity that can export through it, rated
    PV inverter kVA plus BESS PCS kVA (a discharging battery exports like the PV), is compared with it. Above it is a warning: the plant needs export control or a
    smaller AC capacity. With `export_control` = "ppc" on the POI (a declared power plant controller limiting the export), the excess is an info finding that states
    the controller assumption. Within the limit is an info finding giving the sum."""
    net = project.network
    pv, pcs = export_sources(project, catalog)
    ac = pv + pcs
    sizes = {spec.ac_rated_kva for inv in net.by_kind(*GENERATORS) if (spec := inverter_spec(catalog, inv)) is not None}
    for poi in net.by_kind(NodeKind.POI_GRID):
        limit = poi.attributes.get(EXPORT_LIMIT_ATTR)
        if not isinstance(limit, (int, float)) or isinstance(limit, bool):
            continue
        parts = f"PV inverters {pv:g} kVA" + (f" + BESS PCS {pcs:g} kVA" if pcs else "")
        common = {
            "subjects": (poi.id,), "measured": ac, "limit": float(limit), "unit": "kVA", "formula": "sum(n_inverters x ac_rated_kva) + sum(n_pcs x rated_kva) <= export_limit_kva",
            "inputs": (Quantity(name="S_pv", value=pv, unit="kVA", source="network"), Quantity(name="S_pcs", value=pcs, unit="kVA", source="network"),
                       Quantity(name=EXPORT_LIMIT_ATTR, value=limit, unit="kVA", source=f"network.{poi.id}.attributes")),
        }  # fmt: skip
        if ac <= limit:
            yield Finding(rule_id="ratio.export_limit", severity=Severity.INFO, message=f"{parts} = {ac:g} kVA within the {poi.tag} export limit {limit:g} kVA", assumptions=(AC_IS_KVA,), **common)
            continue
        if poi.attributes.get(EXPORT_CONTROL_ATTR) == "ppc":
            yield Finding(
                rule_id="ratio.export_limit", severity=Severity.INFO,
                message=f"{parts} = {ac:g} kVA is above the {poi.tag} export limit {limit:g} kVA by {ac - limit:g} kVA; the declared power plant controller limits the export",
                assumptions=(AC_IS_KVA, PPC), **common,
            )  # fmt: skip
            continue
        fit = f"; {'without the BESS, ' if pcs else ''}at most {int(limit // next(iter(sizes)))} x {next(iter(sizes)):g} kVA inverters fit" if len(sizes) == 1 else ""
        yield Finding(
            rule_id="ratio.export_limit",
            severity=Severity.WARNING,
            message=f"AC capacity that can export, {parts} = {ac:g} kVA, exceeds the {poi.tag} export limit {limit:g} kVA by {ac - limit:g} kVA ({ac / limit - 1:.1%}){fit}; "
            "export control (network_autobuild export_control='ppc': a power plant controller) or a smaller AC capacity is needed",
            assumptions=(AC_IS_KVA,),
            **common,
        )

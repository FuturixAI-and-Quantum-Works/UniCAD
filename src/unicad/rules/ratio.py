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


def _dc_kwp(project: Project, catalog: Catalog) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for f in string_feeds(project, catalog):
        out[f.inverter.id] += f.strings_per_inverter * f.n_series * f.module.pmax / 1000.0
    return out


def _band_finding(rule_id: str, subjects: tuple[str, ...], what: str, ratio: Calc, target: float) -> Finding | None:
    lo, hi = target * (1 - DC_AC_BAND.value), target * (1 + DC_AC_BAND.value)
    if lo <= ratio.value <= hi:
        return None
    side, limit = ("below", lo) if ratio.value < lo else ("above", hi)
    return Finding(
        rule_id=rule_id,
        severity=Severity.WARNING,
        subjects=subjects,
        message=f"{what}: DC/AC ratio {ratio.value:.3f} is {side} the design band {lo:.3f}..{hi:.3f} (target {target:g})",
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
        if (f := _band_finding("ratio.dc_ac_inverter", (inv.id,), f"inverter {inv.id} ({label(net, inv.id)}, {spec.model})", ratio, project.design_basis.dc_ac_ratio_target)) is not None:
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
    if (f := _band_finding("ratio.dc_ac_plant", (), f"plant {project.id} ({dc:.1f} kWp / {ac:g} kVA)", ratio, project.design_basis.dc_ac_ratio_target)) is not None:
        yield f


@rule("ratio.export_limit", "Plant AC capacity within the grid connection limit")
def export_limit(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """When a poi_grid node carries an `export_limit_kva` attribute (sanctioned or contracted export capacity), total rated inverter AC kVA above it is a warning: the plant needs export limiting or a smaller AC capacity."""
    net = project.network
    _, ac = plant_capacity(project, catalog)
    for poi in net.by_kind(NodeKind.POI_GRID):
        limit = poi.attributes.get(EXPORT_LIMIT_ATTR)
        if not isinstance(limit, (int, float)) or isinstance(limit, bool) or ac <= limit:
            continue
        yield Finding(
            rule_id="ratio.export_limit",
            severity=Severity.WARNING,
            subjects=(poi.id,),
            message=f"plant AC capacity {ac:g} kVA exceeds the {poi.tag} export limit {limit:g} kVA; export control or a smaller AC capacity is needed",
            measured=ac,
            limit=float(limit),
            unit="kVA",
            formula="sum(n_inverters x ac_rated_kva) <= export_limit_kva",
            inputs=(Quantity(name="S_ac_total", value=ac, unit="kVA", source="network"), Quantity(name=EXPORT_LIMIT_ATTR, value=limit, unit="kVA", source=f"network.{poi.id}.attributes")),
            assumptions=(AC_IS_KVA,),
        )

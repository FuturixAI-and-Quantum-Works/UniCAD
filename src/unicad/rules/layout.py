"""Array layout against what it was asked to hold."""

import math
from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.catalog import ModuleSpec
from unicad.domain.project import Project
from unicad.rules.model import Finding, Quantity, Severity
from unicad.rules.registry import rule
from unicad.rules.thresholds import LAYOUT_SHORTFALL
from unicad.rules.topology import string_feeds


def _inverter_kwp(project: Project, catalog: Catalog) -> float | None:
    """The largest DC capacity on one inverter of the network, kWp, or None before the network has strings on inverters."""
    per: dict[str, float] = {}
    for f in string_feeds(project, catalog):
        per[f.inverter.id] = per.get(f.inverter.id, 0.0) + f.strings_per_inverter * f.n_series * f.module.pmax / 1000
    return max(per.values(), default=None)


@rule("layout.shortfall", "The placed array reaches the layout_place target")
def shortfall(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """The array placed by layout_place holds whole tables only, so it may fall short of target_dc_kwp by up to one table; once the network has inverters, also by
    the part of the target that is not a whole number of inverters (the layout places whole inverters). A shortfall beyond that plus the layout_shortfall share of
    the target means the site (as imported, less setbacks and keep-outs) does not hold the capacity the brief asks for, and every later step would design a smaller
    plant than asked: enlarge or correct the site, change the table or pitch, or lower the target and record why.

    Worked example: a 1371.6 kWp target on a 150 x 100 m plot that holds 800 kWp in 2 x 18 tables of 635 Wp (22.86 kWp each) falls 571.6 kWp short,
    above 22.86 + 0.02 x 1371.6 = 50.3 kWp, so it is an error."""
    layout = project.array_layout
    if layout is None or layout.target_dc_kwp is None or not layout.blocks:
        return
    placed, largest = 0.0, 0.0
    for t in layout.table_by_id.values():
        spec = catalog.get(t.module)
        if not isinstance(spec, ModuleSpec):
            continue
        placed += t.slot_count * spec.pmax / 1000
        largest = max(largest, t.slot_count * spec.pmax / 1000)
    target = layout.target_dc_kwp
    per_inv = _inverter_kwp(project, catalog)
    whole = math.floor(target / per_inv + 1e-6) * per_inv if per_inv else target
    allowed = (target - whole) + largest + LAYOUT_SHORTFALL.value * target
    short = target - placed
    if short <= allowed + 1e-6:
        return
    rounding = f", the {target - whole:.1f} kWp the target exceeds whole inverters of {per_inv:.2f} kWp" if target - whole > 1e-6 else ""
    yield Finding(
        rule_id="layout.shortfall",
        severity=Severity.ERROR,
        subjects=("array_layout",),
        message=(
            f"the array layout holds {placed:.1f} kWp, {short:.1f} kWp ({short / target:.1%}) below the layout_place target {target:g} kWp; "
            f"at most {allowed:.1f} kWp short is accepted (one {largest:.2f} kWp table{rounding} plus {LAYOUT_SHORTFALL.value:.0%} of the target). "
            "Enlarge or correct the site boundary and setbacks, change the table size or row pitch, or re-run layout_place with a lower target_dc_kwp and record the reduced capacity"
        ),
        measured=round(placed, 3),
        limit=round(target - allowed, 3),
        unit="kWp",
        formula="P_placed >= P_target - ((P_target - floor(P_target / P_inv) x P_inv) + P_table_max + k x P_target)",
        inputs=(
            *((Quantity(name="P_inv", value=round(per_inv, 3), unit="kWp", source="network (largest DC per inverter)"),) if per_inv else ()),
            Quantity(name="P_target", value=target, unit="kWp", source="array_layout.target_dc_kwp"),
            Quantity(name="P_placed", value=round(placed, 3), unit="kWp", source="array_layout"),
            Quantity(name="P_table_max", value=round(largest, 3), unit="kWp", source="array_layout"),
            Quantity(name="k", value=LAYOUT_SHORTFALL.value, source=f"threshold.{LAYOUT_SHORTFALL.name}"),
        ),
        assumptions=(LAYOUT_SHORTFALL.assumption,),
    )

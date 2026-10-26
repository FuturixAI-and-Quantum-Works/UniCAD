"""Lightning protection rule: every table, inverter station and roof lies inside a terminal's protection zone."""

from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.project import Project
from unicad.lps import LpsDesign, design_lps
from unicad.rules.model import Finding, Quantity, Severity
from unicad.rules.registry import rule

_CACHE: list[tuple[Project, LpsDesign]] = []


def lps_for(project: Project) -> LpsDesign | None:
    if project.design_basis.lps is None:
        return None
    for p, d in _CACHE:
        if p is project:
            return d
    d = design_lps(project)
    _CACHE[:] = [*_CACHE[-3:], (project, d)]
    return d


@rule("lps.coverage", "Lightning terminals cover every table, station and roof", clause="IS/IEC 62305; NF C 17-102 for ESE")
def coverage(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Each table centroid and corner, inverter station and (on rooftop projects) roof corner must lie within the protection radius of
    a terminal: the ESE radius Rp of NF C 17-102 (a French standard) from the datasheet value or dT and mast height, or for
    conventional air terminals the rolling-sphere radius of the protection level (IS/IEC 62305). Terminals are placed by a greedy
    set cover over free positions; points left outside every zone are errors. Projects without a lightning basis are not checked."""
    d = lps_for(project)
    if d is None:
        return
    inputs = tuple(Quantity(name=v.name, value=v.value, unit=v.unit, source=v.source) for s in d.steps for v in s.inputs)
    for t in d.uncovered[:200]:
        yield Finding(rule_id="lps.coverage", severity=Severity.ERROR, subjects=(t.id,), message=f"{t.kind} {t.id} lies outside every protection zone (radius {d.radius_m:g} m)",
                      measured=None, limit=d.radius_m, unit="m", inputs=inputs, assumptions=d.assumptions)  # fmt: skip
    if not d.uncovered:
        n = sum(1 for t in d.targets if t.kind == "table")
        yield Finding(
            rule_id="lps.coverage", severity=Severity.INFO, subjects=tuple(t.tag for t in d.terminals[:50]),
            message=f"{len(d.terminals)} {'ESE terminals' if d.method == 'ese' else 'air terminals'} of radius {d.radius_m:g} m cover all {n} tables and {len(d.targets) - n} other points",
            measured=len(d.terminals), unit="terminals", formula=d.steps[-1].formula, inputs=inputs, assumptions=d.assumptions,
        )  # fmt: skip

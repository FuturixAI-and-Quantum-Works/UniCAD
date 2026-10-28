"""Lightning protection rule: every table, station, MV switchyard, BESS unit, string inverter and building lies inside a terminal's protection zone."""

from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.project import Project
from unicad.lps import LpsDesign, design_lps
from unicad.rules.model import Finding, Quantity, Severity
from unicad.rules.registry import rule

_CACHE: list[tuple[Project, LpsDesign]] = []
STEP_SOURCES = {"LPL": "design_basis.lps.level", "dL": "calculated: dL = v x dT with v = 1 m/us (NF C 17-102) from design_basis.lps.ese_delta_t_us"}


def lps_for(project: Project) -> LpsDesign | None:
    if project.design_basis.lps is None:
        return None
    for p, d in _CACHE:
        if p is project:
            return d
    d = design_lps(project)
    _CACHE[:] = [*_CACHE[-3:], (project, d)]
    return d


def coverage_text(d: LpsDesign) -> str:
    return ", ".join(f"{c.label} {c.covered}/{c.total}" for c in d.coverage())


@rule("lps.coverage", "Lightning terminals cover every table, station, switchyard, BESS unit and building", clause="IS/IEC 62305; NF C 17-102 for ESE")
def coverage(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Each table centroid and corner, inverter station, MV switchyard, BESS unit, string inverter and building (roof corners on rooftop projects)
    must lie within the protection radius of a terminal: the ESE radius Rp of NF C 17-102 (a French standard) from the datasheet value or dT and
    mast height, or for conventional air terminals the rolling-sphere radius of the protection level (IS/IEC 62305). The switchyard and each piece
    of placed equipment are covered over an assumed footprint (the catalog has no dimensions); the finding lists covered over total per class.
    Terminals are placed by a greedy set cover over free positions clear of the tables, stations, switchyard and BESS units; points left outside
    every zone are errors. Projects without a lightning basis are not checked."""
    d = lps_for(project)
    if d is None:
        return
    inputs = tuple(Quantity(name=v.name, value=v.value, unit=v.unit, source=v.source or STEP_SOURCES.get(v.name, "calculated by the rule (see formula and docs)")) for s in d.steps for v in s.inputs)
    for t in d.uncovered[:200]:
        yield Finding(rule_id="lps.coverage", severity=Severity.ERROR, subjects=(t.group,), message=f"{t.kind.removesuffix('_corner')} {t.group} lies outside every protection zone (radius {d.radius_m:g} m)",
                      measured=None, limit=d.radius_m, unit="m", inputs=inputs, assumptions=d.assumptions)  # fmt: skip
    if not d.uncovered:
        yield Finding(
            rule_id="lps.coverage", severity=Severity.INFO, subjects=tuple(t.tag for t in d.terminals[:50]),
            message=f"{len(d.terminals)} {'ESE terminals' if d.method == 'ese' else 'air terminals'} of radius {d.radius_m:g} m cover all targets: {coverage_text(d)}",
            measured=len(d.terminals), unit="terminals", formula=d.steps[-1].formula, inputs=inputs, assumptions=d.assumptions,
        )  # fmt: skip


@rule("lps.mast_fence_clearance", "Lightning terminals keep their clearance from the boundary fence")
def mast_fence_clearance(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Every lightning terminal stands at least 3 m (an assumed clearance for the perimeter road and the mast foundation, not a cited
    figure) inside the boundary line, which is the site fence. Terminals are placed by the lightning design inside that margin, so a finding
    means the design had no free position; projects without a lightning basis or a ground boundary are not checked."""
    from shapely.geometry import Point as SPoint

    from unicad.arrays.siting import FENCE_CLEAR_MIN_M
    from unicad.lps.site import boundary, is_rooftop

    d = lps_for(project)
    fence = boundary(project)
    if d is None or fence is None or project.site.geometry is None:
        return
    near = [(t.tag, fence.boundary.distance(SPoint(t.at.x, t.at.y))) for t in d.terminals if not t.on_roof]
    near = [(tag, dist) for tag, dist in near if dist < FENCE_CLEAR_MIN_M - 1e-6]
    for tag, dist in near[:50]:
        yield Finding(
            rule_id="lps.mast_fence_clearance", severity=Severity.WARNING, subjects=(tag,),
            message=f"{tag} stands {dist:.1f} m inside the boundary fence, less than the {FENCE_CLEAR_MIN_M:g} m clearance",
            measured=round(dist, 2), limit=FENCE_CLEAR_MIN_M, unit="m", assumptions=d.assumptions,
        )  # fmt: skip
    if not near and any(not t.on_roof for t in d.terminals) and not is_rooftop(project):
        closest = min(fence.boundary.distance(SPoint(t.at.x, t.at.y)) for t in d.terminals if not t.on_roof)
        yield Finding(
            rule_id="lps.mast_fence_clearance", severity=Severity.INFO, subjects=(),
            message=f"every ground terminal stands at least {closest:.1f} m inside the boundary fence (clearance {FENCE_CLEAR_MIN_M:g} m)",
            measured=round(closest, 2), limit=FENCE_CLEAR_MIN_M, unit="m",
        )  # fmt: skip

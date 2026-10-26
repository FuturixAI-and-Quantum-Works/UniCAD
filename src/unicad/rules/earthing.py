"""Earthing rules: conductor size against the fault, earth resistance against the target, switchyard touch and step voltage, and
that every table and equipment item is bonded to the earth network."""

from collections.abc import Iterator

from unicad.catalog.store import Catalog
from unicad.domain.project import Project
from unicad.earthing import EarthingDesign, Step, design_earthing
from unicad.earthing.layout import BONDED_KINDS
from unicad.earthing.sizing import FLAT_BY_NAME
from unicad.rules.model import Finding, Quantity, Severity
from unicad.rules.registry import rule
from unicad.rules.topology import label

CLAUSE = "IS 3043"


_CACHE: list[tuple[Project, EarthingDesign]] = []


def design_for(project: Project) -> EarthingDesign | None:
    """The project's earthing design, computed once per project object across the rules of one run."""
    if project.design_basis.earthing is None:
        return None
    for p, d in _CACHE:
        if p is project:
            return d
    d = design_earthing(project)
    _CACHE[:] = [*_CACHE[-3:], (project, d)]
    return d


def _inputs(step: Step) -> tuple[Quantity, ...]:
    return tuple(Quantity(name=v.name, value=v.value, unit=v.unit, source=v.source) for v in step.inputs)


@rule("earthing.conductor_size", "Earth conductor carries the earth fault", clause=CLAUSE)
def conductor_size(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """The adiabatic minimum cross-section A = I sqrt(t) / k (k = 80 for galvanised steel, IS 3043) for the design earth fault current and
    clearing time, raised by any corrosion allowance, is compared with the GS flat chosen for each conductor role. The IEEE 80 Kf value
    for zinc-coated steel is reported as a cross-check. Without fault data the size is reported as unverified."""
    d = design_for(project)
    if d is None:
        return
    area = next((s for s in d.steps if s.name == "conductor_area"), None)
    if area is None:
        yield Finding(rule_id="earthing.conductor_size", severity=Severity.WARNING, subjects=(), message=f"no earth fault current and clearing time: the {d.sizes.main} GS flat is not verified")
        return
    cross = next(s for s in d.steps if s.name == "ieee80_area")
    for role, size in (("main", d.sizes.main), ("array", d.sizes.array), ("equipment", d.sizes.equipment)):
        have = FLAT_BY_NAME[size].area_mm2
        ok = have >= area.value
        yield Finding(
            rule_id="earthing.conductor_size", severity=Severity.INFO if ok else Severity.ERROR, subjects=(f"conductor.{role}",),
            message=f"{role} conductor {size} GS flat {have:g} mm2 {'covers' if ok else 'is below'} the {area.value:g} mm2 needed (IEEE 80 cross-check {cross.value:g} mm2)",
            measured=area.value, limit=have, unit="mm2", formula=area.formula, inputs=_inputs(area), assumptions=area.assumptions,
        )  # fmt: skip


@rule("earthing.electrode_resistance", "Earth electrode system meets the target resistance", clause=CLAUSE)
def electrode_resistance(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Each pit's resistance R = 100 rho / (2 pi L) ln(4 L / d) (IS 3043, L and d in cm); pits two electrode lengths apart combine in
    parallel, and in parallel with the buried conductor R = 100 rho / (2 pi L) ln(2 L^2 / (w t)). The total is compared with the
    target resistance, and the pit count with the count the IS 3043 surface current density needs for the fault current."""
    d = design_for(project)
    if d is None:
        return
    ok = d.resistance_ohm <= d.target_ohm
    inputs = _inputs(d.electrode) + (Quantity(name="pits", value=sum(1 for p in d.layout.pits if p.on_ground)),)
    if d.strip is not None:
        inputs += _inputs(d.strip)
    yield Finding(
        rule_id="earthing.electrode_resistance", severity=Severity.INFO if ok else Severity.ERROR, subjects=("earth_network",),
        message=f"earth resistance {d.resistance_ohm:.3g} ohm with {d.pit_count} pits and {sum(r.length_m for r in d.layout.runs if r.buried):,.0f} m of buried conductor "
        f"({'within' if ok else 'above'} the {d.target_ohm:g} ohm target)",
        measured=d.resistance_ohm, limit=d.target_ohm, unit="ohm", formula="R = 1 / (n / R_pit + 1 / R_strip)", inputs=inputs,
        assumptions=tuple(a for a in d.assumptions if "target" in a or "parallel" in a or "electrode" in a),
    )  # fmt: skip
    if d.pits_by_current is not None and d.pit_count < d.pits_by_current:
        yield Finding(
            rule_id="earthing.electrode_resistance", severity=Severity.ERROR, subjects=("earth_network",),
            message=f"{d.pit_count} pits cannot dissipate the earth fault current; IS 3043 current density needs {d.pits_by_current}",
            measured=d.pit_count, limit=d.pits_by_current, unit="pits",
        )  # fmt: skip


@rule("earthing.touch_step", "Switchyard grid keeps touch and step voltage tolerable", clause="IEEE 80")
def touch_step(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """For a project with an MV switchyard and fault data, the IEEE 80 mesh voltage Em = rho Km Ki IG / LM and step voltage
    Es = rho Ks Ki IG / LS of the switchyard grid are compared with the tolerable touch and step voltages for a 50 kg body,
    (1000 + 1.5 Cs rho_s) 0.116 / sqrt(ts) and (1000 + 6 Cs rho_s) 0.116 / sqrt(ts). A ground potential rise below the tolerable
    touch voltage passes without the mesh check."""
    d = design_for(project)
    if d is None or d.grid is None:
        return
    g = d.grid
    for what, got, lim in (("mesh (touch)", g.e_mesh, g.e_touch), ("step", g.e_s, g.e_step)):
        ok = g.gpr <= g.e_touch or got <= lim
        yield Finding(
            rule_id="earthing.touch_step", severity=Severity.INFO if ok else Severity.ERROR, subjects=("switchyard",),
            message=f"switchyard grid {d.grid_spacing_m:g} m mesh: {what} voltage {got:,.0f} V against {lim:,.0f} V tolerable (GPR {g.gpr:,.0f} V)",
            measured=round(got, 1), limit=round(lim, 1), unit="V", formula="Em = rho Km Ki IG / LM; Es = rho Ks Ki IG / LS",
            inputs=(Quantity(name="Rg", value=round(g.rg, 4), unit="ohm"), Quantity(name="Km", value=round(g.km, 4)), Quantity(name="Ki", value=round(g.ki, 4)),
                    Quantity(name="Ks", value=round(g.ks, 4)), Quantity(name="Cs", value=round(g.cs, 4)), Quantity(name="LM", value=round(g.lm, 1), unit="m")),
            assumptions=tuple(a for a in d.assumptions if "grid" in a or "surface" in a or "body" in a or "strip" in a),
        )  # fmt: skip


@rule("earthing.every_equipment_bonded", "Every table and equipment item is bonded to the earth network")
def every_equipment_bonded(project: Project, catalog: Catalog) -> Iterator[Finding]:
    """Every table (MMS) must be crossed by a row conductor, and every inverter, SCB, transformer, panel, switchgear item, arrester and
    lightning terminal must end the number of conductors it needs (two distinct connections for equipment) on a conductor network
    that reaches an earth pit. Projects without an earthing basis are not checked. Network equipment without an earth (pe) connection in the electrical model is reported as a warning."""
    d = design_for(project)
    if d is None:
        return
    need = {it.id: (it.connections, it.tag) for it in d.layout.items} | {t: (1, t) for t in d.layout.tables}
    bad = [(i, n, d.bonded.get(i, 0), tag) for i, (n, tag) in need.items() if d.bonded.get(i, 0) < n]
    for i, n, got, tag in bad[:200]:
        yield Finding(rule_id="earthing.every_equipment_bonded", severity=Severity.ERROR, subjects=(i,), message=f"{tag} has {got} of {n} earth connections", measured=got, limit=n)
    if not bad:
        yield Finding(
            rule_id="earthing.every_equipment_bonded", severity=Severity.INFO, subjects=(),
            message=f"all {len(d.layout.tables)} tables and {len(d.layout.items)} equipment items are bonded to the earth network",
        )  # fmt: skip
    net = project.network
    earthed = {e.src_node for e in net.edges if e.service.value == "earth"} | {e.dst_node for e in net.edges if e.service.value == "earth"}
    for n in net.nodes:
        if n.kind in BONDED_KINDS and n.id not in earthed:
            yield Finding(
                rule_id="earthing.every_equipment_bonded", severity=Severity.WARNING, subjects=(n.id,),
                message=f"{label(net, n.id)} ({n.kind}) has no earth (pe) connection in the electrical network; the earthing layout bonds it at its location",
            )  # fmt: skip

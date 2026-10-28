"""Earthing design for a project: conductor size, electrode count and resistance, the switchyard grid check, the layout and its
quantities. A pure function of the project; nothing is stored beyond the design basis."""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from shapely.geometry import MultiLineString

from unicad.catalog.store import Catalog, load_catalog
from unicad.domain.catalog import InverterSpec
from unicad.domain.network import NodeKind
from unicad.domain.project import EarthingBasis, Project, Segment
from unicad.earthing.layout import MV_KINDS, EarthingLayout, Sizes, bonding, build_layout, equipment
from unicad.earthing.model import ConductorRole, PitRole, Quantity, Step, Value
from unicad.earthing.sizing import (
    FLAT_BY_NAME,
    SOURCES,
    STEEL_KG_PER_M3,
    Grid,
    GridCheck,
    conductor_area,
    electrode_current,
    grid_check,
    ieee80_area,
    parallel,
    pipe_resistance,
    select_flat,
    split_factor,
    strip_current,
    strip_resistance,
    sverak,
)
from unicad.lps.design import LpsDesign, design_lps, is_rooftop
from unicad.lps.site import designated_switchyard, place_switchyard

DEFAULT_SIZE = "50x6"
DOWN_SIZE = "25x3"
ARRAY_MIN_SIZE = "25x6"
GOVERNING_PRACTICE = "equipment and table-row practice (resistance and current density need no more)"
GOVERNING_RESISTANCE = "earth resistance target"
GOVERNING_CURRENT = "IS 3043 current density"
INVERTER_FAULT_FACTOR = 1.5
GRID_SPACING_DEFAULT_M = 6.0
TOL_M = 1e-3
SWITCHYARD_UNKNOWN = (
    "MV switchyard size unknown: set switchyard_m (earthing_design or project_design_basis) or mark the switchyard area on the site; the "
    "switchyard earth grid and its IEEE 80 touch and step check are not done, and the MV equipment is earthed at the plant equipment area"
)
TARGET_PRACTICE = {
    "utility": (1.0, "transformers and substation earthing below 1 ohm"),
    "commercial": (2.0, "commercial premises 2 ohm"),
    "household": (8.0, "household earthing 8 ohm"),
}
TARGET_SOURCE = "earth resistance practice table in J. Parmar, 'How to determine correct number of earthing electrodes, part 2', electrical-engineering-portal.com"
ROLE_TEXT = {
    ConductorRole.MAIN: "MAIN EARTH CONDUCTOR, RINGS AND INTERCONNECTIONS",
    ConductorRole.ARRAY: "MMS / TABLE ROW EARTHING",
    ConductorRole.EQUIPMENT: "EQUIPMENT AND STRUCTURE EARTHING",
    ConductorRole.GRID: "SWITCHYARD EARTH GRID",
    ConductorRole.DOWN: "DOWN CONDUCTOR (ROOF TO PIT / LPS)",
}


class EarthingError(ValueError):
    pass


@dataclass(frozen=True)
class EarthingDesign:
    basis: EarthingBasis
    rooftop: bool
    sizes: Sizes
    steps: tuple[Step, ...]
    electrode: Step
    strip: Step | None
    resistance_ohm: float
    target_ohm: float
    pits_by_resistance: int
    pits_by_current: int | None
    pits_by_practice: int
    pits_governing: str
    array_fault: Step | None
    grid: GridCheck | None
    grid_spacing_m: float | None
    layout: EarthingLayout
    lps: LpsDesign | None
    quantities: tuple[Quantity, ...]
    bonded: dict[str, int]
    assumptions: tuple[str, ...] = field(default=())
    warnings: tuple[str, ...] = field(default=())
    resistance_method: str = ""
    resistance_parts: dict[str, float] = field(default_factory=dict)
    split_factor: float | None = None
    rest_ohm: float | None = None
    switchyard_unsized: bool = False

    @property
    def pit_count(self) -> int:
        return len(self.layout.pits)

    @property
    def unbonded(self) -> tuple[str, ...]:
        need = {it.id: it.connections for it in self.layout.items} | {t: 1 for t in self.layout.tables}
        return tuple(sorted(i for i, n in need.items() if self.bonded.get(i, 0) < n))


@lru_cache(maxsize=1)
def _default_catalog() -> Catalog:
    return load_catalog()


def _array_fault(project: Project, catalog: Catalog, basis: EarthingBasis) -> Step | None:
    """The earth fault current the table-row conductors can carry: the inverters' own contribution (largest of 1.5 x maximum AC output current and
    the DC short-circuit current in the plant), never above the plant fault. The MV earth fault returns through the stations, the main ring and
    the switchyard grid, not along the rows."""
    best: tuple[float, InverterSpec, float, float | None] | None = None
    for n in project.network.by_kind(NodeKind.INVERTER):
        try:
            spec = catalog.get(n.spec) if isinstance(n.spec, str) else n.spec
        except KeyError:
            continue
        if not isinstance(spec, InverterSpec):
            continue
        i_ac = spec.ac_max_kva * 1000 / ((math.sqrt(3) if spec.phases == 3 else 1.0) * spec.ac_voltage)
        i_dc = spec.max_dc_short_circuit_current
        if i_dc is None and spec.max_short_circuit_current_per_mppt is not None:
            i_dc = spec.max_short_circuit_current_per_mppt * spec.mppt_count
        amps = max(INVERTER_FAULT_FACTOR * i_ac, i_dc or 0.0)
        if best is None or amps > best[0]:
            best = (amps, spec, i_ac, i_dc)
    if best is None:
        return None
    amps, spec, i_ac, i_dc = best
    plant = basis.fault_current_ka * 1000
    use = min(amps, plant)
    return Step(
        name="array_fault_current", value=round(use, 1), unit="A", formula=f"I = min(plant earth fault, max({INVERTER_FAULT_FACTOR:g} x I_ac_max, I_dc_sc)) of the largest inverter",
        inputs=(Value("inverter", spec.id, None, "catalog"), Value("I_ac_max", round(i_ac, 1), "A", f"catalog: maximum AC apparent power {spec.ac_max_kva:g} kVA at {spec.ac_voltage:g} V"),
                Value("I_dc_sc", round(i_dc, 1) if i_dc else 0.0, "A", "catalog: maximum DC short-circuit current"),
                Value("factor", INVERTER_FAULT_FACTOR, "", SOURCES["inverter_fault"]), Value("plant earth fault", plant, "A", "design_basis.earthing.fault_current_ka")),
        source=SOURCES["inverter_fault"],
        assumptions=("table-row conductors are sized for the inverter fault contribution, not the MV earth fault, which returns through the stations, the main ring and the switchyard grid (design assumption)",),
    )  # fmt: skip


def _target(project: Project, basis: EarthingBasis) -> tuple[float, str | None]:
    if basis.target_resistance_ohm is not None:
        return basis.target_resistance_ohm, None
    has_tx = any(n.kind in (NodeKind.TRANSFORMER,) for n in project.network.nodes) or project.segment is Segment.UTILITY
    key = "utility" if has_tx else "household" if project.segment is Segment.RESIDENTIAL else "commercial"
    v, why = TARGET_PRACTICE[key]
    return v, f"target earth resistance {v:g} ohm ({why}; {TARGET_SOURCE}); not a cited limit, set target_resistance_ohm to override"


@dataclass(frozen=True)
class Resistance:
    """An earth resistance with how it was found: `parts` are the candidate values, `method` says which one governs and why."""

    value: float
    method: str
    parts: dict[str, float]
    strip: Step | None
    network_limit: float | None


def _measure(lay: EarthingLayout, basis: EarthingBasis, electrode: Step, size: str, yard: tuple[float, float, float, float] | None) -> Resistance:
    """Resistance of the earth network outside the switchyard grid: pits in parallel with the buried strip (IS 3043), bounded below by
    the Sverak (IEEE 80) resistance of the buried network over its convex hull, the value no number of pits can go below."""
    rho = basis.soil_resistivity_ohm_m

    def in_yard(q) -> bool:
        return yard is not None and yard[0] - TOL_M <= q.x <= yard[2] + TOL_M and yard[1] - TOL_M <= q.y <= yard[3] + TOL_M

    buried = [r for r in lay.runs if r.buried and r.role is not ConductorRole.GRID and not all(in_yard(q) for q in r.points)]
    length = sum(r.length_m for r in buried)
    n = sum(1 for p in lay.pits if p.on_ground and not in_yard(p.at))
    width = sum(r.length_m * FLAT_BY_NAME[r.size].width_mm for r in buried) / length if length > 0 else FLAT_BY_NAME[size].width_mm
    strip = strip_resistance(rho, length, width, basis.burial_depth_m) if length > 1 else None
    rp = electrode.value / n if n else math.inf
    ps = parallel(rp, strip.value) if strip else rp
    limit = None
    if len(buried) > 3:
        hull = MultiLineString([[(q.x, q.y) for q in b.points] for b in buried]).convex_hull
        if hull.area > 1:
            limit = rho * (1 / (length + n * basis.electrode_length_m) + 1 / math.sqrt(20 * hull.area) * (1 + 1 / (1 + basis.burial_depth_m * math.sqrt(20 / hull.area))))
    parts = {"pits_and_strip": round(ps, 4)} | ({"network_area_limit": round(limit, 4)} if limit is not None else {})
    if limit is not None and limit >= ps:
        method = "Sverak (IEEE 80) resistance of the buried network over its convex hull area, which is above the pits-and-strip value and governs"
        value = limit
    else:
        method = "pits in parallel with each other and with the buried conductor (IS 3043)"
        value = ps
    return Resistance(value, method, parts, strip, limit)


def _with_grid(rest: Resistance, rg: float) -> Resistance:
    parts = {"switchyard_grid": round(rg, 4), "rest_of_plant": round(rest.value, 4)} | {f"rest_{k}": v for k, v in rest.parts.items()}
    method = f"switchyard grid Rg (Sverak, IEEE 80) in parallel with the rest of the plant earth bonded to it ({rest.method})"
    return Resistance(parallel(rg, rest.value), method, parts, rest.strip, rest.network_limit)


def _strip_capacity(lay: EarthingLayout, rho: float, clearing_s: float) -> Step:
    """IS 3043 dissipation of the buried conductors, each size over its own surface."""
    by_size: dict[str, float] = defaultdict(float)
    for r in lay.runs:
        if r.buried:
            by_size[r.size] += r.length_m
    parts = [strip_current(rho, clearing_s, m, FLAT_BY_NAME[n].width_mm, FLAT_BY_NAME[n].thickness_mm) for n, m in sorted(by_size.items())]
    if not parts:
        return strip_current(rho, clearing_s, 0.0, FLAT_BY_NAME[DEFAULT_SIZE].width_mm, FLAT_BY_NAME[DEFAULT_SIZE].thickness_mm)
    head = parts[0]
    return head.model_copy(update={"value": round(sum(p.value for p in parts), 1), "formula": head.formula + (", summed over the conductor sizes" if len(parts) > 1 else "")})


def _grid(basis: EarthingBasis, size: str, rect: tuple[float, float, float, float], rods: int, spacing: float) -> Grid:
    flat = FLAT_BY_NAME[size]
    return Grid(rect[2] - rect[0], rect[3] - rect[1], spacing, basis.burial_depth_m, flat.width_mm / 2000, rods, basis.electrode_length_m)


def _grid_check(basis: EarthingBasis, g: Grid, r_rest: float) -> tuple[GridCheck, float]:
    """IEEE 80 check with the grid current IG = Sf x the earth fault current, Sf from the grid's own Rg and the rest of the plant."""
    sf = split_factor(sverak(basis.soil_resistivity_ohm_m, g), r_rest)
    c = grid_check(basis.soil_resistivity_ohm_m, g, basis.fault_current_ka * 1000 * sf, basis.fault_clearing_s, basis.surface_resistivity_ohm_m, basis.surface_layer_m)
    return c, sf


def _grid_spacing(basis: EarthingBasis, size: str, rect: tuple[float, float, float, float], rods: int, r_rest: float) -> float:
    """The widest conductor spacing (10 m down to 2 m) whose grid has tolerable touch and step voltages; the finest tried if none has. The grid
    laid, checked and reported is the one this spacing gives (`Grid.mesh` is its widest mesh)."""
    spacing = 10.0
    for tenths in range(100, 19, -5):
        spacing = tenths / 10
        if _grid_check(basis, _grid(basis, size, rect, rods, spacing), r_rest)[0].safe:
            break
    return spacing


def design_earthing(project: Project, basis: EarthingBasis | None = None, lps: LpsDesign | None = None, catalog: Catalog | None = None) -> EarthingDesign:
    basis = basis or project.design_basis.earthing
    if basis is None:
        raise EarthingError("no earthing basis: set design_basis.earthing (earthing_design does)")
    if project.array_layout is None and not project.network.nodes:
        raise EarthingError("nothing to earth: the project has neither an array layout nor a network")
    rho = basis.soil_resistivity_ohm_m
    assumptions: list[str] = [
        f"earth electrode {basis.electrode_diameter_mm:g} mm dia x {basis.electrode_length_m:g} m long (as on the CIL/Refex earthing layout unless set)",
        "pits spaced at least two electrode lengths apart are combined as parallel resistances, in parallel with the buried conductor",
    ]
    warnings: list[str] = []
    steps: list[Step] = []
    fault = basis.fault_current_ka is not None and basis.fault_clearing_s is not None
    if fault:
        area = conductor_area(basis.fault_current_ka, basis.fault_clearing_s, basis.corrosion_allowance)
        cross = ieee80_area(basis.fault_current_ka, basis.fault_clearing_s)
        steps += [area, cross]
        main = select_flat(max(area.value, cross.value)).name
        assumptions.append("conductor sized to the larger of the IS 3043 (k = 80) and IEEE 80 (Kf = 28.96) cross-sections")
        if basis.corrosion_allowance == 0:
            assumptions.append("no corrosion allowance on the conductor cross-section (set corrosion_allowance to add one)")
    else:
        main = DEFAULT_SIZE
        assumptions.append(f"conductor {DEFAULT_SIZE} GS flat: no fault current and clearing time given, so the size is not verified")
    array = main
    array_fault = None
    if fault:
        array_fault = _array_fault(project, catalog or _default_catalog(), basis)
        if array_fault is not None:
            a_area = conductor_area(array_fault.value / 1000, basis.fault_clearing_s, basis.corrosion_allowance).model_copy(update={"name": "array_conductor_area"})
            a_cross = ieee80_area(array_fault.value / 1000, basis.fault_clearing_s).model_copy(update={"name": "array_ieee80_area"})
            thermal = select_flat(max(a_area.value, a_cross.value))
            floor = FLAT_BY_NAME[ARRAY_MIN_SIZE]
            array = max((thermal, floor), key=lambda f: (f.area_mm2, f.width_mm)).name
            array = min(array, main, key=lambda n: FLAT_BY_NAME[n].area_mm2)
            steps += [array_fault, a_area, a_cross]
            assumptions.append(
                f"table-row (MMS) conductors {array} GS flat: the larger of the thermal size for the inverter fault contribution {array_fault.value:,.0f} A and the "
                f"{ARRAY_MIN_SIZE} rail earthing of the CIL/Refex reference layouts, never above the main conductor; main ring, station and switchyard conductors keep "
                f"the plant fault size {main}"
            )
        else:
            assumptions.append("no inverter in the network: table-row conductors are sized for the plant earth fault like the main conductor")
    sizes = Sizes(main=main, array=array, equipment=main, grid=main, down=DOWN_SIZE)
    assumptions.append(f"{DOWN_SIZE} GS flat down conductors (practice; confirm against IS/IEC 62305-3 and NF C 17-102 minimum sections)")
    rooftop = is_rooftop(project)
    if lps is None and project.design_basis.lps is not None:
        lps = design_lps(project)
    electrode = pipe_resistance(rho, basis.electrode_length_m, basis.electrode_diameter_mm)
    target, why = _target(project, basis)
    if why:
        assumptions.append(why)
    _, plant, _ = equipment(project)
    yard = None
    unsized = False
    if not rooftop and any(k in MV_KINDS for _, _, k in plant):
        marked = designated_switchyard(project)
        if basis.switchyard_m is None and marked is None:
            unsized = True
            warnings.append(SWITCHYARD_UNKNOWN)
        else:
            layout = project.array_layout
            if layout is not None and layout.switchyard is not None:
                assumptions.append(f"MV switchyard earth grid centred on the layout's switchyard location ({layout.switchyard.basis})")
            yard, problem = place_switchyard(project, basis.switchyard_m or (0.0, 0.0))
            if problem:
                warnings.append(problem)
            if yard is not None:
                where = "inside the switchyard area marked on the site" if marked is not None else "switchyard_m from the design basis"
                assumptions.append(f"MV switchyard earth grid {yard[2] - yard[0]:.0f} x {yard[3] - yard[1]:.0f} m ({where})")

    def build(extra: int, spacing: float | None, ring: bool) -> EarthingLayout:
        return build_layout(project, sizes, extra, basis.tables_per_pit, basis.electrode_length_m, lps, (yard, spacing) if yard is not None else None, ring=ring)

    def rods(lay: EarthingLayout) -> int:
        return sum(1 for p in lay.pits if yard[0] - TOL_M <= p.at.x <= yard[2] + TOL_M and yard[1] - TOL_M <= p.at.y <= yard[3] + TOL_M)

    per = electrode_current(rho, basis.fault_clearing_s, basis.electrode_length_m, basis.electrode_diameter_mm) if fault else None

    def evaluate(lay: EarthingLayout, spacing: float | None) -> tuple[Resistance, Resistance, float | None, int, int, int, Step | None]:
        rest = _measure(lay, basis, electrode, main, yard)
        rg = sverak(rho, _grid(basis, main, yard, rods(lay), spacing)) if yard is not None and spacing is not None else None
        gov = _with_grid(rest, rg) if rg is not None else rest
        n = sum(1 for p in lay.pits if p.on_ground)
        g_fixed = (1 / rest.strip.value if rest.strip else 0.0) + (1 / rg if rg else 0.0)
        g_need = 1 / target - g_fixed
        need_r = 0 if g_need <= 0 else math.ceil(electrode.value * g_need)
        cap, need_i = None, 0
        if fault:
            cap = _strip_capacity(lay, rho, basis.fault_clearing_s)
            need_i = math.ceil(max(0.0, basis.fault_current_ka * 1000 - cap.value) / per.value)
        return rest, gov, rg, n, need_r, need_i, cap

    spacing = GRID_SPACING_DEFAULT_M if yard is not None else None
    ring = False
    lay = build(0, spacing, ring)
    if yard is not None and fault:
        spacing = _grid_spacing(basis, main, yard, rods(lay), _measure(lay, basis, electrode, main, yard).value)
        lay = build(0, spacing, ring)
    rest, gov, rg, n0, need_r, need_i, cap = evaluate(lay, spacing)
    if rooftop and (gov.value > target or max(need_r, need_i) > n0):
        ring = True
        lay = build(0, spacing, ring)
        rest, gov, rg, n0, need_r, need_i, cap = evaluate(lay, spacing)
        assumptions.append("buried ring earth conductor laid 1.5 m outside each roof carrying an array, inside the plot, with the extra pits on it")
    extra = max(0, need_r - n0, need_i - n0)
    if extra:
        lay = build(extra, spacing, ring)
        rest, gov, rg, n1, _, _, cap = evaluate(lay, spacing)
    if fault:
        steps += [per, cap]
        assumptions.append("IS 3043 surface current density applied to the whole electrode surface in soil: the pits and the buried flat conductor")
    grid, sf = None, None
    if yard is not None and fault:
        grid, sf = _grid_check(basis, _grid(basis, main, yard, rods(lay), spacing), rest.value)
        assumptions += [
            f"switchyard grid current IG = Sf x {basis.fault_current_ka * 1000:,.0f} A with split factor Sf = R_rest / (Rg + R_rest) = {sf:.3f}: the earth fault "
            f"current divides between the switchyard grid (Rg {grid.rg:.3g} ohm) and the rest of the plant earth bonded to it (R_rest {rest.value:.3g} ohm); "
            "mutual resistance between them neglected, decrement factor 1",
            "ground potential rise GPR = IG x Rg (the fault current times the combined earth resistance); mesh and step voltages from the same IG",
            f"flat strip {main} taken as a round conductor of diameter half its width in the IEEE 80 mesh equations",
            "tolerable voltages for a 50 kg body",
        ]
        if basis.surface_resistivity_ohm_m is None:
            assumptions.append("no surface layer (crushed rock) assumed in the tolerable touch and step voltages")
        if grid.e_s > grid.gpr or grid.e_mesh > grid.gpr:
            warnings.append(f"IEEE 80 step {grid.e_s:,.0f} V or mesh {grid.e_mesh:,.0f} V exceeds the GPR {grid.gpr:,.0f} V: the grid geometry is outside the equations' range")
    elif yard is not None:
        assumptions.append(f"switchyard grid conductors {spacing:g} m apart (typical spacing on the CIL/Refex earthmat layout; no fault data to check touch and step voltage)")
    mesh = _grid(basis, main, yard, rods(lay), spacing).mesh if yard is not None else None
    assumptions.append(f"earth resistance: {gov.method}")
    if yard is None and rest.network_limit is not None and rest.network_limit > target:
        warnings.append(f"the buried network's area limits the earth resistance to about {rest.network_limit:.3g} ohm, above the {target:g} ohm target; more pits barely help")
    on_ground = sum(1 for p in lay.pits if p.on_ground)
    needed = max(need_r, need_i)
    governing = GOVERNING_PRACTICE if needed <= n0 else GOVERNING_CURRENT if need_i >= need_r else GOVERNING_RESISTANCE
    if on_ground < max(need_r, need_i):
        warnings.append(f"only {on_ground} pits fit at two electrode lengths apart; {max(need_r, need_i)} are needed")
    warnings += lay.warnings
    bonded = bonding(lay)
    return EarthingDesign(
        basis=basis, rooftop=rooftop, sizes=sizes, steps=tuple(steps), electrode=electrode, strip=gov.strip, resistance_ohm=round(gov.value, 4), target_ohm=target,
        pits_by_resistance=need_r, pits_by_current=need_i if fault else None, pits_by_practice=n0, pits_governing=governing, array_fault=array_fault, grid=grid, grid_spacing_m=mesh,
        layout=lay, lps=lps,
        quantities=quantities(lay, basis, sizes, lps), bonded=bonded, assumptions=tuple(assumptions + list(lps.assumptions if lps else ())), warnings=tuple(warnings),
        resistance_method=gov.method, resistance_parts=gov.parts, split_factor=sf, rest_ohm=round(rest.value, 4) if yard is not None else None,
        switchyard_unsized=unsized,
    )  # fmt: skip


PIT_TEXT = {
    True: "EARTH PIT, {d:g} MM DIA {L:g} M LONG GS PIPE (TREATED)",
    False: "EARTH PIT, {d:g} MM DIA {L:g} M LONG MS ROD (UNTREATED)",
}


def pit_text(role: PitRole, basis: EarthingBasis) -> str:
    return PIT_TEXT[role.treated].format(d=basis.electrode_diameter_mm, L=basis.electrode_length_m)


def quantities(lay: EarthingLayout, basis: EarthingBasis, sizes: Sizes, lps: LpsDesign | None) -> tuple[Quantity, ...]:
    out: list[Quantity] = []
    by_role: dict[PitRole, int] = defaultdict(int)
    for p in lay.pits:
        by_role[p.role] += 1
    for role in PitRole:
        if by_role.get(role):
            out.append(Quantity(item=f"pit.{role.value}", description=f"{pit_text(role, basis)} - {role.value}", quantity=by_role[role], unit="nos"))
    lengths: dict[tuple[str, ConductorRole], float] = defaultdict(float)
    for r in lay.runs:
        lengths[(r.size, r.role)] += r.length_m
    for (size, role), m in sorted(lengths.items(), key=lambda kv: (-FLAT_BY_NAME[kv[0][0]].area_mm2, list(ConductorRole).index(kv[0][1]))):
        out.append(Quantity(item=f"flat.{size}.{role.value}", description=f"{size} MM GS FLAT - {ROLE_TEXT[role]}", quantity=math.ceil(m), unit="m"))
    if lps is not None and lps.terminals:
        what = "ESE LIGHTNING ARRESTER" if lps.method == "ese" else "AIR TERMINAL (ROD)"
        out.append(Quantity(item="lps.terminal", description=f"{what}, PROTECTION RADIUS {lps.radius_m:g} M, WITH MAST", quantity=len(lps.terminals), unit="nos"))
    return tuple(out)


def conductor_mass_kg(lay: EarthingLayout) -> float:
    """Steel mass of all conductors from their cross-sections (galvanising ignored)."""
    return sum(r.length_m * FLAT_BY_NAME[r.size].area_mm2 * 1e-6 * STEEL_KG_PER_M3 for r in lay.runs)


def summary(d: EarthingDesign) -> dict:
    lay = d.layout
    out = {
        "rooftop": d.rooftop,
        "conductor": {"main": d.sizes.main, "array": d.sizes.array, "equipment": d.sizes.equipment, "down": d.sizes.down},
        "array_fault_current_a": d.array_fault.value if d.array_fault is not None else None,
        "conductor_mass_t": round(conductor_mass_kg(lay) / 1000, 1),
        "pits": len(lay.pits),
        "pits_by_role": {r.value: sum(1 for p in lay.pits if p.role is r) for r in PitRole if any(p.role is r for p in lay.pits)},
        "pits_by_practice": d.pits_by_practice,
        "pits_needed_by_resistance": d.pits_by_resistance,
        "pits_needed_by_current": d.pits_by_current,
        "pits_governing_criterion": d.pits_governing,
        "electrode_resistance_ohm": d.electrode.value,
        "resistance_ohm": d.resistance_ohm,
        "resistance_method": d.resistance_method,
        "resistance_parts_ohm": dict(d.resistance_parts),
        "target_ohm": d.target_ohm,
        "tables": len(lay.tables),
        "items": len(lay.items),
        "unbonded": list(d.unbonded[:20]),
        "quantities": [q.model_dump() for q in d.quantities],
        "steps": [s.model_dump() for s in (*d.steps, d.electrode, *([d.strip] if d.strip else []))],
        "assumptions": list(d.assumptions),
        "warnings": list(d.warnings),
        "sources": dict(SOURCES),
    }
    if d.grid is not None:
        g = d.grid
        sy = lay.switchyard
        out["switchyard_grid"] = {
            "size_m": [round(sy.x1 - sy.x0, 3), round(sy.y1 - sy.y0, 3)],
            "extent_m": {"x0": round(sy.x0, 3), "y0": round(sy.y0, 3), "x1": round(sy.x1, 3), "y1": round(sy.y1, 3)}, "spacing_m": d.grid_spacing_m, "rg_ohm": round(g.rg, 4),
            "rest_of_plant_ohm": d.rest_ohm, "split_factor": round(d.split_factor, 4), "fault_current_a": round(d.basis.fault_current_ka * 1000),
            "grid_current_a": round(g.gpr / g.rg, 1), "gpr_v": round(g.gpr), "e_touch_tolerable_v": round(g.e_touch, 1),
            "e_step_tolerable_v": round(g.e_step, 1), "e_mesh_v": round(g.e_mesh, 1), "e_step_v": round(g.e_s, 1), "safe": g.safe,
        }  # fmt: skip
    elif d.grid_spacing_m is not None:
        out["switchyard_grid"] = {"spacing_m": d.grid_spacing_m, "checked": False}
    return out


__all__ = ["EarthingDesign", "EarthingError", "Value", "conductor_mass_kg", "design_earthing", "pit_text", "summary"]

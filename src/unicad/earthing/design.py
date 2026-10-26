"""Earthing design for a project: conductor size, electrode count and resistance, the switchyard grid check, the layout and its
quantities. A pure function of the project; nothing is stored beyond the design basis."""

import math
from collections import defaultdict
from dataclasses import dataclass, field, replace

from shapely.geometry import MultiLineString

from unicad.domain.network import NodeKind
from unicad.domain.project import EarthingBasis, Project, Segment
from unicad.earthing.layout import MV_KINDS, EarthingLayout, Sizes, bonding, build_layout, designated_switchyard, equipment, place_switchyard
from unicad.earthing.model import ConductorRole, PitRole, Quantity, Step, Value
from unicad.earthing.sizing import (
    FLAT_BY_NAME,
    SOURCES,
    Grid,
    GridCheck,
    conductor_area,
    electrode_current,
    grid_check,
    ieee80_area,
    parallel,
    pipe_resistance,
    select_flat,
    strip_resistance,
)
from unicad.lps.design import LpsDesign, design_lps, is_rooftop

DEFAULT_SIZE = "50x6"
DOWN_SIZE = "25x3"
SWITCHYARD_DEFAULT_M = (40.0, 30.0)
GRID_SPACING_DEFAULT_M = 6.0
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
    grid: GridCheck | None
    grid_spacing_m: float | None
    layout: EarthingLayout
    lps: LpsDesign | None
    quantities: tuple[Quantity, ...]
    bonded: dict[str, int]
    assumptions: tuple[str, ...] = field(default=())
    warnings: tuple[str, ...] = field(default=())

    @property
    def pit_count(self) -> int:
        return len(self.layout.pits)

    @property
    def unbonded(self) -> tuple[str, ...]:
        need = {it.id: it.connections for it in self.layout.items} | {t: 1 for t in self.layout.tables}
        return tuple(sorted(i for i, n in need.items() if self.bonded.get(i, 0) < n))


def _target(project: Project, basis: EarthingBasis) -> tuple[float, str | None]:
    if basis.target_resistance_ohm is not None:
        return basis.target_resistance_ohm, None
    has_tx = any(n.kind in (NodeKind.TRANSFORMER,) for n in project.network.nodes) or project.segment is Segment.UTILITY
    key = "utility" if has_tx else "household" if project.segment is Segment.RESIDENTIAL else "commercial"
    v, why = TARGET_PRACTICE[key]
    return v, f"target earth resistance {v:g} ohm ({why}; {TARGET_SOURCE}); not a cited limit, set target_resistance_ohm to override"


def _grid_spacing(basis: EarthingBasis, size: str, rect: tuple[float, float, float, float], rods: int, ig: float, ts: float) -> tuple[float, GridCheck]:
    flat = FLAT_BY_NAME[size]
    lx, ly = rect[2] - rect[0], rect[3] - rect[1]
    last = None
    for tenths in range(100, 19, -5):
        g = Grid(lx, ly, tenths / 10, basis.burial_depth_m, flat.width_mm / 2000, rods, basis.electrode_length_m)
        last = grid_check(basis.soil_resistivity_ohm_m, g, ig, ts, basis.surface_resistivity_ohm_m, basis.surface_layer_m)
        if last.safe:
            return g.mesh, last
    return g.mesh, last


def design_earthing(project: Project, basis: EarthingBasis | None = None, lps: LpsDesign | None = None) -> EarthingDesign:
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
    sizes = Sizes(main=main, array=main, equipment=main, grid=main, down=DOWN_SIZE)
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
    spacing = None
    grid = None
    if not rooftop and any(k in MV_KINDS for _, _, k in plant):
        size = basis.switchyard_m or SWITCHYARD_DEFAULT_M
        if basis.switchyard_m is None and designated_switchyard(project) is None:
            assumptions.append(f"MV switchyard earth grid {size[0]:g} x {size[1]:g} m (switchyard_m not given; assumption)")
        layout = project.array_layout
        stations = [(b.station.x, b.station.y) for b in layout.blocks if b.station is not None] if layout is not None else []
        near = (sum(p[0] for p in stations) / len(stations), sum(p[1] for p in stations) / len(stations)) if stations else None
        yard, problem = place_switchyard(project, size, near)
        if problem:
            warnings.append(problem)
        if yard is not None:
            if fault:
                spacing, grid = _grid_spacing(basis, main, yard, 8, basis.fault_current_ka * 1000, basis.fault_clearing_s)
                assumptions.append("grid current taken as the full earth fault current (split factor 1, decrement factor 1; conservative)")
                assumptions.append(f"flat strip {main} taken as a round conductor of diameter half its width in the IEEE 80 mesh equations")
                if basis.surface_resistivity_ohm_m is None:
                    assumptions.append("no surface layer (crushed rock) assumed in the tolerable touch and step voltages")
                assumptions.append("tolerable voltages for a 50 kg body")
            else:
                spacing = GRID_SPACING_DEFAULT_M
                assumptions.append(f"switchyard grid mesh {spacing:g} m (typical spacing on the CIL/Refex earthmat layout; no fault data to check touch and step voltage)")
    yard_arg = (yard, spacing) if yard is not None else None

    def build(extra: int) -> EarthingLayout:
        return build_layout(project, sizes, extra, basis.tables_per_pit, basis.electrode_length_m, lps, yard_arg)

    def resistance(lay: EarthingLayout) -> tuple[float, Step | None, float | None]:
        buried = [r for r in lay.runs if r.buried]
        length = sum(r.length_m for r in buried)
        n = sum(1 for p in lay.pits if p.on_ground)
        strip = strip_resistance(rho, length, FLAT_BY_NAME[main].width_mm, basis.burial_depth_m) if length > 1 else None
        rp = electrode.value / n if n else math.inf
        r = parallel(rp, strip.value) if strip else rp
        mesh = None
        if len(buried) > 3:
            hull = MultiLineString([[(q.x, q.y) for q in b.points] for b in buried]).convex_hull
            if hull.area > 1:
                mesh = rho * (1 / (length + n * basis.electrode_length_m) + 1 / math.sqrt(20 * hull.area) * (1 + 1 / (1 + basis.burial_depth_m * math.sqrt(20 / hull.area))))
                r = max(r, mesh)
        return r, strip, mesh

    lay = build(0)
    r, strip, mesh = resistance(lay)
    n0 = sum(1 for p in lay.pits if p.on_ground)
    rs = strip.value if strip else math.inf
    need_r = 0 if rs <= target else math.ceil(electrode.value / (1 / (1 / target - 1 / rs) if rs < math.inf else target))
    if mesh is not None and mesh > target:
        warnings.append(f"the buried network's area limits the earth resistance to about {mesh:.3g} ohm, above the {target:g} ohm target; more pits barely help")
    need_i = None
    if fault:
        per = electrode_current(rho, basis.fault_clearing_s, basis.electrode_length_m, basis.electrode_diameter_mm)
        steps.append(per)
        need_i = math.ceil(basis.fault_current_ka * 1000 / per.value)
    extra = max(0, need_r - n0, (need_i or 0) - n0)
    if extra:
        lay = build(extra)
        r, strip, mesh = resistance(lay)
    if mesh is not None and mesh >= r - 1e-12:
        assumptions.append("earth resistance taken as the larger of the pits-and-strip value and the Sverak (IEEE 80) value for the buried network over its convex hull")
    if grid is not None:
        ig = basis.fault_current_ka * 1000
        grid = replace(grid, gpr=ig * min(grid.rg, r))
        assumptions.append("switchyard grid bonded to the plant earth network: ground potential rise from the combined earth resistance")
    if len(lay.pits) < max(need_r, need_i or 0):
        warnings.append(f"only {len(lay.pits)} pits fit at two electrode lengths apart; {max(need_r, need_i or 0)} are needed")
    warnings += lay.warnings
    bonded = bonding(lay)
    d = EarthingDesign(
        basis=basis, rooftop=rooftop, sizes=sizes, steps=tuple(steps), electrode=electrode, strip=strip, resistance_ohm=round(r, 4), target_ohm=target,
        pits_by_resistance=need_r, pits_by_current=need_i, grid=grid, grid_spacing_m=spacing, layout=lay, lps=lps,
        quantities=quantities(lay, basis, sizes, lps), bonded=bonded, assumptions=tuple(assumptions + list(lps.assumptions if lps else ())), warnings=tuple(warnings),
    )  # fmt: skip
    return d


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


def summary(d: EarthingDesign) -> dict:
    lay = d.layout
    out = {
        "rooftop": d.rooftop,
        "conductor": {"main": d.sizes.main, "array": d.sizes.array, "equipment": d.sizes.equipment, "down": d.sizes.down},
        "pits": len(lay.pits),
        "pits_by_role": {r.value: sum(1 for p in lay.pits if p.role is r) for r in PitRole if any(p.role is r for p in lay.pits)},
        "pits_needed_by_resistance": d.pits_by_resistance,
        "pits_needed_by_current": d.pits_by_current,
        "electrode_resistance_ohm": d.electrode.value,
        "resistance_ohm": d.resistance_ohm,
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
        out["switchyard_grid"] = {
            "spacing_m": d.grid_spacing_m, "rg_ohm": round(g.rg, 4), "gpr_v": round(g.gpr), "e_touch_tolerable_v": round(g.e_touch, 1),
            "e_step_tolerable_v": round(g.e_step, 1), "e_mesh_v": round(g.e_mesh, 1), "e_step_v": round(g.e_s, 1), "safe": g.safe,
        }  # fmt: skip
    elif d.grid_spacing_m is not None:
        out["switchyard_grid"] = {"spacing_m": d.grid_spacing_m, "checked": False}
    return out


__all__ = ["EarthingDesign", "EarthingError", "Value", "design_earthing", "pit_text", "summary"]

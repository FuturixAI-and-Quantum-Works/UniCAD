"""Earthing calculations with the working shown: conductor size, electrode and strip resistance, and the IEEE 80 grid check.

Every formula names the page it was taken from (SOURCES). Values without a source are returned as assumptions.
"""

import math
from dataclasses import dataclass

from unicad.earthing.model import Step, Value

SOURCES = {
    "is3043_k": "IS 3043 adiabatic sizing A = I sqrt(t) / k, k = 80 for GI, as quoted in J. Parmar, 'How to determine correct number of "
    "earthing electrodes, part 2', electrical-engineering-portal.com",
    "is3043_pipe": "IS 3043 pipe or rod electrode R = 100 rho / (2 pi L) ln(4 L / d), L and d in cm, as quoted in J. Parmar, part 1, "
    "electrical-engineering-portal.com",
    "is3043_strip": "IS 3043 buried strip R = 100 rho / (2 pi L) ln(2 L^2 / (w t)), L, w, t in cm, as quoted in J. Parmar, part 2, "
    "electrical-engineering-portal.com",
    "is3043_density": "IS 3043 maximum current density at the electrode surface J = 7.57 x 1000 / sqrt(rho t) A/m2, as quoted in J. Parmar, "
    "part 1, electrical-engineering-portal.com",
    "parallel": "Electrodes spaced well apart combine as resistances in parallel, and a pit group in parallel with the buried strip "
    "(J. Parmar, part 1, electrical-engineering-portal.com)",
    "ieee80_kf": "IEEE Std 80 simplified sizing A_kcmil = I_kA Kf sqrt(tc), Kf = 28.96 for zinc-coated steel (Tm 419 C, Ta 40 C), "
    "A_kcmil = 1.974 A_mm2; IEEE 80 table as reproduced in 'Electrical Safety Practice and Standards', Table 8.3",
    "ieee80_grid": "IEEE Std 80-2013 tolerable touch/step voltage, Cs, Sverak grid resistance, Km, Ki, Ks, mesh and step voltage, "
    "as written out in IECCalc 'Earthing grid: touch voltage decides, not resistance' (ieccalc.com) and windfarmbop.com IEEE 80 step by step",
}

GI_K = 80.0
ZINC_STEEL_KF = 28.96
KCMIL_PER_MM2 = 1.974


@dataclass(frozen=True)
class Flat:
    """GS (galvanised steel) flat strip, width x thickness in mm."""

    width_mm: float
    thickness_mm: float

    @property
    def name(self) -> str:
        return f"{self.width_mm:g}x{self.thickness_mm:g}"

    @property
    def area_mm2(self) -> float:
        return self.width_mm * self.thickness_mm


STANDARD_FLATS = tuple(Flat(w, t) for w, t in ((25, 3), (25, 6), (50, 6), (75, 6), (75, 10), (75, 12), (100, 12)))
FLAT_BY_NAME = {f.name: f for f in STANDARD_FLATS}


class SizingError(ValueError):
    pass


def conductor_area(fault_ka: float, clearing_s: float, corrosion_allowance: float = 0.0) -> Step:
    """IS 3043 adiabatic minimum cross-section for a GI conductor, raised by the corrosion allowance."""
    a = fault_ka * 1000 * math.sqrt(clearing_s) / GI_K
    need = a * (1 + corrosion_allowance)
    return Step(
        name="conductor_area", value=round(need, 1), unit="mm2",
        formula="A = I sqrt(t) / k x (1 + corrosion allowance)",
        inputs=(Value("I", fault_ka * 1000, "A", "design_basis.earthing.fault_current_ka"), Value("t", clearing_s, "s", "design_basis.earthing.fault_clearing_s"),
                Value("k", GI_K, "A s^0.5/mm2", SOURCES["is3043_k"]), Value("corrosion allowance", corrosion_allowance, "", "design_basis.earthing.corrosion_allowance")),
        source=SOURCES["is3043_k"],
    )  # fmt: skip


def ieee80_area(fault_ka: float, clearing_s: float) -> Step:
    """IEEE 80 simplified (Kf) minimum cross-section for zinc-coated steel, the cross-check of the IS 3043 value."""
    kcmil = fault_ka * ZINC_STEEL_KF * math.sqrt(clearing_s)
    return Step(
        name="ieee80_area", value=round(kcmil / KCMIL_PER_MM2, 1), unit="mm2", formula="A = I_kA Kf sqrt(tc) / 1.974",
        inputs=(Value("I", fault_ka, "kA"), Value("tc", clearing_s, "s"), Value("Kf", ZINC_STEEL_KF, "", SOURCES["ieee80_kf"])),
        source=SOURCES["ieee80_kf"],
    )  # fmt: skip


def select_flat(area_mm2: float, flats: tuple[Flat, ...] = STANDARD_FLATS) -> Flat:
    for f in sorted(flats, key=lambda f: (f.area_mm2, f.width_mm)):
        if f.area_mm2 >= area_mm2:
            return f
    raise SizingError(f"no standard GS flat reaches {area_mm2:.0f} mm2 (largest {max(f.area_mm2 for f in flats):g} mm2); run parallel conductors or use copper")


def pipe_resistance(rho: float, length_m: float, diameter_mm: float) -> Step:
    """IS 3043 resistance of one pipe or rod electrode."""
    L, d = length_m * 100, diameter_mm / 10
    r = 100 * rho / (2 * math.pi * L) * math.log(4 * L / d)
    return Step(
        name="pipe_resistance", value=round(r, 3), unit="ohm", formula="R = 100 rho / (2 pi L) ln(4 L / d), L and d in cm",
        inputs=(Value("rho", rho, "ohm m", "design_basis.earthing.soil_resistivity_ohm_m"), Value("L", L, "cm"), Value("d", d, "cm")),
        source=SOURCES["is3043_pipe"],
    )  # fmt: skip


def strip_resistance(rho: float, length_m: float, width_mm: float, depth_m: float) -> Step:
    """IS 3043 resistance of a buried strip of total length `length_m`."""
    L, w, t = length_m * 100, width_mm / 10, depth_m * 100
    r = 100 * rho / (2 * math.pi * L) * math.log(2 * L * L / (w * t))
    return Step(
        name="strip_resistance", value=round(r, 4), unit="ohm", formula="R = 100 rho / (2 pi L) ln(2 L^2 / (w t)), L, w, t in cm",
        inputs=(Value("rho", rho, "ohm m"), Value("L", L, "cm"), Value("w", w, "cm"), Value("t", t, "cm")),
        source=SOURCES["is3043_strip"],
    )  # fmt: skip


def electrode_current(rho: float, clearing_s: float, length_m: float, diameter_mm: float) -> Step:
    """IS 3043 current one electrode can dissipate: allowed surface current density x the electrode's surface area."""
    j = 7.57 * 1000 / math.sqrt(rho * clearing_s)
    area = math.pi * diameter_mm / 1000 * length_m
    return Step(
        name="electrode_current", value=round(j * area, 1), unit="A", formula="I = 7.57 x 1000 / sqrt(rho t) x pi d L",
        inputs=(Value("rho", rho, "ohm m"), Value("t", clearing_s, "s"), Value("d", diameter_mm / 1000, "m"), Value("L", length_m, "m")),
        source=SOURCES["is3043_density"],
    )  # fmt: skip


def parallel(*rs: float) -> float:
    return 1 / sum(1 / r for r in rs)


# --- IEEE 80 grid -----------------------------------------------------------------------------


def surface_factor(rho: float, rho_s: float | None, hs: float | None) -> float:
    if rho_s is None or hs is None:
        return 1.0
    return 1 - 0.09 * (1 - rho / rho_s) / (2 * hs + 0.09)


def tolerable(rho: float, rho_s: float | None, hs: float | None, ts: float, body_kg: int = 50) -> tuple[float, float, float]:
    """(Cs, E_touch, E_step) for a 50 or 70 kg body. Without a surface layer the native soil is the surface."""
    k = {50: 0.116, 70: 0.157}[body_kg]
    cs = surface_factor(rho, rho_s, hs)
    rs = rho_s if rho_s is not None and hs is not None else rho
    return cs, (1000 + 1.5 * cs * rs) * k / math.sqrt(ts), (1000 + 6.0 * cs * rs) * k / math.sqrt(ts)


@dataclass(frozen=True)
class Grid:
    """A rectangular mesh of `lx` x `ly` m with conductors every `spacing` m, buried `depth` m, conductor diameter `d` m,
    and `rods` perimeter rods of `rod_length` m."""

    lx: float
    ly: float
    spacing: float
    depth: float
    d: float
    rods: int = 0
    rod_length: float = 0.0

    @property
    def nx(self) -> int:
        return max(2, round(self.lx / self.spacing) + 1)

    @property
    def ny(self) -> int:
        return max(2, round(self.ly / self.spacing) + 1)

    @property
    def area(self) -> float:
        return self.lx * self.ly

    @property
    def lc(self) -> float:
        return self.nx * self.ly + self.ny * self.lx

    @property
    def lr(self) -> float:
        return self.rods * self.rod_length

    @property
    def lp(self) -> float:
        return 2 * (self.lx + self.ly)

    @property
    def mesh(self) -> float:
        return max(self.lx / (self.nx - 1), self.ly / (self.ny - 1))


@dataclass(frozen=True)
class GridCheck:
    rg: float
    gpr: float
    e_touch: float
    e_step: float
    e_mesh: float
    e_s: float
    cs: float
    km: float
    ki: float
    ks: float
    n: float
    lm: float
    ls: float

    @property
    def safe(self) -> bool:
        return self.gpr <= self.e_touch or (self.e_mesh <= self.e_touch and self.e_s <= self.e_step)


def sverak(rho: float, g: Grid) -> float:
    lt = g.lc + g.lr
    return rho * (1 / lt + 1 / math.sqrt(20 * g.area) * (1 + 1 / (1 + g.depth * math.sqrt(20 / g.area))))


def grid_check(rho: float, g: Grid, ig: float, ts: float, rho_s: float | None = None, hs: float | None = None, body_kg: int = 50) -> GridCheck:
    """IEEE 80 mesh and step voltages of a rectangular grid for grid current `ig` A."""
    cs, et, es_tol = tolerable(rho, rho_s, hs, ts, body_kg)
    rg = sverak(rho, g)
    D, h, d = g.mesh, g.depth, g.d
    na = 2 * g.lc / g.lp
    nb = math.sqrt(g.lp / (4 * math.sqrt(g.area)))
    n = na * nb
    kh = math.sqrt(1 + h / 1.0)
    kii = 1.0 if g.rods else 1 / (2 * n) ** (2 / n)
    km = (math.log(D * D / (16 * h * d) + (D + 2 * h) ** 2 / (8 * D * d) - h / (4 * d)) + kii / kh * math.log(8 / (math.pi * (2 * n - 1)))) / (2 * math.pi)
    ki = 0.644 + 0.148 * n
    if g.rods:
        lm = g.lc + (1.55 + 1.22 * g.rod_length / math.sqrt(g.lx**2 + g.ly**2)) * g.lr
    else:
        lm = g.lc
    ks = (1 / (2 * h) + 1 / (D + h) + 1 / D * (1 - 0.5 ** (n - 2))) / math.pi
    ls = 0.75 * g.lc + 0.85 * g.lr
    em = rho * km * ki * ig / lm
    es = rho * ks * ki * ig / ls
    return GridCheck(rg=rg, gpr=ig * rg, e_touch=et, e_step=es_tol, e_mesh=em, e_s=es, cs=cs, km=km, ki=ki, ks=ks, n=n, lm=lm, ls=ls)

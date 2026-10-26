"""Lightning protection zones: protection radius of an ESE terminal (NF C 17-102) or a conventional rod (IS/IEC 62305 rolling
sphere), and the fewest terminals that cover a set of points (greedy set cover on a candidate grid)."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from unicad.earthing.model import Step, Value

SPHERE_RADIUS_M = {"I": 20.0, "II": 30.0, "III": 45.0, "IV": 60.0}
SOURCES = {
    "sphere": "IS/IEC 62305-3 rolling sphere radius per lightning protection level: I 20 m, II 30 m, III 45 m, IV 60 m (also D in "
    "NF C 17-102), as listed by tools.mepbase.com ESE calculator",
    "ese": "NF C 17-102:2011 (French standard; not an Indian or IEC standard) Rp(h) = sqrt(2 D h - h^2 + dL (2 D + dL)) for h >= 5 m, "
    "Rp(h) = h Rp(5) / 5 for 2 m <= h < 5 m, dL = 1 m/us x dT, dT <= 60 us, as written out by tools.mepbase.com ESE calculator",
    "rod": "Rolling sphere geometry (IS/IEC 62305-3 method): a sphere of radius R resting on the ground and touching a vertical rod of "
    "height h <= R leaves unprotected only what it touches; protected radius at height hx is sqrt(h (2R - h)) - sqrt(hx (2R - hx))",
}


class CoverageError(ValueError):
    pass


def sphere_radius(level: str) -> Step:
    return Step(name="rolling_sphere_radius", value=SPHERE_RADIUS_M[level], unit="m", formula="R by protection level", inputs=(Value("LPL", level),), source=SOURCES["sphere"])


def ese_radius(level: str, delta_t_us: float, height_m: float) -> Step:
    """NF C 17-102 protection radius of an ESE air terminal `height_m` above the plane it protects."""
    if delta_t_us > 60:
        raise CoverageError("NF C 17-102 caps the ESE efficiency dT used in design at 60 us")
    if height_m < 2:
        raise CoverageError("NF C 17-102 needs the ESE tip at least 2 m above the area it protects")
    D = SPHERE_RADIUS_M[level]
    dl = delta_t_us

    def rp(h: float) -> float:
        return math.sqrt(max(0.0, 2 * D * h - h * h + dl * (2 * D + dl)))

    r = rp(height_m) if height_m >= 5 else height_m * rp(5.0) / 5
    return Step(
        name="ese_protection_radius", value=round(r, 2), unit="m",
        formula="Rp = sqrt(2 D h - h^2 + dL (2 D + dL))" if height_m >= 5 else "Rp = h Rp(5) / 5",
        inputs=(Value("LPL", level, None, "design_basis.lps.level"), Value("D", D, "m", SOURCES["sphere"]), Value("dT", delta_t_us, "us", "design_basis.lps.ese_delta_t_us"),
                Value("dL", dl, "m"), Value("h", height_m, "m", "design_basis.lps.mast_height_m")),
        source=SOURCES["ese"],
    )  # fmt: skip


def rod_radius(level: str, rod_height_m: float, protected_height_m: float) -> Step:
    """Radius protected at `protected_height_m` by a vertical rod of `rod_height_m` (rolling sphere; rods taller than R use R)."""
    R = SPHERE_RADIUS_M[level]
    h = min(rod_height_m, R)
    hx = protected_height_m
    if hx >= h:
        raise CoverageError(f"rod height {rod_height_m:g} m does not rise above the protected height {hx:g} m")
    r = math.sqrt(h * (2 * R - h)) - math.sqrt(hx * (2 * R - hx))
    return Step(
        name="rod_protection_radius", value=round(r, 2), unit="m", formula="r = sqrt(h (2R - h)) - sqrt(hx (2R - hx))",
        inputs=(Value("LPL", level), Value("R", R, "m", SOURCES["sphere"]), Value("h", rod_height_m, "m"), Value("hx", hx, "m")),
        source=SOURCES["rod"],
    )  # fmt: skip


@dataclass(frozen=True)
class Cover:
    terminals: tuple[tuple[float, float], ...]
    uncovered: tuple[int, ...]


def covered(points: np.ndarray, terminals: Sequence[tuple[float, float]], radius: float) -> np.ndarray:
    if not len(terminals):
        return np.zeros(len(points), dtype=bool)
    t = np.asarray(terminals, dtype=float)
    d2 = ((points[:, None, :] - t[None, :, :]) ** 2).sum(axis=2)
    return (d2 <= radius * radius + 1e-9).any(axis=1)


def greedy_cover(points: np.ndarray, candidates: np.ndarray, radius: float) -> Cover:
    """Pick candidates until every point lies within `radius` of one (greedy set cover), then drop any terminal the others make redundant.
    Ties go to the lower candidate index, so the result is deterministic."""
    if not len(points):
        return Cover((), ())
    hits = np.zeros((len(points), len(candidates)), dtype=bool)
    r2 = radius * radius + 1e-9
    for k in range(0, len(candidates), 256):
        c = candidates[k : k + 256]
        hits[:, k : k + len(c)] = ((points[:, None, :] - c[None, :, :]) ** 2).sum(axis=2) <= r2
    left = np.ones(len(points), dtype=bool)
    chosen: list[int] = []
    while left.any():
        gain = (hits & left[:, None]).sum(axis=0)
        best = int(np.argmax(gain))
        if gain[best] == 0:
            break
        chosen.append(best)
        left &= ~hits[:, best]
    for c in sorted(chosen, key=lambda c: hits[:, c].sum()):
        rest = [k for k in chosen if k != c]
        if rest and hits[:, rest].any(axis=1)[~left].all():
            chosen = rest
    chosen.sort()
    return Cover(tuple((float(candidates[c][0]), float(candidates[c][1])) for c in chosen), tuple(int(i) for i in np.flatnonzero(left)))

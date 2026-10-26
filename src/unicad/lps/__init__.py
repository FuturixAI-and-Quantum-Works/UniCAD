"""Lightning protection: ESE (NF C 17-102) and rolling-sphere air terminals, placed to cover every table, station and roof."""

from unicad.lps.design import LpsDesign, Target, Terminal, coverage_points, design_lps, is_rooftop
from unicad.lps.zones import SPHERE_RADIUS_M, CoverageError, ese_radius, greedy_cover, rod_radius

__all__ = [
    "SPHERE_RADIUS_M", "CoverageError", "LpsDesign", "Target", "Terminal", "coverage_points", "design_lps", "ese_radius", "greedy_cover",
    "is_rooftop", "rod_radius",
]  # fmt: skip

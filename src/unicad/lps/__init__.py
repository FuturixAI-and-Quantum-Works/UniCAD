"""Lightning protection: ESE (NF C 17-102) and rolling-sphere air terminals, placed to cover every table, station, switchyard, BESS unit and building."""

from unicad.lps.design import ClassCoverage, LpsDesign, Target, Terminal, coverage_points, design_lps, equipment_footprint, is_rooftop
from unicad.lps.zones import SPHERE_RADIUS_M, CoverageError, ese_radius, greedy_cover, rod_radius

__all__ = [
    "SPHERE_RADIUS_M", "ClassCoverage", "CoverageError", "LpsDesign", "Target", "Terminal", "coverage_points", "design_lps", "equipment_footprint",
    "ese_radius", "greedy_cover", "is_rooftop", "rod_radius",
]  # fmt: skip

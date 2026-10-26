"""Table geometry in site metres.

A table's local frame: `u` runs along its rows, `v` across them away from the side the modules face
(up the slope of a fixed table). `origin` is the corner at slot (0, 0), so row 0 is the front row.
"""

import math

from shapely.geometry import Polygon

from unicad.domain.layout import Point, Table


def frame(azimuth_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """(u, v) unit vectors in site (east, north) for modules facing `azimuth_deg`."""
    a = math.radians(azimuth_deg)
    fx, fy = math.sin(a), math.cos(a)
    return (_clean(-fy), _clean(fx)), (_clean(-fx), _clean(-fy))


def _clean(v: float) -> float:
    return 0.0 if abs(v) < 1e-12 else v


def plan_depth(t: Table) -> float:
    return t.rows * t.slot_height_m * math.cos(math.radians(t.tilt_deg))


def plan_length(t: Table) -> float:
    return t.cols * t.slot_width_m


def to_site(origin: Point, azimuth_deg: float, a: float, b: float) -> tuple[float, float]:
    (ux, uy), (vx, vy) = frame(azimuth_deg)
    return origin.x + a * ux + b * vx, origin.y + a * uy + b * vy


def table_corners(t: Table) -> list[tuple[float, float]]:
    w, d = plan_length(t), plan_depth(t)
    return [to_site(t.origin, t.azimuth_deg, a, b) for a, b in ((0, 0), (w, 0), (w, d), (0, d))]


def table_polygon(t: Table) -> Polygon:
    return Polygon(table_corners(t))


def table_centre(t: Table) -> tuple[float, float]:
    return to_site(t.origin, t.azimuth_deg, plan_length(t) / 2, plan_depth(t) / 2)

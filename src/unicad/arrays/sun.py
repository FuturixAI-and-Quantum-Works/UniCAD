"""Sun position and the shade-free row pitch it implies.

Sun angles use apparent solar time and the usual spherical-astronomy relations
(declination, latitude, hour angle). The winter solstice declination is the obliquity
of the ecliptic, 23.44 degrees, taken as -23.44 in the northern hemisphere.
"""

import math
from dataclasses import dataclass

WINTER_SOLSTICE_DECLINATION_DEG = -23.44
STEP_MINUTES = 5


@dataclass(frozen=True)
class SunPosition:
    hour: float
    elevation_deg: float
    azimuth_deg: float

    def vector(self) -> tuple[float, float, float]:
        """Unit vector towards the sun in (east, north, up)."""
        el, az = math.radians(self.elevation_deg), math.radians(self.azimuth_deg)
        return math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el)


def sun_position(latitude_deg: float, declination_deg: float, solar_hour: float) -> SunPosition:
    """Elevation and azimuth (clockwise from north) at an apparent solar time."""
    phi, dec = math.radians(latitude_deg), math.radians(declination_deg)
    h = math.radians(15.0 * (solar_hour - 12.0))
    sin_el = math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(h)
    el = math.asin(max(-1.0, min(1.0, sin_el)))
    east = -math.cos(dec) * math.sin(h)
    north = math.cos(phi) * math.sin(dec) - math.sin(phi) * math.cos(dec) * math.cos(h)
    az = math.degrees(math.atan2(east, north)) % 360
    return SunPosition(solar_hour, math.degrees(el), az)


def sun_path(latitude_deg: float, start_hour: float, end_hour: float, declination_deg: float = WINTER_SOLSTICE_DECLINATION_DEG) -> list[SunPosition]:
    n = max(1, round((end_hour - start_hour) * 60 / STEP_MINUTES))
    hours = [start_hour + (end_hour - start_hour) * i / n for i in range(n + 1)]
    return [p for p in (sun_position(latitude_deg, declination_deg, t) for t in hours) if p.elevation_deg > 0]


def fixed_tilt_gap(slope_length_m: float, tilt_deg: float, facing_deg: float, path: list[SunPosition]) -> tuple[float, SunPosition | None]:
    """Clear ground gap behind a fixed row so that its shadow never reaches the next row over `path`.

    The shadow of the top edge, height H = L sin(tilt), runs H cos(sun az - facing) / tan(elevation) along the row normal.
    Returns the gap and the sun position that sets it.
    """
    height = slope_length_m * math.sin(math.radians(tilt_deg))
    worst, at = 0.0, None
    for p in path:
        along = math.cos(math.radians(p.azimuth_deg - facing_deg))
        if along <= 0:
            continue
        gap = height * along / math.tan(math.radians(p.elevation_deg))
        if gap > worst:
            worst, at = gap, p
    return worst, at


def profile_angle_deg(p: SunPosition, facing_deg: float) -> float:
    """Sun elevation projected onto the vertical plane through the row normal."""
    along = math.cos(math.radians(p.azimuth_deg - facing_deg))
    return math.degrees(math.atan2(math.tan(math.radians(p.elevation_deg)), along))


def tracker_rotation_deg(p: SunPosition, axis_azimuth_deg: float, max_rotation_deg: float) -> float:
    """True-tracking rotation of a horizontal single-axis tracker, clamped to its limit; positive tilts towards the cross-axis direction."""
    e, n, u = p.vector()
    ga = math.radians(axis_azimuth_deg)
    cross = e * math.cos(ga) - n * math.sin(ga)
    theta = math.degrees(math.atan2(cross, u))
    return max(-max_rotation_deg, min(max_rotation_deg, theta))


def tracker_pitch(width_m: float, axis_azimuth_deg: float, max_rotation_deg: float, path: list[SunPosition]) -> tuple[float, SunPosition | None]:
    """Axis-to-axis pitch at which no tracker shades its neighbour over `path` without backtracking.

    With rotation theta and the sun's elevation beta in the cross-axis plane, a row's shadow on the plane of the axes
    spans W (cos theta + sin |theta| / tan beta); the next row is clear when the pitch is at least that.
    """
    ga = math.radians(axis_azimuth_deg)
    worst, at = width_m, None
    for p in path:
        e, n, u = p.vector()
        cross = abs(e * math.cos(ga) - n * math.sin(ga))
        if cross < 1e-12:
            continue
        theta = math.radians(abs(tracker_rotation_deg(p, axis_azimuth_deg, max_rotation_deg)))
        span = width_m * (math.cos(theta) + math.sin(theta) * cross / u)
        if span > worst:
            worst, at = span, p
    return worst, at

"""What a plot that holds less than the target would have to be, as an estimate from the layout that was placed."""

import math
from dataclasses import dataclass

from shapely import affinity
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from unicad.domain.site import FeatureKind, SiteGeometry, shape_of

SEARCH_STEPS = 60
MAX_SCALE = 8.0


@dataclass(frozen=True)
class Shortfall:
    target_kwp: float
    placed_kwp: float
    short_kwp: float
    buildable_m2: float
    needed_buildable_m2: float
    plot_m: tuple[float, float] | None
    plot_now_m: tuple[float, float] | None
    scale: float | None

    def message(self) -> str:
        head = (
            f"the plot fits {self.placed_kwp:.1f} kWp of the {self.target_kwp:.1f} kWp target: {self.short_kwp:.1f} kWp ({self.short_kwp / self.target_kwp:.0%}) short. "
            f"Its buildable area is {self.buildable_m2:.0f} m2; the target needs about {self.needed_buildable_m2:.0f} m2 at the same layout density."
        )
        if self.plot_m is None or self.plot_now_m is None:
            return head
        return (
            f"{head} Estimate: the same plot shape scaled to about {self.plot_m[0]:.0f} x {self.plot_m[1]:.0f} m (now {self.plot_now_m[0]:.0f} x {self.plot_now_m[1]:.0f} m, "
            "bounding box) with the same setbacks would fit it; this scales the boundary in proportion and ignores exclusions, so confirm with layout_place."
        )


def _boundary(site: SiteGeometry) -> BaseGeometry | None:
    parts = [shape_of(f) for f in site.of_kind(FeatureKind.BOUNDARY) if f.polygon]
    return unary_union(parts) if parts else None


def shortfall(site: SiteGeometry, target_kwp: float, placed_kwp: float, buildable_m2: float) -> Shortfall | None:
    """None when the target is met. Otherwise the shortfall and, when the site has a boundary, the scale of that boundary (about its centre,
    same setbacks) at which the buildable area would grow in proportion to the target over what was placed."""
    if placed_kwp >= target_kwp - 1e-6 or target_kwp <= 0:
        return None
    needed = buildable_m2 * target_kwp / max(placed_kwp, 1e-6)
    plot = _boundary(site)
    setback = site.setbacks.boundary_m
    if plot is None or plot.is_empty:
        return Shortfall(target_kwp, placed_kwp, target_kwp - placed_kwp, buildable_m2, needed, None, None, None)

    def area_at(s: float) -> float:
        return affinity.scale(plot, s, s, origin="centroid").buffer(-setback, join_style="mitre").area

    lo, hi = 1.0, MAX_SCALE
    if area_at(hi) < needed:
        scale = None
    else:
        for _ in range(SEARCH_STEPS):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if area_at(mid) < needed else (lo, mid)
        scale = hi
    x0, y0, x1, y1 = plot.bounds
    now = (x1 - x0, y1 - y0)
    return Shortfall(
        target_kwp, placed_kwp, target_kwp - placed_kwp, buildable_m2, needed, None if scale is None else (now[0] * scale, now[1] * scale), now,
        None if scale is None else math.ceil(scale * 1000) / 1000,
    )  # fmt: skip

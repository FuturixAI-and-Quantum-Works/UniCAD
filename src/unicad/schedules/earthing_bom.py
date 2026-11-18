"""Earthing and lightning protection material of the bill, as the earthing design counts it."""

from collections import Counter

from unicad.catalog.store import Catalog
from unicad.domain.project import Project
from unicad.earthing import EarthingDesign, EarthingError, design_earthing
from unicad.lps import CoverageError, LpsDesign, design_lps
from unicad.schedules.lines import BomLine

EARTHING = "EARTHING"
LIGHTNING = "LIGHTNING PROTECTION"
NO_ITEM = "no catalog item: specify before ordering"
NOT_COUNTED = (
    "Earthing and lightning protection quantities are those of the earthing and lightning designs (pits by type, GS flat plan lengths by size and use, ESE terminals with masts); "
    "flat lengths carry no vertical-run, jointing or wastage allowance. Test links, clamps, welds, earth-enhancing compound and pit covers are not counted by the design and are not in the bill."
)

_last: list[tuple[Project, Catalog | None, tuple[EarthingDesign | None, LpsDesign | None, tuple[str, ...]]]] = []


def designs(project: Project, catalog: Catalog | None = None) -> tuple[EarthingDesign | None, LpsDesign | None, tuple[str, ...]]:
    """The earthing design (which carries the lightning design) or the lightning design alone, recomputed from the project; why one is missing.
    `catalog` is the one the project is built from (the workspace's, with catalog_add entries); None is the shipped catalog."""
    if _last and _last[0][0] is project and _last[0][1] is catalog:
        return _last[0][2]
    basis = project.design_basis
    earth, lps, why = None, None, []
    try:
        if basis.earthing is not None:
            earth = design_earthing(project, catalog=catalog)
            lps = earth.lps
        elif basis.lps is not None:
            lps = design_lps(project)
    except (EarthingError, CoverageError) as e:
        why.append(f"earthing and lightning protection material is not in the bill: {e}")
    out = (earth, lps, tuple(why))
    _last[:] = [(project, catalog, out)]
    return out


def earthing_lines(project: Project, catalog: Catalog | None = None) -> tuple[list[BomLine], tuple[str, ...], tuple[str, ...]]:
    """Bill lines, assumptions and warnings for the earthing and lightning designs of `project`; empty when it has neither."""
    earth, lps, why = designs(project, catalog)
    lines: list[BomLine] = []
    if earth is not None:
        for q in earth.quantities:
            if q.item.startswith(("pit.", "flat.")):
                lines.append(BomLine(EARTHING, q.description, None, float(q.quantity), q.unit, ("plan length of the earthing design; " if q.unit == "m" else "count of the earthing design; ") + NO_ITEM))
    if lps is not None and lps.terminals:
        n = len(lps.terminals)
        where = Counter("on roof" if t.on_roof else "on ground" for t in lps.terminals)
        placed = "; ".join(f"{k} {v}" for k, v in sorted(where.items()))
        what, mast_of = ("ESE lightning arrester", "ESE terminal") if lps.method == "ese" else ("Air terminal (rod)", "air terminal")
        mast = f", {lps.mast_height_m:g} m above the protected plane" if lps.mast_height_m else ""
        lines.append(BomLine(LIGHTNING, f"{what}, protection radius {lps.radius_m:.1f} m, level {lps.level}", None, float(n), "nos", f"{placed}; {NO_ITEM}"))
        lines.append(BomLine(LIGHTNING, f"Mast for {mast_of}{mast}", None, float(n), "nos", f"one per terminal; {NO_ITEM}"))
    assumptions = (NOT_COUNTED,) if lines else ()
    return lines, assumptions, why

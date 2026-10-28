"""Group rating factors for cables run in parallel, from the KEI Technical Handbook (PDF page 19-20, printed pages 36-37) and, for multicore cables
laid direct in ground beyond six circuits, the Havells industrial cables catalogue (printed page 53)."""

import math
from dataclasses import dataclass

from unicad.domain.catalog import CableSpec
from unicad.domain.network import Laying

KEI = "references/catalog-sources/raw/datasheets/cables/KEI_Technical_Handbook.pdf"
HAVELLS = "references/catalog-sources/raw/datasheets/cables/Havells_Industrial_Cables_Range_Catalogue.pdf"


@dataclass(frozen=True)
class Table:
    """A group rating factor table: factor by (racks or trays in tiers, circuits per rack). A table with one row is a single rack or a flat group."""

    what: str
    where: str
    rows: dict[int, dict[int, float]]
    tiers: str = ""

    def lookup(self, n: int) -> tuple[float, int, int, int] | None:
        """(factor, racks, circuits per rack, tabulated per-rack count) for the fewest tabulated racks that hold `n` circuits, the per-rack count
        rounded up to the next tabulated column; None when the table holds fewer than `n`."""
        for racks in sorted(self.rows):
            cols = self.rows[racks]
            per = math.ceil(n / racks)
            at = min((k for k in cols if k >= per), default=None)
            if at is not None:
                return cols[at], racks, per, at
        return None

    @property
    def capacity(self) -> int:
        return max(r * max(c) for r, c in self.rows.items())


KEI_MC_TRAY = Table(
    "multicore cables on cable trays exposed to air, cables touching", f"KEI Technical Handbook {KEI} p.20 (printed 37) multicore table C",
    {1: {1: 1.0, 2: 0.84, 3: 0.80, 6: 0.75, 9: 0.73}, 2: {1: 1.0, 2: 0.80, 3: 0.76, 6: 0.71, 9: 0.69}, 3: {1: 1.0, 2: 0.78, 3: 0.74, 6: 0.70, 9: 0.68}, 6: {1: 1.0, 2: 0.76, 3: 0.72, 6: 0.68, 9: 0.66}},
    "trays in tiers spaced 300 mm",
)  # fmt: skip
KEI_MC_TRENCH = Table(
    "multicore cables on trays in a covered concrete trench, spaced one diameter", f"KEI Technical Handbook {KEI} p.20 (printed 37) multicore table A",
    {1: {1: 0.95, 2: 0.90, 3: 0.88, 6: 0.85, 9: 0.84}, 2: {1: 0.90, 2: 0.85, 3: 0.83, 6: 0.81, 9: 0.80}, 3: {1: 0.88, 2: 0.83, 3: 0.81, 6: 0.79, 9: 0.78}, 6: {1: 0.86, 2: 0.81, 3: 0.79, 6: 0.77, 9: 0.76}},
    "trays in tiers spaced 300 mm",
)  # fmt: skip
KEI_MC_GROUND = Table(
    "multicore cables laid direct in ground in horizontal formation, touching",
    f"KEI Technical Handbook {KEI} p.20 (printed 37) multicore table D (2-6 cables); Havells industrial cables catalogue {HAVELLS} printed p.53 table 1, multicore horizontal touching (7-12 cables)",
    {1: {2: 0.79, 3: 0.69, 4: 0.62, 5: 0.58, 6: 0.54, 7: 0.52, 8: 0.50, 9: 0.48, 10: 0.46, 11: 0.45, 12: 0.44}},
)
KEI_SC_TRAY = Table(
    "single-core trefoils on racks/trays in open air, trefoils separated by two cable diameters", f"KEI Technical Handbook {KEI} p.20 (printed 37) single core table C",
    {1: {1: 1.0, 2: 0.98, 3: 0.96}, 2: {1: 1.0, 2: 0.95, 3: 0.93}, 3: {1: 1.0, 2: 0.94, 3: 0.92}, 6: {1: 1.0, 2: 0.93, 3: 0.90}},
    "racks in tiers spaced 300 mm",
)
KEI_SC_TRENCH = Table(
    "single-core trefoils on racks in a covered trench, separated by two diameters", f"KEI Technical Handbook {KEI} p.20 (printed 37) single core table B",
    {1: {1: 0.95, 2: 0.90, 3: 0.88}, 2: {1: 0.90, 2: 0.85, 3: 0.83}, 3: {1: 0.88, 2: 0.83, 3: 0.81}, 6: {1: 0.86, 2: 0.81, 3: 0.79}},
    "racks in tiers spaced 300 mm",
)
KEI_SC_GROUND = Table(
    "single-core trefoil groups laid direct in ground in horizontal formation, touching", f"KEI Technical Handbook {KEI} p.19 (printed 36) single core table A",
    {1: {2: 0.79, 3: 0.67, 4: 0.61, 5: 0.56, 6: 0.53, 7: 0.50, 8: 0.48, 9: 0.46, 10: 0.44, 11: 0.43, 12: 0.42}},
)
MULTICORE = {Laying.TRAY: KEI_MC_TRAY, Laying.AIR: KEI_MC_TRAY, Laying.TRENCH: KEI_MC_TRENCH, Laying.BURIED_DIRECT: KEI_MC_GROUND}
SINGLE_CORE = {Laying.TRAY: KEI_SC_TRAY, Laying.AIR: KEI_SC_TRAY, Laying.TRENCH: KEI_SC_TRENCH, Laying.BURIED_DIRECT: KEI_SC_GROUND}


@dataclass(frozen=True)
class Grouping:
    factor: float | None
    source: str


def grouping(cable: CableSpec, laying: Laying, runs: int, dc: bool = False) -> Grouping | None:
    """The group rating factor for `runs` circuits of `cable` laid together; None for a single run. More circuits than one rack or tray holds in the
    table are laid on the fewest tabulated racks in tiers that hold them (a stated installation arrangement). When no table covers the laying or the
    count, the factor is None and the source says why."""
    if runs <= 1:
        return None
    if dc:
        return Grouping(None, f"{runs} parallel DC runs: the KEI handbook group factors are for AC circuits, so no grouping factor is applied (assumption); set edge derating from the installation")
    table = (MULTICORE if cable.cores > 1 else SINGLE_CORE).get(laying)
    if table is None:
        return Grouping(None, f"{runs} parallel runs laid '{laying}': no group rating factor is cited for this laying, so none is applied (assumption); set edge derating from the installation")
    hit = table.lookup(runs)
    if hit is None:
        return Grouping(None, f"{runs} parallel runs laid '{laying}': the table for {table.what} holds at most {table.capacity} circuits, so no grouping factor is applied (assumption); set edge derating")
    factor, racks, per, at = hit
    laid = f" laid as {racks} {table.tiers.split()[0]} of up to {per} ({table.tiers}; installation arrangement assumed)" if racks > 1 else ""
    return Grouping(factor, f"group rating factor {factor:g} for {runs} circuits{laid} ({racks} x {at} in the table) of {table.what}, {table.where}")

"""Group rating factors for cables run in parallel, from the KEI Technical Handbook (PDF page 19-20, printed pages 36-37)."""

from dataclasses import dataclass

from unicad.domain.catalog import CableSpec
from unicad.domain.network import Laying

KEI = "references/catalog-sources/raw/datasheets/cables/KEI_Technical_Handbook.pdf"

MULTICORE = {
    Laying.TRAY: ("multicore cables on one cable tray exposed to air, cables touching", "p.20 (printed 37) table C, 1 tray", {1: 1.0, 2: 0.84, 3: 0.80, 6: 0.75, 9: 0.73}),
    Laying.AIR: ("multicore cables on one cable tray exposed to air, cables touching", "p.20 (printed 37) table C, 1 tray", {1: 1.0, 2: 0.84, 3: 0.80, 6: 0.75, 9: 0.73}),
    Laying.TRENCH: ("multicore cables on one tray in a covered concrete trench, spaced one diameter", "p.20 (printed 37) table A, 1 tray", {1: 0.95, 2: 0.90, 3: 0.88, 6: 0.85, 9: 0.84}),
    Laying.BURIED_DIRECT: ("multicore cables laid direct in ground in horizontal formation, touching", "p.20 (printed 37) table D", {2: 0.79, 3: 0.69, 4: 0.62, 5: 0.58, 6: 0.54}),
}  # fmt: skip
SINGLE_CORE = {
    Laying.TRAY: ("single-core trefoils on one tray in open air", "p.20 (printed 37) single core table C, 1 tray", {1: 1.0, 2: 0.98, 3: 0.96}),
    Laying.AIR: ("single-core trefoils on one tray in open air", "p.20 (printed 37) single core table C, 1 tray", {1: 1.0, 2: 0.98, 3: 0.96}),
    Laying.TRENCH: ("single-core trefoils on one rack in a covered trench, separated by two diameters", "p.20 (printed 37) single core table B, 1 tray", {1: 0.95, 2: 0.90, 3: 0.88}),
    Laying.BURIED_DIRECT: (
        "single-core trefoil groups laid direct in ground in horizontal formation, touching", "p.19 (printed 36) single core table A",
        {2: 0.79, 3: 0.67, 4: 0.61, 5: 0.56, 6: 0.53, 7: 0.50, 8: 0.48, 9: 0.46, 10: 0.44, 11: 0.43, 12: 0.42},
    ),
}  # fmt: skip


@dataclass(frozen=True)
class Grouping:
    factor: float | None
    source: str


def grouping(cable: CableSpec, laying: Laying, runs: int, dc: bool = False) -> Grouping | None:
    """The group rating factor for `runs` circuits of `cable` laid together; None for a single run. When the handbook has no row for
    the laying or the count (the next listed count above `runs` is used), the factor is None and the source says why."""
    if runs <= 1:
        return None
    if dc:
        return Grouping(None, f"{runs} parallel DC runs: the KEI handbook group factors are for AC circuits, so no grouping factor is applied (assumption); set edge derating from the installation")
    table = (MULTICORE if cable.cores > 1 else SINGLE_CORE).get(laying)
    if table is None:
        return Grouping(None, f"{runs} parallel runs laid '{laying}': no group rating factor is cited for this laying, so none is applied (assumption); set edge derating from the installation")
    what, where, rows = table
    at = min((n for n in rows if n >= runs), default=None)
    if at is None:
        return Grouping(None, f"{runs} parallel runs laid '{laying}': the KEI table for {what} stops at {max(rows)} circuits, so no grouping factor is applied (assumption); set edge derating")
    return Grouping(rows[at], f"group rating factor {rows[at]:g} for {runs} circuits ({at} in the table) of {what}, KEI Technical Handbook {KEI} {where}")

"""Breaker trip-unit settings: the long-time (overload) setting Ir an ACB or MCCB is set to, and the range its trip unit allows."""

from dataclasses import dataclass

from unicad.domain.catalog import ProtectionSpec
from unicad.domain.network import Node


@dataclass(frozen=True)
class IrRange:
    """Long-time setting range of a breaker series as a fraction of the frame rating In, and where it comes from."""

    lo: float
    hi: float
    source: str


MTZ_SOURCE = (
    "Schneider Electric MasterPacT MTZ selection guide 0614CT1701, catalogue page 22 (PDF page 23): MicroLogic 3.0 X and 5.0 X / 6.0 X long-time "
    "protection, Ir = 0.4 In to In in 1 A steps"
)
DSINE_SOURCE = (
    "Lauritz Knudsen DSine MCCB catalogue, page 30 'Protection Release for MCCBs': thermal-magnetic release overload setting 80 % - 100 % In for DN0 to DN3B "
    "(the MTX1.0 microprocessor release, not modelled in the catalog entry, allows 40 % to 100 % In)"
)
IR_RANGES: tuple[tuple[str, str, IrRange], ...] = (
    ("Schneider Electric", "MasterPacT MTZ", IrRange(0.4, 1.0, MTZ_SOURCE)),
    ("Lauritz Knudsen", "dsine", IrRange(0.8, 1.0, DSINE_SOURCE)),
)


def ir_range(spec: ProtectionSpec) -> IrRange | None:
    """The catalogued setting range of the breaker's series, from the catalog entry when it states one, else from the series table above."""
    lo, hi = getattr(spec, "ir_min_ratio", None), getattr(spec, "ir_max_ratio", None)
    if lo is not None:
        return IrRange(lo, hi if hi is not None else 1.0, f"{spec.id}: {spec.source.ref}")
    return next((r for maker, prefix, r in IR_RANGES if spec.manufacturer == maker and spec.model.startswith(prefix)), None)


def trip_current(node: Node, spec: ProtectionSpec) -> float | None:
    """The current an overcurrent device is set to trip at under overload: the breaker's long-time setting Ir when one is set, else its rating In."""
    if node.long_time_setting_a is not None and spec.device in ("mccb", "acb"):
        return node.long_time_setting_a
    return spec.rated_current

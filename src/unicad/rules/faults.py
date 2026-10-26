"""Fault level and clearing time at a system voltage, from the design basis."""

from dataclasses import dataclass

from unicad.domain.project import DesignBasis
from unicad.rules.model import Quantity
from unicad.rules.thresholds import FAULT_CLEARING_DEFAULT

VOLTAGE_MATCH = 0.02


@dataclass(frozen=True)
class Fault:
    voltage_v: float
    ka: float
    clearing_s: float
    clearing_note: str | None
    where: str

    def inputs(self) -> tuple[Quantity, ...]:
        return (
            Quantity(name="I_fault", value=self.ka, unit="kA", source=self.where),
            Quantity(name="t", value=self.clearing_s, unit="s", source=self.clearing_note or self.where),
        )

    @property
    def assumptions(self) -> tuple[str, ...]:
        return (self.clearing_note,) if self.clearing_note else ()


def fault_at(basis: DesignBasis, voltage_v: float | None) -> Fault | None:
    """The declared three-phase fault level whose system voltage is within 2 % of `voltage_v`; None when none is declared."""
    if voltage_v is None:
        return None
    level = next((f for f in basis.fault_levels if abs(f.voltage_v - voltage_v) <= VOLTAGE_MATCH * f.voltage_v), None)
    if level is None:
        return None
    where = "design_basis.fault_levels" + (f" ({level.source})" if level.source else "")
    if level.clearing_s is not None:
        return Fault(level.voltage_v, level.fault_ka, level.clearing_s, None, where)
    earthing = basis.earthing
    if earthing is not None and earthing.fault_clearing_s is not None:
        note = f"No clearing time with the {level.voltage_v:g} V fault level: design_basis.earthing.fault_clearing_s ({earthing.fault_clearing_s:g} s) is used."
        return Fault(level.voltage_v, level.fault_ka, earthing.fault_clearing_s, note, where)
    note = f"No clearing time with the {level.voltage_v:g} V fault level: {FAULT_CLEARING_DEFAULT.assumption}"
    return Fault(level.voltage_v, level.fault_ka, FAULT_CLEARING_DEFAULT.value, note, where)

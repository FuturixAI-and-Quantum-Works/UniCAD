"""Fault level and clearing time at a system voltage, from the design basis."""

import math
from dataclasses import dataclass
from unicad.catalog.store import Catalog
from unicad.domain.catalog import TransformerSpec
from unicad.domain.network import NodeKind
from unicad.domain.project import DesignBasis, Project
from unicad.rules.model import Quantity
from unicad.rules.topology import zone_at
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
    t, note = default_clearing(basis, level.voltage_v)
    return Fault(level.voltage_v, level.fault_ka, t, note, where)


def default_clearing(basis: DesignBasis, voltage_v: float) -> tuple[float, str]:
    """Clearing time when the fault level at `voltage_v` gives none: the earthing basis's, else FAULT_CLEARING_DEFAULT; with the note saying which."""
    earthing = basis.earthing
    if earthing is not None and earthing.fault_clearing_s is not None:
        return earthing.fault_clearing_s, f"No clearing time with the {voltage_v:g} V fault level: design_basis.earthing.fault_clearing_s ({earthing.fault_clearing_s:g} s) is used."
    return FAULT_CLEARING_DEFAULT.value, f"No clearing time with the {voltage_v:g} V fault level: {FAULT_CLEARING_DEFAULT.assumption}"


LV_BREAKER_CLEARING_S = 0.1
LV_BREAKER_CLEARING = (
    "Assumption: a fault on an LT cable protected by an MCB, MCCB or ACB is cleared by the breaker's instantaneous trip, taken as 0.1 s: the shortest duration for which "
    "IEC 60364-4-43 uses the adiabatic A >= I sqrt(t) / k (below it the device's let-through I2t from the manufacturer applies). An instantaneous trip clears faster, so this is "
    "conservative; it is not a datasheet break time. Give the LT level a clearing_s and raise this where an ACB has a short-time delay set for selectivity."
)

TRANSFORMER_FED = "Inverter and PCS fault contributions are not included (no catalog field gives them); the transformer-fed current is the prospective fault at the LV bus."


@dataclass(frozen=True)
class Prospective:
    """Three-phase prospective fault current at a device: the declared level for its system voltage, or the current fed through the
    transformers whose LV winding feeds its zone (impedance on the winding base, plus the declared HV source impedance), whichever is larger."""

    ka: float
    voltage_v: float
    where: str
    inputs: tuple[Quantity, ...]
    assumptions: tuple[str, ...]
    formula: str


def transformer_fault_ka(winding_kva: float, impedance_pct: float, lv_v: float, hv_v: float, basis: DesignBasis) -> tuple[float, float | None, str | None]:
    """LV bus fault current fed through one transformer winding: I = S / (sqrt(3) V (z_t + z_s)) with z_s = S / S_sc of the declared HV level.
    Returns (kA, HV source fault MVA or None, the assumption when no HV level is declared)."""
    hv = fault_at(basis, hv_v)
    s_sc = math.sqrt(3) * hv_v * hv.ka / 1000 if hv else None
    z = impedance_pct / 100 + (winding_kva / 1000 / s_sc if s_sc else 0.0)
    note = None if hv else f"No fault level is declared at {hv_v:g} V: the transformer HV side is taken as an infinite bus (conservative)."
    return winding_kva / (math.sqrt(3) * lv_v / 1000 * z) / 1000, s_sc, note


def prospective_fault(project: Project, catalog: Catalog, node_id: str) -> Prospective | None:
    return prospective_in(project, catalog, zone_at(project.network, node_id))


def prospective_in(project: Project, catalog: Catalog, zone) -> Prospective | None:
    net, basis = project.network, project.design_basis
    if zone is None or zone.voltage_v is None:
        return None
    declared = fault_at(basis, zone.voltage_v)
    best = None
    if declared is not None:
        best = Prospective(declared.ka, declared.voltage_v, declared.where, declared.inputs()[:1], (), "")
    fed, inputs, notes = 0.0, [], [TRANSFORMER_FED]
    for n in net.by_kind(NodeKind.TRANSFORMER):
        if n.spec is None or zone_at(net, n.id, "lv") != zone:
            continue
        spec = catalog.get(n.spec)
        if not isinstance(spec, TransformerSpec):
            continue
        base = spec.impedance_base_kva or (spec.winding_kva[0] if spec.winding_kva else spec.rated_kva)
        hv_zone = zone_at(net, n.id, "hv")
        ka, s_sc, note = transformer_fault_ka(base, spec.impedance_pct, zone.voltage_v, hv_zone.voltage_v if hv_zone and hv_zone.voltage_v else spec.hv_kv * 1000, basis)
        fed += ka
        src = spec.id if isinstance(n.spec, str) else f"{n.id} inline spec {spec.id}"
        inputs += [Quantity(name=f"S[{n.id}]", value=base, unit="kVA", source=src), Quantity(name=f"Z[{n.id}]", value=spec.impedance_pct, unit="%", source=src)]
        if s_sc:
            inputs.append(Quantity(name="S_sc_hv", value=round(s_sc, 1), unit="MVA", source="design_basis.fault_levels"))
        if note:
            notes.append(note)
    if fed and (best is None or fed > best.ka):
        inputs.append(Quantity(name="V", value=zone.voltage_v, unit="V", source="network.voltage_zone"))
        best = Prospective(round(fed, 2), zone.voltage_v, "transformer-fed", tuple(inputs), tuple(dict.fromkeys(notes)), "I = S / (sqrt(3) V (Z% / 100 + S / S_sc))")
    return best

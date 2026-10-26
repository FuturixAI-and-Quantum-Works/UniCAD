"""Cable schedule data: one row per cable (edge instance), grouped in sections by template edge."""

from dataclasses import dataclass, field
from enum import StrEnum

from unicad.domain.network import Service
from unicad.routing import TemplateLength


class LengthMode(StrEnum):
    AUTO = "auto"
    NETWORK = "network"
    ESTIMATED = "estimated"


@dataclass(frozen=True)
class CableRow:
    sl: int
    cable_id: str
    edge: str
    instance: str
    src: str
    dst: str
    service: Service
    block: str
    is_string: bool
    from_tag: str
    to_tag: str
    cable_ref: str
    callout: str
    cores: str
    size_mm2: float
    conductor: str
    runs: int
    cables_per_run: int
    length_pos_m: float | None
    length_neg_m: float | None
    length_source: str
    length_basis: str
    laying: str | None
    ib_a: float | None
    iz_a: float | None
    vd_pct: float | None
    loss_w: float | None
    module: str | None
    module_wp: float | None
    remarks: str

    @property
    def is_dc(self) -> bool:
        return self.service is Service.DC

    @property
    def run_length_m(self) -> float | None:
        """Route length of one run: the mean of the polarities for DC, else the single length."""
        if self.length_pos_m is None:
            return None
        return self.length_pos_m if self.length_neg_m is None else (self.length_pos_m + self.length_neg_m) / 2

    @property
    def cable_metres(self) -> float:
        """Cable to be supplied for this row, all runs and conductors together."""
        if self.length_pos_m is None:
            return 0.0
        if self.length_neg_m is not None and self.cables_per_run == 2:
            return self.runs * (self.length_pos_m + self.length_neg_m)
        return self.runs * self.cables_per_run * self.length_pos_m


@dataclass(frozen=True)
class Section:
    title: str
    edge: str
    service: Service
    rows: tuple[CableRow, ...]


@dataclass(frozen=True)
class Schedule:
    project_id: str
    project_name: str
    sections: tuple[Section, ...]
    basis: tuple[tuple[str, str], ...]
    vd_summary: tuple[tuple[str, str], ...]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]
    gaps: tuple[str, ...]
    template_lengths: dict[str, TemplateLength] = field(default_factory=dict)

    @property
    def rows(self) -> tuple[CableRow, ...]:
        return tuple(r for s in self.sections for r in s.rows)

    def totals(self) -> dict[str, float]:
        out: dict[str, float] = {"rows": float(len(self.rows)), "cable_m": 0.0, "loss_w": 0.0}
        for r in self.rows:
            out["cable_m"] += r.cable_metres
            out["loss_w"] += r.loss_w or 0.0
            key = f"cable_m_{r.service.value}"
            out[key] = out.get(key, 0.0) + r.cable_metres
        return {k: round(v, 2) for k, v in out.items()}

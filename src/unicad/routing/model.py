"""Result types of route estimation. Lengths are metres of cable per instance of a template edge."""

from dataclasses import dataclass, field
from statistics import fmean


@dataclass(frozen=True)
class RouteAssumptions:
    """Allowances added to the measured horizontal run, and where the unlocated switchyard stands.

    `drop_per_end_m` is the vertical run at each end of a cable (module rail to trench, trench to terminal);
    `termination_m` the slack kept at each end for making the termination. Both are design assumptions, not
    catalogue data; every estimate reports them.
    """

    drop_per_end_m: float = 1.5
    termination_m: float = 1.0
    switchyard: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if self.drop_per_end_m < 0 or self.termination_m < 0:
            raise ValueError("drop_per_end_m and termination_m cannot be negative")

    @property
    def per_cable_m(self) -> float:
        return 2 * (self.drop_per_end_m + self.termination_m)

    def statements(self) -> tuple[str, ...]:
        return (
            "Horizontal run is the Manhattan distance in the array frame (along the table rows, then across the rows through the gaps between tables); site roads and obstacles are not routed around.",
            f"Each cable gets {self.drop_per_end_m:g} m of vertical drop and {self.termination_m:g} m of termination slack at each end ({self.per_cable_m:g} m per cable): design assumptions, not site measurements.",
            "A string's +ve and -ve leads run from the first and last module of the string, so they differ by the length of the string along the table.",
            "Combiner boxes stand at the centroid of the strings they collect; equipment the layout places in the field (string inverters at the end of their table rows, BESS units) "
            "stands where the layout puts it, DC isolators at the inverter they feed; transformers and block switchgear stand at the inverter station of the layout block holding most of their strings, "
            "and plant-level equipment at the layout's switchyard location.",
        )


@dataclass(frozen=True)
class InstanceLength:
    """Estimated length of one instance of a template edge. `negative_m` is set for DC edges, where the two polarities are routed separately."""

    instance: str
    edge: str
    positive_m: float
    negative_m: float | None
    straight_pos_m: float
    straight_neg_m: float | None
    basis: str

    @property
    def effective_m(self) -> float:
        """One-way length that gives the same loop resistance: the mean of the two polarities."""
        return self.positive_m if self.negative_m is None else (self.positive_m + self.negative_m) / 2


@dataclass(frozen=True)
class TemplateLength:
    edge: str
    count: int
    minimum_m: float
    mean_m: float
    maximum_m: float
    note: str

    def length(self, how: str = "max") -> float:
        if how not in ("max", "mean"):
            raise ValueError(f"how must be 'max' or 'mean', not {how!r}")
        return self.maximum_m if how == "max" else self.mean_m


@dataclass(frozen=True)
class RouteEstimate:
    instances: dict[str, InstanceLength] = field(default_factory=dict)
    unresolved: dict[str, str] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def templates(self) -> dict[str, TemplateLength]:
        by_edge: dict[str, list[float]] = {}
        for i in self.instances.values():
            by_edge.setdefault(i.edge, []).append(i.effective_m)
        out = {}
        for edge, values in by_edge.items():
            lo, hi = min(values), max(values)
            note = f"{len(values)} instances, {lo:.1f} to {hi:.1f} m; the maximum is taken for the template (worst case), the mean is {fmean(values):.1f} m"
            out[edge] = TemplateLength(edge, len(values), lo, fmean(values), hi, note)
        return out

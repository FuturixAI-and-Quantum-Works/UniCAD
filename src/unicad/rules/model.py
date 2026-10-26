"""Findings, reports and the issue gate's result."""

from collections import Counter
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, computed_field

from unicad.domain.project import IssuedFor


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


class Quantity(_Frozen):
    """One input to a calculation. `source` names where the value came from: a catalog id, a design-basis field, a threshold or the network."""

    name: str
    value: float | int | str
    unit: str | None = None
    source: str | None = None


class Calc(_Frozen):
    """A computed value with the working shown."""

    value: float
    unit: str
    formula: str
    inputs: tuple[Quantity, ...] = ()
    assumptions: tuple[str, ...] = ()


class Finding(_Frozen):
    rule_id: str
    severity: Severity
    subjects: tuple[str, ...]
    message: str
    measured: float | None = None
    limit: float | None = None
    unit: str | None = None
    clause: str | None = None
    formula: str | None = None
    inputs: tuple[Quantity, ...] = ()
    assumptions: tuple[str, ...] = ()

    def sort_key(self) -> tuple:
        return (SEVERITY_ORDER[self.severity], self.rule_id, self.subjects, self.message)


class Report(_Frozen):
    project_id: str
    rules: tuple[str, ...]
    findings: tuple[Finding, ...]

    @computed_field
    @property
    def counts(self) -> dict[str, int]:
        c = Counter(f.severity for f in self.findings)
        return {s.value: c.get(s, 0) for s in Severity}

    @computed_field
    @property
    def blocking(self) -> bool:
        return any(f.severity is Severity.ERROR for f in self.findings)

    def by_rule(self, rule_id: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.rule_id == rule_id)

    def render(self) -> str:
        c = self.counts
        lines = [f"{self.project_id}: {c['error']} error, {c['warning']} warning, {c['info']} info ({len(self.rules)} rules)"]
        for f in self.findings:
            extra = f" [{f.measured:g} vs {f.limit:g}{' ' + f.unit if f.unit else ''}]" if f.measured is not None and f.limit is not None else ""
            lines.append(f"  {f.severity.value.upper():7} {f.rule_id}: {f.message}{extra}")
        return "\n".join(lines)


class Gate(_Frozen):
    """Whether a sheet may be issued for `issued_for`. Allowed drafts that still carry errors need a NOT FOR CONSTRUCTION watermark."""

    issued_for: IssuedFor
    allowed: bool
    watermark: bool
    reasons: tuple[str, ...]


class Explanation(_Frozen):
    rule_id: str
    family: str
    title: str
    docs: str
    severity: Severity
    subjects: tuple[str, ...]
    message: str
    formula: str | None
    inputs: tuple[Quantity, ...]
    measured: float | None
    limit: float | None
    unit: str | None
    clause: str | None
    assumptions: tuple[str, ...]
    sources: dict[str, str]

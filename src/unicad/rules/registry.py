"""Rule registry, runner, issue gate and explain."""

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from unicad.catalog.store import Catalog
from unicad.domain.project import IssuedFor, Project, Segment
from unicad.rules.model import Explanation, Finding, Gate, Quantity, Report, Severity

Check = Callable[[Project, Catalog], Iterable[Finding]]
ALL_SEGMENTS = frozenset(Segment)
BLOCKED_BY_ERRORS = frozenset({IssuedFor.APPROVAL, IssuedFor.CONSTRUCTION, IssuedFor.AS_BUILT})


@dataclass(frozen=True)
class Rule:
    id: str
    family: str
    title: str
    clause: str | None
    applies_to: frozenset[Segment]
    docs: str
    check: Check


REGISTRY: dict[str, Rule] = {}


def rule(id: str, title: str, clause: str | None = None, applies_to: Iterable[Segment] = ALL_SEGMENTS) -> Callable[[Check], Check]:
    """Register a check. The family is the id's prefix; the docs are the function's docstring."""

    def register(check: Check) -> Check:
        if id in REGISTRY:
            raise ValueError(f"duplicate rule id {id!r}")
        family = id.split(".", 1)[0]
        REGISTRY[id] = Rule(id, family, title, clause, frozenset(applies_to), inspect.cleandoc(check.__doc__ or ""), check)
        return check

    return register


def families() -> tuple[str, ...]:
    return tuple(dict.fromkeys(r.family for r in REGISTRY.values()))


def run_rules(project: Project, catalog: Catalog, families: Iterable[str] | None = None) -> Report:
    wanted = set(families) if families is not None else None
    if wanted is not None and (unknown := wanted - {r.family for r in REGISTRY.values()}):
        raise ValueError(f"unknown rule families {sorted(unknown)}")
    if problems := project.catalog_problems(catalog):
        raise ValueError("project does not resolve against the catalog: " + "; ".join(problems))
    ran, findings = [], []
    for r in REGISTRY.values():
        if (wanted is not None and r.family not in wanted) or project.segment not in r.applies_to:
            continue
        ran.append(r.id)
        for f in r.check(project, catalog):
            if f.rule_id != r.id:
                raise ValueError(f"rule {r.id!r} produced a finding for {f.rule_id!r}")
            findings.append(f if f.clause or not r.clause else f.model_copy(update={"clause": r.clause}))
    return Report(project_id=project.id, rules=tuple(ran), findings=tuple(sorted(findings, key=Finding.sort_key)))


def issue_gate(report: Report, issued_for: IssuedFor | str) -> Gate:
    purpose = IssuedFor(issued_for)
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    reasons = tuple(f"{f.rule_id} [{', '.join(f.subjects)}]: {f.message}" for f in errors)
    if purpose in BLOCKED_BY_ERRORS:
        return Gate(issued_for=purpose, allowed=not errors, watermark=False, reasons=reasons)
    return Gate(issued_for=purpose, allowed=True, watermark=bool(errors), reasons=reasons)


def _sources(inputs: tuple[Quantity, ...], catalog: Catalog | None) -> dict[str, str]:
    out = {}
    for q in inputs:
        if q.source is None or q.source in out:
            continue
        ref = q.source
        if catalog is not None:
            try:
                spec = catalog.get(q.source)
            except KeyError:
                pass
            else:
                ref = f"{spec.manufacturer} {spec.model}: {spec.source.kind} {spec.source.ref}"
        out[q.source] = ref
    return out


def explain(finding: Finding, catalog: Catalog | None = None) -> Explanation:
    """The rule behind a finding, its formula and inputs, and where each input came from (catalog sources resolved when a catalog is given)."""
    r = REGISTRY[finding.rule_id]
    return Explanation(
        rule_id=r.id,
        family=r.family,
        title=r.title,
        docs=r.docs,
        severity=finding.severity,
        subjects=finding.subjects,
        message=finding.message,
        formula=finding.formula,
        inputs=finding.inputs,
        measured=finding.measured,
        limit=finding.limit,
        unit=finding.unit,
        clause=finding.clause,
        assumptions=finding.assumptions,
        sources=_sources(finding.inputs, catalog),
    )

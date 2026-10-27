from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.domain.project import IssuedFor
from unicad.mcp.errors import guarded
from unicad.mcp.schema import RuleFamiliesArg
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Workspace
from unicad.rules import REGISTRY, Finding, Severity, explain, issue_gate, run_rules
from unicad.tenants.store import thresholds_of

READ_ONLY = ToolAnnotations(readOnlyHint=True)
SIZED_BY_AUTOBUILD = ("protection.fault_level", "cables.short_circuit", "structure.missing_spec")


def rebuild_hint(findings: tuple[Finding, ...]) -> str | None:
    """What to do when errors are in quantities network_autobuild sizes from the design basis (breaking capacity, short-circuit withstand, breaker specs), e.g. after fault levels were added later."""
    ids = sorted({f.rule_id for f in findings if f.severity is Severity.ERROR and f.rule_id.startswith(SIZED_BY_AUTOBUILD)})
    if not ids:
        return None
    return (f"Errors in {', '.join(ids)} concern ratings network_autobuild sizes: re-run network_autobuild with the same intent so breakers and cables "
            "are sized for the current design basis (fault levels, limits; hand edits made since the last build are replaced), then validate again.")


EARTHING_HINTS = {
    "earthing.touch_step": (
        "touch or step voltage above the IEEE 80 tolerable value: give the switchyard a crushed-rock surface layer with earthing_design(project_id, "
        "surface_resistivity=<ohm m, from the rock supplier or the IEEE 80 table of surface materials>, surface_layer_m=<m>), or a larger grid (switchyard_m) or a "
        "shorter clearing time (clearing_s from the protection study), then validate again"
    ),
    "earthing.electrode_resistance": (
        "earth resistance above the target: earthing_design picks the pit count for the target, so re-run earthing_design (with the measured "
        "soil_resistivity, or a longer electrode_length_m) and check target_ohm against the utility's requirement"
    ),
    "earthing.conductor_size": "earth conductor below the fault duty: re-run earthing_design with the fault_ka and clearing_s of the earth fault so it re-sizes the conductor",
    "earthing.every_equipment_bonded": "equipment not bonded to the earth network: re-run earthing_design after the latest layout_place and network_autobuild so every item is laid out",
}


def earthing_hint(findings: tuple[Finding, ...]) -> str | None:
    """What to change when earthing rules report errors: the tool and the parameter that moves each limit."""
    ids = sorted({f.rule_id for f in findings if f.severity is Severity.ERROR and f.rule_id in EARTHING_HINTS})
    return "; ".join(f"{i}: {EARTHING_HINTS[i]}" for i in ids) + "." if ids else None


def compact(f: Finding) -> dict[str, Any]:
    out: dict[str, Any] = {"rule_id": f.rule_id, "severity": f.severity.value, "subjects": list(f.subjects), "message": f.message}
    for key in ("measured", "limit", "unit"):
        if (v := getattr(f, key)) is not None:
            out[key] = v
    return out


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool(annotations=READ_ONLY)
    @guarded
    def validate(
        project_id: ProjectIdArg,
        families: RuleFamiliesArg = None,
        include_info: Annotated[bool, Field(description="Also list info findings (segment voltage drops, unstated deratings); they are always counted.")] = False,
        revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """Run the engineering rules on a project and say whether its sheets may be issued for each purpose.

        Returns finding counts, the findings (errors and warnings first; info only with include_info=true), and a gate per issued_for:
        approval, construction and as_built are blocked by any error; proposal and information are allowed with a NOT FOR CONSTRUCTION watermark.
        Use explain_finding for the formula, inputs and sources behind one finding.
        Example: validate(project_id="rooftop-500kwp", families=["cables", "vdrop"])."""
        project, rev = ws.load(project_id, revision)
        company = thresholds_of(project.company_profile, ws.config.workspace)
        report = run_rules(project, ws.catalog, families, company)
        shown = [compact(f) for f in report.findings if include_info or f.severity is not Severity.INFO]
        gates = {p.value: {"allowed": (g := issue_gate(report, p)).allowed, "watermark": g.watermark} for p in IssuedFor}
        out = {"project_id": project_id, "revision": rev, "rules": len(report.rules), "counts": report.counts, "blocking": report.blocking, "gates": gates, "findings": shown}
        hints = [h for h in (rebuild_hint(report.findings) if project.network.nodes else None, earthing_hint(report.findings)) if h]
        if hints:
            out["next"] = " ".join(hints)
        if company:
            out["company_thresholds"] = {"profile": project.company_profile, "values": {name: s.value for name, s in company.items()}}
        return out

    @server.tool(annotations=READ_ONLY)
    @guarded
    def explain_finding(
        project_id: ProjectIdArg,
        rule_id: Annotated[Literal[tuple(REGISTRY)], Field(description="Rule id from validate, e.g. 'cables.ampacity'")],
        subject: Annotated[str, Field(description="One of the finding's subjects from validate: a node or edge id, e.g. 'e_inv_mccb'")],
        revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """Explain the findings of one rule about one subject: what the rule checks, the formula, every input with its source (catalog datasheet, design basis, network), the assumptions, and the measured value against the limit.

        Example: explain_finding(project_id="rooftop-500kwp", rule_id="vdrop.ac", subject="inv")."""
        project, _ = ws.load(project_id, revision)
        family = rule_id.split(".", 1)[0]
        report = run_rules(project, ws.catalog, [family], thresholds_of(project.company_profile, ws.config.workspace))
        if rule_id not in report.rules:
            raise KeyError(f"no rule {rule_id!r} ran for this project; rules: {[r for r in report.rules if r.startswith(family + '.')]}")
        found = [f for f in report.by_rule(rule_id) if subject in f.subjects]
        if not found:
            subjects = sorted({s for f in report.by_rule(rule_id) for s in f.subjects})
            raise KeyError(f"rule {rule_id!r} has no finding about {subject!r}; its subjects: {subjects or 'none (the rule passed)'}")
        return {"project_id": project_id, "explanations": [explain(f, ws.catalog).model_dump(mode="json") for f in found]}

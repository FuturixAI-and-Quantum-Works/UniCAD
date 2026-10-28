from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.design import Intent, autobuild, size_strings
from unicad.design.autobuild import Template
from unicad.domain.catalog import InverterSpec, ModuleSpec
from unicad.domain.project import Project
from unicad.mcp.errors import guarded
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.tools.rules import compact
from unicad.mcp.workspace import Edit, Workspace
from unicad.tenants.store import thresholds_of

READ_ONLY = ToolAnnotations(readOnlyHint=True)
LIST_CAP = 40


def _spec(ws: Workspace, spec_id: str, cls: type, what: str):
    try:
        spec = ws.catalog.get(spec_id)
    except KeyError:
        raise KeyError(f"no catalog entry {spec_id!r}; catalog_search(kind='{what}') lists them") from None
    if not isinstance(spec, cls):
        raise ValueError(f"{spec_id!r} is a {spec.kind}, not a {what}")
    return spec


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool(annotations=READ_ONLY)
    @guarded
    def design_strings(
        project_id: ProjectIdArg,
        module: Annotated[str, Field(description="Catalog id of the PV module")],
        inverter: Annotated[str, Field(description="Catalog id of the inverter")],
        modules_per_string: Annotated[int | None, Field(gt=0, description="Check a given string length instead of picking the longest")] = None,
        revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """Size PV strings for a module on an inverter against the project's design basis, with the rules engine's temperature models.

        Returns the allowed modules-per-string range and the chosen length, string voltages at the coldest and hottest cell temperatures,
        strings per MPPT and per inverter (limit and the count nearest the DC/AC target), the DC/AC ratio achieved, which constraint binds
        each quantity, and the margins to each limit. strings_per_inverter respects the string-fuse rule validate applies (pv_strings.string_fuses):
        strings_per_mppt_unfused / strings_per_inverter_unfused are the most that need no fuse; string_fuses is 'optional' when the DC/AC target needs
        more, with the trade-off in the assumptions (pass that strings_per_inverter to network_autobuild for a design with one gPV fuse per string).
        Example: design_strings(project_id="p1", module="waaree-bi-55-540", inverter="sungrow-sg110cx")."""
        p, rev = ws.load(project_id, revision)
        d = size_strings(_spec(ws, module, ModuleSpec, "module"), _spec(ws, inverter, InverterSpec, "inverter"), p.design_basis, modules_per_string)
        return {"project_id": project_id, "revision": rev} | d.model_dump(mode="json")

    @server.tool()
    @guarded
    def network_autobuild(
        project_id: ProjectIdArg,
        template: Annotated[Template | None, Field(description="Topology; default from the project segment (utility: central when the inverter is central, else string)")] = None,
        ac_kw: Annotated[float | None, Field(gt=0, description="PV AC capacity in kW (sets the inverter count); for bess_ac the storage power")] = None,
        dc_kwp: Annotated[float | None, Field(gt=0, description="PV DC capacity in kWp")] = None,
        module: Annotated[str | None, Field(description="Catalog module id; default the highest-Pmax ALMM-listed module")] = None,
        inverter: Annotated[str | None, Field(description="Catalog inverter id; default picked per template")] = None,
        grid_voltage_v: Annotated[float | None, Field(gt=0, description="Grid connection voltage; updates the design basis when it differs")] = None,
        modules_per_string: Annotated[int | None, Field(gt=0, description="Fix the string length (default: longest inside the limits)")] = None,
        strings_per_inverter: Annotated[int | None, Field(gt=0, description="Fix strings per inverter (default: nearest the DC/AC target)")] = None,
        inverters_per_block: Annotated[int | None, Field(gt=0, description="Inverters per transformer block (central default 2)")] = None,
        step_up_kv: Annotated[float | None, Field(gt=0, description="C&I: step-up voltage in kV for an HT connection")] = None,
        connection: Annotated[Literal["lt_panel", "dedicated_feeder"], Field(description="C&I LT: via the consumer LT panel or a dedicated feeder")] = "lt_panel",
        metering: Annotated[Literal["net", "abt"], Field(description="C&I HT metering: net or ABT")] = "net",
        block_switch: Annotated[Literal["vcb", "rmu"], Field(description="Switch on each block transformer's HV side")] = "vcb",
        auxiliary: Annotated[bool, Field(description="Add block and plant auxiliary transformers")] = True,
        export_limit_kw: Annotated[float | None, Field(gt=0, description="Sanctioned export capacity in kW, recorded on the POI")] = None,
        export_control: Annotated[Literal["none", "ppc"], Field(description="ppc: a power plant controller limits the export at the POI to export_limit_kw (PV inverters + BESS PCS may exceed it)")] = "none",
        bess_kw: Annotated[float | None, Field(gt=0, description="Storage power in kW (bess_dc, hybrid)")] = None,
        bess_kwh: Annotated[float | None, Field(gt=0, description="Storage energy in kWh")] = None,
        pcs: Annotated[str | None, Field(description="Catalog PCS id")] = None,
        battery: Annotated[str | None, Field(description="Catalog battery id")] = None,
        use_layout: Annotated[bool, Field(description="Build the PV plan from the project's array layout (layout_place) and link every layout string to one pv_string instance")] = True,
        lengths: Annotated[dict[str, float] | None, Field(description="Cable route length overrides in m by role, e.g. {\"vcb_bus\": 650}")] = None,
        bess_x: Annotated[float | None, Field(description="Local x (m) of the BESS area centre (bess_ac, hybrid); default the free area nearest the switchyard")] = None,
        bess_y: Annotated[float | None, Field(description="Local y (m) of the BESS area centre")] = None,
    ) -> dict[str, Any]:
        """Replace the project's electrical network with a complete one built from a short intent: strings sized from the catalog and design basis,
        a topology template (residential, ci_rooftop, utility_string, utility_central, bess_ac, bess_dc, hybrid) with protection, metering, SPDs and
        earthing, breakers rated >= 1.25 x design current and cables sized for ampacity and voltage drop. Then runs the rules.

        Returns the new revision, a summary (instances per kind, DC kWp, AC kVA), rule counts, the remaining error and warning findings, blockers
        (what the catalog or the intent could not satisfy) and every assumption made. Example:
        network_autobuild(project_id="p1", template="ci_rooftop", ac_kw=500, module="adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp", inverter="huawei-sun2000-100ktl-m2")."""
        intent = Intent(
            template=template, ac_kw=ac_kw, dc_kwp=dc_kwp, module=module, inverter=inverter, grid_voltage_v=grid_voltage_v,
            modules_per_string=modules_per_string, strings_per_inverter=strings_per_inverter, inverters_per_block=inverters_per_block,
            step_up_kv=step_up_kv, connection=connection, metering=metering, block_switch=block_switch, auxiliary=auxiliary,
            export_limit_kw=export_limit_kw, export_control=export_control, bess_kw=bess_kw, bess_kwh=bess_kwh, pcs=pcs, battery=battery, lengths=lengths or {}, bess_x=bess_x, bess_y=bess_y,
        )  # fmt: skip

        def change(p: Project) -> Edit:
            layout = p.array_layout if use_layout and p.array_layout is not None and p.array_layout.blocks else None
            company = thresholds_of(p.company_profile, ws.config.workspace)
            r = autobuild(p, ws.catalog, intent, layout=layout, thresholds=company)
            findings = [compact(f) for f in r.findings]
            extra = {
                "template": r.template,
                "design": r.summary,
                "rules": r.counts,
                "findings": findings[:LIST_CAP],
                "findings_truncated": max(0, len(findings) - LIST_CAP),
                "blockers": list(r.blockers),
                "assumptions": list(r.assumptions),
                "strings_design": r.strings.model_dump(mode="json") if r.strings else None,
            }
            if company:
                extra["company_thresholds"] = {"profile": p.company_profile, "values": {name: s.value for name, s in company.items()}}
            problems = tuple({"severity": "warning", "subject": "autobuild", "message": b} for b in r.blockers)
            return Edit(r.project, tuple(n.id for n in r.project.network.nodes), extra, problems)

        return ws.mutate(project_id, change)

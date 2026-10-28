from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.design import Intent, autobuild, size_strings
from unicad.design.strings import InverterStrings, check_inverter_strings
from unicad.design.autobuild import Template
from unicad.domain.catalog import InverterSpec, ModuleSpec
from unicad.domain.network import RelayRating
from unicad.domain.project import Project
from unicad.mcp.errors import guarded
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.tools.rules import compact
from unicad.mcp.workspace import Edit, Workspace
from unicad.rules import Severity, run_rules
from unicad.rules.thresholds import using
from unicad.store.diff import network_edits, reapply_network_edits
from unicad.tenants.store import thresholds_of

READ_ONLY = ToolAnnotations(readOnlyHint=True)
LIST_CAP = 40
InverterStringsArg = Annotated[
    list[InverterStrings] | None,
    Field(description="Strings given per inverter and MPPT, inverter classes in numbering order: each {\"count\": inverters, \"mppts\": [[modules per string of each string on MPPT 1], [..MPPT 2], ...]}, "
                      "e.g. [{\"count\": 4, \"mppts\": [[18, 18], [18, 18], [18, 18], [18, 18], [18, 18], [18, 18], [18, 18]]}, {\"count\": 1, \"mppts\": [[18, 19], [18, 19], [18, 19], [18, 19], [18, 19], [18, 19], [18]]}]"),
]  # fmt: skip
AUTOBUILD_MARK = "network_autobuild"
DISCARDED_MARK = "network_autobuild_discarded"


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
        inverter_strings: InverterStringsArg = None,
        revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """Size PV strings for a module on an inverter against the project's design basis, with the rules engine's temperature models.

        Returns the allowed modules-per-string range and the chosen length, string voltages at the coldest and hottest cell temperatures,
        strings per MPPT and per inverter (limit and the count nearest the DC/AC target), the DC/AC ratio achieved, which constraint binds
        each quantity, and the margins to each limit. strings_per_inverter respects the string-fuse rule validate applies (pv_strings.string_fuses):
        strings_per_mppt_unfused / strings_per_inverter_unfused are the most that need no fuse; string_fuses is 'optional' when the DC/AC target needs
        more, with the trade-off in the assumptions (pass that strings_per_inverter to network_autobuild for a design with one gPV fuse per string).
        With inverter_strings (strings given per inverter and MPPT, lengths that may differ, e.g. 18 and 19 modules on one inverter, and modules per inverter that
        differ between inverters) it checks those instead: every length's Voc at the coldest cell and MPPT window, the MPPTs and strings per MPPT against the
        inverter, and the DC kWp and DC/AC ratio of each inverter class; `given_strings.problems` lists what does not fit (empty when network_autobuild will take them).
        Example: design_strings(project_id="p1", module="waaree-bi-55-540", inverter="sungrow-sg110cx")."""
        p, rev = ws.load(project_id, revision)
        company = thresholds_of(p.company_profile, ws.config.workspace)
        mod, inv = _spec(ws, module, ModuleSpec, "module"), _spec(ws, inverter, InverterSpec, "inverter")
        with using(company):
            if inverter_strings:
                g = check_inverter_strings(mod, inv, p.design_basis, inverter_strings)
                out = {"project_id": project_id, "revision": rev, "given_strings": g.model_dump(mode="json") | {"ok": g.ok}}
            else:
                out = {"project_id": project_id, "revision": rev} | size_strings(mod, inv, p.design_basis, modules_per_string).model_dump(mode="json")
        if company:
            out["company_thresholds"] = {"profile": p.company_profile, "values": {name: s.value for name, s in company.items()}}
        return out

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
        bess_brief_kw: Annotated[float | None, Field(gt=0, description="Storage power in kW the brief asks for, when bess_kw is a whole-unit size away from it (default bess_kw)")] = None,
        bess_brief_kwh: Annotated[float | None, Field(gt=0, description="Storage energy in kWh the brief asks for, when bess_kwh is a whole-unit size away from it (default bess_kwh)")] = None,
        pcs: Annotated[str | None, Field(description="Catalog PCS id")] = None,
        battery: Annotated[str | None, Field(description="Catalog battery id")] = None,
        use_layout: Annotated[bool, Field(description="Build the PV plan from the project's array layout (layout_place) and link every layout string to one pv_string instance")] = True,
        lengths: Annotated[dict[str, float] | None, Field(description="Cable route length overrides in m by role, e.g. {\"vcb_bus\": 650}")] = None,
        bess_x: Annotated[float | None, Field(description="Local x (m) of the BESS area centre (bess_ac, hybrid); default the free area nearest the switchyard")] = None,
        bess_y: Annotated[float | None, Field(description="Local y (m) of the BESS area centre")] = None,
        inverter_strings: InverterStringsArg = None,
        relay: Annotated[RelayRating | None, Field(description="utility_lt: add a bus PT and a numerical relay with these ANSI functions and protocol on the ACCB outgoing, e.g. {\"functions\": [\"50\", \"51\", \"67\", \"67N\", \"27\", \"59\", \"50N\", \"51N\"], \"protocol\": \"IEC 61850\"}")] = None,
        data_logger: Annotated[bool, Field(description="utility_lt: add a data logger on an RS-485 daisy chain through the inverters and the meter")] = False,
        keep_hand_edits: Annotated[bool, Field(description="Re-apply the network edits made since the last network_autobuild (added, changed and removed nodes, connections and groups) onto the new network; edits that no longer fit are listed as skipped")] = False,
    ) -> dict[str, Any]:
        """Replace the project's electrical network with a complete one built from a short intent: strings sized from the catalog and design basis,
        a topology template (residential, ci_rooftop, utility_lt, utility_string, utility_central, bess_ac, bess_dc, hybrid) with protection, metering, SPDs and
        earthing, breakers rated >= 1.25 x design current and cables sized for ampacity and voltage drop. Then runs the rules.

        Returns the new revision, a summary (instances per kind, DC kWp, AC kVA), rule counts, the remaining error and warning findings, blockers
        (what the catalog or the intent could not satisfy) and every assumption made. A re-run replaces the network: `hand_edits` lists the edits made
        since the previous network_autobuild that the new network does not carry, and keep_hand_edits=true re-applies them.
        utility_lt is the LT ground-mount plant: string inverters -> ACCB (MCCB incomers, bus, SPD, outgoing ACB, CT, meter; PT and relay with `relay`;
        data logger with data_logger=true) -> solar incomer in the client's 415 V LT panel, with no transformer and no DISCOM metering (the default for a
        utility project on an LT grid). inverter_strings (residential, ci_rooftop, utility_lt) builds strings given per inverter and MPPT, e.g. 18- and
        19-module strings on one inverter and different module counts per inverter; check them first with design_strings(inverter_strings=...). Example:
        network_autobuild(project_id="p1", template="ci_rooftop", ac_kw=500, module="adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp", inverter="huawei-sun2000-100ktl-m2")."""
        intent = Intent(
            template=template, ac_kw=ac_kw, dc_kwp=dc_kwp, module=module, inverter=inverter, grid_voltage_v=grid_voltage_v,
            modules_per_string=modules_per_string, strings_per_inverter=strings_per_inverter, inverters_per_block=inverters_per_block,
            step_up_kv=step_up_kv, connection=connection, metering=metering, block_switch=block_switch, auxiliary=auxiliary,
            export_limit_kw=export_limit_kw, export_control=export_control, bess_kw=bess_kw, bess_kwh=bess_kwh, bess_brief_kw=bess_brief_kw, bess_brief_kwh=bess_brief_kwh, pcs=pcs, battery=battery, lengths=lengths or {}, bess_x=bess_x, bess_y=bess_y,
            inverter_strings=tuple(inverter_strings) if inverter_strings else None, relay=relay, data_logger=data_logger,
        )  # fmt: skip

        def change(p: Project) -> Edit:
            layout = p.array_layout if use_layout and p.array_layout is not None and p.array_layout.blocks else None
            company = thresholds_of(p.company_profile, ws.config.workspace)
            r = autobuild(p, ws.catalog, intent, layout=layout, thresholds=company)
            project, counts_, found = r.project, r.counts, r.findings
            problems = [{"severity": "warning", "subject": "autobuild", "message": b} for b in r.blockers]
            edits = None
            revs = ws.store.revisions(project_id)
            built_rev = ws.store.marker(project_id, AUTOBUILD_MARK)
            pair = None
            if built_rev in revs:
                built = ws.store.load(project_id, built_rev)
                if network_edits(built, p):
                    pair = (built_rev, built, p)
                elif keep_hand_edits and (gone := ws.store.marker(project_id, DISCARDED_MARK)) and all(r in revs for r in gone):
                    pair = (gone[0], ws.store.load(project_id, gone[0]), ws.store.load(project_id, gone[1]))
            discarded = None
            if pair is not None:
                built_rev, built, edited = pair
                changed = network_edits(built, edited)
                if changed:
                    listed = {k: v.model_dump(mode="json", exclude_defaults=True) for k, v in changed.items()}
                    edits = {"since_revision": built_rev, "edits": listed, "kept": keep_hand_edits}
                    if keep_hand_edits:
                        project, kept, skipped = reapply_network_edits(built, edited, project, ws.rebuild)
                        edits |= {"reapplied": kept, "skipped": skipped}
                        report = run_rules(project, ws.catalog, thresholds=company)
                        counts_, found = report.counts, tuple(f for f in report.findings if f.severity is not Severity.INFO)
                        if skipped:
                            problems.append({"severity": "warning", "subject": "autobuild", "message": f"{len(skipped)} hand edit(s) could not be re-applied: " + "; ".join(skipped[:10])})
                    else:
                        discarded = [built_rev, ws.store.head(project_id)]
                        n = sum(len(v.added) + len(v.removed) + len(v.changed) for v in changed.values())
                        problems.append({"severity": "warning", "subject": "autobuild", "message": (
                            f"the new network replaces {n} hand edit(s) made since the network_autobuild of revision {built_rev} (listed in hand_edits; the previous "
                            f"revision keeps them): re-run with keep_hand_edits=true to re-apply them")})  # fmt: skip
            findings = [compact(f) for f in found]
            extra = {
                "template": r.template,
                "design": r.summary,
                "rules": counts_,
                "findings": findings[:LIST_CAP],
                "findings_truncated": max(0, len(findings) - LIST_CAP),
                "blockers": list(r.blockers),
                "assumptions": list(r.assumptions),
                "strings_design": r.strings.model_dump(mode="json") if r.strings else None,
            }
            if r.given_strings is not None:
                extra["given_strings"] = r.given_strings.model_dump(mode="json")
            if edits is not None:
                extra["hand_edits"] = edits
            if company:
                extra["company_thresholds"] = {"profile": p.company_profile, "values": {name: s.value for name, s in company.items()}}
            outcome["discarded"] = discarded
            return Edit(project, tuple(n.id for n in project.network.nodes), extra, tuple(problems))

        outcome: dict[str, Any] = {}
        with ws.lock:
            out = ws.mutate(project_id, change)
            ws.store.set_marker(project_id, AUTOBUILD_MARK, out["revision"])
            ws.store.set_marker(project_id, DISCARDED_MARK, outcome.get("discarded"))
        return out

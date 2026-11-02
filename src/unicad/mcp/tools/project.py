import datetime as dt
import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from unicad.domain.project import DesignBasis, DesignInput, EarthingBasis, FaultLevel, GridConnection, IssuedFor, IssueRevision, LpsBasis, NoctThermal, Party, Project, Site
from unicad.mcp.errors import guarded
from unicad.mcp.schema import SegmentArg
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Edit, Problem, Workspace, counts
from unicad.mcp.tools.rules import project_rules
from unicad.rules import issue_gate
from unicad.store import canonical_json, content_sha, not_issued_content

READ_ONLY = ToolAnnotations(readOnlyHint=True)
IssuedForArg = Annotated[IssuedFor, Field(description=f"Purpose of the issue: {', '.join(e.value for e in IssuedFor)}")]
GRID_FREQUENCY_HZ = 50.0
REMARKS_FIT = 55
DEFAULTS = {
    "ambient_min_c": 10.0,
    "ambient_max_c": 45.0,
    "altitude_m": 0.0,
    "vdrop_dc_max": 0.02,
    "vdrop_ac_max": 0.02,
    "dc_ac_ratio_target": 1.2,
    "power_factor": 0.99,
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")


def design_basis(grid_voltage_v: float, utility: str | None, overrides: dict[str, float | None]) -> DesignBasis:
    values = {k: (overrides.get(k) if overrides.get(k) is not None else v) for k, v in DEFAULTS.items()}
    phases = 1 if grid_voltage_v <= 250 else 3
    assumptions = [
        f"{k} = {v:g} is a placeholder, not site or standard data; replace it before issue."
        for k, v in values.items()
        if overrides.get(k) is None
    ]
    assumptions.append(f"Grid frequency {GRID_FREQUENCY_HZ:g} Hz and {phases} phase(s) taken from the grid voltage {grid_voltage_v:g} V.")
    assumptions.append("Cell temperature uses the module NOCT from the catalog.")
    return DesignBasis(
        cell_temp=NoctThermal(model="noct"),
        grid=GridConnection(voltage_v=grid_voltage_v, frequency_hz=GRID_FREQUENCY_HZ, phases=phases, utility=utility),
        assumptions=tuple(assumptions),
        **values,
    )


def project_summary(project: Project, revision: int) -> dict[str, Any]:
    return {
        "project_id": project.id,
        "revision": revision,
        "name": project.name,
        "client": project.client.name,
        "developer": project.developer.name if project.developer else None,
        "segment": project.segment,
        "site": project.site.name,
        "grid_voltage_v": project.design_basis.grid.voltage_v,
        "counts": counts(project),
        "sheets": len(project.sheets),
        "assumptions": list(project.design_basis.assumptions),
        "assumed_inputs": [a.model_dump() for a in project.assumed_inputs()],
        "issue": _issue_status(project),
    }


def _issue_status(project: Project) -> dict[str, Any] | None:
    if not project.revisions:
        return None
    last = project.revisions[-1]
    stale = not_issued_content(project)
    return {"rev": last.rev, "issued_for": last.issued_for.value if last.issued_for else None, "model_revision": last.model_revision,
            "model_is_issued_content": None if last.content_sha is None else stale is None, **({"message": stale} if stale else {})}  # fmt: skip


def _issue_fields(r: IssueRevision) -> dict[str, Any]:
    return r.model_dump(exclude={"content_sha", "model_revision"})


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool()
    @guarded
    def project_create(
        name: Annotated[str, Field(description="Project title, e.g. '500 kWp Rooftop Solar PV Plant'")],
        client: Annotated[str, Field(description="Client company name")],
        segment: SegmentArg,
        site: Annotated[str, Field(description="Site name, e.g. 'Demo Industries factory roof'")],
        grid_voltage_v: Annotated[float, Field(gt=0, description="Grid connection voltage in volts, e.g. 415 or 33000")],
        project_id: Annotated[str | None, Field(description="Lowercase id (letters, digits, '-', '_'); derived from name when omitted")] = None,
        state: Annotated[str | None, Field(description="Indian state of the site")] = None,
        address: str | None = None,
        utility: Annotated[str | None, Field(description="DISCOM or transmission utility")] = None,
        client_code: Annotated[str | None, Field(description="Short client code for drawing numbers")] = None,
        developer: Annotated[str | None, Field(description="Project developer when it is not the client, e.g. the RESCO or IPP building the plant")] = None,
        developer_code: Annotated[str | None, Field(description="Short developer code")] = None,
        ambient_min_c: Annotated[float | None, Field(description="Site minimum ambient °C")] = None,
        ambient_max_c: Annotated[float | None, Field(description="Site maximum ambient °C")] = None,
        altitude_m: Annotated[float | None, Field(ge=0, description="Site altitude in metres")] = None,
        vdrop_dc_max: Annotated[float | None, Field(gt=0, lt=1, description="Max DC voltage drop as a fraction, e.g. 0.02")] = None,
        vdrop_ac_max: Annotated[float | None, Field(gt=0, lt=1, description="Max AC voltage drop as a fraction")] = None,
        dc_ac_ratio_target: Annotated[float | None, Field(gt=0, description="Target DC/AC ratio")] = None,
        power_factor: Annotated[float | None, Field(gt=0, le=1, description="Design power factor")] = None,
    ) -> dict[str, Any]:
        """Create a project from minimal intent. Design-basis values you do not pass are filled with placeholders and listed under `assumptions`.

        Example: name="500 kWp Rooftop", client="Demo Industries", segment="ci_rooftop", site="Factory roof", grid_voltage_v=415.
        Creating the same project again with identical inputs changes nothing."""
        pid = project_id or slug(name)
        if not pid:
            raise ToolError("cannot derive a project id from the name; pass project_id")
        overrides = {
            "ambient_min_c": ambient_min_c,
            "ambient_max_c": ambient_max_c,
            "altitude_m": altitude_m,
            "vdrop_dc_max": vdrop_dc_max,
            "vdrop_ac_max": vdrop_ac_max,
            "dc_ac_ratio_target": dc_ac_ratio_target,
            "power_factor": power_factor,
        }
        project = Project(
            id=pid,
            name=name,
            client=Party(name=client, code=client_code),
            developer=Party(name=developer, code=developer_code) if developer else None,
            site=Site(name=site, address=address, state=state),
            design_basis=design_basis(grid_voltage_v, utility, overrides),
            company_profile="default",
            segment=segment,
        )
        with ws.lock:
            head = ws.store.head(pid)
            if head is not None and ws.store.read_text(pid) != canonical_json(project):
                raise ToolError(f"project {pid!r} already exists with different content; use project_get to inspect it or choose another project_id")
            revision = ws.store.save(project)
        return {
            "project_id": pid,
            "revision": revision,
            "changed": head is None,
            "summary": counts(project),
            "assumptions": list(project.design_basis.assumptions),
            "problems": [],
        }

    @server.tool()
    @guarded
    def project_parties(
        project_id: ProjectIdArg,
        name: Annotated[str | None, Field(description="New project title")] = None,
        client: Annotated[str | None, Field(description="Client company name")] = None,
        client_code: Annotated[str | None, Field(description="Short client code")] = None,
        client_address: Annotated[str | None, Field(description="Client address")] = None,
        developer: Annotated[str | None, Field(description="Project developer, when it is not the client (e.g. 'Truere Knight Pvt. Ltd.')")] = None,
        developer_code: Annotated[str | None, Field(description="Short developer code")] = None,
        developer_address: Annotated[str | None, Field(description="Developer address")] = None,
        clear_developer: Annotated[bool, Field(description="Remove the developer")] = False,
    ) -> dict[str, Any]:
        """Set the project title and the parties the title block and register name: the client (owner) and the project developer. Only what you
        pass changes; a developer field you pass on a project without a developer creates one (its name is then required).

        Example: project_parties(project_id="rvunl-heerapura", developer="Truere Knight Pvt. Ltd.")."""

        def change(p: Project) -> Edit:
            touched: list[str] = []
            upd: dict[str, Any] = {}
            if name is not None:
                upd["name"] = name
                touched.append("name")
            c = {k: v for k, v in (("name", client), ("code", client_code), ("address", client_address)) if v is not None}
            if c:
                upd["client"] = p.client.model_copy(update=c)
                touched.append("client")
            d = {k: v for k, v in (("name", developer), ("code", developer_code), ("address", developer_address)) if v is not None}
            if clear_developer and d:
                raise ValueError("developer fields are both given and cleared")
            if clear_developer:
                upd["developer"] = None
                touched.append("developer")
            elif d:
                if p.developer is None and "name" not in d:
                    raise ValueError("this project has no developer yet; pass developer (its name) with the other developer fields")
                upd["developer"] = p.developer.model_copy(update=d) if p.developer else Party(**d)
                touched.append("developer")
            updated = Project.model_validate(p.model_copy(update=upd).model_dump(mode="json"), context={"catalog": ws.catalog})
            parties = {"name": updated.name, "client": updated.client.model_dump(mode="json", exclude_none=True),
                       "developer": updated.developer.model_dump(mode="json", exclude_none=True) if updated.developer else None}  # fmt: skip
            return Edit(updated, tuple(touched), {"parties": parties})

        return ws.mutate(project_id, change)

    @server.tool(annotations=READ_ONLY)
    @guarded
    def project_get(
        project_id: ProjectIdArg,
        revision: RevisionArg = None,
        full: Annotated[bool, Field(description="Also return the complete project JSON (large)")] = False,
    ) -> dict[str, Any]:
        """Summary of a project revision: client, site, grid, counts, assumptions. Pass full=true for the whole model JSON.

        Example: project_get(project_id="rooftop-500kwp")."""
        project, rev = ws.load(project_id, revision)
        out = project_summary(project, rev) | {"head": ws.store.head(project_id)}
        if full:
            out["project"] = project.model_dump(mode="json", exclude_defaults=True)
        return out

    @server.tool(annotations=READ_ONLY)
    @guarded
    def project_list() -> dict[str, Any]:
        """List the projects in the workspace with their latest revision."""
        rows = []
        for pid in ws.store.list():
            try:
                project, rev = ws.load(pid)
                rows.append({"project_id": pid, "revision": rev, "name": project.name, "segment": project.segment, "nodes": len(project.network.nodes)})
            except Exception as e:
                rows.append({"project_id": pid, "error": str(e).splitlines()[0]})
        return {"workspace": str(ws.config.workspace), "projects": rows}

    @server.tool(annotations=READ_ONLY)
    @guarded
    def project_revisions(project_id: ProjectIdArg) -> dict[str, Any]:
        """List a project's revisions with snapshot labels and counts. Use the numbers with project_get and project_diff."""
        revs = ws.store.revisions(project_id)
        if not revs:
            raise KeyError(f"no project {project_id!r} in {ws.config.workspace}; project_list shows what exists")
        labels = ws.store.snapshots(project_id)
        rows = []
        for n in revs:
            project = ws.store.load(project_id, n)
            rows.append({"revision": n, "snapshots": sorted(k for k, v in labels.items() if v == n)} | counts(project))
        return {"project_id": project_id, "head": revs[-1], "revisions": rows}

    @server.tool()
    @guarded
    def project_snapshot(
        project_id: ProjectIdArg,
        label: Annotated[str, Field(description="Label starting with a letter, e.g. 'issued-approval-r0'")],
        revision: Annotated[int | None, Field(description="Revision number to label; omit for the latest")] = None,
    ) -> dict[str, Any]:
        """Name a revision so it can be referred to later (for example the issued set). Re-applying the same label to the same revision does nothing.

        Example: project_snapshot(project_id="rooftop-500kwp", label="issued-r0")."""
        with ws.lock:
            n = ws.store.snapshot(project_id, label, revision)
        return {"project_id": project_id, "label": label, "revision": n, "snapshots": ws.store.snapshots(project_id)}

    @server.tool(annotations=READ_ONLY)
    @guarded
    def project_diff(
        project_id: ProjectIdArg,
        from_revision: Annotated[int | str, Field(description="Older revision number or snapshot label")],
        to_revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """What changed between two revisions: added, removed and changed nodes, edges, groups, sheets (register rows), site features (outline summarised as point count and area) and layout blocks (table, module, string counts), plus changed project fields.

        Example: project_diff(project_id="rooftop-500kwp", from_revision=1)."""
        a, b = ws.resolve(project_id, from_revision), ws.resolve(project_id, to_revision)
        d = ws.store.diff(project_id, a, b)
        out: dict[str, Any] = {"project_id": project_id, "from_revision": a, "to_revision": b, "empty": d.empty}
        for part, key in (("nodes", "nodes"), ("edges", "edges"), ("groups", "groups"), ("sheets", "sheets"), ("site_features", "site"), ("layout_blocks", "layout")):
            c = getattr(d, part)
            if not c.empty:
                out[key] = c.model_dump(mode="json", exclude_defaults=True)
        if d.fields:
            out["fields"] = [f.model_dump(mode="json") for f in d.fields]
        return out

    @server.tool()
    @guarded
    def project_design_basis(
        project_id: ProjectIdArg,
        fault_levels: Annotated[list[FaultLevel] | None, Field(description="Replace the fault levels: one per system voltage, e.g. [{\"voltage_v\": 33000, \"fault_ka\": 25, \"clearing_s\": 1, \"source\": \"DISCOM letter\"}]")] = None,
        earthing: Annotated[EarthingBasis | None, Field(description="Replace the earthing inputs (soil resistivity from the Wenner test is required)")] = None,
        lps: Annotated[LpsBasis | None, Field(description="Replace the lightning protection inputs (level from the IS/IEC 62305-2 risk assessment is required)")] = None,
        clear: Annotated[list[Literal["fault_levels", "earthing", "lps"]], Field(description="Inputs to remove")] = [],  # noqa: B006
        ambient_min_c: Annotated[float | None, Field(description="Site minimum ambient °C (sets the coldest cell temperature for string Voc)")] = None,
        ambient_max_c: Annotated[float | None, Field(description="Site maximum ambient °C")] = None,
        altitude_m: Annotated[float | None, Field(ge=0, description="Site altitude in metres")] = None,
        vdrop_dc_max: Annotated[float | None, Field(gt=0, lt=1, description="Max DC voltage drop as a fraction, e.g. 0.015")] = None,
        vdrop_ac_max: Annotated[float | None, Field(gt=0, lt=1, description="Max AC voltage drop as a fraction")] = None,
        dc_ac_ratio_target: Annotated[float | None, Field(gt=0, description="Target DC/AC ratio")] = None,
        power_factor: Annotated[float | None, Field(gt=0, le=1, description="Design power factor")] = None,
        assumed: Annotated[list[DesignInput] | None, Field(description="Replace the list of design-basis inputs marked as assumed (values you chose, not the client's or a "
                                                              "cited source's), e.g. [\"ambient_max_c\", \"vdrop_ac_max\", \"lps.mast_height_m\"]; [] clears it. The drawings print them as assumed")] = None,
    ) -> dict[str, Any]:
        """Set the design-basis inputs the rules and the earthing, lightning and drawing set use: site values (ambient temperatures, altitude),
        limits (voltage drop, DC/AC target, power factor), fault levels per system voltage, earthing inputs and lightning protection inputs.
        Only the parts you pass (or clear) change, and a value you set replaces its placeholder in `assumptions`. A value you choose yourself
        (not the client's or a cited source's) is still assumed: list it in `assumed` and every drawing it drives prints it under ASSUMED INPUTS
        (layout inputs such as the tilt are marked with layout_place assumed=[...]). The design is not run:
        after changing site values, limits or fault levels on a project with a network, re-run network_autobuild so strings, breakers and
        cables are sized for them (earthing_design and lps_design store the same inputs and report the design; drawings_render draws the
        earthing and LA layouts from them). Site setbacks are set with site_set_setbacks.

        Example: project_design_basis(project_id="p1", earthing={"soil_resistivity_ohm_m": 50, "fault_current_ka": 12.5, "fault_clearing_s": 1},
        lps={"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6})."""

        def change(p: Project) -> Edit:
            data = p.model_dump(mode="json")
            basis = data["design_basis"]
            touched = []
            for key, value in (("fault_levels", fault_levels), ("earthing", earthing), ("lps", lps)):
                if key in clear and value is not None:
                    raise ValueError(f"{key} is both given and cleared")
                if key in clear:
                    basis[key] = [] if key == "fault_levels" else None
                elif value is not None:
                    basis[key] = [f.model_dump(mode="json") for f in value] if key == "fault_levels" else value.model_dump(mode="json")
                else:
                    continue
                touched.append(f"design_basis.{key}")
            values = {"ambient_min_c": ambient_min_c, "ambient_max_c": ambient_max_c, "altitude_m": altitude_m, "vdrop_dc_max": vdrop_dc_max,
                      "vdrop_ac_max": vdrop_ac_max, "dc_ac_ratio_target": dc_ac_ratio_target, "power_factor": power_factor}  # fmt: skip
            for key, value in values.items():
                if value is None:
                    continue
                basis[key] = value
                basis["assumptions"] = [a for a in basis["assumptions"] if not a.startswith(f"{key} = ")]
                touched.append(f"design_basis.{key}")
            if assumed is not None:
                basis["assumed"] = list(dict.fromkeys(assumed))
                touched.append("design_basis.assumed")
            try:
                updated = Project.model_validate(data, context={"catalog": ws.catalog})
            except ValidationError as e:
                raise ValueError("; ".join(err["msg"] for err in e.errors())) from None
            db = updated.design_basis
            extra = {"design_basis": {
                **{k: getattr(db, k) for k in DEFAULTS},
                "fault_levels": [f.model_dump(mode="json", exclude_none=True) for f in db.fault_levels],
                "earthing": db.earthing.model_dump(mode="json", exclude_none=True) if db.earthing else None,
                "lps": db.lps.model_dump(mode="json", exclude_none=True) if db.lps else None,
                "assumptions": list(db.assumptions),
                "assumed": [a.model_dump() for a in updated.assumed_inputs() if a.field.startswith("design_basis.")],
            }}  # fmt: skip
            problems = []
            sizing = [t for t in touched if t.split(".")[1] not in ("earthing", "lps")]
            if sizing and updated.network.nodes and db != p.design_basis:
                problems.append({"severity": "warning", "subject": "design_basis", "message": f"{', '.join(sizing)} changed after the network was built: re-run network_autobuild so strings, breakers and cables are sized for the new values, then validate"})
            return Edit(updated, tuple(touched), extra, tuple(problems))

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def project_issue(
        project_id: ProjectIdArg,
        issued_for: IssuedForArg,
        rev: Annotated[str, Field(min_length=1, max_length=8, description="Revision mark printed on every sheet, e.g. '0', 'A' or 'R1'")],
        drawn: Annotated[str, Field(min_length=1, description="Name (or initials) of who drew the set, printed in the DRAWN cell")],
        checked: Annotated[str | None, Field(description="Name of the checker, printed in the CHECKED cell")] = None,
        approved: Annotated[str | None, Field(description="Name of the approver, printed in the APPROVED cell")] = None,
        date: Annotated[dt.date | None, Field(description="Issue date as YYYY-MM-DD; today when omitted")] = None,
        remarks: Annotated[str | None, Field(description="Revision description in the revision table; 'ISSUED FOR <PURPOSE>' when omitted")] = None,
        amend: Annotated[bool, Field(description="Correct the latest issued revision (same rev mark: its date, purpose, names or remarks) instead of adding a new one; refused for an older revision")] = False,
    ) -> dict[str, Any]:
        """Issue the drawing set: add a revision (mark, date, purpose, drawn/checked/approved names, remarks) that every sheet's title
        block, revision table and the drawing register show from the next drawings_render. The engineering rules run first (as in
        validate): approval, construction and as_built are refused while any error remains, and the refusal lists every error;
        proposal and information are allowed with errors and the response says the sheets carry them. A revision mark already
        issued is refused unless every value is the same and the model content is what was issued (then nothing changes), or amend=true
        corrects the latest revision in place, so a fix to its remarks or names before any newer issue does not cost a new revision. Each
        issue records the model revision and a fingerprint of the content it issues; amend=true re-issues the revision with the current
        content (the reply says so and from which model revision), and validate, project_get and drawings_render say when the model no
        longer is the issued content. Remarks longer than about 55 characters are
        shortened in the title block revision cell; the reply warns. Re-render with drawings_render afterwards.

        Example: project_issue(project_id="rooftop-500kwp", issued_for="approval", rev="0", drawn="A. Kumar", checked="S. Rao", approved="P. Iyer", date="2026-10-28")."""
        purpose = IssuedFor(issued_for)
        entry = IssueRevision(rev=rev, date=date or dt.date.today(), remarks=remarks or f"ISSUED FOR {purpose.value.replace('_', ' ').upper()}",
                              issued_for=purpose, drawn=drawn, checked=checked, approved=approved)  # fmt: skip

        def change(p: Project) -> Edit:
            gate = issue_gate(project_rules(ws, p), purpose)
            if not gate.allowed:
                raise ToolError(f"cannot issue {project_id!r} for {purpose.value}: {len(gate.reasons)} error(s) remain; fix them (validate, explain_finding) or issue for information: "
                                + "; ".join(gate.reasons))  # fmt: skip
            sha, head = content_sha(p), ws.store.head(project_id)
            issued = entry.model_copy(update={"content_sha": sha, "model_revision": head})
            same = next((r for r in p.revisions if r.rev == rev), None)
            problems: tuple[Problem, ...] = ()
            note: dict[str, Any] = {}
            if amend:
                if not p.revisions or p.revisions[-1].rev != rev:
                    latest = p.revisions[-1].rev if p.revisions else None
                    raise ToolError(f"amend corrects only the latest issued revision ({latest!r}), not {rev!r}" if latest else "nothing to amend: the set has not been issued yet")
                was = p.revisions[-1]
                if was.content_sha != sha:
                    issued_from = f"model revision {was.model_revision}" if was.model_revision is not None else "content not fingerprinted"
                    note = {"content": f"Rev {rev} re-issued with the current content (model revision {head}; it was issued from {issued_from})"}
                    problems += ({"severity": "warning", "subject": "issue", "message": f"Rev {rev} now stands for model revision {head}, whose content differs from the Rev {rev} issued "
                                  f"before ({issued_from}): if sheets of the earlier Rev {rev} left the office, issue a new revision mark instead"},)  # fmt: skip
                updated = p.model_copy(update={
                    "revisions": (*p.revisions[:-1], issued),
                    "sheets": tuple(s.model_copy(update={"revisions": tuple(issued if r.rev == rev else r for r in s.revisions)}) for s in p.sheets),
                })  # fmt: skip
            elif same is not None and _issue_fields(same) != _issue_fields(entry):
                raise ToolError(f"revision {rev!r} was already issued on {same.date} for {same.issued_for.value if same.issued_for else 'an unstated purpose'}; "
                                "give a new revision mark, or pass amend=true to correct it while it is the latest revision")  # fmt: skip
            elif same is not None and same.content_sha is not None and same.content_sha != sha:
                raise ToolError(f"revision {rev!r} was issued from model revision {same.model_revision} and the model has changed since: give a new revision mark for the "
                                f"changed content, or pass amend=true to re-issue {rev!r} with it while it is the latest revision")  # fmt: skip
            else:
                sheets = p.sheets if same is not None else tuple(s.model_copy(update={"revisions": (*s.revisions, issued)}) if s.revisions else s for s in p.sheets)
                updated = p if same is not None else p.model_copy(update={"revisions": (*p.revisions, issued), "sheets": sheets})
            problems += tuple({"severity": "warning", "subject": "issue", "message": f"issued for {purpose.value} with an open error: {r}"} for r in gate.reasons)
            if len(entry.remarks) > REMARKS_FIT:
                problems += ({"severity": "warning", "subject": "issue", "message": f"remarks are {len(entry.remarks)} characters; the title block revision cell shows about {REMARKS_FIT} "
                              "(longer text is cut with an ellipsis): shorten them with project_issue(..., amend=true)"},)  # fmt: skip
            shown = next(r for r in updated.revisions if r.rev == rev)
            extra = {"issue": shown.model_dump(mode="json"), "revisions": [r.rev for r in updated.revisions],
                     "gate": {"allowed": gate.allowed, "watermark": gate.watermark, "errors": len(gate.reasons)}} | note  # fmt: skip
            return Edit(updated, ("revisions",), extra, problems)

        return ws.mutate(project_id, change)

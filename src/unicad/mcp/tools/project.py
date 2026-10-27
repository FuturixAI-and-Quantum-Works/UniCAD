import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from unicad.domain.project import DesignBasis, EarthingBasis, FaultLevel, GridConnection, LpsBasis, NoctThermal, Party, Project, Site
from unicad.mcp.errors import guarded
from unicad.mcp.schema import SegmentArg
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Edit, Workspace, counts
from unicad.store import canonical_json

READ_ONLY = ToolAnnotations(readOnlyHint=True)
GRID_FREQUENCY_HZ = 50.0
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
        "segment": project.segment,
        "site": project.site.name,
        "grid_voltage_v": project.design_basis.grid.voltage_v,
        "counts": counts(project),
        "sheets": len(project.sheets),
        "assumptions": list(project.design_basis.assumptions),
    }


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
    ) -> dict[str, Any]:
        """Set the design-basis inputs the rules and the earthing, lightning and drawing set use: site values (ambient temperatures, altitude),
        limits (voltage drop, DC/AC target, power factor), fault levels per system voltage, earthing inputs and lightning protection inputs.
        Only the parts you pass (or clear) change, and a value you set replaces its placeholder in `assumptions`. The design is not run:
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
            }}  # fmt: skip
            problems = []
            sizing = [t for t in touched if t.split(".")[1] not in ("earthing", "lps")]
            if sizing and updated.network.nodes and db != p.design_basis:
                problems.append({"severity": "warning", "subject": "design_basis", "message": f"{', '.join(sizing)} changed after the network was built: re-run network_autobuild so strings, breakers and cables are sized for the new values, then validate"})
            return Edit(updated, tuple(touched), extra, tuple(problems))

        return ws.mutate(project_id, change)

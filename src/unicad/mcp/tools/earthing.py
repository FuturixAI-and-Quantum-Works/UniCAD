from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.domain.project import EarthingBasis, LpsBasis, Project
from unicad.earthing import design_earthing, summary
from unicad.lps import coverage_points, design_lps
from unicad.mcp.errors import guarded
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Edit, Workspace

READ_ONLY = ToolAnnotations(readOnlyHint=True)
LIST_CAP = 40


def _with(ws: Workspace, p: Project, **update: Any) -> Project:
    data = p.model_dump(mode="json")
    for k, v in update.items():
        data["design_basis"][k] = v.model_dump(mode="json")
    return Project.model_validate(data, context={"catalog": ws.catalog})


def _lps_json(d) -> dict[str, Any]:
    ok, n = coverage_points(d)
    return {
        "method": d.method, "level": d.level, "radius_m": d.radius_m, "mast_height_m": d.mast_height_m,
        "terminals": [{"tag": t.tag, "x": t.at.x, "y": t.at.y, "on_roof": t.on_roof} for t in d.terminals[:LIST_CAP]],
        "terminal_count": len(d.terminals), "tables_covered": ok, "tables": n, "uncovered": [t.id for t in d.uncovered[:LIST_CAP]],
        "uncovered_count": len(d.uncovered), "steps": [s.model_dump() for s in d.steps], "assumptions": list(d.assumptions),
    }  # fmt: skip


def _brief(s: dict[str, Any]) -> dict[str, Any]:
    return {k: s[k] for k in ("conductor", "pits", "pits_by_role", "resistance_ohm", "target_ohm", "tables", "items", "unbonded", "quantities", "warnings") if k in s} | (
        {"switchyard_grid": s["switchyard_grid"]} if "switchyard_grid" in s else {}
    )


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool()
    @guarded
    def earthing_design(
        project_id: ProjectIdArg,
        soil_resistivity: Annotated[float, Field(gt=0, description="Soil resistivity in ohm m from the site's Wenner test")],
        fault_ka: Annotated[float | None, Field(gt=0, description="Earth fault current in kA the conductors and electrodes must carry")] = None,
        clearing_s: Annotated[float | None, Field(gt=0, description="Fault clearing time in s (backup protection)")] = None,
        target_ohm: Annotated[float | None, Field(gt=0, description="Target earth resistance; default from practice (1 ohm utility, 2 ohm C&I), reported as an assumption")] = None,
        electrode_length_m: Annotated[float, Field(gt=0, description="Electrode (pipe) length")] = 3.0,
        electrode_diameter_mm: Annotated[float, Field(gt=0, description="Electrode (pipe) diameter")] = 40.0,
        burial_depth_m: Annotated[float, Field(gt=0, description="Burial depth of the earth conductor")] = 0.6,
        corrosion_allowance: Annotated[float, Field(ge=0, lt=5, description="Extra cross-section for corrosion, e.g. 0.2 for 20 %")] = 0.0,
        tables_per_pit: Annotated[int, Field(gt=0, description="At least one array pit per this many tables")] = 20,
        surface_resistivity: Annotated[float | None, Field(gt=0, description="Switchyard surface layer (crushed rock) resistivity, ohm m")] = None,
        surface_layer_m: Annotated[float | None, Field(gt=0, description="Switchyard surface layer thickness")] = None,
    ) -> dict[str, Any]:
        """Design the earthing system: store the inputs in the design basis, then size the GS flat conductor (IS 3043 adiabatic, IEEE 80
        cross-check), count pits for the target resistance and the fault current, check the MV switchyard grid's touch and step voltage
        (IEEE 80) and lay out pits and conductors bonding every table row, station, panel, switchyard item and lightning terminal.

        Run layout_place first; run lps_design to include the lightning terminals' pits. Returns the design summary and quantities.
        Example: earthing_design(project_id="p1", soil_resistivity=50, fault_ka=12.5, clearing_s=1)."""

        def change(p: Project) -> Edit:
            basis = EarthingBasis(
                soil_resistivity_ohm_m=soil_resistivity, fault_current_ka=fault_ka, fault_clearing_s=clearing_s, target_resistance_ohm=target_ohm,
                electrode_length_m=electrode_length_m, electrode_diameter_mm=electrode_diameter_mm, burial_depth_m=burial_depth_m,
                corrosion_allowance=corrosion_allowance, tables_per_pit=tables_per_pit, surface_resistivity_ohm_m=surface_resistivity,
                surface_layer_m=surface_layer_m,
            )  # fmt: skip
            updated = _with(ws, p, earthing=basis)
            s = summary(design_earthing(updated))
            return Edit(updated, ("design_basis.earthing",), {"earthing": _brief(s) | {"assumptions": s["assumptions"]}})

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def lps_design(
        project_id: ProjectIdArg,
        method: Annotated[Literal["ese", "rod"], Field(description="ese (early streamer emission, NF C 17-102) or rod (conventional air terminal, rolling sphere)")],
        level: Annotated[Literal["I", "II", "III", "IV"], Field(description="Lightning protection level from the IS/IEC 62305-2 risk assessment")],
        ese_radius_m: Annotated[float | None, Field(gt=0, description="ESE protection radius from the terminal datasheet for this level and height")] = None,
        ese_delta_t_us: Annotated[float | None, Field(gt=0, le=60, description="ESE efficiency dT from the test certificate (radius computed by NF C 17-102)")] = None,
        mast_height_m: Annotated[float | None, Field(gt=0, description="Terminal height above the protected plane (ESE) or rod height")] = None,
        protected_height_m: Annotated[float | None, Field(ge=0, description="Height to protect for rods (table top or roof plant)")] = None,
    ) -> dict[str, Any]:
        """Design the lightning protection: store the inputs, then place the fewest terminals that put every table centroid and corner,
        inverter station and roof corner inside a protection zone, and prove it. ESE follows NF C 17-102, a French standard used in Indian
        EPC practice; rods use the IS/IEC 62305 rolling sphere of the level.

        Example: lps_design(project_id="p1", method="ese", level="III", ese_delta_t_us=60, mast_height_m=6)."""

        def change(p: Project) -> Edit:
            basis = LpsBasis(level=level, method=method, ese_radius_m=ese_radius_m, ese_delta_t_us=ese_delta_t_us, mast_height_m=mast_height_m, protected_height_m=protected_height_m)
            updated = _with(ws, p, lps=basis)
            d = design_lps(updated)
            return Edit(updated, ("design_basis.lps",), {"lps": _lps_json(d)})

        return ws.mutate(project_id, change)

    @server.tool(annotations=READ_ONLY)
    @guarded
    def earthing_summary(project_id: ProjectIdArg, revision: RevisionArg = None) -> dict[str, Any]:
        """The stored earthing and lightning design recomputed from the project: conductor sizes, pits by type, earth resistance against the
        target, switchyard touch/step check, bonding, terminals and coverage, quantities, calculation steps with sources, and assumptions.

        Example: earthing_summary(project_id="p1")."""
        p, rev = ws.load(project_id, revision)
        out: dict[str, Any] = {"project_id": project_id, "revision": rev}
        if p.design_basis.earthing is None and p.design_basis.lps is None:
            raise ValueError("no earthing or lightning design yet; run earthing_design and lps_design first")
        if p.design_basis.lps is not None:
            out["lps"] = _lps_json(design_lps(p))
        if p.design_basis.earthing is not None:
            out["earthing"] = summary(design_earthing(p))
        return out

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.domain.project import EarthingBasis, LpsBasis, Project
from unicad.earthing import design_earthing, summary
from unicad.lps import coverage_points, design_lps
from unicad.mcp.errors import guarded
from unicad.mcp.schema import LpsLevelArg, LpsMethodArg
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
        "terminals": [{"tag": t.tag, "x": t.at.x, "y": t.at.y, "on_roof": t.on_roof, "shade_m2": t.shade_m2} for t in d.terminals[:LIST_CAP]],
        "terminal_count": len(d.terminals), "tables_covered": ok, "tables": n, "coverage": [{"class": c.label, "covered": c.covered, "total": c.total} for c in d.coverage()],
        "uncovered": [t.id for t in d.uncovered[:LIST_CAP]],
        "uncovered_count": len(d.uncovered), "steps": [s.model_dump() for s in d.steps], "assumptions": list(d.assumptions),
    }  # fmt: skip


def _brief(s: dict[str, Any]) -> dict[str, Any]:
    return {k: s[k] for k in ("conductor", "array_fault_current_a", "conductor_mass_t", "pits", "pits_by_role", "pits_by_practice", "pits_needed_by_resistance", "pits_needed_by_current", "pits_governing_criterion", "resistance_ohm", "resistance_method", "target_ohm", "tables", "items", "unbonded", "quantities", "warnings") if k in s} | (
        {"switchyard_grid": s["switchyard_grid"]} if "switchyard_grid" in s else {}
    )


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool()
    @guarded
    def earthing_design(
        project_id: ProjectIdArg,
        soil_resistivity: Annotated[float | None, Field(gt=0, description="Soil resistivity in ohm m from the site's Wenner test; required unless already in the design basis")] = None,
        fault_ka: Annotated[float | None, Field(gt=0, description="Earth fault current in kA the conductors and electrodes must carry")] = None,
        clearing_s: Annotated[float | None, Field(gt=0, description="Fault clearing time in s (backup protection)")] = None,
        target_ohm: Annotated[float | None, Field(gt=0, description="Target earth resistance; default from practice (1 ohm utility, 2 ohm C&I), reported as an assumption")] = None,
        electrode_length_m: Annotated[float | None, Field(gt=0, description="Electrode (pipe) length; default 3 m")] = None,
        electrode_diameter_mm: Annotated[float | None, Field(gt=0, description="Electrode (pipe) diameter; default 40 mm")] = None,
        burial_depth_m: Annotated[float | None, Field(gt=0, description="Burial depth of the earth conductor; default 0.6 m")] = None,
        corrosion_allowance: Annotated[float | None, Field(ge=0, lt=5, description="Extra cross-section for corrosion, e.g. 0.2 for 20 %; default 0")] = None,
        tables_per_pit: Annotated[int | None, Field(gt=0, description="Ground-mount arrays: at least one array pit per this many tables; default 20")] = None,
        surface_resistivity: Annotated[float | None, Field(gt=0, description="Switchyard surface layer (crushed rock) resistivity, ohm m")] = None,
        surface_layer_m: Annotated[float | None, Field(gt=0, description="Switchyard surface layer thickness in metres")] = None,
        switchyard_m: Annotated[tuple[float, float] | None, Field(description="MV switchyard earth grid length x width in m, from the substation layout; "
                                "needed for the IEEE 80 touch and step check unless the site marks a switchyard area")] = None,  # fmt: skip
    ) -> dict[str, Any]:
        """Design the earthing system: store the inputs in the design basis, then size the GS flat conductor (IS 3043 adiabatic, IEEE 80
        cross-check), count pits for the target resistance and the fault current, check the MV switchyard grid's touch and step voltage
        (IEEE 80, grid current split between the switchyard grid and the rest of the plant earth) and lay out pits and conductors bonding
        every table row, station, panel, switchyard item and lightning terminal.

        Inputs you leave out keep the values already in the design basis (set with project_design_basis or an earlier call); the result lists
        every input with where it came from (inputs, inputs_kept); use project_design_basis to clear one. Run layout_place first; run lps_design to include the lightning terminals' pits. Returns the
        design summary and quantities.
        Example: earthing_design(project_id="p1", soil_resistivity=50, fault_ka=12.5, clearing_s=1, switchyard_m=[60, 45])."""

        def change(p: Project) -> Edit:
            given = {
                "soil_resistivity_ohm_m": soil_resistivity, "fault_current_ka": fault_ka, "fault_clearing_s": clearing_s, "target_resistance_ohm": target_ohm,
                "electrode_length_m": electrode_length_m, "electrode_diameter_mm": electrode_diameter_mm, "burial_depth_m": burial_depth_m,
                "corrosion_allowance": corrosion_allowance, "tables_per_pit": tables_per_pit, "surface_resistivity_ohm_m": surface_resistivity,
                "surface_layer_m": surface_layer_m, "switchyard_m": switchyard_m,
            }  # fmt: skip
            stored = p.design_basis.earthing.model_dump(exclude_none=True) if p.design_basis.earthing is not None else {}
            data = stored | {k: v for k, v in given.items() if v is not None}
            if "soil_resistivity_ohm_m" not in data:
                raise ValueError("soil_resistivity is required: the design basis has no soil resistivity yet")
            updated = _with(ws, p, earthing=EarthingBasis(**data))
            lps = design_lps(updated) if updated.design_basis.lps is not None else None
            s = summary(design_earthing(updated, lps=lps, catalog=ws.catalog))
            extra: dict[str, Any] = {"earthing": _brief(s) | {"assumptions": s["assumptions"]}}
            extra["inputs"] = {k: {"value": v, "from": "this call" if given.get(k) is not None else "kept from the design basis (an earlier call or project_design_basis)"}
                               for k, v in data.items() if k in given}
            kept = sorted(k for k in data if k in given and given[k] is None)
            if kept:
                extra["inputs_kept"] = (f"{', '.join(kept)} kept from earlier calls (inputs left out keep their stored value); clear one with "
                                        "project_design_basis(clear=['earthing']) and pass the rest again")
            problems = []
            if lps is not None:
                extra["lps_terminal_count"] = len(lps.terminals)
                old = p.design_basis.earthing.switchyard_m if p.design_basis.earthing is not None else None
                before = design_lps(p).terminals if old != updated.design_basis.earthing.switchyard_m else lps.terminals
                if len(before) != len(lps.terminals):
                    problems.append({"severity": "warning", "subject": "lps", "message": (
                        f"the lightning design now has {len(lps.terminals)} terminals (lps_design gave {len(before)}): the switchyard size changed what it covers; "
                        "earthing_summary and the LPS sheet show the new design")})  # fmt: skip
                elif [(t.tag, t.at) for t in before] != [(t.tag, t.at) for t in lps.terminals]:
                    moved = [t.tag for t, u in zip(before, lps.terminals, strict=True) if (t.tag, t.at) != (u.tag, u.at)]
                    problems.append({"severity": "warning", "subject": "lps", "message": (
                        f"the lightning terminals {', '.join(moved)} moved (still {len(lps.terminals)} terminals): the switchyard size changed where they may stand; "
                        "earthing_summary and the LPS sheet show the new positions")})  # fmt: skip
            return Edit(updated, ("design_basis.earthing",), extra, tuple(problems))

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def lps_design(
        project_id: ProjectIdArg,
        method: LpsMethodArg,
        level: LpsLevelArg,
        ese_radius_m: Annotated[float | None, Field(gt=0, description="ESE protection radius in metres from the terminal datasheet for this level and height")] = None,
        ese_delta_t_us: Annotated[float | None, Field(gt=0, le=60, description="ESE efficiency dT in microseconds (µs) from the test certificate (radius computed by NF C 17-102)")] = None,
        mast_height_m: Annotated[float | None, Field(gt=0, description="Terminal height in metres above the protected plane (ESE) or rod height")] = None,
        protected_height_m: Annotated[float | None, Field(ge=0, description="Height in metres to protect for rods (table top or roof plant)")] = None,
        placement: Annotated[Literal["auto", "array", "perimeter"], Field(description="Ground plants: 'perimeter' keeps every terminal outside the array (boundary strip, "
                                                                          "roads) so no mast shades the tables; 'array' lets them stand in row gaps (fewest masts); "
                                                                          "'auto' takes the perimeter when it covers every target, else the array")] = "auto",
    ) -> dict[str, Any]:
        """Design the lightning protection: store the inputs, then place the fewest terminals that put every table centroid and corner,
        inverter station, MV switchyard, BESS unit, string inverter and building inside a protection zone, and prove it with covered / total per
        class. The catalog has no dimensions, so stations and placed equipment are covered over an assumed footprint (stated in the assumptions). ESE follows NF C 17-102, a French standard used in Indian
        EPC practice; rods use the IS/IEC 62305 rolling sphere of the level.

        Example: lps_design(project_id="p1", method="ese", level="III", ese_delta_t_us=60, mast_height_m=6)."""

        def change(p: Project) -> Edit:
            basis = LpsBasis(level=level, method=method, ese_radius_m=ese_radius_m, ese_delta_t_us=ese_delta_t_us, mast_height_m=mast_height_m, protected_height_m=protected_height_m,
                             placement=placement)
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
            out["earthing"] = summary(design_earthing(p, catalog=ws.catalog))
        return out

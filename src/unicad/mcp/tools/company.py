import threading
from pathlib import Path
from typing import Annotated, Any

import ezdxf
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.drafting.legend import legend_drawer
from unicad.drafting.sheets import samples
from unicad.drafting.sheets.layermap import apply_layer_map
from unicad.drafting.sheets.model import CompanyProfile, PaperSize
from unicad.drafting.sheets.render import render_sheet
from unicad.drafting.sld.sheet import save_deterministic
from unicad.mcp.errors import guarded
from unicad.mcp.schema import PaperArg
from unicad.mcp.tools.params import ProjectIdArg
from unicad.mcp.workspace import Edit, Workspace
from unicad.render import RenderError, preview
from unicad.tenants.answers import parse_answers
from unicad.tenants.check import check_profile
from unicad.tenants.onboard import onboard
from unicad.tenants.profile_view import describe
from unicad.tenants.store import check_id, find_profile, list_profiles, profiles_dir, save_profile, to_json

READ_ONLY = ToolAnnotations(readOnlyHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
_preview_lock = threading.Lock()

ProfileIdArg = Annotated[str | None, Field(description="Company profile id, e.g. 'acme'; company_list shows them. Omit to use the profile of project_id, or 'default'.")]
OptionalProjectArg = Annotated[str | None, Field(description="Use this project's company profile when profile_id is omitted")]


def _merge(base: Any, patch: Any) -> Any:
    """JSON merge patch: objects merge key by key, null removes a key, anything else replaces."""
    if not isinstance(patch, dict):
        return patch
    out = dict(base) if isinstance(base, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = _merge(out.get(k), v)
    return out


def register(server: MCPServer, ws: Workspace) -> None:
    def resolve_id(profile_id: str | None, project_id: str | None) -> str:
        if profile_id:
            return check_id(profile_id)
        if project_id:
            project, _ = ws.load(project_id)
            return project.company_profile
        return "default"

    def load(profile_id: str) -> tuple[CompanyProfile, str, Path]:
        origin, path = find_profile(profile_id, ws.config.workspace)
        return CompanyProfile.load(path), origin, path

    @server.tool(annotations=READ_ONLY)
    @guarded
    def company_list() -> dict[str, Any]:
        """List the company profiles this workspace can use: its own (onboarded or edited) and the bundled ones. A workspace profile hides a bundled one of the same id.

        Example: company_list()."""
        return {
            "workspace": str(ws.config.workspace),
            "profiles": [{"profile_id": i.id, "name": i.name, "version": i.version, "origin": i.origin} for i in list_profiles(ws.config.workspace)],
        }

    @server.tool(annotations=WRITES)
    @guarded
    def company_onboard(
        dxf_path: Annotated[str, Field(description="The company's DXF: title block (a block per paper size holding {PLACEHOLDER} texts such as {DRAWING_NO}, {TITLE}, {REV}, {SHEET}, {DRAWN}), logo block, layer list and optionally its symbols")],
        answers: Annotated[
            dict[str, Any] | str,
            Field(description="The questionnaire as an object, or the path of a YAML or JSON file. Needs id, name and short_code; optional: address, confidentiality_note, date_format, default_paper, roles, numbering {pattern, disciplines, per_discipline, start}, logo_block, title_blocks, layers {frame, title, text}, layer_map, symbols {key: {block, pins}}, thresholds {name: {value, source}}, revision_marks. See docs/onboarding.md."),
        ],
        accept_partial: Annotated[bool, Field(description="Save the profile even though some pieces failed validation; those pieces are left out")] = False,
    ) -> dict[str, Any]:
        """Create a company profile from the company's own DXF and a short questionnaire, and save it in this workspace.

        Reads the title block per paper size, the logo, the layer list and the symbol blocks, checks each (placeholders present, pins matching, threshold ranges) and reports what was found, what was defaulted and what failed.
        Nothing is saved while anything failed, unless accept_partial=true. Then assign it with company_assign and look at it with company_preview_sheet.
        Example: company_onboard(dxf_path="/data/acme/company.dxf", answers="/data/acme/answers.yaml")."""
        parsed = parse_answers(answers)
        with ws.lock:
            report = onboard(Path(dxf_path), parsed, ws.config.workspace, accept_partial=accept_partial)
        out = report.as_dict()
        if report.profile is not None and report.saved:
            out["profile"] = describe(report.profile, parsed.id, "workspace", report.path)
        return out

    @server.tool(annotations=READ_ONLY)
    @guarded
    def company_get(profile_id: ProfileIdArg = None, project_id: OptionalProjectArg = None) -> dict[str, Any]:
        """Show a company profile: name, drawing numbering, title block per paper size, layers and layer map, symbol overrides, and every rule threshold with its value and where the value comes from (default or company).

        `problems` lists files that are missing or imported pieces that no longer validate.
        Example: company_get(profile_id="acme")."""
        pid = resolve_id(profile_id, project_id)
        profile, origin, path = load(pid)
        problems, facts = check_profile(profile)
        return describe(profile, pid, origin, path) | {"problems": problems, "checked": facts}

    @server.tool(annotations=WRITES)
    @guarded
    def company_update(
        fields: Annotated[dict[str, Any], Field(description="Changes as a JSON merge patch on the profile: objects merge key by key, null removes a key, anything else replaces. E.g. {'thresholds': {'dc_ac_band': {'value': 0.2, 'source': 'DS-04 rev C'}}}")],
        profile_id: ProfileIdArg = None,
        project_id: OptionalProjectArg = None,
    ) -> dict[str, Any]:
        """Change a company profile and save it in this workspace (a bundled profile is copied there first). The result must validate and must not add problems with the imported files; nothing is saved otherwise.

        Relative file paths are relative to <workspace>/profiles. Example: company_update(profile_id="acme", fields={"thresholds": {"dc_ac_band": {"value": 0.2, "source": "Acme DS-04 rev C"}}})."""
        pid = resolve_id(profile_id, project_id)
        with ws.lock:
            profile, _, _ = load(pid)
            base = profiles_dir(ws.config.workspace)
            current = to_json(profile, base.resolve())
            merged = _merge(current, fields)
            merged["version"] = 2
            updated = CompanyProfile.model_validate(merged).anchored(base)
            before, _ = check_profile(profile)
            after, _ = check_profile(updated)
            if new := [p for p in after if p not in before]:
                raise ValueError("the change breaks the profile: " + "; ".join(new))
            path = save_profile(updated, pid, ws.config.workspace)
        return describe(updated, pid, "workspace", path) | {"problems": after}

    @server.tool(annotations=WRITES)
    @guarded
    def company_assign(project_id: ProjectIdArg, profile_id: Annotated[str, Field(description="Profile id from company_list")]) -> dict[str, Any]:
        """Make a project use a company profile: its title block, drawing numbers, layers, symbols and rule thresholds apply from the next drawing render or validate.

        Example: company_assign(project_id="rooftop-500kwp", profile_id="acme")."""
        load(check_id(profile_id))
        return ws.mutate(project_id, lambda p: Edit(p.model_copy(update={"company_profile": profile_id}))) | {"profile_id": profile_id}

    @server.tool(annotations=WRITES)
    @guarded
    def company_preview_sheet(
        paper: PaperArg = PaperSize.A3,
        profile_id: ProfileIdArg = None,
        project_id: OptionalProjectArg = None,
        region: Annotated[list[float] | None, Field(min_length=4, max_length=4, description="Crop [x0, y0, x1, y1] in sheet mm from the lower-left corner; omit for the whole sheet")] = None,
        px: Annotated[int, Field(ge=200, le=6000, description="Pixels on the long side of the image")] = 1600,
    ) -> dict[str, Any]:
        """PNG of an empty sample sheet drawn with a company profile: its title block filled with sample data, drawing number, layers and company symbols in the legend. Use it to check an onboarded profile before drawing a project.

        Crop to the title block with region=[x0, y0, x1, y1] (the right-hand column) to read its text. Example: company_preview_sheet(paper="A3", profile_id="acme")."""
        pid = resolve_id(profile_id, project_id)
        profile, _, _ = load(pid)
        out = profiles_dir(ws.config.workspace) / pid / "preview"
        out.mkdir(parents=True, exist_ok=True)
        spec = samples.rooftop_500kwp(paper)
        number = profile.drawing_number(client="SUN", project="202611", discipline="electrical", seq=1)
        spec = spec.model_copy(update={"title_block": spec.title_block.model_copy(update={"drawing_number": number})})
        doc = ezdxf.new("R2018", setup=["linetypes"])
        rendered = render_sheet(doc, spec, profile, legend_drawer(doc.modelspace()))
        warnings = [*rendered.warnings, *apply_layer_map(doc, profile)]
        dxf = out / f"sheet-{paper.value}.dxf"
        save_deterministic(doc, dxf)
        tag = "full" if region is None else "_".join(f"{v:g}" for v in region)
        png = out / f"sheet-{paper.value}-{tag}-{px}.png"
        with _preview_lock:
            try:
                shot = preview(dxf, tuple(region) if region else None, px, paper=paper.value, out=png)
            except RenderError as exc:
                raise ToolError(f"preview failed: {exc}") from None
        return {
            "profile_id": pid,
            "paper": paper.value,
            "png": str(shot.path or png),
            "dxf": str(dxf),
            "region": list(shot.region),
            "width_px": shot.width_px,
            "height_px": shot.height_px,
            "px_per_mm": round(shot.px_per_mm, 3),
            "title_block": profile.title_block.kind,
            "drawing_number": number,
            "warnings": [*warnings, *shot.warnings],
        }

import json
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.mcp.errors import guarded
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Workspace
from unicad.render import RenderError, preview
from unicad.sets import BUILDERS, render_set
from unicad.sets.render import INDEX

WRITES_FILES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
Format = Literal["dxf", "pdf", "dwg"]
_render_lock = threading.Lock()


def _out_dir(ws: Workspace, project_id: str, rev: int) -> Path:
    return ws.config.workspace / project_id / "drawings" / f"rev-{rev:03d}"


def _kinds(kinds: list[str]) -> list[str]:
    unknown = [k for k in kinds if k not in BUILDERS]
    if unknown or not kinds:
        raise ValueError(f"unknown drawing kinds {unknown}; available: {sorted(BUILDERS)}")
    return list(dict.fromkeys(kinds))


def _render(ws: Workspace, project_id: str, revision, kinds: list[str], formats: list[str]) -> dict[str, Any]:
    p, rev = ws.load(project_id, revision)
    out = _out_dir(ws, project_id, rev)
    with _render_lock:
        try:
            index = render_set(p, out, kinds=_kinds(kinds), formats=tuple(dict.fromkeys(formats)), catalog=ws.catalog)
        except RenderError as exc:
            raise ToolError(f"rendering failed: {exc}; formats=['dxf'] writes the DXF without LibreCAD") from None
    return {"project_id": project_id, "revision": rev, "out_dir": str(out)} | index.as_dict(absolute=True)


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool(annotations=WRITES_FILES)
    @guarded
    def drawings_render(
        project_id: ProjectIdArg,
        kinds: Annotated[list[str], Field(description=f"Drawing kinds to include, in set order. Available: {sorted(BUILDERS)}")] = ["sld"],  # noqa: B006
        formats: Annotated[list[Format], Field(description="Files per sheet: dxf (always written), pdf (LibreCAD, plus one merged set PDF), dwg")] = ["dxf", "pdf", "dwg"],  # noqa: B006
        revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """Plan and render the project's drawing set and return where every file is. The model is not changed.

        SLD: one overall SLD with each identical block drawn once (x N), a detail sheet per distinct block type (inverters, SCBs, strings),
        and more sheets wherever a sheet would not be readable even on A0. Every conductor that leaves a sheet ends in an off-sheet marker
        naming the sheet number and the equipment on the other side. Numbers follow the company profile pattern (the project's existing SLD
        numbers are reused). Returns per sheet: number, title, paper, 'sheet x of y', drawing scale, files (absolute paths), cross-references
        and warnings; plus the merged set PDF. A missing DWG converter is a warning, not an error.
        Example: drawings_render(project_id="utility-50mwac", formats=["dxf", "pdf"])."""
        return _render(ws, project_id, revision, kinds, list(formats))

    @server.tool(annotations=WRITES_FILES)
    @guarded
    def drawings_preview(
        project_id: ProjectIdArg,
        sheet: Annotated[str, Field(description="Sheet id or drawing number from drawings_render, e.g. 'sld' or 'UDEMO-DRL-U50-EE-001'")],
        region: Annotated[
            list[float] | None,
            Field(min_length=4, max_length=4, description="Crop [x0, y0, x1, y1] in sheet mm from the lower-left corner; omit for the whole sheet"),
        ] = None,
        px: Annotated[int, Field(ge=200, le=6000, description="Pixels on the long side of the image")] = 1600,
        kinds: Annotated[list[str], Field(description="Drawing kinds of the set, when it has to be rendered first")] = ["sld"],  # noqa: B006
        revision: RevisionArg = None,
    ) -> dict[str, Any]:
        """PNG of one sheet (or a region of it) rasterised from the same PDF that goes into the issued set; renders the set first
        when needed. Returns the PNG path, the region shown in sheet mm, pixels per mm and legibility warnings (crop smaller to read
        small text). Example: drawings_preview(project_id="utility-50mwac", sheet="sld", region=[0, 0, 400, 300])."""
        rev = ws.resolve(project_id, revision)
        out = _out_dir(ws, project_id, rev)

        def find() -> dict[str, Any] | None:
            if not (out / INDEX).is_file():
                return None
            data = json.loads((out / INDEX).read_text(encoding="utf-8"))
            return next((s for s in data["sheets"] if sheet in (s["id"], s["number"])), None)

        entry = find()
        if entry is None or not entry["files"].get("pdf") or not (out / entry["files"]["pdf"]).is_file():
            _render(ws, project_id, rev, kinds, ["dxf", "pdf"])
            entry = find()
        if entry is None:
            raise KeyError(f"no sheet {sheet!r} in the drawing set of {project_id!r}; drawings_render lists the sheets")
        pdf = out / entry["files"]["pdf"]
        tag = "full" if region is None else "_".join(f"{v:g}" for v in region)
        png = out / "preview" / f"{pdf.stem}-{tag}-{px}.png"
        png.parent.mkdir(parents=True, exist_ok=True)
        try:
            shot = preview(pdf, tuple(region) if region else None, px, out=png)
        except RenderError as exc:
            raise ToolError(f"preview failed: {exc}") from None
        return {
            "project_id": project_id,
            "revision": rev,
            "sheet": entry["id"],
            "number": entry["number"],
            "png": str(shot.path or png),
            "region": list(shot.region),
            "width_px": shot.width_px,
            "height_px": shot.height_px,
            "px_per_mm": round(shot.px_per_mm, 3),
            "warnings": list(shot.warnings),
        }

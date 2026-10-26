from collections import Counter
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from shapely.ops import unary_union

from unicad.domain.project import Project
from unicad.domain.site import (
    LINE_KINDS,
    SQM_PER_ACRE,
    FeatureKind,
    LonLat,
    Setbacks,
    SiteFeature,
    SiteGeometry,
    buildable_area,
    shape_of,
    site_issues,
)
from unicad.ingest.site_common import Georef, ParsedSite, Placed, RawFeature, place
from unicad.ingest.site_coords import CoordinateSystem
from unicad.ingest.site_import import FORMATS, import_site
from unicad.mcp.errors import guarded
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Edit, Problem, Workspace

READ_ONLY = ToolAnnotations(readOnlyHint=True)
LIST_CAP = 40

KindMappingArg = Annotated[
    dict[str, str] | None,
    Field(
        description=(
            "Overrides for how shapes are classified: {placemark name, folder name or DXF layer: kind}. Keys match case-insensitively, "
            "exact first and then as a substring; the value is a feature kind or 'ignore'. Without it names are matched against a rules table "
            "(e.g. 'roof', 'pond', 'boundary', 'road', 'transmission')."
        )
    ),
]
OriginLon = Annotated[float | None, Field(ge=-180, le=180, description="Longitude of the file's local (0, 0). Only for local-metre files (DXF, coordinate lists) when the project has no site latitude/longitude yet")]
OriginLat = Annotated[float | None, Field(ge=-90, le=90, description="Latitude of the file's local (0, 0); pair with origin_lon")]


def _problem(severity: str, subject: str, message: str) -> Problem:
    return {"severity": severity, "subject": subject, "message": message}


def _acres(m2: float) -> float:
    return round(m2 / SQM_PER_ACRE, 3)


def _area(m2: float) -> dict[str, float]:
    return {"m2": round(m2, 1), "acres": _acres(m2)}


def _site_geometry(p: Project) -> SiteGeometry:
    if p.site.geometry is None:
        raise ValueError("this project has no site geometry yet; import a file with site_import or add a shape with site_set_feature")
    return p.site.geometry


def _with_geometry(p: Project, geometry: SiteGeometry, moved: bool = False) -> Project:
    """`p` with the geometry attached and the site's crs set; latitude and longitude are filled in when empty, or recomputed when `moved`."""
    site = p.site
    updates: dict[str, Any] = {"geometry": geometry, "crs": geometry.crs}
    if moved or site.latitude is None or site.longitude is None:
        regions = geometry.of_kind(FeatureKind.BOUNDARY, FeatureKind.ROOF) or geometry.features
        centre = unary_union([shape_of(f) for f in regions]).centroid
        lon, lat = geometry.local_to_lonlat(centre.x, centre.y)
        updates |= {"longitude": round(lon, 6), "latitude": round(lat, 6)}
    return p.model_copy(update={"site": site.model_copy(update=updates)})


def _georef(p: Project, lon: float | None, lat: float | None) -> Georef:
    if (lon is None) != (lat is None):
        raise ValueError("pass origin_lon and origin_lat together")
    if lon is not None and lat is not None:
        return Georef(LonLat(lon=lon, lat=lat))
    s = p.site
    if p.site.geometry is None and s.longitude is not None and s.latitude is not None:
        return Georef(LonLat(lon=s.longitude, lat=s.latitude))
    return Georef()


def _describe(f: SiteFeature) -> dict[str, Any]:
    out: dict[str, Any] = {"id": f.id, "kind": f.kind.value}
    if f.name and f.name != f.id:
        out["name"] = f.name
    out |= {"area_m2": round(f.area_m2, 1)} if f.is_area else {"length_m": round(f.length_m, 1)}
    if f.height_m is not None:
        out["height_m"] = f.height_m
    if f.width_m is not None:
        out["width_m"] = f.width_m
    return out


def _bbox(geometry: SiteGeometry) -> dict[str, float] | None:
    if not geometry.features:
        return None
    x0, y0, x1, y1 = unary_union([shape_of(f) for f in geometry.features]).bounds
    return {"x_min": round(x0, 2), "y_min": round(y0, 2), "x_max": round(x1, 2), "y_max": round(y1, 2)}


def _issue_problems(geometry: SiteGeometry) -> list[Problem]:
    return [_problem(i.severity, i.subject, i.message) for i in site_issues(geometry)]


def _frame_info(geometry: SiteGeometry) -> dict[str, Any]:
    return {
        "crs": geometry.crs,
        "origin": {"lon": geometry.origin.lon, "lat": geometry.origin.lat},
        "grid_scale_factor": round(geometry.grid_scale_factor(), 6),
    }


def _buildable_json(geometry: SiteGeometry, setbacks: Setbacks) -> dict[str, Any]:
    area = buildable_area(geometry, setbacks)
    roofs = [
        {"id": r.id, "roof_m2": round(r.area_m2, 1), "buildable_m2": round(area.roofs[r.id].area, 1)}
        for r in geometry.of_kind(FeatureKind.ROOF)
    ]
    return {
        "ground": _area(area.ground_m2),
        "roofs": {**_area(area.roof_m2), "by_roof": roofs},
        "total": _area(area.ground_m2 + area.roof_m2),
    }


def _union_area(features: tuple[SiteFeature, ...]) -> float:
    return float(unary_union([shape_of(f) for f in features]).area) if features else 0.0


def _edit(p: Project, placed: Placed, source: str, moved: bool = False) -> Edit:
    geometry = placed.geometry
    updated = _with_geometry(p, geometry, moved)
    by_id = geometry.feature_by_id
    problems = [_problem("warning", source, text) for text in placed.skipped]
    problems += [_problem("warning", source, text) for text in placed.warnings]
    problems += _issue_problems(geometry)
    imported = [_describe(by_id[i]) for i in placed.added]
    extra: dict[str, Any] = {
        "source": source,
        **_frame_info(geometry),
        "imported": imported[:LIST_CAP],
        "imported_count": len(imported),
        "imported_by_kind": dict(Counter(d["kind"] for d in imported)),
        "replaced": placed.replaced,
        "removed": placed.removed,
        "skipped": placed.skipped[:LIST_CAP],
        "buildable": _buildable_json(geometry, geometry.setbacks),
        "setbacks_m": geometry.setbacks.model_dump(),
    }
    return Edit(updated, tuple(placed.added), extra, tuple(problems))


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool()
    @guarded
    def site_import(
        project_id: ProjectIdArg,
        file_path: Annotated[str, Field(description="Path to a .kml, .kmz, .geojson, .dxf or coordinate-list (.csv/.txt) file on the machine running the server")],
        format: Annotated[str | None, Field(description=f"One of {', '.join(FORMATS)}; guessed from the extension when omitted")] = None,
        kind_mapping: KindMappingArg = None,
        origin_lon: OriginLon = None,
        origin_lat: OriginLat = None,
        source_crs: Annotated[str | None, Field(description="EPSG code when a DXF or coordinate list holds projected metres, e.g. 'EPSG:32643'; omit for local metres")] = None,
        units: Annotated[str | None, Field(description="Drawing units of a DXF without a usable $INSUNITS: m, mm, cm, ft, in, km, yd, dm")] = None,
        coordinate_system: Annotated[CoordinateSystem | None, Field(description="Coordinate lists only: lonlat, latlon or local (metres). Guessed when omitted")] = None,
        replace: Annotated[bool, Field(description="Discard the existing site geometry first and start a new local frame")] = False,
    ) -> dict[str, Any]:
        """Import site geometry (boundary, roofs, exclusions, roads, obstacles) from KML/KMZ, GeoJSON, DXF or a coordinate list into local metres.

        The local frame is the UTM zone of the site; its origin (the lon/lat of local 0,0) is stored with the geometry. Shapes are classified by name, folder or
        layer (see kind_mapping). Importing the same file again replaces what it imported before; features from other files or added by hand are kept.
        Closed polygons become areas; open paths become roads, setback lines or transmission lines.
        Example: site_import(project_id="p1", file_path="/data/site.kml", kind_mapping={"Plot": "boundary"})."""

        def change(p: Project) -> Edit:
            path = Path(file_path).expanduser()
            try:
                placed = import_site(
                    path, p.site.geometry, format=format, kind_mapping=kind_mapping, origin=_georef(p, origin_lon, origin_lat).origin,
                    source_crs=source_crs, units=units, coordinate_system=coordinate_system, replace=replace,
                )  # fmt: skip
            except OSError as e:
                raise ToolError(f"cannot read {file_path}: {e.strerror or e}") from None
            return _edit(p, placed, path.name, moved=replace)

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def site_set_feature(
        project_id: ProjectIdArg,
        id: Annotated[str, Field(description="Feature id (letters, digits, '_', '-', '.'); an existing id is replaced")],
        kind: Annotated[FeatureKind, Field(description="boundary, roof, exclusion, road, building, obstacle, water, setback_line, transmission_line or existing_structure")],
        coordinates: Annotated[list[list[float]], Field(min_length=2, description="Points as [x, y] pairs: local metres, or [lon, lat] when coordinate_system='lonlat'. Do not repeat the first point at the end")],
        coordinate_system: Annotated[CoordinateSystem, Field(description="'local' (metres in the site frame), 'lonlat' (degrees) or 'latlon'")] = "local",
        closed: Annotated[bool | None, Field(description="Polygon (true) or open path (false). Default: polygon, except road, setback_line and transmission_line which default to a path")] = None,
        holes: Annotated[list[list[list[float]]] | None, Field(description="Inner rings of a polygon, same coordinate system")] = None,
        name: str | None = None,
        height_m: Annotated[float | None, Field(ge=0, description="Height above the roof or ground, for roofs and obstacles")] = None,
        width_m: Annotated[float | None, Field(gt=0, description="Corridor width of a road, setback line or transmission line path")] = None,
        setback_m: Annotated[float | None, Field(ge=0, description="Clearance to keep around this feature (inset for boundary and roof), replacing the project default")] = None,
        attributes: dict[str, str | int | float | bool] | None = None,
        origin_lon: OriginLon = None,
        origin_lat: OriginLat = None,
    ) -> dict[str, Any]:
        """Add or replace one site feature from coordinates, e.g. a pond the survey missed or a roof obstacle. Creates the site frame if the project has none.

        Example: site_set_feature(project_id="p1", id="ahu_3", kind="obstacle", coordinates=[[10,5],[14,5],[14,8],[10,8]], height_m=1.8)."""

        def change(p: Project) -> Edit:
            pts = _points(coordinates, "coordinates")
            is_closed = closed if closed is not None else kind not in LINE_KINDS
            if coordinate_system == "latlon":
                pts = [(y, x) for x, y in pts]
            frame = "local" if coordinate_system == "local" else "lonlat"
            ring_holes = [_points(h, "holes") for h in holes or []]
            if coordinate_system == "latlon":
                ring_holes = [[(y, x) for x, y in h] for h in ring_holes]
            raw = RawFeature(
                pts, is_closed, (), name, holes=ring_holes, kind=kind, id=id, height_m=height_m, width_m=width_m, setback_m=setback_m,
                attributes=dict(attributes or {}),
            )  # fmt: skip
            parsed = ParsedSite([raw], frame)
            placed = place(parsed, p.site.geometry, georef=_georef(p, origin_lon, origin_lat))
            return _edit(p, placed, id)

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def site_remove_feature(
        project_id: ProjectIdArg,
        ids: Annotated[list[str], Field(min_length=1, description="Ids of site features to remove")],
    ) -> dict[str, Any]:
        """Remove site features by id. The site frame (CRS and origin) stays.

        Example: site_remove_feature(project_id="p1", ids=["rocky-outcrop"])."""

        def change(p: Project) -> Edit:
            geometry = _site_geometry(p)
            unknown = [i for i in ids if i not in geometry.feature_by_id]
            if unknown:
                raise ValueError(f"no site feature(s) {unknown}; features: {[f.id for f in geometry.features][:LIST_CAP]}")
            gone = set(ids)
            updated = geometry.with_features(f for f in geometry.features if f.id not in gone)
            return Edit(
                p.model_copy(update={"site": p.site.model_copy(update={"geometry": updated})}),
                (),
                {"removed": sorted(gone), "buildable": _buildable_json(updated, updated.setbacks)},
                tuple(_issue_problems(updated)),
            )

        return ws.mutate(project_id, change)

    @server.tool()
    @guarded
    def site_set_setbacks(
        project_id: ProjectIdArg,
        boundary_m: Annotated[float | None, Field(ge=0, description="Clearance inside the site boundary, e.g. for a perimeter fence road")] = None,
        roof_edge_m: Annotated[float | None, Field(ge=0, description="Clearance inside each roof edge")] = None,
        exclusion_m: Annotated[float | None, Field(ge=0, description="Clearance around exclusions, water, buildings, existing structures and road or line corridors")] = None,
        obstacle_m: Annotated[float | None, Field(ge=0, description="Clearance around obstacles such as roof units")] = None,
    ) -> dict[str, Any]:
        """Set the default clearances used for buildable area. Values you omit keep their stored value (initially 0). Take the distances from the design basis or the authority's rules; none are assumed.

        Example: site_set_setbacks(project_id="p1", boundary_m=5, roof_edge_m=1.0)."""

        def change(p: Project) -> Edit:
            geometry = _site_geometry(p)
            given = {k: v for k, v in {"boundary_m": boundary_m, "roof_edge_m": roof_edge_m, "exclusion_m": exclusion_m, "obstacle_m": obstacle_m}.items() if v is not None}
            if not given:
                raise ValueError("pass at least one of boundary_m, roof_edge_m, exclusion_m, obstacle_m")
            setbacks = geometry.setbacks.model_copy(update=given)
            updated = geometry.model_copy(update={"setbacks": setbacks})
            return Edit(
                p.model_copy(update={"site": p.site.model_copy(update={"geometry": updated})}),
                (),
                {"setbacks_m": setbacks.model_dump(), "buildable": _buildable_json(updated, setbacks)},
                tuple(_issue_problems(updated)),
            )

        return ws.mutate(project_id, change)

    @server.tool(annotations=READ_ONLY)
    @guarded
    def site_summary(
        project_id: ProjectIdArg,
        revision: RevisionArg = None,
        boundary_setback_m: Annotated[float | None, Field(ge=0, description="Trial boundary clearance; not stored")] = None,
        roof_edge_setback_m: Annotated[float | None, Field(ge=0, description="Trial roof edge clearance; not stored")] = None,
        exclusion_setback_m: Annotated[float | None, Field(ge=0, description="Trial exclusion clearance; not stored")] = None,
        obstacle_setback_m: Annotated[float | None, Field(ge=0, description="Trial obstacle clearance; not stored")] = None,
    ) -> dict[str, Any]:
        """Site geometry summary: frame, feature counts, areas in m2 and acres, buildable area after setbacks, and warnings. Setback arguments try other clearances without storing them.

        Areas are on the projected grid (grid_scale_factor is the grid/ground distance ratio at the origin).
        Example: site_summary(project_id="p1", boundary_setback_m=10)."""
        p, rev = ws.load(project_id, revision)
        geometry = _site_geometry(p)
        trial = {
            k: v
            for k, v in {"boundary_m": boundary_setback_m, "roof_edge_m": roof_edge_setback_m, "exclusion_m": exclusion_setback_m, "obstacle_m": obstacle_setback_m}.items()
            if v is not None
        }
        setbacks = geometry.setbacks.model_copy(update=trial)
        by_kind = {k: geometry.of_kind(k) for k in FeatureKind}
        counts = {k.value: len(v) for k, v in by_kind.items() if v}
        gross: dict[str, Any] = {}
        for kind in (FeatureKind.BOUNDARY, FeatureKind.ROOF, FeatureKind.EXCLUSION, FeatureKind.WATER, FeatureKind.BUILDING, FeatureKind.OBSTACLE, FeatureKind.EXISTING_STRUCTURE):
            if by_kind[kind]:
                gross[kind.value] = _area(_union_area(by_kind[kind]))
        lengths = {k.value: round(sum(f.length_m for f in by_kind[k]), 1) for k in LINE_KINDS if any(f.polyline for f in by_kind[k])}
        features = [_describe(f) for f in geometry.features]
        problems = _issue_problems(geometry)
        return {
            "project_id": project_id,
            "revision": rev,
            **_frame_info(geometry),
            "bbox_m": _bbox(geometry),
            "counts": counts,
            "areas": gross,
            "path_lengths_m": lengths,
            "setbacks_m": setbacks.model_dump(),
            "setbacks_are_trial": bool(trial),
            "buildable": _buildable_json(geometry, setbacks),
            "features": features[: LIST_CAP * 2],
            "features_truncated": max(0, len(features) - LIST_CAP * 2),
            "problems": problems,
        }


def _points(raw: list[list[float]], what: str) -> list[tuple[float, float]]:
    out = []
    for p in raw:
        if len(p) != 2:
            raise ValueError(f"{what}: each point must be [x, y], got {p}")
        out.append((float(p[0]), float(p[1])))
    return out


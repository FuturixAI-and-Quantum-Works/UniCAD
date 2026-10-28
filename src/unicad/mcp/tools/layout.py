from collections import Counter
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.arrays import FillOrder, PlacementResult, PlacementSpec, block_summaries, place_array, plan_length, table_centre
from unicad.arrays.geometry import frame
from unicad.domain.catalog import ModuleSpec
from unicad.domain.layout import ArrayLayout, Orientation, StringAssignment
from unicad.domain.network import NodeKind
from unicad.design.placement import BESS_PITCH_M, _bess_units
from unicad.domain.project import Project, Segment
from unicad.domain.site import SiteGeometry
from unicad.mcp.errors import guarded
from unicad.mcp.schema import MountingArg, PitchMethodArg, RegionModeArg
from unicad.mcp.tools.params import ProjectIdArg, RevisionArg
from unicad.mcp.workspace import Edit, Problem, Workspace
from unicad.rules.structure import BESS_SEPARATION_M

READ_ONLY = ToolAnnotations(readOnlyHint=True)
LIST_CAP = 40

Metres = Annotated[float | None, Field(gt=0)]


def _problem(severity: str, subject: str, message: str) -> Problem:
    return {"severity": severity, "subject": subject, "message": message}


def _module(ws: Workspace, module_id: str) -> ModuleSpec:
    try:
        spec = ws.catalog.get(module_id)
    except KeyError:
        raise KeyError(f"no catalog entry {module_id!r}; catalog_search(kind='module') lists modules") from None
    if not isinstance(spec, ModuleSpec):
        raise ValueError(f"{module_id!r} is a {spec.kind}, not a module")
    return spec


def _site(p: Project) -> SiteGeometry:
    if p.site.geometry is None:
        raise ValueError("this project has no site geometry; import one with site_import first")
    return p.site.geometry


def _network_strings(p: Project) -> tuple[list[str], set[int]]:
    """pv_string instance ids in network order, and the modules_in_series values they declare."""
    net = p.network
    ids: list[str] = []
    series: set[int] = set()
    for n in net.nodes:
        if n.kind is NodeKind.PV_STRING:
            ids += net.instance_ids(n.id)
            if n.modules_in_series is not None:
                series.add(n.modules_in_series)
    return ids, series


def _link(planned: tuple[StringAssignment, ...], network_ids: list[str]) -> tuple[StringAssignment, ...]:
    return tuple(a.model_copy(update={"string": sid}) for a, sid in zip(planned, network_ids))


def _blocks_json(summaries, limit: int = LIST_CAP) -> list[dict[str, Any]]:
    out = []
    for s in summaries[:limit]:
        d = s.model_dump(mode="json")
        d["station"] = None if s.station is None else {"x": s.station.x, "y": s.station.y}
        out.append(d)
    return out


def _result_json(r: PlacementResult) -> dict[str, Any]:
    return {
        "metrics": r.metrics.model_dump(),
        "pitch": r.pitch.model_dump(mode="json"),
        "full_table": r.full_table.model_dump(),
        "half_table": None if r.half_table is None else r.half_table.model_dump(),
        "target_reached": r.target_reached,
        "blocks": _blocks_json(r.blocks),
        "internal_roads": len(r.roads),
        "switchyard": None if r.layout.switchyard is None else {"x": r.layout.switchyard.at.x, "y": r.layout.switchyard.at.y, "basis": r.layout.switchyard.basis},
        "assumptions": list(r.assumptions),
    }


def _row_pitch(layout: ArrayLayout) -> float | None:
    tables = list(layout.table_by_id.values())
    if not tables:
        return None
    _, (vx, vy) = frame(tables[0].azimuth_deg)
    rows = sorted({round(t.origin.x * vx + t.origin.y * vy, 3) for t in tables})
    steps = Counter(round(b - a, 2) for a, b in zip(rows, rows[1:]) if b - a > 0.01)
    return steps.most_common(1)[0][0] if steps else None


def _layout(p: Project) -> ArrayLayout:
    if p.array_layout is None or not p.array_layout.blocks:
        raise ValueError("this project has no array layout yet; run layout_place first")
    return p.array_layout


def _full_cols(layout: ArrayLayout) -> int:
    return max(t.cols for t in layout.table_by_id.values())


BESS_AREA_ASSUMED_M = (4 * BESS_PITCH_M, 3 * BESS_PITCH_M)
BESS_SEGMENTS = frozenset({Segment.HYBRID, Segment.BESS})


def _bess_area(p: Project, given: list[float] | None) -> tuple[tuple[float, float] | None, str]:
    """The BESS area to keep free beside the switchyard and where its size comes from: given, else on a hybrid or BESS plant the grid
    network_autobuild stands the network's BESS units on, else an assumed area of four units by three rows at that grid's pitch."""
    if given is not None:
        return (given[0], given[1]), "bess_area_m"
    if p.segment not in BESS_SEGMENTS:
        return None, ""
    units = _bess_units(p.network.expand())
    if units:
        return (max(len(u) for u in units) * BESS_PITCH_M, len(units) * BESS_PITCH_M), f"the grid of the network's {len(units)} BESS block(s) at {BESS_PITCH_M:g} m pitch"
    return BESS_AREA_ASSUMED_M, (f"assumed: the network has no BESS units yet, so 4 units by 3 rows at {BESS_PITCH_M:g} m pitch; "
                                 "pass bess_area_m or re-run layout_place after network_autobuild")


def half_table_for_strings(rows: int, cols: int, mps: int) -> int | None:
    """The longest table shorter than `cols` whose rows x length modules make whole strings of `mps`."""
    return next((k for k in range(cols - 1, 0, -1) if rows * k >= mps and rows * k % mps == 0), None)


def string_problems(spec: PlacementSpec) -> list[Problem]:
    """Say when the table sizes cannot make whole strings: strings never cross tables, so the remainder modules stay unstrung."""
    mps = spec.modules_per_string or spec.table_cols
    out = []
    sizes = [("table", spec.table_cols)] + ([("half table", spec.half_table_cols)] if spec.half_tables else [])
    for what, cols in sizes:
        n = spec.table_rows * cols
        if n < mps:
            out.append(_problem("error", "layout", f"a {what} of {spec.table_rows} x {cols} = {n} modules cannot hold a string of {mps} (strings never cross tables): its modules stay unstrung; use tables of at least {mps} modules or turn half tables off"))
        elif n % mps:
            out.append(_problem("warning", "layout", f"a {what} of {spec.table_rows} x {cols} = {n} modules holds {n // mps} string(s) of {mps} and leaves {n % mps} modules unstrung; use a length that is a multiple of the string"))
    return out


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool()
    @guarded
    def layout_place(
        project_id: ProjectIdArg,
        module: Annotated[str, Field(description="Catalog id of the PV module, e.g. 'waaree-bi-55-540'")],
        mounting: MountingArg = "fixed_tilt",
        table_rows: Annotated[int, Field(gt=0, description="Modules across a table (2 for 2P, 1 for a 1P tracker)")] = 2,
        table_cols: Annotated[int | None, Field(gt=0, description="Modules along a table or tracker; default the string length (modules_per_string), else 28")] = None,
        orientation: Annotated[Orientation, Field(description="portrait or landscape")] = Orientation.PORTRAIT,
        tilt_deg: Annotated[float, Field(ge=0, le=60, description="Fixed tilt in degrees from horizontal; 0 for trackers and flat roofs")] = 0.0,
        azimuth_deg: Annotated[float, Field(ge=0, lt=360, description="Direction the modules face in degrees, clockwise from grid north; 180 = south")] = 180.0,
        axis_azimuth_deg: Annotated[float, Field(ge=0, lt=180, description="Tracker axis direction in degrees, clockwise from grid north; 0 = north-south")] = 0.0,
        pitch_method: PitchMethodArg = "solstice",
        pitch_m: Annotated[Metres, Field(description="Row-to-row pitch in metres for pitch_method='explicit'")] = None,
        gcr: Annotated[float | None, Field(gt=0, lt=1, description="Ground coverage ratio for pitch_method='gcr'")] = None,
        row_gap_m: Annotated[float | None, Field(ge=0, description="Clear gap between rows in metres for pitch_method='gap'")] = None,
        half_tables: Annotated[bool, Field(description="Fill edges with half tables (only where a half table holds whole strings, unless half_cols is given)")] = True,
        half_cols: Annotated[int | None, Field(gt=0, description="Modules along a half table; default the longest shorter table that holds whole strings")] = None,
        table_gap_m: Annotated[float | None, Field(ge=0, description="Gap between tables along a row (default 1.5 m on the ground, 0.6 m maintenance walkway on roofs; reported as an assumption)")] = None,
        module_gap_m: Annotated[float | None, Field(ge=0, description="Gap between modules on a table (default 0.02 m, reported as an assumption)")] = None,
        road_every_rows: Annotated[int | None, Field(gt=0, description="Leave an internal road after every N rows")] = None,
        road_width_m: Annotated[Metres, Field(description="Internal road width (default 6 m)")] = None,
        walkway_every_tables: Annotated[int | None, Field(gt=0, description="Leave a walkway after every N tables along a row")] = None,
        walkway_width_m: Annotated[Metres, Field(description="Walkway width (default 4 m)")] = None,
        row_walkway_every_rows: Annotated[int | None, Field(gt=0, description="Leave a maintenance walkway between rows after every N rows; on roofs the default is 2 when the rows stand closer than a walkway (reported as an assumption)")] = None,
        row_walkway_m: Annotated[Metres, Field(description="Clear width of the walkway between rows in metres (default 0.6 m)")] = None,
        target_dc_kwp: Annotated[float | None, Field(gt=0, description="Stop at this DC capacity in kWp instead of filling the site")] = None,
        fill: Annotated[FillOrder, Field(description="With a target: switchyard (default) grows the array from the switchyard (switchyard_x/y, an area marked on the site, "
                                                     "else the site's transmission line) and falls back to rows; rows fills the front rows first; nearest fills closest to fill_from")] = FillOrder.SWITCHYARD,
        fill_from_x: Annotated[float | None, Field(description="Local x (m) of the point to fill around, e.g. the pooling substation")] = None,
        fill_from_y: Annotated[float | None, Field(description="Local y (m) of the point to fill around")] = None,
        n_blocks: Annotated[int | None, Field(gt=0, description="Number of inverter blocks")] = None,
        block_dc_kwp: Annotated[float | None, Field(gt=0, description="Largest DC capacity per block in kWp; the block count is rounded up from it")] = None,
        modules_per_string: Annotated[int | None, Field(gt=0, description="Modules in series; default the network pv_string modules_in_series, else table_cols")] = None,
        strings_per_inverter: Annotated[int | None, Field(gt=0, description="Strings per inverter within a block (string inverters); default one inverter per block")] = None,
        regions: RegionModeArg = "auto",
        whole_inverters: Annotated[bool, Field(description="With strings_per_inverter: size every block to whole inverters and leave out the few remainder tables instead of adding a part-loaded inverter")] = True,
        station_x: Annotated[float | None, Field(description="Local x (m) of the inverter station for every block, e.g. a ground-level inverter room; default on the block (on a roof for rooftop arrays)")] = None,
        station_y: Annotated[float | None, Field(description="Local y (m) of the inverter station")] = None,
        switchyard_x: Annotated[float | None, Field(description="Local x (m) of the MV switchyard (main LT panel on an LT plant); cable routing and earthing read it. "
                                                    "Default: a site area of kind 'switchyard', else derived and reported")] = None,
        switchyard_y: Annotated[float | None, Field(description="Local y (m) of the MV switchyard")] = None,
        bess_area_m: Annotated[list[Annotated[float, Field(gt=0)]] | None, Field(min_length=2, max_length=2, description="BESS area length x width (m) kept free of tables "
                                "beside the switchyard, where network_autobuild then places the BESS units. Default on hybrid and BESS plants: the grid of the network's "
                                "BESS units, else an assumed area (reported)")] = None,
    ) -> dict[str, Any]:
        """Place PV tables or trackers on the project's buildable site area, split them into inverter blocks of near-equal DC capacity,
        site an inverter station per block and assign strings to module slots. Replaces any existing array layout.

        Uses the site setbacks stored with site_set_setbacks. When the network has pv_string nodes, their instances are linked to the
        planned string slots in block order; otherwise the layout is stored without string assignments and the plan is reported.
        Blocks keep to one side of every road and line corridor while each side holds whole inverters in equal blocks; otherwise a warning names the
        blocks that stand on both sides of a transmission line corridor (network_autobuild then keeps every inverter's strings on one side of the line
        except where a side's strings do not make up whole inverters, and reports those). The MV switchyard and the BESS area keep 3 m (assumed) from
        the boundary fence; set switchyard_m in project_design_basis earthing before this call so the yard is kept free of
        tables at its real size.
        Example: layout_place(project_id="p1", module="waaree-bi-55-540", tilt_deg=25, road_every_rows=12, block_dc_kwp=15000)."""

        def change(p: Project) -> Edit:
            site = _site(p)
            mod = _module(ws, module)
            network_ids, series = _network_strings(p)
            mps = modules_per_string or (next(iter(series)) if len(series) == 1 else None)
            given = {
                "pitch_m": pitch_m, "gcr": gcr, "row_gap_m": row_gap_m, "half_cols": half_cols, "table_gap_m": table_gap_m,
                "module_gap_m": module_gap_m, "road_every_rows": road_every_rows, "road_width_m": road_width_m,
                "walkway_every_tables": walkway_every_tables, "walkway_width_m": walkway_width_m, "target_dc_kwp": target_dc_kwp,
                "row_walkway_every_rows": row_walkway_every_rows, "row_walkway_m": row_walkway_m,
                "n_blocks": n_blocks, "block_dc_kwp": block_dc_kwp, "modules_per_string": mps, "strings_per_inverter": strings_per_inverter,
            }  # fmt: skip
            if (fill_from_x is None) != (fill_from_y is None):
                raise ValueError("pass fill_from_x and fill_from_y together")
            if (station_x is None) != (station_y is None):
                raise ValueError("pass station_x and station_y together")
            if (switchyard_x is None) != (switchyard_y is None):
                raise ValueError("pass switchyard_x and switchyard_y together")
            earthing = p.design_basis.earthing
            bess_area, bess_basis = _bess_area(p, bess_area_m)
            cols = table_cols or mps or 28
            notes: list[str] = []
            if table_cols is None:
                notes.append(f"table length {cols} modules: {'one string per table row (modules_per_string)' if mps else 'default, not from a cited source'}")
            halves, half = half_tables, half_cols
            if half_tables and half_cols is None and mps:
                half = half_table_for_strings(table_rows, cols, mps)
                if half is None:
                    halves = False
                    notes.append(f"half tables off: no shorter table of {table_rows} x n modules holds whole strings of {mps}")
                elif half != max(1, cols // 2):
                    notes.append(f"half tables of {table_rows} x {half} modules, the longest shorter table that holds whole strings of {mps}")
            spec = PlacementSpec(
                module=mod.id, mounting=mounting, table_rows=table_rows, table_cols=cols, orientation=orientation, tilt_deg=tilt_deg,
                azimuth_deg=azimuth_deg, axis_azimuth_deg=axis_azimuth_deg, pitch_method=pitch_method, half_tables=halves, fill=fill,
                fill_from=None if fill_from_x is None else {"x": fill_from_x, "y": fill_from_y}, regions=regions, whole_inverters=whole_inverters,
                station=None if station_x is None else {"x": station_x, "y": station_y},
                switchyard=None if switchyard_x is None else {"x": switchyard_x, "y": switchyard_y},
                switchyard_m=earthing.switchyard_m if earthing is not None else None,
                bess_area_m=bess_area, bess_gap_m=BESS_SEPARATION_M, bess_basis=bess_basis,
                **{k: v for k, v in (given | {"half_cols": half}).items() if v is not None},
            )  # fmt: skip
            result = place_array(site, spec, mod)
            problems = [_problem("warning", "layout", w) for w in result.warnings] + string_problems(spec)
            planned = result.layout.strings
            linked: tuple[StringAssignment, ...] = ()
            if network_ids:
                if series and spec.modules_per_string not in series:
                    problems.append(_problem("warning", "layout", f"network strings have {sorted(series)} modules in series but the layout uses {spec.modules_per_string}; strings not linked"))
                else:
                    linked = _link(planned, network_ids)
                    if len(network_ids) != len(planned):
                        problems.append(_problem("warning", "layout", f"network has {len(network_ids)} pv_string instances, the layout has slots for {len(planned)} strings; {min(len(network_ids), len(planned))} linked"))
            layout = result.layout.model_copy(update={"strings": linked})
            data = p.model_dump(mode="json")
            data["array_layout"] = layout.model_dump(mode="json")
            updated = Project.model_validate(data, context={"catalog": ws.catalog})
            extra = _result_json(result)
            extra["assumptions"] = notes + extra["assumptions"]
            extra |= {
                "strings_planned": len(planned),
                "strings_linked": len(linked),
                "string_id_example": planned[0].string if planned else None,
            }
            return Edit(updated, tuple(b.id for b in layout.blocks), extra, tuple(problems))

        return ws.mutate(project_id, change)

    @server.tool(annotations=READ_ONLY)
    @guarded
    def layout_summary(project_id: ProjectIdArg, revision: RevisionArg = None) -> dict[str, Any]:
        """Array layout totals: tables (full and half), modules, DC kWp, blocks and their balance, row pitch and GCR, linked strings, the
        switchyard location with how it was chosen, and the equipment network_autobuild placed in the field (string inverters, BESS units) by kind.

        Example: layout_summary(project_id="p1")."""
        p, rev = ws.load(project_id, revision)
        layout = _layout(p)
        tables = list(layout.table_by_id.values())
        mod = _module(ws, tables[0].module)
        full_cols = _full_cols(layout)
        summaries = block_summaries(layout.blocks, full_cols, mod.pmax)
        caps = [s.dc_kwp for s in summaries]
        mean = sum(caps) / len(caps)
        pitch = _row_pitch(layout)
        t0 = tables[0]
        slope = t0.rows * t0.slot_height_m
        modules = sum(t.slot_count for t in tables)
        mountings = Counter(t.mounting.value for t in tables)
        return {
            "project_id": project_id,
            "revision": rev,
            "module": mod.id,
            "module_wp": mod.pmax,
            "mounting": dict(mountings),
            "table": f"{t0.rows}{t0.orientation.value[0].upper()} x {full_cols}",
            "tables_full": sum(1 for t in tables if t.cols >= full_cols),
            "tables_half": sum(1 for t in tables if t.cols < full_cols),
            "modules": modules,
            "dc_kwp": round(modules * mod.pmax / 1000, 3),
            "blocks": len(layout.blocks),
            "block_dc_kwp": {"min": min(caps), "max": max(caps), "mean": round(mean, 3)},
            "block_balance": round((max(caps) - min(caps)) / mean, 5) if mean else 0.0,
            "row_pitch_m": pitch,
            "gcr": round(slope / pitch, 4) if pitch else None,
            "tilt_deg": t0.tilt_deg,
            "array_length_m": round(sum(plan_length(t) for t in tables), 1),
            "strings_linked": len(layout.strings),
            "switchyard": None if layout.switchyard is None else {"x": layout.switchyard.at.x, "y": layout.switchyard.at.y, "basis": layout.switchyard.basis},
            "field_equipment": dict(Counter(e.kind for e in layout.equipment)),
        }

    @server.tool(annotations=READ_ONLY)
    @guarded
    def layout_blocks(
        project_id: ProjectIdArg,
        revision: RevisionArg = None,
        block: Annotated[str | None, Field(description="One block id (e.g. 'B03') to list its tables and strings")] = None,
    ) -> dict[str, Any]:
        """Inverter blocks: tables full/half, modules, DC kWp, station location (local metres) and outline extent. With `block`,
        also that block's tables (id, centre, modules) and linked strings.

        Example: layout_blocks(project_id="p1", block="B02")."""
        p, rev = ws.load(project_id, revision)
        layout = _layout(p)
        tables = list(layout.table_by_id.values())
        mod = _module(ws, tables[0].module)
        summaries = block_summaries(layout.blocks, _full_cols(layout), mod.pmax)
        out: dict[str, Any] = {"project_id": project_id, "revision": rev, "blocks": []}
        for b, s in zip(layout.blocks, summaries):
            d = _blocks_json([s])[0]
            if b.boundary:
                xs, ys = [q.x for q in b.boundary], [q.y for q in b.boundary]
                d["extent_m"] = {"x_min": min(xs), "y_min": min(ys), "x_max": max(xs), "y_max": max(ys)}
            out["blocks"].append(d)
        if block is not None:
            by_id = {b.id: b for b in layout.blocks}
            if block not in by_id:
                raise KeyError(f"no block {block!r}; blocks: {list(by_id)}")
            b = by_id[block]
            ids = {t.id for t in b.tables}
            strings = [a for a in layout.strings if a.spans[0].table in ids]
            out["tables"] = [
                {"id": t.id, "centre": [round(c, 2) for c in table_centre(t)], "cols": t.cols, "modules": t.slot_count} for t in b.tables[: LIST_CAP * 5]
            ]
            out["tables_truncated"] = max(0, len(b.tables) - LIST_CAP * 5)
            out["strings"] = [{"string": a.string, "spans": [s.model_dump() for s in a.spans]} for a in strings[: LIST_CAP * 5]]
            out["strings_truncated"] = max(0, len(strings) - LIST_CAP * 5)
        return out

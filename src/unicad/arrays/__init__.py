"""Array placement: tables on the buildable area, inverter blocks, strings. Pure functions of the site, the spec and the module."""

from shapely.geometry import LineString

from unicad.arrays import siting
from unicad.arrays.blocks import block_count, build_blocks
from unicad.arrays.geometry import plan_depth, plan_length, table_centre, table_corners, table_polygon
from unicad.arrays.place import PlacementError, inside_problems, place_tables, regions_for
from unicad.arrays.spec import (
    BlockSummary,
    FillOrder,
    MountingKind,
    PitchMethod,
    PlacementMetrics,
    PlacementResult,
    PlacementSpec,
)
from unicad.arrays.strings import assign_strings
from unicad.domain.catalog import ModuleSpec
from unicad.domain.layout import ArrayLayout, LayoutBlock
from unicad.domain.site import FeatureKind, SiteGeometry

__all__ = [
    "BlockSummary", "FillOrder", "MountingKind", "PitchMethod", "PlacementError", "PlacementMetrics", "PlacementResult", "PlacementSpec",
    "block_summaries", "place_array", "plan_depth", "plan_length", "table_centre", "table_corners", "table_polygon",
]  # fmt: skip


def block_summaries(blocks: tuple[LayoutBlock, ...], full_cols: int, module_wp: float) -> tuple[BlockSummary, ...]:
    out = []
    for b in blocks:
        modules = sum(t.slot_count for t in b.tables)
        full = sum(1 for t in b.tables if t.cols >= full_cols)
        out.append(BlockSummary(
            id=b.id, name=b.name, tables_full=full, tables_half=len(b.tables) - full, modules=modules,
            dc_kwp=round(modules * module_wp / 1000, 3), station=b.station,
        ))  # fmt: skip
    return tuple(out)


def place_array(site: SiteGeometry, spec: PlacementSpec, module: ModuleSpec) -> PlacementResult:
    if spec.module != module.id:
        raise PlacementError(f"spec module {spec.module!r} does not match the module spec {module.id!r}")
    widened = [f.id for f in site.of_kind(FeatureKind.ROAD) if f.polyline and f.width_m is None]
    if widened:
        site = site.with_features(f.model_copy(update={"width_m": spec.road_width_m}) if f.id in widened else f for f in site.features)
    placed = place_tables(site, spec, module)
    if not placed.tables:
        raise PlacementError("no table fits the buildable area; try a shorter table, a smaller setback or half tables")
    stray = inside_problems(placed.tables, regions_for(site, spec), table_polygon)
    if stray:
        raise PlacementError(f"internal error: {len(stray)} tables leave the buildable area, e.g. {stray[:5]}")
    kwp = [t.slot_count * module.pmax / 1000 for t in placed.tables]
    total = sum(kwp)
    n = block_count(total, spec.n_blocks, spec.block_dc_kwp, len(placed.tables))
    closing = 1.0 + max(placed.pitch.pitch_m, spec.road_width_m if spec.road_every_rows else 0, spec.walkway_width_m if spec.walkway_every_tables else 0, spec.table_gap_m)
    block_notes: list[str] = []
    mps = spec.modules_per_string or spec.table_cols
    spi = spec.strings_per_inverter
    on_roofs = spec.regions == "roofs" or (spec.regions == "auto" and spec.mounting.on_roof)
    blocks = tuple(build_blocks(
        placed.tables, kwp, n, spec.table_azimuth_deg, site, placed.roads, closing, spec.block_dc_kwp, block_notes,
        flexible=spec.n_blocks is None and spec.block_dc_kwp is not None, on_roofs=on_roofs, station=spec.station,
        units=[t.slot_count // mps for t in placed.tables] if spi else None, quantum=spi, trim=spec.whole_inverters,
    ))  # fmt: skip
    if not blocks:
        raise PlacementError(f"the placed tables hold fewer strings than one inverter of {spi} strings; lower strings_per_inverter or pass whole_inverters=false")
    strings, unstrung = assign_strings(blocks, mps, spec.strings_per_inverter)
    yard = siting.switchyard(
        site, [b.station for b in blocks if b.station is not None], [t for b in blocks for t in b.tables], on_roofs, spec.switchyard, spec.switchyard_m,
        [LineString([(p.x, p.y) for p in r.points]) for r in placed.roads], spec.road_width_m,
    )  # fmt: skip
    layout = ArrayLayout(blocks=blocks, strings=tuple(strings), switchyard=yard)
    summaries = block_summaries(blocks, spec.table_cols, module.pmax)
    caps = [s.dc_kwp for s in summaries]
    mean = sum(caps) / len(caps)
    tables = [t for b in blocks for t in b.tables]
    full = sum(1 for t in tables if t.cols >= spec.table_cols)
    modules = sum(t.slot_count for t in tables)
    pitch = placed.pitch.pitch_m
    used = sum((plan_length(t) + spec.table_gap_m) * pitch for t in tables)
    metrics = PlacementMetrics(
        tables_full=full, tables_half=len(tables) - full, modules=modules, dc_kwp=round(modules * module.pmax / 1000, 3),
        module_wp=module.pmax, pitch_m=pitch, gcr=round(placed.full.slope_m / pitch, 4), buildable_m2=round(placed.buildable_m2, 1),
        area_used_m2=round(used, 1), strings=len(strings), unstrung_modules=unstrung,
        block_balance=round((max(caps) - min(caps)) / mean, 5) if mean else 0.0,
    )  # fmt: skip
    assumptions = spec.assumptions()
    if yard is not None:
        assumptions.append(f"{'MV switchyard' if not on_roofs else 'Main LT panel / switchyard'} at local ({yard.at.x:.1f}, {yard.at.y:.1f}): {yard.basis}; cable routing and earthing use this location")
    if any(b.station is not None for b in blocks) and spec.station is None and not on_roofs:
        assumptions.append(siting.SPOT_ASSUMPTION)
    if widened:
        assumptions.append(f"site road(s) {', '.join(repr(i) for i in widened)} have no width: taken as {spec.road_width_m:g} m (road_width_m) so tables and stations keep off them; set width_m on the site feature for the real width")
    if spec.modules_per_string is None:
        assumptions.append(f"modules per string: {mps} (one table row; set modules_per_string from the string sizing)")
    warnings = list(placed.warnings) + sorted(set(block_notes))
    warnings += [f"{f.kind.value} {f.id!r} has no width_m, so it carves nothing from the buildable area and tables may cover it"
                 for f in site.features if f.polyline and f.width_m is None]  # fmt: skip
    if unstrung:
        warnings.append(f"{unstrung} modules do not make up a whole string of {mps} and are left unstrung")
    return PlacementResult(
        layout=layout, metrics=metrics, pitch=placed.pitch, full_table=placed.full, half_table=placed.half, blocks=summaries,
        roads=tuple(placed.roads), target_reached=placed.target_reached, assumptions=tuple(assumptions), warnings=tuple(warnings),
    )  # fmt: skip

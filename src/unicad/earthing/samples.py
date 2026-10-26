"""Sample projects for the earthing and LPS samples and tests: the Rajasthan ground-mount fixture site with the 50 MWac utility
network, and the Pune rooftop fixture site with the 500 kWp rooftop network, each with an array laid out by place_array."""

import json
from functools import lru_cache
from pathlib import Path

from unicad.arrays import PlacementResult, PlacementSpec, place_array
from unicad.catalog.store import load_catalog
from unicad.domain.layout import Point
from unicad.domain.project import EarthingBasis, LpsBasis, Project
from unicad.domain.site import FeatureKind, Setbacks, SiteFeature
from unicad.ingest.site_import import import_site

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures"
MODULE = "waaree-bi-55-540"

GROUND_SPEC = PlacementSpec(module=MODULE, table_rows=2, table_cols=28, tilt_deg=25, road_every_rows=12, road_width_m=6, block_dc_kwp=15000)
ROOF_SPEC = PlacementSpec(
    module=MODULE, mounting="rooftop_flush", table_rows=2, table_cols=12, half_cols=6, pitch_method="gap", row_gap_m=0.6,
    table_gap_m=0.6, walkway_every_tables=4, walkway_width_m=1.2, modules_per_string=24,
)  # fmt: skip

SAMPLE_ASSUMPTIONS = (
    "Sample earthing inputs, not site data: soil resistivity, earth fault current and clearing time are illustrative.",
    "The 33 kV pooling switchyard area (60 x 45 m beside the access road) is added to the fixture site for the sample.",
    "Sample lightning inputs: ESE efficiency dT and mast height are illustrative, not from a terminal datasheet.",
)


POOLING_AREA = SiteFeature(
    id="pooling-switchyard", kind=FeatureKind.EXCLUSION, name="33 KV POOLING SWITCHYARD (SAMPLE)", attributes={"use": "switchyard"},
    polygon=(Point(x=345, y=95), Point(x=405, y=95), Point(x=405, y=140), Point(x=345, y=140)),
)  # fmt: skip


def _project(fixture: str, site_file: str, setbacks: Setbacks, spec: PlacementSpec, extra: tuple[SiteFeature, ...] = ()) -> tuple[Project, PlacementResult]:
    catalog = load_catalog()
    data = json.loads((FIXTURES / "projects" / f"{fixture}.json").read_text())
    site = import_site(FIXTURES / "sites" / site_file, None).geometry.model_copy(update={"setbacks": setbacks})
    if extra:
        site = site.with_features([*site.features, *extra])
    result = place_array(site, spec, catalog.get(spec.module))
    data["site"]["geometry"] = site.model_dump(mode="json")
    data["array_layout"] = result.layout.model_copy(update={"strings": ()}).model_dump(mode="json")
    return Project.model_validate(data, context={"catalog": catalog}), result


def with_basis(p: Project, earthing: EarthingBasis | None = None, lps: LpsBasis | None = None) -> Project:
    db = p.design_basis.model_copy(update={"earthing": earthing, "lps": lps, "assumptions": p.design_basis.assumptions + SAMPLE_ASSUMPTIONS})
    return p.model_copy(update={"design_basis": db})


@lru_cache(maxsize=1)
def rajasthan() -> tuple[Project, PlacementResult]:
    p, r = _project("utility_50mwac", "ground_mount_rajasthan.kml", Setbacks(boundary_m=10, exclusion_m=5), GROUND_SPEC, (POOLING_AREA,))
    return with_basis(
        p,
        EarthingBasis(soil_resistivity_ohm_m=50.0, fault_current_ka=12.5, fault_clearing_s=1.0, surface_resistivity_ohm_m=3000.0, surface_layer_m=0.15),
        LpsBasis(level="III", method="ese", ese_delta_t_us=60.0, mast_height_m=6.0),
    ), r  # fmt: skip


@lru_cache(maxsize=1)
def pune() -> tuple[Project, PlacementResult]:
    p, r = _project("rooftop_500kwp", "rooftop_midc_pune.kml", Setbacks(roof_edge_m=1.2, obstacle_m=0.6), ROOF_SPEC)
    return with_basis(
        p,
        EarthingBasis(soil_resistivity_ohm_m=50.0, fault_current_ka=10.0, fault_clearing_s=0.5),
        LpsBasis(level="II", method="ese", ese_delta_t_us=30.0, mast_height_m=5.0),
    ), r  # fmt: skip

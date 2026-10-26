import json
import re
import zipfile
from pathlib import Path

import ezdxf
import pytest
from pydantic import ValidationError
from pyproj import Geod, Transformer
from shapely.geometry import Polygon

from unicad.domain.layout import Point
from unicad.domain.site import (
    FeatureKind,
    LonLat,
    Setbacks,
    SiteFeature,
    SiteGeometry,
    buildable_area,
    site_issues,
    utm_crs_for,
)
from unicad.ingest.site_common import SiteImportError
from unicad.ingest.site_import import import_site

SITES = Path(__file__).resolve().parents[1] / "fixtures" / "sites"
EXPECTED = json.loads((SITES / "expected.json").read_text(encoding="utf-8"))
KUTCH_ORIGIN = LonLat(lon=69.8, lat=23.45)
TOLERANCE = 0.005


def approx(value: float, expected: float, tol: float = TOLERANCE):
    return value == pytest.approx(expected, rel=tol)


def kml_reference(path: Path) -> dict[str, tuple[float, float]]:
    """Placemark name -> (UTM grid area, WGS 84 ellipsoidal area), computed from the raw coordinates without the importer."""
    text = path.read_text(encoding="utf-8")
    out = {}
    for name, ring in re.findall(r"<Placemark><name>([^<]+)</name>.*?<Polygon>.*?<coordinates>([^<]+)</coordinates>", text):
        pts = [tuple(float(v) for v in tok.split(",")[:2]) for tok in ring.split()]
        out[name] = grid_and_geodesic_area(pts)
    return out


def grid_and_geodesic_area(lonlat: list[tuple[float, float]]) -> tuple[float, float]:
    crs = utm_crs_for(sum(p[0] for p in lonlat) / len(lonlat), sum(p[1] for p in lonlat) / len(lonlat))
    to_xy = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    grid = Polygon([to_xy.transform(lon, lat) for lon, lat in lonlat]).area
    geodesic = abs(Geod(ellps="WGS84").polygon_area_perimeter([p[0] for p in lonlat], [p[1] for p in lonlat])[0])
    return grid, geodesic


def areas_by_name(geometry: SiteGeometry) -> dict[str, float]:
    return {f.name: f.area_m2 for f in geometry.features if f.is_area}


def sq(x: float, y: float, w: float, h: float) -> tuple[Point, ...]:
    return tuple(Point(x=a, y=b) for a, b in [(x, y), (x + w, y), (x + w, y + h), (x, y + h)])


def site(*features: SiteFeature, setbacks: Setbacks | None = None) -> SiteGeometry:
    return SiteGeometry(crs="EPSG:32643", origin=LonLat(lon=73.0, lat=20.0), features=features, setbacks=setbacks or Setbacks())


def area_feature(id: str, kind: FeatureKind, x: float, y: float, w: float, h: float, **kw) -> SiteFeature:
    return SiteFeature(id=id, kind=kind, polygon=sq(x, y, w, h), **kw)


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def kml_doc(*placemarks: str) -> str:
    return f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document>{"".join(placemarks)}</Document></kml>'


def kml_polygon(name: str, ring: list[tuple[float, float]], inner: list[tuple[float, float]] | None = None, folder: str | None = None) -> str:
    def coords(pts):
        return " ".join(f"{x},{y},0" for x, y in [*pts, pts[0]])

    hole = f"<innerBoundaryIs><LinearRing><coordinates>{coords(inner)}</coordinates></LinearRing></innerBoundaryIs>" if inner else ""
    pm = f"<Placemark><name>{name}</name><Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(ring)}</coordinates></LinearRing></outerBoundaryIs>{hole}</Polygon></Placemark>"
    return f"<Folder><name>{folder}</name>{pm}</Folder>" if folder else pm


def box(lon: float, lat: float, dlon: float = 0.002, dlat: float = 0.002) -> list[tuple[float, float]]:
    return [(lon, lat), (lon + dlon, lat), (lon + dlon, lat + dlat), (lon, lat + dlat)]


# --- importers and areas ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ground_mount_rajasthan.kml", "ground_mount_rajasthan.geojson", "rooftop_midc_pune.kml", "rooftop_midc_pune.kmz"])
def test_lonlat_files_import_with_reference_areas(name):
    geometry = import_site(SITES / name, None).geometry
    reference = kml_reference(SITES / (Path(name).stem + (".kml" if name.endswith("kmz") else ".kml")))
    areas = areas_by_name(geometry)
    assert set(areas) <= set(reference) or name.endswith("geojson")
    for feature in geometry.features:
        if not feature.is_area:
            continue
        grid, geodesic = reference[feature.name]
        assert approx(feature.area_m2, grid, 0.0005), feature.id
        assert approx(feature.area_m2, geodesic), feature.id


def test_ground_mount_fixture_is_two_hundred_acres_with_expected_kinds():
    geometry = import_site(SITES / "ground_mount_rajasthan.kml", None).geometry
    kinds = {f.id: f.kind for f in geometry.features}
    assert kinds == {
        "site-boundary": FeatureKind.BOUNDARY,
        "village-pond": FeatureKind.WATER,
        "rocky-outcrop": FeatureKind.EXCLUSION,
        "132-kv-transmission-corridor": FeatureKind.TRANSMISSION_LINE,
        "access-road": FeatureKind.ROAD,
    }
    boundary = geometry.feature_by_id["site-boundary"]
    assert approx(boundary.area_m2 / 4046.8564224, 200, 0.002)
    assert approx(boundary.area_m2, EXPECTED["ground_mount_rajasthan"]["areas_m2"]["boundary_m2"], 0.0005)
    road = geometry.feature_by_id["access-road"]
    assert road.width_m == 5 and road.polyline and approx(road.length_m, 403.9, 0.01)
    assert geometry.crs == "EPSG:32643"


def test_geojson_and_kml_agree():
    kml = import_site(SITES / "ground_mount_rajasthan.kml", None).geometry
    gj = import_site(SITES / "ground_mount_rajasthan.geojson", None).geometry
    assert kml.crs == gj.crs and kml.origin == gj.origin
    for f in kml.features:
        other = gj.feature_by_id[f.id]
        assert other.kind == f.kind
        assert approx(other.area_m2 + other.length_m, f.area_m2 + f.length_m, 0.0005)


def test_rooftop_fixture_roofs_obstacles_and_heights():
    geometry = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    roofs = geometry.of_kind(FeatureKind.ROOF)
    assert {r.id for r in roofs} == {"shed-a-roof", "shed-b-roof"}
    assert all(r.height_m == 9.5 for r in roofs)
    obstacles = geometry.of_kind(FeatureKind.OBSTACLE)
    assert len(obstacles) == 8
    assert geometry.feature_by_id["chimney-stack"].height_m == 12.0
    assert geometry.feature_by_id["overhead-water-tank"].kind is FeatureKind.OBSTACLE
    assert approx(sum(o.area_m2 for o in obstacles), EXPECTED["rooftop_midc_pune"]["areas_m2"]["obstacle_m2"], 0.002)
    assert geometry.crs == "EPSG:32643"
    assert not [i for i in site_issues(geometry) if i.severity == "warning"]


def test_kmz_equals_kml():
    def stripped(name):
        return [f.model_copy(update={"attributes": {}}) for f in import_site(SITES / name, None).geometry.features]

    assert stripped("rooftop_midc_pune.kmz") == stripped("rooftop_midc_pune.kml")


def test_dxf_site_plan_imports_in_metres_with_layer_kinds():
    placed = import_site(SITES / "site_plan_kutch.dxf", None, origin=KUTCH_ORIGIN)
    g = placed.geometry
    expected = EXPECTED["site_plan_kutch"]["areas_m2"]
    by_kind = {f.kind: f for f in g.features}
    assert approx(by_kind[FeatureKind.BOUNDARY].area_m2, expected["boundary_m2"], 0.0005)
    assert approx(by_kind[FeatureKind.WATER].area_m2, expected["pond_m2"], 0.0005)
    assert approx(by_kind[FeatureKind.TRANSMISSION_LINE].area_m2, expected["corridor_m2"], 0.0005)
    assert approx(by_kind[FeatureKind.OBSTACLE].area_m2, expected["tank_m2"], 0.005)
    road = by_kind[FeatureKind.ROAD]
    assert road.polyline and not road.polygon
    assert g.crs == "EPSG:32642" and g.origin == KUTCH_ORIGIN
    first = by_kind[FeatureKind.BOUNDARY].polygon[0]
    assert (first.x, first.y) == (0.0, 0.0)
    assert not placed.skipped


def test_dxf_units_come_from_insunits_or_the_units_argument(tmp_path):
    def rewritten(insunits: int, scale: float, name: str) -> Path:
        doc = ezdxf.readfile(SITES / "site_plan_kutch.dxf")
        doc.header["$INSUNITS"] = insunits
        scaled = ezdxf.new("R2010", setup=True)
        scaled.header["$INSUNITS"] = insunits
        for layer in ("SITE_BOUNDARY", "POND"):
            scaled.layers.add(layer)
        for e in doc.modelspace().query("LWPOLYLINE"):
            if e.dxf.layer == "SITE_BOUNDARY":
                scaled.modelspace().add_lwpolyline([(x * scale, y * scale) for x, y, *_ in e.get_points()], close=True, dxfattribs={"layer": "SITE_BOUNDARY"})
        scaled.saveas(tmp_path / name)
        return tmp_path / name

    expected = EXPECTED["site_plan_kutch"]["areas_m2"]["boundary_m2"]
    mm = import_site(rewritten(4, 1000, "mm.dxf"), None, origin=KUTCH_ORIGIN).geometry
    assert approx(mm.of_kind(FeatureKind.BOUNDARY)[0].area_m2, expected, 0.0005)
    feet = import_site(rewritten(2, 1 / 0.3048, "ft.dxf"), None, origin=KUTCH_ORIGIN).geometry
    assert approx(feet.of_kind(FeatureKind.BOUNDARY)[0].area_m2, expected, 0.0005)
    unitless = import_site(rewritten(0, 1000, "u.dxf"), None, origin=KUTCH_ORIGIN, units="mm")
    assert approx(unitless.geometry.of_kind(FeatureKind.BOUNDARY)[0].area_m2, expected, 0.0005)
    guessed = import_site(rewritten(0, 1, "g.dxf"), None, origin=KUTCH_ORIGIN)
    assert any("$INSUNITS" in w for w in guessed.warnings)


def test_dxf_without_georeference_asks_for_an_origin():
    with pytest.raises(SiteImportError, match="origin_lon and origin_lat"):
        import_site(SITES / "site_plan_kutch.dxf", None)


def test_dxf_in_projected_coordinates_matches_local_import(tmp_path):
    reference = import_site(SITES / "site_plan_kutch.dxf", None, origin=KUTCH_ORIGIN).geometry
    ox, oy = Transformer.from_crs("EPSG:4326", "EPSG:32642", always_xy=True).transform(KUTCH_ORIGIN.lon, KUTCH_ORIGIN.lat)
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.layers.add("SITE_BOUNDARY")
    boundary = reference.of_kind(FeatureKind.BOUNDARY)[0]
    doc.modelspace().add_lwpolyline([(ox + p.x, oy + p.y) for p in boundary.polygon], close=True, dxfattribs={"layer": "SITE_BOUNDARY"})
    doc.saveas(tmp_path / "utm.dxf")
    placed = import_site(tmp_path / "utm.dxf", None, source_crs="EPSG:32642").geometry
    assert placed.crs == "EPSG:32642"
    assert approx(placed.of_kind(FeatureKind.BOUNDARY)[0].area_m2, boundary.area_m2, 0.0001)
    assert abs(placed.origin.lon - KUTCH_ORIGIN.lon) < 1e-4


def test_dxf_hidden_layers_are_skipped_and_hatch_islands_become_holes(tmp_path):
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.layers.add("SITE_BOUNDARY")
    doc.layers.add("EXCLUSION_OLD", dxfattribs={"flags": 1})
    msp = doc.modelspace()
    hatch = msp.add_hatch(dxfattribs={"layer": "SITE_BOUNDARY"})
    hatch.paths.add_polyline_path([(0, 0), (200, 0), (200, 100), (0, 100)], is_closed=True)
    hatch.paths.add_polyline_path([(50, 30), (90, 30), (90, 70), (50, 70)], is_closed=True)
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "EXCLUSION_OLD"})
    doc.saveas(tmp_path / "h.dxf")
    placed = import_site(tmp_path / "h.dxf", None, origin=KUTCH_ORIGIN)
    (boundary,) = placed.geometry.features
    assert len(boundary.holes) == 1
    assert boundary.area_m2 == pytest.approx(200 * 100 - 40 * 40)
    assert any("EXCLUSION_OLD" in w for w in placed.warnings)


def test_kml_inner_boundary_is_a_hole(tmp_path):
    outer = box(72.0, 24.0, 0.01, 0.01)
    inner = box(72.004, 24.004, 0.002, 0.002)
    path = write(tmp_path, "hole.kml", kml_doc(kml_polygon("Plot boundary", outer, inner)))
    g = import_site(path, None).geometry
    (b,) = g.features
    assert len(b.holes) == 1
    whole = grid_and_geodesic_area(outer)[0]
    hole = grid_and_geodesic_area(inner)[0]
    assert approx(b.area_m2, whole - hole, 0.0005)


@pytest.mark.parametrize(
    ("lon", "lat", "crs"),
    [(70.9, 23.4, "EPSG:32642"), (72.35, 27.1, "EPSG:32643"), (73.86, 18.76, "EPSG:32643"), (80.27, 13.08, "EPSG:32644"), (88.36, 22.57, "EPSG:32645"), (91.75, 26.14, "EPSG:32646"), (93.9, 24.8, "EPSG:32646")],
)
def test_crs_is_the_utm_zone_of_the_site(lon, lat, crs, tmp_path):
    assert utm_crs_for(lon, lat) == crs
    path = write(tmp_path, "s.kml", kml_doc(kml_polygon("Site boundary", box(lon - 0.001, lat - 0.001))))
    assert import_site(path, None).geometry.crs == crs


def test_kind_mapping_beats_the_rules_and_ignore_drops_a_shape(tmp_path):
    path = write(
        tmp_path, "m.kml",
        kml_doc(
            kml_polygon("Survey No. 14", box(72.0, 24.0, 0.01, 0.01), folder="Survey"),
            kml_polygon("Pond", box(72.002, 24.002), folder="Survey"),
            kml_polygon("Scratch notes", box(72.006, 24.006), folder="Survey"),
        ),
    )  # fmt: skip
    default = import_site(path, None)
    assert {f.id: f.kind for f in default.geometry.features} == {"pond": FeatureKind.WATER}
    assert any("Survey No. 14" in s and "kind_mapping" in s for s in default.skipped)
    mapped = import_site(path, None, kind_mapping={"survey no. 14": "boundary", "pond": "exclusion", "Scratch": "ignore"}).geometry
    assert {f.id: f.kind for f in mapped.features} == {"survey-no-14": FeatureKind.BOUNDARY, "pond": FeatureKind.EXCLUSION}
    with pytest.raises(SiteImportError, match="not a feature kind"):
        import_site(path, None, kind_mapping={"pond": "lake"})


def test_folder_names_classify_when_the_name_does_not(tmp_path):
    path = write(tmp_path, "f.kml", kml_doc(kml_polygon("A", box(72.0, 24.0, 0.01, 0.01), folder="Boundary"), kml_polygon("B", box(72.002, 24.002), folder="Exclusions")))
    kinds = {f.id: f.kind for f in import_site(path, None).geometry.features}
    assert kinds == {"a": FeatureKind.BOUNDARY, "b": FeatureKind.EXCLUSION}


def test_a_lone_unnamed_polygon_is_taken_as_the_boundary_with_a_warning(tmp_path):
    path = write(tmp_path, "u.kml", kml_doc(kml_polygon("Untitled Polygon", box(72.0, 24.0, 0.01, 0.01))))
    placed = import_site(path, None)
    assert placed.geometry.features[0].kind is FeatureKind.BOUNDARY
    assert any("only polygon" in w for w in placed.warnings)


def test_nothing_classifiable_is_an_error_that_names_the_shapes(tmp_path):
    path = write(tmp_path, "n.kml", kml_doc(kml_polygon("Alpha", box(72.0, 24.0)), kml_polygon("Beta", box(72.01, 24.0))))
    with pytest.raises(SiteImportError, match="Alpha.*kind_mapping"):
        import_site(path, None)


def test_self_intersecting_polygon_is_skipped_and_reported(tmp_path):
    bowtie = [(72.0, 24.0), (72.002, 24.002), (72.002, 24.0), (72.0, 24.002)]
    path = write(tmp_path, "b.kml", kml_doc(kml_polygon("Plot boundary", box(72.0, 24.0, 0.01, 0.01)), kml_polygon("Bad pond", bowtie)))
    placed = import_site(path, None)
    assert [f.id for f in placed.geometry.features] == ["plot-boundary"]
    assert any("Bad pond" in s and "Self-intersection" in s for s in placed.skipped)


def test_open_path_for_an_area_kind_is_skipped(tmp_path):
    line = "<Placemark><name>Pond edge</name><LineString><coordinates>72.0,24.0,0 72.001,24.0,0 72.001,24.001,0</coordinates></LineString></Placemark>"
    path = write(tmp_path, "o.kml", kml_doc(kml_polygon("Plot boundary", box(72.0, 24.0, 0.01, 0.01)), line))
    placed = import_site(path, None)
    assert any("open path" in s for s in placed.skipped)


def test_malformed_files_fail_with_a_clear_message(tmp_path):
    with pytest.raises(SiteImportError, match="not valid KML"):
        import_site(write(tmp_path, "bad.kml", "<kml><Document>"), None)
    with pytest.raises(SiteImportError, match="not valid GeoJSON"):
        import_site(write(tmp_path, "bad.geojson", "{nope"), None)
    with pytest.raises(SiteImportError, match="cannot tell the format"):
        import_site(write(tmp_path, "site.pdf", "x"), None)
    with pytest.raises(SiteImportError, match="no such file"):
        import_site(tmp_path / "missing.kml", None)
    with zipfile.ZipFile(tmp_path / "empty.kmz", "w") as z:
        z.writestr("readme.txt", "no kml here")
    with pytest.raises(SiteImportError, match="without a .kml"):
        import_site(tmp_path / "empty.kmz", None)


def test_geojson_declared_projected_crs_and_kind_property(tmp_path):
    to_xy = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
    ring = [to_xy.transform(lon, lat) for lon, lat in box(75.0, 26.0, 0.005, 0.005)]
    doc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32643"}},
        "features": [{"type": "Feature", "properties": {"name": "Parcel", "kind": "boundary", "setback": 4}, "geometry": {"type": "Polygon", "coordinates": [[*map(list, ring), list(ring[0])]]}}],
    }
    g = import_site(write(tmp_path, "p.geojson", json.dumps(doc)), None).geometry
    (b,) = g.features
    assert b.kind is FeatureKind.BOUNDARY and b.setback_m == 4
    assert approx(b.area_m2, Polygon(ring).area, 0.0001)


def test_coordinate_lists(tmp_path):
    ring = box(72.0, 24.0, 0.01, 0.01)
    lonlat = write(tmp_path, "a.csv", "lon,lat\n" + "\n".join(f"{x},{y}" for x, y in ring) + "\n")
    g = import_site(lonlat, None).geometry
    assert g.features[0].kind is FeatureKind.BOUNDARY
    assert approx(g.features[0].area_m2, grid_and_geodesic_area(ring)[0], 0.0005)

    swapped = write(tmp_path, "b.txt", "\n".join(f"{y} {x}" for x, y in ring) + "\n")
    placed = import_site(swapped, None)
    assert any("lat,lon" in w for w in placed.warnings)
    assert placed.geometry.features[0].area_m2 == pytest.approx(g.features[0].area_m2, rel=1e-6)

    local = write(tmp_path, "c.txt", "# boundary plot\n0 0\n100 0\n100 50\n0 50\n\n# pond village pond\n20 20\n30 20\n30 30\n20 30\n\n# road access\n0 -10\n50 0\n")
    placed = import_site(local, None, origin=LonLat(lon=73.0, lat=20.0))
    kinds = {f.id: f.kind for f in placed.geometry.features}
    assert kinds == {"plot": FeatureKind.BOUNDARY, "village-pond": FeatureKind.WATER, "access": FeatureKind.ROAD}
    assert placed.geometry.feature_by_id["plot"].area_m2 == pytest.approx(5000)
    assert placed.geometry.feature_by_id["access"].polyline
    with pytest.raises(SiteImportError, match="no georeference"):
        import_site(local, None)


# --- merging and idempotence -----------------------------------------------------------------


def test_reimporting_a_file_changes_nothing_and_other_sources_survive():
    first = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    again = import_site(SITES / "rooftop_midc_pune.kml", first)
    assert again.geometry == first and not again.replaced and not again.removed
    as_kmz = import_site(SITES / "rooftop_midc_pune.kmz", first)
    assert not as_kmz.removed and len(as_kmz.geometry.features) == len(first.features) + len(first.features)

    manual = first.with_features([*first.features, area_feature("extra", FeatureKind.OBSTACLE, 100, 30, 3, 3)])
    merged = import_site(SITES / "rooftop_midc_pune.kml", manual).geometry
    assert merged == manual


def test_importing_a_second_file_keeps_the_frame_and_adds_features(tmp_path):
    base = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    extra = write(tmp_path, "extra.kml", kml_doc(kml_polygon("Transformer yard exclusion", box(base.origin.lon + 0.0005, base.origin.lat + 0.0003, 0.0001, 0.0001))))
    merged = import_site(extra, base).geometry
    assert merged.crs == base.crs and merged.origin == base.origin
    assert {f.id for f in base.features} < {f.id for f in merged.features}
    new = merged.feature_by_id["transformer-yard-exclusion"]
    assert 30 < new.polygon[0].x < 70 and 20 < new.polygon[0].y < 40


def test_a_local_file_lands_in_the_existing_frame_by_default(tmp_path):
    base = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    local = write(tmp_path, "l.txt", "# obstacle vent stack\n150 100\n152 100\n152 102\n150 102\n")
    merged = import_site(local, base).geometry
    vent = merged.feature_by_id["vent-stack"]
    assert (vent.polygon[0].x, vent.polygon[0].y) == (150.0, 100.0)


def test_replace_starts_a_new_frame():
    base = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    placed = import_site(SITES / "ground_mount_rajasthan.kml", base, replace=True)
    assert placed.geometry.crs == "EPSG:32643" and placed.geometry.origin != base.origin
    assert "shed-a-roof" in placed.removed
    assert "shed-a-roof" not in placed.geometry.feature_by_id


def test_unnamed_shapes_get_stable_numbered_ids(tmp_path):
    path = write(tmp_path, "un.kml", kml_doc(kml_polygon("Plot boundary", box(72.0, 24.0, 0.01, 0.01)), kml_polygon("Pond", box(72.001, 24.001)), kml_polygon("Pond", box(72.004, 24.004))))
    g = import_site(path, None).geometry
    assert [f.id for f in g.features] == ["plot-boundary", "pond", "pond-2"]
    assert import_site(path, g).geometry == g


# --- model rules -----------------------------------------------------------------------------


def test_model_rejects_invalid_shapes():
    with pytest.raises(ValidationError, match="Self-intersection"):
        SiteFeature(id="x", kind=FeatureKind.EXCLUSION, polygon=tuple(Point(x=a, y=b) for a, b in [(0, 0), (10, 10), (10, 0), (0, 10)]))
    with pytest.raises(ValidationError, match="polygon is invalid"):
        SiteFeature(id="x", kind=FeatureKind.EXCLUSION, polygon=tuple(Point(x=a, y=b) for a, b in [(0, 0), (5, 0), (10, 0)]))
    with pytest.raises(ValidationError, match="needs a polygon"):
        SiteFeature(id="x", kind=FeatureKind.BOUNDARY, polyline=(Point(x=0, y=0), Point(x=1, y=1)))
    with pytest.raises(ValidationError, match="exactly one of polygon or polyline"):
        SiteFeature(id="x", kind=FeatureKind.ROAD)
    with pytest.raises(ValidationError, match="at least 3"):
        SiteFeature(id="x", kind=FeatureKind.WATER, polygon=(Point(x=0, y=0), Point(x=1, y=0)))
    with pytest.raises(ValidationError, match="duplicate site feature id"):
        site(area_feature("a", FeatureKind.ROOF, 0, 0, 5, 5), area_feature("a", FeatureKind.WATER, 10, 0, 5, 5))
    with pytest.raises(ValidationError, match="projected CRS"):
        SiteGeometry(crs="EPSG:4326", origin=LonLat(lon=73, lat=20))
    with pytest.raises(ValidationError, match="Invalid|invalid"):
        SiteFeature(id="x", kind=FeatureKind.WATER, polygon=sq(0, 0, 10, 10), holes=(sq(20, 20, 2, 2),))


def test_closing_point_is_not_stored():
    f = SiteFeature(id="x", kind=FeatureKind.WATER, polygon=(*sq(0, 0, 4, 4), Point(x=0, y=0)))
    assert len(f.polygon) == 4


def test_geometry_round_trips_through_lonlat():
    g = import_site(SITES / "rooftop_midc_pune.kml", None).geometry
    x, y = 123.456, 78.9
    lon, lat = g.local_to_lonlat(x, y)
    bx, by = g.lonlat_to_local(lon, lat)
    assert (bx, by) == pytest.approx((x, y), abs=1e-6)
    assert g.local_to_lonlat(0, 0) == pytest.approx((g.origin.lon, g.origin.lat), abs=1e-9)
    assert 0.9995 < g.grid_scale_factor() < 1.002


# --- buildable area and issues ---------------------------------------------------------------


def test_buildable_ground_area_subtracts_exclusions_corridors_and_setbacks():
    boundary = area_feature("b", FeatureKind.BOUNDARY, 0, 0, 100, 100)
    pond = area_feature("pond", FeatureKind.WATER, 40, 40, 10, 10)
    road = SiteFeature(id="rd", kind=FeatureKind.ROAD, polyline=(Point(x=0, y=80), Point(x=100, y=80)), width_m=4)
    unsized = SiteFeature(id="tl", kind=FeatureKind.TRANSMISSION_LINE, polyline=(Point(x=0, y=10), Point(x=100, y=10)))
    g = site(boundary, pond, road, unsized)
    assert buildable_area(g).ground_m2 == pytest.approx(10000 - 100 - 400)
    with_setbacks = buildable_area(g, Setbacks(boundary_m=5, exclusion_m=2))
    inner = Polygon([(5, 5), (95, 5), (95, 95), (5, 95)])
    pond_zone = Polygon([(38, 38), (52, 38), (52, 52), (38, 52)])
    road_zone = Polygon([(0, 76), (100, 76), (100, 84), (0, 84)])
    expected = inner.difference(pond_zone).difference(road_zone).area
    assert with_setbacks.ground_m2 == pytest.approx(expected)
    stored = site(boundary, pond, road, unsized, setbacks=Setbacks(boundary_m=5, exclusion_m=2))
    assert buildable_area(stored).ground_m2 == pytest.approx(expected)


def test_feature_setback_overrides_the_default():
    boundary = area_feature("b", FeatureKind.BOUNDARY, 0, 0, 100, 100, setback_m=10)
    pond = area_feature("pond", FeatureKind.WATER, 40, 40, 10, 10, setback_m=0)
    g = site(boundary, pond)
    assert buildable_area(g, Setbacks(boundary_m=1, exclusion_m=5)).ground_m2 == pytest.approx(80 * 80 - 100)


def test_buildable_roof_regions_exclude_obstacles_and_the_roof_edge():
    roof_a = area_feature("ra", FeatureKind.ROOF, 10, 10, 40, 20)
    roof_b = area_feature("rb", FeatureKind.ROOF, 10, 40, 20, 20)
    vent = area_feature("v", FeatureKind.OBSTACLE, 20, 15, 2, 2)
    plot = area_feature("plot", FeatureKind.BOUNDARY, 0, 0, 100, 100)
    g = site(plot, roof_a, roof_b, vent)
    area = buildable_area(g)
    assert area.roofs["ra"].area == pytest.approx(800 - 4)
    assert area.roofs["rb"].area == pytest.approx(400)
    assert area.ground_m2 == pytest.approx(10000 - 800 - 400 - 4 + 4)
    with_sb = buildable_area(g, Setbacks(roof_edge_m=1, obstacle_m=1))
    assert with_sb.roofs["ra"].area == pytest.approx(38 * 18 - 16)
    assert with_sb.roof_m2 == pytest.approx(38 * 18 - 16 + 18 * 18)


def test_site_issues_flag_implausible_placements():
    plot = area_feature("plot", FeatureKind.BOUNDARY, 0, 0, 100, 100)
    assert site_issues(site(plot)) == []
    outside_roof = area_feature("roof_out", FeatureKind.ROOF, 90, 10, 30, 10)
    far_pond = area_feature("pond_far", FeatureKind.WATER, 500, 500, 5, 5)
    stray = area_feature("vent_far", FeatureKind.OBSTACLE, 300, 300, 2, 2)
    overlap_a = area_feature("ra", FeatureKind.ROOF, 10, 50, 30, 30)
    overlap_b = area_feature("rb", FeatureKind.ROOF, 30, 50, 30, 30)
    issues = {(i.subject): i.message for i in site_issues(site(plot, outside_roof, far_pond, stray, overlap_a, overlap_b))}
    assert "of this roof lies outside the boundary" in issues["roof_out"]
    assert "entirely outside the boundary" in issues["pond_far"]
    assert "touches no roof or boundary" in issues["vent_far"]
    assert "overlaps roof 'rb'" in issues["ra"]
    assert [i.subject for i in site_issues(site())] == ["site"]
    assert any("buildable area is zero" in i.message for i in site_issues(site(plot, setbacks=Setbacks(boundary_m=60))))


def test_a_file_from_another_site_is_flagged():
    ground = import_site(SITES / "ground_mount_rajasthan.kml", None).geometry
    placed = import_site(SITES / "rooftop_midc_pune.kml", ground)
    assert any("km from the site origin" in w for w in placed.warnings)


def test_large_files_import_quickly(tmp_path):
    marks = [kml_polygon(f"Pond {i}", box(72.0 + 0.0005 * (i % 100), 24.0 + 0.0005 * (i // 100), 0.0002, 0.0002)) for i in range(3000)]
    path = write(tmp_path, "big.kml", kml_doc(kml_polygon("Plot boundary", box(71.99, 23.99, 0.2, 0.2)), *marks))
    placed = import_site(path, None)
    assert len(placed.geometry.features) == 3001
    assert placed.geometry.feature_by_id["pond-2999"].kind is FeatureKind.WATER

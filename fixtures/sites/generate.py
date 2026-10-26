"""Regenerates the synthetic site fixtures in this directory: `uv run python fixtures/sites/generate.py`.

Geometry is defined in local metres from an anchor lon/lat (the south-west corner), projected with the UTM zone of the anchor.
All coordinates are invented; they only resemble a Rajasthan ground-mount site and a Pune MIDC industrial shed.
"""

import json
import math
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import ezdxf
from pyproj import Transformer
from shapely.geometry import Polygon

HERE = Path(__file__).parent
ACRE = 4046.8564224

GROUND = {"name": "Synthetic 200 acre ground-mount site, Phalodi, Rajasthan", "lon": 72.3500000, "lat": 27.1000000, "epsg": 32643}
ROOFTOP = {"name": "Synthetic industrial shed, MIDC Chakan, Pune", "lon": 73.8600000, "lat": 18.7600000, "epsg": 32643}
KUTCH = {"name": "Synthetic 82 acre site plan, Kutch, Gujarat", "lon": 69.8000000, "lat": 23.4500000, "epsg": 32642}

BOUNDARY = [(0, 40), (310, 0), (640, 25), (905, 60), (930, 330), (900, 640), (610, 905), (280, 880), (35, 800), (-5, 420)]
_scale = math.sqrt(200 * ACRE / Polygon(BOUNDARY).area)
BOUNDARY = [(round(x * _scale, 2), round(y * _scale, 2)) for x, y in BOUNDARY]

POND = [(250, 520), (330, 495), (395, 530), (410, 590), (350, 640), (275, 620)]
ROCK = [(620, 150), (700, 140), (730, 200), (690, 260), (630, 230)]
CORRIDOR = [(0, 735), (940, 765), (940, 805), (0, 775)]
ROAD = [(300, -40), (305, 60), (330, 140), (420, 200), (470, 300)]

SHEDS = {
    "Shed A roof": [(20, 20), (140, 20), (140, 62), (20, 62)],
    "Shed B roof": [(20, 78), (100, 78), (100, 108), (20, 108)],
}
PLOT = [(0, 0), (190, 0), (190, 135), (0, 135)]
SKYLIGHTS = {f"Skylight strip {i}": [(30 + 22 * i, 30), (30 + 22 * i + 3, 30), (30 + 22 * i + 3, 52), (30 + 22 * i, 52)] for i in range(1, 5)}
ROOF_UNITS = {"Rooftop AHU 1": ([(110, 85), (116, 85), (116, 90), (110, 90)], 1.8), "Rooftop AHU 2": ([(120, 85), (126, 85), (126, 90), (120, 90)], 1.8)}
TANK = ([(60, 90), (65, 90), (65, 95), (60, 95)], 4.0)
CHIMNEY = ([(130, 70), (132, 70), (132, 72), (130, 72)], 12.0)

DXF_BOUNDARY = [(0, 0), (700, 0), (700, 420), (350, 520), (0, 440)]
DXF_POND = [(150, 150), (260, 150), (260, 230), (150, 230)]
DXF_ROAD = [(350, -30), (350, 100), (420, 180), (480, 300)]
DXF_LINE = [(0, 300), (700, 330), (700, 360), (0, 330)]


def converters(spec: dict):
    to_xy = Transformer.from_crs("EPSG:4326", f"EPSG:{spec['epsg']}", always_xy=True)
    to_ll = Transformer.from_crs(f"EPSG:{spec['epsg']}", "EPSG:4326", always_xy=True)
    ox, oy = to_xy.transform(spec["lon"], spec["lat"])

    def lonlat(points):
        return [tuple(round(v, 8) for v in to_ll.transform(ox + x, oy + y)) for x, y in points]

    return lonlat


def coords(points, close: bool) -> str:
    pts = list(points) + ([points[0]] if close else [])
    return " ".join(f"{lon:.8f},{lat:.8f},0" for lon, lat in pts)


def placemark(name: str, ring, desc: str = "", extra: dict | None = None, line: bool = False) -> str:
    data = "".join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in (extra or {}).items())
    ext = f"<ExtendedData>{data}</ExtendedData>" if data else ""
    if line:
        geom = f"<LineString><tessellate>1</tessellate><coordinates>{coords(ring, False)}</coordinates></LineString>"
    else:
        geom = f"<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing><coordinates>{coords(ring, True)}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
    d = f"<description>{escape(desc)}</description>" if desc else ""
    return f"<Placemark><name>{escape(name)}</name>{d}{ext}{geom}</Placemark>"


def kml(title: str, folders: dict[str, list[str]]) -> str:
    body = "".join(f"<Folder><name>{escape(n)}</name>{''.join(items)}</Folder>" for n, items in folders.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
        f"<name>{escape(title)}</name>\n<description>SYNTHETIC TEST DATA - not a real site.</description>\n{body}\n</Document>\n</kml>\n"
    )


def build_ground() -> dict:
    ll = converters(GROUND)
    folders = {
        "Site": [placemark("Site boundary", ll(BOUNDARY), "Synthetic land parcel")],
        "Exclusions": [
            placemark("Village pond", ll(POND)),
            placemark("Rocky outcrop", ll(ROCK)),
            placemark("132 kV transmission corridor", ll(CORRIDOR), "Existing line, 40 m wide corridor"),
        ],
        "Access": [placemark("Access road", ll(ROAD), extra={"width_m": 5}, line=True)],
    }
    (HERE / "ground_mount_rajasthan.kml").write_text(kml(GROUND["name"], folders), encoding="utf-8")

    def feature(name, kind, ring, **props):
        geom = {"type": "Polygon", "coordinates": [[list(p) for p in ll(ring)] + [list(ll(ring)[0])]]}
        return {"type": "Feature", "properties": {"name": name, "kind": kind, **props}, "geometry": geom}

    road = {"type": "Feature", "properties": {"name": "Access road", "kind": "road", "width_m": 5}, "geometry": {"type": "LineString", "coordinates": [list(p) for p in ll(ROAD)]}}
    gj = {
        "type": "FeatureCollection",
        "note": "SYNTHETIC TEST DATA - not a real site.",
        "features": [
            feature("Site boundary", "boundary", BOUNDARY),
            feature("Village pond", "water", POND),
            feature("Rocky outcrop", "exclusion", ROCK),
            feature("132 kV transmission corridor", "transmission_line", CORRIDOR),
            road,
        ],
    }
    (HERE / "ground_mount_rajasthan.geojson").write_text(json.dumps(gj, indent=1) + "\n", encoding="utf-8")
    return {
        "boundary_m2": Polygon(BOUNDARY).area,
        "pond_m2": Polygon(POND).area,
        "rock_m2": Polygon(ROCK).area,
        "corridor_m2": Polygon(CORRIDOR).area,
    }


def build_rooftop() -> dict:
    ll = converters(ROOFTOP)
    folders = {
        "Plot": [placemark("MIDC plot boundary", ll(PLOT), "Plot in an industrial area, synthetic")],
        "Roofs": [placemark(n, ll(r), extra={"height_m": 9.5}) for n, r in SHEDS.items()],
        "Obstacles": [placemark(n, ll(r), extra={"height_m": 0.6}) for n, r in SKYLIGHTS.items()]
        + [placemark(n, ll(r), extra={"height_m": h}) for n, (r, h) in ROOF_UNITS.items()]
        + [placemark("Overhead water tank", ll(TANK[0]), extra={"height_m": TANK[1]}), placemark("Chimney stack", ll(CHIMNEY[0]), extra={"height_m": CHIMNEY[1]})],
    }
    text = kml(ROOFTOP["name"], folders)
    (HERE / "rooftop_midc_pune.kml").write_text(text, encoding="utf-8")
    with zipfile.ZipFile(HERE / "rooftop_midc_pune.kmz", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("doc.kml", (2020, 1, 1, 0, 0, 0)), text)
    return {
        "plot_m2": Polygon(PLOT).area,
        "roof_a_m2": Polygon(SHEDS["Shed A roof"]).area,
        "roof_b_m2": Polygon(SHEDS["Shed B roof"]).area,
        "obstacle_m2": sum(Polygon(r).area for r in [*SKYLIGHTS.values(), *(u[0] for u in ROOF_UNITS.values()), TANK[0], CHIMNEY[0]]),
    }


def build_dxf() -> dict:
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 6
    for layer in ("SITE_BOUNDARY", "POND", "TL_CORRIDOR", "ACCESS_ROAD", "WATER_TANK", "TEXT"):
        doc.layers.add(layer)
    msp = doc.modelspace()
    msp.add_lwpolyline(DXF_BOUNDARY, close=True, dxfattribs={"layer": "SITE_BOUNDARY"})
    hatch = msp.add_hatch(dxfattribs={"layer": "POND"})
    hatch.paths.add_polyline_path(DXF_POND, is_closed=True)
    msp.add_lwpolyline(DXF_LINE, close=True, dxfattribs={"layer": "TL_CORRIDOR"})
    msp.add_lwpolyline(DXF_ROAD, dxfattribs={"layer": "ACCESS_ROAD"})
    msp.add_circle((560, 120), 8, dxfattribs={"layer": "WATER_TANK"})
    msp.add_text("SYNTHETIC SITE PLAN - NOT A REAL SITE", dxfattribs={"layer": "TEXT", "height": 6, "insert": (10, 480)})
    doc.saveas(HERE / "site_plan_kutch.dxf")
    return {
        "boundary_m2": Polygon(DXF_BOUNDARY).area,
        "pond_m2": Polygon(DXF_POND).area,
        "corridor_m2": Polygon(DXF_LINE).area,
        "tank_m2": math.pi * 8**2,
    }


if __name__ == "__main__":
    expected = {
        "ground_mount_rajasthan": {**GROUND, "areas_m2": build_ground()},
        "rooftop_midc_pune": {**ROOFTOP, "areas_m2": build_rooftop()},
        "site_plan_kutch": {**KUTCH, "areas_m2": build_dxf()},
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1) + "\n", encoding="utf-8")

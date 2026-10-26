# Site fixtures

All files are **synthetic**. The coordinates are invented to resemble real sites and are not surveyed data. Regenerate them with `uv run python fixtures/sites/generate.py`; `expected.json` records the designed areas in grid metres (the importer must reproduce them).

| File | What it is | Georeference |
|---|---|---|
| `ground_mount_rajasthan.kml`, `.geojson` | 200 acre ground-mount parcel near Phalodi: boundary, village pond, rocky outcrop, 132 kV transmission corridor, 5 m access road | lon/lat; UTM zone 43N (EPSG:32643) |
| `rooftop_midc_pune.kml`, `.kmz` | Industrial plot in an MIDC area near Chakan, Pune: plot boundary, two shed roofs, skylights, rooftop units, a water tank and a chimney (with `height_m`) | lon/lat; UTM zone 43N |
| `site_plan_kutch.dxf` | 33 ha site plan in metres (`$INSUNITS` 6): layers `SITE_BOUNDARY`, `POND` (hatch), `TL_CORRIDOR`, `ACCESS_ROAD`, `WATER_TANK` (circle) | none in the file: drawing 0,0 is at 69.8000 E, 23.4500 N (UTM zone 42N, EPSG:32642); pass it as `origin_lon`/`origin_lat` |

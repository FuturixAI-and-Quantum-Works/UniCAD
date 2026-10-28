# UniCAD MCP tools

Generated from the registered tools by `uv run unicad-mcp --write-docs docs/mcp-tools.md`; `tests/test_mcp.py` fails when this file is out of date. Do not edit by hand. The tool sequence for a whole project is in [agent-workflow.md](agent-workflow.md).

Mutating tools return `{project_id, revision, changed, summary, problems}`. `changed` is false and `revision` stays put when the edit leaves the model identical. Failures come back as tool errors carrying the domain's message.

## Project tools

### `project_create`

Create a project from minimal intent. Design-basis values you do not pass are filled with placeholders and listed under `assumptions`.

Example: name="500 kWp Rooftop", client="Demo Industries", segment="ci_rooftop", site="Factory roof", grid_voltage_v=415.
Creating the same project again with identical inputs changes nothing.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | yes | Project title, e.g. '500 kWp Rooftop Solar PV Plant' |
| `client` | string | yes | Client company name |
| `segment` | `residential` \| `ci_rooftop` \| `carport` \| `utility` \| `bess` \| `hybrid` | yes | Project segment: residential, ci_rooftop, carport, utility, bess, hybrid |
| `site` | string | yes | Site name, e.g. 'Demo Industries factory roof' |
| `grid_voltage_v` | number | yes | Grid connection voltage in volts, e.g. 415 or 33000 |
| `project_id` | string | no | Lowercase id (letters, digits, '-', '_'); derived from name when omitted |
| `state` | string | no | Indian state of the site |
| `address` | string | no |  |
| `utility` | string | no | DISCOM or transmission utility |
| `client_code` | string | no | Short client code for drawing numbers |
| `ambient_min_c` | number | no | Site minimum ambient °C |
| `ambient_max_c` | number | no | Site maximum ambient °C |
| `altitude_m` | number | no | Site altitude in metres |
| `vdrop_dc_max` | number | no | Max DC voltage drop as a fraction, e.g. 0.02 |
| `vdrop_ac_max` | number | no | Max AC voltage drop as a fraction |
| `dc_ac_ratio_target` | number | no | Target DC/AC ratio |
| `power_factor` | number | no | Design power factor |

### `project_get`

Summary of a project revision: client, site, grid, counts, assumptions. Pass full=true for the whole model JSON.

Example: project_get(project_id="rooftop-500kwp").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |
| `full` | boolean | no | Also return the complete project JSON (large) Default: `False`. |

### `project_list`

List the projects in the workspace with their latest revision.

Read-only.

### `project_revisions`

List a project's revisions with snapshot labels and counts. Use the numbers with project_get and project_diff.

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |

### `project_snapshot`

Name a revision so it can be referred to later (for example the issued set). Re-applying the same label to the same revision does nothing.

Example: project_snapshot(project_id="rooftop-500kwp", label="issued-r0").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `label` | string | yes | Label starting with a letter, e.g. 'issued-approval-r0' |
| `revision` | integer | no | Revision number to label; omit for the latest |

### `project_diff`

What changed between two revisions: added, removed and changed nodes, edges, groups, sheets (register rows), site features (outline summarised as point count and area) and layout blocks (table, module, string counts), plus changed project fields.

Example: project_diff(project_id="rooftop-500kwp", from_revision=1).

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `from_revision` | integer or string | yes | Older revision number or snapshot label |
| `to_revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

### `project_design_basis`

Set the design-basis inputs the rules and the earthing, lightning and drawing set use: site values (ambient temperatures, altitude),
limits (voltage drop, DC/AC target, power factor), fault levels per system voltage, earthing inputs and lightning protection inputs.
Only the parts you pass (or clear) change, and a value you set replaces its placeholder in `assumptions`. The design is not run:
after changing site values, limits or fault levels on a project with a network, re-run network_autobuild so strings, breakers and
cables are sized for them (earthing_design and lps_design store the same inputs and report the design; drawings_render draws the
earthing and LA layouts from them). Site setbacks are set with site_set_setbacks.

Example: project_design_basis(project_id="p1", earthing={"soil_resistivity_ohm_m": 50, "fault_current_ka": 12.5, "fault_clearing_s": 1},
lps={"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `fault_levels` | list of object | no | Replace the fault levels: one per system voltage, e.g. [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "DISCOM letter"}] |
| `earthing` | object | no | Replace the earthing inputs (soil resistivity from the Wenner test is required) |
| `lps` | object | no | Replace the lightning protection inputs (level from the IS/IEC 62305-2 risk assessment is required) |
| `clear` | list of `fault_levels` \| `earthing` \| `lps` | no | Inputs to remove Default: `[]`. |
| `ambient_min_c` | number | no | Site minimum ambient °C (sets the coldest cell temperature for string Voc) |
| `ambient_max_c` | number | no | Site maximum ambient °C |
| `altitude_m` | number | no | Site altitude in metres |
| `vdrop_dc_max` | number | no | Max DC voltage drop as a fraction, e.g. 0.015 |
| `vdrop_ac_max` | number | no | Max AC voltage drop as a fraction |
| `dc_ac_ratio_target` | number | no | Target DC/AC ratio |
| `power_factor` | number | no | Design power factor |

### `project_issue`

Issue the drawing set: add a revision (mark, date, purpose, drawn/checked/approved names, remarks) that every sheet's title
block, revision table and the drawing register show from the next drawings_render. The engineering rules run first (as in
validate): approval, construction and as_built are refused while any error remains, and the refusal lists every error;
proposal and information are allowed with errors and the response says the sheets carry them. A revision mark already
issued is refused unless every value is the same (then nothing changes). Re-render with drawings_render afterwards.

Example: project_issue(project_id="rooftop-500kwp", issued_for="approval", rev="0", drawn="A. Kumar", checked="S. Rao", approved="P. Iyer", date="2026-10-28").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `issued_for` | `proposal` \| `information` \| `approval` \| `construction` \| `as_built` | yes | Purpose of the issue: proposal, information, approval, construction, as_built |
| `rev` | string | yes | Revision mark printed on every sheet, e.g. '0', 'A' or 'R1' |
| `drawn` | string | yes | Name (or initials) of who drew the set, printed in the DRAWN cell |
| `checked` | string | no | Name of the checker, printed in the CHECKED cell |
| `approved` | string | no | Name of the approver, printed in the APPROVED cell |
| `date` | string | no | Issue date as YYYY-MM-DD; today when omitted |
| `remarks` | string | no | Revision description in the revision table; 'ISSUED FOR <PURPOSE>' when omitted |

## Catalog tools

### `catalog_search`

Find equipment in the catalog; returns compact rows with the key ratings. Use the row `id` as a node `spec` or edge `cable`.

When nothing matches, `note` says where the catalog's range of that kind ends (for example the largest module) and `range`, `nearest_below`, `nearest_above` give the numbers.

Example: catalog_search(kind="inverter", min_power=90, max_power=110).

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `text` | string | no | Words that must all appear in id, kind, manufacturer or model, e.g. 'growatt 100' |
| `kind` | `module` \| `inverter` \| `battery` \| `pcs` \| `transformer` \| `cable` \| `protection` \| `meter` \| `logger` | no | Catalog kind: module, inverter, battery, pcs, transformer, cable, protection, meter or logger (data logger) |
| `manufacturer` | string | no | Exact manufacturer name, case-insensitive |
| `min_power` | number | no | Inclusive lower bound: W for modules, kVA for inverter, pcs and transformer |
| `max_power` | number | no | Inclusive upper bound, same units as min_power |
| `limit` | integer | no | Maximum rows returned, number Default: `20`. |

### `catalog_get`

Full catalog entry with every rating and its source.

Example: catalog_get(id="growatt-max-100ktl3-x-lv").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Catalog id from catalog_search |

## Site tools

### `site_import`

Import site geometry (boundary, roofs, exclusions, roads, obstacles) from KML/KMZ, GeoJSON, DXF or a coordinate list into local metres.

The local frame is the UTM zone of the site; its origin (the lon/lat of local 0,0) is stored with the geometry. Shapes are classified by name, folder or
layer (see kind_mapping). Importing the same file again replaces what it imported before; features from other files or added by hand are kept. With replace=true the old features go but the default setbacks (site_set_setbacks) are kept.
Closed polygons become areas; open paths become roads, setback lines or transmission lines.
Example: site_import(project_id="p1", file_path="/data/site.kml", kind_mapping={"Plot": "boundary"}).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `file_path` | string | yes | Path to a .kml, .kmz, .geojson, .dxf or coordinate-list (.csv/.txt) file on the machine running the server |
| `format` | `kml` \| `kmz` \| `geojson` \| `dxf` \| `coords` | no | Site file format: kml, kmz, geojson, dxf or coords; guessed from the extension when omitted |
| `kind_mapping` | object | no | Overrides for how shapes are classified: {placemark name, folder name or DXF layer: kind}. Keys match case-insensitively, exact first and then as a substring; the value is a feature kind or 'ignore'. Without it names are matched against a rules table (e.g. 'roof', 'pond', 'boundary', 'road', 'transmission'). |
| `origin_lon` | number | no | Longitude of the file's local (0, 0). Only for local-metre files (DXF, coordinate lists) when the project has no site latitude/longitude yet |
| `origin_lat` | number | no | Latitude of the file's local (0, 0); pair with origin_lon |
| `source_crs` | string | no | EPSG code when a DXF or coordinate list holds projected metres, e.g. 'EPSG:32643'; omit for local metres |
| `units` | `m` \| `mm` \| `cm` \| `ft` \| `in` \| `km` \| `yd` \| `dm` | no | Drawing unit of a DXF without a usable $INSUNITS: m, mm, cm, ft, in, km, yd or dm |
| `coordinate_system` | `lonlat` \| `latlon` \| `local` | no | Coordinate lists only: lonlat, latlon or local (metres). Guessed when omitted |
| `replace` | boolean | no | Discard the existing site geometry first and start a new local frame; the default setbacks are kept Default: `False`. |

### `site_set_feature`

Add or replace one site feature from coordinates, e.g. a pond the survey missed or a roof obstacle. Creates the site frame if the project has none.

Example: site_set_feature(project_id="p1", id="ahu_3", kind="obstacle", coordinates=[[10,5],[14,5],[14,8],[10,8]], height_m=1.8).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `id` | string | yes | Feature id (letters, digits, '_', '-', '.'); an existing id is replaced |
| `kind` | `boundary` \| `roof` \| `exclusion` \| `road` \| `building` \| `obstacle` \| `water` \| `setback_line` \| `transmission_line` \| `existing_structure` \| `switchyard` | yes | Feature kind: boundary, roof, exclusion, road, building, obstacle, water, setback_line, transmission_line, existing_structure, switchyard; 'switchyard' marks the MV switchyard area that layout, routing and earthing use |
| `coordinates` | list of list of number | yes | Points as [x, y] pairs: local metres, or [lon, lat] when coordinate_system='lonlat'. Do not repeat the first point at the end |
| `coordinate_system` | `lonlat` \| `latlon` \| `local` | no | 'local' (metres in the site frame), 'lonlat' (degrees) or 'latlon' Default: `local`. |
| `closed` | boolean | no | Polygon (true) or open path (false). Default: polygon, except road, setback_line and transmission_line which default to a path |
| `holes` | list of list of list of number | no | Inner rings of a polygon, same coordinate system |
| `name` | string | no |  |
| `height_m` | number | no | Height in metres above the roof or ground, for roofs and obstacles |
| `width_m` | number | no | Corridor width in metres of a road, setback line or transmission line path |
| `setback_m` | number | no | Clearance in metres to keep around this feature (inset for boundary and roof), replacing the project default |
| `attributes` | object | no |  |
| `origin_lon` | number | no | Longitude of the file's local (0, 0). Only for local-metre files (DXF, coordinate lists) when the project has no site latitude/longitude yet |
| `origin_lat` | number | no | Latitude of the file's local (0, 0); pair with origin_lon |

### `site_remove_feature`

Remove site features by id. The site frame (CRS and origin) stays.

Example: site_remove_feature(project_id="p1", ids=["rocky-outcrop"]).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `ids` | list of string | yes | Ids of site features to remove |

### `site_set_setbacks`

Set the default clearances used for buildable area. Values you omit keep their stored value (initially 0). Take the distances from the design basis or the authority's rules; none are assumed.

Example: site_set_setbacks(project_id="p1", boundary_m=5, roof_edge_m=1.0).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `boundary_m` | number | no | Clearance in metres inside the site boundary, e.g. for a perimeter fence road |
| `roof_edge_m` | number | no | Clearance in metres inside each roof edge |
| `exclusion_m` | number | no | Clearance in metres around exclusions, water, buildings, existing structures and road or line corridors |
| `obstacle_m` | number | no | Clearance in metres around obstacles such as roof units |

### `site_summary`

Site geometry summary: frame, feature counts, areas in m2 and acres, buildable area after setbacks, and warnings. Setback arguments try other clearances without storing them.

Areas are on the projected grid (grid_scale_factor is the grid/ground distance ratio at the origin).
Example: site_summary(project_id="p1", boundary_setback_m=10).

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |
| `boundary_setback_m` | number | no | Trial boundary clearance in metres; not stored |
| `roof_edge_setback_m` | number | no | Trial roof edge clearance in metres; not stored |
| `exclusion_setback_m` | number | no | Trial exclusion clearance in metres; not stored |
| `obstacle_setback_m` | number | no | Trial obstacle clearance in metres; not stored |

## Layout tools

### `layout_place`

Place PV tables or trackers on the project's buildable site area, split them into inverter blocks of near-equal DC capacity,
site an inverter station per block and assign strings to module slots. Replaces any existing array layout.

Uses the site setbacks stored with site_set_setbacks. When the network has pv_string nodes, their instances are linked to the
planned string slots in block order; otherwise the layout is stored without string assignments and the plan is reported.
Blocks keep to one side of every road and line corridor while each side holds whole inverters in equal blocks; otherwise a warning names the
blocks that stand on both sides of a transmission line corridor (network_autobuild then keeps every inverter's strings on one side of the line
except where a side's strings do not make up whole inverters, and reports those). The MV switchyard and the BESS area keep 3 m (assumed) from
the boundary fence; set switchyard_m in project_design_basis earthing before this call so the yard is kept free of
tables at its real size.
Example: layout_place(project_id="p1", module="waaree-bi-55-540", tilt_deg=25, road_every_rows=12, block_dc_kwp=15000).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `module` | string | yes | Catalog id of the PV module, e.g. 'waaree-bi-55-540' |
| `mounting` | `fixed_tilt` \| `tracker_1p` \| `rooftop_flush` \| `rooftop_tilted` \| `carport` | no | Mounting structure: fixed_tilt, tracker_1p, rooftop_flush, rooftop_tilted, carport Default: `fixed_tilt`. |
| `table_rows` | integer | no | Modules across a table (2 for 2P, 1 for a 1P tracker) Default: `2`. |
| `table_cols` | integer | no | Modules along a table or tracker; default the string length (modules_per_string), else 28 |
| `orientation` | `portrait` \| `landscape` | no | portrait or landscape Default: `portrait`. |
| `tilt_deg` | number | no | Fixed tilt in degrees from horizontal; 0 for trackers and flat roofs Default: `0.0`. |
| `azimuth_deg` | number | no | Direction the modules face in degrees, clockwise from grid north; 180 = south Default: `180.0`. |
| `axis_azimuth_deg` | number | no | Tracker axis direction in degrees, clockwise from grid north; 0 = north-south Default: `0.0`. |
| `pitch_method` | `explicit` \| `gcr` \| `gap` \| `solstice` | no | Row pitch method: explicit, gcr, gap, solstice. solstice is shade-free 09:00-15:00 solar time on the winter solstice; explicit uses pitch_m, gcr uses gcr, gap uses row_gap_m Default: `solstice`. |
| `pitch_m` | number | no | Row-to-row pitch in metres for pitch_method='explicit' |
| `gcr` | number | no | Ground coverage ratio for pitch_method='gcr' |
| `row_gap_m` | number | no | Clear gap between rows in metres for pitch_method='gap' |
| `half_tables` | boolean | no | Fill edges with half tables (only where a half table holds whole strings, unless half_cols is given) Default: `True`. |
| `half_cols` | integer | no | Modules along a half table; default the longest shorter table that holds whole strings |
| `table_gap_m` | number | no | Gap between tables along a row (default 1.5 m on the ground, 0.6 m maintenance walkway on roofs; reported as an assumption) |
| `module_gap_m` | number | no | Gap between modules on a table (default 0.02 m, reported as an assumption) |
| `road_every_rows` | integer | no | Leave an internal road after every N rows |
| `road_width_m` | number | no | Internal road width (default 6 m) |
| `walkway_every_tables` | integer | no | Leave a walkway after every N tables along a row |
| `walkway_width_m` | number | no | Walkway width (default 4 m) |
| `row_walkway_every_rows` | integer | no | Leave a maintenance walkway between rows after every N rows; on roofs the default is 2 when the rows stand closer than a walkway (reported as an assumption) |
| `row_walkway_m` | number | no | Clear width of the walkway between rows in metres (default 0.6 m) |
| `target_dc_kwp` | number | no | Stop at this DC capacity in kWp instead of filling the site |
| `fill` | `switchyard` \| `rows` \| `nearest` | no | With a target: switchyard (default) grows the array from the switchyard (switchyard_x/y, an area marked on the site, else the site's transmission line) and falls back to rows; rows fills the front rows first; nearest fills closest to fill_from Default: `switchyard`. |
| `fill_from_x` | number | no | Local x (m) of the point to fill around, e.g. the pooling substation |
| `fill_from_y` | number | no | Local y (m) of the point to fill around |
| `n_blocks` | integer | no | Number of inverter blocks |
| `block_dc_kwp` | number | no | Largest DC capacity per block in kWp; the block count is rounded up from it |
| `modules_per_string` | integer | no | Modules in series; default the network pv_string modules_in_series, else table_cols |
| `strings_per_inverter` | integer | no | Strings per inverter within a block (string inverters); default one inverter per block |
| `regions` | `auto` \| `ground` \| `roofs` | no | Where to place: auto (roofs for rooftop mountings, else ground), ground or roofs Default: `auto`. |
| `whole_inverters` | boolean | no | With strings_per_inverter: size every block to whole inverters and leave out the few remainder tables instead of adding a part-loaded inverter Default: `True`. |
| `station_x` | number | no | Local x (m) of the inverter station for every block, e.g. a ground-level inverter room; default on the block (on a roof for rooftop arrays) |
| `station_y` | number | no | Local y (m) of the inverter station |
| `switchyard_x` | number | no | Local x (m) of the MV switchyard (main LT panel on an LT plant); cable routing and earthing read it. Default: a site area of kind 'switchyard', else derived and reported |
| `switchyard_y` | number | no | Local y (m) of the MV switchyard |
| `bess_area_m` | list of number | no | BESS area length x width (m) kept free of tables beside the switchyard, where network_autobuild then places the BESS units. Default on hybrid and BESS plants: the grid of the network's BESS units, else an assumed area (reported) |

### `layout_summary`

Array layout totals: tables (full and half), modules, DC kWp, blocks and their balance, row pitch and GCR, linked strings, the
switchyard location with how it was chosen, and the equipment network_autobuild placed in the field (string inverters, BESS units) by kind.

Example: layout_summary(project_id="p1").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

### `layout_blocks`

Inverter blocks: tables full/half, modules, DC kWp, station location (local metres) and outline extent. With `block`,
also that block's tables (id, centre, modules) and linked strings.

Example: layout_blocks(project_id="p1", block="B02").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |
| `block` | string | no | One block id (e.g. 'B03') to list its tables and strings |

## Design tools

### `design_strings`

Size PV strings for a module on an inverter against the project's design basis, with the rules engine's temperature models.

Returns the allowed modules-per-string range and the chosen length, string voltages at the coldest and hottest cell temperatures,
strings per MPPT and per inverter (limit and the count nearest the DC/AC target), the DC/AC ratio achieved, which constraint binds
each quantity, and the margins to each limit. strings_per_inverter respects the string-fuse rule validate applies (pv_strings.string_fuses):
strings_per_mppt_unfused / strings_per_inverter_unfused are the most that need no fuse; string_fuses is 'optional' when the DC/AC target needs
more, with the trade-off in the assumptions (pass that strings_per_inverter to network_autobuild for a design with one gPV fuse per string).
Example: design_strings(project_id="p1", module="waaree-bi-55-540", inverter="sungrow-sg110cx").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `module` | string | yes | Catalog id of the PV module |
| `inverter` | string | yes | Catalog id of the inverter |
| `modules_per_string` | integer | no | Check a given string length instead of picking the longest |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Network tools

### `network_autobuild`

Replace the project's electrical network with a complete one built from a short intent: strings sized from the catalog and design basis,
a topology template (residential, ci_rooftop, utility_string, utility_central, bess_ac, bess_dc, hybrid) with protection, metering, SPDs and
earthing, breakers rated >= 1.25 x design current and cables sized for ampacity and voltage drop. Then runs the rules.

Returns the new revision, a summary (instances per kind, DC kWp, AC kVA), rule counts, the remaining error and warning findings, blockers
(what the catalog or the intent could not satisfy) and every assumption made. Example:
network_autobuild(project_id="p1", template="ci_rooftop", ac_kw=500, module="adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp", inverter="huawei-sun2000-100ktl-m2").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `template` | `residential` \| `ci_rooftop` \| `utility_string` \| `utility_central` \| `bess_ac` \| `bess_dc` \| `hybrid` | no | Topology; default from the project segment (utility: central when the inverter is central, else string) |
| `ac_kw` | number | no | PV AC capacity in kW (sets the inverter count); for bess_ac the storage power |
| `dc_kwp` | number | no | PV DC capacity in kWp |
| `module` | string | no | Catalog module id; default the highest-Pmax ALMM-listed module |
| `inverter` | string | no | Catalog inverter id; default picked per template |
| `grid_voltage_v` | number | no | Grid connection voltage; updates the design basis when it differs |
| `modules_per_string` | integer | no | Fix the string length (default: longest inside the limits) |
| `strings_per_inverter` | integer | no | Fix strings per inverter (default: nearest the DC/AC target) |
| `inverters_per_block` | integer | no | Inverters per transformer block (central default 2) |
| `step_up_kv` | number | no | C&I: step-up voltage in kV for an HT connection |
| `connection` | `lt_panel` \| `dedicated_feeder` | no | C&I LT: via the consumer LT panel or a dedicated feeder Default: `lt_panel`. |
| `metering` | `net` \| `abt` | no | C&I HT metering: net or ABT Default: `net`. |
| `block_switch` | `vcb` \| `rmu` | no | Switch on each block transformer's HV side Default: `vcb`. |
| `auxiliary` | boolean | no | Add block and plant auxiliary transformers Default: `True`. |
| `export_limit_kw` | number | no | Sanctioned export capacity in kW, recorded on the POI |
| `export_control` | `none` \| `ppc` | no | ppc: a power plant controller limits the export at the POI to export_limit_kw (PV inverters + BESS PCS may exceed it) Default: `none`. |
| `bess_kw` | number | no | Storage power in kW (bess_dc, hybrid) |
| `bess_kwh` | number | no | Storage energy in kWh |
| `bess_brief_kw` | number | no | Storage power in kW the brief asks for, when bess_kw is a whole-unit size away from it (default bess_kw) |
| `bess_brief_kwh` | number | no | Storage energy in kWh the brief asks for, when bess_kwh is a whole-unit size away from it (default bess_kwh) |
| `pcs` | string | no | Catalog PCS id |
| `battery` | string | no | Catalog battery id |
| `use_layout` | boolean | no | Build the PV plan from the project's array layout (layout_place) and link every layout string to one pv_string instance Default: `True`. |
| `lengths` | object | no | Cable route length overrides in m by role, e.g. {"vcb_bus": 650} |
| `bess_x` | number | no | Local x (m) of the BESS area centre (bess_ac, hybrid); default the free area nearest the switchyard |
| `bess_y` | number | no | Local y (m) of the BESS area centre |

### `network_add_group`

Add a repeated block of equipment (e.g. 5 identical inverter sections). Model identical equipment once and give the group a count.

Example: network_add_group(project_id="p1", id="inv_sec", count=5, title="100 kW INVERTER SECTION").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `id` | string | yes | Unique id (letters, digits, '_', '-', '.'), e.g. 'inv_sec' |
| `count` | integer | no | How many identical copies of everything in this group Default: `1`. |
| `tag` | string | no | Drawing tag; needs one {0:02d}-style placeholder per repeating group around it, including this one when count > 1. Default is derived from id. |
| `within` | string | no | Id of the enclosing group |
| `title` | string | no | Box title on the SLD, e.g. '100 kW STRING INVERTER SECTION' |

### `network_add_node`

Add an equipment node. Ports are named per kind: pv_string out; inverter dc, ac; transformer lv, hv; most others in, out.

Example: network_add_node(project_id="p1", id="inv", kind="inverter", spec="growatt-max-100ktl3-x-lv", group="inv_sec").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `id` | string | yes | Unique id for this equipment template, e.g. 'inv' |
| `kind` | `pv_string` \| `pv_array_block` \| `scb` \| `dc_fuse_box` \| `dc_isolator` \| `dc_spd` \| `dc_dc_converter` \| `inverter` \| `pcs` \| `battery` \| `acdb` \| `lt_panel` \| `breaker` \| `isolator` \| `fuse` \| `ac_spd` \| `ct` \| `pt` \| `meter` \| `relay` \| `transformer` \| `aux_transformer` \| `ht_panel` \| `rmu` \| `vcb` \| `busbar` \| `lightning_arrester` \| `earth_electrode` \| `poi_grid` \| `dg` \| `load` | yes | Equipment kind: pv_string, pv_array_block, scb, dc_fuse_box, dc_isolator, dc_spd, dc_dc_converter, inverter, pcs, battery, acdb, lt_panel, breaker, isolator, fuse, ac_spd, ct, pt, meter, relay, transformer, aux_transformer, ht_panel, rmu, vcb, busbar, lightning_arrester, earth_electrode, poi_grid, dg, load |
| `spec` | string | no | Catalog id from catalog_search (module, inverter, breaker, ...) |
| `count` | integer | no | Instances per instance of its group, e.g. 10 strings per inverter Default: `1`. |
| `group` | string | no | Id of the group it repeats with |
| `tag` | string | no | Drawing tag with {0:02d}-style placeholders (see network_add_group); default derived from id |
| `voltage_v` | number | no | Nominal voltage in volts of the circuit it sits on, e.g. 415 |
| `modules_in_series` | integer | no | Modules per string; pv_string only |
| `ports` | list of string | no | Override port names; defaults depend on kind (inverter: dc, ac) |
| `attributes` | object | no | Free label/value pairs shown on drawings, e.g. {"rating_a": 250} |
| `title` | string | no | Box title on the SLD |
| `symbol` | string | no | Override the drawing symbol key |

### `network_connect`

Connect two nodes. Instances pair up by group: equal counts connect one to one, a count of 1 fans in or out.

Example: network_connect(project_id="p1", src="inv:ac", dst="acdb:in", cable="polycab-...", length_m=25).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `src` | string | yes | Source terminal 'node_id:port'; power flows from src to dst (generation towards grid) |
| `dst` | string | yes | Destination terminal 'node_id:port' |
| `service` | `dc` \| `lv_ac` \| `mv_ac` \| `hv_ac` \| `earth` \| `control` | no | Circuit type (dc, lv_ac, mv_ac, hv_ac, earth, control); inferred from the ports and nodes when unambiguous, else required |
| `cable` | string | no | Cable catalog id from catalog_search(kind='cable') |
| `length_m` | number | no | Route length in metres, per instance |
| `runs` | integer | no | Parallel cables per connection Default: `1`. |
| `laying` | `air` \| `tray` \| `conduit` \| `trench` \| `buried_direct` \| `buried_duct` \| `overhead` | no | Laying method, which sets the ampacity table: air, tray, conduit, trench, buried_direct, buried_duct, overhead |
| `derating` | number | no | Installation derating factor, e.g. 0.8 |
| `remarks` | string | no | Free text shown in the cable schedule remarks |
| `edge_id` | string | no | Id for the connection; default 'e_<src>_<dst>' |

### `network_update`

Change fields of an existing node, connection or group. The id itself cannot change.

Node fields: tag, kind, spec, symbol, count, group, voltage_v (V), modules_in_series, ports, attributes, title.
Edge fields: src, dst, service, cable, runs, length_m (m), laying, derating, remarks. Group fields: tag, count, within, title.
Example: network_update(project_id="p1", id="e_inv_acdb", fields={"length_m": 30}).
Changing a group count can invalidate member tags; update those tags in the same session.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `id` | string | yes | Id of an existing node, connection (edge) or group |
| `fields` | object | yes | Fields to change; null clears a field. For `attributes` the given keys are merged (null removes a key). Enum-valued fields: kind (node), service and laying (edge). |

### `network_remove`

Remove nodes, connections or groups. Removing a node also removes its connections; the reply lists everything removed.

Example: network_remove(project_id="p1", ids=["scb"]).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `ids` | list of string | yes | Ids of nodes, connections (edges) or groups to remove |
| `include_members` | boolean | no | When removing a group, also remove the nodes and groups inside it Default: `False`. |

### `network_summary`

Counts by node kind (templates and real instances), connections by service, voltage zones, unconnected ports and nodes missing a catalog spec.

Example: network_summary(project_id="p1").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Validate tools

### `validate`

Run the engineering rules on a project and say whether its sheets may be issued for each purpose.

Returns finding counts, the findings (errors and warnings first; info only with include_info=true), and a gate per issued_for:
approval, construction and as_built are blocked by any error; proposal and information are allowed with a NOT FOR CONSTRUCTION watermark.
Use explain_finding for the formula, inputs and sources behind one finding.
Example: validate(project_id="rooftop-500kwp", families=["cables", "vdrop"]).

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `families` | list of `structure` \| `pv_strings` \| `ratio` \| `protection` \| `cables` \| `vdrop` \| `earthing` \| `lps` | no | Rule families to run; omit for all: structure, pv_strings, ratio, protection, cables, vdrop, earthing, lps |
| `include_info` | boolean | no | Also list info findings (segment voltage drops, unstated deratings); they are always counted. Default: `False`. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Explain tools

### `explain_finding`

Explain the findings of one rule about one subject: what the rule checks, the formula, every input with its source (catalog datasheet, design basis, network), the assumptions, and the measured value against the limit.

Example: explain_finding(project_id="rooftop-500kwp", rule_id="vdrop.ac", subject="inv").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `rule_id` | `structure.unconnected_port` \| `structure.path_to_poi` \| `structure.inverter_ac_voltage` \| `structure.transformer_voltage` \| `structure.missing_spec` \| `structure.bess_separation` \| `pv_strings.voc_max` \| `pv_strings.hot_cell` \| `pv_strings.vmp_min` \| `pv_strings.start_voltage` \| `pv_strings.vmp_max` \| `pv_strings.inputs` \| `pv_strings.isc_per_mppt` \| `pv_strings.operating_current` \| `pv_strings.string_fuses` \| `ratio.dc_ac_inverter` \| `ratio.dc_ac_plant` \| `ratio.bess_intent` \| `ratio.export_limit` \| `protection.rated_current` \| `protection.breaking_capacity` \| `protection.fault_level` \| `protection.oversize` \| `protection.rated_voltage` \| `protection.spd_dc` \| `protection.spd_ac` \| `protection.spd_ac_uc` \| `protection.spd_ac_poles` \| `protection.earthing` \| `protection.string_fuse_rating` \| `protection.mv_cable` \| `protection.lv_both_ends` \| `protection.transformer_feed` \| `cables.missing` \| `cables.ampacity` \| `cables.protection` \| `cables.terminals` \| `cables.battery_dc` \| `cables.short_circuit` \| `vdrop.dc` \| `vdrop.ac` \| `vdrop.bands` \| `earthing.conductor_size` \| `earthing.electrode_resistance` \| `earthing.touch_step` \| `earthing.every_equipment_bonded` \| `earthing.mat_fence_clearance` \| `lps.coverage` \| `lps.mast_fence_clearance` | yes | Rule id from validate, e.g. 'cables.ampacity' |
| `subject` | string | yes | One of the finding's subjects from validate: a node or edge id, e.g. 'e_inv_mccb' |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Earthing tools

### `earthing_design`

Design the earthing system: store the inputs in the design basis, then size the GS flat conductor (IS 3043 adiabatic, IEEE 80
cross-check), count pits for the target resistance and the fault current, check the MV switchyard grid's touch and step voltage
(IEEE 80, grid current split between the switchyard grid and the rest of the plant earth) and lay out pits and conductors bonding
every table row, station, panel, switchyard item and lightning terminal.

Inputs you leave out keep the values already in the design basis (set with project_design_basis or an earlier call); use
project_design_basis to clear one. Run layout_place first; run lps_design to include the lightning terminals' pits. Returns the
design summary and quantities.
Example: earthing_design(project_id="p1", soil_resistivity=50, fault_ka=12.5, clearing_s=1, switchyard_m=[60, 45]).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `soil_resistivity` | number | no | Soil resistivity in ohm m from the site's Wenner test; required unless already in the design basis |
| `fault_ka` | number | no | Earth fault current in kA the conductors and electrodes must carry |
| `clearing_s` | number | no | Fault clearing time in s (backup protection) |
| `target_ohm` | number | no | Target earth resistance; default from practice (1 ohm utility, 2 ohm C&I), reported as an assumption |
| `electrode_length_m` | number | no | Electrode (pipe) length; default 3 m |
| `electrode_diameter_mm` | number | no | Electrode (pipe) diameter; default 40 mm |
| `burial_depth_m` | number | no | Burial depth of the earth conductor; default 0.6 m |
| `corrosion_allowance` | number | no | Extra cross-section for corrosion, e.g. 0.2 for 20 %; default 0 |
| `tables_per_pit` | integer | no | Ground-mount arrays: at least one array pit per this many tables; default 20 |
| `surface_resistivity` | number | no | Switchyard surface layer (crushed rock) resistivity, ohm m |
| `surface_layer_m` | number | no | Switchyard surface layer thickness in metres |
| `switchyard_m` | list of any | no | MV switchyard earth grid length x width in m, from the substation layout; needed for the IEEE 80 touch and step check unless the site marks a switchyard area |

### `earthing_summary`

The stored earthing and lightning design recomputed from the project: conductor sizes, pits by type, earth resistance against the
target, switchyard touch/step check, bonding, terminals and coverage, quantities, calculation steps with sources, and assumptions.

Example: earthing_summary(project_id="p1").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Lps tools

### `lps_design`

Design the lightning protection: store the inputs, then place the fewest terminals that put every table centroid and corner,
inverter station, MV switchyard, BESS unit, string inverter and building inside a protection zone, and prove it with covered / total per
class. The catalog has no dimensions, so stations and placed equipment are covered over an assumed footprint (stated in the assumptions). ESE follows NF C 17-102, a French standard used in Indian
EPC practice; rods use the IS/IEC 62305 rolling sphere of the level.

Example: lps_design(project_id="p1", method="ese", level="III", ese_delta_t_us=60, mast_height_m=6).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `method` | `ese` \| `rod` | yes | ese (early streamer emission, NF C 17-102) or rod (conventional air terminal, rolling sphere) |
| `level` | `I` \| `II` \| `III` \| `IV` | yes | Lightning protection level from the IS/IEC 62305-2 risk assessment: I, II, III or IV |
| `ese_radius_m` | number | no | ESE protection radius in metres from the terminal datasheet for this level and height |
| `ese_delta_t_us` | number | no | ESE efficiency dT in microseconds (µs) from the test certificate (radius computed by NF C 17-102) |
| `mast_height_m` | number | no | Terminal height in metres above the protected plane (ESE) or rod height |
| `protected_height_m` | number | no | Height in metres to protect for rods (table top or roof plant) |

## Cable tools

### `cable_schedule`

Write the cable schedule: one row per cable with Indian-EPC positional id, from/to, cable, runs, +ve/-ve lengths, laying, design current, derated ampacity, % voltage drop and power loss.

Voltage drop, current and ampacity come from the rules engine, so they match `validate`. Lengths the network states are used as given; the rest are estimated from the array layout
(Manhattan route along the table rows plus drops and termination allowance, all reported as assumptions). Units: lengths and `cable_m` totals in metres, design current Ib and ampacity Iz in A, voltage drop in %, power loss in W.
When the network's stored length is the worst-case route the cable was sized for (as network_autobuild stores it), each cable row carries its own routed length from the layout, not that worst case.
Returns the file paths, totals, the worst-path voltage drop, the length source counts and any cable the schedule could not complete.
Example: cable_schedule(project_id="rooftop-500kwp", format="pdf", lengths="estimated").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `format` | `xlsx` \| `dxf` \| `pdf` | no | xlsx: one row per cable or item; dxf: company-sheet pages, one DXF each; pdf: those pages printed through LibreCAD into one PDF Default: `xlsx`. |
| `out_dir` | string | no | Directory to write into; default <workspace>/<project_id>/exports |
| `lengths` | `auto` \| `network` \| `estimated` | no | auto: use the lengths the network states and estimate the edges that state none from the array layout; network: only stated lengths; estimated: replace stated lengths with layout estimates wherever the layout allows Default: `auto`. |
| `detail` | `auto` \| `full` \| `summary` | no | DXF and PDF only: list every string (full) or one row per combiner (summary); auto lists every string up to 150 strings. The XLSX always lists every cable Default: `auto`. |
| `drop_per_end_m` | number | no | Vertical drop added at each cable end when a length is estimated (default 1.5 m, reported as an assumption) Default: `1.5`. |
| `termination_m` | number | no | Termination slack added at each cable end when a length is estimated (default 1.0 m, reported as an assumption) Default: `1.0`. |
| `switchyard_x` | number | no | Local x (m) of the MV switchyard, which the project does not locate; default the centroid of the block stations |
| `switchyard_y` | number | no | Local y (m) of the MV switchyard |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Bom tools

### `bom`

Write the bill of materials: modules, inverters, combiner boxes, transformers, breakers, isolators, SPDs, meters and panels from the network expansion, cable metres by catalog type with wastage,
and MC4 connectors, lugs, glands and MV termination kits counted per termination of the cable schedule. When the project has earthing and lightning designs, it also lists their material as those designs count it:
earth pits by type (nos), GS flat by size and use (m, plan length), ESE terminals and their masts (nos).

Units: cable in metres, equipment, pits, terminals and masts in nos, MC4 in pairs, MV termination kits in sets. Returns the file paths, the headline quantities and the assumptions behind every derived count.
Cable metres use the same `lengths` basis as cable_schedule.
Example: bom(project_id="utility-50mwac", format="xlsx", wastage_pct=5).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `format` | `xlsx` \| `dxf` \| `pdf` | no | xlsx: one row per cable or item; dxf: company-sheet pages, one DXF each; pdf: those pages printed through LibreCAD into one PDF Default: `xlsx`. |
| `out_dir` | string | no | Directory to write into; default <workspace>/<project_id>/exports |
| `wastage_pct` | number | no | Cable wastage added to the routed metres (default 5 %, reported as an assumption) Default: `5.0`. |
| `lengths` | `auto` \| `network` \| `estimated` | no | auto: use the lengths the network states and estimate the edges that state none from the array layout; network: only stated lengths; estimated: replace stated lengths with layout estimates wherever the layout allows Default: `auto`. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Drawings tools

### `drawings_render`

Plan and render the project's drawing set from the stored project and return where every file is. The model is not changed.

kinds=["all"] gives the complete EPC set: register (drawing list of every sheet with number, title, size, scale, revision, purpose
and date), SLD (overall SLD with each identical block drawn once, a detail sheet per block type, more sheets wherever one would not
be readable on A0; conductors leaving a sheet end in off-sheet markers naming the other sheet), array layout (site plan at a true
scale; internal roads are not stored with the layout and are not drawn), earthing and lightning (LA) layouts (designed at render
time from design_basis.earthing and .lps, see project_design_basis), string wiring diagrams per inverter or SCB type, cable
schedule and bill of materials pages. A kind the project cannot draw yet (no layout, no earthing inputs) is skipped with a warning.
Numbers follow the company profile pattern: the project's stored sheet numbers of a kind are reused first, then each kind takes
fresh numbers from its block (register 000, SLD 001, layout 101, earthing 301, LPS after earthing, strings 401, cable schedule 501,
BOM 601). Returns per sheet: number, title, paper, 'sheet x of y' in the set and within its kind, drawing scale, files (absolute
paths), cross-references and warnings; plus the merged set PDF (one page per sheet). A missing DWG converter is a warning.
Example: drawings_render(project_id="utility-50mwac", kinds=["all"], formats=["dxf", "pdf"]).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `kinds` | list of `all` \| `register` \| `sld` \| `layout` \| `earthing` \| `lps` \| `strings` \| `cable_schedule` \| `bom` | no | Drawing kinds to include, in set order, or 'all' for the complete set. Available: ['all', 'register', 'sld', 'layout', 'earthing', 'lps', 'strings', 'cable_schedule', 'bom'] Default: `['sld']`. |
| `formats` | list of `dxf` \| `pdf` \| `dwg` | no | Files per sheet: dxf (always written), pdf (LibreCAD, plus one merged set PDF), dwg Default: `['dxf', 'pdf', 'dwg']`. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

### `drawings_preview`

PNG of one sheet (or a region of it) rasterised from the same PDF that goes into the issued set; renders the set first
when needed. Returns the PNG path, the region shown in sheet mm, pixels per mm and legibility warnings (crop smaller to read
small text). Example: drawings_preview(project_id="utility-50mwac", sheet="sld", region=[0, 0, 400, 300]).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `sheet` | string | yes | Sheet id or drawing number from drawings_render, e.g. 'sld' or 'UDEMO-DRL-U50-EE-001' |
| `region` | list of number | no | Crop [x0, y0, x1, y1] in sheet mm from the lower-left corner; omit for the whole sheet |
| `px` | integer | no | Pixels on the long side of the image Default: `1600`. |
| `kinds` | list of `all` \| `register` \| `sld` \| `layout` \| `earthing` \| `lps` \| `strings` \| `cable_schedule` \| `bom` | no | Drawing kinds of the set, when it has to be rendered first: ['all', 'register', 'sld', 'layout', 'earthing', 'lps', 'strings', 'cable_schedule', 'bom'] Default: `['sld']`. |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Company tools

### `company_list`

List the company profiles this workspace can use: its own (onboarded or edited) and the bundled ones. A workspace profile hides a bundled one of the same id.

Example: company_list().

Read-only.

### `company_onboard`

Create a company profile from the company's own DXF and a short questionnaire, and save it in this workspace.

Reads the title block per paper size, the logo, the layer list and the symbol blocks, checks each (placeholders present, pins matching, threshold ranges) and reports what was found, what was defaulted and what failed.
Nothing is saved while anything failed, unless accept_partial=true. Then assign it with company_assign and look at it with company_preview_sheet.
Example: company_onboard(dxf_path="/data/acme/company.dxf", answers="/data/acme/answers.yaml").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `dxf_path` | string | yes | The company's DXF: title block (a block per paper size holding {PLACEHOLDER} texts such as {DRAWING_NO}, {TITLE}, {REV}, {SHEET}, {DRAWN}), logo block, layer list and optionally its symbols |
| `answers` | object or string | yes | The questionnaire as an object, or the path of a YAML or JSON file. Needs id, name and short_code; optional: address, confidentiality_note, date_format, default_paper, roles, numbering {pattern, disciplines, per_discipline, start}, logo_block, title_blocks, layers {frame, title, text}, layer_map, symbols {key: {block, pins}}, thresholds {name: {value, source}}, revision_marks. See docs/onboarding.md. |
| `accept_partial` | boolean | no | Save the profile even though some pieces failed validation; those pieces are left out Default: `False`. |

### `company_get`

Show a company profile: name, drawing numbering, title block per paper size, layers and layer map, symbol overrides, and every rule threshold with its value and where the value comes from (default or company).

`problems` lists files that are missing or imported pieces that no longer validate.
Example: company_get(profile_id="acme").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `profile_id` | string | no | Company profile id, e.g. 'acme'; company_list shows them. Omit to use the profile of project_id, or 'default'. |
| `project_id` | string | no | Use this project's company profile when profile_id is omitted |

### `company_update`

Change a company profile and save it in this workspace (a bundled profile is copied there first). The result must validate and must not add problems with the imported files; nothing is saved otherwise.

Relative file paths are relative to <workspace>/profiles. Example: company_update(profile_id="acme", fields={"thresholds": {"dc_ac_band": {"value": 0.2, "source": "Acme DS-04 rev C"}}}).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `fields` | object | yes | Changes as a JSON merge patch on the profile: objects merge key by key, null removes a key, anything else replaces. E.g. {'thresholds': {'dc_ac_band': {'value': 0.2, 'source': 'DS-04 rev C'}}} |
| `profile_id` | string | no | Company profile id, e.g. 'acme'; company_list shows them. Omit to use the profile of project_id, or 'default'. |
| `project_id` | string | no | Use this project's company profile when profile_id is omitted |

### `company_assign`

Make a project use a company profile: its title block, drawing numbers, layers, symbols and rule thresholds apply from the next drawing render or validate.

Example: company_assign(project_id="rooftop-500kwp", profile_id="acme").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `profile_id` | string | yes | Profile id from company_list |

### `company_preview_sheet`

PNG of an empty sample sheet drawn with a company profile: its title block filled with sample data, drawing number, layers and company symbols in the legend. Use it to check an onboarded profile before drawing a project.

Crop to the title block with region=[x0, y0, x1, y1] (the right-hand column) to read its text. Example: company_preview_sheet(paper="A3", profile_id="acme").

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `paper` | `A0` \| `A1` \| `A2` \| `A3` \| `A4` | no | Paper size: A0, A1, A2, A3, A4 Default: `A3`. |
| `profile_id` | string | no | Company profile id, e.g. 'acme'; company_list shows them. Omit to use the profile of project_id, or 'default'. |
| `project_id` | string | no | Use this project's company profile when profile_id is omitted |
| `region` | list of number | no | Crop [x0, y0, x1, y1] in sheet mm from the lower-left corner; omit for the whole sheet |
| `px` | integer | no | Pixels on the long side of the image Default: `1600`. |

## Resources

- `unicad://catalog/{kind}`: Every catalog entry of one kind as compact rows.
- `unicad://project/{id}`: The complete project model JSON at its latest revision.
- `unicad://docs/tools`: Reference for every tool, resource and prompt this server exposes.

## Prompts

- `new_rooftop_project`: Step plan for a commercial or industrial rooftop plant, from site survey to issued drawing set.
- `new_utility_project`: Step plan for a ground-mount plant with MV collection, from site boundary to issued drawing set.
- `new_bess_project`: Step plan for a standalone or hybrid battery storage plant.
- `revise_after_comments`: Step plan for changing an issued project after reviewer or client comments, keeping a diffable baseline.

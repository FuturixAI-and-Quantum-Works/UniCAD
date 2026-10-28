from mcp.server.mcpserver import MCPServer

from unicad.mcp.workspace import Workspace

ROOFTOP = """Build a rooftop PV drawing set for "{name}" with UniCAD tools. State intent only; never invent ratings or coordinates.

1. Ask the user for anything missing: grid voltage, DC or AC capacity, inverter make/size, module make/Wp, roof survey file (KML, KMZ, GeoJSON, DXF), roof setbacks from the design basis, site ambient temperatures, fault level at the grid connection, soil resistivity, lightning protection level. Mark anything you assume.
2. project_create(name, client, segment="ci_rooftop", site, grid_voltage_v). Read back `assumptions`: they are placeholders.
3. project_design_basis(project_id, ...) before any design: ambient_min_c, ambient_max_c, altitude_m, fault_levels (one per system voltage, with its source), earthing (soil resistivity from the Wenner test, earth fault current and clearing time) and lps (level from the risk assessment, method). Each value you set replaces its placeholder.
4. site_import(project_id, file_path). Check `problems` and `imported_by_kind`; add missed obstacles with site_set_feature (kind="obstacle", height_m). site_set_setbacks with distances from the design basis, then site_summary for the buildable roof area.
5. catalog_search for the module and inverter, catalog_get to check ratings, design_strings(project_id, module, inverter) for the string length.
6. layout_place(project_id, module, mounting="rooftop_flush" or "rooftop_tilted", target_dc_kwp, modules_per_string, strings_per_inverter, ...). Check layout_summary: DC kWp reached, blocks, row pitch.
7. network_autobuild(project_id, template="ci_rooftop", ac_kw or dc_kwp, module, inverter). It sizes breakers and cables for the fault levels set in step 3. Read `blockers` and `assumptions`. For hand changes use network_add_node / network_connect / network_update. If you change the design basis afterwards, run network_autobuild again.
8. validate(project_id). For each error, explain_finding(project_id, rule_id, subject), change the design (network_update, layout_place, site tools), and validate again until there are no errors. Report warnings to the user.
9. earthing_design(project_id) and lps_design(project_id, method, level) design from the stored inputs; validate again.
10. Deliverables: cable_schedule and bom (xlsx). Quote the paths and the assumptions each returns.
11. project_snapshot(project_id, label) once the design is agreed, e.g. "agreed-r0", so a later revision can be diffed against it.
12. Issue last: project_issue(project_id, issued_for="approval", rev="0", drawn, checked, approved, date) once the rules show no errors, then drawings_render(project_id, kinds=["all"]) so every sheet and the drawing register carry the revision, names and date. Quote the set path."""

UTILITY = """Build a utility-scale PV drawing set for "{name}" with UniCAD tools. State intent only; never invent ratings or coordinates.

1. Ask the user for anything missing: AC and DC capacity, inverter or PCS make/size, module make/Wp, MV voltage level, grid voltage at the point of interconnection, site boundary file, setbacks from the design basis, site ambient temperatures, fault levels at each system voltage, soil resistivity, earth fault level and clearing time, lightning protection level. Mark anything you assume.
2. project_create(name, client, segment="utility", site, grid_voltage_v). Read back `assumptions`: they are placeholders.
3. project_design_basis(project_id, ...) before any design: ambient_min_c, ambient_max_c, altitude_m, fault_levels (one per system voltage, with its source), earthing (soil resistivity from the Wenner test, earth fault current and clearing time, and switchyard_m: the MV switchyard earth grid length x width from the substation layout, set now because the layout and the BESS placement size the yard clearance from it) and lps (level from the risk assessment, method). Each value you set replaces its placeholder.
4. site_import(project_id, file_path) for boundary, exclusions, water, roads and transmission lines; fix classification with kind_mapping or site_set_feature. site_set_setbacks (boundary_m, exclusion_m) from the design basis; site_summary for the buildable acres.
5. catalog_search for module, inverter (or pcs) and transformer; design_strings(project_id, module, inverter) for the string length and DC/AC ratio.
6. layout_place(project_id, module, tilt_deg, road_every_rows, target_dc_kwp, block_dc_kwp or n_blocks, modules_per_string, strings_per_inverter). Check layout_summary (block_balance, gcr) and layout_blocks.
7. network_autobuild(project_id, template="utility_string" or "utility_central" (MV export) or "utility_lt" (415 V export into the client's LT panel), module, inverter, inverters_per_block). Layout strings are linked to the network automatically; breakers and cables are sized for the fault levels set in step 3. Read `blockers` and `assumptions`. If you change the design basis afterwards, run network_autobuild again.
8. validate(project_id); explain_finding for each error; fix and re-validate until no errors remain.
9. earthing_design(project_id, ...) (switchyard_m is already in the design basis from step 3; pass it here only to change it, and then place the layout and build the network again) and lps_design(project_id, method, level, ...) design from the stored inputs; validate again.
10. Deliverables: cable_schedule and bom. Quote paths, totals and assumptions.
11. project_snapshot(project_id, label) once the design is agreed, so a later revision can be diffed against it.
12. Issue last: project_issue(project_id, issued_for="approval", rev="0", drawn, checked, approved, date) once the rules show no errors, then drawings_render(project_id, kinds=["all"]) so every sheet and the drawing register carry the revision, names and date. Quote the set path."""

BESS = """Build a battery energy storage drawing set for "{name}" with UniCAD tools. State intent only; never invent ratings or coordinates.

1. Ask the user for anything missing: storage power (kW) and energy (kWh), AC- or DC-coupled, PCS and battery make, grid voltage at the point of interconnection, PV capacity when hybrid, export limit, site file. Mark anything you assume.
2. project_create(name, client, segment="bess" or "hybrid", site, grid_voltage_v). Read back `assumptions`.
3. project_design_basis(project_id, earthing with switchyard_m=[length, width], ...) before the network is built when there is a ground site: a hybrid's BESS units are placed clear of the switchyard earth mat, so its size is needed first. site_import(project_id, file_path) when a site file exists; site_set_setbacks from the design basis.
4. catalog_search(kind="pcs") and catalog_search(kind="battery"), then catalog_get to confirm ratings.
5. network_autobuild(project_id, template="bess_ac" (AC-coupled), "bess_dc" (DC-coupled) or "hybrid", bess_kw, bess_kwh, pcs, battery, export_limit_kw; for hybrid also ac_kw or dc_kwp, module, inverter). Read `blockers` and `assumptions`.
6. validate(project_id); explain_finding for each error; fix with network_update and re-validate until no errors remain.
7. earthing_design and lps_design when a layout exists; validate again.
8. Deliverables: cable_schedule and bom. Quote paths and assumptions.
9. project_snapshot(project_id, label) once the design is agreed.
10. Issue last: project_issue(project_id, issued_for="approval", rev="0", drawn, checked, approved, date) once the rules show no errors, then drawings_render(project_id, kinds=["all"]) so every sheet carries the revision, names and date."""

REVISE = """Revise project "{project_id}" after review comments.

Comments to address:
{comments}

1. project_revisions(project_id) and project_snapshot the issued revision first if it has no label (e.g. "issued-r0"), so the baseline is kept.
2. For each comment decide which part of the model it changes: site (site_set_feature, site_remove_feature, site_set_setbacks), layout (layout_place), network (network_update, network_add_node, network_connect, network_remove, or network_autobuild to rebuild), earthing or lightning (earthing_design, lps_design). Change the model, never the drawing. If a comment needs a value you do not have, ask the user.
3. validate(project_id); explain_finding for each error; repeat until no errors remain.
4. project_diff(project_id, from_revision="<baseline label>") and summarise per comment what changed. Say which comments you did not address and why.
5. Re-run cable_schedule and bom and project_snapshot the revised design (e.g. "agreed-r1").
6. Issue last: project_issue(project_id, issued_for, rev=<next mark, e.g. "1">, drawn, checked, approved, date, remarks=<what changed>), then drawings_render(project_id, kinds=["all"]) so every sheet carries the new revision."""


def register(server: MCPServer, ws: Workspace) -> None:
    @server.prompt()
    def new_rooftop_project(name: str = "Rooftop solar project") -> str:
        """Step plan for a commercial or industrial rooftop plant, from site survey to issued drawing set."""
        return ROOFTOP.format(name=name)

    @server.prompt()
    def new_utility_project(name: str = "Utility-scale solar project") -> str:
        """Step plan for a ground-mount plant with MV collection, from site boundary to issued drawing set."""
        return UTILITY.format(name=name)

    @server.prompt()
    def new_bess_project(name: str = "Battery storage project") -> str:
        """Step plan for a standalone or hybrid battery storage plant."""
        return BESS.format(name=name)

    @server.prompt()
    def revise_after_comments(project_id: str, comments: str) -> str:
        """Step plan for changing an issued project after reviewer or client comments, keeping a diffable baseline."""
        return REVISE.format(project_id=project_id, comments=comments)

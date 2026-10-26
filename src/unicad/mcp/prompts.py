from mcp.server.mcpserver import MCPServer

from unicad.mcp.workspace import Workspace

ROOFTOP = """Build a rooftop PV drawing set for "{name}" with UniCAD tools. State intent only; never invent ratings or coordinates.

1. Ask the user for anything missing: grid voltage, DC or AC capacity, inverter make/size, module make/Wp, roof survey file (KML, KMZ, GeoJSON, DXF), roof setbacks from the design basis, soil resistivity, lightning protection level. Mark anything you assume.
2. project_create(name, client, segment="ci_rooftop", site, grid_voltage_v). Read back `assumptions` and replace placeholders with site data when the user has it.
3. site_import(project_id, file_path). Check `problems` and `imported_by_kind`; add missed obstacles with site_set_feature (kind="obstacle", height_m). site_set_setbacks with distances from the design basis, then site_summary for the buildable roof area.
4. catalog_search for the module and inverter, catalog_get to check ratings, design_strings(project_id, module, inverter) for the string length.
5. layout_place(project_id, module, mounting="rooftop_flush" or "rooftop_tilted", target_dc_kwp, ...). Check layout_summary: DC kWp reached, blocks, row pitch.
6. network_autobuild(project_id, template="ci_rooftop", ac_kw or dc_kwp, module, inverter). Read `blockers` and `assumptions`. For hand changes use network_add_node / network_connect / network_update.
7. validate(project_id). For each error, explain_finding(project_id, rule_id, subject), change the design (network_update, layout_place, site tools), and validate again until there are no errors. Report warnings to the user.
8. earthing_design(project_id, soil_resistivity, fault_ka, clearing_s) and lps_design(project_id, method, level); validate again.
9. Deliverables: drawings_render for the drawing sheets, cable_schedule and bom (xlsx). Quote the paths and the assumptions each returns.
10. project_snapshot(project_id, label) once the set is agreed, e.g. "issued-approval-r0"."""

UTILITY = """Build a utility-scale PV drawing set for "{name}" with UniCAD tools. State intent only; never invent ratings or coordinates.

1. Ask the user for anything missing: AC and DC capacity, inverter or PCS make/size, module make/Wp, MV voltage level, grid voltage at the point of interconnection, site boundary file, setbacks from the design basis, soil resistivity, earth fault level and clearing time, lightning protection level. Mark anything you assume.
2. project_create(name, client, segment="utility", site, grid_voltage_v). Read back `assumptions`.
3. site_import(project_id, file_path) for boundary, exclusions, water, roads and transmission lines; fix classification with kind_mapping or site_set_feature. site_set_setbacks (boundary_m, exclusion_m) from the design basis; site_summary for the buildable acres.
4. catalog_search for module, inverter (or pcs) and transformer; design_strings(project_id, module, inverter) for the string length and DC/AC ratio.
5. layout_place(project_id, module, tilt_deg, road_every_rows, target_dc_kwp, block_dc_kwp or n_blocks, modules_per_string). Check layout_summary (block_balance, gcr) and layout_blocks.
6. network_autobuild(project_id, template="utility_string" or "utility_central", module, inverter, inverters_per_block). Layout strings are linked to the network automatically. Read `blockers` and `assumptions`.
7. validate(project_id); explain_finding for each error; fix and re-validate until no errors remain.
8. earthing_design(project_id, soil_resistivity, fault_ka, clearing_s, ...) and lps_design(project_id, method, level, ...); validate again.
9. Deliverables: drawings_render for the drawing sheets, cable_schedule and bom. Quote paths, totals and assumptions.
10. project_snapshot(project_id, label) once the set is agreed."""

BESS = """Build a battery energy storage drawing set for "{name}" with UniCAD tools. State intent only; never invent ratings or coordinates.

1. Ask the user for anything missing: storage power (kW) and energy (kWh), AC- or DC-coupled, PCS and battery make, grid voltage at the point of interconnection, PV capacity when hybrid, export limit, site file. Mark anything you assume.
2. project_create(name, client, segment="bess" or "hybrid", site, grid_voltage_v). Read back `assumptions`.
3. site_import(project_id, file_path) when a site file exists; site_set_setbacks from the design basis.
4. catalog_search(kind="pcs") and catalog_search(kind="battery"), then catalog_get to confirm ratings.
5. network_autobuild(project_id, template="bess_ac" (AC-coupled), "bess_dc" (DC-coupled) or "hybrid", bess_kw, bess_kwh, pcs, battery, export_limit_kw; for hybrid also ac_kw or dc_kwp, module, inverter). Read `blockers` and `assumptions`.
6. validate(project_id); explain_finding for each error; fix with network_update and re-validate until no errors remain.
7. earthing_design and lps_design when a layout exists; validate again.
8. Deliverables: drawings_render for the drawing sheets, cable_schedule and bom. Quote paths and assumptions.
9. project_snapshot(project_id, label) once the set is agreed."""

REVISE = """Revise project "{project_id}" after review comments.

Comments to address:
{comments}

1. project_revisions(project_id) and project_snapshot the issued revision first if it has no label (e.g. "issued-r0"), so the baseline is kept.
2. For each comment decide which part of the model it changes: site (site_set_feature, site_remove_feature, site_set_setbacks), layout (layout_place), network (network_update, network_add_node, network_connect, network_remove, or network_autobuild to rebuild), earthing or lightning (earthing_design, lps_design). Change the model, never the drawing. If a comment needs a value you do not have, ask the user.
3. validate(project_id); explain_finding for each error; repeat until no errors remain.
4. project_diff(project_id, from_revision="<baseline label>") and summarise per comment what changed. Say which comments you did not address and why.
5. Re-run drawings_render, cable_schedule and bom, then project_snapshot with the next revision label (e.g. "issued-r1")."""


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

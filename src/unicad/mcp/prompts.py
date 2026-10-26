from mcp.server.mcpserver import MCPServer

from unicad.mcp.workspace import Workspace

ROOFTOP = """Build a rooftop PV project model for "{name}" with UniCAD tools. State intent only; never invent ratings.

1. Ask the user for anything missing: grid voltage, DC capacity, inverter make/size, module make/Wp, modules per string, string count, metering and point of interconnection. Mark anything you assume.
2. project_create(name, client, segment="ci_rooftop", site, grid_voltage_v). Read back the `assumptions` and replace placeholders with site data when the user has it.
3. catalog_search for the module and inverter (kind, manufacturer, min_power/max_power), then catalog_get to check the ratings.
4. network_add_group for each repeated inverter section (count = number of identical inverters). Put strings, DC protection and the inverter inside it.
5. network_add_node: pv_string (spec=module id, count=strings per inverter, modules_in_series), dc_isolator / dc_spd as needed, inverter (spec), then acdb, lt_panel, breaker and meter nodes outside the group with count 1, and a poi_grid.
6. network_connect in power-flow direction: string:out -> inverter:dc, inverter:ac -> acdb:in, acdb -> lt_panel -> meter -> poi_grid:in. Add cable (catalog id) and length_m when known.
7. network_summary: unconnected_ports and nodes_without_spec should be empty before you move on. Fix with network_connect / network_update.
8. project_snapshot(label) once the model is agreed."""

UTILITY = """Build a utility-scale PV project model for "{name}" with UniCAD tools. State intent only; never invent ratings.

1. Ask the user for anything missing: AC capacity, DC capacity, inverter or PCS make/size, module make/Wp, modules per string, block layout, MV voltage level, transformer rating, grid voltage at the point of interconnection. Mark anything you assume.
2. project_create(name, client, segment="utility", site, grid_voltage_v). Read back the `assumptions`.
3. catalog_search for module, inverter (or pcs) and transformer (kind, min_power/max_power); confirm with catalog_get.
4. network_add_group for each repeated level, e.g. a block group (count = blocks) holding an inverter group (count = inverters per block).
5. network_add_node: pv_string, scb or dc_fuse_box, inverter, then the block transformer, and outside the groups the RMU / VCB, ht_panel, metering and poi_grid. Use `count` and `group` for repeats; never add the same equipment many times.
6. network_connect in power-flow direction: strings -> scb -> inverter:dc, inverter:ac -> transformer:lv, transformer:hv -> rmu -> vcb -> poi_grid:in. Pass service when the tool cannot infer it.
7. network_summary: check voltage_zones match the intended voltage levels, and that unconnected_ports and nodes_without_spec are empty.
8. project_snapshot(label) once the model is agreed."""


def register(server: MCPServer, ws: Workspace) -> None:
    @server.prompt()
    def new_rooftop_project(name: str = "Rooftop solar project") -> str:
        """Step plan for modelling a commercial or industrial rooftop plant."""
        return ROOFTOP.format(name=name)

    @server.prompt()
    def new_utility_project(name: str = "Utility-scale solar project") -> str:
        """Step plan for modelling a ground-mount plant with MV collection."""
        return UTILITY.format(name=name)

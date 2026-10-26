# UniCAD MCP tools

Generated from the registered tools by `uv run unicad-mcp --write-docs docs/mcp-tools.md`; `tests/test_mcp.py` fails when this file is out of date. Do not edit by hand.

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
| `segment` | `residential` \| `ci_rooftop` \| `carport` \| `utility` \| `bess` \| `hybrid` | yes | Project segment |
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

What changed between two revisions: added, removed and changed nodes, edges, groups and sheets, plus changed project fields.

Example: project_diff(project_id="rooftop-500kwp", from_revision=1).

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `from_revision` | integer or string | yes | Older revision number or snapshot label |
| `to_revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Catalog tools

### `catalog_search`

Find equipment in the catalog; returns compact rows with the key ratings. Use the row `id` as a node `spec` or edge `cable`.

Example: catalog_search(kind="inverter", min_power=90, max_power=110).

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `text` | string | no | Words that must all appear in id, kind, manufacturer or model, e.g. 'growatt 100' |
| `kind` | string | no | One of: module, inverter, battery, pcs, transformer, cable, protection, meter |
| `manufacturer` | string | no | Exact manufacturer name, case-insensitive |
| `min_power` | number | no | Inclusive lower bound: W for modules, kVA for inverter, pcs and transformer |
| `max_power` | number | no | Inclusive upper bound, same units as min_power |
| `limit` | integer | no | Default: `20`. |

### `catalog_get`

Full catalog entry with every rating and its source.

Example: catalog_get(id="growatt-max-100ktl3-x-lv").

Read-only.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | yes | Catalog id from catalog_search |

## Network tools

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
| `kind` | `pv_string` \| `pv_array_block` \| `scb` \| `dc_fuse_box` \| `dc_isolator` \| `dc_spd` \| `inverter` \| `pcs` \| `battery` \| `acdb` \| `lt_panel` \| `breaker` \| `isolator` \| `fuse` \| `ac_spd` \| `ct` \| `pt` \| `meter` \| `relay` \| `transformer` \| `aux_transformer` \| `ht_panel` \| `rmu` \| `vcb` \| `busbar` \| `lightning_arrester` \| `earth_electrode` \| `poi_grid` \| `dg` \| `load` | yes | Equipment kind |
| `spec` | string | no | Catalog id from catalog_search (module, inverter, breaker, ...) |
| `count` | integer | no | Instances per instance of its group, e.g. 10 strings per inverter Default: `1`. |
| `group` | string | no | Id of the group it repeats with |
| `tag` | string | no | Drawing tag with {0:02d}-style placeholders (see network_add_group); default derived from id |
| `voltage_v` | number | no | Nominal voltage in volts of the circuit it sits on, e.g. 415 |
| `modules_in_series` | integer | no | Modules per string; pv_string only |
| `ports` | list of string | no | Override port names; defaults depend on kind (inverter: dc, ac) |
| `attributes` | object | no | Free label/value pairs shown on drawings, e.g. {"rating_a": 250} |
| `title` | string | no |  |
| `symbol` | string | no | Override the drawing symbol key |

### `network_connect`

Connect two nodes. Instances pair up by group: equal counts connect one to one, a count of 1 fans in or out.

Example: network_connect(project_id="p1", src="inv:ac", dst="acdb:in", cable="polycab-...", length_m=25).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `src` | string | yes | Source terminal 'node_id:port'; power flows from src to dst (generation towards grid) |
| `dst` | string | yes | Destination terminal 'node_id:port' |
| `service` | `dc` \| `lv_ac` \| `mv_ac` \| `hv_ac` \| `earth` \| `control` | no | Circuit type; inferred from the ports and nodes when unambiguous, else required |
| `cable` | string | no | Cable catalog id from catalog_search(kind='cable') |
| `length_m` | number | no | Route length in metres, per instance |
| `runs` | integer | no | Parallel cables per connection Default: `1`. |
| `laying` | `air` \| `tray` \| `conduit` \| `trench` \| `buried_direct` \| `buried_duct` \| `overhead` | no |  |
| `derating` | number | no | Installation derating factor, e.g. 0.8 |
| `remarks` | string | no |  |
| `edge_id` | string | no | Id for the connection; default 'e_<src>_<dst>' |

### `network_update`

Change fields of an existing node, connection or group. The id itself cannot change.

Node fields: tag, kind, spec, symbol, count, group, voltage_v, modules_in_series, ports, attributes, title.
Edge fields: src, dst, service, cable, runs, length_m, laying, derating, remarks. Group fields: tag, count, within, title.
Example: network_update(project_id="p1", id="e_inv_acdb", fields={"length_m": 30}).
Changing a group count can invalidate member tags; update those tags in the same session.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id, e.g. 'rooftop-500kwp'. project_list shows existing ids. |
| `id` | string | yes | Id of an existing node, connection (edge) or group |
| `fields` | object | yes | Fields to change; null clears a field. For `attributes` the given keys are merged (null removes a key). |

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
| `families` | list of string | no | Rule families to run; omit for all: structure, pv_strings, ratio, protection, cables, vdrop |
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
| `rule_id` | string | yes | Rule id from validate, e.g. 'cables.ampacity' |
| `subject` | string | yes | One of the finding's subjects from validate: a node or edge id, e.g. 'e_inv_mccb' |
| `revision` | integer or string | no | Revision number or snapshot label. Omit for the latest revision. |

## Resources

- `unicad://catalog/{kind}`: Every catalog entry of one kind as compact rows.
- `unicad://project/{id}`: The complete project model JSON at its latest revision.
- `unicad://docs/tools`: Reference for every tool, resource and prompt this server exposes.

## Prompts

- `new_rooftop_project`: Step plan for modelling a commercial or industrial rooftop plant.
- `new_utility_project`: Step plan for modelling a ground-mount plant with MV collection.

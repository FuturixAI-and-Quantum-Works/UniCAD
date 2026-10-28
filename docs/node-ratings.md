# Node ratings, string MPPT allocation and breaker settings

Typed fields on network nodes (`unicad.domain.network.Node`). Agents set them with `network_add_node` or `network_update(fields={...})`. The rules read them as the table shows. The drafting code (`drafting/sld`, `drafting/string_diagram`) reads them by these field names to label the SLD and group strings by MPPT. Each field is accepted only on the node kinds listed, and any other kind is refused with an error.

| Field | Kinds | Shape | Read by |
|---|---|---|---|
| `busbar` | busbar, acdb, lt_panel, ht_panel, scb | `{rating_a, material: "al" \| "cu", phases: "3P+N", fault_ka, fault_s}` | SLD bus label |
| `ct` | ct | `{primary_a, secondary_a, cores: [{accuracy_class, burden_va, purpose: "metering" \| "protection"}]}`; `ct.ratio` gives "2500/1 A" | SLD CT label |
| `pt` | pt | `{primary_v, secondary_v, phase_to_neutral, cores: [...]}` (line-to-line volts; `phase_to_neutral` prints the ratio over V3); `pt.ratio` gives "0.415 kV/V3 / 110 V/V3" | SLD PT label |
| `relay` | relay | `{functions: ["50", "51", "67", "67N", "27", "59", "50N", "51N"], protocol: "IEC 61850"}`; each function is an ANSI/IEEE C37.2 device number | SLD relay symbol and label |
| `spd` | ac_spd, dc_spd | `{spd_type: "1" \| "2" \| "1+2" \| "3", imax_ka, in_ka, uc_v}` as specified. The catalog spec keeps In; Imax is the brief's figure | SLD SPD label |
| `long_time_setting_a` | breaker | Ir in A, the long-time (overload) setting of an MCCB or ACB trip unit | `protection.rated_current` (Ir >= k x Ib), `cables.protection` (Ir <= Iz), `protection.trip_setting` (range), cable sizing |
| `mppt` | pv_string | One MPPT number (1-based) per instance within its inverter, in instance order | `pv_strings.*` strings per MPPT (`rules.topology.recorded_mppt`), string diagram MPPT grouping |

## Long-time setting ranges

`protection.trip_setting` checks Ir against the range of the breaker's trip unit. The catalog entry can state the range as `ir_min_ratio` and `ir_max_ratio`. Without those fields, the rule uses the series table in `rules/settings.py`:

- Schneider MasterPacT MTZ with MicroLogic X: Ir = 0.4 In to In in 1 A steps. Source: MasterPacT MTZ selection guide 0614CT1701, catalogue page 22.
- Lauritz Knudsen DSine MCCB with a thermal-magnetic release: overload setting 80 % to 100 % In. Source: DSine catalogue page 30. The MTX1.0 microprocessor release allows 40 % to 100 %, but the catalog entries do not record that release.

For any other breaker with a setting, the rule gives a warning to confirm the setting against the datasheet.

## Strings given per inverter and MPPT

`network_autobuild(inverter_strings=[...])` and `design_strings(inverter_strings=[...])` take inverter classes in numbering order. Each class is `{count, mppts}`, where `mppts[k]` lists the modules per string of every string on MPPT k + 1. One pv_string template is made for each string length, with tag `STR<n>-<inverter>-<string>` and its `mppt` field filled. The first class repeats as one group (INV-01..INV-n). Each later inverter is a group of its own. Templates: residential, ci_rooftop, utility_lt.

## Project parties

`Project.developer` is a `Party` (`name`, `code`, `address`), or null when the client develops the plant itself. Set it with `project_create(developer=...)` or `project_parties`.

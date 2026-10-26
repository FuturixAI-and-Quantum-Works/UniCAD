# Engineering rules

Generated from the rule registry by `uv run python -m unicad.rules.docs > docs/rules.md`. Do not edit.

Every rule is a pure function of the project and the catalog that yields findings (`error`, `warning`, `info`).
A sheet cannot be issued for approval, construction or as-built while any `error` finding exists;
proposal and information issues are allowed with a NOT FOR CONSTRUCTION watermark.
Clause references name the standard only; clause numbers are added once verified against the standard text.

## Thresholds

| Name | Value | Basis |
|---|---|---|
| `isc_factor` | 1.25 | Worst-case PV current taken as 1.25 x Isc(STC), following IEC 62548 practice; the factor is not verified against the standard text. |
| `continuous_factor` | 1.25 | Protective devices on inverter outputs are sized for 1.25 x maximum inverter current (continuous-duty practice; not taken from a cited standard). |
| `dc_ac_band` | 0.1 | DC/AC ratio is accepted within +/-10 % of the design-basis target (illustrative band, not a cited limit). |
| `voltage_tolerance` | 0.05 | Equipment nominal voltages within 5 % of the zone voltage are treated as compatible (illustrative tolerance; confirm the equipment's operating range). |
| `stc_irradiance` | 1000 W/m2 | Maximum cell temperature is evaluated at 1000 W/m2 plane-of-array irradiance. |
| `stc_temp` | 25 degC | Module datasheet values are at STC cell temperature 25 degC. |

## structure

### `structure.unconnected_port`: Every power port is connected

Standard: none (design practice). Segments: all.

A power port with no cable is either missing a connection or belongs to equipment that should not be there. Allowed while drafting; blocks issue for approval.

### `structure.path_to_poi`: Every source reaches the point of interconnection

Standard: none (design practice). Segments: all.

Each generating source (PV string, array block, battery, DG) must have a downstream power path to a poi_grid node.

### `structure.inverter_ac_voltage`: Inverter AC voltage matches its AC zone

Standard: none (design practice). Segments: all.

The rated AC voltage of each inverter or PCS is compared with the declared voltage of the zone its AC port connects to. A difference above the tolerance is an error; any smaller non-zero difference is reported for confirmation.

### `structure.transformer_voltage`: Transformer windings match their zones

Standard: none (design practice). Segments: all.

Each transformer's HV winding is compared with the zone on its 'hv' port and its LV winding (the nearest, for multi-winding units) with the zone on its 'lv' port.

### `structure.missing_spec`: Rated equipment names a catalog spec

Standard: none (design practice). Segments: all.

Strings, inverters, PCS, batteries and transformers need a catalog spec (and strings a module count) for the engineering rules to run: missing ones are errors. Protection devices and meters without a spec cannot be rating-checked: warnings.


## pv_strings

### `pv_strings.voc_max`: String Voc at minimum temperature within DC limits

Standard: IEC 62548. Segments: all.

N x Voc at the coldest cell temperature must not exceed the lower of the inverter's maximum DC input voltage and the module's maximum system voltage. Exceeding it can destroy the inverter input stage.

### `pv_strings.hot_cell`: Maximum cell temperature can be computed

Standard: none (design practice). Segments: all.

The NOCT thermal model needs a NOCT value from the design basis or the module spec. Without it the hot-weather checks are skipped and this warning is raised.

### `pv_strings.vmp_min`: String Vmp at maximum temperature above MPPT minimum

Standard: IEC 62548. Segments: all.

N x Vmp at the hottest cell temperature (thermal model at ambient_max_c, 1000 W/m2) should stay at or above the inverter's minimum MPPT voltage, or the inverter leaves MPP tracking on hot days and energy is lost.

### `pv_strings.start_voltage`: String Voc at maximum temperature above inverter start voltage

Standard: none (design practice). Segments: all.

An inverter starts when its open-circuit DC voltage exceeds its start voltage. N x Voc at the hottest cell temperature below the start voltage means the inverter may not start: error. Not checked when the catalog has no start voltage.

### `pv_strings.vmp_max`: String Vmp at minimum temperature below MPPT maximum

Standard: IEC 62548. Segments: all.

N x Vmp at the coldest cell temperature should not exceed the inverter's maximum MPPT voltage, or the inverter clips at the top of its window on cold bright days.

### `pv_strings.inputs`: Inverter has enough DC inputs

Standard: none (design practice). Segments: all.

Parallel DC inputs into an inverter (strings landing directly, or SCB outputs) must fit mppt_count x inputs_per_mppt. Inverters whose catalog entry gives only a total (central inverters) are checked against dc_input_count.

### `pv_strings.isc_per_mppt`: Short-circuit current per MPPT within inverter rating

Standard: IEC 62548. Segments: all.

Strings per MPPT x 1.25 x module Isc must not exceed the inverter's maximum short-circuit current per MPPT. When the catalog gives only the whole-inverter value (central inverters), all strings into the inverter x 1.25 x Isc are checked against max_dc_short_circuit_current. Not checked when the catalog has neither.

### `pv_strings.operating_current`: Operating current per MPPT within inverter input rating

Standard: none (design practice). Segments: all.

Strings per MPPT x module Imp above the inverter's maximum input current per MPPT means the inverter current-limits (clips) at high irradiance: warning, not a safety limit. When the catalog gives only the whole-inverter value, all strings into the inverter x Imp are checked against max_dc_input_current.

### `pv_strings.string_fuses`: Parallel strings have overcurrent protection where reverse current can exceed the module rating

Standard: IEC 62548. Segments: all.

With Np strings in parallel, a faulted string can receive (Np - 1) x 1.25 x Isc from the others. When Np > 2 and that exceeds the module's maximum series fuse rating, each string needs a fuse: error if the path from string to inverter has no fuse or fuse box. An SCB on the path is taken to hold string fuses (info, stated as an assumption).


## ratio

### `ratio.dc_ac_inverter`: DC/AC ratio per inverter within the design band

Standard: none (design practice). Segments: all.

DC kWp feeding one inverter (strings x modules in series x Pmax) over its rated AC kVA, compared with the design basis dc_ac_ratio_target. Outside the band is a warning.

### `ratio.dc_ac_plant`: Plant DC/AC ratio within the design band

Standard: none (design practice). Segments: all.

Total DC kWp over total rated inverter AC kVA, compared with the design basis dc_ac_ratio_target. Outside the band is a warning.

### `ratio.export_limit`: Plant AC capacity within the grid connection limit

Standard: none (design practice). Segments: all.

When a poi_grid node carries an `export_limit_kva` attribute (sanctioned or contracted export capacity), total rated inverter AC kVA above it is a warning: the plant needs export limiting or a smaller AC capacity.


## protection

### `protection.rated_current`: Device rated current covers the design current

Standard: IS 732. Segments: all.

A breaker, fuse or switch on an export path must be rated for the current it carries: on the AC side In >= 1.25 x the maximum current of the inverters behind it, on the DC side In >= 1.25 x Isc of the strings behind it. String fuses (gPV) are checked by protection.string_fuse_rating. The upper bound In <= cable ampacity belongs to cable sizing, which reuses the same design current.

### `protection.breaking_capacity`: Interrupting devices state a breaking capacity

Standard: none (design practice). Segments: all.

Breakers and fuses must carry a breaking capacity (kA) so it can be shown on the SLD and compared with the fault level. A spec without one is an error.

### `protection.rated_voltage`: Device rated voltage covers the system voltage

Standard: none (design practice). Segments: all.

DC devices must be rated for the highest string Voc at the coldest cell temperature in their zone; AC devices for the declared zone voltage (line-to-line for 3- and 4-pole devices). AC surge protective devices are not checked: their rated voltage is a continuous operating voltage with a different basis.

### `protection.spd_dc`: Surge protection on the inverter DC side

Standard: IEC 62548. Segments: all.

Each inverter or PCS DC zone should contain a DC surge protective device. Warning when none is modelled: an SPD built into the inverter or SCB should still be modelled so the SLD shows it.

### `protection.spd_ac`: Surge protection on the inverter AC side

Standard: none (design practice). Segments: all.

Each inverter or PCS AC zone should contain an AC SPD or lightning arrester. Warning when none is modelled.

### `protection.earthing`: Inverters and panels are earthed

Standard: IS 3043. Segments: all.

Every inverter and PCS needs an earth connection (an edge on its 'pe' port): error when missing. Distribution panels and SCBs without one are warnings, since their earthing may be detailed on the earthing drawing instead of the SLD.

### `protection.string_fuse_rating`: String fuse rating between string current and module limit

Standard: IEC 62548. Segments: all.

A gPV fuse carrying n strings must be rated at least n x 1.25 x Isc, and a fuse protecting a single string must not exceed the module's maximum series fuse rating.


## cables

### `cables.missing`: Power connections have a cable and a length

Standard: none (design practice). Segments: all.

Every power edge needs a cable (catalog id or inline spec) and a route length so that ampacity and voltage drop can be checked. Taps to SPDs, lightning arresters and PTs are exempt, and so are links with no laying between switchgear, CTs, meters and a panel or busbar: they are taken as busbar connections inside the panel. Panel-to-panel links, and any link given a laying, need a cable.

### `cables.ampacity`: Cable current-carrying capacity covers the design current

Standard: IS 732. Segments: all.

Design current Ib (DC: 1.25 x Isc of the strings carried; AC: maximum inverter current at the zone voltage) must not exceed Iz = catalog ampacity x derating x runs.

The ampacity column follows the laying: air, tray, trench and overhead use the in-air rating, buried_direct the in-ground rating, buried_duct and conduit the in-duct rating. Single-core cables use the two-cable rating for DC and single-phase circuits and the three-cable rating for three-phase. The derating is the edge's `derating`; when absent 1.0 is used and an info finding says so. A cable without a rating for its laying, or an edge without a laying, is a warning.

### `cables.protection`: Overcurrent device rating does not exceed the cable ampacity

Standard: IS 732 / IEC 60364-4-43. Segments: all.

Overload coordination Ib <= In <= Iz: the rated current In of a breaker or fuse must not exceed the derated ampacity Iz of each cable connected directly to it, so the device trips before the cable overheats. Ib <= In is protection.rated_current. MV breakers are not checked: their trip settings live in relays the network does not model.


## vdrop

### `vdrop.dc`: DC voltage drop from string to inverter within the design basis

Standard: none (design practice). Segments: all.

Per DC cable: dV% = 100 x 2 x L x (I / runs) x R / 1000 / V with I = n x Imp of the strings carried and V = N x Vmp of the string, R at 90 degC (info per segment). Along the path from each string template to its inverter the segment drops are summed and compared with design_basis.vdrop_dc_max: error when over.

### `vdrop.ac`: AC voltage drop from inverter to the point of interconnection within the design basis

Standard: none (design practice). Segments: all.

Per AC cable: dV% = 100 x k x L x (Ib / runs) x (R cos phi + X sin phi) / 1000 / V with k = sqrt(3) for three-phase and 2 for single-phase, Ib the maximum inverter current, V the zone voltage, R at 90 degC and cos phi the design-basis power factor (info per segment). Along the path from each inverter or PCS template to the POI the segment drops of every AC voltage level are summed and compared with design_basis.vdrop_ac_max: error when over.


## earthing

### `earthing.conductor_size`: Earth conductor carries the earth fault

Standard: IS 3043. Segments: all.

The adiabatic minimum cross-section A = I sqrt(t) / k (k = 80 for galvanised steel, IS 3043) for the design earth fault current and
clearing time, raised by any corrosion allowance, is compared with the GS flat chosen for each conductor role. The IEEE 80 Kf value
for zinc-coated steel is reported as a cross-check. Without fault data the size is reported as unverified.

### `earthing.electrode_resistance`: Earth electrode system meets the target resistance

Standard: IS 3043. Segments: all.

Each pit's resistance R = 100 rho / (2 pi L) ln(4 L / d) (IS 3043, L and d in cm); pits two electrode lengths apart combine in
parallel, and in parallel with the buried conductor R = 100 rho / (2 pi L) ln(2 L^2 / (w t)). The total is compared with the
target resistance, and the pit count with the count the IS 3043 surface current density needs for the fault current.

### `earthing.touch_step`: Switchyard grid keeps touch and step voltage tolerable

Standard: IEEE 80. Segments: all.

For a project with an MV switchyard and fault data, the IEEE 80 mesh voltage Em = rho Km Ki IG / LM and step voltage
Es = rho Ks Ki IG / LS of the switchyard grid are compared with the tolerable touch and step voltages for a 50 kg body,
(1000 + 1.5 Cs rho_s) 0.116 / sqrt(ts) and (1000 + 6 Cs rho_s) 0.116 / sqrt(ts). A ground potential rise below the tolerable
touch voltage passes without the mesh check.

### `earthing.every_equipment_bonded`: Every table and equipment item is bonded to the earth network

Standard: none (design practice). Segments: all.

Every table (MMS) must be crossed by a row conductor, and every inverter, SCB, transformer, panel, switchgear item, arrester and
lightning terminal must end the number of conductors it needs (two distinct connections for equipment) on a conductor network
that reaches an earth pit. Projects without an earthing basis are not checked. Network equipment without an earth (pe) connection in the electrical model is reported as a warning.


## lps

### `lps.coverage`: Lightning terminals cover every table, station and roof

Standard: IS/IEC 62305; NF C 17-102 for ESE. Segments: all.

Each table centroid and corner, inverter station and (on rooftop projects) roof corner must lie within the protection radius of
a terminal: the ESE radius Rp of NF C 17-102 (a French standard) from the datasheet value or dT and mast height, or for
conventional air terminals the rolling-sphere radius of the protection level (IS/IEC 62305). Terminals are placed by a greedy
set cover over free positions; points left outside every zone are errors. Projects without a lightning basis are not checked.

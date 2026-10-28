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
| `k_xlpe_cu` | 143 A s^0.5/mm2 | Adiabatic constant k of a copper conductor with XLPE insulation (90 degC to 250 degC), 0.143 kA s^0.5/mm2 in the KEI Technical Handbook, short circuit rating table; the Polycab XLPE catalogue gives the same 1 s ratings (143.1 kA for 1000 mm2). Manufacturer value, standard name IS 7098. |
| `k_xlpe_al` | 94 A s^0.5/mm2 | Adiabatic constant k of an aluminium conductor with XLPE insulation (90 degC to 250 degC), 0.094 kA s^0.5/mm2 in the KEI Technical Handbook, short circuit rating table (Polycab's 1 s table gives 94.5 kA for 1000 mm2; the lower value is used). Manufacturer value, standard name IS 7098. |
| `fault_clearing_default` | 1 s | Fault clearing time taken as 1 s, the duration cable and switchgear short-circuit ratings are tabulated for, when neither the fault level nor the earthing basis gives a clearing time. |
| `string_fuse_min` | 1.5 | String fuse rating In > 1.5 x Isc(STC) of the module (IEC 62548:2016 6.5.5.1 a) as quoted in ieccalc.com 'What current a PV DC circuit must be rated for' (2026-09-18), and IEC 60364-7-712:2017 as quoted in M. Mitolo, 'Protection Against Fault Currents in Photovoltaic Arrays', Distributed Generation & Alternative Energy Journal (2025): 1.5 x ISC_MOD < In < 2.4 x ISC_MOD and In <= IMOD_MAX_OCPR for a fuse protecting one string; the standards' own text was not available.). |
| `string_fuse_max` | 2.4 | String fuse rating In < 2.4 x Isc(STC) of the module (IEC 62548:2016 6.5.5.1 a) as quoted in ieccalc.com 'What current a PV DC circuit must be rated for' (2026-09-18), and IEC 60364-7-712:2017 as quoted in M. Mitolo, 'Protection Against Fault Currents in Photovoltaic Arrays', Distributed Generation & Alternative Energy Journal (2025): 1.5 x ISC_MOD < In < 2.4 x ISC_MOD and In <= IMOD_MAX_OCPR for a fuse protecting one string; the standards' own text was not available.). |

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

Strings, inverters, PCS, batteries and transformers need a catalog spec (and strings a module count) for the engineering rules to run, and breakers, vacuum breakers and fuses one so their current and breaking capacity can be checked against the design current and the prospective fault: missing ones are errors. Isolators, SPDs and meters without a spec cannot be rating-checked: warnings.

### `structure.bess_separation`: BESS units stand outside the switchyard with a fire separation

Standard: none (design practice). Segments: all.

Battery containers, PCS and BESS transformers placed on the layout must stand outside the MV switchyard earth mat and at least BESS_SEPARATION_M from
its edge (see the assumption for the source). Checked when the switchyard size is known (switchyard_m or a marked area): error when closer, with the
re-run that moves them (network_autobuild places BESS units clear of the switchyard).


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

### `ratio.bess_intent`: BESS power and energy as asked

Standard: none (design practice). Segments: all.

When PCS nodes carry the requested storage size (bess_kw_requested / bess_kwh_requested, set by network_autobuild), the built PCS kVA and battery kWh are
compared with it, because whole catalog units rarely add up to the request exactly: a difference above 1 % in either is a warning that states it, a smaller one an info finding.

### `ratio.export_limit`: Plant AC capacity within the grid connection limit

Standard: none (design practice). Segments: all.

When a poi_grid node carries an `export_limit_kva` attribute (sanctioned or contracted export capacity), the AC capacity that can export through it, rated
PV inverter kVA plus BESS PCS kVA (a discharging battery exports like the PV), is compared with it. Above it is a warning: the plant needs export control or a
smaller AC capacity. With `export_control` = "ppc" on the POI (a declared power plant controller limiting the export), the excess is an info finding that states
the controller assumption. Within the limit is an info finding giving the sum.


## protection

### `protection.rated_current`: Device rated current covers the design current

Standard: IS 732. Segments: all.

A breaker, fuse or switch on an export path must be rated for the current it carries: on the AC side In >= 1.25 x the maximum current of the inverters behind it, on the DC side In >= 1.25 x Isc of the strings behind it. A DC isolator node standing for several separate string circuits (isolator_ways) is checked per device: each switches poles / 2 circuits and carries their share. String fuses (gPV) are checked by protection.string_fuse_rating. The upper bound In <= cable ampacity belongs to cable sizing, which reuses the same design current.

### `protection.breaking_capacity`: Interrupting devices state a breaking capacity

Standard: none (design practice). Segments: all.

Breakers and fuses must carry a breaking capacity (kA) so it can be shown on the SLD and compared with the fault level. A spec without one is an error.

### `protection.fault_level`: Interrupting devices break the prospective fault current

Standard: none (design practice). Segments: all.

A breaker or fuse must have a breaking capacity (Icu; for vacuum breakers also the short-time withstand current Icw) at or above the prospective three-phase fault current at
its zone. That is the larger of the level declared in design_basis.fault_levels for the zone's system voltage and, where a transformer LV winding feeds the zone, the current the
transformer passes: I = S / (sqrt(3) V (Z% / 100 + S / S_sc)), with S the kVA base of the winding's impedance, Z% its catalog (or inline) impedance and S_sc = sqrt(3) V_hv I_hv
the declared fault level on the HV side (an infinite bus when none is declared, which is conservative). Inverter and PCS contributions are not added. An error when the breaking
capacity is below. Where neither applies, a single info finding lists the voltages and the devices are not checked; autobuild then picks the lowest breaking capacity at or above a
placeholder minimum and reports that as an assumption. A breaker without a catalog spec is an error of structure.missing_spec.

Worked examples: a 20 kA VD4 vacuum breaker on a 33 kV bus with a 25 kA fault level is an error (20 kA < 25 kA); the 25 kA VD4 passes. A 7040 kVA, 7 % IDT winding at 800 V
on an infinite bus passes 7040 / (sqrt(3) x 0.8 x 0.07) = 72.6 kA to its LV bus, so a 12 kA MCCB there is an error.

### `protection.oversize`: LT breakers are not rated above what the design current needs without a stated reason

Standard: none (design practice). Segments: all.

An LT breaker (MCB, MCCB, ACB) on an export path rated above the smallest catalog breaker that covers k x Ib at its voltage and poles is a warning, because the
cable it protects must then carry In, not Ib (cables.protection). The finding says why the larger device is there: no smaller catalog breaker breaks the prospective
fault at its bus (protection.fault_level), or the smaller frames' terminals do not take the cable it lands (cables.terminals), or neither (a smaller one fits).

### `protection.rated_voltage`: Device rated voltage covers the system voltage

Standard: none (design practice). Segments: all.

DC devices must be rated for the highest string Voc at the coldest cell temperature in their zone; AC devices for the declared zone voltage (line-to-line for 3- and 4-pole devices). AC surge protective devices are not checked: their rated voltage is a continuous operating voltage with a different basis.

### `protection.spd_dc`: Surge protection on the inverter DC side

Standard: IEC 62548. Segments: all.

Each inverter or PCS DC zone should contain a DC surge protective device. Warning when none is modelled: an SPD built into the inverter or SCB should still be modelled so the SLD shows it.

### `protection.spd_ac`: Surge protection on the inverter AC side

Standard: none (design practice). Segments: all.

Each inverter or PCS AC zone should contain an AC SPD or lightning arrester. Warning when none is modelled.

### `protection.spd_ac_poles`: AC SPD has a pole per phase conductor

Standard: none (design practice). Segments: all.

An AC surge protective device on a three-phase circuit must protect every phase: a three- or four-pole SPD (3+0, 3+1, 4+0). A single- or two-pole
catalog SPD there protects one phase only: error. Circuits fed only by single-phase inverters count as single-phase.

### `protection.earthing`: Inverters and panels are earthed

Standard: IS 3043. Segments: all.

Every inverter and PCS needs an earth connection (an edge on its 'pe' port): error when missing. Distribution panels and SCBs without one are warnings, since their earthing may be detailed on the earthing drawing instead of the SLD.

### `protection.string_fuse_rating`: String fuse rating between string current and module limit

Standard: IEC 62548. Segments: all.

A gPV fuse protecting one string must be rated 1.5 x Isc < In < 2.4 x Isc and In <= the module's maximum series fuse rating (IMOD_MAX_OCPR); a fuse protecting a
group of N strings 1.5 x N x Isc < In < IMOD_MAX_OCPR - (N - 1) x Isc. Isc is the module short-circuit current at STC. The factors are the string_fuse_min and
string_fuse_max thresholds, whose text names the sources the clause was read from.

### `protection.mv_cable`: Every MV cable is protected at the end the fault current comes from

Standard: none (design practice). Segments: all.

An MV cable must have a circuit breaker, RMU breaker or fuse (switch-fuse) at its grid end, so a cable fault is cleared from the side that feeds it:
the bus end of a block feeder, the transformer-tap end of an auxiliary transformer feeder. A breaker at the far end only (the block VCB of a feeder that
tees straight onto the main bus) leaves the cable unprotected: error.

### `protection.lv_both_ends`: LT export cables are protected at the grid end

Standard: IS 732 / IEC 60364-4-43. Segments: all.

An LT AC cable between the generators and the grid is fed from both ends. It needs an MCB, MCCB, ACB or fuse at its grid end (downstream, through
in-line CTs, meters and isolators), or a transformer there whose HV breaker clears the fault. A cable whose only overcurrent device is at the generator end
(an ACDB outgoing breaker) while its grid end lands on a panel or busbar is an error: add the solar incomer breaker at the panel. A cable that ends at the
supply point itself (the DISCOM service connection) is protected by the supply's own device, by others, and is not checked.

Worked example: ACDB ACB -> cable -> CT -> isolator -> cable -> consumer LT panel. Both cables reach the panel through the CT and isolator only, so both are
flagged until an incomer breaker is placed at the LT panel end.

### `protection.transformer_feed`: Every transformer tap is switched and protected

Standard: none (design practice). Segments: all.

A transformer fed from another transformer's terminals or from a bus (an auxiliary transformer tapped off a block transformer's HV side, a plant
auxiliary transformer on the main bus) must be fed through a breaker, RMU breaker or switch-fuse on its supply side: error when the supply reaches it
through cables and panel wiring alone. Block transformers on an export path are protected by their block VCB or RMU (protection.mv_cable).


## cables

### `cables.missing`: Power connections have a cable and a length

Standard: none (design practice). Segments: all.

Every power edge needs a cable (catalog id or inline spec) and a route length so that ampacity and voltage drop can be checked. Taps to SPDs, lightning arresters and PTs are exempt, and so are links with no laying between switchgear, CTs, meters and a panel or busbar: they are taken as busbar connections inside the panel. Panel-to-panel links, and any link given a laying, need a cable.

### `cables.ampacity`: Cable current-carrying capacity covers the design current

Standard: IS 732. Segments: all.

Design current Ib (DC: 1.25 x Isc of the strings carried; AC: maximum inverter current at the zone voltage) must not exceed Iz = catalog ampacity x derating x runs.

The ampacity column follows the laying: air, tray, trench and overhead use the in-air rating, buried_direct the in-ground rating, buried_duct and conduit the in-duct rating. Single-core cables use the two-cable rating for DC and single-phase circuits and the three-cable rating for three-phase. The derating is the edge's `derating`; when absent, parallel runs (runs > 1) of AC cables take the group rating factor of the KEI Technical Handbook for the laying and the number of circuits (next tabulated count up), and a single run, or a laying or count the handbook does not tabulate, uses 1.0; an info finding says which. A cable without a rating for its laying, or an edge without a laying, is a warning.

### `cables.protection`: Overcurrent device rating does not exceed the cable ampacity

Standard: IS 732 / IEC 60364-4-43. Segments: all.

Overload coordination Ib <= In <= Iz: the rated current In of a breaker or fuse must not exceed the derated ampacity Iz of each cable it protects, so the device trips before the cable overheats.
A device protects the cables connected to it and those reached from it through in-line CTs, meters and isolators (which do not interrupt overload current), up to the next panel, switching device
or equipment. Ib <= In is protection.rated_current. MV breakers are not checked: their trip settings live in relays the network does not model.

Worked example: an ACB of In 1250 A feeding a CT, an isolator and then a cable of Iz 980 A to the LT panel is an error (1250 A > 980 A), although the cable does not touch the ACB.

### `cables.terminals`: Cables fit the terminals of the switchgear they land on

Standard: none (design practice). Segments: all.

A cable landing on a breaker or switch must fit its terminals: conductor size per pole at most the device's largest terminal cable and parallel runs at most
its cables per pole, as the catalog gives them from the device datasheet (or as a labelled assumption in the catalog source where the datasheet is silent).
Every length band of a template edge is checked too. An error when a cable does not fit.

### `cables.battery_dc`: Battery DC cables carry the PCS current at the battery's lowest voltage

Standard: none (design practice). Segments: all.

A battery-to-PCS (or battery-to-DC-DC converter) cable must carry the battery's maximum continuous current when the catalog gives it, else the converter's rated
power at the battery's minimum voltage, shared by the batteries in parallel on that converter: Ib = P / V_min / n. Iz below Ib is an error. Where no DC breaker or fuse
is modelled on the cable, the finding states that the container's integrated DC switchgear is relied on; a modelled device must have In >= Ib (protection.rated_current).

### `cables.short_circuit`: Cable conductor withstands the prospective fault

Standard: IS 732 / IEC 60364-4-43. Segments: all.

The conductor of every AC power cable must carry the prospective fault current for the clearing time without overheating (adiabatic heating): A >= I sqrt(t) / k, where A is the conductor
area (area x parallel runs), I the three-phase fault current and t its clearing time, and k the conductor and insulation constant.

I is the level declared for the cable's system voltage (design_basis.fault_levels) or, in an LT zone (up to 1000 V) fed by a transformer, the transformer-fed current of
protection.fault_level when that is larger. t depends on the voltage level: an LT cable protected by an MCB, MCCB or ACB (directly, or through in-line CTs, meters and
isolators as for cables.protection) is cleared by the breaker's instantaneous trip, taken as 0.1 s (a labelled assumption: the shortest duration IEC 60364-4-43 applies the
adiabatic formula to); an LT cable a transformer feeds with no LT breaker on it is cleared by the VCB on that transformer's HV side, in the feeder relay's instantaneous
stage plus the VCB's catalogued total breaking time (0.023 + 0.020 + 0.060 = 0.103 s for an ABB VD4 behind an ABB REF615, labelled as an assumption on the relay settings);
every other cable uses the clearing time of its level, else design_basis.earthing.fault_clearing_s, else 1 s, and the finding says which.
k is 143 A s^0.5/mm2 for XLPE copper and 94 for XLPE aluminium (90 degC to 250 degC, from the KEI Technical Handbook short circuit table; the Polycab catalogue agrees). The fault is taken at the
cable's source end, which is conservative (cable impedance only lowers it), and only the conductor is checked, not the armour or screen. Other insulations have no cited k and are reported as not
checked. Where no fault current is known for a cable's system voltage a single info finding lists the voltages; DC string cables are not checked because a string cannot deliver more than about 1.25 Isc.

Worked examples: a 33 kV feeder, fault level 25 kA cleared in 1 s, aluminium XLPE: I sqrt(t) / k = 25000 x 1 / 94 = 266 mm2, so one run of 240 sq.mm is an error
(it withstands 22.6 kA) and one run of 300 sq.mm passes (28.2 kA). A 415 V cable behind an ACB with a 25 kA level: 25000 x sqrt(0.1) / 94 = 84.1 mm2, so one run of 95 sq.mm passes.


## vdrop

### `vdrop.dc`: DC voltage drop from string to inverter within the design basis

Standard: none (design practice). Segments: all.

Per DC cable: dV% = 100 x 2 x L x (I / runs) x R / 1000 / V with I = n x Imp of the strings carried and V = N x Vmp of the string, R at 90 degC (info per segment). Along the path from each string template to its inverter the segment drops are summed and compared with design_basis.vdrop_dc_max: error when over.

### `vdrop.ac`: AC voltage drop from inverter to the point of interconnection within the design basis

Standard: none (design practice). Segments: all.

Per AC cable: dV% = 100 x k x L x (Ib / runs) x (R cos phi + X sin phi) / 1000 / V with k = sqrt(3) for three-phase and 2 for single-phase, Ib the maximum inverter current, V the zone voltage, R at 90 degC and cos phi the design-basis power factor (info per segment). Along the path from each inverter or PCS template to the POI the segment drops of every AC voltage level are summed and compared with design_basis.vdrop_ac_max: error when over.

### `vdrop.bands`: Length-band cables keep within the worst-case cable's drop and carry the current

Standard: none (design practice). Segments: all.

A template edge may give its shorter instances smaller cables (Edge.bands): each band's cable, at the band's longest route (upto_m), must have a
voltage drop no larger than the edge's own cable at the worst-case length (so every instance stays inside the path limit vdrop.dc / vdrop.ac check
on the worst case), and an ampacity no lower than the design current and the rating of every device protecting the edge. Error otherwise.


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
touch voltage passes without the mesh check. The grid current is the earth fault current times the split factor
Sf = R_rest / (Rg + R_rest) between the grid and the rest of the plant earth; GPR, mesh and step voltage all use it. Without a
switchyard size (switchyard_m or a marked area) the check is not done and a warning says so.

### `earthing.every_equipment_bonded`: Every table and equipment item is bonded to the earth network

Standard: none (design practice). Segments: all.

Every table (MMS) must be crossed by a row conductor, and every inverter, SCB, transformer, panel, switchgear item, arrester and
lightning terminal must end the number of conductors it needs (two distinct connections for equipment) on a conductor network
that reaches an earth pit. Projects without an earthing basis are not checked. Network equipment without an earth (pe) connection in the electrical model is reported as a warning.

### `earthing.mat_fence_clearance`: Switchyard earth mat keeps its clearance from the boundary fence

Standard: none (design practice). Segments: all.

The switchyard earth grid stands at least 3 m (an assumed clearance for the perimeter road and fence earthing, not a cited figure)
inside the boundary line, which is the site fence; a grid that touches or crowds the fence puts the touch voltage outside the mat on
the public side. Closer than that is an error (a warning for a grid inside a switchyard area marked on the site, which is the client's
choice); between 3 m and the site's boundary setback, which layout_place keeps, is a warning. Checked for a ground plant whose
switchyard grid is sized. Fix: run layout_place again (it keeps the yard clear of the fence), or pass switchyard_x/y for a spot
inside the buildable area.


## lps

### `lps.coverage`: Lightning terminals cover every table, station, switchyard, BESS unit and building

Standard: IS/IEC 62305; NF C 17-102 for ESE. Segments: all.

Each table centroid and corner, inverter station, MV switchyard, BESS unit, string inverter and building (roof corners on rooftop projects)
must lie within the protection radius of a terminal: the ESE radius Rp of NF C 17-102 (a French standard) from the datasheet value or dT and
mast height, or for conventional air terminals the rolling-sphere radius of the protection level (IS/IEC 62305). The switchyard and each piece
of placed equipment are covered over an assumed footprint (the catalog has no dimensions); the finding lists covered over total per class.
Terminals are placed by a greedy set cover over free positions clear of the tables, stations, switchyard and BESS units; points left outside
every zone are errors. Projects without a lightning basis are not checked.

### `lps.mast_fence_clearance`: Lightning terminals keep their clearance from the boundary fence

Standard: none (design practice). Segments: all.

Every lightning terminal stands at least 3 m (an assumed clearance for the perimeter road and the mast foundation, not a cited
figure) inside the boundary line, which is the site fence. Terminals are placed by the lightning design inside that margin, so a finding
means the design had no free position; projects without a lightning basis or a ground boundary are not checked.

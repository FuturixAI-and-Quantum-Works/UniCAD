# UniCAD: plan

UniCAD is an MCP server that lets AI agents produce solar EPC drawing sets as DXF, PDF, and DWG files that open in LibreCAD. It covers SLDs, string and wiring diagrams, array layouts, cable schedules, earthing and LPS, and title-blocked sheet sets. It targets Indian practice (IS/CEA) for residential, C&I rooftop and carport, utility ground-mount, and BESS/hybrid projects. It is a product for many EPCs.

## 1. Decisions so far

| Topic | Decision | Source |
| --- | --- | --- |
| LibreCAD coupling | Generate files, then use LibreCAD's headless `dxf2pdf` / `dxf2png` / `dxf2svg` to render. No live plugin in v1. | Research + scale spike |
| Scale | Proven fine. 180k modules or 7,920 strings render in 0.4 to 5 s. | `spikes/scale/README.md` (measured) |
| Engineering | Draft and validate. A typed project model is checked against rules before anything renders. | User |
| Region | India: IS/CEA conventions, DISCOM submission formats | User |
| Segments | Residential, C&I rooftop/carport, utility, BESS/hybrid | User |
| Inputs | Plain text, datasheet PDFs, site DXF/DWG/KML, PVsyst/Helioscope exports | User |
| Outputs | DXF + PDF + DWG | User |
| Runtime | Python, local stdio first. Add a Streamable HTTP transport for the in-house agent app. | User |
| Clients | Claude Desktop/Code, Cursor/OpenCode, own agent app | User |
| Tenancy | Multi-EPC. Each company has its own title block, symbols, layer standard, numbering, and rule thresholds. | User |
| First proof | C&I rooftop SLD, utility SLD with MV yard, and utility site layout, all end to end | User |

### Why not a live LibreCAD plugin

LibreCAD has no scripting API. Its plugin interface (`document_interface.h`) is a small C++ API that can only modify the open drawing. A live bridge exists only as an out-of-tree fork (bivex, see LibreCAD issue #2577). That would mean every customer runs a custom LibreCAD build, and every LibreCAD release could break us. The only reason to accept that cost is live editing of an open drawing. The architecture below keeps that door open: rendering sits behind one interface, so a `LiveLibreCADBackend` can be added later without touching the domain.

Prior art: `aiblueprint-mcp` (ezdxf + LibreCAD preview, MIT) and `autocad-mcp`. Both expose drawing primitives (line, rectangle, hatch). We deliberately don't copy that shape (see section 3).

## 2. Core idea: the project model is the source of truth, and drawings are renders

LLMs are poor at placing coordinates and good at stating intent ("5 x 100 kW inverters, 18 modules per string, LT connection at 415 V"). So agents edit a typed **project model**. The server validates it, lays it out deterministically, and renders every sheet from it. Re-rendering is idempotent: the same model always gives the same DXF.

```
inputs ──► ingest ──► Project model ──► rules engine ──► findings
 (text, PDF,           (typed, versioned        │
  PAN/OND, KML,         JSON on disk)           ▼
  DXF/DWG)                    │           layout engines (SLD placement, array placement, cable routing)
                              ▼                 │
                        equipment catalog       ▼
                                          drafting (symbol library, sheet templates, company profile)
                                                │
                                                ▼
                               DXF ──► LibreCAD dxf2pdf ──► PDF set
                                 ├──► ODA File Converter ──► DWG
                                 └──► region preview PNG ──► back to the agent
```

## 3. Data shapes (decide these first)

All shapes are Pydantic v2 models. Engineering quantities carry units (`pint`, or branded float types such as `Volts` and `Amps`) so a kW never gets passed where a kVA is expected.

**Catalog** (shared, versioned, tenant-overridable):
- `ModuleSpec`: Pmax, Voc, Isc, Vmp, Imp, temperature coefficients, dimensions, bifaciality, ALMM listing.
- `InverterSpec`: AC rating (kVA/kW), MPPT count, MPPT voltage window, max DC voltage, max input current per MPPT, strings per MPPT, AC voltage.
- `BatterySpec`, `PCSSpec`, `TransformerSpec` (kVA, vector group, %Z, voltages), `CableSpec` (material, cross-section, cores, insulation, ampacity tables), `ProtectionSpec` (MCB/MCCB/ACB/VCB/fuse/SPD/isolator, ratings, breaking capacity), `MeterSpec`.

**Project**:
- `Project`: metadata, client, site, company profile ref, revision history, design basis (ambient Tmin/Tmax, altitude, grid voltage, voltage-drop limits).
- `Site`: boundary polygon, roofs or land parcels, exclusion zones, obstacles, orientation, the coordinate reference.
- `ElectricalNetwork`: a **directed graph**. Nodes are equipment instances (string, combiner/SCB, inverter, ACDB, LT panel, transformer, RMU, VCB, metering, POI, BESS, PCS). Edges are cable runs (cable spec, length, laying method, derating). SLDs, cable schedules, BOM, and voltage-drop checks are all queries over this graph, so they can't disagree.
- `ArrayLayout`: blocks → tables or trackers → module slots, each slot mapped to a string node in the network.
- `Sheet`: drawing number, kind (SLD, layout, string diagram, cable schedule, earthing, LPS), paper size, scale, the subgraph or region it shows, revision.

**Findings**: `Finding { rule_id, severity: error|warning|info, subject_ids, message, clause_ref, measured, limit }`. Rules live in a **registry** (one module per body of rules: DC string sizing, cable sizing, protection, earthing, CEA connectivity) rather than as conditionals spread through the renderers. Rendering a sheet for submission requires zero `error` findings. Drafts may render with a red "NOT FOR CONSTRUCTION" watermark.

## 4. MCP surface

Tools are coarse and domain-level. Every mutating tool returns the new model revision, any new findings, and optionally a preview image.

| Group | Tools |
| --- | --- |
| Project | `project_create`, `project_get`, `project_list`, `project_snapshot` / `project_diff` (revisions) |
| Ingest | `catalog_import_datasheet` (PDF text → agent-filled spec, schema-validated), `catalog_import_pan_ond` (PVsyst `.PAN`/`.OND`), `site_import` (KML/KMZ, DXF, DWG), `pvsyst_import_report`, `helioscope_import` |
| Catalog | `catalog_search`, `catalog_add`, `catalog_get` |
| Design | `design_strings` (propose a string length and MPPT allocation within limits), `network_add_equipment`, `network_connect`, `network_update`, `network_autobuild` (from a block-level description such as "5 inv, 1 ACDB, LT 415 V POI"), `layout_place_arrays` (polygon, setbacks, pitch or GCR, walkways), `cables_size`, `earthing_design`, `lps_design` |
| Check | `validate` (all rules, or a single family), `explain_finding` |
| Draw | `sheets_plan` (proposes the drawing register), `sheet_render`, `set_render` (whole set to DXF/PDF/DWG), `preview` (PNG of a sheet or a cropped region at readable scale) |
| Escape hatch | `annotate` (free notes, clouds, leaders, revision triangles on a user layer), `dxf_query` (read entities from any DXF) |

Resources: catalog entries, project JSON, rule documentation with clause references, company profiles. Prompts: "DISCOM net-metering SLD", "utility SLD + MV yard", "array layout from KML".

Manual edits in LibreCAD survive regeneration. Generated content goes only on `G-*` layers. On re-render, entities on any other layer are copied forward. LibreCAD reports text edits made on generated layers so the agent can absorb them into the model.

## 5. Rendering details the spike forced

- **Previews.** `dxf2png` renders only the whole drawing, which is unreadable for a site. We'll render cropped previews with ezdxf's drawing add-on (PyMuPDF backend) and keep LibreCAD for the official PDF set. A CI check confirms the two agree on every golden fixture.
- **SLD placement.** We'll use deterministic layered placement over the network graph: POI at the top, transformers next, then panels and inverters, with strings at the bottom. Sheets split by subtree, and cross-reference tags ("TO SH. E-103 / INV-07") mark each split. A utility SLD becomes an overall SLD plus one detail sheet per block.
- **Layout.** We place tables or trackers as nested blocks, which made the file 50x smaller in the spike. Geometry uses `shapely`.
- **Tables** (cable schedule, BOM, legend) are drawn as lines plus TEXT/MTEXT, because LibreCAD doesn't support the DXF TABLE entity (to confirm in phase 0). The same data also exports as XLSX.
- **Fonts and hatches.** We'll use LibreCAD-safe SHX/LFF text styles and only the hatch patterns LibreCAD ships. Each item gets a phase 0 compatibility test.
- **DWG.** We convert with the ODA File Converter (free download). Its licence terms for bundling inside a commercial product must be checked. If they don't allow it, we ask the customer to install it.
- **Headless.** Production and CI hosts run LibreCAD under `xvfb-run`. We pin a LibreCAD version and keep a known-good AppImage.

### LibreCAD 2.2.1.6 facts found while building the symbol library

| Fact | Evidence | Rule for the writer |
| --- | --- | --- |
| `dxf2pdf` makes the page 1000x too big when `$INSUNITS` is unset | A4 came out as 595276 x 841890 pt. Setting `doc.units = MM` gave 842 x 595 pt. | Every DXF we write sets `$INSUNITS = 4` (mm) |
| ATTRIB entities are not drawn | A test block's ATTRIB was missing from the PDF, but plain TEXT next to it printed | `insert()` writes invisible ATTRIBs for machine reading plus visible TEXT on `G-SYMBOL-TXT` |
| Line weights print only at a fixed scale | Lines with weights 0.70, 0.35 and default printed at distinct widths with `-s 1 -c` | Sheets are drawn at paper mm and exported with `-s 1 -c`, not `-a` |
| LWPOLYLINE width is ignored | Symbol agent's test, not rechecked | Filled shapes use SOLID |
| Text is exported as strokes, not as PDF text | `pdftotext` returns nothing | Search and indexing read the model or the DXF, never the PDF |
| `dxf2png` draws text as empty boxes | Symbol agent's report, not rechecked | Agent previews come from PDF rasterising (`pdftoppm`), not from `dxf2png` |

## 6. Standards scope (India): to be confirmed with your samples

This is my starting list. Every clause reference gets checked against the source document before a rule cites it.

- Symbols: IS 12032 (the IEC 60617 equivalent).
- PV array and DC: IEC 62548 practice, IS/IEC 61730, MNRE/ALMM module listing, 1500 V DC limits.
- Grid connectivity: CEA (Technical Standards for Connectivity of Distributed Generation Resources) Regulations, plus each state DISCOM's net-metering SLD format.
- Safety: CEA (Measures relating to Safety and Electric Supply) Regulations.
- Wiring and cables: IS 732, IS 694, IS 1554, IS 7098.
- Earthing: IS 3043. LPS: IS/IEC 62305.
- Inverters: IS 16221, IS 16169.

Rule thresholds such as voltage drop, DC/AC ratio, and the safety factor on Isc go in the company profile. Defaults come from the standard where one exists. Otherwise they come from common practice and are marked as such in the finding.

## 7. Repo layout

```
unicad/
  domain/       models: catalog, project, network graph, layout, sheets, units
  ingest/       pdf, pan_ond, kml, dxf_dwg, pvsyst, helioscope
  rules/        registry + one module per rule family, each with worked-example tests
  layout/       sld_placement, array_placement, cable_routing
  drafting/     symbols (IS 12032 block library), sheet templates, profile, writer (ezdxf)
  render/       backend interface; librecad (pdf/png/svg), oda (dwg), preview (crops)
  mcp/          FastMCP server: tools, resources, prompts; stdio + streamable HTTP
  store/        project storage, revisions, tenant/company profiles
profiles/       default + per-company (title block DXF, layers, numbering, thresholds)
fixtures/       reference projects (yours, anonymised) + expected outputs
spikes/         throwaway experiments with their measured results
```

Stack: Python 3.12+, `uv`, `mcp` (FastMCP), `pydantic`, `ezdxf`, `shapely`, `networkx`, `pypdf`/`pdfplumber`, `pymupdf`, `openpyxl`, `fastkml`, `pytest`.

## 8. Phases

Each phase ends in a check that runs on real artifacts. Nothing starts until the previous phase's check is green.

**Phase 0: foundations (about 1 week)**
- Compatibility matrix: render TEXT/MTEXT, ATTRIB, nested blocks, hatches, dimensions, linetypes, and fonts through LibreCAD, the ODA round trip, and the preview renderer. Record what works.
- Domain models with units, the network graph, and project store with revisions.
- MCP skeleton exposing `project_*` and `preview`. Smoke test by connecting from Claude Code and OpenCode.
- Exit check: one hand-written fixture model renders to DXF, PDF, and DWG. It opens in LibreCAD GUI and in a DWG viewer.

**Phase 1: C&I rooftop SLD end to end (about 2 weeks)**
- Catalog, plus import from datasheet PDFs and PAN/OND files.
- DC string sizing rules and protection/cable rules for LT.
- First symbol library, A3/A2 sheet template, title block, SLD placement.
- `network_autobuild` for rooftop topologies: string inverters → ACDB → LT panel → net meter → DISCOM.
- Exit check: from a plain-text brief and two datasheets, an agent produces a DISCOM-style SLD. Your engineers compare it to one of your past drawings and agree it is submittable. Rules tests reproduce hand calculations.

**Phase 2: utility SLD with MV yard (about 2 to 3 weeks)**
- MV equipment: IDT, RMU, VCB, CT/PT, metering, 33 kV or higher POI. Also SCB/combiner and central-inverter topologies, and BESS/PCS (AC- and DC-coupled).
- Multi-sheet pagination with cross-references, plus an overall SLD.
- Exit check: a 50 to 100 MWp fixture produces an overall SLD plus block sheets with no broken cross-references. Every string in the model appears on exactly one sheet, verified by a script.

**Phase 3: array site layout (about 2 to 3 weeks)**
- KML and DXF site import, setbacks and exclusions, fixed-tilt table and tracker placement by pitch or GCR, roads and walkways, block partitioning tied to inverter blocks.
- Rooftop variant: roof outline, obstacles, fire-access paths.
- Exit check: on one of your past sites, the module count and DC capacity land within ±2% of your as-designed drawing. Layout blocks match the SLD blocks one-to-one.

**Phase 4: the rest of the set**
- String and wiring diagrams generated from the layout-to-network mapping.
- Cable routing along trenches and trays, cable schedule (DXF + XLSX), and BOM.
- Earthing layout (IS 3043 sizing) and LPS (IS/IEC 62305 rolling-sphere or protection-angle zones).
- Drawing register and full set render.

**Phase 5: product hardening**
- Tenant onboarding: import a company's title block DXF, symbol overrides, layer map, and numbering scheme.
- Streamable HTTP transport with auth, for your agent app.
- Packaging: `uvx unicad-mcp`, a Docker image with LibreCAD and Xvfb, and pinned versions.
- Optional: a live LibreCAD bridge, if customers ask for live editing.

## 9. Verification strategy

- **Rules.** Each rule has worked examples taken from real datasheets and hand calculations, asserted against the expected finding.
- **Golden fixtures.** Each fixture project renders to DXF. Tests check the semantics with ezdxf queries (equipment tags present, attribute values, sheet count, no entities outside the sheet frame). They also diff PNGs with a tolerance.
- **LibreCAD acceptance.** Every fixture runs through the pinned LibreCAD `dxf2pdf`. The test requires exit 0 and a non-blank page per sheet.
- **Agent evals.** A fixed set of briefs runs through a real MCP client. We score whether the agent reaches zero errors, how many tool calls it takes, and whether a reviewer would sign the output.

## 10. Risks

| Risk | Mitigation |
| --- | --- |
| LibreCAD renders some DXF features differently from AutoCAD (fonts, MTEXT, hatches) | Phase 0 compatibility matrix. Restrict the writer to the subset that works. Run the ODA round trip in CI. |
| Agents produce wrong engineering values | The rules engine blocks submission renders. Title block keeps "Designed / Checked / Approved" for a human signer. Product terms say the tool assists a licensed engineer. |
| DISCOM formats differ by state | Format is part of the company and region profile, with no hard-coded sheet layout. |
| ODA licensing for commercial bundling | Check early. Fall back to a customer-installed converter. |
| Datasheet PDF extraction errors | The agent fills the spec and the server validates ranges and cross-checks (Pmax ≈ Vmp × Imp). The user confirms new catalog entries once. |
| Manual LibreCAD edits lost on regenerate | Layer ownership rule (section 4) plus a test for it. |

## 11. Remaining steps (as of 2026-10-29)

Status: phases 0-5 are built and merged. Verifier runs 1-6 are closed, and every run-6 defect that blocked issue is fixed (r1-elec, r2-draw, r3-site-perf merged; master 1865 passed, e2e 5 passed). The first real client brief (RVUNL Heerapura 1.4 MWp / 1.2 MWac, run report `/tmp/opencode/rvunl/run/REPORT.md`) reached 0 validate errors but could not be issued For Approval. Everything below is open. IDs: `R6-*` from verifier run 6, `RV-*` from the RVUNL run.

### 11.1 Catalog gaps (block real client briefs)
- RV-S1/S2: no module above 635 Wp (the brief had 720 Wp) and no 150 kW string inverter (the closest are 100 kVA and 300 kW). Add both from datasheets once the client names the make and model. Until then every brief with a newer module or inverter gets a different plant.
- RV-S4: no multifunction meter (RS-485); only ABT/TVM meters exist.
- RV-S5: no data logger part. The logger is drawn as a kWh meter marked "RATING TO BE SPECIFIED".
- RV-m6: the SPD entry has In 20 kA; the brief's "40 kA" is probably Imax. Check against the datasheet and show both.
- Still missing: 33 kV fuse-switch; 1500 V ~500 A SCB outgoing DC switch; 800 V ~400 A breakers beyond EasyPact; IDT catalog entries (inline project specs only); equipment dimensions (envelopes assumed 8 m / 2 m).
- RV-m5: `catalog_search` returns nothing above the catalog's range without saying where the range ends (e.g. "largest module is 635 Wp").

### 11.2 Modelling gaps (what the brief asks for that the model cannot hold)
- RV-34: mixed string lengths in one inverter (18 and 19 modules) and uneven modules per inverter (252 / 234 / 240). Today strings are uniform.
- RV-30: no project developer field (model, title block, register).
- RV-31: the given site lat/long is stored but printed on no sheet. Print it on the layout and in the title block.
- RV-S7: no LT ground-mount template. `utility_string` fails with "no catalog 0.415 kV transformer >= 110 kVA" for an LT plant, and the agent had to use `ci_rooftop` and delete the DISCOM meter, consumer panel and load by hand. Add a template for an LT ground plant into a client panel, or make the error point to `ci_rooftop`.
- RV-A6: a fixed outgoing cable (7R x 300 Al in the brief) cannot be checked against a lower ACB setting. Support an ACB long-time setting (Ir) below the frame rating, so 7R against 2500 A frame / ~2000 A setting can pass or fail on its merits.
- RV-M5: node `attributes` (busbar rating, CT/PT cores, relay functions) are never drawn. The agent had to put this data in the title text. Draw the standard attributes on the SLD; give the relay a proper symbol with its ANSI numbers.

### 11.3 Drafting defects
- RV-M2: projects without an EPC profile print the demo "UniCAD Demo EPC" with a Pune address. Ask for the EPC profile up front, or print a neutral title block that asks for it.
- RV-M1: assumptions (plot boundary, soil resistivity, fault level, LPS level, tilt) are stored but not marked on the sheets. Every assumption that drives a sheet must appear there as "ASSUMED" (a notes block per sheet plus a set-level assumptions list on the register).
- RV-M3: the layout sheet shows no ACCB, Main Board Panel or cable route for an LT plant; it shows indicative "stations" that do not exist.
- RV-M4: communication links (RS-485 daisy chain inverter -> logger) cannot be drawn ("missing pin").
- RV-M8: the earthing sheet draws 12 block "station" earth details and pits when the design has no stations (string inverters on an LT bus). The plan is also dense at A1.
- RV-M7: the LPS step put one 6 m ESE mast in the middle of the array (it shades modules), with a 97 m circle well past the plot. Place masts in row gaps or at the boundary, and say when a single mast is chosen.
- RV-m1: layout blocks are fragmented and interleaved, with diagonal dashed block boundaries.
- RV-m2 / R6 open: sheets use only part of the paper (RVUNL SLD on A1 is mostly blank; run-6 C SLD sheet 2 and A earthing sheet about a quarter filled). Smaller paper made them unreadable before, so this needs a better fit, not just a smaller sheet.
- RV-m3: revision remarks over ~55 characters are truncated in the title block, and the user is warned only at render. Reject or wrap at `project_issue`.
- RV-m4: the BOM leaves out client-scope items (Main Board Panel) without saying so. Add a "client scope, not in this BOM" note.
- R6 open: some labels sit far from what they name; some layout labels cover array tables.
- R6 open: battery tags on crowded sheets go into stacks with crossing leaders (earthing sheet of the hybrid). Acceptable, not good.
- R6 open: an unreadable SLD sheet gets a red banner and a set warning, but no validate rule blocks issuing it. Add one in rules.

### 11.4 Tool and agent-experience defects
- RV-M6: `layout_place` can fall short of the target (`target_reached: false`, 800 of 1400 kWp) and later steps and validate still pass with 0 errors. Make a shortfall beyond a tolerance a validate error (or at least a warning carried into every later result).
- RV-M4b: `network_update` accepts an unknown `symbol` silently and `drawings_render` then fails. Validate the symbol key at update and expose the valid keys through MCP.
- `network_connect` names the valid control-edge ports ("sig/sec") only in the error after a failure. List them in the tool description.
- `project_issue` cannot amend a revision, so one fix costs a whole new issued revision (RVUNL ended with four revisions in one day).
- `site_import(replace=True)` silently resets the setbacks.
- Every edit makes a revision (RVUNL reached revision 201), and re-running `network_autobuild` discards hand edits. Hand edits need to survive a rebuild, or the tool must say what it will discard.

### 11.5 Engineering inputs still assumed (need the EPC or a protection engineer)
- IDT impedance 7 % from one BHEL 5.5 MVA spec, applied to every inverter-duty transformer (`design/templates.py` IDT_Z_PCT).
- LT clearing 0.1 s and HV-cleared LT faults 0.103 s; relay settings unverified.
- EasyPact Solar MCCB terminals assumed 2 x 300 mm2 per pole.
- Floating (transformerless) arrays assumed, so both poles fused.
- Defaults when not given: switchyard 60 x 45 m, crushed rock 3000 ohm-m, BESS area 40 x 30 m, road width 6 m, rooftop walkway 0.6 m every 2 rows, ESE dT 60 us, inverter fault 1.5 x In, equipment fence clearance 3 m.
- Fault levels are user inputs; there is no fault study. RVUNL took 31.5 kA / 3 s from the busbar rating.
- Bus-section VCB not added (no cited rule).

### 11.6 Product hardening
- DWG is LibreDWG R2000 only, with ATTRIB doubling on read-back; never opened in AutoCAD. The ODA converter is not obtained.
- The company layer map applies only to SLD sets and the company preview; schedules, BOM, earthing and site sheets keep UniCAD layers.
- Design thresholds vs the company profile: check that every design path reads the profile thresholds (N12).
- Big-plant renders are now ~38 s for 45 MW (was 167 s), under the 60 s client timeout. Larger plants may still exceed it; add a background render job or progress reporting.
- Rooftop fixture SLD splits to 3 pages in the Docker smoke; check that a single-sheet DISCOM rooftop SLD is still possible.
- `.git` is 733 MB from committed regenerated samples. Move samples out of history or to LFS.
- Licensing before shipping: derived tables from manufacturer catalogues and BIS IS 1180, the 5.7 MB ALMM extract, 7 third-party source mirrors to replace (`references/catalog-sources/SOURCES.md`). Sample drawings stay internal.

### 11.7 Order of work
1. Catalog parts for the RVUNL module and inverter (needs make and model from the client), then rerun RVUNL. This turns RV-S1..S3 into matches.
2. Fix wave 7 in three disjoint units: (a) model and rules: developer field, mixed strings, LT ground template, ACB setting, layout-shortfall rule, unreadable-sheet rule, symbol validation; (b) drafting: assumptions on sheets, title block without a profile, attributes on the SLD, comms links, LT layout equipment and cable route, station-less earthing, sheet fill, labels; (c) site and LPS: mast placement, block fragmentation, coordinates on sheets, `site_import` setbacks, issue amend.
3. Verifier run 7 on the three standard scenarios plus the RVUNL brief as scenario D. Done when every set is answered "a licensed engineer could issue it For Approval after normal review".
4. Product hardening (11.6) and the EPC-owned inputs (11.5) in parallel with the customer.

## 12. What I need from you

1. Sample drawings are being sourced from public tenders and DISCOM formats (`references/samples/`). Your own title block, when you have one, replaces the default.
2. The DISCOM or state formats you submit to most often.
3. The design basis you use today: voltage-drop limits, DC/AC ratio targets, cable derating approach, and Tmin/Tmax source.
4. Whether a human-in-the-loop approval step must be enforced in the tool, or only shown in the title block.
5. For the RVUNL Heerapura brief: make and model of the 720 Wp module and the 150 kW inverter, the EPC company's title block, and the actual ACB setting for the 7R x 300 sq.mm outgoing cable.

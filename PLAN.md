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

## 11. What I need from you

1. Sample drawings are being sourced from public tenders and DISCOM formats (`references/samples/`). Your own title block, when you have one, replaces the default.
2. The DISCOM or state formats you submit to most often.
3. The design basis you use today: voltage-drop limits, DC/AC ratio targets, cable derating approach, and Tmin/Tmax source.
4. Whether a human-in-the-loop approval step must be enforced in the tool, or only shown in the title block.

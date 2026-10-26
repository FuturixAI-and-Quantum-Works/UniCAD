# Onboarding a company

A company profile makes every sheet and rule follow one EPC's standards: its title block and logo, layer names, drawing numbers, symbols and engineering thresholds. A new company onboards itself with one DXF and a short questionnaire; nothing needs to be coded.

```
company_onboard(dxf_path="company.dxf", answers="answers.yaml")   # builds and saves the profile, reports what it found
company_preview_sheet(paper="A3", profile_id="acme")              # look at it
company_assign(project_id="rooftop-500kwp", profile_id="acme")    # use it for a project
company_update(profile_id="acme", fields={...})                   # change it later
```

Profiles live in `<workspace>/profiles/<id>.json`, with their DXF files under `<workspace>/profiles/<id>/`. With `--tenants` each tenant has its own workspace and so its own profiles. A tenant profile hides a bundled profile (`profiles/` in the repository: `default`, `acme`) of the same id; `UNICAD_PROFILES` adds more read-only directories. A project names its profile in `company_profile`; `company_assign` sets it. `drawings_render`, `cable_schedule`, `bom`, `validate` and `explain_finding` use the project's profile.

The sample used by the tests is in `profiles/samples/acme/` (`acme-company.dxf`, `answers.yaml`), written by `uv run python -m unicad.tenants.samples <dir>`. Its result is the bundled `profiles/acme.json`.

## The company DXF

One DXF, any CAD package, millimetre units. UniCAD reads:

| What | How it is found |
| --- | --- |
| Title block per paper size | A block whose name has the paper size (`ACME_TB_A3`, `TITLEBLOCK-A1`) and at least three placeholders. Or name it in `title_blocks: {A3: BLOCK}`, or draw it in modelspace on a layer: `title_blocks: {A3: {layer: TB-A3}}`. |
| Logo | The block with `LOGO` in its name, or `logo_block`. It is copied to `<id>/logo.dxf`. |
| Layers | The layer table. The frame, title and text layers are matched by name (`FRAME`/`BORDER`, `TITLE`, `TEXT`) or given in `layers`. |
| Symbols | The blocks named in `symbols`. |

### Title block placeholders

A placeholder is a TEXT, MTEXT or ATTDEF whose text is `{NAME}` (an ATTDEF may carry the name as its tag). UniCAD replaces it with the value, wrapped, condensed and shrunk until it fits the **cell** the placeholder sits in: the closed box of lines around it, cut off below any label text above it in the same cell. The placeholder's own height is the largest text height; its alignment (left, centre, right) and anchor point are kept. The rest of the block is copied as drawn, scaled uniformly so its width equals the sheet's right-hand column (115 mm on A3, 161 mm on A1). A sheet whose paper size has no title block uses the one for the nearest paper size, scaled.

| Placeholder | Value |
| --- | --- |
| `{DRAWING_NO}` (required) | Drawing number from the profile's pattern |
| `{TITLE}` or `{DRAWING_TITLE}` (required) | Drawing title |
| `{REV}` (required) | Revision |
| `{SHEET}` (`1 of 6`) or `{SHEET_NO}` (required, one of the two); `{SHEET_COUNT}` | Sheet number and count |
| `{OWNER}` or `{CLIENT}`, `{CONSULTANT}`, `{PROJECT_TITLE}`, `{LOCATION}`, `{LOA_NO}` | Project fields |
| `{SCALE}`, `{PAPER}` | Scale and paper size |
| `{DRAWN}`, `{CHECKED}`, `{APPROVED}`; `{DRAWN_DATE}`, `{CHECKED_DATE}`, `{APPROVED_DATE}`, `{DATE}` (drawn date) | Sign-off, dates in the profile's `date_format` |
| `{ISSUED_FOR}` | Issue purpose as text |
| `{ISSUED_PROPOSAL}`, `{ISSUED_INFORMATION}`, `{ISSUED_APPROVAL}`, `{ISSUED_CONSTRUCTION}`, `{ISSUED_AS_BUILT}` | Put one in each tick box; the selected one gets a cross |
| `{COMPANY}`, `{ADDRESS}`, `{SHORT_CODE}`, `{CONFIDENTIALITY}` | From the profile |
| `{LOGO}` | The logo is drawn, as large as fits, in this cell |

Onboarding fails a title block with an unknown placeholder, a missing required one, a duplicated one, a cell too small for its text, or a height over 60 % of the sheet column. A project value that has no placeholder (say a consultant) is left out and the sheet carries a warning. Static text uses the profile's text style; entities on layer `0` go to the title layer; IMAGE, WIPEOUT and hatches other than SOLID/ANSI31 are dropped and reported.

### Company symbols

`symbols: {pv.module: {block: ACME_PV_MODULE, pins: named}}` replaces a UniCAD symbol (keys of the symbol library in `src/unicad/drafting/symbols/library`, for example `pv.module`, `inst.kwh`, `sw.mccb`) with the company's block. The diagram routes conductors to the UniCAD symbol's pins, so the block must land on them:

- `pins: named`: put a POINT on a layer `PIN_<pin name>` (for example `PIN_dc+`, `PIN_dc-`) at each pin of the symbol. The block is scaled uniformly and moved so the points land on the pins (within 0.25 mm). Every pin must be marked; a mark for a pin the symbol does not have is refused.
- `pins: fit`: no marks. The block's extents are fitted to the symbol's box (the shapes must agree within 5 %) and every pin must then touch its geometry (within 0.5 mm).

The geometry goes on the UniCAD symbol layer, so the layer map restyles it. A block that spills more than a quarter of the symbol's size outside its box is refused. An override that stops validating is skipped at render time and the sheet carries a warning.

### Layers

`layers: {frame: ACME-FRAME, title: ACME-TITLE, text: ACME-TEXT}` sets the sheet furniture layers. `layer_map: {G-SYMBOL: ACME-SYM}` renames any other UniCAD layer in the finished drawing, taking colour, linetype and lineweight from the company layer (lineweights under 0.13 mm become 0.25 mm; linetypes LibreCAD cannot print become CONTINUOUS; both are reported).

## The questionnaire

YAML or JSON; unknown keys are refused.

```yaml
id: acme                     # profile id: a-z 0-9 - _
name: Acme Solar EPC Pvt. Ltd.
short_code: ACME             # {company} in drawing numbers
address: "..."
confidentiality_note: "..."
date_format: "%d.%m.%Y"
default_paper: A3
roles: {drawn: DRN, checked: CHKD, approved: APPD}   # sign-off labels
numbering:
  pattern: "{company}/{client}/{project}/{discipline}/{seq:03d}"
  disciplines: {electrical: EL, civil: CV, substation: SS}
  per_discipline: true       # each discipline counts its own sequence
  start: {civil: 101}
logo_block: ACME_LOGO
layers: {frame: ACME-FRAME, title: ACME-TITLE, text: ACME-TEXT}
layer_map: {G-SYMBOL: ACME-SYM}
symbols:
  pv.module: {block: ACME_PV_MODULE, pins: named}
thresholds:
  dc_ac_band:
    value: 0.15
    source: "Acme design standard DS-04 clause 3.2"
revision_marks: {style: cloud, stamp_text: REVISED}
```

Only `id`, `name` and `short_code` are required. Everything else falls back to UniCAD's defaults and the report says so.

## The report

`company_onboard` returns `found` (what was read and from where), `defaulted` (what fell back to a default), `failed` (what did not validate), `warnings`, and, when saved, the profile. Pieces that fail are left out. Nothing is saved while anything failed, unless `accept_partial=true`. Fix the DXF or answers and run it again; the same inputs give the same profile.

## Thresholds

Every constant in `unicad.rules.thresholds` can be set with a value and a source text. Names, defaults and accepted ranges:

| Name | Default | Accepted | Used by |
| --- | --- | --- | --- |
| `isc_factor` | 1.25 | 1 to 3 | String, protection and combiner current rules |
| `continuous_factor` | 1.25 | 1 to 3 | Inverter output protection rating |
| `dc_ac_band` | 0.10 | 0 to 1 | DC/AC ratio warnings |
| `voltage_tolerance` | 0.05 | 0 to 0.5 | Equipment voltage compatibility |
| `stc_irradiance` | 1000 W/m2 | 100 to 1500 | Hot-cell temperature |
| `stc_temp` | 25 degC | 0 to 60 | Voltage at temperature |
| `k_xlpe_cu` | 143 | 50 to 250 | Cable short-circuit withstand |
| `k_xlpe_al` | 94 | 30 to 200 | Cable short-circuit withstand |
| `fault_clearing_default` | 1 s | 0.01 to 10 | Clearing time when the basis gives none |

`validate` and `explain_finding` run the rules with the project's profile. A finding that depends on a company value quotes `Company standard: <source> (value ...)` in its assumptions instead of the generic text, and `validate` lists the company values under `company_thresholds`. The design tools (`design_*`, `autobuild`) still use the defaults. `company_get` shows every threshold with its origin.

## Drawing numbers

`pattern` may use `{company}`, `{client}`, `{project}`, `{discipline}` and `{seq}`. A project's existing sheet numbers are kept; new sheets continue after the highest sequence number. With `per_discipline: true` each discipline continues its own sequence, starting at `start[discipline]` (default 1).

## Profile format

`CompanyProfile` in `src/unicad/drafting/sheets/model.py`. Files without `version` are version 1 and load unchanged; the version 2 blocks below are refused in a version 1 file. Saving through `company_update` writes version 2.

| Field | Meaning |
| --- | --- |
| `name`, `short_code`, `address`, `confidentiality_note`, `logo` | Identity; `logo` is a DXF path |
| `drawing_number_pattern`, `discipline_codes`, `numbering` {`per_discipline`, `start`} | Numbering |
| `default_paper`, `text_style`, `text_font`, `date_format`, `roles` | Sheet defaults and sign-off labels |
| `layers` {frame, title, text: layer spec} | Sheet furniture layers |
| `layer_map` {UniCAD layer: layer spec} | Version 2. Rename layers in the finished drawing |
| `title_block` {`kind`: builtin or imported, `templates`: {paper: {`dxf`, `block` or `layer`}}} | Version 2 |
| `symbol_overrides` {symbol key: {`dxf`, `block`, `pins`}} | Version 2 |
| `thresholds` {name: {`value`, `source`}} | Version 2 |
| `revision_marks` {`style`: triangle or cloud, `stamp_text`} | Version 2. Recorded for the revision-marking tools; the sheet furniture does not use it yet |

Relative file paths are relative to the profile file.

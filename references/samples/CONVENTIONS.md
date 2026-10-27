# Drawing conventions distilled from the public samples

> Page images are local only. Rebuild them with `uv run python tools/render_reference_pages.py`, which writes `pages/<raw-subfolder>/<pdf-stem>/pNNN.png`. The older `pages/<short-name>/` paths below refer to the first extraction and map to the same PDFs listed in SOURCES.md.

This file distils the drawing conventions from the samples in `raw/` (see `SOURCES.md`). Each observation is tagged with the sample it comes from:

| Tag | Sample | PNGs |
| --- | --- | --- |
| **[CIL]** | Coal India 100 MWac, Refex/Svaryu EPC set, Gujarat. A real Indian EPC submission and the primary reference. | `pages/cil100/cil_pNNN*.png` |
| **[KHV]** | NHPC/GSECL Khavda 33/400 kV pooling-substation tender SLD (Tatva for GERMI). | `pages/khavda/` |
| **[LNT]** | L&T DC cable schedule, GSECL 35 MWac + BESS, Panandhro. | `pages/cablesched/` |
| **[HES]** | HESCOM rooftop net-metering SLD. | `pages/hescom/` |
| **[TGR]** | TGREDCO LT/HT net-metering schematics. | `pages/tgredco/` |
| **[HVN]** | Heaven Solar brochure: C&I rooftop SLD, earthing and LA layout (low resolution). | `pages/heaven/` |
| **[IND]** | Indravati 160 MW FPV DPR: 132 kV SLD and general layout. | `pages/indravati/` |
| **[EDF]** | US Morris Ridge, WSP: DC SLD, BESS DC SLD E-117. Non-Indian; use it for structure only. | `pages/morris/` |
| **[IOM]** | SLD_Automation DXFs (US MV switchgear, AutoCAD Electrical). Use them for DXF structure only. | `pages/dxf/` |
| **[IEC]** | IEC 60617 symbol chart. | `pages/iec/` |

Confidence: [CIL] is a complete, vector, real-project set, so most "must replicate" items come from it. The rooftop samples ([HES], [TGR], [HVN]) are schematic or low resolution. A real DISCOM-submission rooftop or C&I SLD in CAD quality is still a gap.

---

## 1. Sheet frame and title block (all kinds)

### Frame
- The border has zone markers: numbers 1..7 or 1..8 along the top and bottom, letters A..E or A..F up the sides. A small corner trim mark sits at the top-left and bottom-right. [CIL p42, p44, p114, p120]
- A copyright line runs along the top edge of the SLD sheets: "THIS DOCUMENTS IS THE PROPERTY OF REFEX ENERGY LTD. IT SHOULD NOT BE COPIED OR MODIFIED WITHOUT OUR PERMISSION IN WRITING." [CIL p40, p42, p44]. [LNT p1] has the same idea as vertical text in the left margin.
- Layout of an A0 SLD sheet [CIL p42, p44]:
  - The drawing field takes the left ~80 % of the sheet.
  - The **right column** stacks, from top to bottom: LEGEND table, SYSTEM PARAMETERS table, then the title block.
  - The **bottom band** holds GENERAL NOTES on the left and the REV / DATE / REMARKS revision table in the middle. The title block sits bottom-right.

### Title-block fields [CIL, on every sheet]
- OWNER: the owner logo (Coal India).
- PROJECT TITLE: a long LOA scope sentence, e.g. "SUPPLY OF PLANT AND EQUIPMENT INCLUDING TYPE TEST AND SPECIFIED SPARES FOR 100 MW AC SOLAR POWER PLANT TO BE SETUP IN GUJARAT".
- OWNER : name. PROJECT LOCATION : site. EPC : name. LOA NO : "CIL/EED/SOLAR/2021/20, Dated: 26-04-2021".
- DRAWING TITLE.
- DWG NO / DRAWING NO, e.g. `REFEX-CIL-202109-EE-004`. The pattern is EPC-OWNER-JOBNO-DISCIPLINE-SEQ, with the disciplines EE electrical, CE civil and PSS substation. A REL DWG NO field appears on civil sheets.
- A single-row grid: DRAWN | CHECKED | APPROVED | DATE | SCALE | SHEET NO. | PAPER SIZE | REV. Entries are initials ("MI RCK RCK"), dates as DD.MM.YY, scale "NTS" or "1:100", sheet "1 Of 2", size "A0", rev "00".
- EPC block: logo plus postal address, phone, fax and website.
- **DRAWING ISSUED FOR** checkboxes: As built / Approval / Information / Proposal, with one ticked.
- Revision table rows: REV | DATE | REMARKS, e.g. "0 | 06-10-21 | For Preliminary Submission" and "00 | 13/06/2022 | FIRST SUBMISSION".

Other samples:
- [LNT p1] uses a document-style cover: Project, Owner logo, Engineering, EPC Contractor logo, Job Code, Document Title, Doc No in one-character boxes (`O22001-PTD-S-DC-CS-0004`), Rev No, Size ("A4 & A3"), Page No ("1 of 51"). It has a revision-history row with Prepared/Checked/Approved, each with a date, and "Released For" checkboxes: PRELIMINARY / ENQUIRY / INFORMATION / APPROVAL / FABRICATION.
- [KHV p109] has a revision list R0..R5 with DATE / REMARKS ("ISSUED FOR APPROVAL", "ISSUED FOR TENDERING") / DRN / CKED. It also has PROJECT, CONSULTANT and CLIENT blocks with logos, plus DRAWING TITLE, DEALT/SCALE/DATE/CHECKED/APPROVED, discipline checkboxes (ELECTRICAL / MECHANICAL / HVAC / PLUMBING / FFTG), PROJECT No, DRAWING No and REV.No. A big red annotation reads "ISSUED FOR TENDERING".
- [IND] uses a slim right-hand strip: key-plan satellite image, STATUS ("FOR DPR PURPOSE ONLY"), PROJECT, TITLE, LOCATION, DATE, SCALE "NTS", size "A1" or "A3".

> Model implication: the title block is a per-tenant template with fields {owner, owner_logo, epc, epc_logo, epc_address, project_title, location, loa_no, dwg_no, title, drawn, checked, approved, date, scale, sheet "n Of m", paper, rev, issued_for ∈ {proposal, information, approval, as_built}, revisions[]}.

### Paper sizes and scale
| Sheet | Paper | Scale | Source |
| --- | --- | --- | --- |
| DC SLD, AC SLD, 66 kV SLD, array layout | A0 landscape | NTS | [CIL p40, p42, p44, p88] |
| Switchyard layout, earthing layout | A1 | 1:125, 1:100 | [CIL p114, p116] |
| EKD sheet 2, earthmat details | A2 | 1/125 or NTC | [CIL p119, p121] |
| Civil details | A3 | 1:50 | [CIL p7, p22] |
| Cable schedule | A3 landscape table pages, A4 cover | | [LNT] |
| Tender pooling-substation SLD | A0 | | [KHV p109] |

- Dates appear as DD.MM.YY or DD-MM-YY. Dimensions are in mm, "LEVELS ARE IN METER" [CIL p2].
- Fonts: SLDs use a Times-like serif. Layout and earthing sheets use an all-caps simplex-style CAD font with tracking (a romans.shx look). [CIL] [IOM] use romans.shx and ISOCP2.shx (IEC style).

---

## 2. Utility AC SLD (33 kV collection) [CIL p42; KHV p109]

### Equipment chain [CIL p40 + p42 + p44]
```
PV module 540 Wp, 2P × 29 portrait strings joined by Y-connectors
 → string cable "4 Sq mm Cu.Cable" (+ve), "6 Sqmm Cu Cable"
 → SMB "1500 Vdc, 11 IN 1 OUT": 45 A fuse per input, SPD 1500 VDC Type-2, DC on-load isolator 400 A 1500 Vdc
 → "1.9/3.3kV, 1C X 300/400 Sq.mm, Al. Cable XLPE, Armoured"
 → PCU (central inverter) 3.125 MW @50 °C: 18 DC inputs with 400 A fuses (9, 17, 18 spare), 2 MPPT, 2 × 2000 A ACB 690 V 80 kA
 → "1.9/3.3kV, 8R X 1C X 300 Sq mm/Ph. Al. Cable XLPE, Armoured"
 → IDT "12.5 MVA, YNd11d11d11d11, 33/0.660-0.660-0.660-0.660 kV, ONAN, Z=8%, Al. Winding, OCTC +5% TO -5% IN STEP OF 2.5%"
     (one 5-winding IDT per 4 PCUs; LV BCT 3500/1A CL-PS; aux transformer 20 kVA 0.66/0.415 kV dry type with ACDB)
 → ICOG panel (outdoor IP-55 at the block): 33 kV 250 A busbar, 33 kV 630 A VCB 25 kA 1 s, CT 250/1A CL-PS, relay, SA 30 kV 10 kA Type 2
 → "33KV, 1R X 3C X 185 SQ.MM AL.XLPE, ARMOURED CABLE LENGTH(30M APPROX)"
 → 33 kV MAIN HT SWITCHBOARD PANEL (indoor/outdoor, 50 MW per section):
     "33kV(+/-10%), 1200A, @50°C Ambient, 50Hz(+/-3%), 3Ph, 25kA for 1 sec, AL. Busbar"
     incomers: 36 kV 630 A VCB, CT 250/1-1A (core-1 CL 5P20 10 VA protection; core-2 CL 0.5 10 VA metering), MFM import/export, SA
     bus PT draw-out: 33kV/√3 / 110V/√3 / 110V/√3 / 110V/√3, cores CL 0.2 / 3P / 3P 50 VA, open delta with damping resistor
     outgoing: 36 kV 1250 A VCB 25 kA; CT 1200/1-1-1A (CL 0.2s 15 VA / 5P20 15 VA / PS); line PT; SA
     tie feeder between the two 50 MW sections; aux-transformer feeder (100 kVA for the CMCS)
 → "33kV, 3R X 3C X 300 Sq.mm AL. XLPE, Armoured Cable" → "TO PLANT TIE TRANSFORMER-1 66/33 KV, 36/60MVA"
```
[KHV] shows the same pattern at 400 kV. 33 kV indoor VCB panels (150 MWp each, "33KV, 1250A VCB 40KA/3 SEC") feed a 33 kV LINE-BUS, then a 33 kV bus duct, then a 33-33/400 kV 315 MVA three-winding transformer ("OFAF/ODAF, YNyn0, OLTC +5% to -10% in step of 1.25%, z=12.5%"), then 400 kV GIS on a double bus (BUS-A / BUS-B) with DBCR isolators with E/S and SF6 CBs ("400KV, 2500A, 40kA/3S").

### Grouping
- Dashed or solid boxes enclose each panel and block. Every box has a title tab, e.g. "BLOCK-01 / OUTDOOR TYPE (IP-55) / 12.5MWac", "ICOG Panel-1", "33 KV MAIN HT SWITCHBOARD PANEL INDOOR OR OUTDOOR TYPE (50MW)", "CENTRAL MONITORING AND CONTROL STATION (@ CMCS)", "AUX TRAFO WITH ACDB PANEL". [CIL p42]
- A large bold title box sits at the top-left of the field: "PLANT (100MWac)". [CIL p42]
- Blocks repeat identically. Blocks 01-08 are drawn in full, not as "typical" [CIL p42]. The DPR instead draws one block and adds a box "2 Identical Blocks of 40 MW" [IND p189].
- The HT feeders are vertical. The main busbar is a long horizontal line labelled with its full rating. Feeders are numbered 1..n above the bus. [CIL p42]

### Annotation style
- Ratings are stacked text left of each device, CAD-style: "36kV,630A, VCB / 25kA for 1 Sec." [CIL p42]
- Cable callouts are text above an underline, attached by a short leader to a small ellipse on the cable (the cable-tick symbol): "33kV,1R X 3C X 185 Sq.mm / AL. XLPE, Armoured Cable". The format is **`<voltage grade>, <runs>R X <cores>C X <size> Sq.mm[/Ph.] <Al.|Cu> <XLPE> <Armoured>`**. [CIL p40, p42]
- CT callouts give ratio, core and class per core: "Core-2, 250/1-1A, CL - 0.5, 10 VA" and "Core - 1, 250/1-1A, CL - 5P20, 10 VA". [CIL p42]
- PT callouts write the ratio as a fraction with √3: "33kV/√3 / 110V/√3 / 110V/√3", then one line per core. [CIL p42; KHV]
- Transformers have a multi-line callout: rating, vector group, ratio, cooling, Z, winding material, tap changer and range. [CIL p40, p42]
- Device tags are free text: PCU-01..32, SMB-1.1..1.15, ICOG Panel-1..8, INVERTER TRANSFORMER-01..08.

### Protection, metering and indication
- Relays are circles holding ANSI device numbers, grouped in a rounded box per feeder: 50, 51, 50N, 51N, 27, 59, 67, 67N, 86, 87T, 94, 95, 25, 49WA/49WT/49OA/49OT (winding/oil temperature alarm/trip), 63BA/63BT (Buchholz), 63PT (PRV), 64R, LBB. The legend maps each number to a description. [CIL p42 legend 24-45; CIL p44 "LEGENDS FOR 66kV"; KHV "PROTECTION FOR 33/400 kV SLD"]
- MFM is drawn as a row of small ovals (KW, KVAR, KWH, KVARH) with the labels "Import / Export (MFM)" and "RS485 Communication Port to SCADA". Relays carry "IEC-61850 COMM PROTOCOL". [CIL p42]
- Each breaker has an indication-lamp row of filled dots labelled ON, OFF, SC (spring charged), SP (service position), TP (test position), TCH (trip circuit healthy), TNC, L/R, ES, AT, DF, AF. [CIL p42 legend 12-23]
- Annunciators are a small grid labelled "12 Window Annunciator" or "16 Window". There is also a VPIS (voltage presence indication system) and a HOOTER. [CIL p42]
- The [KHV] meter legend is boxed abbreviations: LM, MFM, MVAr, MWh, MW, F, V, A, VSS, ASS, ABT. There is also a "BCPU" (bay control & protection unit) box holding MVAr/MW/A/MVArh/MWh/V and relays 51N/51/50/67N/67/27. [KHV p109]
- A system-parameters table sits on the sheet: nominal voltage 33 kV, maximum 36 kV, design ambient 50 °C, short-circuit level 25 kA for 1 s, phase-phase 320 mm, phase-earth 320 mm, sectional clearance 275 mm, creepage 31 mm/kV, BIL 170 kVp, PFWV 70 kV rms, 50 Hz, effectively earthed. [CIL p42; at 66 kV on CIL p44]

### Standard general notes [CIL p42]
The notes are numbered and all caps:
1. What the SLD represents (capacity, voltage).
2. Block capacity and count.
3. Inverter/IDT combination.
4. Bus split and tie.
5. Indoor/outdoor location of the HT panels.
6. Feeder grouping is still to be finalised.
7. Aux transformer ratings.
8. Aux supply source.
9. **"ALL LT & HT POWER CABLE SIZES AND NUMBER OF RUNS ARE CONSIDERED AS TENTATIVE…"**
10. Relays are IEC-61850 and MFMs RS-485.
11. IDT impedance and CT/PT sizing will be submitted separately.
12. UPS/PLC/DB locations are decided during detailed engineering.

### Colour [KHV p109]
- Red: main power path and feeders.
- Yellow: 33 kV busbar and bus duct.
- Green: panel boundaries and panel titles ("33KV INDOOR VCB PANEL 150 MWp SOLAR CAPACITY").
- Magenta: switchgear symbols.
- Cyan: CT/PT secondary wiring to relays and meters.

[CIL] SLDs are black on white. Their layout and earthing sheets use colour by layer (see §6 and §7).

---

## 3. HV pooling-substation / MV-yard SLD (66 / 132 / 400 kV) [CIL p44; KHV p109; IND p189]

- Bays are boxed and numbered: "BAY NO - 101 BUS PT BAY", "102 66/33KV TFR-1", "103 66KV LINE-1", "104 BUS COUPLER BAY", up to 107. [CIL p44]
- **Device tag = bay number + ANSI function + suffix** [CIL p44]: `103-89L1` (line-side isolator), `103-89L1E` (its earth switch), `103-52` (CB), `103-89L2`, `103-LA`, `103-PT`, `103-CT`, `103-MPT`, `103-MCT`, `101-BPT-01`, `104-89A`.
- The chain for a line bay, top to bottom [CIL p44]:
  - Arrow up: "TO NEAREST GRID SS THROUGH 66KV SC TRANSMISSION LINE …".
  - LA "60kV, 10kA, CLASS-III".
  - Line PT (3 cores) with HRC fuse.
  - 89L1 isolator "72.5kV,800A, 31.5kA for 1 sec WITH 1ES".
  - Metering box "METERING YARD": MPT, MCT CL 0.2s, "1 NO. ABT METER (STANDBY)".
  - CT 3-core "700/1-1-1A" (CL-0.2s / PS / PS).
  - 52 CB "72.5kV,1250A, 31.5kA for 1 sec" with an anti-pumping box 94.
  - 89L2 isolator, then the bus.
  - Next to the bay sits a "66KV LINE CONTROL RELAY PANEL-1" box holding MFM/TVM/BCU, a relay-circle cluster and FFR (fuse-failure relay) taps.
- The busbar label has the full spec: "66kV, ACSR ZEBRA, SINGLE BUS, 700A, 31.5kA/1 SEC." [CIL p44]
- Transformer bay: "POWER TRANSFORMER-1 66/33 kV, 36/60MVA, (ONAN/ONAF), YNyn0, Z=11%, OLTC +/-10%, IN STEP OF 2.5%", with HV and LV BCTs (PS class) and "FROM: 50 MWac 33kV HT SWGR O/G FEEDER-01 @ CMCS". [CIL p44]
- The legend is a **quantity-bearing BOM legend**: S.NO, SYMBOL, DESCRIPTION, QUANTITY, e.g. "72.5kV,1250A,31.5kA FOR 1 SEC OUTDOOR SF6 CIRCUIT BREAKER — 5 NOS". [CIL p44]
- Notes [CIL p44] cite utility practice: "SINGLE BUS SWITCHING SCHEME CONSIDERED AS PER GETCO PRACTICE AND AS PER CBIP MANUAL", no PLCC/wave trap at 66 kV, no 87BB, and metering CT/PT subject to STU confirmation.
- 132 kV DPR level [IND p189]: grid substation box "132/33kV TENTULIKHUNTI SUBSTATION" → "DOUBLE CIRCUIT OVER HEAD 132 kV TRANSMISSION LINE", "132 KV, Zebra Conductor" → 132 kV AC busbar → 4 × "POWER TRANSFORMER 50 MVA 33/132 kV", each with "Auxiliary TFMR 25KVA, 3 PH Dry type" → 33 kV AC busbar → 40 MW block boxes.

---

## 4. DC SLD / string diagram [CIL p40; EDF p3; HVN p11]

- [CIL p40] draws one box per block. Each box holds 4 PCUs. Under each PCU sit 4-5 SMBs drawn as **long thin rectangles labelled "SMB-1.11"**, with input numbers 1, 9/10, 11 and "Spare". Below them, modules appear as small squares "M1"/"M2" in pairs joined to a +ve bus and a −ve bus.
- PCU callout [CIL p40]: "PCU - 01 / OUTDOOR TYPE / Output Voltage: 660V / Rating: 3.125 MW @50°C". A second column gives "Peak Power Rating of Module : 540 Wp / DC Power : 4604.04 KWp / Total no. of Modules : 8526 Nos." Per-PCU DC power and module counts are listed, so they differ between PCUs.
- Typical-detail insets in the right column [CIL p40]:
  - "STRING MONITORING BOX (TYPICAL) 11-INPUT 1-OUTPUT": fuses per input, SPD, isolator.
  - "POWER CONDITIONING UNIT - 3.125 MW (TYPICAL)": inverter square with = and ~, 18 fused inputs, 2 ACBs, with notes.
  - A **plant summary table**: plant capacity, module technology, module rating, orientation, tilt, modules per string, pitch, SMB rating, DC inputs per SMB, total SMBs, inverter rating, inverter count, IDT count.
- A bottom strip [CIL p40] shows "Module Interconnection Layout for 2P X 29 Modules" with modules 1..29 in two rows and Y-Connector-1 (+ve) and Y-Connector-2 (−ve).
- Legend [CIL p40]:
  - PV module: small square "M1".
  - PCU: square with a diagonal, "=" in the lower left and "~" in the upper right.
  - Inverter transformer: interlocking circles with Δ/Y marks.
  - Fuse: rectangle with a line through it.
  - DC on-load isolator.
  - SPD: arrow/varistor symbol with earth.
  - SCB: rectangle with the text "SCB".
- [EDF p3] adds US practice worth copying for IDs: combiner tag `DCC-E01-01-01-01`, inverter-input tag `DCI-E01-01-01`, string tag `STR01`. It also has a "maximum DC voltage" calculation table (module Voc, temperature coefficient, minimum temperature, giving 1,462.6 V).
- C&I string-inverter SLD [HVN p11]:
  - Strings S1..S9 are drawn vertically under each inverter. Each string has PV-module boxes at its top and bottom, with **+ve in red and −ve in blue**, grouped under MPPT-1..MPPT-4.
  - The inverter box is subdivided top to bottom into AC PROTECTION (SPD symbol), INVERTER SECTION (diagonal box, AC/DC), DC PROTECTION (SPD) and DC SWITCH.
  - Inverter output cables "1R X 4C X 35 Sq.mm 1.1KV AL Armd." and "1R X 4C X 25 Sq.mm 1.1KV AL Armd." run to an LT panel with 4P MCCBs/MCBs. The image is blurry, so these values are approximate: a ~130 A 25 kA 4P microprocessor-release MCCB as the incomer, ~63 A 10 kA 4P MCBs per inverter, a 16 A DP MCB and a 16 A socket with switch.
  - Earth strips "2R X 25X3 EARTH STRIP" are drawn in green.
  - An inverter data table lists inverter model (SUNGROW-33KTL-M), DC power 134.87 kWp, AC power 116 kWp, nominal MPP 585 V, MPP range 200-950 V, DC I/P current 82 A, AC O/P current 52 A.
  - LT-panel notes cover the "O/L, S/C & E/F protection" settings range, Ics = 100 % Icu, and 25 kA/50 kA breaking capacity.

---

## 5. Rooftop net-metering SLD (DISCOM format) [HES; TGR p27, p28]

- HESCOM chain, left to right then down [HES]:
  - Solar PV array → "DC combiner box" (DC fuse) → "DC distribution box" (DC SPD + DC disconnect switch) → "DC to AC Grid tied Inverter".
  - → "Manual disconnect Switch" → **"Uni-directional meter" (Solar Meter)** → "AC distribution box" (AC disconnect switch + AC surge protection device).
  - → "Consumer Distribution Panel" (to consumer loads) → **"Bi-directional Meter" (Net Meter)** → "Distribution Network / HESCOM GRID".
  - Each enclosure is a dashed box with its name under it.
- TGREDCO LT chain [TGR p27]: solar PV modules → protection device (per array) → junction box → inverter (DC + / −, then AC) → **isolation transformer** → manual disconnect → solar energy meter → interconnection point inside the consumer premises with an **auto relay** → bi-directional energy meter → service wire → electric grid.
- TGREDCO HT version [TGR p28]: the same up to the interconnection on the consumer LV side, then the existing transformer, then "HT Bi-Directional Energy Meter", then "Existing CTPT Set (11kV)/CTs, PTs (33kV)", then the grid.
- Both formats are block diagrams with pictorial icons, not CAD SLDs. For a DISCOM submission we must produce a proper CAD SLD that still shows these **mandatory items, labelled with these names**: generation (solar) meter, net/bi-directional meter, manual (lockable) AC disconnect accessible to the DISCOM, DC and AC SPDs, DC isolator, inverter anti-islanding, the isolation transformer where required, the interconnection point at the consumer DB/LT panel, and the CT/PT set for HT.

---

## 6. Array / site layout [CIL p88; IND p188; KHV p111; HVN p14]

- Site boundary: a thick bright-green polyline (the fence). The periphery road, street lighting and road shoulder are called out with leaders in a corner detail. [CIL p88]
- Each **block is drawn in its own colour**: the tables are hatched rectangles, and the same colour swatch appears in the summary table. Inverter stations are small rectangles with a leader to "BLOCK NO-x". [CIL p88]
- "MMS TABLE DETAILS" table columns: BLOCK NO | MODULE Wp (with colour swatch) | FULL TABLE | HALF TABLE | TOTAL | KW. The totals row is in red. [CIL p88]
- Inset "FULL TABLE TYPICAL ARRANGEMENT" with dimensions (62.29 m length, 7.25 m pitch, 1.50 gap) and a "HALF TABLE" inset. [CIL p88]
- North arrow: a large compass rose top-right [CIL p88] or a small one top-left [CIL p114, p116].
- A revision note in big italic text explains the change: "REVISION 1: Rev 0 was layout with 460 watts solar modules…" [CIL p88].
- Floating-PV and DPR layouts [IND p188]: island blocks are hatched grids with dimensioned spacing "20.0000". There is a string-block detail ("4 Strings in 1 block, 28 modules in series", dimensions 66.71 × 5.60), and the note "ALL DIMENSIONS ARE IN METER".
- Substation-area layout [KHV p111]: equipment boxes with ratings, "ROAD" as a hatched band, overall "SIZE - 276 M X 142 M" and "PROPOSE - 300 M X 150 M", and chain dimensions along the edges.

---

## 7. Earthing layout [CIL p116, p120, p121]

- The main earth grid is an orthogonal mesh with dimensioned typical spacing "6000(TYP.)" [CIL p116] or 4250 × 3550 [CIL p120]. It is drawn over a greyed-out underlay of the switchyard layout.
- **Each conductor size has its own linetype and colour**, and the BOQ legend repeats them [CIL p116 BOQ]:
  | Conductor | Use | Drawn as |
  | --- | --- | --- |
  | 75×6 mm GS flat | cable trench and main earth grid | green dash-dot |
  | 75×12 mm GS flat | structure, LA, counter and equipment earthing | magenta dash-dot |
  | 50×6 mm GS flat | MOM box, JB, BMK | orange dash-dot-dot |
  | 25×6 mm GS flat | rail earthing | cyan dashed |
  | 25×3 mm GS flat | gate and fence | light-orange dashed |
  | Flexible Cu braid | gate | |
  | Auxiliary earth mat for operator safety, 1.5 × 1.5 m at 300 mm spacing, below isolators and earth switches | | orange grid hatch |
- **Earth-pit symbols and tags** [CIL p116 legend and BOQ]:
  | Tag | Meaning | Symbol |
  | --- | --- | --- |
  | EP / EPN / EPB | treated pits (periphery, transformer neutral, transformer body): "40 MM DIA 3 MTR LONG GS PIPE (TREATED)" | circle with an X, orange |
  | EPT / ELA | untreated pits for PT and LA: "40MM DIA, 3M LONG MS ROD (UNTREATED)" | circle with an X, magenta |
  | LE | lightning pits | circle with a cross, cyan |

  Each pit is numbered (EP1.., ELA1.., LE1..). A BOQ table gives the quantity of each pit type and the metres of each conductor.
- Standard earthing notes [CIL p116]:
  - Installation confirms to **IS-3043**; earthing design per **IEEE-80**.
  - Locations are indicative.
  - The conductor runs 300 mm below a cable trench.
  - The main grid is at a minimum 600 mm below FGL.
  - Minimum spacing between electrodes is one electrode length.
  - The grid diverts around foundations.
  - Every equipment item and structure gets **two distinct earth connections**.
  - BMKs and JBs are earthed at two points.
  - The Cu flat for transformer neutral is in the vendor's scope.
  - Auxiliary mats go under isolator handles.
  - Cable trays and supports are earthed every 30 m.
  - Grid joints are welded; equipment joints are bolted.
  - Each fence post is earthed with 25×3 GS flat every 2.5 m.
- The details sheet [CIL p121] gives typical elevations for CB, BPI, PT, isolator, CT, LA (with surge counter), power transformer (Cu flat neutral), riser, JB/MK, fence, gate (flexible braid), aux mat, LCLM/tower and rail. It also has **"TYPICAL SECTION FOR UN-TREATED / TREATED EARTHING PIT AS PER IS-3043"**: CI hinged cover 400 mm, 100 mm PVC conduit, 40 mm × 3000 mm GS pipe, a homogeneous layer of coke/charcoal/salt/sand, and a bolted 75×6 joint detail.
- EKD / clamps BOM [CIL p118]: per-bay quantities of terminal connectors, T-clamps, PG clamps and insulator strings in a matrix with one column per bay (LINE BAY-103, 105, TRAFO BAY-102, 106, BUS COUPLER-104, BUS PT-101, 107, MAIN BUS, TOTAL). Circled item numbers on the plan are keyed to the table.

## 8. Lightning protection (LA) layout [HVN p14; CIL p114]

- Rooftop and C&I [HVN p14]: the roof plan carries the module layout. Each LA position gets a large **protection-radius circle** labelled "Lightning Arrestor-1, Radius covered 107 meters" (ESE-type). The north arrow sits in the top-left box and a key plan in the top-right.
- Switchyard [CIL p114]: an LCLM (lightning-cum-lighting mast) symbol and shield-wire peaks on towers. Shield-wire coverage lines are drawn as green dashed diagonals between the tower peaks. The BOQ table holds the switchyard equipment with quantities.

## 9. Cable schedule [LNT]

- The cover sheet [LNT p1] explains the **positional ID scheme** in boxes with leader explanations:
  - String & structure ID: `Bxx Ix Sxx Rxx xxxx IPxx` = Block, Inverter, SMB, structure Row, Structure no, SMB I/P no.
  - SMB ID: `SMB-Bxx-Ixx-Sxx`.
  - String cable ID: `Bxx Ix Sxx Rxx xxxx P x-Sq.mm` (P = power cable, then the cable size).
  - Notes: "The values in the cable schedule are indicative only. Cable laying shall be done after actual measurement at site."
  - UniCAD note: this describes the [LNT] sample. UniCAD does not copy its index-based IDs; its cable IDs start with the network tags of the equipment fed (e.g. `INV-01-SCB-02-R003-0027-IP05-4 sqmm` = inverter tag, combiner tag, structure row, structure no., input no., cable size; see `unicad.schedules.ids.SCHEME`), so one tag names the same equipment on the SLD, string diagrams and schedule.
- Block header [LNT p3]:
  - Module data: Wattage, Vmp, Imp, Voc, Isc, including a bifacial-gain row (×1.035).
  - Cable cross-section: string 4 sq.mm, SMB to inverter 400 sq.mm.
  - Resistance in Ω/km at 20 °C and 50 °C, with the note "Resistance of cable at 50°C is considered".
  - The voltage-drop formula, written out.
  - Average string Vd 0.50 %, DC cable Vd 0.66 %, total 1.16 %.
- Row columns [LNT p4]: SL.NO | CABLE IDENTIFICATION NO. (e.g. `B01- I1- S02- R003- 0027- IP05-4 sqmm`) | TYPE OF MODULE | MODULE WATTAGE | FROM (`B01- I1- S02- R003- 0027- IP05`) | TO (`SMB-B01-I1-S02`) | NO.OF RUNS | LENGTH (M) POSITIVE POLARITY | NEGATIVE POLARITY | Power loss (W) | Voltage drop (%).
- The header row has a light-blue fill and the table is fully ruled. The page footer reads "Sensitivity: LNT Construction Internal Use".

## 10. BESS / hybrid SLD [EDF p15 (US); KHV and CIL patterns for the MV side]

- BESS DC SLD E-117 [EDF p15]:
  - The PCS sits in a dashed box: "PCS-01, Power Electronics PCS 3,630 KVA 34.5 KV (TYP. FOR PCS NUMBERS 01-26)". It has 4 DC inputs, each with a ± load-break switch and fuses.
  - Each input goes to a dashed DC combiner box `CMB-01-01`, labelled "4,865KWH DC, 952-1,224VDC" and containing a 1500 A main switch, 100 A fuses per rack, and racks `DCR-01-01-01`… drawn as boxes with ±.
  - Battery spec under each combiner: "BATTERY TYPE: CATL 340S 1P, 952-1,224 VDC, 304.03 kWh, 62.2-80A, 17 MODULES PER RACK".
  - **The legend separates "INITIAL BATTERY RACK" (plain box) from "AUGMENTATION BATTERY RACK" (hatched box).**
  - An "OVERALL SYSTEM DESCRIPTION" table gives POI energy and power requirement, PCS nameplate at unity and at 0.93 PF, rack energy, total DC energy, and strings and modules at BOL and EOL.
  - Naming-convention note: `[EQUIP]-[PCS]-[INV/CMB]-[RCK]`.
- For Indian BESS SLDs, use the [CIL] and [KHV] MV-side conventions: 33 kV ICOG/RMU, then a PCS transformer (vector group, Z, taps), then the 33 kV switchboard, then the pooling transformer, then POI metering (ABT). This is an inference; we have no Indian BESS sample.

## 11. Symbols (how they are drawn)

| Item | Drawing | Source |
| --- | --- | --- |
| Circuit breaker (VCB/SF6) | Small square with "52" (or a plain square); **draw-out chevrons «» above and below** for withdrawable VCBs; an anti-pumping "94" box beside it | [CIL p42, p44] |
| Isolator / disconnector | Blade at an angle with a pivot dot. "WITH 1ES" adds an earth-switch blade to a ground symbol. DBCR (double-break centre-rotating) has a centre dot. | [CIL p44; KHV legend 2-5; IEC p15] |
| CT | Semicircle loop(s) on the line, one per core. P1/P2 primary and S1/S2 secondary marks. Cores stacked with ratio/class text. | [CIL p42, p44; KHV] |
| PT/VT | Two or three interlocking circles with a star mark, fused (HRC fuse) secondaries | [CIL p44; IEC p40] |
| Transformer | Interlocking circles, with Δ/Y marks inside for windings. A **5-winding IDT is drawn as one Y circle above four Δ circles in a row**. | [CIL p40, p42] |
| Surge arrester / LA | Arrow-in-rectangle on a stub with an earth symbol; gapless ZnO | [CIL legend 1, p44 item 8; KHV legend 1; IEC p18] |
| Inverter / PCU | Square with a diagonal; "=" lower-left, "~" upper-right | [CIL p40, p42; TGR; EDF; IEC p42] |
| PV module | Small square labelled "M1"/"M2" | [CIL p40] |
| PV module (string drawing) | Box labelled "PV" | [HVN] |
| Fuse | Rectangle with a line through it, in line with the conductor | [CIL p40; EDF] |
| SPD | Varistor box, with or without an arrow, to earth | [HES; HVN] |
| Earth | Three decreasing horizontal lines | [IEC p11; HVN] |
| Meter | Circle with an arrow; the bi-directional meter has a double arrow | [TGR] |
| MFM | Ovals with kW/kVAr/kWh/kVArh | [CIL] |
| Relay | Circle with the ANSI number | [CIL; KHV] |
| Cable marker | Small ellipse on the line, with a leader to an underlined callout | [CIL p40, p42] |
| Feeder direction | Filled arrowhead at a feeder end: "TO …" or "FROM …" | [CIL p44] |
| Busbar | Heavier line weight than feeders, with the full rating text along it | [CIL; KHV] |

Every SLD carries a **LEGEND table** (SL.NO | SYMBOL | DESCRIPTION, with QUANTITY on the HV SLD) that covers only the symbols used on that sheet. [CIL p40, p42, p44; KHV p109]

## 12. DXF structure seen [IOM, AutoCAD Electrical]

- Layers: `SYMS`, `WIRES`, `TAGS`, `DESC`, `LOC`, `TERMS`, `XREF`, `XREFCHILD`, `DESCCHILD`, `LINK`, `WIREFIXED`, `WIRENO`, `WIREREF`, `WIREREF_DEST`, `POS`, `WIRECOPY`, `MISC`, `TEXT`, `TEXT2`, `INFORBORDER` (border and title), `REV`, `OBJ`, `BM`, `ASHADE`, `DEFPOINTS`. The AutoCAD Electrical WD_M block carries the drawing-wide attributes.
- Blocks use the AcadE naming scheme: first letter V or H for vertical or horizontal orientation, then the device family. Examples: `VCB11TH` breaker, `HXF1CT` CT, `VXF1T1_1-` transformer, `VFU1`/`HFU1` fuse, `HGND2` ground, `VDS11F` disconnect, `WDDOT` wire junction dot, `ARRESTER`, `DEVICE_BUBBLE` (ANSI relay circle).
- Ratings live in attributes on the device block (e.g. FEEDER_CELL attributes `17.5KV`, `3150A`, `40KA`, `52-GEN`) and in MTEXT next to it.
- Text styles: `ROMANS.SHX`, `WD_IEC` → `ISOCP2.shx`, arial. `$INSUNITS = 1` (inches, US).
- We have no Indian DXF. The [CIL] PDFs show colour-by-function on layouts (fence green, earthing by conductor size, block hatches by colour). This implies a layer per conductor type and per block.

---

## Must-replicate shortlist

1. Use an **A0 SLD sheet** with a zone-marked border, a right column (LEGEND, then SYSTEM PARAMETERS, then title block) and a bottom band (GENERAL NOTES, then REV table). Use the Indian title-block fields: Owner, EPC, Project title, Location, **LOA No**, Drawing title, Dwg No, Drawn/Checked/Approved/Date/Scale/Sheet "n Of m"/Paper/Rev, and **Drawing issued for** (Proposal / Information / Approval / As built). [CIL]
2. Write cable callouts as `<grade>, <runs>R X <cores>C X <size> Sq.mm <Al./Cu> XLPE Armoured`, joined to the cable with an ellipse tick. Write string cables as "4 Sq mm Cu". [CIL; HVN]
3. Annotate each device in stacked text with all its ratings. For CTs and PTs give per-core ratio, class and VA. For transformers give MVA, vector group, ratio, cooling, Z and tap range. For busbars give the full spec string. [CIL; KHV]
4. Draw protection as ANSI numbers in circles plus a legend mapping them, and draw the MFM, annunciator and indication-lamp rows on HT feeders. [CIL; KHV]
5. On HV yards, tag devices by bay (`103-89L1`, `103-52`), give each bay a titled box, and use a BOM legend with quantities. [CIL p44]
6. Show repeated blocks either in full or as "n identical blocks", with per-unit DC kWp and module counts. Add a plant summary table on the DC SLD. [CIL p40; IND]
7. For rooftop and DISCOM sheets, include and label the generation meter, the bi-directional net meter, the manual disconnect, DC and AC SPDs, the DC isolator, the isolation transformer where required, and the interconnection point. [HES; TGR]
8. On the earthing layout, give each conductor size its own linetype and colour. Draw pit symbols by type (treated, untreated, lightning) with tags EP/EPN/EPB/ELA/EPT/LE. Add a BOQ table with quantities and the standard IS-3043 / IEEE-80 note set. Add the IS-3043 pit section details. [CIL p116, p121]
9. On the array layout, colour each block, add the MMS table summary (full and half tables, kW per block), the typical table arrangement inset, the north arrow and the fence and road callouts. [CIL p88]
10. In the cable schedule, use positional IDs (Block-Inverter-SMB-Row-Structure-Input-Size), separate + and − lengths, power loss and voltage drop per row, and a header with the R at 50 °C basis and the formula. [LNT]
11. On BESS sheets, separate initial racks from augmentation racks, and add a BOL/EOL system table. [EDF]

## Gaps (still need samples)
- A real Indian C&I rooftop SLD in CAD quality, LT net metering at 415 V with an ACDB, MCCB ratings and CT-operated meters.
- An Indian rooftop earthing and LA layout at readable resolution.
- An Indian BESS / hybrid SLD.
- A 132 kV yard in vector form (the [IND] SLD is raster).
- Any Indian-practice DXF with layer names.
- IS 12032 itself, which is not freely downloadable. We have IEC 60617 only.

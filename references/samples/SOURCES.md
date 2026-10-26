# Sample drawing sources

All files fetched on 2026-10-26. Use them only as internal references for conventions. Do not redistribute or ship them. Several carry proprietary notices (see the Licence column). None has an open licence except the MIT SVG symbols.

"Subset" means we kept only the drawing pages of a larger PDF. The page numbers refer to the original PDF. Rendered PNGs are in `pages/<dir>/`. Tiles `_tRC.png` are row/column crops of large sheets.

## Kept

| File (under `raw/`) | Source URL | Licence / terms | Content | Pages kept / PNG dir |
| --- | --- | --- | --- | --- |
| `utility/CIL-100MW-Gujarat_Refex_EPC-drawings_SLD-DC-AC-66kV_layout_earthing.pdf` | https://d3u7ubx0okog7j.cloudfront.net/documents/APPENDIX_20_DRAWING_compressed.pdf | No licence. Published as a tender appendix. Each sheet says "property of Refex Energy Ltd. It should not be copied or modified without our permission". | Real EPC drawing set for Coal India's 100 MWac / 146.5 MWp plant in Gujarat (Refex/Svaryu): DC SLD, 33 kV AC SLD, 66 kV pooling-substation SLD, array layout, 66 kV switchyard layout, earthing design and earthmat layout, EKD/BOM, title blocks. **Best sample.** | Subset p1,2,7,39-44,88,114-121 (121-page original). `pages/cil100/` |
| `utility/NHPC-Khavda-200MW_tender_33kV-SLD_and_plant-layout.pdf` | https://www.nhpcindia.com/welcome/download_tender/1689681992t.pdf | No licence. NHPC public tender (669 pages). | Tender schematic SLD of the GSECL Khavda 1964 MW south-block 33/400 kV GIS pooling substation (by Tatva for GERMI), colour-coded by voltage, legend and meter tables. Also a GIS block layout. | Subset p109, p111. `pages/khavda/` |
| `cable-schedule/gprojensure_35MWac_DC-cable-schedule.pdf` | https://gprojensure.com/uploads/7fd8a4855422439f448ca8e0a68f3958.pdf | No licence. The cover says "property of L&T Construction ... must not be passed on to any third party". | L&T DC cable schedule (51 pages, A4 & A3) for the GSECL 35 MWac + 57 MWh BESS plant at KLTPS Panandhro: an ID-scheme cover sheet and a voltage-drop table per string. | Full file. `pages/cablesched/` (p1-4) |
| `rooftop/HESCOM_SRTPV_net-metering_schematic-SLD.pdf` | https://srtpv.hescom.co.in/SRTPV/document/Schematic%20Diagram%20Net%20metering.pdf | No licence. Published by the DISCOM. | HESCOM (Karnataka) block-style SLD of a rooftop net-metering connection. | Full (1 page). `pages/hescom/` |
| `rooftop/TGREDCO_net-metering-guidelines_LT-HT-schematics.pdf` | https://tgredco.telangana.gov.in/PDFs/Solar_Energy/Solar_Rooftop_Net_metering_Guidelines.pdf | No licence. Telangana state agency guideline (scanned). | Pictorial LT and HT net-metering schematics, plus the application annexure. | Subset p23,27,28. `pages/tgredco/` |
| `rooftop/HeavenSolar_brochure_CI-rooftop_SLD_cable-layout_earthing-LA-layout.pdf` | https://5.imimg.com/data5/JB/OG/MY-33921560/commercial-solar-power-plant-3d-video-design-sketchup-expert-heaven-solar.pdf | No licence. Marketing brochure from an Indian design firm. Images are watermarked and low resolution (about 1000 px). | Screenshots of a real C&I rooftop SLD (3 Sungrow string inverters to an LT panel), a PV/cable layout, and an earthing & LA layout with protection-radius circles. | Subset p11,12,14. `pages/heaven/` |
| `dpr/Indravati-160MW-FPV_DPR_annexure-I_layout-and-SLD.pdf` | https://www.cecp-eu.in/uploads/documents/events/65/DPR_Indravati_final_V4_total_2.pdf | EU-funded DPR with an EU disclaimer. No licence. | Floating PV DPR (Odisha): an indicative 132/33 kV SLD for 160 MW (4 × 50 MVA) and a general layout of floating islands with an A3/A1 title strip. Raster images, medium quality. | Subset p162,164,165,166,188,189. `pages/indravati/` |
| `international/EDF-MorrisRidge-US_part1_DC-SLD_AC-SLD_sheets.pdf` | https://www.edf-re.com/wp-content/uploads/005C_Appendix-05-B.-Preliminary-Electrical-Design-Drawings_Part-1-of-2.pdf | No licence. Public US permitting filing (WSP for EDF Renewables). | US 34.5 kV PV DC SLD (combiners, string IDs) and AC SLD. Not an Indian sample: use it only for structure and naming. | Subset p3, p5. `pages/morris/mr1_*` |
| `international/EDF-MorrisRidge-US_part2_BESS-PCS-SLD_MV-collection.pdf` | https://www.edf-re.com/wp-content/uploads/005D_Appendix-05-B.-Preliminary-Electrical-Design-Drawings_Part-2-of-2.pdf (link found on https://www.edf-re.com/flipbook/3308) | Same as part 1. | BESS DC SLD E-117 (PCS, DC combiners, battery racks, BOL/EOL augmentation) and a sheet of the 34.5 kV MV collection. Our only BESS sample. | Subset p14, p15. `pages/morris/mr2_*` |
| `symbols/IEC60617_symbols_chart_qelectrotech.pdf` | https://qelectrotech.org/forum/misc.php?action=pun_attachment&item=2124&download=1 | No licence. A forum attachment. The symbols themselves are IEC copyright. | A 53-page IEC 60617 symbol table (symbol, description, comment). | Full. `pages/iec/` (p11 earth, p15 switch-disconnector/isolator, p18 surge arrester, p34-35 transformers/CT, p42 inverter/converter, p44-45 meters) |
| `symbols/ng-diagram-sld-iec60617-svg/*.svg` | https://github.com/synergycodes/ng-diagram-single-line-diagram (`src/assets/symbols/`, branch main) | **MIT** (`LICENSE` copied). | 15 IEC-style SLD symbols as SVG: breaker, disconnector, earthing switch, CT, VT, CVT, transformer (2- and 3-winding), surge arrester, ground, relay, and others. | Full. |
| `dxf/SLD_Automation/*.dxf` | https://github.com/AmmarIsmailBaig/SLD_Automation (`cad_files/`, branch main) | **No licence in the repo**, so all rights are reserved. The sheets carry an "IOM Components proprietary" notice. | Real MV switchgear one-line DXFs (US/ANSI, 13.8 kV and 4.16 kV) drawn in AutoCAD Electrical, plus a symbol-block library. Our only DXFs: useful for layer, block and attribute naming, not for Indian conventions. | `8372+A01-050-001.dxf`, `8513+A01-000-052.dxf`, `symbol_library.dxf`, `feeder_cell_library.dxf`. Previews in `pages/dxf/` |

## Fetched but not kept (no drawings, or not useful)

| URL | Result |
| --- | --- |
| https://www.eqmagpro.com/wp-content/uploads/2020/11/Tendernotice_1-3_compressed-11-20.pdf | NTPC Gandhar 20 MW BOS spec, 10 pages of text. The SLD enclosure is not included. |
| https://cdkn.org/sites/default/files/files/Up-Karnataka-Solar-park-DPR-1.pdf | Karnataka 2000 MW park DPR. The annexures "Indicative SLD 50 MW" and "220 kV pooling SLD" are only title pages; the drawings are missing from the PDF. |
| https://germi-public.s3.ap-south-1.amazonaws.com/ProjectReport/20160825%20RTPV%20Best%20Practices%20Manual%20Master%20Ver%203.2.pdf | GERMI rooftop best-practice manual. Text only; it lists the drawings needed (SLD, earthing layout with spec). |
| https://d3u7ubx0okog7j.cloudfront.net/documents/specification_tender_No_2122020001.pdf | NLC-related solar spec (441 pages). Its drawings are civil only (fence, gate, security cabin, parking shed); NLC title block, A3. |
| https://jmkresearch.com/wp-content/uploads/2023/12/BSPGCL-185-MW-Solar-with-254-MWh-BESS-EPC-Bihar-Dec-2023.pdf | BSPGCL solar + BESS tender, 342 pages, no drawing pages. |
| https://www.seci.co.in/uploads/tenders/Annexure_B_to_the_BoS_RfP_for_600_MW_with_12000_MWh_BESS.pdf | SECI BESS BoS scope, text only. |
| https://www.iimb.ac.in/sites/default/files/inline-files/Tender-Solar-Plant-2025.pdf | IIMB rooftop tender, text only. It requires an "SLD, layout plan, earthing layout". |
| https://quezoncity.gov.ph/wp-content/uploads/2026/06/5.-ENGINEERING-26-BLDG-00054-SECTION-VII.-DRAWINGS.pdf | 63 MB scan, unreadable (no OCR). Deleted. |
| https://www.cadforum.cz/catalog_en/block.asp?blk=9046 | HTML page only. The DWG download link needs a login. |

## Unreachable

| URL | Result |
| --- | --- |
| https://www.seci.co.in/Upload/Tender/SECI000143-2404882-TechnicalTenderDocument.pdf | HTTP 500 |
| https://www.duquesnelight.com/docs/default-source/pdf-library//example-single-line.pdf | Returned a 212-byte HTML stub |
| Scribd documents found by search (e.g. "80.04 kWp LA layout", "MCR earthing layout", "NTPC Badhla 260 MW SLD") | Need a login. Not attempted. |

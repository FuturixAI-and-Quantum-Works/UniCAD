import json
import sys
from pathlib import Path

from unicad.catalog.store import load_catalog
from unicad.domain.project import Project
from unicad.store import canonical_json

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "fixtures" / "projects"
cat = load_catalog()


def node(id, tag, kind, **kw):
    return {"id": id, "tag": tag, "kind": kind, **kw}


def edge(id, src, dst, service, **kw):
    return {"id": id, "src": src, "dst": dst, "service": service, **kw}


def rev0(remarks="FIRST SUBMISSION"):
    return [{"rev": "00", "date": "2026-10-26", "remarks": remarks, "issued_for": "approval"}]


DC_DERATING = "Derating 1.0: Polycab H1Z2Z2-K ambient factor is 1.00 up to 60 C (polycab_solar_dc_en50618_derating.csv); grouping not applied"
DC_PASS_THROUGH = "One cable pair per string: the DC fuse box passes strings through to separate inverter inputs"

# ---------------- rooftop 500 kWp ----------------
MOD = "adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp"
INV = "huawei-sun2000-100ktl-m2"
m = cat.get(MOD)
STR_LEN, STR_PER_INV, N_INV = 18, 10, 5

roof_nodes = [
    node("str", "STR-{0:02d}-{1:02d}", "pv_string", spec=MOD, count=STR_PER_INV, group="inv_sec", modules_in_series=STR_LEN),
    node("dcfb", "DCFB-{0:02d}", "dc_fuse_box", group="inv_sec"),
    node("dcspd", "DC-SPD-{0:02d}", "dc_spd", spec="finder-7p.23.9.500.x015", group="inv_sec"),
    node("dciso", "DC-ISO-{0:02d}", "dc_isolator", spec="abb-otdc250e11", group="inv_sec"),
    node("inv", "INV-{0:02d}", "inverter", spec=INV, group="inv_sec"),
    node("inv_mccb", "MCCB-{0:02d}", "breaker", spec="lk-dsine-dn1-250-c-200a-4p", group="inv_sec", voltage_v=415.0),
    node("acdb", "ACDB-01", "acdb", voltage_v=415.0, title="AC DISTRIBUTION BOARD"),
    node("acdb_acb", "ACB-01", "breaker", spec="schneider-mtz2-n1-1000a-4p", voltage_v=415.0),
    node("solar_ct", "CT-01", "ct", voltage_v=415.0),
    node("solar_meter", "SM-01", "meter", spec="generic-meter-generation-ct-0.5s", ports=["sig"], title="SOLAR GENERATION METER"),
    node("ac_spd", "AC-SPD-01", "ac_spd", spec="finder-7p.25.8.275.x020", voltage_v=415.0),
    node("ac_iso", "AC-ISO-01", "isolator", spec="abb-ot1000e04p", voltage_v=415.0),
    node("ltp", "LTP-01", "lt_panel", voltage_v=415.0, title="CONSUMER LT PANEL 415 V"),
    node("load", "LOAD-01", "load", voltage_v=415.0, title="CONSUMER LOADS"),
    node("net_ct", "CT-02", "ct", voltage_v=415.0),
    node("net_meter", "NM-01", "meter", spec="generic-meter-net-ct-0.5s", ports=["sig"], title="DISCOM NET METER"),
    node("poi", "POI-01", "poi_grid", voltage_v=415.0, title="DISCOM LT SUPPLY 415 V"),
    node("ep", "EP-01", "earth_electrode", title="SYSTEM EARTH PIT"),
]
roof_edges = [
    edge("e_str", "str:out", "dcfb:in", "dc", cable="polycab-h1z2z2-k-4mm2", length_m=25.0, laying="tray", derating=1.0, remarks=DC_DERATING),
    edge("e_dcfb_spd", "dcfb:out", "dcspd:line", "dc"),
    edge("e_dcfb_iso", "dcfb:out", "dciso:in", "dc", cable="polycab-h1z2z2-k-6mm2", runs=STR_PER_INV, length_m=5.0, laying="tray", derating=1.0, remarks=DC_PASS_THROUGH),
    edge("e_iso_inv", "dciso:out", "inv:dc", "dc", cable="polycab-h1z2z2-k-6mm2", runs=STR_PER_INV, length_m=3.0, laying="tray", derating=1.0, remarks=DC_PASS_THROUGH),
    edge("e_inv_mccb", "inv:ac", "inv_mccb:in", "lv_ac", cable="polycab-lt-xlpe-al-4c-95-arm", length_m=30.0, laying="tray"),
    edge("e_mccb_acdb", "inv_mccb:out", "acdb:in", "lv_ac"),
    edge("e_acdb_acb", "acdb:out", "acdb_acb:in", "lv_ac"),
    edge("e_acb_ct", "acdb_acb:out", "solar_ct:in", "lv_ac", cable="polycab-lt-xlpe-al-1c-400-arm", runs=3, length_m=60.0, laying="tray", remarks="Roof ACDB to the solar metering and isolation panel in the LT room"),
    edge("e_ct_spd", "solar_ct:out", "ac_spd:line", "lv_ac"),
    edge("e_ct_iso", "solar_ct:out", "ac_iso:in", "lv_ac"),
    edge("e_iso_ltp", "ac_iso:out", "ltp:in", "lv_ac", cable="polycab-lt-xlpe-al-1c-400-arm", runs=3, length_m=5.0, laying="tray"),
    edge("e_ltp_load", "ltp:out", "load:in", "lv_ac", remarks="Existing consumer distribution, by others"),
    edge("e_ltp_ct", "ltp:out", "net_ct:in", "lv_ac"),
    edge("e_ct_poi", "net_ct:out", "poi:in", "lv_ac", remarks="DISCOM service connection, by others"),
    edge("c_solar_meter", "solar_ct:sec", "solar_meter:sig", "control"),
    edge("c_net_meter", "net_ct:sec", "net_meter:sig", "control"),
    edge("pe_inv", "inv:pe", "ep:pe", "earth"),
    edge("pe_acdb", "acdb:pe", "ep:pe", "earth"),
    edge("pe_lt", "ltp:pe", "ep:pe", "earth"),
]

# Roof layout: one table of 2 rows x 18 portrait slots per pair of strings.
slot_w, slot_h = (m.width + 20) / 1000, (m.length + 20) / 1000
tables, strings = [], []
n_tables = N_INV * STR_PER_INV // 2
for t in range(n_tables):
    tables.append({
        "id": f"T{t + 1:02d}",
        "mounting": "fixed_tilt",
        "origin": {"x": round((t % 5) * (STR_LEN * slot_w + 1.5), 3), "y": round((t // 5) * (2 * slot_h * 0.94 + 2.0), 3)},
        "azimuth_deg": 180.0,
        "tilt_deg": 10.0,
        "rows": 2,
        "cols": STR_LEN,
        "orientation": "portrait",
        "module": MOD,
        "slot_width_m": round(slot_w, 4),
        "slot_height_m": round(slot_h, 4),
    })
k = 0
for i in range(1, N_INV + 1):
    for s in range(1, STR_PER_INV + 1):
        strings.append({"string": f"str@{i}.{s}", "spans": [{"table": f"T{k // 2 + 1:02d}", "row": k % 2, "col": 0, "n": STR_LEN}]})
        k += 1

rooftop = {
    "id": "rooftop-500kwp",
    "name": "500 kWp Rooftop Solar PV Plant (Net Metering)",
    "client": {"name": "Demo Industries Pvt Ltd", "code": "DIPL"},
    "site": {"name": "Demo Industries factory roof", "state": "Karnataka", "address": "Demo industrial area (illustrative fixture)"},
    "design_basis": {
        "ambient_min_c": 10.0,
        "ambient_max_c": 45.0,
        "cell_temp": {"model": "noct"},
        "altitude_m": 900.0,
        "grid": {"voltage_v": 415.0, "frequency_hz": 50.0, "phases": 3, "utility": "DISCOM"},
        "vdrop_dc_max": 0.02,
        "vdrop_ac_max": 0.02,
        "dc_ac_ratio_target": 1.0,
        "power_factor": 0.99,
        "assumptions": [
            "Fixture values, not site data: ambient_min_c, ambient_max_c, altitude_m.",
            "vdrop_dc_max, vdrop_ac_max, power_factor and dc_ac_ratio_target are illustrative design targets, not cited limits.",
            "Cell temperature uses the module NOCT from the catalog.",
        ],
    },
    "company_profile": "default",
    "segment": "ci_rooftop",
    "network": {
        "groups": [{"id": "inv_sec", "tag": "INVERTER SECTION-{0:02d}", "count": N_INV, "title": "100 kW STRING INVERTER SECTION"}],
        "nodes": roof_nodes,
        "edges": roof_edges,
    },
    "array_layout": {"blocks": [{"id": "roof1", "name": "MAIN SHED ROOF", "tables": tables}], "strings": strings},
    "sheets": [
        {"id": "sld", "number": "UDEMO-DIPL-RT500-EE-001", "kind": "sld", "title": "SINGLE LINE DIAGRAM 500 kWp", "paper": "A3", "revisions": rev0()},
        {"id": "string", "number": "UDEMO-DIPL-RT500-EE-002", "kind": "string_diagram", "title": "STRING DIAGRAM INV-01", "paper": "A3", "scope": {"nodes": ["inv"]}, "revisions": rev0()},
        {"id": "layout", "number": "UDEMO-DIPL-RT500-EE-003", "kind": "layout", "title": "MODULE LAYOUT", "paper": "A3", "scale": "1:200", "revisions": rev0()},
        {"id": "cables", "number": "UDEMO-DIPL-RT500-EE-004", "kind": "cable_schedule", "title": "CABLE SCHEDULE", "paper": "A3", "revisions": rev0()},
    ],
    "revisions": rev0(),
}

# ---------------- utility 50 MWac ----------------
UMOD = "vikram-solar-hypersol-m10r-144-580-wp"
UINV = "sma-sc-4400-up"
N_BLOCK, INV_PER_BLOCK, SCB_PER_INV, STR_PER_SCB, U_STR_LEN = 6, 2, 15, 24, 27

MV_CABLE_BLOCK = "havells-ht-xlpe-al-3c-120-33kv-arm"
MV_CABLE_AUX = "havells-ht-xlpe-al-3c-50-33kv-arm"

BHEL_IDT = "references/catalog-sources/raw/datasheets/transformers/BHEL_PS439-1304_5.5MVA_33kV_IDT_spec_via_indembassysweden.pdf"
IDT_LV_KV = cat.get(UINV).ac_voltage / 1000
IDT_SPEC = {
    "kind": "transformer",
    "id": "idt-8800kva-33-0.66-0.66-ynd11d11-project",
    "manufacturer": "Project-specific",
    "model": "8800 kVA 33/0.66-0.66 kV inverter-duty transformer",
    "source": {
        "kind": "manual",
        "ref": f"Project-specific. LV voltage = ac_voltage of catalog {UINV}. %Z, vector group, LV1-LV2 impedance and OCTC range borrowed from "
               f"BHEL purchase specification PS 439-1304 (5.5 MVA, 33 kV) {BHEL_IDT} p.5; "
               "references/catalog-sources/extracted/idt_impedance_citations.csv. Not a vendor guarantee for this rating.",
        "retrieved": "2026-10-26",
    },
    "rated_kva": 8800,
    "hv_kv": 33.0,
    "lv_kv": [IDT_LV_KV, IDT_LV_KV],
    "vector_group": "YNd11d11",
    "impedance_pct": 7.0,
    "cooling": "ONAN",
    "standard": "IS 2026",
    "winding_kva": [4400, 4400],
    "impedance_base_kva": 4400,
    "lv_lv_impedance_min_pct": 11.0,
    "tap_changer": "octc",
    "tap_range_pct": [-10.0, 10.0],
    "tap_step_pct": 2.5,
}

u_nodes = [
    node("str", "STR-{0:02d}-{1}-{2:02d}-{3:02d}", "pv_string", spec=UMOD, count=STR_PER_SCB, group="scb_sec", modules_in_series=U_STR_LEN),
    node("scb", "SCB-{0:02d}-{1}-{2:02d}", "scb", group="scb_sec"),
    node("inv", "INV-{0:02d}-{1}", "inverter", spec=UINV, group="inv_sec"),
    node("inv_dcspd", "DC-SPD-{0:02d}-{1}", "dc_spd", spec="finder-7p.23.9.500.x015", group="inv_sec"),
    node("inv_acspd", "AC-SPD-{0:02d}-{1}", "ac_spd", spec="dehn-dg-se-ci-440-fm", group="inv_sec", voltage_v=660.0),
    node("idt", "IDT-{0:02d}", "transformer", spec=IDT_SPEC, group="block"),
    node("blk_aux", "BLK-AUX-TR-{0:02d}", "aux_transformer", spec="is1180-33kv-63kva-dyn11", group="block"),
    node("blk_aux_load", "BLK-AUX-LOAD-{0:02d}", "load", group="block", voltage_v=415.0, title="BLOCK AUXILIARY ACDB"),
    node("icog_vcb", "ICOG-VCB-{0:02d}", "vcb", spec="abb-vd4-36kv-630a-20ka", group="block", voltage_v=33000.0),
    node("icog_la", "ICOG-LA-{0:02d}", "lightning_arrester", spec="abb-polim-d-24-ur30kv", group="block", voltage_v=33000.0),
    node("mv_bus", "MV-BUS-01", "busbar", group="mvsb", voltage_v=33000.0, title="33 kV MAIN HT SWITCHBOARD BUS"),
    node("mv_pt", "MV-PT-01", "pt", spec="generic-pt-33kv-wbsedcl-0.5-3p-100va", group="mvsb", voltage_v=33000.0),
    node("aux_vcb", "VCB-AUX-01", "vcb", spec="abb-vd4-36kv-630a-20ka", group="mvsb", voltage_v=33000.0),
    node("aux_tr", "AUX-TR-01", "aux_transformer", spec="is1180-33kv-100kva-dyn11", group="mvsb"),
    node("aux_load", "AUX-LOAD-01", "load", voltage_v=415.0, title="PLANT AUXILIARY / CMCS ACDB"),
    node("out_vcb", "VCB-OG-01", "vcb", spec="abb-vd4-36kv-1250a-25ka", group="mvsb", voltage_v=33000.0),
    node("out_ct", "CT-OG-01", "ct", group="mvsb", voltage_v=33000.0),
    node("out_relay", "REL-OG-01", "relay", group="mvsb"),
    node("abt_main", "ABT-M-01", "meter", spec="generic-meter-abt-ct_pt-0.2s", ports=["sig"], group="mvsb"),
    node("abt_check", "ABT-C-01", "meter", spec="generic-meter-check-ct_pt-0.2s", ports=["sig"], group="mvsb"),
    node("out_la", "LA-OG-01", "lightning_arrester", spec="generic-la-36kv-ur30-20ka-counter", voltage_v=33000.0),
    node("poi", "POI-01", "poi_grid", voltage_v=33000.0, title="33 kV EXPORT LINE TO GRID SUBSTATION"),
    node("ep", "EP-MAIN", "earth_electrode", title="MAIN EARTH GRID"),
]
u_edges = [
    edge("e_str", "str:out", "scb:in", "dc", cable="polycab-h1z2z2-k-4mm2", length_m=30.0, laying="air"),
    edge("e_scb_inv", "scb:out", "inv:dc", "dc", cable="polycab-h1z2z2-k-240mm2", runs=2, length_m=150.0, laying="buried_direct"),
    edge("e_scb_dcspd", "scb:out", "inv_dcspd:line", "dc"),
    edge("e_inv_idt", "inv:ac", "idt:lv", "lv_ac", cable="kei-lt-xlpe-al-1c-400-arm", runs=8, length_m=15.0, laying="trench"),
    edge("e_inv_acspd", "inv:ac", "inv_acspd:line", "lv_ac"),
    edge("e_idt_aux", "idt:hv", "blk_aux:hv", "mv_ac", cable=MV_CABLE_AUX, length_m=10.0, laying="buried_direct"),
    edge("e_aux_load", "blk_aux:lv", "blk_aux_load:in", "lv_ac", cable="polycab-lt-xlpe-al-4c-35-arm", length_m=10.0, laying="tray"),
    edge("e_idt_vcb", "idt:hv", "icog_vcb:in", "mv_ac", cable=MV_CABLE_BLOCK, length_m=10.0, laying="buried_direct"),
    edge("e_icog_la", "idt:hv", "icog_la:line", "mv_ac"),
    edge("e_icog_bus", "icog_vcb:out", "mv_bus:bus", "mv_ac", cable=MV_CABLE_BLOCK, length_m=400.0, laying="buried_direct", remarks="Block to main HT switchboard; length is a fixture assumption"),
    edge("e_bus_pt", "mv_bus:bus", "mv_pt:in", "mv_ac"),
    edge("e_bus_aux", "mv_bus:bus", "aux_vcb:in", "mv_ac"),
    edge("e_auxvcb_tr", "aux_vcb:out", "aux_tr:hv", "mv_ac", cable=MV_CABLE_AUX, length_m=20.0, laying="buried_direct"),
    edge("e_auxtr_load", "aux_tr:lv", "aux_load:in", "lv_ac", cable="polycab-lt-xlpe-al-4c-70-arm", length_m=20.0, laying="tray"),
    edge("e_bus_og", "mv_bus:bus", "out_vcb:in", "mv_ac"),
    edge("e_og_ct", "out_vcb:out", "out_ct:in", "mv_ac"),
    edge("e_ct_la", "out_ct:out", "out_la:line", "mv_ac"),
    edge("e_ct_poi", "out_ct:out", "poi:in", "mv_ac", remarks="33 kV export line, route by others"),
    edge("c_ct_relay", "out_ct:sec", "out_relay:sig", "control"),
    edge("c_ct_abt", "out_ct:sec", "abt_main:sig", "control"),
    edge("c_ct_check", "out_ct:sec", "abt_check:sig", "control"),
    edge("c_pt_abt", "mv_pt:sec", "abt_main:sig", "control"),
    edge("c_pt_check", "mv_pt:sec", "abt_check:sig", "control"),
    edge("pe_idt", "idt:pe", "ep:pe", "earth"),
    edge("pe_inv", "inv:pe", "ep:pe", "earth"),
    edge("pe_scb", "scb:pe", "ep:pe", "earth"),
]
utility = {
    "id": "utility-50mwac",
    "name": "50 MWac Ground-Mounted Solar PV Plant",
    "client": {"name": "Demo Renewables Ltd", "code": "DRL"},
    "site": {"name": "Demo solar park (illustrative fixture)", "state": "Gujarat"},
    "design_basis": {
        "ambient_min_c": 5.0,
        "ambient_max_c": 50.0,
        "cell_temp": {"model": "pvsyst", "uc": 29.0, "uv": 0.0},
        "altitude_m": 50.0,
        "grid": {"voltage_v": 33000.0, "frequency_hz": 50.0, "phases": 3, "utility": "STU"},
        "vdrop_dc_max": 0.015,
        "vdrop_ac_max": 0.01,
        "dc_ac_ratio_target": 1.3,
        "power_factor": 0.95,
        "assumptions": [
            "Fixture values, not site data: ambient_min_c, ambient_max_c, altitude_m.",
            "PVsyst thermal factors uc=29, uv=0 are the PVsyst free-standing default, used here as an assumption.",
            "vdrop_dc_max, vdrop_ac_max, power_factor and dc_ac_ratio_target are illustrative design targets, not cited limits.",
            "12 x SC 4400 UP give 52.8 MVA; export is assumed limited to 50 MWac at the POI by the plant controller.",
            "IDT-nn is a project-specific inline spec (idt-8800kva-33-0.66-0.66-ynd11d11-project), not a catalog entry: 8800 kVA as two 4400 kVA LV windings, one per SC 4400 UP; LV voltage 0.66 kV is the catalog ac_voltage of the SC 4400 UP.",
            "Borrowed for the IDT from BHEL purchase specification PS 439-1304 (5.5 MVA, 33 kV inverter-duty transformer, p.5): %Z 7 % (stated on the 2.75 MVA winding base, applied here on the 4400 kVA winding base), vector group YNd11d11, LV1-LV2 impedance min 11 %, OCTC -10 to +10 % in 2.5 % steps, each LV winding 50 % of rated kVA. Nothing else about the IDT is sourced; the 8800 kVA rating and ONAN cooling are design choices.",
            "BLK-AUX-TR-nn is the catalog is1180-33kv-63kva-dyn11, fed from the 33 kV side of the IDT; 63 kVA is a design assumption, not a calculated auxiliary load.",
            "VCB ratings (630 A / 20 kA at the blocks and for the auxiliary feeder, 1250 A / 25 kA outgoing) are placeholders pending a fault study. CT-OG-01 has no spec: the only catalogued 33 kV CT is 200-100-50/1 A, below the export current of about 920 A.",
            "MV and auxiliary cable sizes (Havells 33 kV 3C Al) and all cable lengths are fixture choices, not a routed design. The export line from CT-OG-01 to POI-01 is by others and carries no cable.",
        ],
    },
    "company_profile": "default",
    "segment": "utility",
    "network": {
        "groups": [
            {"id": "block", "tag": "BLOCK-{0:02d}", "count": N_BLOCK, "title": "INVERTER BLOCK 8.8 MVA"},
            {"id": "inv_sec", "tag": "PCU-{0:02d}-{1}", "count": INV_PER_BLOCK, "within": "block"},
            {"id": "scb_sec", "tag": "SCB SECTION-{0:02d}-{1}-{2:02d}", "count": SCB_PER_INV, "within": "inv_sec"},
            {"id": "mvsb", "tag": "33 kV MAIN HT SWITCHBOARD", "title": "33 kV MAIN HT SWITCHBOARD PANEL"},
        ],
        "nodes": u_nodes,
        "edges": u_edges,
    },
    "sheets": [
        {"id": "sld", "number": "UDEMO-DRL-U50-EE-001", "kind": "sld", "title": "OVERALL SINGLE LINE DIAGRAM 50 MWac", "paper": "A0", "revisions": rev0()},
        {"id": "sld_blk", "number": "UDEMO-DRL-U50-EE-002", "kind": "sld_detail", "title": "TYPICAL BLOCK SLD", "paper": "A1", "scope": {"nodes": ["idt"]}, "revisions": rev0()},
        {"id": "dc_sld", "number": "UDEMO-DRL-U50-EE-003", "kind": "string_diagram", "title": "TYPICAL DC SLD (SCB)", "paper": "A1", "scope": {"nodes": ["scb"]}, "revisions": rev0()},
        {"id": "cables", "number": "UDEMO-DRL-U50-EE-004", "kind": "cable_schedule", "title": "CABLE SCHEDULE", "paper": "A3", "revisions": rev0()},
    ],
    "revisions": rev0(),
}

for name, raw in (("rooftop_500kwp", rooftop), ("utility_50mwac", utility)):
    p = Project.model_validate(raw, context={"catalog": cat})
    (OUT / f"{name}.json").write_text(canonical_json(p), encoding="utf-8")
    print(name, len(p.network.expand().nodes), "instances")

"""Topology templates: pure functions from a plan to a built (not yet sized) network with its sizing hints and assumptions."""

import math
from dataclasses import dataclass, field

from unicad.catalog.store import Catalog
from unicad.domain.catalog import BatterySpec, InverterSpec, ModuleSpec, PCSSpec, TransformerSpec
from unicad.domain.layout import StringAssignment
from unicad.domain.network import HOUSED_IN, Laying, NodeKind, Service
from unicad.domain.project import Project
from unicad.design.builder import EdgeHint, NetBuilder, NodeHint
from unicad.design.plan import BlockClass, InvClass, PvPlan, merged, sections, split_even
from unicad.design.select import (
    DC_FAMILY,
    DC_STRING_FAMILY,
    DC_STRING_MIN,
    LV1_FAMILY,
    LV3_FAMILY,
    LV_MIN,
    NoFit,
    mv_family,
    pick_lv_breaker,
    pick_meter,
    pick_mv,
    pick_rated,
    pick_transformer,
)
from unicad.design.strings import DesignError, fuses_needed
from unicad.rules.faults import transformer_fault_ka
from unicad.rules.thresholds import CONTINUOUS_FACTOR, ISC_FACTOR

V = Service
BHEL_IDT = "references/catalog-sources/raw/datasheets/transformers/BHEL_PS439-1304_5.5MVA_33kV_IDT_spec_via_indembassysweden.pdf"
SCB_STRINGS = 24
SCB_ASSUMPTION = f"String combiner boxes take up to {SCB_STRINGS} strings each (common SCB size, as in the hand-built utility fixture); no SCB is catalogued, so SCBs carry no spec."
BLOCK_AUX_KVA = 63.0
PLANT_AUX_KVA = 100.0
DC_ISOLATOR_MAX_A = 250.0
IDT_Z_PCT = 7.0
MAX_LV_WINDINGS = 2

ROOF_LENGTHS = {"string": 25.0, "dcfb_iso": 5.0, "iso_inv": 3.0, "inv_acdb": 30.0, "acdb_meter": 60.0, "iso_ltp": 5.0, "acb_tr": 15.0, "idt_vcb": 20.0, "vcb_bus": 50.0}
UTILITY_LENGTHS = {
    "string": 30.0, "scb_inv": 150.0, "inv_accb": 60.0, "inv_idt": 15.0, "accb_idt": 15.0, "idt_vcb": 10.0, "vcb_bus": 400.0,
    "aux_mv": 10.0, "aux_lv": 10.0, "main_aux_mv": 20.0, "main_aux_lv": 20.0, "bat_pcs": 10.0, "pcs_tr": 15.0, "dcdc_inv": 30.0,
}  # fmt: skip
RES_LENGTHS = {"string": 15.0, "dcfb_iso": 2.0, "iso_inv": 2.0, "inv_acdb": 10.0, "acdb_meter": 5.0, "meter_db": 10.0}


def default_lengths(template: str) -> dict[str, float]:
    over = RES_LENGTHS if template == "residential" else ROOF_LENGTHS if template == "ci_rooftop" else {}
    return {**UTILITY_LENGTHS, **ROOF_LENGTHS, **over} if over else {**ROOF_LENGTHS, **UTILITY_LENGTHS}


@dataclass
class Built:
    builder: NetBuilder
    string_links: list[tuple[list[str], int, list[list[StringAssignment]]]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


@dataclass
class Ctx:
    project: Project
    catalog: Catalog
    lengths: dict[str, float]
    given: frozenset[str] = frozenset()
    b: NetBuilder = field(default_factory=NetBuilder)
    built: Built | None = None

    def __post_init__(self) -> None:
        self.built = Built(self.b)

    @property
    def grid_v(self) -> float:
        return self.project.design_basis.grid.voltage_v

    @property
    def phases(self) -> int:
        return self.project.design_basis.grid.phases

    def L(self, key: str) -> float:
        """The route length for `key`; the next edge the builder adds is recorded as using that role."""
        self.b.last_role = key
        return self.lengths[key]

    def lengths_assumption(self, keys: list[str]) -> None:
        used = ", ".join(f"{k} {self.lengths[k]:g} m" for k in keys if k in self.lengths and k not in self.given)
        if used:
            self.b.assume(f"Cable route lengths are design placeholders, not a routed design: {used}.")


def lv_hint(phases: int) -> EdgeHint:
    return EdgeHint(LV3_FAMILY if phases == 3 else LV1_FAMILY)


def _poles(phases: int) -> tuple[int, ...]:
    return (4, 3) if phases == 3 else (2, 1)


def inline_transformer(id_: str, title: str, lv_v: float, windings: int, winding_kva: float, hv_kv: float, lv_source: str) -> TransformerSpec:
    """Project-specific multi-winding transformer with %Z, vector group pattern, LV-LV impedance and taps borrowed from BHEL PS 439-1304."""
    lv_kv = round(lv_v / 1000, 4)
    return TransformerSpec(
        kind="transformer",
        id=id_,
        manufacturer="Project-specific",
        model=title,
        source={
            "kind": "manual",
            "ref": f"Project-specific. LV voltage = {lv_source}. %Z, vector group pattern, LV1-LV2 impedance and OCTC range borrowed from "
            f"BHEL purchase specification PS 439-1304 (5.5 MVA, 33 kV) {BHEL_IDT} p.5; references/catalog-sources/extracted/idt_impedance_citations.csv. "
            "Not a vendor guarantee for this rating.",
            "retrieved": "2026-10-26",
        },
        rated_kva=windings * winding_kva,
        hv_kv=hv_kv,
        lv_kv=tuple([lv_kv] * windings),
        vector_group="YN" + "d11" * windings,
        impedance_pct=IDT_Z_PCT,
        cooling="ONAN",
        standard="IS 2026",
        winding_kva=tuple([winding_kva] * windings) if windings > 1 else None,
        impedance_base_kva=winding_kva,
        lv_lv_impedance_min_pct=11.0 if windings > 1 else None,
        tap_changer="octc",
        tap_range_pct=(-10.0, 10.0),
        tap_step_pct=2.5,
    )


def inline_assumption(tag: str, spec: TransformerSpec, windings: int) -> str:
    extra = "" if windings == 2 else f" BHEL specifies two LV windings; the {windings}-winding vector group {spec.vector_group} extends that pattern and is not sourced."
    return (
        f"{tag} is a project-specific inline spec ({spec.id}), not a catalog entry: {spec.rated_kva:g} kVA as {windings} x {spec.winding_kva[0] if spec.winding_kva else spec.rated_kva:g} kVA LV "
        f"winding(s) at {spec.lv_kv[0]:g} kV. Borrowed from BHEL PS 439-1304 (p.5): %Z 7 % (stated on the 2.75 MVA winding base, applied on the "
        f"{spec.impedance_base_kva:g} kVA winding base), YNd11 pattern, LV1-LV2 impedance min 11 %, OCTC -10..+10 % in 2.5 % steps. "
        f"The rating and ONAN cooling are design choices.{extra}"
    )


# ---------------- DC side ----------------


STRING_FUSES = (
    "Each string has its own gPV fuse in a string fuse box at the inverter (STR-FUSE), rated at least 1.25 x Isc and at most the module's maximum series fuse, "
    "because more strings share an MPPT than the module's reverse-current rating allows unfused (IEC 62548 practice; pv_strings.string_fuses)."
)


def dc_string_inverter(c: Ctx, group: str, sfx: str, inv_id: str, n_strings: int, mps: int, module: ModuleSpec, laying: Laying, isolator: bool, inverter: InverterSpec | None = None) -> list[str]:
    """Strings straight onto a string inverter, or through a DC junction box and a DC isolator when one is rated for the current. When more strings share an
    MPPT than the module allows unfused, each string lands through its own gPV fuse (string fuse box) instead of the pass-through junction box."""
    b = c.b
    s = b.node(f"str{sfx}", "STR", NodeKind.PV_STRING, group=group, count=n_strings, spec=module.id, modules_in_series=mps)
    b.assume(DC_STRING_MIN)
    fused = inverter is not None and fuses_needed(module, math.ceil(n_strings / inverter.mppt_count))
    ib = n_strings * ISC_FACTOR.value * module.isc
    if isolator and ib > DC_ISOLATOR_MAX_A:
        b.assume(f"DC isolators are catalogued up to {DC_ISOLATOR_MAX_A:g} A; inverters with {n_strings} strings ({ib:.0f} A) rely on their integrated DC switch instead.")
        isolator = False
    spd = b.node(f"dcspd{sfx}", "DC-SPD", NodeKind.DC_SPD, group=group, hint=NodeHint("dc_spd"))
    ways = f"{n_strings} string circuits, each its own +/- cable pair to its own inverter input (not paralleled)"
    if fused:
        fu = b.node(f"sfuse{sfx}", "STR-FUSE", NodeKind.FUSE, group=group, count=n_strings, hint=NodeHint("string_fuse", poles=(1,)), title="gPV STRING FUSE (STRING FUSE BOX)")
        b.edge(f"e_str{sfx}", f"{s}:out", f"{fu}:in", V.DC, EdgeHint(DC_STRING_FAMILY), c.L("string"), laying)
        b.edge(f"e_str_spd{sfx}", f"{s}:out", f"{spd}:line", V.DC)
        b.assume(STRING_FUSES)
        if not isolator:
            b.edge(f"e_fuse_inv{sfx}", f"{fu}:out", f"{inv_id}:dc", V.DC, EdgeHint(DC_STRING_FAMILY), c.L("iso_inv"), Laying.TRAY, remarks="string fuse box to inverter input, one pair per string")
            b.assume("String inverters without a separate DC isolator use their integrated DC switch-disconnector (inverter datasheet feature, not modelled).")
            return [s]
        iso = b.node(f"dciso{sfx}", "DC-ISO", NodeKind.DC_ISOLATOR, group=group, hint=NodeHint("dc_isolator", poles=(2, 4)))
        b.edge(f"e_dcfb_iso{sfx}", f"{fu}:out", f"{iso}:in", V.DC, EdgeHint(DC_STRING_FAMILY), c.L("dcfb_iso"), Laying.TRAY, remarks=ways)
        b.edge(f"e_iso_inv{sfx}", f"{iso}:out", f"{inv_id}:dc", V.DC, EdgeHint(DC_STRING_FAMILY, runs=n_strings), c.L("iso_inv"), Laying.TRAY, remarks=ways)
        b.assume("Each DC isolator node stands for the isolation of all string circuits of its inverter (one 2-pole way per circuit); it is rated as one device for the sum of their currents, which is conservative.")
        return [s]
    if not isolator:
        b.edge(f"e_str{sfx}", f"{s}:out", f"{inv_id}:dc", V.DC, EdgeHint(DC_STRING_FAMILY), c.L("string"), laying)
        b.edge(f"e_str_spd{sfx}", f"{s}:out", f"{spd}:line", V.DC)
        b.assume("String inverters without a separate DC isolator use their integrated DC switch-disconnector (inverter datasheet feature, not modelled).")
        return [s]
    fb = b.node(f"dcfb{sfx}", "DCJB", NodeKind.DC_FUSE_BOX, group=group, symbol="dc.jb", title="DC JUNCTION BOX")
    iso = b.node(f"dciso{sfx}", "DC-ISO", NodeKind.DC_ISOLATOR, group=group, hint=NodeHint("dc_isolator", poles=(2, 4)))
    b.edge(f"e_str{sfx}", f"{s}:out", f"{fb}:in", V.DC, EdgeHint(DC_STRING_FAMILY), c.L("string"), laying)
    b.edge(f"e_dcfb_spd{sfx}", f"{fb}:out", f"{spd}:line", V.DC)
    b.edge(f"e_dcfb_iso{sfx}", f"{fb}:out", f"{iso}:in", V.DC, EdgeHint(DC_STRING_FAMILY, runs=n_strings), c.L("dcfb_iso"), Laying.TRAY, remarks=ways)
    b.edge(f"e_iso_inv{sfx}", f"{iso}:out", f"{inv_id}:dc", V.DC, EdgeHint(DC_STRING_FAMILY, runs=n_strings), c.L("iso_inv"), Laying.TRAY, remarks=ways)
    b.assume(
        "The DC junction box passes strings through to separate inverter inputs (one cable pair per string); no string fuses are needed with at most two strings per MPPT.",
        "Each DC isolator node stands for the isolation of all string circuits of its inverter (one 2-pole way per circuit); it is rated as one device for the sum of their currents, which is conservative.",
    )
    return [s]


def dc_central(c: Ctx, group: str, sfx: str, inv_id: str, n_strings: int, mps: int, module: ModuleSpec, inverter: InverterSpec) -> list[str]:
    b = c.b
    b.assume(SCB_ASSUMPTION)
    classes = split_even(n_strings, SCB_STRINGS)
    if inverter.dc_input_count is not None and sum(k for k, _ in classes) > inverter.dc_input_count:
        raise DesignError(f"{n_strings} strings need {sum(k for k, _ in classes)} SCBs, more than the {inverter.dc_input_count} DC inputs of {inverter.id}")
    spd = b.node(f"inv_dcspd{sfx}", "DC-SPD", NodeKind.DC_SPD, group=group, hint=NodeHint("dc_spd"))
    ids = []
    for k, (count, per) in enumerate(classes):
        letter = "" if len(classes) == 1 else chr(ord("a") + k)
        x, t = f"{sfx}{letter}", f"-{letter.upper()}" if letter else ""
        g = b.group(f"scb_sec{x}", f"SCB SECTION{t}", count=count, within=group)
        s = b.node(f"str{x}", f"STR{t}", NodeKind.PV_STRING, group=g, count=per, spec=module.id, modules_in_series=mps)
        scb = b.node(f"scb{x}", f"SCB{t}", NodeKind.SCB, group=g, title=f"{per}-INPUT STRING COMBINER BOX")
        b.edge(f"e_str{x}", f"{s}:out", f"{scb}:in", V.DC, EdgeHint(DC_STRING_FAMILY), c.L("string"), Laying.AIR)
        b.edge(f"e_scb_inv{x}", f"{scb}:out", f"{inv_id}:dc", V.DC, EdgeHint(DC_FAMILY), c.L("scb_inv"), Laying.TRENCH)
        b.edge(f"e_scb_spd{x}", f"{scb}:out", f"{spd}:line", V.DC)
        b.earth(scb)
        ids.append(s)
    b.assume(DC_STRING_MIN, "SCB to inverter DC cables are laid in trenches and rated with the in-air ampacity column (the catalogued solar DC cable has no in-ground rating).")
    return ids


# ---------------- LV collection ----------------


def inverter_groups(c: Ctx, within: str | None, blk: BlockClass, inverter: InverterSpec, module: ModuleSpec, mps: int, central: bool, dc_laying: Laying, isolator: bool, prefix: str = "INVERTER SECTION") -> list[tuple[InvClass, str, str]]:
    """One group per inverter class with its strings, DC side and inverter; returns (class, group id, inverter id)."""
    b, out = c.b, []
    for ic in blk.invs:
        sfx = f"{blk.sfx}{ic.sfx}"
        g = b.group(f"inv_sec{sfx}", prefix, count=ic.count, within=within, part=ic.part, title=f"{inverter.ac_rated_kva:g} kVA {inverter.model} SECTION")
        inv = b.node(f"inv{sfx}", "INV", NodeKind.INVERTER, group=g, spec=inverter.id)
        if central:
            ids = dc_central(c, g, sfx, inv, ic.strings, mps, module, inverter)
        else:
            ids = dc_string_inverter(c, g, sfx, inv, ic.strings, mps, module, dc_laying, isolator, inverter)
        b.earth(inv)
        if ic.slots:
            c.built.string_links.append((ids, ic.count, ic.slots))
        out.append((ic, g, inv))
    return out


def lv_board(c: Ctx, group: str | None, sfx: str, invs: list[tuple[InvClass, str, str]], v: float, phases: int, title: str, inv_len: str, inv_laying: Laying, prefix: str = "ACDB") -> str:
    """Inverter outgoing breakers onto an ACDB/ACCB bus with an AC SPD; returns the outgoing breaker's id."""
    b = c.b
    board = b.node(f"acdb{sfx}", prefix, NodeKind.ACDB, group=group, voltage_v=v, title=title)
    for ic, g, inv in invs:
        x = inv.removeprefix("inv")
        brk = b.node(f"inv_brk{x}", "MCCB", NodeKind.BREAKER, group=g, voltage_v=v, hint=NodeHint("lv_breaker", _poles(phases)))
        b.edge(f"e_inv_brk{x}", f"{inv}:ac", f"{brk}:in", V.LV_AC, lv_hint(phases), c.L(inv_len), inv_laying)
        b.edge(f"e_brk_acdb{x}", f"{brk}:out", f"{board}:in", V.LV_AC)
    spd = b.node(f"acdb_spd{sfx}", f"{prefix}-SPD", NodeKind.AC_SPD, group=group, voltage_v=v, hint=NodeHint("ac_spd", _poles(phases)))
    b.edge(f"e_acdb_spd{sfx}", f"{board}:out", f"{spd}:line", V.LV_AC)
    out = b.node(f"acdb_brk{sfx}", "ACB" if phases == 3 else "MCB", NodeKind.BREAKER, group=group, voltage_v=v, hint=NodeHint("lv_breaker", _poles(phases)))
    b.edge(f"e_acdb_brk{sfx}", f"{board}:out", f"{out}:in", V.LV_AC)
    b.earth(board)
    b.assume(LV_MIN)
    return out


# ---------------- MV side ----------------


def mv_yard(c: Ctx, kv: float, metering: str, aux: bool, export_limit_kw: float | None) -> str:
    """Main HT switchboard: bus, PT, auxiliary feeder, outgoing VCB with CT, relay, meters and LA, and the POI. Returns the bus id."""
    b, cat, vv = c.b, c.catalog, kv * 1000
    g = b.group("mvsb", f"{kv:g} kV MAIN HT SWITCHBOARD", title=f"{kv:g} kV MAIN HT SWITCHBOARD PANEL")
    bus = b.node("mv_bus", "MV-BUS-01", NodeKind.BUSBAR, group=g, voltage_v=vv, title=f"{kv:g} kV MAIN HT SWITCHBOARD BUS")
    pt = pick_mv(cat, "pt", kv)
    b.node("mv_pt", "MV-PT-01", NodeKind.PT, group=g, voltage_v=vv, spec=pt.id if pt else None)
    b.edge("e_bus_pt", "mv_bus:bus", "mv_pt:in", V.MV_AC)
    if pt is None:
        b.assume(f"No catalogued PT for a {kv:g} kV system; MV-PT-01 carries no spec.")
    if aux:
        tr = pick_transformer(cat, PLANT_AUX_KVA, kv)
        b.node("aux_vcb", "VCB-AUX-01", NodeKind.VCB, group=g, voltage_v=vv, hint=NodeHint("vcb", nominal_a=tr.rated_kva / (math.sqrt(3) * kv)))
        b.node("aux_tr", "AUX-TR-01", NodeKind.AUX_TRANSFORMER, group=g, spec=tr.id)
        b.node("aux_load", "AUX-LOAD-01", NodeKind.LOAD, voltage_v=415.0, title="PLANT AUXILIARY / CMCS ACDB")
        b.edge("e_bus_aux", "mv_bus:bus", "aux_vcb:in", V.MV_AC)
        b.edge("e_auxvcb_tr", "aux_vcb:out", "aux_tr:hv", V.MV_AC, EdgeHint(mv_family(kv), nominal_a=tr.rated_kva / (math.sqrt(3) * kv)), c.L("main_aux_mv"), Laying.BURIED_DIRECT)
        b.edge("e_auxtr_load", "aux_tr:lv", "aux_load:in", V.LV_AC, EdgeHint(LV3_FAMILY, nominal_a=tr.rated_kva / (math.sqrt(3) * 0.415)), c.L("main_aux_lv"), Laying.TRAY)
        b.assume(f"AUX-TR-01 is the catalog {tr.id}; {PLANT_AUX_KVA:g} kVA is a design assumption, not a calculated auxiliary load.")
    b.node("out_vcb", "VCB-OG-01", NodeKind.VCB, group=g, voltage_v=vv, hint=NodeHint("vcb"))
    b.node("out_ct", "CT-OG-01", NodeKind.CT, group=g, voltage_v=vv)
    b.node("out_relay", "REL-OG-01", NodeKind.RELAY, group=g)
    b.edge("e_bus_og", "mv_bus:bus", "out_vcb:in", V.MV_AC)
    b.edge("e_og_ct", "out_vcb:out", "out_ct:in", V.MV_AC)
    b.edge("c_ct_relay", "out_ct:sec", "out_relay:sig", V.CONTROL)
    b.assume("CT-OG-01 has no spec: the catalog's only MV CT (33 kV, 200-100-50/1 A) is not chosen for an export feeder; ratio to follow the export current.")
    meters = [("abt_main", "ABT-M-01", "abt", "ABT MAIN METER"), ("abt_check", "ABT-C-01", "check", "ABT CHECK METER")] if metering == "abt" else [("net_meter", "NM-01", "net", "DISCOM NET METER (HT)")]
    for mid, tag, fn, title in meters:
        b.node(mid, tag, NodeKind.METER, group=g, spec=pick_meter(c.catalog, fn, "ct_pt").id, ports=["sig"], title=title)
        b.edge(f"c_ct_{mid}", "out_ct:sec", f"{mid}:sig", V.CONTROL)
        b.edge(f"c_pt_{mid}", "mv_pt:sec", f"{mid}:sig", V.CONTROL)
    b.assume("ABT main and check meters (0.2S, CT/PT operated) at the export feeder, per CEA metering practice for grid-connected generators." if metering == "abt" else "HT net meter (CT/PT operated) at the DISCOM interface.")
    la = pick_mv(cat, "lightning_arrester", kv)
    b.node("out_la", "LA-OG-01", NodeKind.LIGHTNING_ARRESTER, voltage_v=vv, spec=la.id if la else None)
    b.edge("e_ct_la", "out_ct:out", "out_la:line", V.MV_AC)
    attrs = {"export_limit_kva": float(export_limit_kw)} if export_limit_kw else {}
    b.node("poi", "POI-01", NodeKind.POI_GRID, voltage_v=vv, title=f"{kv:g} kV EXPORT LINE TO GRID SUBSTATION", attributes=attrs)
    b.edge("e_ct_poi", "out_ct:out", "poi:in", V.MV_AC, remarks=f"{kv:g} kV export line, route by others")
    b.earth()
    return bus


GEM_RMU = "GeM 33 kV outdoor ring main unit specification (2025-12), sec. 3.0 and 4.6: the transformer T-off is a vacuum circuit breaker with 3 O/C + 1 E/F relays"
FEEDER_PANELS = (
    "Every block feeder ends at the main HT switchboard in its own feeder panel (VCB-BF-nn with CT-BF-nn and an O/C + E/F relay REL-BF-nn), so the "
    "feeder cable is switched and protected at the bus end, which is the end the grid's fault current comes from; the block-end VCB/RMU protects the block transformer."
)


def block_feeder(c: Ctx, group: str, sfx: str, tr_id: str, kv: float, switch: str, block_aux: bool, tag: str = "ICOG") -> None:
    """Transformer HV side of one block: LA, optional block auxiliary transformer on its own T-off VCB, the block VCB or RMU, the feeder cable and the
    feeder panel (VCB, CT, relay) at the main HT switchboard bus."""
    b, vv = c.b, kv * 1000
    fam = mv_family(kv)
    if block_aux:
        aux = pick_transformer(c.catalog, BLOCK_AUX_KVA, kv)
        i_aux = aux.rated_kva / (math.sqrt(3) * kv)
        b.node(f"blk_aux_vcb{sfx}", f"{tag}-AUX-VCB", NodeKind.VCB, group=group, voltage_v=vv, hint=NodeHint("vcb", nominal_a=i_aux), title="BLOCK AUX TRANSFORMER T-OFF VCB")
        b.node(f"blk_aux{sfx}", "BLK-AUX-TR", NodeKind.AUX_TRANSFORMER, group=group, spec=aux.id)
        b.node(f"blk_aux_load{sfx}", "BLK-AUX-LOAD", NodeKind.LOAD, group=group, voltage_v=415.0, title="BLOCK AUXILIARY ACDB")
        b.edge(f"e_idt_auxvcb{sfx}", f"{tr_id}:hv", f"blk_aux_vcb{sfx}:in", V.MV_AC, EdgeHint(fam, nominal_a=i_aux), c.L("idt_vcb"), Laying.BURIED_DIRECT)
        b.edge(f"e_idt_aux{sfx}", f"blk_aux_vcb{sfx}:out", f"blk_aux{sfx}:hv", V.MV_AC, EdgeHint(fam, nominal_a=i_aux), c.L("aux_mv"), Laying.BURIED_DIRECT)
        b.edge(f"e_aux_load{sfx}", f"blk_aux{sfx}:lv", f"blk_aux_load{sfx}:in", V.LV_AC, EdgeHint(LV3_FAMILY, nominal_a=aux.rated_kva / (math.sqrt(3) * 0.415)), c.L("aux_lv"), Laying.TRAY)
        b.assume(
            f"Block auxiliary transformers are the catalog {aux.id}, fed from the HV side of the block transformer; {BLOCK_AUX_KVA:g} kVA is a design assumption, not a calculated load.",
            f"Each block auxiliary transformer is fed through its own T-off vacuum breaker with O/C and E/F protection ({GEM_RMU}); no {kv:g} kV fuse-switch is catalogued, so a fuse T-off is not offered.",
        )
    if switch == "rmu":
        rmu = pick_rated(c.catalog, ["rmu"], 0.0, vv, (3,))
        b.node(f"icog{sfx}", f"{tag}-RMU", NodeKind.RMU, group=group, voltage_v=vv, spec=rmu.id)
    else:
        b.node(f"icog{sfx}", f"{tag}-VCB", NodeKind.VCB, group=group, voltage_v=vv, hint=NodeHint("vcb"))
    la = pick_mv(c.catalog, "lightning_arrester", kv)
    b.node(f"icog_la{sfx}", f"{tag}-LA", NodeKind.LIGHTNING_ARRESTER, group=group, voltage_v=vv, spec=la.id if la else None)
    b.edge(f"e_tr_icog{sfx}", f"{tr_id}:hv", f"icog{sfx}:in", V.MV_AC, EdgeHint(fam), c.L("idt_vcb"), Laying.BURIED_DIRECT)
    b.edge(f"e_icog_la{sfx}", f"{tr_id}:hv", f"icog_la{sfx}:line", V.MV_AC)
    bf = "BF" if tag == "ICOG" else f"{tag}-BF"
    panel = {HOUSED_IN: "mvsb"}
    b.node(f"bf_ct{sfx}", f"CT-{bf}", NodeKind.CT, group=group, voltage_v=vv, title="FEEDER PANEL CT AT MAIN HT SWITCHBOARD", attributes=panel)
    b.node(f"bf_vcb{sfx}", f"VCB-{bf}", NodeKind.VCB, group=group, voltage_v=vv, hint=NodeHint("vcb"), title="FEEDER PANEL VCB AT MAIN HT SWITCHBOARD", attributes=panel)
    b.node(f"bf_relay{sfx}", f"REL-{bf}", NodeKind.RELAY, group=group, title="FEEDER O/C + E/F RELAY", attributes=panel)
    b.edge(f"e_icog_bus{sfx}", f"icog{sfx}:out", f"bf_ct{sfx}:in", V.MV_AC, EdgeHint(fam), c.L("vcb_bus"), Laying.BURIED_DIRECT)
    b.edge(f"e_bfct_vcb{sfx}", f"bf_ct{sfx}:out", f"bf_vcb{sfx}:in", V.MV_AC)
    b.edge(f"e_bf_bus{sfx}", f"bf_vcb{sfx}:out", "mv_bus:bus", V.MV_AC)
    b.edge(f"c_bf_relay{sfx}", f"bf_ct{sfx}:sec", f"bf_relay{sfx}:sig", V.CONTROL)
    b.assume(FEEDER_PANELS, "Feeder panel CTs (CT-BF-nn) carry no spec: their ratio follows the block feeder current, set with the relay settings.")
    b.earth(tr_id)


def _kv(c: Ctx) -> float:
    kv = c.grid_v / 1000
    if kv not in (11.0, 22.0, 33.0):
        raise DesignError(f"grid voltage {c.grid_v:g} V: MV templates export at 11, 22 or 33 kV; higher voltages need a pooling transformer the catalog does not hold")
    return kv


# ---------------- templates ----------------


def residential(c: Ctx, plan: PvPlan, inverter: InverterSpec, module: ModuleSpec) -> Built:
    """Strings -> DC SPD + DC isolator -> inverter -> ACDB (MCB, SPD) -> generation meter -> consumer DB -> net meter -> DISCOM LT supply."""
    b, ph, v = c.b, c.phases, c.grid_v
    if inverter.phases != ph:
        raise DesignError(f"the grid is {ph}-phase but {inverter.id} is {inverter.phases}-phase; the catalog has no {ph}-phase inverter for this template")
    blk = plan.blocks[0]
    invs = inverter_groups(c, None, blk, inverter, module, plan.modules_per_string, False, Laying.TRAY, True)
    out = lv_board(c, None, "", invs, v, ph, "SOLAR ACDB", "inv_acdb", Laying.CONDUIT, prefix="ACDB")
    b.node("gen_meter", "GM-01", NodeKind.METER, spec=pick_meter(c.catalog, "generation", "direct").id, title="SOLAR GENERATION METER")
    b.node("db", "DB-01", NodeKind.LT_PANEL, voltage_v=v, title="CONSUMER MAIN DB")
    b.node("load", "LOAD-01", NodeKind.LOAD, voltage_v=v, title="CONSUMER LOADS")
    b.node("net_meter", "NM-01", NodeKind.METER, spec=pick_meter(c.catalog, "net", "direct").id, title="DISCOM NET METER (BIDIRECTIONAL)")
    b.node("poi", "POI-01", NodeKind.POI_GRID, voltage_v=v, title=f"DISCOM LT SUPPLY {v:g} V")
    b.edge("e_brk_gm", f"{out}:out", "gen_meter:in", V.LV_AC, lv_hint(ph), c.L("acdb_meter"), Laying.CONDUIT)
    b.edge("e_gm_db", "gen_meter:out", "db:in", V.LV_AC, lv_hint(ph), c.L("meter_db"), Laying.CONDUIT)
    b.edge("e_db_load", "db:out", "load:in", V.LV_AC, remarks="Existing consumer wiring, by others")
    b.edge("e_db_nm", "db:out", "net_meter:in", V.LV_AC)
    b.edge("e_nm_poi", "net_meter:out", "poi:in", V.LV_AC, remarks="DISCOM service connection, by others")
    b.earth("db")
    b.assume("Direct-connected generation and bidirectional net meters, as for LT net-metered rooftop connections; meter class per the DISCOM's regulations.")
    c.lengths_assumption(["string", "dcfb_iso", "iso_inv", "inv_acdb", "acdb_meter", "meter_db"])
    return c.built


def ci_rooftop(c: Ctx, plan: PvPlan, inverter: InverterSpec, module: ModuleSpec, step_up_kv: float | None, connection: str, metering: str) -> Built:
    """String inverters -> ACDB -> solar CT/generation meter -> isolator -> LT panel (or dedicated feeder) -> net meter -> POI, or with step_up_kv,
    ACDB sections -> step-up transformer -> RMU -> HT bus -> net/ABT metering -> POI."""
    b, v = c.b, 415.0
    lv_ph = 3
    if inverter.phases != 3:
        raise DesignError(f"{inverter.id} is single-phase; C&I templates need three-phase inverters")
    if step_up_kv is None:
        if c.grid_v != 415.0:
            raise DesignError(f"grid voltage {c.grid_v:g} V: LT connection templates connect at 415 V; pass step_up_kv for an HT connection")
        invs = inverter_groups(c, None, merged(plan).blocks[0], inverter, module, plan.modules_per_string, False, Laying.TRAY, True)
        out = lv_board(c, None, "", invs, v, lv_ph, "AC DISTRIBUTION BOARD", "inv_acdb", Laying.TRAY)
        b.node("solar_ct", "CT-01", NodeKind.CT, voltage_v=v)
        b.node("solar_meter", "SM-01", NodeKind.METER, spec=pick_meter(c.catalog, "generation", "ct").id, ports=["sig"], title="SOLAR GENERATION METER")
        b.node("ac_spd", "AC-SPD-01", NodeKind.AC_SPD, voltage_v=v, hint=NodeHint("ac_spd"))
        b.node("ac_iso", "AC-ISO-01", NodeKind.ISOLATOR, voltage_v=v, hint=NodeHint("ac_isolator", (4, 3)))
        b.edge("e_brk_ct", f"{out}:out", "solar_ct:in", V.LV_AC, lv_hint(3), c.L("acdb_meter"), Laying.TRAY, remarks="ACDB to the solar metering and isolation panel")
        b.edge("e_ct_spd", "solar_ct:out", "ac_spd:line", V.LV_AC)
        b.edge("e_ct_iso", "solar_ct:out", "ac_iso:in", V.LV_AC)
        b.edge("c_solar_meter", "solar_ct:sec", "solar_meter:sig", V.CONTROL)
        b.node("net_ct", "CT-02", NodeKind.CT, voltage_v=v)
        b.node("net_meter", "NM-01", NodeKind.METER, spec=pick_meter(c.catalog, "net", "ct").id, ports=["sig"], title="DISCOM NET METER")
        b.node("poi", "POI-01", NodeKind.POI_GRID, voltage_v=v, title="DISCOM LT SUPPLY 415 V")
        if connection == "dedicated_feeder":
            b.edge("e_iso_netct", "ac_iso:out", "net_ct:in", V.LV_AC, lv_hint(3), c.L("iso_ltp"), Laying.TRAY, remarks="Dedicated LT feeder to the DISCOM metering point")
            b.assume("Dedicated LT feeder: the plant exports through its own feeder and net/export meter, not through the consumer LT panel.")
        else:
            b.node("ltp", "LTP-01", NodeKind.LT_PANEL, voltage_v=v, title="CONSUMER LT PANEL 415 V")
            b.node("load", "LOAD-01", NodeKind.LOAD, voltage_v=v, title="CONSUMER LOADS")
            b.edge("e_iso_ltp", "ac_iso:out", "ltp:in", V.LV_AC, lv_hint(3), c.L("iso_ltp"), Laying.TRAY)
            b.edge("e_ltp_load", "ltp:out", "load:in", V.LV_AC, remarks="Existing consumer distribution, by others")
            b.edge("e_ltp_ct", "ltp:out", "net_ct:in", V.LV_AC)
            b.earth("ltp")
        b.edge("e_ct_poi", "net_ct:out", "poi:in", V.LV_AC, remarks="DISCOM service connection, by others")
        b.edge("c_net_meter", "net_ct:sec", "net_meter:sig", V.CONTROL)
        b.assume("Solar generation meter and DISCOM net meter are CT-operated 0.5S, as for LT net-metered C&I connections.")
        c.lengths_assumption(["string", "dcfb_iso", "iso_inv", "inv_acdb", "acdb_meter", "iso_ltp"])
        return c.built
    kv = step_up_kv
    if c.grid_v != kv * 1000:
        raise DesignError(f"step_up_kv {kv:g} kV must equal the grid voltage {c.grid_v / 1000:g} kV")
    tr_max = max((s.rated_kva for s in c.catalog.filter("transformer") if isinstance(s, TransformerSpec) and s.hv_kv == kv), default=None)
    if tr_max is None:
        raise DesignError(f"no catalog transformer with a {kv:g} kV HV winding")
    acb_max = max(p.rated_current for p in c.catalog.filter("protection") if getattr(p, "device", None) == "acb" and p.rated_current)
    per_sec = max(1, min(int(tr_max // inverter.ac_max_kva), int(acb_max / 1.25 * math.sqrt(3) * v / 1000 // inverter.ac_max_kva)))
    blk = plan.blocks[0]
    if plan.from_layout or len(plan.blocks) > 1:
        raise DesignError("the C&I step-up template takes a uniform plan; place the array without blocks or use utility_string")
    n_inv = blk.inverters
    secs = split_even(n_inv, per_sec)
    if len(secs) > 1:
        raise DesignError(f"{n_inv} inverters need {len(secs)} uneven LT sections; pick an inverter count that divides into sections of at most {per_sec}")
    n_sec, per = secs[0]
    tr = pick_transformer(c.catalog, per * inverter.ac_max_kva, kv)
    g = b.group("lt_sec", "LT SECTION", count=n_sec, title=f"{tr.rated_kva:g} kVA STEP-UP SECTION")
    sec_blk = BlockClass("", 1, [InvClass("", per, blk.invs[0].strings)])
    invs = inverter_groups(c, g, sec_blk, inverter, module, plan.modules_per_string, False, Laying.TRAY, True)
    out = lv_board(c, g, "", invs, v, 3, "AC DISTRIBUTION BOARD", "inv_acdb", Laying.TRAY)
    b.node("solar_ct", "CT", NodeKind.CT, group=g, voltage_v=v)
    b.node("solar_meter", "SM", NodeKind.METER, group=g, spec=pick_meter(c.catalog, "generation", "ct").id, ports=["sig"], title="SOLAR GENERATION METER")
    b.node("tr", "TR", NodeKind.TRANSFORMER, group=g, spec=tr.id)
    b.edge("e_brk_ct", f"{out}:out", "solar_ct:in", V.LV_AC)
    b.edge("e_ct_tr", "solar_ct:out", "tr:lv", V.LV_AC, lv_hint(3), c.L("acb_tr"), Laying.TRENCH)
    b.edge("c_solar_meter", "solar_ct:sec", "solar_meter:sig", V.CONTROL)
    mv_yard(c, kv, metering, aux=False, export_limit_kw=None)
    block_feeder(c, g, "", "tr", kv, "rmu", block_aux=False, tag="TR")
    b.assume(f"Step-up transformers are the catalog {tr.id} (IS 1180 distribution transformer used as a generator step-up), {per} inverters per section.")
    c.lengths_assumption(["string", "dcfb_iso", "iso_inv", "inv_acdb", "acb_tr", "idt_vcb", "vcb_bus"])
    return c.built


def lv_bus_cap(catalog: Catalog, basis, inverter: InverterSpec, v: float, kv: float, inline: bool) -> tuple[int, str]:
    """The most inverters one LV bus (one transformer LV winding) can take so that every breaker on it has a catalog spec: the bus
    breaker rated >= 1.25 x the inverters' current and each breaker's breaking capacity >= the fault the winding passes to the bus.
    Returns the count and what stops the next inverter."""
    i_inv = inverter.ac_max_kva * 1000 / (math.sqrt(3) * v)
    best, why = 0, "the search limit"
    for n in range(1, 101):
        kva = n * inverter.ac_max_kva
        if inline:
            base, z = kva, IDT_Z_PCT
        else:
            try:
                tr = pick_transformer(catalog, kva, kv)
            except NoFit:
                why = f"no catalog {kv:g} kV transformer is rated >= {kva:g} kVA"
                break
            base, z = tr.impedance_base_kva or tr.rated_kva, tr.impedance_pct
        ka, _, _ = transformer_fault_ka(base, z, v, kv * 1000, basis)
        try:
            pick_lv_breaker(catalog, CONTINUOUS_FACTOR.value * n * i_inv, v, (4, 3), ka)
        except NoFit:
            why = f"no catalog bus breaker is rated >= {CONTINUOUS_FACTOR.value * n * i_inv:.0f} A at {v:g} V with a breaking capacity >= the {ka:.1f} kA bus fault"
            break
        try:
            pick_lv_breaker(catalog, CONTINUOUS_FACTOR.value * i_inv, v, (4, 3), ka)
        except NoFit:
            why = f"no catalog inverter breaker rated >= {CONTINUOUS_FACTOR.value * i_inv:.0f} A at {v:g} V has a breaking capacity >= the {ka:.1f} kA bus fault of {n} inverters"
            break
        best = n
    return best, why


def string_block_limits(catalog: Catalog, basis, inverter: InverterSpec, kv: float) -> tuple[int, int, str]:
    """(inverters per LV bus, LV buses per block, why the bus stops there) for string inverters on a block transformer."""
    v = inverter.ac_voltage if inverter.ac_voltage > 440 else 415.0
    inline = v != 415.0
    cap, why = lv_bus_cap(catalog, basis, inverter, v, kv, inline)
    if cap == 0:
        raise DesignError(f"{inverter.id} cannot be connected to a {v:g} V block bus with catalog breakers: {why}")
    return cap, MAX_LV_WINDINGS if inline else 1, why


def string_blocks(c: Ctx, plan: PvPlan, inverter: InverterSpec, module: ModuleSpec, kv: float, switch: str, aux: bool) -> None:
    """Per block: string inverters -> LT ACCB -> block transformer -> VCB/RMU feeder. Inverters up to 440 V use a catalog IS 1180 transformer;
    800 V-class inverters get a project-specific inline transformer, as central inverters do, with up to two LV windings, one ACCB on each,
    when one LV bus cannot carry the block with catalog breakers."""
    b = c.b
    v = inverter.ac_voltage if inverter.ac_voltage > 440 else 415.0
    inline = v != 415.0
    cap, windings, why = string_block_limits(c.catalog, c.project.design_basis, inverter, kv)
    split = {id(blk): sections(blk, cap, plan.from_layout) if blk.inverters > cap else [blk] for blk in plan.blocks}
    std_w = {len(secs): max(max(sec.inverters for sec in o) for o in split.values() if len(o) == len(secs)) for secs in split.values()}
    std_n = max(blk.inverters for blk in plan.blocks)
    if len({blk.inverters for blk in plan.blocks}) > 1:
        b.assume(
            f"Block transformer ratings are standardised: every block takes the rating that covers the largest block ({std_n} x {inverter.ac_max_kva:g} kVA"
            f"{', ' + str(max(sec.inverters for o in split.values() for sec in o)) + ' per LV winding' if inline else ''}), so the blocks are interchangeable and one spare serves all."
        )
    for blk in plan.blocks:
        n = blk.inverters
        secs = split[id(blk)]
        if len(secs) > windings:
            raise DesignError(
                f"block {blk.part or blk.sfx or ''} has {n} inverters; a block takes at most {cap * windings} ({windings} LV bus(es) of {cap}: beyond that {why}). "
                "Use smaller blocks (layout_place block_dc_kwp or n_blocks, or inverters_per_block)"
            )
        g = b.group(f"block{blk.sfx}", "BLOCK", count=blk.count, part=blk.part, title=f"STRING INVERTER BLOCK ({n} x {inverter.ac_rated_kva:g} kVA)")
        outs = []
        for sec in secs:
            within = g if len(secs) == 1 else b.group(f"lv_sec{sec.sfx}", "LV", within=g, part=sec.part, tag=f"{b.group_tag(g)} SECTION {sec.part}", title=f"LV SECTION {sec.part} ({sec.inverters} x {inverter.ac_rated_kva:g} kVA)")
            invs = inverter_groups(c, within, sec, inverter, module, plan.modules_per_string, False, Laying.AIR, True)
            outs.append((sec, lv_board(c, within, sec.sfx, invs, v, 3, "LT AC COMBINER BOX (ACCB)", "inv_accb", Laying.TRENCH, prefix="ACCB")))
        if len(secs) > 1:
            b.assume(f"Blocks above {cap} inverters split into LV sections on separate IDT LV windings, {cap} inverters at most per bus: beyond that {why}.")
        if inline:
            k = len(secs)
            w_kva = std_w[k] * inverter.ac_max_kva
            lv = "-".join([f"{v / 1000:g}"] * k)
            spec = inline_transformer(
                f"idt-{k * w_kva:g}kva-{kv:g}-{lv}-yn{'d11' * k}-project", f"{k * w_kva:g} kVA {kv:g}/{lv} kV inverter-duty transformer", v, k, w_kva, kv, f"ac_voltage of catalog {inverter.id}"
            )  # fmt: skip
            b.assume(inline_assumption("IDT-nn", spec, k))
        else:
            try:
                spec = pick_transformer(c.catalog, std_n * inverter.ac_max_kva, kv).id
            except NoFit as e:
                raise DesignError(f"block of {n} inverters: {e}") from None
        idt = b.node(f"idt{blk.sfx}", "IDT", NodeKind.TRANSFORMER, group=g, spec=spec)
        for sec, out in outs:
            x = sec.sfx
            b.edge(f"e_accb_idt{x}", f"{out}:out", f"{idt}:lv", V.LV_AC, lv_hint(3), c.L("accb_idt"), Laying.TRENCH)
        block_feeder(c, g, blk.sfx, idt, kv, switch, block_aux=aux)
    if not inline:
        b.assume("Block transformers are catalog IS 1180 distribution transformers (0.433 kV LV) used as inverter-duty step-up transformers; confirm inverter-duty suitability with the vendor.")


def utility_string(c: Ctx, plan: PvPlan, inverter: InverterSpec, module: ModuleSpec, switch: str, aux: bool, export_limit_kw: float | None) -> Built:
    """Per block: string inverters -> LT ACCB -> catalog IDT -> VCB/RMU -> MV bus -> export."""
    kv = _kv(c)
    string_blocks(c, plan, inverter, module, kv, switch, aux)
    mv_yard(c, kv, "abt", aux, export_limit_kw)
    c.lengths_assumption(["string", "inv_accb", "accb_idt", "idt_vcb", "vcb_bus", "aux_mv", "aux_lv"])
    return c.built


def central_blocks(c: Ctx, plan: PvPlan, inverter: InverterSpec, module: ModuleSpec, kv: float, switch: str, aux: bool, battery: "DcBattery | None" = None) -> None:
    b = c.b
    vv = inverter.ac_voltage
    for blk in plan.blocks:
        g = b.group(f"block{blk.sfx}", "BLOCK", count=blk.count, part=blk.part, title=f"INVERTER BLOCK {blk.inverters * inverter.ac_rated_kva / 1000:g} MVA")
        invs = inverter_groups(c, g, blk, inverter, module, plan.modules_per_string, True, Laying.AIR, False, prefix="PCU")
        n = blk.inverters
        spec = inline_transformer(
            f"idt-{n * inverter.ac_rated_kva:g}kva-{kv:g}-{'-'.join([f'{vv / 1000:g}'] * n)}-yn{'d11' * n}-project",
            f"{n * inverter.ac_rated_kva:g} kVA {kv:g}/{'-'.join([f'{vv / 1000:g}'] * n)} kV inverter-duty transformer",
            vv, n, inverter.ac_rated_kva, kv, f"ac_voltage of catalog {inverter.id}",
        )  # fmt: skip
        idt = b.node(f"idt{blk.sfx}", "IDT", NodeKind.TRANSFORMER, group=g, spec=spec)
        b.assume(inline_assumption("IDT-nn", spec, n))
        for ic, ig, inv in invs:
            x = inv.removeprefix("inv")
            b.node(f"inv_acspd{x}", "AC-SPD", NodeKind.AC_SPD, group=ig, voltage_v=vv, hint=NodeHint("ac_spd"))
            b.edge(f"e_inv_idt{x}", f"{inv}:ac", f"{idt}:lv", V.LV_AC, EdgeHint(LV3_FAMILY), c.L("inv_idt"), Laying.TRENCH)
            b.edge(f"e_inv_acspd{x}", f"{inv}:ac", f"inv_acspd{x}:line", V.LV_AC)
            if battery is not None:
                battery.attach(c, ig, x, inv)
        block_feeder(c, g, blk.sfx, idt, kv, switch, block_aux=aux)
    b.assume("Central inverters connect straight to their IDT LV winding through their integrated AC breaker (inverter datasheet feature, not modelled).")


def utility_central(c: Ctx, plan: PvPlan, inverter: InverterSpec, module: ModuleSpec, switch: str, aux: bool, export_limit_kw: float | None, battery: "DcBattery | None" = None) -> Built:
    """Per block: SCBs -> central inverters -> multi-LV IDT -> VCB -> MV bus -> export, with block and plant auxiliary transformers."""
    kv = _kv(c)
    central_blocks(c, plan, inverter, module, kv, switch, aux, battery)
    mv_yard(c, kv, "abt", aux, export_limit_kw)
    c.lengths_assumption(["string", "scb_inv", "inv_idt", "idt_vcb", "vcb_bus", "aux_mv", "aux_lv", "main_aux_mv", "main_aux_lv", *(["bat_pcs", "dcdc_inv"] if battery else [])])
    return c.built


@dataclass
class BessPlan:
    pcs: PCSSpec
    battery: BatterySpec
    blocks: int
    pcs_per_block: int
    batteries_per_pcs: int

    @property
    def kw(self) -> float:
        return self.blocks * self.pcs_per_block * self.pcs.rated_kva

    @property
    def kwh(self) -> float:
        return self.blocks * self.pcs_per_block * self.batteries_per_pcs * self.battery.energy_kwh


def bess_blocks(c: Ctx, bp: BessPlan, kv: float, switch: str) -> None:
    """Per block: battery containers -> PCS -> multi-LV BESS transformer -> VCB -> MV bus."""
    b, pcs, bat = c.b, bp.pcs, bp.battery
    vv = pcs.ac_voltage
    n = bp.pcs_per_block
    g = b.group("bess_block", "BESS BLOCK", count=bp.blocks, title=f"BESS BLOCK {n * pcs.rated_kva / 1000:g} MVA")
    pg = b.group("pcs_sec", "PCS", count=n, within=g, title=f"{pcs.rated_kva:g} kVA {pcs.model}")
    b.node("bat", "BAT", NodeKind.BATTERY, group=pg, count=bp.batteries_per_pcs, spec=bat.id, title=f"{bat.energy_kwh:g} kWh BATTERY CONTAINER")
    b.node("pcs", "PCS", NodeKind.PCS, group=pg, spec=pcs.id)
    i_dc = pcs.rated_kva * 1000 / bat.voltage_min / bp.batteries_per_pcs
    b.edge("e_bat_pcs", "bat:out", "pcs:dc", V.DC, EdgeHint(DC_FAMILY, nominal_a=i_dc / 1.25), c.L("bat_pcs"), Laying.TRENCH)
    b.node("pcs_dcspd", "BESS-DC-SPD", NodeKind.DC_SPD, group=pg, hint=NodeHint("dc_spd", voltage_v=bat.voltage_max))
    b.edge("e_bat_spd", "bat:out", "pcs_dcspd:line", V.DC)
    b.node("pcs_acspd", "BESS-AC-SPD", NodeKind.AC_SPD, group=pg, voltage_v=vv, hint=NodeHint("ac_spd"))
    b.edge("e_pcs_acspd", "pcs:ac", "pcs_acspd:line", V.LV_AC)
    spec = inline_transformer(
        f"bess-tr-{n * pcs.rated_kva:g}kva-{kv:g}-{'-'.join([f'{vv / 1000:g}'] * n)}-yn{'d11' * n}-project",
        f"{n * pcs.rated_kva:g} kVA {kv:g}/{vv / 1000:g} kV BESS transformer", vv, n, pcs.rated_kva, kv, f"ac_voltage of catalog {pcs.id}",
    )  # fmt: skip
    b.node("bess_tr", "BESS-TR", NodeKind.TRANSFORMER, group=g, spec=spec)
    brk = b.node("pcs_brk", "BESS-ACB", NodeKind.BREAKER, group=pg, voltage_v=vv, hint=NodeHint("lv_breaker", (3,)))
    b.edge("e_pcs_brk", "pcs:ac", f"{brk}:in", V.LV_AC, EdgeHint(LV3_FAMILY), c.L("pcs_tr"), Laying.TRENCH)
    b.edge("e_brk_tr", f"{brk}:out", "bess_tr:lv", V.LV_AC, EdgeHint(LV3_FAMILY), c.L("acb_tr"), Laying.TRENCH)
    b.assume("Each PCS feeds its BESS transformer LV winding through its own LT breaker (BESS-ACB), so a PCS or winding can be isolated and its fault cleared on its own.")
    b.earth("pcs")
    block_feeder(c, g, "_bess", "bess_tr", kv, switch, block_aux=False, tag="BESS")
    b.assume(
        inline_assumption("BESS-TR-nn", spec, n),
        f"Battery DC current is not in the {bat.id} datasheet; the battery-to-PCS cable is sized for the PCS rating at the battery minimum voltage, "
        f"{pcs.rated_kva:g} kVA / {bat.voltage_min:g} V = {pcs.rated_kva * 1000 / bat.voltage_min:.0f} A per PCS (rules do not check it).",
        "Battery DC switchgear is integrated in the battery container (not modelled).",
    )


def bess_ac(c: Ctx, bp: BessPlan, switch: str, aux: bool, export_limit_kw: float | None) -> Built:
    kv = _kv(c)
    bess_blocks(c, bp, kv, switch)
    mv_yard(c, kv, "abt", aux, export_limit_kw)
    c.lengths_assumption(["bat_pcs", "pcs_tr", "acb_tr", "idt_vcb", "vcb_bus", "main_aux_mv", "main_aux_lv"])
    return c.built


@dataclass
class DcBattery:
    """DC-coupled storage per central inverter: battery containers -> DC-DC converter -> inverter DC bus."""

    battery: BatterySpec
    per_inverter: int
    kw_per_inverter: float

    def attach(self, c: Ctx, group: str, x: str, inv: str) -> None:
        b, bat = c.b, self.battery
        b.node(f"bat{x}", "BAT", NodeKind.BATTERY, group=group, count=self.per_inverter, spec=bat.id, title=f"{bat.energy_kwh:g} kWh BATTERY CONTAINER")
        b.node(f"dcdc{x}", "DCDC", NodeKind.DC_DC_CONVERTER, group=group, title="DC-DC CONVERTER", attributes={"rated_kw": self.kw_per_inverter})
        i_bat = self.kw_per_inverter * 1000 / bat.voltage_min
        b.edge(f"e_bat_dcdc{x}", f"bat{x}:out", f"dcdc{x}:in", V.DC, EdgeHint(DC_FAMILY, nominal_a=i_bat / self.per_inverter / 1.25), c.L("bat_pcs"), Laying.TRENCH)
        b.edge(f"e_dcdc_inv{x}", f"dcdc{x}:out", f"{inv}:dc", V.DC, EdgeHint(DC_FAMILY, nominal_a=i_bat / 1.25), c.L("dcdc_inv"), Laying.TRENCH)
        b.earth(f"dcdc{x}")
        b.assume(
            "The DC-DC converter is a dc_dc_converter node (symbol inv.optimiser): the catalog has no DC-DC converter, so it carries no spec. It splits the battery "
            "voltage zone from the inverter DC bus, and takes one DC input of the inverter.",
            f"DC-coupled storage: {self.per_inverter} x {bat.id} per inverter through one DC-DC converter rated {self.kw_per_inverter:g} kW (design choice); "
            f"its cables are sized for {self.kw_per_inverter:g} kW at the battery minimum voltage {bat.voltage_min:g} V (rules do not check them).",
        )


def plan_bess(catalog: Catalog, kw: float, kwh: float | None, pcs_id: str | None, battery_id: str | None, pcs_per_block: int = 2) -> BessPlan:
    pcs_list = [p for p in catalog.filter("pcs") if isinstance(p, PCSSpec)]
    bats = [x for x in catalog.filter("battery") if isinstance(x, BatterySpec)]
    def fits(x: BatterySpec, p: PCSSpec) -> bool:
        return x.voltage_min >= p.dc_voltage_min - 1e-6 and x.voltage_max <= p.dc_voltage_max + 1e-6

    if pcs_id:
        pcs = catalog.get(pcs_id)
        if not isinstance(pcs, PCSSpec):
            raise DesignError(f"{pcs_id!r} is not a PCS")
    else:
        pool = [p for p in pcs_list if any(fits(x, p) for x in bats)]
        pcs = max(pool, key=lambda p: (p.rated_kva, p.id), default=None)
        if pcs is None:
            raise DesignError("the catalog has no PCS with a battery inside its DC window")
    bat = catalog.get(battery_id) if battery_id else next((x for x in bats if fits(x, pcs)), None)
    if not isinstance(bat, BatterySpec):
        raise DesignError(f"no catalog battery inside the DC window of {pcs.id} ({pcs.dc_voltage_min:g}..{pcs.dc_voltage_max:g} V)")
    if bat.voltage_min < pcs.dc_voltage_min - 1e-6 or bat.voltage_max > pcs.dc_voltage_max + 1e-6:
        raise DesignError(f"{bat.id} ({bat.voltage_min:g}..{bat.voltage_max:g} V) is outside the DC window of {pcs.id} ({pcs.dc_voltage_min:g}..{pcs.dc_voltage_max:g} V)")
    n_pcs = math.ceil(kw / pcs.rated_kva - 1e-9)
    blocks = math.ceil(n_pcs / pcs_per_block)
    n_pcs = blocks * pcs_per_block
    per = max(1, math.ceil((kwh or bat.energy_kwh * n_pcs) / (bat.energy_kwh * n_pcs) - 1e-9))
    return BessPlan(pcs, bat, blocks, pcs_per_block, per)


def bess_dc_battery(catalog: Catalog, inverter: InverterSpec, n_inverters: int, kw: float, kwh: float | None, battery_id: str | None) -> DcBattery:
    bats = [x for x in catalog.filter("battery") if isinstance(x, BatterySpec)]
    bat = catalog.get(battery_id) if battery_id else (bats[0] if bats else None)
    if not isinstance(bat, BatterySpec):
        raise DesignError("the catalog has no battery")
    per_inv_kw = kw / n_inverters
    if per_inv_kw > inverter.ac_rated_kva:
        raise DesignError(f"{kw:g} kW of DC-coupled storage over {n_inverters} inverters exceeds their {inverter.ac_rated_kva:g} kVA rating")
    per = max(1, math.ceil((kwh or bat.energy_kwh * n_inverters) / (bat.energy_kwh * n_inverters) - 1e-9))
    return DcBattery(bat, per, round(per_inv_kw, 1))


def bess_dc(c: Ctx, plan: PvPlan, inverter: InverterSpec, module: ModuleSpec, battery: DcBattery, switch: str, aux: bool, export_limit_kw: float | None) -> Built:
    if inverter.topology != "central":
        raise DesignError("the DC-coupled template couples storage on central inverters; pick a central inverter")
    return utility_central(c, plan, inverter, module, switch, aux, export_limit_kw, battery)


def hybrid(c: Ctx, plan: PvPlan, inverter: InverterSpec, module: ModuleSpec, bp: BessPlan, switch: str, aux: bool, export_limit_kw: float | None) -> Built:
    """PV blocks (central or string inverters) and AC-coupled BESS blocks on one MV bus."""
    kv = _kv(c)
    if inverter.topology == "central":
        central_blocks(c, plan, inverter, module, kv, switch, aux)
    else:
        string_blocks(c, plan, inverter, module, kv, switch, aux)
    bess_blocks(c, bp, kv, switch)
    mv_yard(c, kv, "abt", aux, export_limit_kw)
    c.lengths_assumption(["string", "scb_inv", "inv_accb", "accb_idt", "inv_idt", "bat_pcs", "pcs_tr", "acb_tr", "idt_vcb", "vcb_bus"])
    return c.built

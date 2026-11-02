"""Build catalog/*.json from references/catalog-sources/extracted.

Run: uv run python tools/build_catalog.py [--out catalog]
Output is deterministic: entries sorted by id, no timestamps.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unicad.domain.catalog import (  # noqa: E402
    Ampacity,
    BatterySpec,
    CableSpec,
    CellTech,
    DataLoggerSpec,
    Dimensions,
    EquipmentSpec,
    InverterSpec,
    MeterSpec,
    ModuleSpec,
    PCSSpec,
    ProtectionSpec,
    Source,
    TransformerSpec,
)

EXTRACTED = ROOT / "references" / "catalog-sources" / "extracted"
EXTRACTED_REL = "references/catalog-sources/extracted"
RETRIEVED = "2026-10-26"
LATER_RETRIEVED = "2026-10-29"
LATER_DATASHEETS = ("Avaada_", "Solis_S6-GC3P150-200K", "Secure_Elite440", "Solis_S3-Logger")
ALMM_REF = "ALMM List-I"

KIND_FILES = {
    "module": "modules.json",
    "inverter": "inverters.json",
    "battery": "batteries.json",
    "pcs": "pcs.json",
    "transformer": "transformers.json",
    "cable": "cables.json",
    "protection": "protection.json",
    "meter": "meters.json",
    "logger": "loggers.json",
}
ADAPTER = TypeAdapter(EquipmentSpec)

DC_TEMP_COEFF_CU = 0.00393
IS1180_PDF = "raw/standards/IS1180-1_2014_Amendment4_2021_via_MJP.pdf"


@dataclass(frozen=True)
class Skip:
    kind: str
    ref: str
    reason: str


@dataclass
class Build:
    entries: dict[str, list[BaseModel]] = field(default_factory=lambda: defaultdict(list))
    skips: list[Skip] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    filtered: Counter = field(default_factory=Counter)
    almm_stats: list[tuple[str, bool | None]] = field(default_factory=list)

    def add(self, kind: str, ref: str, spec_cls: type[BaseModel], **fields: Any) -> BaseModel | None:
        try:
            spec = spec_cls(**fields)
        except ValidationError as e:
            err = e.errors()[0]
            self.skips.append(Skip(kind, ref, f"validation failed: {'.'.join(map(str, err['loc']))}: {err['msg']}"))
            return None
        self.entries[kind].append(spec)
        return spec

    def skip(self, kind: str, ref: str, reason: str) -> None:
        self.skips.append(Skip(kind, ref, reason))


def read_csv(name: str) -> list[tuple[int, dict[str, str]]]:
    with open(EXTRACTED / name, newline="", encoding="utf-8") as f:
        return [(i, row) for i, row in enumerate(csv.DictReader(f), start=2)]


def num(s: str | None) -> float | None:
    if s is None:
        return None
    m = re.match(r"\s*-?\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def slug(*parts: object) -> str:
    return re.sub(r"[^a-z0-9._]+", "-", "-".join(str(p) for p in parts).lower()).strip("-")


def source(kind: str, raw: str, csv_name: str, row: int, page: str | int | None = None, extra: str = "") -> Source:
    where = f"{raw} p.{page}" if page else raw
    ref = f"{where}; {EXTRACTED_REL}/{csv_name} row {row}" + (f"; {extra}" if extra else "")
    return Source(kind=kind, ref=ref, retrieved=LATER_RETRIEVED if Path(raw).name.startswith(LATER_DATASHEETS) else RETRIEVED)


def dimensions(text: str | None) -> Dimensions | None:
    """'6058x2896x2438' as written W x H x D in the datasheet."""
    if not text:
        return None
    w, h, d = (float(x) for x in text.split("x"))
    return Dimensions(width=w, height=h, depth=d)


def missing(**fields: object) -> list[str]:
    return [k for k, v in fields.items() if v is None]


class Almm:
    """ALMM lists the manufacturing plant, which is often a contract maker, so a model code is matched on its own."""

    def __init__(self) -> None:
        self.by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
        for _, row in read_csv("almm_list1_modules.csv"):
            if not row["parse_suspect"]:
                self.by_code[self.code(row["enlisted_model"])].append(row)

    @staticmethod
    def code(model: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", model.upper())

    @staticmethod
    def valid_to(row: dict[str, str]) -> str | None:
        m = re.fullmatch(r"(\d\d)\.(\d\d)\.(\d{4})", row["valid_to"].strip())
        return f"{m[3]}-{m[2]}-{m[1]}" if m else None

    def lookup(self, model_code: str | None) -> tuple[bool | None, dict[str, str] | None]:
        """True if a currently valid enlistment matches; False if every match has expired; None when unknown."""
        if not model_code:
            return None, None
        rows = self.by_code.get(self.code(model_code), [])
        dated = sorted(((self.valid_to(r), r) for r in rows), key=lambda t: (t[0] or "", t[1]["revision_date"]))
        valid = [t for t in dated if t[0] and t[0] >= RETRIEVED]
        if valid:
            return True, valid[-1][1]
        if dated and all(t[0] for t in dated):
            return False, dated[-1][1]
        return None, None

    @staticmethod
    def ref(row: dict[str, str]) -> str:
        return (f"{ALMM_REF} rev {row['revision_date']} p.{row['pdf_page']}: {row['enlisted_model']} "
                f"({row['manufacturer'].removeprefix('M/s. ').removeprefix('M/s ')}) valid to {row['valid_to'] or 'n/a'}")

    @staticmethod
    def tech(row: dict[str, str] | None) -> CellTech | None:
        return tech_from_text(row["module_type"]) if row else None


def tech_from_text(text: str) -> CellTech | None:
    t = text.lower()
    if "topcon" in t:
        return CellTech.TOPCON
    if "heterojunction" in t or "hjt" in t:
        return CellTech.HJT
    if "perc" in t:
        return CellTech.MONO_PERC
    return None


DATASHEET_ALMM_CODE = {
    "Waaree_": lambda r: r["model"],
    "Adani_": lambda r: f"ASB-M10-144-{r['pmax_w']}",
    "VikramSolar_": lambda r: f"HYPERSOL VSMDH.72.{r['pmax_w']}.05",
    "TataPowerSolar_": lambda r: f"TP{r['pmax_w']}LG10TB",
    "Avaada_": lambda r: f"AVN66G12G{r['pmax_w']}",
}


def almm_code_for(row: dict[str, str]) -> str | None:
    name = Path(row["source_file"]).name
    for prefix, fn in DATASHEET_ALMM_CODE.items():
        if name.startswith(prefix):
            return fn(row)
    return None


def import_modules(b: Build, almm: Almm) -> None:
    b.notes.append(
        "720 Wp class: Avaada Electro AVN66G12G (ENLUME G12, N-type TOPCon bifacial glass-glass, 132 half-cut cells of 210 mm, 2384 x 1303 mm), 700-720 Wp, ALMM List-I enlisted. A 720 Wp TOPCon module is G12 (full 210 mm "
        "cells); the G12R modules in the catalog stop at 635 Wp. The datasheet states no maximum series fuse rating, so `max_series_fuse` is empty and the string-fuse rule warns above two strings per MPPT."
    )
    for line, r in read_csv("module_datasheets.csv"):
        ref = f"module_datasheets.csv row {line} ({r['manufacturer']} {r['model']})"
        listed, match = almm.lookup(almm_code_for(r))
        tech = tech_from_text(r["technology"]) or almm.tech(match)
        fields = dict(
            pmax=num(r["pmax_w"]), voc=num(r["voc_v"]), isc=num(r["isc_a"]), vmp=num(r["vmp_v"]), imp=num(r["imp_a"]),
            temp_coeff_voc=num(r["beta_voc_pct_per_c"]), temp_coeff_isc=num(r["alpha_isc_pct_per_c"]),
            temp_coeff_pmax=num(r["gamma_pmax_pct_per_c"]), length=num(r["length_mm"]), width=num(r["width_mm"]),
            cells=int(r["cells"]) if r["cells"] else None, tech=tech, max_system_voltage=num(r["max_system_v"]),
        )
        if gaps := missing(**fields):
            b.skip("module", ref, f"missing {', '.join(gaps)}")
            continue
        bifaciality = num(r["bifaciality_pct"])
        extra = "cell technology taken from ALMM module type" if not tech_from_text(r["technology"]) else ""
        spec = b.add(
            "module", ref, ModuleSpec,
            id=slug(r["manufacturer"], r["model"]), manufacturer=r["manufacturer"], model=r["model"],
            source=source("datasheet", r["source_file"], "module_datasheets.csv", line, r["const_page"], extra),
            noct_c=num(r["noct_c"]), max_series_fuse=num(r["max_series_fuse_a"]),
            bifacial="bifacial" in r["technology"].lower(),
            bifaciality=bifaciality / 100 if bifaciality is not None else None,
            almm_listed=listed, almm_ref=almm.ref(match) if match else None, **fields,
        )
        if spec:
            b.almm_stats.append((r["manufacturer"], listed))

    cec_candidates = [(line, r) for line, r in read_csv("cec_modules_india_mfrs.csv")]
    for line, r in cec_candidates:
        tech = r["technology"].strip().lower()
        if tech != "mono-c-si":
            b.filtered["CEC module: technology not mono c-Si"] += 1
            continue
        if float(r["pmax_stc_w"]) < 500:
            b.filtered["CEC module: below 500 Wp"] += 1
            continue
        ref = f"cec_modules_india_mfrs.csv row {line} ({r['name']})"
        model = r["name"].removeprefix(r["manufacturer"]).strip()
        listed, match = almm.lookup(model)
        length, width = num(r["length_m"]), num(r["width_m"])
        fields = dict(
            pmax=num(r["pmax_stc_w"]), voc=num(r["voc_v"]), isc=num(r["isc_a"]), vmp=num(r["vmp_v"]), imp=num(r["imp_a"]),
            temp_coeff_voc=num(r["beta_voc_pct_per_k"]), temp_coeff_isc=num(r["alpha_isc_pct_per_k"]),
            temp_coeff_pmax=num(r["gamma_pmp_pct_per_k"]), length=length * 1000 if length else None,
            width=width * 1000 if width else None, cells=int(float(r["cells_in_series"])) if r["cells_in_series"] else None,
            tech=almm.tech(match), max_system_voltage=None,
        )
        if gaps := missing(**fields):
            b.skip("module", ref, f"missing {', '.join(gaps)}")
            continue
        spec = b.add(
            "module", ref, ModuleSpec, id=slug("cec", r["manufacturer"], model), manufacturer=r["manufacturer"], model=model,
            source=Source(kind="cec", ref=f"{EXTRACTED_REL}/cec_modules_india_mfrs.csv row {line} (SAM CEC Modules, {r['sam_date']})", retrieved=RETRIEVED),
            noct_c=num(r["noct_c"]), bifacial=r["bifacial"] == "1", almm_listed=listed,
            almm_ref=almm.ref(match) if match else None, **fields,
        )
        if spec:
            b.almm_stats.append((r["manufacturer"], listed))


def import_inverters(b: Build) -> None:
    b.notes.append(
        "150 kW class: Solis S6-GC3P150K07-NV-ND (150 kW / 165 kVA, 400 V, 7 MPPT, 21 strings = 3 inputs per MPPT, 54 A and 75 A per MPPT). The RVUNL brief asked for 7 MPPT with 2 strings each; "
        "no datasheet found with 7 x 2 inputs, so the closest real unit is catalogued and two strings per MPPT stay within its limits for the 720 Wp module."
    )
    for line, r in read_csv("inverter_datasheets.csv"):
        ref = f"inverter_datasheets.csv row {line} ({r['manufacturer']} {r['model']})"
        if "transformer" in r["note"]:
            b.skip("transformer", ref, "inverter-duty transformer in MV skid: %Z not published in datasheet")
        # rated = active power at unity pf when stated; the headline kVA is the apparent-power ceiling
        rated_kva = num(r["ac_rated_kw"]) or num(r["ac_rated_kva"])
        strings = [int(s) for s in re.findall(r"\d+", r["strings_per_mppt"])]
        eff = num(r["max_efficiency_pct"])
        fields = dict(
            ac_rated_kva=rated_kva, ac_max_kva=num(r["ac_rated_kva"]), ac_voltage=num(r["ac_voltage_v"]),
            dc_max_voltage=num(r["vdc_max_v"]), mppt_min=num(r["mppt_low_v"]), mppt_max=num(r["mppt_high_v"]),
            mppt_count=int(r["mppt_count"]), inputs_per_mppt=min(strings) if strings else None,
            max_input_current_per_mppt=num(r["idc_max_per_mppt_a"]),
            max_short_circuit_current_per_mppt=num(r["isc_max_per_mppt_a"]),
            dc_input_count=int(r["dc_input_count"]) if r["dc_input_count"] else None,
            max_dc_input_current=num(r["idc_max_total_a"]), max_dc_short_circuit_current=num(r["isc_max_total_a"]),
        )
        required = {k: v for k, v in fields.items() if k in ("ac_rated_kva", "ac_max_kva", "ac_voltage", "dc_max_voltage", "mppt_min", "mppt_max")}
        if gaps := missing(**required):
            b.skip("inverter", ref, f"missing {', '.join(gaps)}")
            continue
        extra = "inputs per MPPT differ between trackers; smallest used" if len(set(strings)) > 1 else ""
        if int(r["mppt_count"]) > 1 and not r["idc_max_per_mppt_a"]:
            extra = "datasheet gives DC input totals only; per-MPPT currents left empty"
        if "(derived)" in r["note"]:
            extra = f"{extra}; {r['note']}" if extra else r["note"]
        if r["ac_terminal_max_mm2"]:
            fields["ac_terminal_max_mm2"] = num(r["ac_terminal_max_mm2"])
            t = f"AC terminal from p.{r['ac_terminal_pdf_page']}: '{r['ac_terminal_text']}'; cables per pole not stated"
            extra = f"{extra}; {t}" if extra else t
        b.add(
            "inverter", ref, InverterSpec,
            id=slug(r["manufacturer"], r["model"]), manufacturer=r["manufacturer"], model=r["model"],
            source=source("datasheet", r["source_file"], "inverter_datasheets.csv", line, r["pdf_page"], extra),
            topology="central" if "central" in r["note"] or "skid" in r["note"] else "string", phases=3,
            start_voltage=num(r["start_voltage_v"]), max_efficiency=eff / 100 if eff else None, dimensions=dimensions(r["dimensions_mm"]), **fields,
        )
    import_residential_inverters(b)
    cec = read_csv("cec_inverters_core.csv")
    india_v = sum(1 for _, r in cec if 380 <= float(r["vac_v"]) <= 440)
    b.notes.append(
        f"CEC inverters: none imported. The CEC list has no MPPT count, inputs per MPPT or short-circuit current, "
        f"all required by InverterSpec. {len(cec)} rows examined; {india_v} are at 380-440 V and still lack those fields."
    )


def import_residential_inverters(b: Build) -> None:
    for line, r in read_csv("inverter_residential_datasheets.csv"):
        ref = f"inverter_residential_datasheets.csv row {line} ({r['manufacturer']} {r['model']})"
        strings = [int(s) for s in re.findall(r"\d+", r["strings_per_mppt"])]
        eff = num(r["max_efficiency_pct"])
        extra = r["note"]
        if len(set(strings)) > 1:
            extra = "inputs per MPPT differ between trackers; smallest used; " + extra
        b.add(
            "inverter", ref, InverterSpec,
            id=slug(r["manufacturer"], r["model"]), manufacturer=r["manufacturer"], model=r["model"],
            source=source("datasheet", r["source_file"], "inverter_residential_datasheets.csv", line, r["pdf_page"], extra),
            topology="string", phases=int(r["phases"]), ac_rated_kva=num(r["ac_rated_kw"]), ac_max_kva=num(r["ac_max_kva"]),
            ac_voltage=num(r["ac_voltage_v"]), dc_max_voltage=num(r["vdc_max_v"]), mppt_min=num(r["mppt_low_v"]), mppt_max=num(r["mppt_high_v"]),
            start_voltage=num(r["start_voltage_v"]), mppt_count=int(r["mppt_count"]), inputs_per_mppt=min(strings),
            max_input_current_per_mppt=num(r["idc_max_per_mppt_a"]), max_short_circuit_current_per_mppt=num(r["isc_max_per_mppt_a"]),
            dc_input_count=int(r["dc_input_count"]) if r["dc_input_count"] else None,
            max_dc_input_current=num(r["idc_max_total_a"]), max_dc_short_circuit_current=num(r["isc_max_total_a"]),
            max_efficiency=eff / 100 if eff else None,
        )
    b.notes.append(
        "Residential inverters (1-10 kW, 1- and 3-phase): Growatt MIN TL-X / MOD TL3-X, Solis S6-GR1P, Sungrow SG-RS / SG-RT, Polycab PSIS / PSIT and Havells Enviro GTi are catalogued "
        "from their datasheets. Luminous NXI: no datasheet could be downloaded (the product pages give capacity and efficiency only), so none is catalogued. "
        "Where a datasheet states efficiency per group of models rather than per model it is left empty."
    )


def import_bess(b: Build) -> None:
    for line, r in read_csv("bess_pcs_datasheets.csv"):
        ref = f"bess_pcs_datasheets.csv row {line} ({r['model']})"
        if r["transformer_vector"]:
            b.skip("transformer", ref, "MV transformer in PCS skid: %Z not published in datasheet")
        raw, page = r["source_file"], r["pdf_page"]
        vmin, vmax = num(r["dc_voltage_min_v"]), num(r["dc_voltage_max_v"])
        b.add(
            "pcs", ref, PCSSpec, id=slug(r["manufacturer"], r["model"]), manufacturer=r["manufacturer"], model=r["model"],
            source=source("datasheet", raw, "bess_pcs_datasheets.csv", line, page),
            rated_kva=num(r["ac_power_kva"]), ac_voltage=num(r["ac_voltage_v"]), dc_voltage_min=vmin, dc_voltage_max=vmax,
            dimensions=dimensions(r["dimensions_mm"]),
        )
        if not r["energy_kwh"]:
            continue
        series = re.search(r"(\d+)S", r["configuration"])
        cell_v = re.search(r"([\d.]+) V", r["cell"])
        model = r["model"].split("-")[0]
        if any(s.id == slug(r["manufacturer"], model) for s in b.entries["battery"]):
            continue
        b.add(
            "battery", ref, BatterySpec, id=slug(r["manufacturer"], model), manufacturer=r["manufacturer"], model=model,
            source=source("datasheet", raw, "bess_pcs_datasheets.csv", line, page, "nominal voltage = series cells x cell voltage; datasheet gives no DC current"),
            chemistry="lfp", energy_kwh=num(r["energy_kwh"]),
            nominal_voltage=round(int(series[1]) * float(cell_v[1]), 1), voltage_min=vmin, voltage_max=vmax,
            dimensions=dimensions(r["dimensions_mm"]),
        )


def import_transformers(b: Build) -> None:
    b.notes.append(
        "Inverter-duty transformers: not catalogued. The only citable %Z are two BHEL purchase specifications "
        "(2.7 MVA 33 kV 6.25 %, 5.5 MVA 33 kV 7 %, see idt_impedance_citations.csv). Both give the LV voltage as a range set per inverter "
        "(340-410 V, 500-700 V), and TransformerSpec needs a single lv_kv. No standard %Z ladder for 1.6-12.5 MVA was found, and no purchaser "
        "specification with one stated LV voltage could be downloaded (SECI tender documents returned HTTP 500). A project that needs an IDT carries "
        "an inline TransformerSpec on the node and records in design_basis.assumptions which cited values it borrowed."
    )
    ratings_33kv_min = 16
    for line, r in read_csv("transformer_is1180_ratings_losses.csv"):
        if r["phase"] != "3":
            b.skip("transformer", f"transformer_is1180_ratings_losses.csv row {line}", "single-phase rating: TransformerSpec is three-phase")
            continue
        kva = float(r["kva"])
        for hv in (11, 33):
            if hv == 33 and kva < ratings_33kv_min:
                continue
            b.add(
                "transformer", f"transformer_is1180_ratings_losses.csv row {line}", TransformerSpec,
                id=slug("is1180", f"{hv}kv", f"{kva:g}kva", "dyn11"), manufacturer="Generic (IS 1180)",
                model=f"{kva:g} kVA {hv}/0.433 kV",
                source=source("standard", IS1180_PDF, "transformer_is1180_ratings_losses.csv", line, r["pdf_page"],
                              "Dyn11 per IS 1180-1:2014 cl. 6.6 (Amd 4 allows Dyn1); 33 kV class ratings from 16 kVA per IS 1180-1 Amd 4 Table 1 (raw/standards/IS1180-1_2014_Amendment4_2021_via_MJP.pdf p.1)"),
                rated_kva=kva, hv_kv=hv, lv_kv=(0.433,), vector_group="Dyn11", impedance_pct=float(r["impedance_pct"]),
                cooling="ONAN", standard="IS 1180 (Part 1):2014",
            )


def kei_row_problem(r: dict[str, str], r20_ref: dict[tuple[str, str], float]) -> str | None:
    """The KEI extract shifts columns on some rows; catch them against the IS 8130 R20 values from the Polycab table."""
    ref20 = r20_ref.get((r["material"], r["size_mm2"]))
    r20, r_ac = num(r["r_dc20_ohm_km"]), num(r["r_ac_ohm_km"])
    reactances = [x for x in (num(r["x_armoured_ohm_km"]), num(r["x_unarmoured_ohm_km"])) if x is not None]
    if ref20 is None or r20 is None or abs(r20 - ref20) / ref20 > 0.03:
        return "extracted R20 disagrees with the IS 8130 value (column shift in the KEI extract)"
    if r_ac is None or not r20 <= r_ac <= 1.4 * r20 or any(not 0.04 <= x <= 0.3 for x in reactances):
        return "extracted R or X is implausible (column shift in the KEI extract)"
    return None


def import_cables(b: Build) -> None:
    r90 = {r["size_mm2"]: r for _, r in read_csv("polycab_lt_conductor.csv")}
    reactance = {r["size_mm2"]: r for _, r in read_csv("polycab_lt_reactance.csv")}
    seen: dict[tuple, tuple[str, str, str]] = {}
    pending_2x1c: dict[tuple, tuple[int, dict[str, str]]] = {}
    for line, r in read_csv("polycab_lt_ampacity.csv"):
        ref = f"polycab_lt_ampacity.csv row {line} ({r['type_code']} {r['cores_in_weight_table']}C {r['size_mm2']} mm2)"
        cores = r["cores_in_weight_table"]
        armoured = r["armoured"] == "True"
        key = (r["material"], armoured, cores, r["size_mm2"], r["arrangement"])
        amps = (r["ground_a"], r["duct_a"], r["air_a"])
        if key in seen:
            if seen[key] != amps:
                raise ValueError(f"conflicting ampacity for {key}: {seen[key]} vs {amps}")
            b.skip("cable", ref, "duplicate of a strip/wire-armour variant with identical ampacity")
            continue
        seen[key] = amps
        if r["arrangement"] == "2x1c":
            pending_2x1c[key[:4]] = (line, r)
            continue
        two = pending_2x1c.pop(key[:4], None)
        material = r["material"].lower()
        reduced = cores == "3.5"
        n_cores = 3 if reduced else int(cores)
        reactance_col = ("x_1c_armoured_ohm_km" if armoured else "x_1c_unarmoured_ohm_km") if cores == "1" else "x_multicore_ohm_km"
        rr = r90.get(r["size_mm2"])
        extra = "R at 90 C from polycab_lt_conductor.csv, X from polycab_lt_reactance.csv"
        if two:
            extra += f"; ampacity_2x1c from row {two[0]}, ampacity_air/ground/duct are the 3 single-core cable circuit"
        if reduced:
            extra += "; 3.5C reduced neutral, neutral size not stated in catalogue"
        b.add(
            "cable", ref, CableSpec,
            id=slug("polycab-lt-xlpe", material, f"{cores}c", r["size_mm2"], "arm" if armoured else "unarm"),
            manufacturer="Polycab", model=f"{r['type_code']} {cores}C {r['size_mm2']} sq.mm",
            source=source("catalog", "raw/datasheets/cables/Polycab_LT_Cable_Catalog_V9.pdf", "polycab_lt_ampacity.csv", line, r["pdf_page"], extra),
            service="lv_ac", conductor=material, cores=n_cores, reduced_neutral=reduced, size_mm2=float(r["size_mm2"]), insulation="xlpe",
            armoured=armoured, voltage_grade="1.1 kV", ampacity_air=num(r["air_a"]), ampacity_ground=num(r["ground_a"]),
            ampacity_duct=num(r["duct_a"]),
            ampacity_2x1c=Ampacity(air=num(two[1]["air_a"]), ground=num(two[1]["ground_a"]), duct=num(two[1]["duct_a"])) if two else None,
            r_ohm_per_km_90c=num(rr[f"r_ac90_{material}_ohm_km"]) if rr else None,
            x_ohm_per_km=num(reactance[r["size_mm2"]][reactance_col]) if r["size_mm2"] in reactance else None,
        )
    for (_, _, cores, size), (line, r) in pending_2x1c.items():
        b.skip("cable", f"polycab_lt_ampacity.csv row {line} ({r['type_code']} {cores}C {size} mm2)", "2x1c row without a matching 3x1c row")

    r20_ref = {(mat, size): float(row[f"r_dc20_{mat.lower()}_ohm_km"]) for size, row in r90.items() for mat in ("Cu", "Al")}
    kei_seen: dict[tuple, tuple] = {}
    kei_pending: dict[tuple, tuple[int, dict[str, str]]] = {}
    for line, r in read_csv("kei_lt_cables.csv"):
        ref = f"kei_lt_cables.csv row {line} ({r['standard']} {r['cores']}C {r['size_mm2']} mm2)"
        size, _, neutral = r["size_mm2"].partition("/")
        if reason := kei_row_problem({**r, "size_mm2": size}, r20_ref):
            b.skip("cable", ref, reason)
            continue
        dedupe_key = (r["material"], r["insulation"], r["cores"], r["arrangement"], r["armour_scope"], r["size_mm2"])
        values = tuple(r[k] for k in ("r_dc20_ohm_km", "r_ac_ohm_km", "x_armoured_ohm_km", "x_unarmoured_ohm_km", "ground_a", "duct_a", "air_a"))
        if dedupe_key in kei_seen:
            if kei_seen[dedupe_key] != values:
                raise ValueError(f"conflicting KEI rows for {dedupe_key}")
            b.skip("cable", ref, "duplicate of a solid/stranded conductor row with identical values")
            continue
        kei_seen[dedupe_key] = values
        if r["arrangement"] == "2x1c":
            kei_pending[dedupe_key[:3] + dedupe_key[4:]] = (line, r)
            continue
        two = kei_pending.pop(dedupe_key[:3] + dedupe_key[4:], None)
        scope = r["armour_scope"]
        variants = {"armoured & unarmoured": (True, False), "armoured only": (True,), "unarmoured only": (False,)}[scope]
        insulation, material = r["insulation"].lower(), r["material"].lower()
        reduced = r["cores"] == "3.5"
        hot_r = num(r["r_ac_ohm_km"]) if num(r["rac_temp_c"]) == 90 else None
        warm_r = num(r["r_ac_ohm_km"]) if num(r["rac_temp_c"]) == 70 and insulation == "pvc" else None
        for armoured in variants:
            x = num(r["x_armoured_ohm_km"] if armoured else r["x_unarmoured_ohm_km"])
            if x is None and r["arrangement"] == "multicore":
                x = num(r["x_armoured_ohm_km"]) or num(r["x_unarmoured_ohm_km"])
            extra = "" if hot_r else "R is the AC resistance at 70 C, the PVC maximum conductor temperature (r_ohm_per_km_70c)" if warm_r else f"R given at {r['rac_temp_c']} C only, so r_ohm_per_km_90c left empty"
            if two:
                extra += ("; " if extra else "") + f"ampacity_2x1c from row {two[0]}, ampacity_air/ground/duct are the 3 single-core cable circuit"
            b.add(
                "cable", ref, CableSpec,
                id=slug("kei-lt", insulation, material, f"{r['cores']}c", size, "arm" if armoured else "unarm"),
                manufacturer="KEI", model=f"{r['standard']} {r['cores']}C {size} sq.mm" + (f" (neutral {neutral})" if neutral else ""),
                source=source("catalog", "raw/datasheets/cables/KEI_Technical_Handbook.pdf", "kei_lt_cables.csv", line, r["pdf_page"], extra),
                service="lv_ac", conductor=material, cores=3 if reduced else int(r["cores"]), reduced_neutral=reduced,
                neutral_size_mm2=float(neutral) if neutral else None, size_mm2=float(size), insulation=insulation,
                armoured=armoured, voltage_grade="1.1 kV", ampacity_air=num(r["air_a"]), ampacity_ground=num(r["ground_a"]),
                ampacity_duct=num(r["duct_a"]),
                ampacity_2x1c=Ampacity(air=num(two[1]["air_a"]), ground=num(two[1]["ground_a"]), duct=num(two[1]["duct_a"])) if two else None,
                r_ohm_per_km_90c=hot_r, r_ohm_per_km_70c=warm_r, x_ohm_per_km=x,
            )
    for (line, r) in kei_pending.values():
        b.skip("cable", f"kei_lt_cables.csv row {line}", "2x1c row without a matching 3x1c row")

    for line, r in read_csv("polycab_solar_dc_en50618.csv"):
        size = r["size_mm2"]
        r20 = float(r["r_dc20_ohm_km"])
        b.add(
            "cable", f"polycab_solar_dc_en50618.csv row {line} ({size} mm2)", CableSpec,
            id=slug("polycab-h1z2z2-k", f"{float(size):g}mm2"), manufacturer="Polycab", model=f"H1Z2Z2-K {float(size):g} sq.mm",
            source=source("catalog", "raw/datasheets/cables/Polycab_Solar_H1Z2Z2-K_EN50618_DS.pdf", "polycab_solar_dc_en50618.csv", line, r["pdf_page"],
                          f"ampacity is single cable in free air at {r['ambient_c']} C ambient / {r['max_conductor_c']} C conductor; R at 90 C = R20 x (1 + {DC_TEMP_COEFF_CU} x 70)"),
            service="dc_solar", conductor="cu", cores=1, size_mm2=float(size), insulation="xlpo", armoured=False,
            voltage_grade="1.5 kV DC", ampacity_air=num(r["single_free_air_a"]),
            r_ohm_per_km_90c=round(r20 * (1 + DC_TEMP_COEFF_CU * 70), 4),
        )


def import_ht_cables(b: Build) -> None:
    code = {("al", 1): "A2XFaY/A2XWaY", ("cu", 1): "2XFaY/2XWaY", ("al", 3): "A2XFY/A2XWY", ("cu", 3): "2XFY/2XWY"}
    arrangement = "ampacity_air/ground/duct are a circuit of three single-core cables" 
    for line, r in read_csv("havells_ht_cables.csv"):
        size = float(r["size_mm2"])
        cores = int(r["cores"])
        if size < 50 or (cores == 3 and size > 400) or size > 630:
            continue
        depth = "1050 mm" if r["voltage_grade"] == "19/33 kV" else "900 mm"
        for material in ("al", "cu"):
            b.add(
                "cable", f"havells_ht_cables.csv row {line} ({r['voltage_grade']} {cores}C {r['size_mm2']} mm2 {material})", CableSpec,
                id=slug("havells-ht-xlpe", material, f"{cores}c", r["size_mm2"], r["voltage_grade"].replace(" kV", "kv").replace("6.35/11", "11").replace("19/33", "33"), "arm"),
                manufacturer="Havells", model=f"{code[(material, cores)]} {r['voltage_grade']} {cores}C {r['size_mm2']} sq.mm",
                source=source("catalog", "raw/datasheets/cables/Havells_Industrial_Cables_Range_Catalogue.pdf", "havells_ht_cables.csv", line, r["pdf_page"],
                              f"IS 7098 Part-2 XLPE, earthed grade, Table {r['table']}; R is the approximate AC resistance at 90 C; X at 50 Hz; "
                              f"ground 30 C / air 40 C / soil 150 C.cm/W, laid at {depth}; round-wire and flat-strip armour share one rating"
                              + (f"; {arrangement}" if cores == 1 else "")),
                service="mv_ac", conductor=material, cores=cores, size_mm2=size, insulation="xlpe", armoured=True,
                voltage_grade=r["voltage_grade"], ampacity_air=num(r[f"{material}_air_a"]), ampacity_ground=num(r[f"{material}_ground_a"]),
                ampacity_duct=num(r[f"{material}_duct_a"]), r_ohm_per_km_90c=num(r[f"r_ac90_{material}"]), x_ohm_per_km=num(r["x_ohm_km"]),
            )


def import_protection(b: Build) -> None:
    ladders = json.loads((EXTRACTED / "protection_ladders.json").read_text())
    ref_base = f"{EXTRACTED_REL}/protection_ladders.json"

    def src(block: dict[str, Any], page: object = None, extra: str = "") -> Source:
        p = page if page is not None else block["pdf_page"]
        pages = "-".join(map(str, p)) if isinstance(p, list) else p
        ref = f"{block['source']} p.{pages}; {ref_base}" + (f"; {extra}" if extra else "")
        return Source(kind="catalog", ref=ref, retrieved=RETRIEVED)

    mcb = ladders["mcb"]
    for curve in "BCD":
        for in_a in mcb["rated_current_a"]:
            icu = 50 if in_a <= 4 else 10
            for poles in (1, 2, 3, 4):
                b.add(
                    "protection", "protection_ladders.json mcb", ProtectionSpec,
                    id=slug("schneider-ic60n", f"{curve}{in_a:g}", f"{poles}p"), manufacturer="Schneider Electric",
                    model=f"Acti9 iC60N {curve}{in_a:g} {poles}P", source=src(mcb, extra="Icu at 220-240 V (1P) / 380-415 V (2-4P), IEC 60947-2"),
                    device="mcb", rated_current=in_a, rated_voltage=240 if poles == 1 else 415, poles=poles,
                    breaking_capacity_ka=icu, curve=curve,
                )

    mccb = ladders["mccb"]
    for frame in mccb["frames"]:
        term = frame["terminal_with_spreaders"]
        for kind, rating in frame["types"].items():
            for in_a in frame["in_a"]:
                for poles in (3, 4):
                    b.add(
                        "protection", f"protection_ladders.json mccb {frame['frame']}", ProtectionSpec,
                        id=slug("lk-dsine", frame["frame"], kind, f"{in_a}a", f"{poles}p"), manufacturer="Lauritz Knudsen",
                        model=f"dsine {frame['frame']} {kind} {in_a}A {poles}P",
                        source=src(mccb, extra=f"Icu at 415 V AC; terminal capacity p.{mccb['terminal_pdf_page']}: {mccb['terminal_note']}"),
                        device="mccb", rated_current=in_a, rated_voltage=415, poles=poles,
                        breaking_capacity_ka=rating["icu_415v_ka"], terminal_max_mm2=term["max_mm2"], terminal_cables_per_pole=term["cables_per_pole"],
                    )
    b.notes.append("MCCB DC ratings (250/500/750 V) not catalogued: ProtectionSpec has no AC/DC field and no 1500 V DC ladder was found.")

    acb = ladders["acb"]
    icu_by_frame = acb["mtz2_icu_ka_440vac"]
    for in_a in acb["frames_a"]["MTZ2"]:
        key = "800-1600A" if in_a <= 1600 else "2000A" if in_a == 2000 else "2500-4000A"
        for variant, icu in icu_by_frame[key].items():
            for poles in (3,) if variant.startswith("L") else (3, 4):
                b.add(
                    "protection", "protection_ladders.json acb MTZ2", ProtectionSpec,
                    id=slug("schneider-mtz2", variant, f"{in_a}a", f"{poles}p"), manufacturer="Schneider Electric",
                    model=f"MasterPacT MTZ2 {variant} {in_a}A {poles}P",
                    source=src(acb, extra="Icu at 440 V AC; L variants are 3P only"),
                    device="acb", rated_current=in_a, rated_voltage=440, poles=poles, breaking_capacity_ka=icu,
                )
    b.notes.append("ACB MTZ1 and MTZ3 not catalogued: only their frame ratings were transcribed, no Icu.")

    fuse = ladders["gpv_fuse"]
    for in_a in fuse["rated_current_a"]:
        code = f"PV-{in_a:g}A10F".replace(".", "-")
        b.add(
            "protection", "protection_ladders.json gpv_fuse", ProtectionSpec,
            id=slug("eaton-bussmann", code), manufacturer="Eaton Bussmann", model=code, source=src(fuse, extra="10x38 mm, IEC 60269-6 gPV"),
            device="fuse_gpv", rated_current=in_a, rated_voltage=fuse["rated_voltage_vdc"], poles=1,
            breaking_capacity_ka=fuse["breaking_capacity_ka"],
        )
    fuse15 = ladders["gpv_fuse_1500v"]
    for in_a in fuse15["rated_current_a"]:
        code = fuse15["catalog_number"].format(amps=in_a)
        b.add(
            "protection", "protection_ladders.json gpv_fuse_1500v", ProtectionSpec,
            id=slug("mersen", code), manufacturer="Mersen", model=code,
            source=src(fuse15, extra="10x85 mm, IEC 60269-6 gPV, manufacturer-hosted datasheet"),
            device="fuse_gpv", rated_current=in_a, rated_voltage=fuse15["rated_voltage_vdc"], poles=1,
            breaking_capacity_ka=fuse15["breaking_capacity_ka"],
        )
    b.notes.append("gPV fuses: 1000 V DC 10x38 (Eaton, third-party mirror) and 1500 V DC 10x85 (Mersen HP15M, manufacturer-hosted) ladders are catalogued.")

    vcb = ladders["vcb_36kv"]
    for variant in vcb["variants"]:
        for isc in variant["isc_ka"]:
            code = f"VD4 36.{variant['in_a'] // 100:02d}.{int(isc)}"
            b.add(
                "protection", "protection_ladders.json vcb_36kv", ProtectionSpec,
                id=slug("abb-vd4", "36kv", f"{variant['in_a']}a", f"{isc:g}ka"), manufacturer="ABB", model=code,
                source=src(vcb, extra=f"fixed version, IEC 62271-100; short-time withstand (3 s) equals rated Isc per p.18; total breaking time <= {vcb['total_breaking_time_ms']} ms per p.{vcb['total_breaking_time_page']}"),
                device="vcb", rated_current=variant["in_a"], rated_voltage=vcb["rated_voltage_kv"] * 1000, poles=3,
                breaking_capacity_ka=isc, short_time_withstand_ka=isc, short_time_duration_s=3, break_time_s=vcb["total_breaking_time_ms"] / 1000,
            )
    b.notes.append(
        "VCB 36 kV: ABB VD4 offers 630 A at 16 and 20 kA only; 25 kA starts at 1250 A, so there is no 630 A / 25 kA entry. 1600, 2000 and 2500 A are catalogued at 25, 31.5 and 40 kA "
        "(2500 A rated at ambient below 40 C); 3150 A (assembled poles) is not."
    )

    rmu = ladders["rmu_36kv"]
    b.add(
        "protection", "protection_ladders.json rmu_36kv", ProtectionSpec,
        id="generic-rmu-36kv-630a-25ka", manufacturer="Generic (GeM buyer specification)", model="33 kV SF6 outdoor RMU 2 LBS + 1 VCB 630 A",
        source=src(rmu, extra="purchaser's technical specification, not a manufacturer datasheet; verify against the chosen supplier"),
        device="rmu", rated_current=rmu["rated_current_a"], rated_voltage=rmu["rated_voltage_kv"] * 1000, poles=3,
        breaking_capacity_ka=rmu["vcb_breaking_ka"], short_time_withstand_ka=rmu["short_time_ka_3s"], short_time_duration_s=3,
    )

    ct = ladders["ct_33kv"]
    b.add(
        "protection", "protection_ladders.json ct_33kv", ProtectionSpec,
        id="generic-ct-33kv-200-100-50-1-1a", manufacturer="Generic (WBSEDCL specification)", model="33 kV outdoor CT 200-100-50/1-1 A",
        source=src(ct, extra="a utility's procurement specification, not a manufacturer datasheet"),
        device="ct", rated_voltage=ct["highest_system_voltage_kv"] * 1000, poles=1, ratio=ct["ratio"],
        accuracy_class=" + ".join(f"{c['class']} ({c['burden_va']} VA, {c['use']})" for c in ct["cores"]),
        short_time_withstand_ka=ct["short_time_ka_3s"], short_time_duration_s=3,
    )
    b.notes.append("LV-side CT/PT and a 36 kV manufacturer RMU: no citable text-extractable source was found, so none are catalogued.")

    la = ladders["la_30kv"]
    b.add(
        "protection", "protection_ladders.json la_30kv", ProtectionSpec,
        id="abb-polim-d-24-ur30kv", manufacturer="ABB", model="POLIM-D 24-xx (Uc 24 kV, Ur 30 kV)",
        source=src(la, extra="third-party-hosted copy of the ABB data sheet; line discharge class 1, IEC 60099-4"),
        device="lightning_arrester", rated_voltage=la["ur_kv"] * 1000, poles=1,
        nominal_discharge_current_ka=la["nominal_discharge_current_ka"],
    )

    spd = ladders["spd_pv_dc"]
    for v in spd["variants"]:
        b.add(
            "protection", "protection_ladders.json spd_pv_dc", ProtectionSpec,
            id=slug("finder", v["code"]), manufacturer="Finder", model=v["code"],
            source=src(spd, extra="3 varistor modules (+, -, PE) per catalogue page 12"),
            device="spd_dc", rated_voltage=v["ucpv_vdc"], poles=3, spd_type="2",
            nominal_discharge_current_ka=v["in_ka"], max_discharge_current_ka=v["imax_ka"],
        )


def import_switchgear(b: Build) -> None:
    ot_pdf = "raw/datasheets/protection/ABB_OT_OTM_switch-disconnectors_1SCC301020C0201.pdf"
    for line, r in read_csv("abb_ot_switch_disconnectors.csv"):
        icw = num(r["icw_1s_ka"])
        extra = (
            "AC-22A current from the ordering table (column headed 400 V; the technical-data tables give AC-22A up to 415 V), rated_voltage is that 415 V; "
            "the switch's rated insulation and operational voltage AC-20 is 1000 V; non-fusible load-break switch, so no breaking capacity"
            + (f"; Icw 1 s from the technical-data tables on pdf p.{r['icw_pdf_page']}" if icw else "; short-time withstand not read: the large-frame table columns are ambiguous in the text layer")
        )
        b.add(
            "protection", f"abb_ot_switch_disconnectors.csv row {line}", ProtectionSpec,
            id=slug("abb", r["type"]), manufacturer="ABB", model=f"{r['type']} switch-disconnector {r['poles']}P",
            source=source("catalog", ot_pdf, "abb_ot_switch_disconnectors.csv", line, r["pdf_page"], extra),
            device="ac_isolator", rated_current=num(r["ac22a_a"]), rated_voltage=415, poles=int(r["poles"]),
            short_time_withstand_ka=icw, short_time_duration_s=1 if icw else None,
        )
    otdc_pdf = "raw/datasheets/protection/ABB_OTDC_DC_switch-disconnectors_1SCC301022C0201_2024-06.pdf"
    for line, r in read_csv("abb_otdc_switch_disconnectors.csv"):
        parallel = r["circuits"] == "1" and r["poles"] == "4"
        extra = (
            f"OTDC {r['series']}, {r['circuits']} circuit(s) on {r['poles']} poles, rated current is DC-21B at {r['voltage_vdc']} V DC"
            + ("; two poles in parallel per polarity (ABB connection bar kit, installation mandatory)" if parallel else "")
            + (f"; DC-PV2 rating is {r['dc_pv2_a']} A at 1500 V" if r["dc_pv2_a"] else "")
            + f"; Icw {r['icw_ka']} kA for {r['icw_s']} s from the technical-data table on pdf p.{r['tech_pdf_page']}"
        )
        b.add(
            "protection", f"abb_otdc_switch_disconnectors.csv row {line}", ProtectionSpec,
            id=slug("abb", r["type"]), manufacturer="ABB", model=f"{r['type']} DC switch-disconnector {r['poles']}P {r['voltage_vdc']} V DC",
            source=source("catalog", otdc_pdf, "abb_otdc_switch_disconnectors.csv", line, r["pdf_page"], extra),
            device="dc_isolator", rated_current=num(r["dc21b_a"]), rated_voltage=num(r["voltage_vdc"]), poles=int(r["poles"]),
            short_time_withstand_ka=num(r["icw_ka"]), short_time_duration_s=num(r["icw_s"]),
        )

    phase2 = json.loads((EXTRACTED / "protection_phase2.json").read_text())
    ref_base = f"{EXTRACTED_REL}/protection_phase2.json"

    def src(block: dict[str, Any], extra: str = "", page: object = None) -> Source:
        ref = f"{block['source']} p.{page or block['pdf_page']}; {ref_base}" + (f"; {extra}" if extra else "")
        return Source(kind="catalog", ref=ref, retrieved=RETRIEVED)

    finder = phase2["spd_ac_finder"]
    for v in finder["variants"]:
        b.add(
            "protection", "protection_phase2.json spd_ac_finder", ProtectionSpec,
            id=slug("finder", v["code"]), manufacturer="Finder", model=v["code"],
            source=src(finder, f"{finder['note']} Maximum backup fuse {v['max_backup_fuse_a']} A gG; Up {v['up_in_kv']} kV at In; Imax {v['imax_ka']} kA"),
            device="spd_ac", rated_voltage=v["uc_vac"], poles=v["poles"], nominal_discharge_current_ka=v["in_ka"],
            max_discharge_current_ka=v["imax_ka"], spd_type="2",
        )
    dehn = phase2["spd_ac_dehn"]
    for v in dehn["variants"]:
        b.add(
            "protection", "protection_phase2.json spd_ac_dehn", ProtectionSpec,
            id=slug("dehn", v["code"]), manufacturer="DEHN", model=f"DEHNguard {v['code']}",
            source=src(dehn, f"part no. {v['part_no']}, single-pole, for {v['system']}; Up <= {v['up_kv']} kV; backup fuse {v['backup_fuse']}; one unit per phase"),
            device="spd_ac", rated_voltage=v["uc_vac"], poles=v["poles"], nominal_discharge_current_ka=v["in_ka"], spd_type="2",
        )
    dehn3 = phase2["spd_ac_dehn_3p"]
    for v in dehn3["variants"]:
        b.add(
            "protection", "protection_phase2.json spd_ac_dehn_3p", ProtectionSpec,
            id=slug("dehn", v["code"]), manufacturer="DEHN", model=f"DEHNguard {v['code']}",
            source=src(dehn3, f"part no. {v['part_no']}; {dehn3['note']} Up <= {v['up_kv']} kV; Imax {v['imax_ka']} kA; max mains-side backup fuse {v['max_backup_fuse_a']} A gG; remote signalling {v['remote_signalling']}"),
            device="spd_ac", rated_voltage=v["uc_vac"], poles=v["poles"], nominal_discharge_current_ka=v["in_ka"],
            max_discharge_current_ka=v["imax_ka"], spd_type="2",
        )
    it = phase2["spd_ac_dehn_it"]
    req = it["it_requirement"]
    for v in it["variants"]:
        b.add(
            "protection", "protection_phase2.json spd_ac_dehn_it", ProtectionSpec,
            id=slug("dehn", v["code"], f"{v['units']}x"), manufacturer="DEHN", model=f"{v['units']} x DEHNguard {v['code']} (3-0 circuit)",
            source=src(it, f"part no. {v['part_no']} x {v['units']}; {it['note']} IT requirement: '{req['text']}', {req['url']} (printed p.{req['printed_page']}, Figure {req['figure']}); "
                       f"Up <= {v['up_kv']} kV; Imax {v['imax_ka']} kA; max mains-side backup fuse {v['max_backup_fuse_a']} A gG; remote signalling {v['remote_signalling']}"),
            device="spd_ac", rated_voltage=v["uc_vac"], poles=v["poles"], nominal_discharge_current_ka=v["in_ka"],
            max_discharge_current_ka=v["imax_ka"], spd_type="2",
        )
    s0 = json.loads((EXTRACTED / "protection_s0.json").read_text())
    sfu = s0["sfu_36kv"]
    b.add(
        "protection", "protection_s0.json sfu_36kv", ProtectionSpec,
        id="abb-safeplus-36-f-module-200a", manufacturer="ABB", model="SafePlus 36 F-module switch-fuse-disconnector",
        source=Source(
            kind="catalog", retrieved=LATER_RETRIEVED,
            ref=f"{sfu['source']} p.{sfu['pdf_page']}; {EXTRACTED_REL}/protection_s0.json sfu_36kv; {sfu['standard']}; rated voltage {sfu['rated_voltage_kv']} kV (a 33 kV system); "
                f"rated normal current {sfu['rated_normal_current_a']} A depends on the fuse-link; breaking current {sfu['breaking_current_ka']} kA is limited by the HV fuse-links; "
                f"transfer current {sfu['transfer_current_a']} A; fuse-links to {sfu['fuse_link_standard']} up to {sfu['max_fuse_link_rated_current_a']} A rated (p.{sfu['fuse_pdf_page']}); "
                f"busbars {sfu['busbar_short_time_withstand_ka']} kA for {sfu['busbar_short_time_s']} s",
        ),
        device="sfu", rated_current=sfu["rated_normal_current_a"], rated_voltage=sfu["rated_voltage_kv"] * 1000, poles=3,
        breaking_capacity_ka=sfu["breaking_current_ka"],
    )
    b.notes.append(
        "33 kV fuse-switch: ABB SafePlus 36 F-module (36 kV, 200 A, breaking current 20 kA limited by the fuse-links, fuse-links up to 63 A). It is a gas-insulated RMU module, not an outdoor pole-mounted fuse switch; "
        "no datasheet for an outdoor 33 kV fuse-switch was found."
    )
    pt = phase2["pt_33kv"]
    b.add(
        "protection", "protection_phase2.json pt_33kv", ProtectionSpec,
        id="generic-pt-33kv-wbsedcl-0.5-3p-100va", manufacturer="Generic (WBSEDCL specification)",
        model="33 kV outdoor single-phase PT 33000/sqrt3:110/sqrt3-110/sqrt3 V",
        source=src(pt, "a utility's procurement specification, not a manufacturer datasheet; voltage factor " + pt["voltage_factor"]),
        device="pt", rated_voltage=pt["highest_voltage_kv"] * 1000, poles=1, ratio=pt["ratio"],
        accuracy_class=" + ".join(f"{c['class']} ({c['burden_va']} VA, {c['use']})" for c in pt["cores"]), burden_va=pt["cores"][0]["burden_va"],
    )
    la = phase2["la_36kv_counter"]
    b.add(
        "protection", "protection_phase2.json la_36kv_counter", ProtectionSpec,
        id="generic-la-36kv-ur30-20ka-counter", manufacturer="Generic (buyer technical specification)",
        model="36 kV class station gapless metal-oxide surge arrester Ur 30 kV Uc 25 kV with discharge counter",
        source=src(la, f"{la['product']}; discharge counter and leakage current meter on pdf p.{la['counter_pdf_page']}: {la['counter']}; line discharge class {la['line_discharge_class']}"),
        device="lightning_arrester", rated_voltage=la["ur_kv"] * 1000, poles=1, nominal_discharge_current_ka=la["nominal_discharge_current_ka"], surge_counter=True,
    )
    b.notes.append("Isolators: ABB OT E-series (AC, 200-2500 A, 3/4P) and OTDC (DC, 100-250 A, 1000 V S1.0 and 1500 V S2.0, 2/4P). The 1250-2500 A OT frames carry no short-time withstand because the text layer of their table columns is ambiguous.")
    b.notes.append("AC SPD Type 2: Finder 7P.23/7P.25 for 230/400 V systems, DEHN DG SE CI 440 FM (single pole, for 400/690 V systems), DEHN DG M WE 600 (3+0) and a set of three DEHN DG SE H 1000 FM (Uc 1000 V, 3-0 circuit) for IT buses up to 909 V. DC SPD 1500 V is the Finder 7P.23.9.500.x015 entry above.")
    b.notes.append("33 kV PT and the 36 kV class arrester with discharge counter come from purchaser specifications (WBSEDCL; a buyer's switchgear surge-arrester specification whose issuer the document does not name), not manufacturer datasheets.")


def import_p5_protection(b: Build) -> None:
    p5 = json.loads((EXTRACTED / "protection_p5.json").read_text())
    ref_base = f"{EXTRACTED_REL}/protection_p5.json"

    def src(block: dict[str, Any], extra: str = "", page: object = None) -> Source:
        ref = f"{block['source']} p.{page or block['pdf_page']}; {ref_base}" + (f"; {extra}" if extra else "")
        return Source(kind="catalog", ref=ref, retrieved=RETRIEVED)

    h10 = p5["acb_mtz2_h10"]
    for in_a in h10["frames_a"]:
        b.add(
            "protection", "protection_p5.json acb_mtz2_h10", ProtectionSpec,
            id=slug("schneider-mtz2", "h10", f"{in_a}a", "3p"), manufacturer="Schneider Electric", model=f"MasterPacT MTZ2 H10 {in_a}A 3P",
            source=src(h10, f"Icu {h10['icu_ka_1150vac']} kA at {h10['ue_vac']} V AC; {h10['note']}"),
            device="acb", rated_current=in_a, rated_voltage=h10["ue_vac"], poles=3, breaking_capacity_ka=h10["icu_ka_1150vac"],
            short_time_withstand_ka=h10["icw_ka_1s"], short_time_duration_s=1,
        )
    tmax = p5["mccb_tmax_1150v"]
    for v in tmax["variants"]:
        for in_a in v["in_a"]:
            for poles in tmax["poles"]:
                b.add(
                    "protection", f"protection_p5.json mccb_tmax_1150v {v['frame']}", ProtectionSpec,
                    id=slug("abb-tmax", v["frame"].split()[0], v["version"], f"{in_a}a", f"{poles}p"), manufacturer="ABB",
                    model=f"SACE Tmax {v['frame']} {v['version']} {in_a}A {poles}P ({v['ue_vac']} V AC)",
                    source=src(tmax, f"Icu {v['icu_ka_at_ue']} kA at Ue {v['ue_vac']} V AC; rated current from the electronic trip-unit sensor table on p.{tmax['sensor_pdf_page']}; {tmax['note']}"),
                    device="mccb", rated_current=in_a, rated_voltage=v["ue_vac"], poles=poles, breaking_capacity_ka=v["icu_ka_at_ue"],
                )
    easy = p5["mccb_easypact_solar_800v"]
    term = easy["terminal_assumption"]
    for fr in easy["frames"]:
        for code, icu in fr["icu_ka"].items():
            for in_a in fr["in_a"]:
                ref = fr["ref"].format(code=code, in_a=in_a)
                b.add(
                    "protection", f"protection_p5.json mccb_easypact_solar_800v {fr['frame']}", ProtectionSpec,
                    id=slug("schneider-easypact-solar", ref), manufacturer="Schneider Electric",
                    model=f"EasyPact Solar {fr['frame']} {fr['release']} {in_a}A 3P {icu} kA at 800 V AC ({ref})",
                    source=src(easy, f"Icu {icu} kA at Ue {easy['ue_vac']} V AC; {easy['note']}"),
                    device="mccb", rated_current=in_a, rated_voltage=easy["ue_vac"], poles=3, breaking_capacity_ka=icu,
                    terminal_max_mm2=term["max_mm2"], terminal_cables_per_pole=term["cables_per_pole"],
                )
    ct = p5["ct_11kv"]
    b.add(
        "protection", "protection_p5.json ct_11kv", ProtectionSpec,
        id="generic-ct-11kv-apdcl-0.5-5p10", manufacturer="Generic (APDCL specification)", model="11 kV outdoor CT, 0.5 + 5P10, 25 kA 3 s",
        source=src(ct, ct["note"]), device="ct", rated_voltage=ct["highest_system_voltage_kv"] * 1000, poles=1,
        accuracy_class=" + ".join(f"{c['class']} ({c['burden_va']} VA, {c['use']})" for c in ct["cores"]),
        short_time_withstand_ka=ct["short_time_ka"], short_time_duration_s=ct["short_time_s"],
    )
    pt = p5["pt_11kv"]
    b.add(
        "protection", "protection_p5.json pt_11kv", ProtectionSpec,
        id="generic-pt-11kv-apdcl-0.5-100va", manufacturer="Generic (APDCL specification)", model="11 kV outdoor single-phase PT 11/0.11 kV, 0.5, 100 VA",
        source=src(pt, f"{pt['note']} Voltage factor {pt['voltage_factor']}"), device="pt", rated_voltage=pt["highest_system_voltage_kv"] * 1000, poles=1,
        ratio=pt["ratio"], accuracy_class=" + ".join(f"{c['class']} ({c['burden_va']} VA, {c['use']})" for c in pt["cores"]), burden_va=pt["cores"][0]["burden_va"],
    )
    la = p5["la_11kv"]
    b.add(
        "protection", "protection_p5.json la_11kv", ProtectionSpec,
        id="generic-la-11kv-apdcl-ur9-10ka", manufacturer="Generic (APDCL specification)",
        model="11 kV station class gapless metal-oxide surge arrester Ur 9 kV Uc 7.65 kV 10 kA",
        source=src(la, f"line discharge class {la['line_discharge_class']}; {la['note']}"), device="lightning_arrester",
        rated_voltage=la["ur_kv"] * 1000, poles=1, nominal_discharge_current_ka=la["nominal_discharge_current_ka"],
    )
    b.notes.append(
        "1500 V DC switch for a string combiner outgoing circuit around 500 A: ABB OTDC M-series (315, 400, 500, 630, 800 A two-pole; 800 and 1000 A as two poles in parallel per polarity), "
        "DC-21B at 1500 V, DC-PV2 500 A for the 630 and 800 A sizes, Icw 10 kA for 1 s. Before this the largest 1500 V DC switch was the 250 A OTDC S2.0."
    )
    b.notes.append(
        "800 V ~400 A breakers beyond EasyPact: none added. Schneider ComPacT NSX400K (Icu 36 kA at 800 V AC) appears in Schneider's online user guide, which refused the download (HTTP 403); "
        "the 2014 ComPacT NSX catalogue found elsewhere has no 800 V breaking-capacity row. ABB Tmax T5 V 400 A (1150 V AC, Icu 12 kA) remains the other 400 A entry at this voltage."
    )
    b.notes.append(
        "800 V-class LT breakers: Schneider MasterPacT MTZ2 H10 (1150 V AC, 800-4000 A, 3P, Icu 50 kA), ABB Tmax T4/T5 V and T6 L (1000-1150 V AC, 100-800 A, 3P/4P, Icu 12 kA) and Schneider EasyPact Solar MCCB 400/800 (800 V AC, 125-800 A, 3P, Icu 25/36/50 kA) are catalogued. "
        "Siemens 3WL / 3VA and Schneider ComPacT NSX/NS at 800 V and above were not found in a text-extractable source."
    )
    b.notes.append(
        "33 kV inverter-duty transformers with an 800 V LV winding: gap. The only public sources found are manufacturer product pages (for example JDS Transformers, '33/0.800 kV, up to 25 MVA') that state "
        "no %Z, and the two BHEL purchase specifications, whose LV windings are 340-410 V and 500-700 V. TransformerSpec needs %Z, so none is catalogued; string-inverter blocks at 800 V "
        "use the same project-specific inline spec as central inverters (BHEL PS 439-1304 values, reported as an assumption)."
    )
    b.notes.append(
        "11 kV CT, PT and arrester: purchaser (APDCL) specifications, not manufacturer datasheets. The CT carries no ratio because the specification leaves the primary current to the project."
    )


METER_SOURCE = Source(
    kind="standard",
    ref="CEA (Installation and Operation of Meters) Regulations, 2006 as amended (2019): accuracy class table, Regulation 5/Schedule. "
        "Interface meters 0.2S; consumer meters up to 650 V 1.0 (direct) or 0.5S (CT-operated), above 650 V to 33 kV 0.5S, above 33 kV 0.2S",
    retrieved=RETRIEVED,
)

METERS = [
    ("net", "direct", "1.0"), ("net", "ct", "0.5S"), ("net", "ct_pt", "0.5S"),
    ("generation", "direct", "1.0"), ("generation", "ct", "0.5S"), ("generation", "ct_pt", "0.5S"),
    ("abt", "ct_pt", "0.2S"), ("check", "ct_pt", "0.2S"),
]


def import_meters(b: Build) -> None:
    for function, connection, cls in METERS:
        b.add(
            "meter", "hand-authored", MeterSpec, id=slug("generic-meter", function, connection, cls),
            manufacturer="Generic (CEA metering regulations)",
            model=f"{function.upper() if function == 'abt' else function.capitalize()} meter {connection.upper()} class {cls}",
            source=METER_SOURCE, function=function, accuracy_class=cls, connection=connection,
        )


def import_meter_datasheets(b: Build) -> None:
    b.notes.append(
        "Multifunction meter: Secure Elite 440 (Secure Meters, India), RS-485 Modbus RTU, CT operated, classes 0.2S / 0.5S / 1.0. The datasheet lists the three classes without tying them to model numbers 441-448, "
        "so one entry per class carries the family name. Schneider EasyLogic PM2230 was not catalogued: its datasheet host refused the download (HTTP 403)."
    )
    for line, r in read_csv("meter_datasheets.csv"):
        b.add(
            "meter", f"meter_datasheets.csv row {line}", MeterSpec, id=slug(r["manufacturer"], r["model"]), manufacturer=r["manufacturer"], model=r["model"],
            source=source("datasheet", r["source_file"], "meter_datasheets.csv", line, r["pdf_page"], r["note"]),
            function=r["function"], accuracy_class=r["accuracy_class"], connection=r["connection"], communication=r["communication"] or None,
        )


def import_loggers(b: Build) -> None:
    b.notes.append(
        "Data logger: a catalog kind of its own (`logger`, DataLoggerSpec), drawn with the inst.datalogger symbol. Solis S3-Logger (India edition datasheet) is the only entry: it reads Solis inverters, meters and weather stations "
        "over four RS-485 ports, so it matches the Solis inverter catalogued for the 150 kW class. A logger of another make needs its own datasheet; the Huawei SmartLogger and Sungrow Logger1000 sheets were not fetched."
    )
    def span(r: dict[str, str], key: str) -> tuple[float, float] | None:
        lo, hi = num(r[f"{key}_min"]), num(r[f"{key}_max"])
        return (lo, hi) if lo is not None and hi is not None else None

    for line, r in read_csv("logger_datasheets.csv"):
        b.add(
            "logger", f"logger_datasheets.csv row {line}", DataLoggerSpec, id=slug(r["manufacturer"], r["model"]), manufacturer=r["manufacturer"], model=r["model"],
            source=source("datasheet", r["source_file"], "logger_datasheets.csv", line, r["pdf_page"], r["note"]),
            rs485_ports=int(r["rs485_ports"]), max_devices=int(r["max_devices"]) if r["max_devices"] else None,
            max_devices_per_rs485_port=int(r["max_devices_per_rs485_port"]) if r["max_devices_per_rs485_port"] else None,
            rs485_protocols=tuple(p.strip() for p in r["rs485_protocols"].split(";") if p.strip()), ethernet_ports=int(r["ethernet_ports"] or 0),
            ethernet_protocols=tuple(p.strip() for p in r["ethernet_protocols"].split(";") if p.strip()),
            ac_supply_v=span(r, "ac_supply_v"), dc_supply_v=span(r, "dc_supply_v"), ip_rating=r["ip_rating"] or None,
            dimensions=Dimensions(width=num(r["length_mm"]), depth=num(r["width_mm"]), height=num(r["height_mm"])) if r["length_mm"] else None,
        )


def serialise(specs: list[BaseModel]) -> str:
    ordered = sorted(specs, key=lambda s: s.id)
    return json.dumps([s.model_dump(mode="json") for s in ordered], indent=2, ensure_ascii=False) + "\n"


def report(b: Build, almm_total: int) -> str:
    lines = ["# Catalog build report", "", "Generated by `tools/build_catalog.py`. Do not edit.", "", "## Entries", "", "| Kind | Count |", "|---|---|"]
    total = 0
    for kind in KIND_FILES:
        n = len(b.entries[kind])
        total += n
        lines.append(f"| {kind} | {n} |")
    lines += [f"| **total** | **{total}** |", "", "## By manufacturer", "", "| Kind | Manufacturer | Count |", "|---|---|---|"]
    for kind in KIND_FILES:
        for mfr, n in sorted(Counter(s.manufacturer for s in b.entries[kind]).items()):
            lines.append(f"| {kind} | {mfr} | {n} |")

    stats = Counter(listed for _, listed in b.almm_stats)
    lines += ["", "## ALMM match (modules)", "",
              f"ALMM List-I rows loaded: {almm_total}. A module is matched on its model code against ALMM enlisted models; ALMM names the plant owner, which can be a contract maker, so the manufacturer is not compared. "
              f"`true` = a currently valid enlistment (valid to on or after {RETRIEVED}); `false` = matches exist and all have expired; "
              "`null` = no match or an undated match (not guessed).", "",
              "| almm_listed | Modules |", "|---|---|"]
    for label, key in (("true", True), ("false", False), ("null", None)):
        lines.append(f"| {label} | {stats.get(key, 0)} |")
    per_mfr: dict[str, Counter] = defaultdict(Counter)
    for mfr, listed in b.almm_stats:
        per_mfr[mfr][listed] += 1
    if per_mfr:
        lines += ["", "| Manufacturer | true | false | null |", "|---|---|---|---|"]
        for mfr in sorted(per_mfr):
            c = per_mfr[mfr]
            lines.append(f"| {mfr} | {c.get(True, 0)} | {c.get(False, 0)} | {c.get(None, 0)} |")

    lines += ["", "## Filtered before import", ""]
    lines += [f"- {k}: {n}" for k, n in sorted(b.filtered.items())] or ["- none"]

    lines += ["", "## Skipped rows", "", "Grouped by kind and reason; up to five example source rows each.", ""]
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in b.skips:
        groups[(s.kind, s.reason)].append(s.ref)
    lines += ["| Kind | Reason | Rows | Examples |", "|---|---|---|---|"]
    for (kind, reason), refs in sorted(groups.items()):
        lines.append(f"| {kind} | {reason} | {len(refs)} | {'; '.join(refs[:5])} |")
    if not groups:
        lines.append("| - | none | 0 | |")

    lines += ["", "## Notes", ""] + [f"- {n}" for n in b.notes]
    return "\n".join(lines) + "\n"


def build(out: Path) -> Build:
    b = Build()
    almm = Almm()
    import_modules(b, almm)
    import_inverters(b)
    import_bess(b)
    import_transformers(b)
    import_cables(b)
    import_ht_cables(b)
    import_protection(b)
    import_switchgear(b)
    import_p5_protection(b)
    import_meters(b)
    import_meter_datasheets(b)
    import_loggers(b)

    ids = Counter(s.id for specs in b.entries.values() for s in specs)
    if dupes := [i for i, n in ids.items() if n > 1]:
        raise ValueError(f"duplicate ids: {sorted(dupes)[:10]}")

    out.mkdir(parents=True, exist_ok=True)
    for kind, name in KIND_FILES.items():
        text = serialise(b.entries[kind])
        for item in json.loads(text):
            ADAPTER.validate_python(item)
        (out / name).write_text(text, encoding="utf-8")
    (out / "REPORT.md").write_text(report(b, sum(len(v) for v in almm.by_code.values())), encoding="utf-8")
    return b


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "catalog")
    args = parser.parse_args()
    b = build(args.out)
    print({k: len(v) for k, v in b.entries.items()}, f"skipped {len(b.skips)}")


if __name__ == "__main__":
    main()

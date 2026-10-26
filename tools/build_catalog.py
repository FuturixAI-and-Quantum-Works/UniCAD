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
    return Source(kind=kind, ref=ref, retrieved=RETRIEVED)


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
}


def almm_code_for(row: dict[str, str]) -> str | None:
    name = Path(row["source_file"]).name
    for prefix, fn in DATASHEET_ALMM_CODE.items():
        if name.startswith(prefix):
            return fn(row)
    return None


def import_modules(b: Build, almm: Almm) -> None:
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
        b.add(
            "inverter", ref, InverterSpec,
            id=slug(r["manufacturer"], r["model"]), manufacturer=r["manufacturer"], model=r["model"],
            source=source("datasheet", r["source_file"], "inverter_datasheets.csv", line, r["pdf_page"], extra),
            topology="central" if "central" in r["note"] or "skid" in r["note"] else "string", phases=3,
            start_voltage=num(r["start_voltage_v"]), max_efficiency=eff / 100 if eff else None, **fields,
        )
    cec = read_csv("cec_inverters_core.csv")
    india_v = sum(1 for _, r in cec if 380 <= float(r["vac_v"]) <= 440)
    b.notes.append(
        f"CEC inverters: none imported. The CEC list has no MPPT count, inputs per MPPT or short-circuit current, "
        f"all required by InverterSpec. {len(cec)} rows examined; {india_v} are at 380-440 V and still lack those fields."
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
        )


def import_transformers(b: Build) -> None:
    ratings_33kv_min = 100
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
                              "Dyn11 per IS 1180-1:2014 cl. 6.6 (Amd 4 allows Dyn1); 33 kV class offered from 100 kVA per BIS/ITMA presentation"),
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
        for armoured in variants:
            x = num(r["x_armoured_ohm_km"] if armoured else r["x_unarmoured_ohm_km"])
            if x is None and r["arrangement"] == "multicore":
                x = num(r["x_armoured_ohm_km"]) or num(r["x_unarmoured_ohm_km"])
            extra = "" if hot_r else f"R given at {r['rac_temp_c']} C only, so r_ohm_per_km_90c left empty"
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
                r_ohm_per_km_90c=hot_r, x_ohm_per_km=x,
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
        for kind, rating in frame["types"].items():
            for in_a in frame["in_a"]:
                for poles in (3, 4):
                    b.add(
                        "protection", f"protection_ladders.json mccb {frame['frame']}", ProtectionSpec,
                        id=slug("lk-dsine", frame["frame"], kind, f"{in_a}a", f"{poles}p"), manufacturer="Lauritz Knudsen",
                        model=f"dsine {frame['frame']} {kind} {in_a}A {poles}P", source=src(mccb, extra="Icu at 415 V AC"),
                        device="mccb", rated_current=in_a, rated_voltage=415, poles=poles,
                        breaking_capacity_ka=rating["icu_415v_ka"],
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
    b.notes.append("gPV fuses: only the 1000 V DC 10x38 ladder is catalogued; no 1500 V ladder was obtainable.")

    spd = ladders["spd_pv_dc"]
    for v in spd["variants"]:
        b.add(
            "protection", "protection_ladders.json spd_pv_dc", ProtectionSpec,
            id=slug("finder", v["code"]), manufacturer="Finder", model=v["code"],
            source=src(spd, extra="3 varistor modules (+, -, PE) per catalogue page 12"),
            device="spd_dc", rated_voltage=v["ucpv_vdc"], poles=3, spd_type="2",
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
    import_protection(b)
    import_meters(b)

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

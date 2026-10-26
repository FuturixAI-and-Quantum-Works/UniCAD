"""Extract STC electrical tables from Indian-market module datasheets to one CSV.

Run: python3 scripts/extract_module_datasheets.py   (needs poppler's pdftotext)
Output: extracted/module_datasheets.csv (one row per power bin).

Per-bin electrical values (pmax, vmp, imp, voc, isc, efficiency) are parsed from the PDF text. Per-datasheet
constants (temperature coefficients, dimensions, cells, bifaciality, system voltage) were read off the same PDF
text by hand on 2026-10-26 and are written in CONSTANTS below; each carries the PDF page it came from.

Datasheets whose electrical table is an image (Premier, Renewsys, Tata TP540HG10TB, Tata brochure) are not
parsed here; see SOURCES.md gaps.
"""

import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "raw/datasheets/modules"
OUT = ROOT / "extracted/module_datasheets.csv"
NUM = r"\d+(?:\.\d+)?"


def text(pdf):
    return subprocess.run(["pdftotext", "-layout", str(DS / pdf), "-"], capture_output=True, text=True, check=True).stdout


def nums_after(line, label_re, n):
    m = re.search(label_re, line)
    vals = re.findall(NUM, line[m.end():].replace("52 .01", "52.01"))
    return vals[:n]


def first_line(lines, label_re):
    return next(l for l in lines if re.search(label_re, l))


def waaree(pdf, prefix):
    rows = []
    for ln in text(pdf).splitlines():
        m = re.match(rf"^\s*({prefix}-\d+)\s+(.*)$", ln)
        if m:
            v = re.findall(NUM, m.group(2))
            if len(v) == 11:
                # Pmax STC, Pmax NOCT, Vmp STC, Vmp NOCT, Imp STC, Imp NOCT, Isc STC, Isc NOCT, Voc STC, Voc NOCT, Eff
                rows.append({"model": m.group(1), "pmax_w": v[0], "vmp_v": v[2], "imp_a": v[4], "isc_a": v[6],
                             "voc_v": v[8], "efficiency_pct": v[10]})
    return rows


def label_rows(pdf, labels, n, model_fmt):
    lines = text(pdf).splitlines()
    cols = {k: nums_after(first_line(lines, rx), rx, n) for k, rx in labels.items()}
    return [{"model": model_fmt.format(p=cols["pmax_w"][j]), **{k: cols[k][j] for k in cols}} for j in range(n)]


def tata_tp600(pdf):
    # Row labels of the STC table are an image. Value rows appear in the order Pmax, tolerance, efficiency,
    # Vmp, Imp, Voc, Isc (same order as the labelled BSTC table directly below in the PDF).
    lines = [l for l in text(pdf).splitlines() if l.strip()]
    start = next(i for i, l in enumerate(lines) if re.fullmatch(r"\s*570\s+575\s+580\s+585\s+590\s+595\s+600\s*", l))
    block = [re.findall(NUM, lines[start + k]) for k in (0, 2, 3, 4, 5, 6)]
    keys = ["pmax_w", "efficiency_pct", "vmp_v", "imp_a", "voc_v", "isc_a"]
    return [{"model": f"TP600LG10TB series {block[0][j]} Wp", **{k: block[i][j] for i, k in enumerate(keys)}} for j in range(7)]


SHEETS = [
    ("Waaree_Elite_BiN-08-565-600_144_TOPCon.pdf", "Waaree", lambda f: waaree(f, "BiN-08"),
     dict(technology="N-type TOPCon bifacial, dual glass", cells=144, bifaciality_pct="80±10", alpha_isc_pct_per_c=0.046,
          beta_voc_pct_per_c=-0.26, gamma_pmax_pct_per_c=-0.30, noct_c="43±2", length_mm=2278, width_mm=1134,
          thickness_mm=33, weight_kg=32.5, max_system_v=1500, max_series_fuse_a=30, const_page=2)),
    ("Waaree_EliteR_BiN-21-600-635_132_TOPCon.pdf", "Waaree", lambda f: waaree(f, "BiN-21"),
     dict(technology="N-type TOPCon bifacial (G12R), dual glass", cells=132, bifaciality_pct="80±10",
          alpha_isc_pct_per_c=0.046, beta_voc_pct_per_c=-0.26, gamma_pmax_pct_per_c=-0.30, noct_c="43±2",
          length_mm=2382, width_mm=1134, thickness_mm=35, weight_kg=33.8, max_system_v=1500, max_series_fuse_a=30, const_page=2)),
    ("Waaree_Ahnay_Bi-55-520-550_144_PERC.pdf", "Waaree", lambda f: waaree(f, "Bi-55"),
     dict(technology="Mono PERC bifacial, dual glass", cells=144, bifaciality_pct="70±10", alpha_isc_pct_per_c=0.05,
          beta_voc_pct_per_c=-0.25, gamma_pmax_pct_per_c=-0.34, noct_c="43±2", length_mm=2272, width_mm=1133,
          thickness_mm=35, weight_kg=32.5, max_system_v=1500, max_series_fuse_a=25, const_page=2)),
    ("Adani_ElanShine_TOPCon_G2G_GenII.pdf", "Adani Solar",
     lambda f: label_rows(f, {"pmax_w": r"Peak power, Pmax\(Wp\)", "vmp_v": r"Maximum voltage, Vmpp \(V\)",
                              "imp_a": r"Maximum current, Impp \(A\)", "voc_v": r"Open circuit voltage, Voc \(V\)",
                              "isc_a": r"Short circuit current, Isc \(A\)", "efficiency_pct": r"Module efﬁciency \(%\)"},
                          7, "Elan Shine TOPCon G2G Gen-II {p} Wp"),
     dict(technology="N-type TOPCon bifacial, glass-glass, M10 144 half-cut", cells=144, bifaciality_pct="80±5",
          alpha_isc_pct_per_c=0.028, beta_voc_pct_per_c=-0.24, gamma_pmax_pct_per_c=-0.32, noct_c="45±2",
          length_mm=2278, width_mm=1133, thickness_mm=30, weight_kg=31.3, max_system_v=1500, max_series_fuse_a=30,
          const_page=2)),
    ("VikramSolar_Hypersol_M10R_580-605_144.pdf", "Vikram Solar",
     lambda f: label_rows(f, {"pmax_w": r"Peak Power Pmax \(Wp\)", "vmp_v": r"Maximum Voltage Vmpp \(V\)",
                              "imp_a": r"Maximum Current Impp \(A\)", "voc_v": r"Open Circuit Voltage Voc \(V\)",
                              "isc_a": r"Short Circuit Current Isc \(A\)", "efficiency_pct": r"Module Efficiency \(%\)"},
                          6, "Hypersol M10R 144 {p} Wp"),
     dict(technology="N-type bifacial, glass-glass, M10R", cells=144, bifaciality_pct="80", alpha_isc_pct_per_c=0.046,
          beta_voc_pct_per_c=-0.26, gamma_pmax_pct_per_c=-0.30, noct_c="45±2", length_mm=2278, width_mm=1134,
          thickness_mm=30, weight_kg=31.6, max_system_v=1500, max_series_fuse_a=30, const_page=2)),
    ("TataPowerSolar_TP600LG10TB_PERC.pdf", "Tata Power Solar", tata_tp600,
     dict(technology="Mono PERC bifacial, glass-transparent backsheet", cells=156, bifaciality_pct="70±10",
          alpha_isc_pct_per_c=0.05, beta_voc_pct_per_c=-0.28, gamma_pmax_pct_per_c=-0.36, noct_c="45±1",
          length_mm=2456, width_mm=1134, thickness_mm=35, weight_kg=31, max_system_v=1500, max_series_fuse_a=25,
          const_page=2,
          note="Text layer lacks row labels; STC order and all constants verified against the rendered page")),
]

FIELDS = ["source_file", "manufacturer", "model", "pmax_w", "vmp_v", "imp_a", "voc_v", "isc_a", "efficiency_pct",
          "technology", "cells", "bifaciality_pct", "alpha_isc_pct_per_c", "beta_voc_pct_per_c", "gamma_pmax_pct_per_c",
          "noct_c", "length_mm", "width_mm", "thickness_mm", "weight_kg", "max_system_v", "max_series_fuse_a",
          "const_page", "note"]

rows = []
for pdf, mfr, parse, const in SHEETS:
    for r in parse(pdf):
        rows.append({"source_file": f"raw/datasheets/modules/{pdf}", "manufacturer": mfr, **r, **const})
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows)
print(f"{OUT.name}: {len(rows)} rows")

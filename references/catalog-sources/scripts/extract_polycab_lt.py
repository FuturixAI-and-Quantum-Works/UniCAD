"""Extract current-rating tables from the Polycab LT cable catalogue (V9) to CSV.

Run: uv run python scripts/extract_polycab_lt.py   (needs poppler's pdftotext on PATH)
Outputs:
  extracted/polycab_lt_ampacity.csv      per product family (IS 7098-1 XLPE / IS 1554-1 PVC power cables)
  extracted/polycab_lt_conductor.csv     conductor resistance table (DC @20C, AC @90C) from catalogue p.10
  extracted/polycab_lt_reactance.csv     approx. reactance at 50 Hz from catalogue p.9
  extracted/polycab_lt_pvc_vs_xlpe_al_3c.csv  PVC (IS 1554-1) vs XLPE (IS 7098-1) 3/3.5/4C Al ratings, catalogue p.8
Each row carries pdf_page so it traces back to raw/datasheets/cables/Polycab_LT_Cable_Catalog_V9.pdf.
"""

import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "raw/datasheets/cables/Polycab_LT_Cable_Catalog_V9.pdf"
OUT = ROOT / "extracted"
NUM = r"-?\d+(?:\.\d+)?"


def page_text(i):
    return subprocess.run(["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(PDF), "-"],
                          capture_output=True, text=True, check=True).stdout


def n_pages():
    info = subprocess.run(["pdfinfo", str(PDF)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", info).group(1))


def numeric_rows(lines):
    for ln in lines:
        toks = ln.split()
        if toks and all(re.fullmatch(NUM, t) for t in toks):
            yield [float(t) if "." in t else int(t) for t in toks]


def title_of(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    head = " ".join(lines[:2])
    return re.sub(r"\s+", " ", head) if "POLYCAB" in head else ""


def ampacity():
    rows, cores_seen, codes_seen, last_title = [], [], [], ""
    for i in range(1, n_pages() + 1):
        text = page_text(i)
        title = title_of(text) or last_title
        last_title = title
        found = re.findall(r"\b(\d(?:\.5)?)\s*[xX]\s*\d{1,4}(?![.\d])", text)
        if found:
            cores_seen = sorted(set(found), key=float)
        codes = sorted(set(re.findall(r"\b(A?2X[A-Za-z]{0,3}Y)\b", text)))
        if codes:
            codes_seen = codes
        if "Current carrying capacity" not in text or "POWER CABLE" not in title:
            continue
        code = re.search(r"\(([A-Z0-9]+)\)", title)
        material = "Cu" if "COPPER" in title else "Al" if "ALUMINIUM" in title else ""
        insulation = "XLPE" if "XLPE" in title else "PVC" if "PVC" in title else ""
        standard = "IS 7098-1" if "7098" in title else "IS 1554-1" if "1554" in title else ""
        armoured = "UNARMORED" not in title and "UNARMOURED" not in title
        amb = [re.search(rf"{k} temperature:\s*(\d+)", text, re.I) for k in ("Air Ambient", "Ground ambient", "Conductor operating")]
        amb = [m.group(1) if m else "" for m in amb]
        body = text.split("Current carrying capacity", 1)[1].split("Air Ambient", 1)[0]
        base = {"pdf_page": i, "product_title": title, "type_code": code.group(1) if code else "/".join(codes_seen),
                "material": material, "insulation": insulation, "standard": standard, "armoured": armoured,
                "cores_in_weight_table": "/".join(cores_seen),
                "air_ambient_c": amb[0], "ground_ambient_c": amb[1], "conductor_temp_c": amb[2]}
        for r in numeric_rows(body.splitlines()):
            # Multicore tables: size, ground, duct, air, R20. Single-core tables: size, then
            # ground/duct/air each for 2 and 3 single-core cables, then R20.
            if len(r) == 5:
                rows.append({**base, "size_mm2": r[0], "arrangement": "multicore",
                             "ground_a": r[1], "duct_a": r[2], "air_a": r[3], "r_dc20_ohm_km": r[4]})
            elif len(r) == 8:
                for k, arr in ((0, "2x1c"), (1, "3x1c")):
                    rows.append({**base, "size_mm2": r[0], "arrangement": arr, "ground_a": r[1 + k],
                                 "duct_a": r[3 + k], "air_a": r[5 + k], "r_dc20_ohm_km": r[7]})
    fields = ["pdf_page", "product_title", "type_code", "material", "insulation", "standard", "armoured",
              "cores_in_weight_table", "arrangement", "size_mm2", "ground_a", "duct_a", "air_a", "r_dc20_ohm_km",
              "air_ambient_c", "ground_ambient_c", "conductor_temp_c"]
    write(OUT / "polycab_lt_ampacity.csv", fields, rows)


def find_page(needle):
    return next(i for i in range(1, n_pages() + 1) if needle in page_text(i))


def conductor_and_reactance():
    p_react, p_cond = find_page("Approximate Reactance"), find_page("A.C Resistance at")
    t9, t10 = page_text(p_react), page_text(p_cond)
    react = []
    sect = t9.split("Approximate Reactance", 1)[1]
    for ln in sect.splitlines():
        toks = ln.split()
        if toks and re.fullmatch(NUM, toks[0]) and all(re.fullmatch(NUM, t) or t == "-" for t in toks) and len(toks) == 4:
            react.append({"pdf_page": p_react, "size_mm2": toks[0], "x_1c_unarmoured_ohm_km": toks[1],
                          "x_1c_armoured_ohm_km": toks[2].replace("-", ""), "x_multicore_ohm_km": toks[3].replace("-", "")})
    write(OUT / "polycab_lt_reactance.csv", list(react[0]), react)
    cond = []
    for ln in t10.splitlines():
        toks = ln.replace("*", "").split()
        if len(toks) == 9 and all(re.fullmatch(NUM, t) or t == "-" for t in toks):
            cond.append({"pdf_page": p_cond, "size_mm2": toks[0], "r_dc20_cu_ohm_km": toks[5], "r_dc20_al_ohm_km": toks[6],
                         "r_ac90_cu_ohm_km": toks[7], "r_ac90_al_ohm_km": toks[8]})
    write(OUT / "polycab_lt_conductor.csv", list(cond[0]), cond)


def comparative_pvc_xlpe():
    p = find_page("COMPARATIVE CURRENT RATING")
    rows = []
    for ln in page_text(p).split("COMPARATIVE CURRENT RATING", 1)[1].split("Comparision", 1)[0].splitlines():
        toks = ln.split()
        if len(toks) == 7 and all(re.fullmatch(NUM, t) for t in toks):
            rows.append({"pdf_page": p, "size_mm2": toks[0], "pvc_is1554_ground_a": toks[1], "pvc_is1554_air_a": toks[2],
                         "pvc_vdrop_mv_per_a_m": toks[3], "xlpe_is7098_ground_a": toks[4], "xlpe_is7098_air_a": toks[5],
                         "xlpe_vdrop_mv_per_a_m": toks[6]})
    write(OUT / "polycab_lt_pvc_vs_xlpe_al_3c.csv", list(rows[0]), rows)


def write(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{path.name}: {len(rows)} rows")


if __name__ == "__main__":
    ampacity()
    conductor_and_reactance()
    comparative_pvc_xlpe()

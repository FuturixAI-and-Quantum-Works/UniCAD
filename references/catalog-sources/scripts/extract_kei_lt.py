"""Extract LT (<=1.1 kV) PVC (IS 1554-1) and XLPE (IS 7098-1) cable tables from the KEI Technical Handbook.

Run: python3 scripts/extract_kei_lt.py   (needs poppler's pdftotext)
Output: extracted/kei_lt_cables.csv, one row per (section, size, arrangement) with R_dc20, R_ac (at the
section's stated conductor temperature), reactance, capacitance and ground/duct/air ratings.
Rows carry pdf_page so they trace to raw/datasheets/cables/KEI_Technical_Handbook.pdf.
The handbook's HT (>1.1 kV) sections are skipped.
"""

import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "raw/datasheets/cables/KEI_Technical_Handbook.pdf"
OUT = ROOT / "extracted"
NUM = r"\d+(?:\.\d+)?"
SECTION_RE = re.compile(r"^\s*(\d(?:\.5)?) CORE,? (ALUMINIUM|COPPER)( CONDUCTOR,)? (PVC|XLPE)")
ROW_RE = re.compile(r"^\s*(\d(?:\.5)?)C\s*x\s*(\d+(?:\.\d+)?(?:/\d+)?)\s+(\d+(?:/\d+)?)\s")

pages = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True, check=True).stdout.split("\f")
rows, section = [], None
for pno, page in enumerate(pages, 1):
    for ln in page.splitlines():
        m = SECTION_RE.match(ln)
        if m:
            section = {"cores": m.group(1), "material": "Al" if m.group(2) == "ALUMINIUM" else "Cu",
                       "insulation": m.group(4), "title": re.sub(r"\s+", " ", ln.strip()),
                       "standard": "IS 1554-1" if m.group(4) == "PVC" else "IS 7098-1",
                       "armour_scope": "armoured only" if "ARMOURED POWER" in ln and "&" not in ln and "UNARMOURED" not in ln
                       else "unarmoured only" if "UNARMOURED POWER" in ln and "&" not in ln else "armoured & unarmoured",
                       "rac_temp_c": ""}
            continue
        if section is None:
            continue
        t = re.search(r"\b(70|90)\s*°C", ln)
        if t and not section["rac_temp_c"] and not ROW_RE.match(ln):
            section["rac_temp_c"] = t.group(1)
        m = ROW_RE.match(ln)
        if not m or m.group(1) != section["cores"]:
            continue
        nums = [x for x in ln.split()[3:] if re.fullmatch(NUM, x)]
        base = {"pdf_page": pno, **{k: section[k] for k in ("title", "standard", "material", "insulation", "cores", "armour_scope", "rac_temp_c")},
                "size_mm2": m.group(2), "min_wires": m.group(3)}
        if section["cores"] == "1" and len(nums) >= 12:
            rdc, rac, xa, ca, xu, cu, g2, g3, d2, d3, a2, a3 = nums[-12:]
            for arr, g, d, a in (("2x1c", g2, d2, a2), ("3x1c", g3, d3, a3)):
                rows.append({**base, "arrangement": arr, "r_dc20_ohm_km": rdc, "r_ac_ohm_km": rac,
                             "x_armoured_ohm_km": xa, "c_armoured_uf_km": ca, "x_unarmoured_ohm_km": xu,
                             "c_unarmoured_uf_km": cu, "ground_a": g, "duct_a": d, "air_a": a})
        elif section["cores"] != "1" and len(nums) >= 7:
            rdc, rac, x, c, g, d, a = nums[-7:]
            rows.append({**base, "arrangement": "multicore", "r_dc20_ohm_km": rdc, "r_ac_ohm_km": rac,
                         "x_armoured_ohm_km": x, "c_armoured_uf_km": c, "ground_a": g, "duct_a": d, "air_a": a})

fields = ["pdf_page", "title", "standard", "material", "insulation", "cores", "armour_scope", "arrangement", "size_mm2", "min_wires",
          "r_dc20_ohm_km", "r_ac_ohm_km", "rac_temp_c", "x_armoured_ohm_km", "c_armoured_uf_km", "x_unarmoured_ohm_km",
          "c_unarmoured_uf_km", "ground_a", "duct_a", "air_a"]
with open(OUT / "kei_lt_cables.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
print(f"kei_lt_cables.csv: {len(rows)} rows")

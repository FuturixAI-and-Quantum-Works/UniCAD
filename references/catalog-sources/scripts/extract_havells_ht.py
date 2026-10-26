"""Extract the 6.35/11 kV and 19/33 kV XLPE armoured cable electrical tables from the Havells industrial cables catalogue.

Run: python3 scripts/extract_havells_ht.py   (needs poppler's pdftotext)
Output: extracted/havells_ht_cables.csv, one row per (voltage grade, cores, size) with R_dc20 and R_ac90 for
Al and Cu, reactance, capacitance, ground/duct/air current ratings (Al and Cu) and 1 s short-circuit ratings.
Rows carry pdf_page so they trace to raw/datasheets/cables/Havells_Industrial_Cables_Range_Catalogue.pdf.
"""

import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "raw/datasheets/cables/Havells_Industrial_Cables_Range_Catalogue.pdf"
OUT = ROOT / "extracted"
NUM = r"\d+(?:\.\d+)?"
GRADES = {
    "6.6 kV/6.6 kV & 6.35 kV/11 kV": "6.35/11 kV",
    "19 kV/33 kV": "19/33 kV",
}
FIELDS = ["r_dc20_al", "r_dc20_cu", "r_ac90_al", "r_ac90_cu", "x_ohm_km", "c_uf_km",
          "al_ground_a", "al_duct_a", "al_air_a", "cu_ground_a", "cu_duct_a", "cu_air_a", "sc_1s_al_ka", "sc_1s_cu_ka"]
HEADER_RE = re.compile(r"TECHNICAL DETAILS FOR HAVELLS (.+?)\s*$")
CORES_RE = re.compile(r"^\s*(SINGLE|THREE) CORE, ALUMINIUM/COPPER CONDUCTOR, XLPE INSULATED, ARMOURED CABLES")
TABLE_RE = re.compile(r"Table\s*-\s*(\d+)")

pages = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True, check=True).stdout.split("\f")
rows = []
for pno, page in enumerate(pages, 1):
    lines = page.splitlines()
    grade = cores = None
    for i, ln in enumerate(lines):
        if m := HEADER_RE.search(ln):
            grade = GRADES.get(m.group(1).strip())
            if grade and i + 1 < len(lines) and (c := CORES_RE.match(lines[i + 1])):
                cores = 1 if c.group(1) == "SINGLE" else 3
        if "Electrical Parameters" in ln and grade and cores:
            table = TABLE_RE.search(page)
            for row in lines[i:]:
                toks = row.split()
                if len(toks) == 15 and all(re.fullmatch(NUM, t) for t in toks):
                    rows.append({"pdf_page": pno, "table": table.group(1) if table else "", "voltage_grade": grade, "cores": cores,
                                 "size_mm2": toks[0], **dict(zip(FIELDS, toks[1:])), "standard": "IS 7098 Part-2"})
            break

with open(OUT / "havells_ht_cables.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["pdf_page", "table", "standard", "voltage_grade", "cores", "size_mm2", *FIELDS])
    w.writeheader()
    w.writerows(rows)
print(f"{len(rows)} rows", sorted({(r['voltage_grade'], r['cores']) for r in rows}))

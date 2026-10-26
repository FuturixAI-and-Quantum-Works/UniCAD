"""Extract the Polycab H1Z2Z2-K (EN 50618) solar DC cable data sheet to CSV.

Run: python3 scripts/extract_polycab_solar.py   (needs poppler's pdftotext)
Output: extracted/polycab_solar_dc_en50618.csv, extracted/polycab_solar_dc_en50618_derating.csv
"""

import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "raw/datasheets/cables/Polycab_Solar_H1Z2Z2-K_EN50618_DS.pdf"
OUT = ROOT / "extracted"
NUM = r"\d+(?:\.\d+)?"

text = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True, check=True).stdout
fields = ["size_mm2", "insulation_mm", "sheath_mm", "overall_dia_mm", "weight_kg_km", "r_dc20_ohm_km",
          "single_free_air_a", "single_on_surface_a", "two_touching_on_surface_a"]
rows = []
for ln in text.splitlines():
    toks = ln.split()
    if len(toks) == 9 and all(re.fullmatch(NUM, t) for t in toks):
        rows.append(dict(zip(fields, toks)) | {"conductor": "tinned Cu class 5", "standard": "EN 50618 (H1Z2Z2-K)",
                                                "max_conductor_c": 120, "ambient_c": 60, "pdf_page": 2})
with open(OUT / "polycab_solar_dc_en50618.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["pdf_page", "standard", "conductor", *fields, "max_conductor_c", "ambient_c"])
    w.writeheader()
    w.writerows(rows)

# De-rating block: header "up to 60 / 70 80 90" then factors "1.00 0.92 0.84 0.75".
block = text.split("De-rating factor", 1)[1]
factors = re.search(r"(\d\.\d\d)\s+(\d\.\d\d)\s+(\d\.\d\d)\s+(\d\.\d\d)", block).groups()
with open(OUT / "polycab_solar_dc_en50618_derating.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["pdf_page", "ambient_c_max", "factor"])
    for amb, fac in zip((60, 70, 80, 90), factors):
        w.writerow([2, amb, fac])
print(f"{len(rows)} cable rows, derating {factors}")

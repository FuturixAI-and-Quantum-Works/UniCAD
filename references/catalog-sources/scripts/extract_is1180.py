"""Extract IS 1180 (Part 1):2014 Amendment 4 (2021) distribution-transformer tables to CSV.

Run: python3 scripts/extract_is1180.py   (needs poppler's pdftotext)
Source: raw/standards/IS1180-1_2014_Amendment4_2021_via_MJP.pdf
Output: extracted/transformer_is1180_ratings_losses.csv
Columns: phase, kVA, impedance %, max total loss (W) at 50 % and 100 % load for energy-efficiency levels 1..5.
"""

import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "raw/standards/IS1180-1_2014_Amendment4_2021_via_MJP.pdf"
OUT = ROOT / "extracted/transformer_is1180_ratings_losses.csv"

pages = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True, check=True).stdout.split("\f")
rows = []
for pno, page in enumerate(pages, 1):
    for ln in page.splitlines():
        m = re.match(r"^\s*([ivx]+)\)\s+(.*)$", ln)
        if not m:
            continue
        # Columns are separated by 2+ spaces; a single space is a thousands separator ("1 140").
        cells = [c.replace(" ", "") for c in re.split(r"\s{2,}", m.group(2).strip())]
        if len(cells) != 12 or not all(re.fullmatch(r"\d+(?:\.\d+)?", c) for c in cells):
            continue
        kva, z, *losses = cells
        # Single-phase table (5..100 kVA) follows the three-phase tables in the amendment.
        phase = 1 if any(r["phase"] == 3 and r["kva"] == "2500" for r in rows) else 3
        row = {"pdf_page": pno, "phase": phase, "kva": kva, "impedance_pct": z}
        for lvl in range(5):
            row[f"eel{lvl + 1}_loss50_w"] = losses[2 * lvl]
            row[f"eel{lvl + 1}_loss100_w"] = losses[2 * lvl + 1]
        rows.append(row)

with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"{OUT.name}: {len(rows)} rows")

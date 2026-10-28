"""Extract ABB switch-disconnector ordering data: AC (OT E-series) and DC (OTDC S1.0 / S2.0) to CSV.

Run: python3 scripts/extract_abb_switches.py   (needs poppler's pdftotext)
Output: extracted/abb_ot_switch_disconnectors.csv, extracted/abb_otdc_switch_disconnectors.csv
OT rows are the 3- and 4-pole base-mounting E-series types (OT200E..OT2500E, _03P / _04P) with the AC-22A current
from the ordering table. The short-time withstand current (1 s) is the value in the technical-data tables for sizes
where that table is unambiguous (200..1000 A); larger sizes are left empty.
OTDC rows are the IEC front-operated types: S1.0 (OTDC100E..250E, 1000 V DC-21B), S2.0 (OTDC100GV..250GV, 1500 V) and M (OTDC315FV..1000FV, 1500 V, 315-1000 A).
"""

import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw/datasheets/protection"
OT_PDF = "ABB_OT_OTM_switch-disconnectors_1SCC301020C0201.pdf"
OTDC_PDF = "ABB_OTDC_DC_switch-disconnectors_1SCC301022C0201_2024-06.pdf"
OUT = ROOT / "extracted"

ICW_1S_KA = {200: 8, 250: 8, 315: 15, 400: 15, 630: 20, 800: 20, 1000: 50}
ICW_PAGE = {200: "10-11", 250: "10-11", 315: "10-11", 400: "10-11", 630: "10-11", 800: "10-11", 1000: "12"}
OT_ROW = re.compile(r"^\s*(3|4)\s+(\d+)\s+(\d+)/(\d+|-)\s+(OT(\d+)E0[34]P)\s+(1SCA\d+R\d+)\s")
OTDC_S1 = re.compile(r"^\s*(1000)\s+(\d+)\s+(\d)\s+\S+,?\s*\S+\s+(\d)\s+(OTDC(\d+)E(11|22))\s+(1SCA\d+R\d+)(?:\s|$)")
OTDC_S2 = re.compile(r"^\s*(1500)\s+(\d+)\s+(\d+)\s+(\d)\s+\S+,?\s*\S+\s+(\d)\s+(OTDC(\d+)GV(11|22))\s+(1SCA\d+R\d+)(?:\s|$)")


OTDC_M = re.compile(r"^\s*(1500)\s+(\d+)\s+(\d+|-)\s+(\d)\s+\S+,?\s*\S+\s+(\d)\s+(OTDC(\d+)FV(11|22))\s+(1SCA\d+R\d+)(?:\s|$)")


def pages(name: str) -> list[str]:
    return subprocess.run(["pdftotext", "-layout", str(RAW / name), "-"], capture_output=True, text=True, check=True).stdout.split("\f")


ot_rows = []
for pno, page in enumerate(pages(OT_PDF), 1):
    for ln in page.splitlines():
        if m := OT_ROW.match(ln):
            poles, ith, ac22, ac23, typ, size, order = m.groups()
            if not 200 <= int(size) <= 2500 or ith != size:
                continue
            ot_rows.append({"pdf_page": pno, "type": typ, "order_code": order, "poles": poles, "size_a": size, "ith_open_a": ith,
                            "ac22a_a": ac22, "ac23a_a": "" if ac23 == "-" else ac23,
                            "icw_1s_ka": ICW_1S_KA.get(int(size), ""), "icw_pdf_page": ICW_PAGE.get(int(size), "")})
ot_rows.sort(key=lambda r: (int(r["size_a"]), int(r["poles"])))
with open(OUT / "abb_ot_switch_disconnectors.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(ot_rows[0]))
    w.writeheader()
    w.writerows(ot_rows)

dc_rows = []
for pno, page in enumerate(pages(OTDC_PDF), 1):
    if pno not in (36, 37, 46):
        continue
    for ln in page.splitlines():
        if "UL Ordering" in ln:
            break
        if m := OTDC_S1.match(ln):
            volt, amps, circuits, poles, typ, size, _, order = m.groups()
            dc_rows.append({"pdf_page": pno, "series": "S1.0", "type": typ, "order_code": order, "voltage_vdc": volt, "poles": poles,
                            "circuits": circuits, "dc21b_a": amps, "dc_pv2_a": "", "icw_ka": 5, "icw_s": 1, "tech_pdf_page": 31})
        elif m := OTDC_M.match(ln):
            volt, amps, pv2, circuits, poles, typ, size, _, order = m.groups()
            dc_rows.append({"pdf_page": pno, "series": "M", "type": typ, "order_code": order, "voltage_vdc": volt, "poles": poles,
                            "circuits": circuits, "dc21b_a": amps, "dc_pv2_a": "" if pv2 == "-" else pv2, "icw_ka": 10, "icw_s": 1, "tech_pdf_page": 42})
        elif m := OTDC_S2.match(ln):
            volt, amps, pv2, circuits, poles, typ, size, _, order = m.groups()
            dc_rows.append({"pdf_page": pno, "series": "S2.0", "type": typ, "order_code": order, "voltage_vdc": volt, "poles": poles,
                            "circuits": circuits, "dc21b_a": amps, "dc_pv2_a": pv2, "icw_ka": 10, "icw_s": 0.1, "tech_pdf_page": 32})
dc_rows = [r for r in dc_rows if r["type"].endswith(("11", "22"))]
seen, uniq = set(), []
for r in dc_rows:
    if r["type"] not in seen:
        seen.add(r["type"])
        uniq.append(r)
with open(OUT / "abb_otdc_switch_disconnectors.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(uniq[0]))
    w.writeheader()
    w.writerows(uniq)
print(f"{len(ot_rows)} OT rows, {len(uniq)} OTDC rows")

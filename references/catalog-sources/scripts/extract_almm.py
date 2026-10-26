"""Extract MNRE ALMM List-I (modules) and List-II (cells) PDFs to CSV.

Run: uv run --with pdfplumber python scripts/extract_almm.py
Reads raw/ALMM_List-I_*.pdf and raw/ALMM_List-II_*.pdf, writes extracted/almm_list1_modules.csv
and extracted/almm_list2_cells.csv. Every row carries pdf_page so values trace back to the PDF.
"""

import csv
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "raw", ROOT / "extracted"

STATUS_RE = re.compile(r"\(([^)]*(?:Addition|Inclusion|Renewal|Change)[^)]*)\)", re.I)
REVISION_RE = re.compile(r"New additions on ([0-9./]+)")


def clean(s):
    return unspace(re.sub(r"\s+", " ", s).strip()) if s else ""


def unspace(s):
    # Some PDF pages render text with a space between every glyph: "2 1 . 2 7", "N S G - B F T 60",
    # "S o l a r C e l l s". Join every run of 3+ single-character tokens.
    out, run = [], []
    for tok in s.split(" "):
        if len(tok) == 1:
            run.append(tok)
            continue
        out.append("".join(run) if len(run) >= 3 else " ".join(run))
        run = []
        out.append(tok)
    out.append("".join(run) if len(run) >= 3 else " ".join(run))
    return " ".join(t for t in out if t)


def split_status(name):
    m = STATUS_RE.search(name)
    if not m:
        return name, ""
    return clean(STATUS_RE.sub("", name)), m.group(1)


def list1(pdf_path, out_path):
    cols = ["sno", "manufacturer", "location", "bis_reg", "enlisted_capacity_mw", "type_sno",
            "module_type", "applied_model", "enlisted_model", "efficiency_pct", "cells",
            "system_voltage_v", "valid_from", "valid_to"]
    rows, mfr, grp, revision = [], {}, {}, None
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages, 1):
            m = REVISION_RE.search(page.extract_text() or "")
            if m:
                revision = m.group(1).replace(".", "/")
            for table in page.extract_tables():
                for r in table:
                    if len(r) != 14:
                        print(f"skip page {pi}: {len(r)} cols", file=sys.stderr)
                        continue
                    c = [clean(x) for x in r]
                    if c[0].startswith("S.") or c[12] == "From" or "Name of the" in c[1] or "Efficiency" in c[9]:
                        continue
                    if not c[8]:
                        continue
                    if c[1]:
                        mfr = dict(zip(cols[:5], c[:5]))
                        mfr["valid_from"], mfr["valid_to"] = c[12], c[13]
                    if c[6] or c[7]:
                        grp = {"type_sno": c[5], "module_type": c[6], "applied_model": c[7],
                               "cells": c[10], "system_voltage_v": c[11]}
                    if c[12] and not c[1]:
                        mfr["valid_from"], mfr["valid_to"] = c[12], c[13]
                    row = {**mfr, **grp, "enlisted_model": c[8],
                           "efficiency_pct": c[9].replace(" ", ""), "revision_date": revision, "pdf_page": pi}
                    row["manufacturer"], row["listing_note"] = split_status(row["manufacturer"])
                    wp = re.search(r"\((\d{2,4})\s*W[pP]?\)", row.get("applied_model", ""))
                    row["applied_model_wp"] = wp.group(1) if wp else ""
                    try:
                        ok = 10 <= float(row["efficiency_pct"]) <= 30
                    except ValueError:
                        ok = False
                    # Flags rows where pdfplumber shifted a cell (efficiency outside 10-30 % or unparsable).
                    row["parse_suspect"] = ";".join(
                        [x for x, bad in (("efficiency", not ok), ("manufacturer", not row["manufacturer"])) if bad])
                    rows.append(row)
    fields = ["revision_date", "pdf_page", "listing_note", *cols[:8], "applied_model_wp", *cols[8:], "parse_suspect"]
    write(out_path, fields, rows)


def list2(pdf_path, out_path):
    cols = ["sno", "manufacturer", "location", "enlisted_capacity_mw", "cell_type", "enlisted_models",
            "avg_efficiency_or_wattage", "range_efficiency_or_wattage", "valid_from", "valid_to"]
    rows, revision = [], None
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages, 1):
            m = REVISION_RE.search(page.extract_text() or "")
            if m:
                revision = m.group(1).replace(".", "/")
            for table in page.extract_tables():
                ncol = len(table[0])
                for r in table:
                    c = [clean(x) for x in r]
                    if c[0].startswith("S.") or c[0].startswith("ALMM") or "From" in c:
                        continue
                    if ncol == 8:
                        # Revision tables that revise efficiency/wattage ranges of existing entries.
                        rec = {"sno": c[0], "manufacturer": c[1], "location": c[2], "enlisted_capacity_mw": c[3],
                               "cell_type": c[4], "range_efficiency_or_wattage": c[7],
                               "note": f"range revised; previously {c[5]} / {c[6]}"}
                        if not c[0] and rows and rows[-1]["pdf_page"] == pi:
                            prev = rows[-1]
                            for k in ("sno", "manufacturer", "location"):
                                rec[k] = prev[k]
                    elif ncol == 10:
                        if not any(c[:6]) and rows and rows[-1]["pdf_page"] == pi:
                            # Continuation row carrying the wattage half of efficiency/wattage.
                            prev = rows[-1]
                            prev["avg_efficiency_or_wattage"] += " / " + c[6]
                            prev["range_efficiency_or_wattage"] += " / " + c[7]
                            continue
                        rec = dict(zip(cols, c))
                        if not c[0] and rows and rows[-1]["pdf_page"] == pi:
                            prev = rows[-1]
                            for k in ("sno", "manufacturer", "location", "valid_from", "valid_to"):
                                rec[k] = rec.get(k) or prev[k]
                    else:
                        print(f"skip page {pi}: {ncol} cols", file=sys.stderr)
                        continue
                    if not rec.get("manufacturer"):
                        continue
                    rec["manufacturer"], rec["listing_note"] = split_status(rec["manufacturer"])
                    rec.update(revision_date=revision, pdf_page=pi)
                    rows.append(rec)
    fields = ["revision_date", "pdf_page", "listing_note", *cols, "note"]
    write(out_path, fields, rows)


def write(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{path.name}: {len(rows)} rows", file=sys.stderr)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    list1(max(RAW.glob("ALMM_List-I_*.pdf")), OUT / "almm_list1_modules.csv")
    list2(max(RAW.glob("ALMM_List-II_*.pdf")), OUT / "almm_list2_cells.csv")

"""Reduce the NREL SAM CEC module and inverter libraries to the fields the catalog needs.

Run: uv run --with pandas python scripts/extract_cec.py
Inputs: raw/CEC_Modules.csv, raw/CEC_Inverters.csv (SAM library format: row 2 = units, row 3 = SAM variable names)
Outputs:
  extracted/cec_modules_core.csv          all modules, catalog fields only
  extracted/cec_modules_india_mfrs.csv    subset whose Manufacturer matches INDIA_MFRS below
  extracted/cec_inverters_core.csv        all inverters, catalog fields only
Derived columns (alpha_isc_pct_per_k, beta_voc_pct_per_k) are computed from the raw A/K and V/K values.
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "raw", ROOT / "extracted"

# Manufacturer-name substrings for Indian module makers present in the CEC list (checked by hand on 2026-10-26).
INDIA_MFRS = ["WAAREE", "Adani", "Vikram Solar", "PREMIER ENERGIES", "Tata Power", "Goldi", "Rayzon", "Saatvik",
              "EMMVEE", "Gautam Solar", "SOLEX", "Reliance Industries"]

m = pd.read_csv(RAW / "CEC_Modules.csv", skiprows=[1, 2], low_memory=False)
m = m.loc[:, ~m.columns.str.startswith("Unnamed")]
mods = pd.DataFrame({
    "name": m["Name"], "manufacturer": m["Manufacturer"], "technology": m["Technology"], "bifacial": m["Bifacial"],
    "pmax_stc_w": m["STC"], "ptc_w": m["PTC"], "voc_v": m["V_oc_ref"], "isc_a": m["I_sc_ref"], "vmp_v": m["V_mp_ref"],
    "imp_a": m["I_mp_ref"], "alpha_isc_a_per_k": m["alpha_sc"], "beta_voc_v_per_k": m["beta_oc"],
    "gamma_pmp_pct_per_k": m["gamma_pmp"], "noct_c": m["T_NOCT"], "cells_in_series": m["N_s"],
    "length_m": m["Length"], "width_m": m["Width"], "area_m2": m["A_c"], "bipv": m["BIPV"],
    "sam_version": m["Version"], "sam_date": m["Date"],
})
mods["alpha_isc_pct_per_k"] = (mods.alpha_isc_a_per_k / mods.isc_a * 100).round(4)
mods["beta_voc_pct_per_k"] = (mods.beta_voc_v_per_k / mods.voc_v * 100).round(4)
mods.to_csv(OUT / "cec_modules_core.csv", index=False)
india = mods[mods.manufacturer.str.contains("|".join(INDIA_MFRS), case=False, na=False)]
india.to_csv(OUT / "cec_modules_india_mfrs.csv", index=False)

i = pd.read_csv(RAW / "CEC_Inverters.csv", skiprows=[1, 2])
mfr_model = i["Name"].str.split(": ", n=1, expand=True)
invs = pd.DataFrame({
    "name": i["Name"], "manufacturer": mfr_model[0], "model": mfr_model[1], "vac_v": i["Vac"],
    "paco_w": i["Paco"], "pdco_w": i["Pdco"], "vdco_v": i["Vdco"], "pso_w": i["Pso"], "pnt_w": i["Pnt"],
    "vdcmax_v": i["Vdcmax"], "idcmax_a": i["Idcmax"], "mppt_low_v": i["Mppt_low"], "mppt_high_v": i["Mppt_high"],
    "c0": i["C0"], "c1": i["C1"], "c2": i["C2"], "c3": i["C3"], "cec_date": i["CEC_Date"], "cec_hybrid": i["CEC_hybrid"],
})
invs.to_csv(OUT / "cec_inverters_core.csv", index=False)
print(f"modules {len(mods)}, india subset {len(india)}, inverters {len(invs)}")

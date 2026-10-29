"""Drafting follow-ups after fix wave 7 on the RVUNL LT plant as the brief gives it (720 Wp Avaada modules, 8 Solis 150 kW inverters with 7 MPPTs,
inverter 8 with 18- and 19-module strings, an ACCB into the client's 415 V panel with CT, PT, relay, meter and an RS-485 data logger)."""

import pytest
from test_design_rvunl import BRIEF, CATALOG, RELAY, heerapura

from unicad.design import autobuild
from unicad.drafting.string_diagram import parameters_for, plan_types
from unicad.rules.pv import cell_temp_min, string_voltage

MODULE = "avaada-electro-avn66g12g-720"
INVERTER = "solis-s6-gc3p150k07-nv-nd"


@pytest.fixture(scope="module")
def lt():
    return autobuild(heerapura(), CATALOG, {"module": MODULE, "inverter": INVERTER, "inverter_strings": BRIEF, "relay": RELAY, "data_logger": True}).project


def test_string_diagram_groups_strings_on_their_recorded_mppt(lt):
    types, _ = plan_types(lt, CATALOG)
    inv8 = next(t for t in types if t.representative.tag == "INV-08")
    by_mppt: dict[int, list[str]] = {}
    for ln in inv8.lines:
        by_mppt.setdefault(ln.mppt, []).append(ln.tag)
    assert by_mppt == {k: [f"STR18-08-{k:02d}", f"STR19-08-{k:02d}"] for k in range(1, 7)} | {7: ["STR18-08-07"]}
    assert [ln.mppt for ln in inv8.lines] == sorted(ln.mppt for ln in inv8.lines), "one MPPT's strings sit together"
    assert [ln.ip for ln in inv8.lines] == [f"IP{i:02d}" for i in range(1, 14)]
    assert inv8.mppt.used == 7 and inv8.mppt.mppt_count == 7 and inv8.mppt.inputs_per_mppt == 2
    assert any("as the network records them" in n for n in inv8.notes) and not any("spread evenly" in n for n in inv8.notes)
    inv5 = next(t for t in types if t.representative.tag == "INV-05")
    assert [ln.mppt for ln in inv5.lines] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7]


def test_string_diagram_prints_each_family_voc(lt):
    types, _ = plan_types(lt, CATALOG)
    inv8 = next(t for t in types if t.representative.tag == "INV-08")
    module = CATALOG.get(MODULE)
    cold = cell_temp_min(lt.design_basis)
    voc = {n: round(string_voltage(module, n, "voc", cold).value, 1) for n in (18, 19)}
    assert {ln.modules: ln.voc_cold_v for ln in inv8.lines} == voc and voc[19] > voc[18]
    params = dict(parameters_for(inv8))
    assert params["MODULES IN SERIES"] == "18 / 19"
    assert params[f"VOC AT {inv8.tmin_c:g} DEG C"] == f"{voc[18]:.1f} V (18) / {voc[19]:.1f} V (19) (LIMIT {inv8.voc_limit_v:g} V)"

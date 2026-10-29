"""Drafting follow-ups after fix wave 7 on the RVUNL LT plant as the brief gives it (720 Wp Avaada modules, 8 Solis 150 kW inverters with 7 MPPTs,
inverter 8 with 18- and 19-module strings, an ACCB into the client's 415 V panel with CT, PT, relay, meter and an RS-485 data logger)."""

from pathlib import Path

import pytest
from test_design_rvunl import BRIEF, CATALOG, RELAY, heerapura

from unicad.design import autobuild
from unicad.domain.network import SpdRating
from unicad.domain.project import Project
from unicad.drafting.sld.annotate import bus_texts, node_texts
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


# ---------------- typed node ratings on the SLD (N2) ----------------

DATA = Path(__file__).resolve().parent / "data"


def bench2() -> Project:
    """The RVUNL plant as bench run 2 built it over MCP (utility_lt, Avaada 720 Wp, Solis 150 kW, data logger, relay) with the brief's typed ratings."""
    return Project.model_validate_json((DATA / "rvunl_heerapura_bench2.json").read_text(encoding="utf-8"))


def texts_of(project: Project, nid: str) -> dict[str, str]:
    return node_texts(project.network, project.network.node(nid), CATALOG)


def test_typed_busbar_prints_as_a_rating_not_a_field_dump():
    p = bench2()
    lines = bus_texts(p.network, p.network.node("acdb"))
    assert lines[1] == "415 V, 2500 A, 31.5 kA 3 s, AL BUSBAR, 3P+N"
    assert not any("=" in x or "MATERIAL" in x for x in lines), lines


def test_typed_ct_pt_relay_spd_and_ir_print_beside_their_symbols():
    p = bench2()
    ct = texts_of(p, "solar_ct")["RATING"]
    assert ct == "415 V, 2500/1 A, C1 CL 0.2S 15 VA METERING, C2 CL PS PROTECTION, C3 CL 0.2S 15 VA METERING", ct
    pt = texts_of(p, "solar_pt")["RATING"]
    assert pt == "415 V, 0.415 kV/\u221a3 / 110 V/\u221a3, C1 CL 0.2 15 VA METERING, C2 CL 3P 15 VA PROTECTION", pt
    relay = texts_of(p, "relay")
    assert [relay[f"ANSI{i}"] for i in range(1, 7)] == ["50", "51", "67", "67N", "27", "59"]
    assert relay["RATING"] == "50N, 51N, IEC 61850", relay
    spd = texts_of(p, "acdb_spd")["RATING"]
    assert spd == "4P, TYPE 2, 275 V, In/Imax 20/40 kA", spd
    acb = texts_of(p, "acdb_brk")["RATING"]
    assert acb.startswith("2500 A, Ir = 2400 A, 4P"), acb
    assert "Ir" not in texts_of(p, "inv_brka")["RATING"]


def test_typed_spd_imax_wins_over_the_catalog():
    p = bench2()
    net = p.network
    spd = net.node("acdb_spd").model_copy(update={"spd": SpdRating(spd_type="2", imax_ka=65)})
    text = node_texts(net, spd, CATALOG)["RATING"]
    assert text == "4P, TYPE 2, 275 V, In/Imax 20/65 kA", text


def test_attribute_fallbacks_still_print():
    p = bench2()
    net = p.network
    ct = net.node("solar_ct").model_copy(update={"ct": None, "attributes": {"ratio": "2500/1 A", "cores": ["0.2S 15 VA", "PS"]}})
    assert node_texts(net, ct, CATALOG)["RATING"] == "415 V, RATIO 2500/1 A, C1 0.2S 15 VA, C2 PS"
    bus = net.node("acdb").model_copy(update={"busbar": None, "attributes": {"rating_a": 2500, "fault_ka": 31.5, "fault_duration_s": 3, "material": "al"}})
    assert bus_texts(net, bus)[1] == "415 V, 2500 A, 31.5 kA 3 s, AL BUSBAR"

"""Catalog parts the RVUNL Heerapura brief named: 720 Wp module, 150 kW inverter, multifunction meter, data logger (RV-S1, S2, S4, S5)."""

import csv
from pathlib import Path

import pytest

from unicad.catalog import Catalog, load_catalog
from unicad.design import size_strings
from unicad.domain.catalog import DataLoggerSpec, InverterSpec, MeterSpec, ModuleSpec
from unicad.domain.network import SPEC_KINDS, NodeKind
from unicad.drafting.sld import annotate
from unicad.drafting.symbols import REGISTRY
from unicad.mcp.tools.catalog import search_catalog
from unicad.schedules.bom import _describe
from unicad.store import parse_project

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "references" / "catalog-sources" / "extracted"
MODULE = "avaada-electro-avn66g12g-720"
INVERTER = "solis-s6-gc3p150k07-nv-nd"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(ROOT / "catalog")


def test_720_wp_module_matches_the_datasheet_and_is_almm_listed(catalog):
    m = catalog.get(MODULE)
    assert isinstance(m, ModuleSpec)
    assert (m.pmax, m.vmp, m.imp, m.voc, m.isc) == (720, 41.3, 17.43, 49.0, 18.48)
    assert (m.temp_coeff_pmax, m.temp_coeff_voc, m.temp_coeff_isc, m.noct_c) == (-0.30, -0.25, 0.04, 45)
    assert (m.length, m.width, m.cells, m.tech.value, m.bifacial, m.max_system_voltage) == (2384, 1303, 132, "topcon", True, 1500)
    assert m.max_series_fuse is None
    assert m.almm_listed is True and "AVN66G12G720" in m.almm_ref
    assert "Avaada_Enlume_AVN66G12G_G2G_TOPCon_700-720.pdf p.2" in m.source.ref and m.source.retrieved == "2026-10-29"


def test_every_avaada_bin_is_catalogued_from_the_extracted_row(catalog):
    with open(EXTRACTED / "module_datasheets.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["manufacturer"] == "Avaada Electro"]
    assert [r["pmax_w"] for r in rows] == ["700", "705", "710", "715", "720"]
    for r in rows:
        m = catalog.get(f"avaada-electro-avn66g12g-{r['pmax_w']}")
        assert (m.pmax, m.vmp, m.imp, m.voc, m.isc) == tuple(float(r[k]) for k in ("pmax_w", "vmp_v", "imp_a", "voc_v", "isc_a"))


def test_rvunl_logged_searches_now_find_parts(catalog):
    modules = search_catalog(catalog, kind="module", min_power=650)
    assert modules["total"] == 5 and {r["power"] for r in modules["rows"]} == {700, 705, 710, 715, 720}
    inverters = search_catalog(catalog, kind="inverter", min_power=120, max_power=200)
    assert [r["id"] for r in inverters["rows"]] == [INVERTER]
    assert search_catalog(catalog, kind="module", min_power=800)["note"].count("largest module is 720 Wp") == 1


def test_150_kw_inverter_has_seven_mppt_and_the_datasheet_ratings(catalog):
    i = catalog.get(INVERTER)
    assert isinstance(i, InverterSpec) and i.symbol == "inv.string"
    assert (i.ac_rated_kva, i.ac_max_kva, i.ac_voltage, i.phases) == (150, 165, 400, 3)
    assert (i.mppt_count, i.inputs_per_mppt, i.max_input_current_per_mppt, i.max_short_circuit_current_per_mppt) == (7, 3, 54, 75)
    assert (i.dc_max_voltage, i.mppt_min, i.mppt_max, i.start_voltage, i.ac_terminal_max_mm2) == (1100, 160, 1000, 180, 400)
    assert "(derived)" in i.source.ref and "inverter_datasheets.csv" in i.source.ref


def test_720_wp_module_strings_on_the_150_kw_inverter_fit_two_per_mppt(catalog):
    basis = parse_project((ROOT / "fixtures" / "projects" / "rooftop_500kwp.json").read_text(encoding="utf-8"), catalog).design_basis
    d = size_strings(catalog.get(MODULE), catalog.get(INVERTER), basis, modules_per_string=18)
    assert d.strings_per_mppt_max == 3 and d.strings_per_inverter_max == 21
    assert 2 * 18.48 <= 75 and 2 * 17.43 <= 54
    assert d.voltages.voc_cold_v < 1100


def test_multifunction_meter_is_rs485_ct_operated_and_draws_as_an_mfm(catalog):
    meters = [m for m in catalog.filter(kind="meter") if isinstance(m, MeterSpec) and m.function == "multifunction"]
    assert sorted(m.accuracy_class for m in meters) == ["0.2S", "0.5S", "1.0"]
    for m in meters:
        assert (m.manufacturer, m.connection, m.communication, m.symbol) == ("Secure Meters", "ct", "RS-485 Modbus RTU", "inst.mfm")
        assert "Secure_Elite440_DS.pdf p.4" in m.source.ref and m.symbol in REGISTRY


def test_data_logger_is_its_own_kind_with_the_solis_s3_ratings(catalog):
    log = catalog.get("solis-s3-logger")
    assert isinstance(log, DataLoggerSpec) and log.kind == "logger" and log.symbol == "inst.datalogger" and log.symbol in REGISTRY
    assert (log.rs485_ports, log.max_devices, log.max_devices_per_rs485_port, log.ethernet_ports) == (4, 60, 15, 1)
    assert log.ac_supply_v == (100, 240) and log.dc_supply_v == (9, 36) and (log.dimensions.width, log.dimensions.depth, log.dimensions.height) == (89, 121, 27)
    assert "Modbus-RTU" in log.rs485_protocols and "IEC60870-5-104" in log.ethernet_protocols
    assert "Solis_S3-Logger_DS.pdf p.2" in log.source.ref


def test_a_meter_node_may_name_a_logger_spec_and_search_lists_the_kind(catalog):
    assert "logger" in SPEC_KINDS[NodeKind.METER]
    r = search_catalog(catalog, kind="logger")
    assert r["total"] == 1 and r["rows"][0]["key"]["rs485_ports"] == 4


def test_sld_texts_for_the_new_meter_and_logger(catalog):
    from unicad.domain.network import ElectricalNetwork, Node

    net = ElectricalNetwork(nodes=[Node(id="mfm", tag="MFM-01", kind=NodeKind.METER, spec="secure-meters-elite-440-class-0.2s"), Node(id="dl", tag="DL-01", kind=NodeKind.METER, spec="solis-s3-logger")], edges=[])
    mfm = annotate.node_texts(net, net.node("mfm"), catalog)
    assert (mfm["DESC"], mfm["RATING"]) == ("MULTIFUNCTION METER", "CL 0.2S, CT OPERATED")
    dl = annotate.node_texts(net, net.node("dl"), catalog)
    assert (dl["DESC"], dl["RATING"]) == ("DATA LOGGER", "4 x RS-485, 1 x ETHERNET, 60 DEVICES")
    assert _describe(catalog.get("solis-s3-logger"), net.node("dl")) == "Solis S3-Logger, data logger, 4 x RS-485"
    assert _describe(catalog.get("secure-meters-elite-440-class-0.2s"), net.node("mfm")) == "Secure Meters Elite 440 class 0.2S, multifunction meter, class 0.2S"

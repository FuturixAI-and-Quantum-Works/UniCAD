"""The close-e2e-2 verifier scenarios (A rooftop, B utility, C hybrid, R reg-b4) replayed in-process from the logged MCP calls, and the
drawing defects found on them: every kind renders, and the sheets read correctly."""

import asyncio
import functools
import json
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import ezdxf
import pytest
from test_design import CATALOG
from test_symbols import misread_texts

from shapely.ops import unary_union

from unicad.domain.network import NodeKind
from unicad.domain.site import FeatureKind, shape_of
from unicad.drafting.earthing_plan.sheet import render_earthing_sheet, render_lps_sheet
from unicad.earthing import design_earthing
from unicad.lps import design_lps
from unicad.sets.common import sheet_spec
from unicad.sets.plans import EarthingBuilder, LpsBuilder, plant_areas
from unicad.domain.project import Project
from unicad.drafting.sld import SldOptions
from unicad.drafting.sld.model import MIN_PRINTED_TEXT_MM
from unicad.drafting.sld.annotate import system_parameters
from unicad.drafting.sld.layout import layout_sld
from unicad.drafting.string_diagram import build_string_diagram_sheets
from unicad.rules.ratio import plant_capacity
from unicad.mcp import Config, build_server
from unicad.sets import ALL_KINDS, render_set
from unicad.sets.numbering import Numberer
from unicad.sets.strings import StringsBuilder
from unicad.store import ProjectStore
from unicad.tenants.store import load_profile

ROOT = Path(__file__).resolve().parents[1]
DRAWN_ON = date(2026, 10, 1)

CALLS = {'sgt-chakan': [('company_onboard',
                 {'dxf_path': '{root}/profiles/samples/acme/acme-company.dxf', 'answers': '{root}/profiles/samples/acme/answers.yaml'}),
                ('project_create',
                 {'name': 'Shree Ganesh Textiles 800 kWp Rooftop',
                  'client': 'Shree Ganesh Textiles Pvt Ltd',
                  'segment': 'ci_rooftop',
                  'site': 'MIDC Chakan, Pune',
                  'grid_voltage_v': 415,
                  'project_id': 'sgt-chakan',
                  'state': 'Maharashtra',
                  'utility': 'MSEDCL',
                  'client_code': 'SGT',
                  'address': 'Plot B-12, MIDC Chakan Phase II, Pune 410501'}),
                ('company_assign', {'project_id': 'sgt-chakan', 'profile_id': 'acme'}),
                ('project_design_basis',
                 {'project_id': 'sgt-chakan',
                  'ambient_min_c': 8,
                  'ambient_max_c': 42,
                  'altitude_m': 640,
                  'vdrop_dc_max': 0.02,
                  'vdrop_ac_max': 0.02,
                  'dc_ac_ratio_target': 1.2,
                  'power_factor': 0.99,
                  'fault_levels': [{'voltage_v': 415, 'fault_ka': 25, 'clearing_s': 1, 'source': 'user input: MSEDCL LT fault level 25 kA 1 s'}],
                  'earthing': {'soil_resistivity_ohm_m': 100, 'fault_current_ka': 25, 'fault_clearing_s': 1},
                  'lps': {'level': 'II', 'method': 'ese', 'ese_delta_t_us': 60, 'mast_height_m': 5}}),
                ('site_import', {'project_id': 'sgt-chakan', 'file_path': '{root}/fixtures/sites/rooftop_midc_pune.kml'}),
                ('site_set_setbacks', {'project_id': 'sgt-chakan', 'roof_edge_m': 1.0, 'obstacle_m': 0.6}),
                ('layout_place',
                 {'project_id': 'sgt-chakan',
                  'module': 'adani-solar-elan-shine-topcon-g2g-gen-ii-560-wp',
                  'mounting': 'rooftop_flush',
                  'pitch_method': 'gap',
                  'row_gap_m': 0.6,
                  'table_gap_m': 0.6,
                  'table_rows': 2,
                  'target_dc_kwp': 806.4,
                  'modules_per_string': 20,
                  'strings_per_inverter': 12}),
                ('network_autobuild',
                 {'project_id': 'sgt-chakan',
                  'template': 'ci_rooftop',
                  'dc_kwp': 806.4,
                  'module': 'adani-solar-elan-shine-topcon-g2g-gen-ii-560-wp',
                  'inverter': 'huawei-sun2000-100ktl-m2',
                  'modules_per_string': 20,
                  'strings_per_inverter': 12,
                  'connection': 'lt_panel'}),
                ('network_update',
                 {'project_id': 'sgt-chakan',
                  'id': 'e_ct_poi',
                  'fields': {'cable': 'polycab-lt-xlpe-al-1c-300-arm',
                             'runs': 3,
                             'length_m': 20,
                             'laying': 'tray',
                             'remarks': 'DISCOM service connection, length per user input'}}),
                ('earthing_design', {'project_id': 'sgt-chakan'}),
                ('lps_design', {'project_id': 'sgt-chakan', 'method': 'ese', 'level': 'II', 'ese_delta_t_us': 60, 'mast_height_m': 5})],
 'thar-40mw': [('project_create',
                {'name': 'Thar Renewables 40 MWac Solar PV Plant',
                 'client': 'Thar Renewables Ltd',
                 'segment': 'utility',
                 'site': 'Phalodi, Jodhpur',
                 'grid_voltage_v': 33000,
                 'project_id': 'thar-40mw',
                 'state': 'Rajasthan',
                 'utility': 'RVPNL',
                 'client_code': 'TRL'}),
               ('project_design_basis',
                {'project_id': 'thar-40mw',
                 'ambient_min_c': 2,
                 'ambient_max_c': 48,
                 'altitude_m': 230,
                 'vdrop_dc_max': 0.015,
                 'vdrop_ac_max': 0.02,
                 'dc_ac_ratio_target': 1.25,
                 'power_factor': 0.95,
                 'fault_levels': [{'voltage_v': 33000, 'fault_ka': 25, 'clearing_s': 1, 'source': 'user input: RVPNL 33 kV fault level 25 kA 1 s'}],
                 'earthing': {'soil_resistivity_ohm_m': 150, 'fault_current_ka': 25, 'fault_clearing_s': 1},
                 'lps': {'level': 'III', 'method': 'ese', 'ese_delta_t_us': 60, 'mast_height_m': 6}}),
               ('site_import', {'project_id': 'thar-40mw', 'file_path': '{root}/fixtures/sites/ground_mount_rajasthan.kml'}),
               ('site_set_setbacks', {'project_id': 'thar-40mw', 'boundary_m': 10, 'exclusion_m': 10}),
               ('project_design_basis', {'project_id': 'thar-40mw', 'dc_ac_ratio_target': 1.1}),
               ('layout_place',
                {'project_id': 'thar-40mw',
                 'module': 'vikram-solar-hypersol-m10r-144-590-wp',
                 'mounting': 'fixed_tilt',
                 'tilt_deg': 25,
                 'table_rows': 2,
                 'modules_per_string': 27,
                 'strings_per_inverter': 24,
                 'n_blocks': 6,
                 'target_dc_kwp': 43584.5,
                 'road_every_rows': 12}),
               ('network_autobuild',
                {'project_id': 'thar-40mw',
                 'template': 'utility_string',
                 'module': 'vikram-solar-hypersol-m10r-144-590-wp',
                 'inverter': 'sungrow-sg350hx',
                 'modules_per_string': 27,
                 'strings_per_inverter': 24,
                 'inverters_per_block': 19}),
               ('network_update',
                {'project_id': 'thar-40mw',
                 'id': 'e_ct_poi',
                 'fields': {'cable': 'havells-ht-xlpe-al-3c-300-33kv-arm',
                            'runs': 2,
                            'length_m': 200,
                            'laying': 'buried_direct',
                            'remarks': '33 kV export to RVPNL line termination, length per user input'}}),
               ('earthing_design', {'project_id': 'thar-40mw', 'switchyard_m': [60, 45], 'surface_resistivity': 3000, 'surface_layer_m': 0.1}),
               ('lps_design', {'project_id': 'thar-40mw', 'method': 'ese', 'level': 'III', 'ese_delta_t_us': 60, 'mast_height_m': 6}),
               ('network_update', {'project_id': 'thar-40mw', 'id': 'e_ct_poi', 'fields': {'runs': 3}})],
 'kutch-hybrid': [('project_create',
                   {'name': 'Kutch Hybrid 20 MWac PV + 10 MW 20 MWh BESS',
                    'client': 'Kutch Hybrid Power Pvt Ltd',
                    'segment': 'hybrid',
                    'site': 'Kutch, Gujarat',
                    'grid_voltage_v': 33000,
                    'project_id': 'kutch-hybrid',
                    'state': 'Gujarat',
                    'utility': 'GETCO',
                    'client_code': 'KHP'}),
                  ('project_design_basis',
                   {'project_id': 'kutch-hybrid',
                    'ambient_min_c': 5,
                    'ambient_max_c': 46,
                    'altitude_m': 20,
                    'vdrop_dc_max': 0.015,
                    'vdrop_ac_max': 0.02,
                    'dc_ac_ratio_target': 1.1,
                    'power_factor': 0.95,
                    'fault_levels': [{'voltage_v': 33000,
                                      'fault_ka': 25,
                                      'clearing_s': 1,
                                      'source': 'user input: GETCO 33 kV fault level 25 kA 1 s'}],
                    'earthing': {'soil_resistivity_ohm_m': 80, 'fault_current_ka': 25, 'fault_clearing_s': 1, 'switchyard_m': [60, 45]},
                    'lps': {'level': 'III', 'method': 'ese', 'ese_delta_t_us': 60, 'mast_height_m': 6}}),
                  ('site_import',
                   {'project_id': 'kutch-hybrid', 'file_path': '{root}/fixtures/sites/site_plan_kutch.dxf', 'origin_lon': 69.8, 'origin_lat': 23.45}),
                  ('site_set_setbacks', {'project_id': 'kutch-hybrid', 'boundary_m': 10, 'exclusion_m': 10}),
                  ('layout_place',
                   {'project_id': 'kutch-hybrid',
                    'module': 'vikram-solar-hypersol-m10r-144-590-wp',
                    'mounting': 'fixed_tilt',
                    'tilt_deg': 23,
                    'table_rows': 2,
                    'modules_per_string': 27,
                    'strings_per_inverter': 24,
                    'n_blocks': 3,
                    'target_dc_kwp': 21792.24,
                    'road_every_rows': 12}),
                  ('network_autobuild',
                   {'project_id': 'kutch-hybrid',
                    'template': 'hybrid',
                    'ac_kw': 20064,
                    'module': 'vikram-solar-hypersol-m10r-144-590-wp',
                    'inverter': 'sungrow-sg350hx',
                    'modules_per_string': 27,
                    'strings_per_inverter': 24,
                    'inverters_per_block': 19,
                    'bess_kw': 10000,
                    'bess_kwh': 20000,
                    'pcs': 'sungrow-st5015kwh-2500kw-2h',
                    'battery': 'sungrow-st5015kwh',
                    'export_limit_kw': 30000}),
                  ('network_update',
                   {'project_id': 'kutch-hybrid',
                    'id': 'e_ct_poi',
                    'fields': {'cable': 'havells-ht-xlpe-al-3c-300-33kv-arm',
                               'runs': 3,
                               'length_m': 300,
                               'laying': 'buried_direct',
                               'remarks': '33 kV export to GETCO line termination, length per user input'}}),
                  ('earthing_design', {'project_id': 'kutch-hybrid', 'surface_resistivity': 3000, 'surface_layer_m': 0.1}),
                  ('lps_design', {'project_id': 'kutch-hybrid', 'method': 'ese', 'level': 'III', 'ese_delta_t_us': 60, 'mast_height_m': 6})],
 'reg-b4': [('project_create',
             {'name': 'Regress B4 rooftop nblocks6',
              'client': 'Shree Ganesh Textiles Pvt Ltd',
              'segment': 'ci_rooftop',
              'site': 'MIDC Chakan',
              'grid_voltage_v': 415,
              'project_id': 'reg-b4',
              'state': 'Maharashtra'}),
            ('project_design_basis',
             {'project_id': 'reg-b4',
              'ambient_min_c': 8,
              'ambient_max_c': 42,
              'fault_levels': [{'voltage_v': 415, 'fault_ka': 25, 'clearing_s': 1, 'source': 'test'}]}),
            ('site_import', {'project_id': 'reg-b4', 'file_path': '{root}/fixtures/sites/rooftop_midc_pune.kml'}),
            ('site_set_setbacks', {'project_id': 'reg-b4', 'roof_edge_m': 1.0, 'obstacle_m': 0.6}),
            ('layout_place',
             {'project_id': 'reg-b4',
              'module': 'adani-solar-elan-shine-topcon-g2g-gen-ii-560-wp',
              'mounting': 'rooftop_flush',
              'pitch_method': 'gap',
              'row_gap_m': 0.6,
              'table_gap_m': 0.6,
              'table_rows': 2,
              'target_dc_kwp': 806.4,
              'modules_per_string': 20,
              'strings_per_inverter': 12,
              'n_blocks': 6}),
            ('network_autobuild',
             {'project_id': 'reg-b4',
              'template': 'ci_rooftop',
              'dc_kwp': 806.4,
              'module': 'adani-solar-elan-shine-topcon-g2g-gen-ii-560-wp',
              'inverter': 'huawei-sun2000-100ktl-m2',
              'modules_per_string': 20,
              'strings_per_inverter': 12})]}


@functools.cache
def workspace(project_id: str) -> Path:
    """A fresh workspace holding `project_id` built by its logged calls (cached per test process)."""
    ws = Path(tempfile.mkdtemp(prefix=f"e2e2-{project_id}-", dir="/tmp/opencode" if Path("/tmp/opencode").is_dir() else None)) / "ws"

    async def go() -> None:
        server = build_server(Config(workspace=ws))
        for tool, args in CALLS[project_id]:
            args = json.loads(json.dumps(args).replace("{root}", str(ROOT)))
            res = await server.call_tool(tool, args)
            assert not getattr(res, "is_error", False), f"{tool}: {res}"

    asyncio.run(go())
    return ws


def scenario(project_id: str) -> tuple[Project, object]:
    ws = workspace(project_id)
    project = ProjectStore(ws).load(project_id, catalog=CATALOG)
    return project, load_profile(project.company_profile, ws)


def test_n1_strings_render_for_a_rooftop_with_one_inverter_per_block():
    project, profile = scenario("reg-b4")
    sheets = build_string_diagram_sheets(project, CATALOG, profile)
    assert sheets and all(r.warnings == () for r in sheets)
    drawn = Counter(s for r in sheets for s in r.covers)
    assert set(drawn) == {i for n in project.network.by_kind(NodeKind.PV_STRING) for i in project.network.instance_ids(n.id)}
    assert max(drawn.values()) == 1
    planned = StringsBuilder().plan(project, profile, CATALOG, Numberer.for_project(project, profile))
    assert [s.paper for s in planned] == [r.paper.value for r in sheets]


def test_n5_hybrid_system_parameters_count_pv_inverters_only_as_the_ratio_rule_does():
    project, _ = scenario("kutch-hybrid")
    rows = dict(system_parameters(project, CATALOG))
    dc, ac = plant_capacity(project, CATALOG)
    assert rows["DC / AC RATIO"] == f"{dc / ac:.2f}" == "1.09"
    assert rows["AC CAPACITY (INVERTER RATED)"] == "20.064 MVA"
    assert rows["INVERTERS"] == "57 x 352 kVA (SUNGROW SG350HX)"
    assert rows["BESS PCS"].startswith("4 x 2.52 MVA (SUNGROW ST5015")


def test_m24_an_unissued_set_dates_every_sheet_and_register_row_with_the_render_date(tmp_path):
    project, profile = scenario("sgt-chakan")
    assert not project.revisions
    index = render_set(project, tmp_path, kinds=ALL_KINDS, formats=("dxf",), profile=profile, catalog=CATALOG, drawn_on=DRAWN_ON)
    stamp = DRAWN_ON.strftime(profile.date_format)
    for s in index.sheets:
        texts = [e.dxf.text for e in ezdxf.readfile(tmp_path / s["files"]["dxf"]).modelspace().query("TEXT")]
        assert stamp in texts, s["number"]
    (register,) = [s for s in index.sheets if s["kind"] == "register"]
    assert {r["date"] for r in register["rows"]} == {stamp}


@pytest.mark.parametrize("project_id", ["sgt-chakan", "kutch-hybrid"])
def test_n13_no_text_on_any_sheet_reads_upside_down_or_mirrored(project_id, tmp_path):
    project, profile = scenario(project_id)
    index = render_set(project, tmp_path, kinds=ALL_KINDS, formats=("dxf",), profile=profile, catalog=CATALOG, drawn_on=DRAWN_ON)
    bad = {s["number"]: m for s in index.sheets if (m := misread_texts(ezdxf.readfile(tmp_path / s["files"]["dxf"])))}
    assert bad == {}


def test_n4_each_lv_feeder_of_a_two_winding_idt_lands_on_its_own_winding():
    project, _ = scenario("kutch-hybrid")
    lay = layout_sld(project.network, CATALOG, SldOptions(), None, None)
    for b in ("b01", "b02", "b03"):
        ends = {w.edge_id: w.end for w in lay.wires if w.edge_id in (f"e_accb_idt_{b}a", f"e_accb_idt_{b}b")}
        assert {e.owner for e in ends.values()} == {f"idt_{b}"}
        assert sorted(e.pin for e in ends.values()) == ["lv1", "lv2"]
        assert len({e.at for e in ends.values()}) == 2
    assert not [s for s in lay.stubs if s.node_id.startswith("idt_")]
    assert [lb.text for s in lay.stubs if s.node_id == "bess_tr" for lb in s.labels] == ["LV2 TO PCS-01-02"]


def _plan_sheets(project, profile):
    numbers = Numberer.for_project(project, profile)
    return EarthingBuilder().plan(project, profile, CATALOG, numbers)[0], LpsBuilder().plan(project, profile, CATALOG, numbers)[0]


def test_n14_rooftop_earthing_and_lps_plans_show_the_whole_plot_boundary():
    project, profile = scenario("sgt-chakan")
    boundary = unary_union([shape_of(f) for f in project.site.geometry.features if f.kind is FeatureKind.BOUNDARY])
    earth, lps = _plan_sheets(project, profile)
    sheets = [
        render_earthing_sheet(ezdxf.new("R2018", setup=True), project, CATALOG, design_earthing(project), sheet_spec(project, earth, 1, 1), profile),
        render_lps_sheet(ezdxf.new("R2018", setup=True), project, CATALOG, design_lps(project), sheet_spec(project, lps, 1, 1), profile),
    ]
    for res in sheets:
        x0, y0, x1, y1 = res.extent
        bx0, by0, bx1, by1 = boundary.bounds
        assert x0 <= bx0 and y0 <= by0 and x1 >= bx1 and y1 >= by1


def test_n15_the_switchyard_mesh_label_is_the_mesh_the_calculation_used():
    project, profile = scenario("thar-40mw")
    d = design_earthing(project)
    earth, _ = _plan_sheets(project, profile)
    doc = ezdxf.new("R2018", setup=True)
    render_earthing_sheet(doc, project, CATALOG, d, sheet_spec(project, earth, 1, 1), profile)
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    i = texts.index("MV SWITCHYARD EARTH MAT")
    assert texts[i + 1] == f"{d.grid_spacing_m:g} m MESH" and not any(t.endswith("m MESH") for t in texts[i + 2 :])
    assert any(t.startswith(f"{d.grid_spacing_m:.2f} m / ") for t in texts)


def test_n7_layout_and_lps_sheets_show_the_switchyard_where_the_earthing_mat_is(tmp_path):
    project, profile = scenario("kutch-hybrid")
    sy = design_earthing(project).layout.switchyard
    name = f"MV SWITCHYARD ({sy.x1 - sy.x0:g} x {sy.y1 - sy.y0:g} m)"
    index = render_set(project, tmp_path, kinds=("layout", "earthing", "lps"), formats=("dxf",), profile=profile, catalog=CATALOG, drawn_on=DRAWN_ON)
    for s in index.sheets:
        msp = ezdxf.readfile(tmp_path / s["files"]["dxf"]).modelspace()
        texts = [e.dxf.text for e in msp.query("TEXT")]
        assert name in texts if s["kind"] != "earthing" else any(t.startswith("MV SWITCHYARD EARTH MAT") for t in texts), s["kind"]
    assert [a.name for a in plant_areas(project)] == [name]
    assert plant_areas(project)[0].outline == ((sy.x0, sy.y0), (sy.x1, sy.y0), (sy.x1, sy.y1), (sy.x0, sy.y1))


def printed_heights(doc) -> list[float]:
    """Height on paper of every TEXT of the diagram blocks (the SLD body is one block inserted at its fit scale)."""
    out = []
    for ref in doc.modelspace().query("INSERT"):
        if ref.dxf.name.startswith("UC_SLD"):
            out += [t.dxf.height * ref.dxf.xscale for t in doc.blocks[ref.dxf.name].query("TEXT")]
    return out


def test_m23_every_hybrid_sld_sheet_prints_its_smallest_text_at_the_lettering_floor(tmp_path):
    project, profile = scenario("kutch-hybrid")
    index = render_set(project, tmp_path, kinds=("sld",), formats=("dxf",), profile=profile, catalog=CATALOG, drawn_on=DRAWN_ON)
    for s in index.sheets:
        heights = printed_heights(ezdxf.readfile(tmp_path / s["files"]["dxf"]))
        assert heights and min(heights) >= MIN_PRINTED_TEXT_MM - 1e-6, (s["number"], s["paper"], min(heights))

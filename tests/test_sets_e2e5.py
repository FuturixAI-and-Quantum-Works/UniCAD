"""Regressions from the fifth end-to-end verifier run (close-e2e-5), replayed over MCP stdio from its logged tool arguments: the overall SLD in
stacked rows on one sheet, sheet numbering, BESS siting, station drawing positions, lightning masts and the drawing details the run flagged."""

import asyncio
from pathlib import Path

import pytest
from test_mcp import connect

from unicad.catalog import load_catalog
from unicad.drafting.sheets.layout import metrics_for
from unicad.drafting.sheets.model import PaperSize
from unicad.drafting.sld.sheet import fit_layout
from unicad.sets.numbering import Numberer
from unicad.sets.render import load_profile
from unicad.sets.sld import SldBuilder, _drawn, _shown, partition
from unicad.store import ProjectStore

ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / "fixtures" / "sites"
CATALOG = load_catalog()
A, B, C = "sgt-chakan", "thar-35mw", "kutch-hybrid"

A_CALLS = [
    ("project_create", {"name": "450 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "Shree Ganesh Textiles factory roof, MIDC Chakan",
                        "grid_voltage_v": 415, "state": "Maharashtra", "address": "MIDC Chakan, Pune", "utility": "MSEDCL", "client_code": "SGT", "project_id": A}),
    ("project_design_basis", {"project_id": A, "ambient_min_c": 6, "ambient_max_c": 44, "altitude_m": 640, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.12,
                              "power_factor": 0.99, "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5}}),
    ("site_import", {"project_id": A, "file_path": str(SITES / "rooftop_midc_pune.kml")}),
    ("site_set_setbacks", {"project_id": A, "roof_edge_m": 1.0, "obstacle_m": 0.6}),
    ("layout_place", {"project_id": A, "module": "tata-power-solar-tp600lg10tb-series-590-wp", "mounting": "rooftop_flush", "target_dc_kwp": 448.4, "modules_per_string": 19,
                      "strings_per_inverter": 10}),
    ("network_autobuild", {"project_id": A, "template": "ci_rooftop", "ac_kw": 400, "module": "tata-power-solar-tp600lg10tb-series-590-wp", "inverter": "solis-s5-gc100k",
                           "modules_per_string": 19, "strings_per_inverter": 10, "lengths": {"acdb_meter": 25}}),
    ("earthing_design", {"project_id": A}),
    ("lps_design", {"project_id": A, "method": "ese", "level": "II", "ese_delta_t_us": 60, "mast_height_m": 5}),
]  # fmt: skip
B_CALLS = [
    ("project_create", {"name": "35 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Rajasthan",
                        "grid_voltage_v": 33000, "state": "Rajasthan", "utility": "RVPNL", "client_code": "TRL", "project_id": B}),
    ("project_design_basis", {"project_id": B, "ambient_min_c": 2, "ambient_max_c": 48, "altitude_m": 250, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.25,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPNL 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": B, "file_path": str(SITES / "ground_mount_rajasthan.kml")}),
    ("site_set_setbacks", {"project_id": B, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": B, "module": "waaree-bin-21-630", "tilt_deg": 25, "road_every_rows": 10, "target_dc_kwp": 45500, "n_blocks": 4, "modules_per_string": 28,
                      "strings_per_inverter": 312}),
    ("network_autobuild", {"project_id": B, "template": "utility_central", "ac_kw": 35200, "module": "waaree-bin-21-630", "inverter": "sma-sc-4400-up", "modules_per_string": 28,
                           "strings_per_inverter": 312, "inverters_per_block": 2}),
    ("earthing_design", {"project_id": B, "soil_resistivity": 150, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45], "surface_resistivity": 3000, "surface_layer_m": 0.1}),
    ("lps_design", {"project_id": B, "method": "ese", "level": "III", "ese_delta_t_us": 60, "mast_height_m": 6}),
]  # fmt: skip
C_BUILD = {
    "project_id": C, "template": "hybrid", "ac_kw": 12000, "module": "waaree-bi-55-545", "inverter": "huawei-sun2000-330ktl-h1", "modules_per_string": 28, "strings_per_inverter": 24,
    "inverters_per_block": 10, "bess_kw": 7560, "bess_kwh": 15045, "pcs": "sungrow-st5015kwh-2500kw-2h", "battery": "sungrow-st5015kwh", "export_limit_kw": 12000,
}  # fmt: skip
C_CALLS = [
    ("project_create", {"name": "12 MWac Solar PV + 6 MW / 12 MWh BESS Hybrid Plant", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch hybrid site, Gujarat",
                        "grid_voltage_v": 33000, "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": C}),
    ("project_design_basis", {"project_id": C, "ambient_min_c": 5, "ambient_max_c": 46, "altitude_m": 20, "vdrop_dc_max": 0.02, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.2,
                              "power_factor": 0.95, "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6}}),
    ("site_import", {"project_id": C, "file_path": str(SITES / "site_plan_kutch.dxf"), "origin_lon": 69.8, "origin_lat": 23.45}),
    ("site_set_setbacks", {"project_id": C, "boundary_m": 10, "exclusion_m": 10}),
    ("layout_place", {"project_id": C, "module": "waaree-bi-55-545", "tilt_deg": 22, "road_every_rows": 10, "target_dc_kwp": 14649.6, "n_blocks": 4, "modules_per_string": 28,
                      "strings_per_inverter": 24}),
    ("network_autobuild", C_BUILD),
    ("earthing_design", {"project_id": C, "soil_resistivity": 80, "fault_ka": 25, "clearing_s": 1, "switchyard_m": [60, 45], "surface_resistivity": 3000, "surface_layer_m": 0.1}),
    ("network_autobuild", C_BUILD),
    ("earthing_design", {"project_id": C}),
    ("lps_design", {"project_id": C, "method": "ese", "level": "III", "ese_delta_t_us": 60, "mast_height_m": 6}),
]  # fmt: skip


@pytest.fixture(scope="module")
def ws(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("e2e5") / "ws"

    async def go():
        async with connect(root) as c:
            for tool, args in (*A_CALLS, *B_CALLS, *C_CALLS):
                await c.call(tool, **args)

    asyncio.run(asyncio.wait_for(go(), 900))
    return root


@pytest.fixture(scope="module")
def projects(ws):
    store = ProjectStore(ws)
    return {pid: store.load(pid, catalog=CATALOG) for pid in (A, B, C)}


def _sld_sheets(p):
    profile = load_profile(p.company_profile)
    sheets = SldBuilder().plan(p, profile, CATALOG, Numberer.for_project(p, profile))
    out = []
    for s, part in zip(sheets, partition(p.network, sheets)):
        lay, opts, scale = fit_layout(_shown(p, s, CATALOG), CATALOG, s.paper, _drawn(p.network, s, part.nodes), xrefs=part.xrefs)
        area = metrics_for(PaperSize(s.paper)).drawing_area
        fill = lay.extent.width * scale * lay.extent.height * scale / (area.width * area.height)
        out.append((s, lay, opts, scale, fill))
    return out


@pytest.mark.parametrize("pid", [B, C])
def test_m23_utility_overall_sld_fills_its_a0_or_a1_sheet_in_stacked_rows_at_the_text_floor(projects, pid):
    """Run 5: the B overall SLD was two A0 sheets at about 45 % fill (C the same); the feeders now stack in rows that share one scale on a sheet,
    each conductor cut between rows ending in a marker naming the row it continues in."""
    overall = [x for x in _sld_sheets(projects[pid]) if x[0].title.startswith("OVERALL")]
    if pid == B:
        assert len(overall) == 1 and overall[0][0].title == "OVERALL SINGLE LINE DIAGRAM"
    for s, lay, opts, scale, fill in overall:
        assert scale >= opts.min_scale, (s.title, scale)
        if s.paper in ("A0", "A1"):
            assert fill >= 0.40, (s.title, s.paper, fill)
    s, lay, *_ = overall[0]
    rows = [h.text for h in lay.headings]
    assert rows[0] == f"ROW 1 OF {len(rows)}" and len(rows) >= 2
    refs = [dict(sym.values).get("SHEET", "") for sym in lay.symbols if sym.role == "offsheet"]
    assert any(r.startswith("SEE ROW ") for r in refs), refs


def test_n17_sheet_x_of_y_counts_the_parts_of_the_same_drawing(projects):
    """Run 5: B SLD 001 read 'PART 1 OF 2' in its title and '1 of 3' in the sheet box (the overall parts and the typical block counted together)."""
    p = projects[C]
    profile = load_profile(p.company_profile)
    sheets = SldBuilder().plan(p, profile, CATALOG, Numberer.for_project(p, profile))
    built = SldBuilder().build(p, sheets, sheets, profile, CATALOG)
    for b in built:
        texts = {e.dxf.text for e in b.doc.modelspace().query("TEXT")}
        title = b.sheet.title
        if " (PART " in title:
            k, n = title.rsplit("(PART ", 1)[1].rstrip(")").split(" OF ")
            assert f"{k} of {n}" in texts, (title, sorted(t for t in texts if " of " in t))
        else:
            assert "1 of 1" in texts, title


def test_r5_m6_bess_stands_off_the_road_and_beside_the_switchyard(projects):
    """Run 5: the 3 x 3 BESS grid straddled the Kutch access road (PCS-01 1.4 m from its centreline) 432 m from the switchyard: the site road
    has no width, so the free area ignored it, and tables left no room near the yard. The road now takes the assumed 6 m width and layout_place
    keeps a BESS area free beside the switchyard."""
    from shapely.geometry import Point as SPoint
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from unicad.arrays.geometry import table_corners
    from unicad.arrays.siting import ROAD_WIDTH_ASSUMED_M, keep_off_m
    from unicad.domain.site import FeatureKind, shape_of
    from unicad.rules.structure import BESS_SEPARATION_M

    p = projects[C]
    site, layout = p.site.geometry, p.array_layout
    bess = [e for e in layout.equipment if e.kind in ("battery", "pcs", "transformer")]
    assert len(bess) == 9
    road = next(shape_of(f) for f in site.features if f.kind is FeatureKind.ROAD)
    tables = unary_union([Polygon(table_corners(t)) for t in layout.table_by_id.values()])
    yard = layout.switchyard.at
    for e in bess:
        pt = SPoint(e.at.x, e.at.y)
        assert road.distance(pt) >= ROAD_WIDTH_ASSUMED_M / 2 + 1.5, (e.tag, road.distance(pt))
        assert tables.distance(pt) >= 1.5, e.tag
        for f in site.features:
            if f.kind in (FeatureKind.WATER, FeatureKind.TRANSMISSION_LINE, FeatureKind.EXCLUSION):
                assert shape_of(f).distance(pt) >= keep_off_m(site, f) - 1e-6, (e.tag, f.id)
        assert abs(e.at.x - yard.x) + abs(e.at.y - yard.y) <= 120, (e.tag, e.at, yard)
    lx, ly = p.design_basis.earthing.switchyard_m
    from shapely.geometry import box

    mat = box(yard.x - lx / 2, yard.y - ly / 2, yard.x + lx / 2, yard.y + ly / 2)
    assert min(mat.distance(SPoint(e.at.x, e.at.y)) for e in bess) >= BESS_SEPARATION_M - 1e-6


@pytest.mark.parametrize("pid", [B, C])
def test_r5_m9_lightning_masts_keep_every_feature_setback_like_other_equipment(projects, pid):
    """Run 5: B ESE-13 stood on the edge of the village pond and C ESE-03 exactly on the edge of the 132 kV corridor; terminals kept clear of
    tables only. They now keep the setback of water, exclusions and line corridors and stand off roads, as stations and BESS units do."""
    from shapely.geometry import Point as SPoint

    from unicad.arrays.siting import ROAD_WIDTH_ASSUMED_M, keep_off_m
    from unicad.domain.site import FeatureKind, shape_of
    from unicad.lps.design import design_lps

    p = projects[pid]
    site = p.site.geometry
    d = design_lps(p)
    assert d.terminals and d.covered_all
    for t in d.terminals:
        if t.on_roof:
            continue
        pt = SPoint(t.at.x, t.at.y)
        for f in site.features:
            g = shape_of(f)
            if f.kind in (FeatureKind.WATER, FeatureKind.TRANSMISSION_LINE, FeatureKind.EXCLUSION, FeatureKind.OBSTACLE):
                assert g.distance(pt) >= keep_off_m(site, f) - 1e-6, (t.tag, f.id, g.distance(pt))
            elif f.kind is FeatureKind.ROAD and f.polyline:
                assert g.distance(pt) >= (f.width_m or ROAD_WIDTH_ASSUMED_M) / 2 - 1e-2, (t.tag, f.id)


@pytest.mark.parametrize("pid", [B, C])
def test_r5_m8_layout_sheet_draws_each_station_at_its_model_position(projects, pid):
    """Run 5: the layout sheet moved the 20 m station symbol off the tables beside its point, onto the 132 kV corridor (C BLOCK-03/04, B BLOCK-04),
    while the model had them 11.5 m and 12.9 m clear. The symbol is now centred on the stored position on open ground."""
    import ezdxf

    from unicad.drafting.site_plan.sheet import LayoutDrawing, render_layout_sheet
    from unicad.drafting.sheets.model import IssuedFor, SheetSpec, TitleBlockData
    from unicad.drafting.symbols import REGISTRY

    p = projects[pid]
    layout = p.array_layout
    tables = list(layout.table_by_id.values())
    drawing = LayoutDrawing(p.site.geometry, layout, CATALOG.get(tables[0].module), max(t.cols for t in tables))
    tb = TitleBlockData(client="C", project_title="P", location="L", drawing_title="LAYOUT", drawing_number="X-101", revision="0", paper=PaperSize.A1,
                        issued_for=IssuedFor.INFORMATION)
    doc = ezdxf.new("R2018", setup=True)
    sheet = render_layout_sheet(doc, drawing, SheetSpec(paper=PaperSize.A1, title_block=tb), load_profile(p.company_profile))
    k = 1000 / sheet.scale
    msp = doc.modelspace()
    first = next(e for e in msp.query("INSERT") if e.dxf.name.startswith("ARR_TABLE"))
    ox, oy = first.dxf.insert.x - k * tables[0].origin.x, first.dxf.insert.y - k * tables[0].origin.y
    sym = REGISTRY["layout.inverter"]
    drawn = sorted((round(e.dxf.insert.x + sym.width * e.dxf.xscale / 2, 1), round(e.dxf.insert.y + sym.height * e.dxf.yscale / 2, 1))
                   for e in msp.query("INSERT") if e.dxf.name == sym.block_name and sheet.plan.x0 <= e.dxf.insert.x <= sheet.plan.x1)  # fmt: skip
    model = sorted((round(ox + k * b.station.x, 1), round(oy + k * b.station.y, 1)) for b in layout.blocks if b.station is not None)
    assert drawn == pytest.approx(model, abs=0.2)


def test_n18_station_earthing_detail_shows_only_equipment_at_that_station(projects):
    """Run 5: rooftop Detail A drew LTP-01 and CT-02 (the ground LT room) at the roof station, and the B block station drew VCB-BF-01 and
    CT-BF-01, which are feeder panels of the main HT switchboard at the switchyard."""
    from unicad.earthing.design import design_earthing

    a = design_earthing(projects[A]).layout
    tags = {it.id: it.tag for it in a.items}
    station = next(g for g in a.stations if g.name == "BLOCK-01 STATION")
    assert "ACDB-01" in {tags[i] for i in station.items} and not {"LTP-01", "CT-02"} & {tags[i] for i in station.items}
    panel = next(g for g in a.stations if g.name == "PLANT EQUIPMENT AREA")
    assert {"LTP-01", "CT-02"} <= {tags[i] for i in panel.items}
    b = design_earthing(projects[B]).layout
    tags = {it.id: it.tag for it in b.items}
    for g in b.stations:
        if g.kind == "station":
            assert not [tags[i] for i in g.items if tags.get(i, "").startswith(("VCB-BF", "CT-BF"))], g.name
    assert any(it.tag.startswith("VCB-BF") for it in b.items)


def _built(p, kind):
    from unicad.sets.registry import builder

    profile = load_profile(p.company_profile)
    b = builder(kind)
    sheets = b.plan(p, profile, CATALOG, Numberer.for_project(p, profile))
    return b.build(p, sheets, sheets, profile, CATALOG)


def _texts(doc) -> set[str]:
    out = set()
    for space in (doc.modelspace(), *doc.blocks):
        for e in space.query("TEXT"):
            out |= {t.strip() for t in e.dxf.text.split(",")}
    return out


def test_m7_rooftop_earthing_plan_tags_every_junction_box(projects):
    """Run 5 (and run 4 m7): the DCJBs on the rooftop earthing plan were unlabelled squares."""
    from unicad.earthing.design import design_earthing

    p = projects[A]
    (sheet,) = _built(p, "earthing")
    texts = _texts(sheet.doc)
    boxes = [it.tag for it in design_earthing(p).layout.items if it.tag.startswith("DCJB")]
    assert len(boxes) == 4 and set(boxes) <= texts, sorted(set(boxes) - texts)


@pytest.mark.parametrize("kind", ["layout", "lps"])
def test_n21_plant_area_and_terminal_labels_stay_with_what_they_name(projects, kind):
    """Run 5: C's 'MV SWITCHYARD' label sat far from its box and B's ESE-09 label read as ESE-07's. The area name is written inside its
    outline, and a terminal label is its tag alone (the radius is in the schedule), so it fits beside its terminal."""
    p = projects[C]
    (sheet,) = _built(p, kind)
    msp = sheet.doc.modelspace()
    names = [e for e in msp.query("TEXT") if e.dxf.text in ("MV SWITCHYARD", "(60 x 45 m)", "MV SWITCHYARD (60 x 45 m)")]
    outlines = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "L-STATION" and len(e) == 4]
    assert names and outlines
    for t in names:
        at = t.dxf.align_point if t.dxf.hasattr("align_point") and t.dxf.halign else t.dxf.insert
        x, y = at.x, at.y
        assert any(min(q[0] for q in o.get_points()) <= x <= max(q[0] for q in o.get_points()) and min(q[1] for q in o.get_points()) <= y <= max(q[1] for q in o.get_points())
                   for o in outlines), t.dxf.text  # fmt: skip
    if kind == "lps":
        assert not [e for e in msp.query("TEXT") if "RADIUS COVERED" in e.dxf.text]


def test_r5_m3_string_fuses_are_drawn_in_both_poles_as_designed_and_billed(projects):
    """Run 5: C's string diagram drew one fuse per string in the (+) pole and the SLD read '25 A, 1P', while the design and the BOM fuse both
    poles (1920 links for 960 strings)."""
    from unicad.drafting.symbols import REGISTRY

    p = projects[C]
    fuses = [n for n in p.network.nodes if n.kind.value == "fuse"]
    assert fuses and all(n.attributes.get("fused_poles") == 2 for n in fuses)
    block = REGISTRY["dc.fuse"].block_name
    for b in _built(p, "strings"):
        refs = [e for e in b.doc.modelspace().query("INSERT") if e.dxf.name.startswith(block)]
        legend = [e for e in refs if e.dxf.xscale == e.dxf.yscale and abs(e.dxf.rotation) < 1e-6]
        strings = {t.split(" ")[0] for t in _texts(b.doc) if t.startswith("STR-") and " : " in t}
        assert strings and len(refs) - len(legend) == 2 * len(strings), (len(refs), len(legend), len(strings))
        raw = " ".join(e.dxf.text for e in b.doc.modelspace().query("TEXT"))
        assert "+ AND -" in raw and "POLES" in raw
    sld = [t for b in _built(p, "sld") for t in _texts(b.doc)]
    assert "1P (+ AND - POLES)" in " ".join(sld) and not [t for t in sld if t.startswith("25 A") and t.split(", ")[1:2] == ["1P"]]


def test_m10_sld_notes_list_each_cable_remark_once(projects):
    """Run 5: A SLD notes 3 and 4 were the same string-circuit remark, one with a length range appended. The range now stays on the
    callout and both circuits refer to one note."""
    p = projects[A]
    ((s, lay, *_),) = _sld_sheets(p)
    assert len(lay.notes) == len(set(lay.notes))
    assert not [(a, b) for a in lay.notes for b in lay.notes if a != b and (a.startswith(b) or b.startswith(a))], lay.notes
    callouts = [lb.text for c in lay.callouts for lb in c.labels]
    assert any("L = " in t and ".." in t for t in callouts)

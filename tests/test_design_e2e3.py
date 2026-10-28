"""Regressions from the third end-to-end verifier run (close-e2e-3), replayed from its logged MCP tool arguments (stdio)."""

import asyncio
from pathlib import Path

from test_mcp import connect

ROOT = Path(__file__).resolve().parents[1]
PUNE = ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml"
RAJ = ROOT / "fixtures" / "sites" / "ground_mount_rajasthan.kml"
KUTCH = ROOT / "fixtures" / "sites" / "site_plan_kutch.dxf"
ACME = ROOT / "profiles" / "samples" / "acme"

A, B, C = "sgt-chakan", "thar-30mw", "kutch-hybrid"
A_MOD, A_INV = "tata-power-solar-tp600lg10tb-series-590-wp", "solis-s5-gc100k"
B_MOD, B_INV = "adani-solar-elan-shine-topcon-g2g-gen-ii-580-wp", "sungrow-sg3125hv-30"
C_MOD = "waaree-bin-21-620"
A_CREATE = {
    "name": "600 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop", "site": "Shree Ganesh Textiles factory roof, MIDC Chakan",
    "grid_voltage_v": 415, "state": "Maharashtra", "address": "Plot, MIDC Chakan, Pune, Maharashtra", "utility": "MSEDCL", "client_code": "SGT", "project_id": A,
}  # fmt: skip
A_BASIS = {
    "project_id": A, "ambient_min_c": 6, "ambient_max_c": 44, "altitude_m": 640, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.15,
    "power_factor": 0.99, "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
    "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5},
}  # fmt: skip
A_PLACE = {"project_id": A, "module": A_MOD, "mounting": "rooftop_flush", "modules_per_string": 19, "strings_per_inverter": 11, "target_dc_kwp": 616.55, "n_blocks": 1}
A_BUILD = {
    "project_id": A, "template": "ci_rooftop", "ac_kw": 500, "module": A_MOD, "inverter": A_INV, "modules_per_string": 19, "strings_per_inverter": 11, "connection": "lt_panel",
}  # fmt: skip
B_CREATE = {
    "name": "30 MWac Ground Mount Solar PV Plant", "client": "Thar Renewables Ltd", "segment": "utility", "site": "Thar Renewables solar park, Phalodi", "grid_voltage_v": 33000,
    "state": "Rajasthan", "utility": "RVPN", "client_code": "TRL", "project_id": B, "address": "Phalodi, Jodhpur district, Rajasthan",
}  # fmt: skip
B_BASIS = {
    "project_id": B, "ambient_min_c": 3, "ambient_max_c": 49, "altitude_m": 240, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015, "dc_ac_ratio_target": 1.25, "power_factor": 0.95,
    "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "RVPN 33 kV fault level (brief)"}],
    "earthing": {"soil_resistivity_ohm_m": 150, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
}  # fmt: skip
B_PLACE = {
    "project_id": B, "module": B_MOD, "mounting": "fixed_tilt", "tilt_deg": 25, "table_rows": 2, "table_cols": 27, "road_every_rows": 12, "modules_per_string": 27,
    "strings_per_inverter": 274, "n_blocks": 9, "target_dc_kwp": 38617.56,
}  # fmt: skip
B_BUILD = {
    "project_id": B, "template": "utility_central", "module": B_MOD, "inverter": B_INV, "modules_per_string": 27, "strings_per_inverter": 274, "inverters_per_block": 1,
}  # fmt: skip
C_CREATE = {
    "name": "Kutch Hybrid 20 MWac PV + 10 MW 20 MWh BESS", "client": "Kutch Hybrid Power Pvt Ltd", "segment": "hybrid", "site": "Kutch hybrid park", "grid_voltage_v": 33000,
    "state": "Gujarat", "utility": "GETCO", "client_code": "KHP", "project_id": C, "address": "Kutch district, Gujarat",
}  # fmt: skip
C_BASIS = {
    "project_id": C, "ambient_min_c": 5, "ambient_max_c": 47, "altitude_m": 20, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.02, "dc_ac_ratio_target": 1.15, "power_factor": 0.95,
    "fault_levels": [{"voltage_v": 33000, "fault_ka": 25, "clearing_s": 1, "source": "GETCO 33 kV fault level (brief)"}],
    "earthing": {"soil_resistivity_ohm_m": 80, "fault_current_ka": 25, "fault_clearing_s": 1}, "lps": {"level": "III", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 6},
}  # fmt: skip
C_PLACE = {
    "project_id": C, "module": C_MOD, "mounting": "fixed_tilt", "tilt_deg": 23, "table_rows": 2, "table_cols": 28, "road_every_rows": 12, "modules_per_string": 28,
    "strings_per_inverter": 22, "n_blocks": 4, "target_dc_kwp": 22300,
}  # fmt: skip
C_BESS = {"bess_kw": 10000, "bess_kwh": 20000, "pcs": "sungrow-st5015kwh-2500kw-2h", "battery": "sungrow-st5015kwh", "export_limit_kw": 30000}
C_BUILD = {
    "project_id": C, "template": "hybrid", "module": C_MOD, "inverter": "sungrow-sg350hx", "modules_per_string": 28, "strings_per_inverter": 22, "inverters_per_block": 17, **C_BESS,
}  # fmt: skip
C_HUAWEI = {
    "project_id": C, "template": "hybrid", "ac_kw": 20400, "module": C_MOD, "inverter": "huawei-sun2000-330ktl-h1", "modules_per_string": 28, "strings_per_inverter": 19,
    "inverters_per_block": 18, **C_BESS,
}  # fmt: skip
C_HUAWEI_PLACE = C_PLACE | {"strings_per_inverter": 19, "target_dc_kwp": 22600}


def run(coro, timeout=600):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def site_a(c):
    await c.call("company_onboard", dxf_path=str(ACME / "acme-company.dxf"), answers=str(ACME / "answers.yaml"))
    await c.call("project_create", **A_CREATE)
    await c.call("company_assign", project_id=A, profile_id="acme")
    await c.call("project_design_basis", **A_BASIS)
    await c.call("site_import", project_id=A, file_path=str(PUNE))
    await c.call("site_set_setbacks", project_id=A, roof_edge_m=1.0, obstacle_m=0.6)


async def site_b(c):
    await c.call("project_create", **B_CREATE)
    await c.call("project_design_basis", **B_BASIS)
    await c.call("site_import", project_id=B, file_path=str(RAJ))
    await c.call("site_set_setbacks", project_id=B, boundary_m=10, exclusion_m=10)


async def site_c(c):
    await c.call("project_create", **C_CREATE)
    await c.call("project_design_basis", **C_BASIS)
    await c.call("site_import", project_id=C, file_path=str(KUTCH), origin_lon=69.8, origin_lat=23.45)
    await c.call("site_set_setbacks", project_id=C, boundary_m=10, exclusion_m=10)


async def project(c, pid):
    return (await c.call("project_get", project_id=pid, full=True))["project"]


def _inverters(placed, spi):
    return [round(b["modules"] / spi / placed["metrics"]["modules"] * placed["metrics"]["strings"]) for b in placed["blocks"]]


def test_a1_odd_strings_per_inverter_on_two_string_tables_reach_the_target_by_mixing_table_sizes(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_a(c)
            placed = await c.call("layout_place", **(A_PLACE | {"target_dc_kwp": 369.93}))
            assert placed["target_reached"] is True
            assert placed["metrics"]["strings"] == 33 and placed["metrics"]["dc_kwp"] == 369.93, placed["metrics"]
            assert any("mixed table sizes" in p["message"] for p in placed["problems"]), placed["problems"]
            layout = (await project(c, A))["array_layout"]
            assert sorted({t["rows"] for b in layout["blocks"] for t in b["tables"]}) == [1, 2]
            # the logged call: the roof (with its maintenance walkways) holds 4 whole inverters, and layout_place says so instead of reporting the target reached
            placed = await c.call("layout_place", **A_PLACE)
            assert placed["target_reached"] is False and placed["metrics"]["strings"] % 11 == 0
            short = [p["message"] for p in placed["problems"] if "not reached with whole inverters" in p["message"]]
            assert short and "kWp short" in short[0], placed["problems"]

    run(go())


def test_b5_n_blocks_at_the_exact_target_gives_every_block_and_the_whole_target(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_b(c)
            placed = await c.call("layout_place", **B_PLACE)
            assert placed["target_reached"] is True and len(placed["blocks"]) == 9, placed["blocks"]
            assert placed["metrics"]["dc_kwp"] == 38617.56

    run(go())


def test_a1_a_target_that_is_not_whole_inverters_says_so_with_the_shortfall(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            placed = await c.call("layout_place", **C_PLACE)
            assert placed["target_reached"] is False
            short = [p["message"] for p in placed["problems"] if "not reached with whole inverters" in p["message"]]
            assert short and "148.64 kWp short" in short[0] and "22151.36 (58 inverters) or 22533.28 (59)" in short[0], placed["problems"]
            assert placed["metrics"]["dc_kwp"] == 22151.36

    run(go())


def test_c2_blocks_balance_to_one_inverter_and_block_dc_kwp_is_an_upper_bound(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            spi = C_PLACE["strings_per_inverter"]
            placed = await c.call("layout_place", **C_PLACE)
            invs = [b["modules"] // (spi * 28) for b in placed["blocks"]]
            assert all(b["modules"] % (spi * 28) == 0 for b in placed["blocks"])
            assert max(invs) - min(invs) <= 1 and sum(invs) == 58, invs
            capped = await c.call("layout_place", **(C_PLACE | {"n_blocks": None, "block_dc_kwp": 5729, "target_dc_kwp": 22916}))
            assert all(b["dc_kwp"] <= 5729 for b in capped["blocks"]), capped["blocks"]
            invs = [b["modules"] // (spi * 28) for b in capped["blocks"]]
            assert max(invs) - min(invs) <= 1 and capped["target_reached"] is True, invs

    run(go())


def test_b9_default_fill_grows_from_the_switchyard_side(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_b(c)

            def mean_feeder(placed):
                y = placed["switchyard"]
                d = [abs(b["station"]["x"] - y["x"]) + abs(b["station"]["y"] - y["y"]) for b in placed["blocks"]]
                return sum(d) / len(d)

            rows = await c.call("layout_place", **(B_PLACE | {"fill": "rows"}))
            near = await c.call("layout_place", **B_PLACE)
            assert near["target_reached"] and any("filled nearest the transmission line" in a for a in near["assumptions"]), near["assumptions"]
            assert mean_feeder(near) < 0.75 * mean_feeder(rows), (mean_feeder(near), mean_feeder(rows))

    run(go())


def test_c1_design_strings_and_autobuild_respect_the_string_fuse_rule_validate_applies(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            sd = await c.call("design_strings", project_id=C, module=C_MOD, inverter="huawei-sun2000-330ktl-h1")
            assert sd["strings_per_mppt_unfused"] == 2 and sd["strings_per_inverter"] <= 12 == sd["strings_per_inverter_unfused"], sd
            assert sd["string_fuses"] == "optional" and any("gPV fuse per string" in a for a in sd["assumptions"]), sd["assumptions"]
            await c.call("layout_place", **C_HUAWEI_PLACE)
            built = await c.call("network_autobuild", **(C_HUAWEI | {"ac_kw": None}))
            v = await c.call("validate", project_id=C, families=["pv_strings", "protection"])
            errors = [f for f in v["findings"] if f["severity"] == "error"]
            assert not errors and not [f for f in built["findings"] if f["severity"] == "error"], errors
            net = (await project(c, C))["network"]
            fuses = [n for n in net["nodes"] if n["kind"] == "fuse"]
            assert fuses and all(n["spec"] and n["spec"].startswith("mersen-hp15m") for n in fuses), fuses
            assert any("gPV fuse in a string fuse box" in a for a in built["assumptions"])

    run(go())


def test_b2_b3_n11_feeders_end_in_switchboard_breakers_aux_taps_have_their_own_vcb_and_inverter_cables_clear_on_the_idt_breaker(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_b(c)
            await c.call("layout_place", **B_PLACE)
            built = await c.call("network_autobuild", **B_BUILD)
            net = (await project(c, B))["network"]
            nodes, edges = {n["id"]: n for n in net["nodes"]}, {e["id"]: e for e in net["edges"]}
            feeders = [e for e in edges.values() if e["id"].startswith("e_icog_bus")]
            assert feeders and all(e["dst"].startswith("bf_ct") and e["cable"] for e in feeders), feeders
            on_bus = [e for e in edges.values() if e["dst"] == "mv_bus:bus" and e["src"].startswith(("icog", "bf_"))]
            assert on_bus and all(nodes[e["src"].split(":")[0]]["kind"] == "vcb" and nodes[e["src"].split(":")[0]]["spec"] for e in on_bus), on_bus
            aux = [e for e in edges.values() if e["dst"].startswith("blk_aux") and e["dst"].endswith(":hv")]
            assert aux and all(nodes[e["src"].split(":")[0]]["kind"] == "vcb" for e in aux), aux
            v = await c.call("validate", project_id=B, families=["protection", "cables"], include_info=True)
            assert not [f for f in v["findings"] if f["rule_id"] in ("protection.mv_cable", "protection.transformer_feed")], v["findings"]
            sc = [f for f in v["findings"] if f["rule_id"] == "cables.short_circuit" and any(s.startswith("e_inv_idt") for s in f["subjects"])]
            assert sc, [f["subjects"] for f in v["findings"] if f["rule_id"] == "cables.short_circuit"]
            for f in sc:
                ex = await c.call("explain_finding", project_id=B, rule_id="cables.short_circuit", subject=f["subjects"][0])
                text = str(ex)
                assert "No clearing time" not in text and "on its HV side" in text and "ICOG-VCB" in text, ex
            assert any("feeder panel" in a for a in built["assumptions"])

    run(go())


def test_n23_three_phase_ac_spds_have_three_or_four_poles_and_a_single_pole_one_is_flagged(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            await c.call("layout_place", **C_PLACE)
            built = await c.call("network_autobuild", **C_BUILD)
            assert not [b for b in built["blockers"] if "SPD" in b], built["blockers"]
            net = (await project(c, C))["network"]
            spds = [n for n in net["nodes"] if n["kind"] == "ac_spd"]
            for n in spds:
                spec = (await c.call("catalog_get", id=n["spec"]))
                spec = spec.get("spec", spec)
                assert spec["poles"] >= 3, (n["id"], spec)
            one = next(n for n in spds if n["voltage_v"] and n["voltage_v"] >= 600)
            await c.call("network_update", project_id=C, id=one["id"], fields={"spec": "dehn-dg-se-ci-440-fm"})
            v = await c.call("validate", project_id=C, families=["protection"])
            hits = [f for f in v["findings"] if f["rule_id"] == "protection.spd_ac_poles"]
            assert hits and hits[0]["severity"] == "error" and hits[0]["subjects"] == [one["id"]], hits

    run(go())


def _sheet(path, name):
    import openpyxl

    ws = openpyxl.load_workbook(path, read_only=True)[name]
    head, _units, *body = list(ws.iter_rows(values_only=True))
    return [dict(zip(head, r, strict=False)) for r in body if r[0] is not None]


def test_s1_a4_string_and_lt_cables_are_sized_per_length_band_and_the_schedule_and_bom_follow(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_a(c)
            await c.call("layout_place", **A_PLACE)
            built = await c.call("network_autobuild", **A_BUILD)
            assert any(a.startswith("String and LT cables are sized per length band") for a in built["assumptions"])
            net = (await project(c, A))["network"]
            banded = {e["id"]: e for e in net["edges"] if e.get("bands")}
            assert any(i.startswith("e_inv_brk") for i in banded), sorted(banded)
            v = await c.call("validate", project_id=A)
            errors = [f["rule_id"] for f in v["findings"] if f["severity"] == "error"]
            # the roof with its maintenance walkways holds less than the 616.55 kWp target, so layout.shortfall (RV-M6) is the one error
            assert errors == ["layout.shortfall"] and not [f for f in v["findings"] if f["rule_id"] == "vdrop.bands"], v["findings"]
            rows = _sheet((await c.call("cable_schedule", project_id=A, format="xlsx"))["files"][0], "Cable schedule")
            for eid, e in banded.items():
                mine = [r for r in rows if r["NETWORK EDGE"] == eid]
                refs = {r["CATALOG ID"] for r in mine}
                assert refs == {e["cable"], *(b["cable"] for b in e["bands"])}, (eid, refs)
                for r in mine:
                    length = (r["LENGTH POSITIVE POLARITY"] + (r["LENGTH NEGATIVE POLARITY"] or r["LENGTH POSITIVE POLARITY"])) / 2
                    band = next((b for b in e["bands"] if length <= b["upto_m"] + 1e-6), None)
                    assert r["CATALOG ID"] == (band["cable"] if band else e["cable"]), (eid, length, r["CATALOG ID"])
                    assert r["VOLTAGE DROP"] is not None
            bom = _sheet((await c.call("bom", project_id=A, format="xlsx"))["files"][0], "BOM")
            ordered = {ln["CATALOG ID"] for ln in bom if ln["CATEGORY"] == "CABLES"}
            assert {b["cable"] for e in banded.values() for b in e["bands"]} <= ordered, ordered

    run(go())


def test_c2_block_transformers_take_one_standard_rating_covering_the_largest_block(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            placed = await c.call("layout_place", **C_PLACE)
            assert len({b["modules"] for b in placed["blocks"]}) > 1
            built = await c.call("network_autobuild", **C_BUILD)
            net = (await project(c, C))["network"]
            idts = [n["spec"] for n in net["nodes"] if n["id"].startswith("idt")]
            ratings = {(s["rated_kva"], tuple(s["winding_kva"] or ())) if isinstance(s, dict) else s for s in idts}
            assert len(idts) > 1 and len(ratings) == 1, ratings
            assert any("ratings are standardised" in a for a in built["assumptions"])

    run(go())


def test_r1_flush_rooftop_rows_get_a_maintenance_walkway_every_two_rows(tmp_path):
    import math

    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_a(c)
            placed = await c.call("layout_place", **A_PLACE)
            assert any("maintenance walkway 0.6 m wide after every 2 rows" in a for a in placed["assumptions"]), placed["assumptions"]
            lay = (await project(c, A))["array_layout"]
            tables = [t for b in lay["blocks"] for t in b["tables"]]
            depth = max(t["rows"] * t["slot_height_m"] for t in tables)
            by_roof_col = {}
            for t in tables:
                az = math.radians(t["azimuth_deg"])
                across = -t["origin"]["x"] * math.cos(az) + t["origin"]["y"] * math.sin(az)
                along = t["origin"]["x"] * math.sin(az) + t["origin"]["y"] * math.cos(az)
                by_roof_col.setdefault(round(along, 1), []).append(across)
            gaps = [round(abs(b - a) - depth, 2) for v in by_roof_col.values() for a, b in zip(sorted(v), sorted(v)[1:])]
            assert any(g >= 0.6 - 1e-6 for g in gaps), gaps

    run(go())


def test_c3_bess_units_stand_outside_the_switchyard_mat_with_a_cited_separation(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            await c.call("layout_place", **C_PLACE)
            built = await c.call("network_autobuild", **C_BUILD)
            assert any("IFC 2024 Section 1207.8.3" in a for a in built["assumptions"]) and any("switchyard size is not set" in a for a in built["assumptions"])
            await c.call("earthing_design", project_id=C, soil_resistivity=80, fault_ka=25, clearing_s=1, switchyard_m=[60, 45])
            lay = (await project(c, C))["array_layout"]
            yx, yy = lay["switchyard"]["at"]["x"], lay["switchyard"]["at"]["y"]
            bess = [e for e in lay["equipment"] if e["kind"] in ("battery", "pcs", "transformer")]
            assert len(bess) == 10, bess
            for e in bess:
                dx, dy = abs(e["at"]["x"] - yx) - 30, abs(e["at"]["y"] - yy) - 22.5
                assert max(dx, dy) >= 3.05 - 1e-6, (e, yx, yy)
            v = await c.call("validate", project_id=C, families=["structure"])
            assert not [f for f in v["findings"] if f["rule_id"] == "structure.bess_separation"], v["findings"]

    run(go())


def test_a3_rooftop_string_inverters_stand_on_a_roof_that_carries_their_strings(tmp_path):
    from shapely.geometry import Point as SPoint

    from unicad.arrays.geometry import table_centre
    from unicad.domain.layout import Table
    from unicad.domain.site import FeatureKind, SiteGeometry, shape_of

    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_a(c)
            await c.call("layout_place", **A_PLACE)
            await c.call("network_autobuild", **A_BUILD)
            p = await project(c, A)
            site = SiteGeometry.model_validate(p["site"]["geometry"])
            roofs = [shape_of(f) for f in site.of_kind(FeatureKind.ROOF) if f.polygon]
            tables = {t["id"]: Table.model_validate(t) for b in p["array_layout"]["blocks"] for t in b["tables"]}
            strings = {a["string"]: a for a in p["array_layout"]["strings"]}
            inv = [e for e in p["array_layout"]["equipment"] if e["kind"] == "inverter"]
            assert inv
            for e in inv:
                at = SPoint(e["at"]["x"], e["at"]["y"])
                roof = next((r for r in roofs if r.covers(at)), None)
                assert roof is not None, e
                base, _, idx = e["node"].partition("@")
                mine = [a for s, a in strings.items() if s.startswith(f"{base.replace('inv', 'str', 1)}@{idx}.")]
                assert mine, (e, list(strings)[:3])
                on = sum(roof.covers(SPoint(*table_centre(tables[a["spans"][0]["table"]]))) for a in mine)
                assert on * 2 >= len(mine), (e, on, len(mine))

    run(go())

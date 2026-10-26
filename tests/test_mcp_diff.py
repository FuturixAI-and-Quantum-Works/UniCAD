"""project_diff and the project summaries over MCP: site, layout and sheet changes stay readable."""

from pathlib import Path

from test_mcp import CREATE, session_test

SITES = Path(__file__).resolve().parents[1] / "fixtures" / "sites"
ROOFTOP = str(SITES / "rooftop_midc_pune.kml")
MODULE = "adani-solar-elan-shine-topcon-g2g-gen-ii-555-wp"
LAYOUT = dict(module=MODULE, mounting="rooftop_flush", table_cols=10, pitch_method="gap", row_gap_m=0.6, half_tables=False, target_dc_kwp=244.2, modules_per_string=20)


@session_test
async def test_summaries_grow_with_the_project(c):
    created = await c.call("project_create", **CREATE)
    pid = created["project_id"]
    assert created["summary"] == {"groups": 0, "nodes": 0, "edges": 0}

    imported = await c.call("site_import", project_id=pid, file_path=ROOFTOP)
    assert imported["summary"]["site_features"] == 11 and "layout_blocks" not in imported["summary"]

    placed = await c.call("layout_place", project_id=pid, **LAYOUT)
    assert placed["summary"] | {} == {"groups": 0, "nodes": 0, "edges": 0, "site_features": 11, "layout_blocks": 1, "layout_tables": 22, "layout_strings": 0}

    built = await c.call("network_autobuild", project_id=pid, template="ci_rooftop", dc_kwp=244.2, module=MODULE, inverter="huawei-sun2000-100ktl-m2")
    assert built["summary"]["layout_strings"] == 22 and built["summary"]["nodes"] > 0

    got = await c.call("project_get", project_id=pid)
    assert got["counts"] == built["summary"]
    revs = await c.call("project_revisions", project_id=pid)
    assert revs["revisions"][0] == {"revision": 1, "snapshots": [], "groups": 0, "nodes": 0, "edges": 0}
    assert revs["revisions"][-1]["site_features"] == 11 and revs["revisions"][-1]["layout_blocks"] == 1

    designed = await c.call("earthing_design", project_id=pid, soil_resistivity=100, fault_ka=10, clearing_s=1)
    assert designed["summary"]["earthing"] is True and "lps" not in designed["summary"]


@session_test
async def test_project_diff_reports_site_by_feature_and_layout_by_block(c):
    pid = (await c.call("project_create", **CREATE))["project_id"]
    await c.call("site_import", project_id=pid, file_path=ROOFTOP)
    await c.call("layout_place", project_id=pid, **LAYOUT)
    await c.call("project_snapshot", project_id=pid, label="surveyed")

    await c.call("site_set_feature", project_id=pid, id="rooftop-ahu-3", kind="obstacle", coordinates=[[30, 24], [34, 24], [34, 28], [30, 28]], height_m=1.8)
    await c.call("site_set_feature", project_id=pid, id="rooftop-ahu-1", kind="obstacle", coordinates=[[110, 85], [116, 85], [116, 90], [110, 90]], height_m=2.4)
    await c.call("site_remove_feature", project_id=pid, ids=["chimney-stack"])
    await c.call("layout_place", project_id=pid, **LAYOUT | {"target_dc_kwp": 200})

    d = await c.call("project_diff", project_id=pid, from_revision="surveyed")
    assert d["site"]["added"] == ["rooftop-ahu-3"] and d["site"]["removed"] == ["chimney-stack"]
    (ahu,) = d["site"]["changed"]
    assert ahu["id"] == "rooftop-ahu-1" and {"path": "height_m", "before": 1.8, "after": 2.4} in ahu["fields"]
    assert "polygon" not in str(ahu)
    (block,) = d["layout"]["changed"]
    fields = {f["path"]: f for f in block["fields"]}
    assert block["id"] == "B01" and fields["tables"] == {"path": "tables", "before": 22, "after": 18}
    assert fields["modules"]["before"] == 440 and fields["modules"]["after"] == 360
    assert "fields" not in d, d.get("fields")
    assert len(str(d)) < 2500

    assert (await c.call("project_diff", project_id=pid, from_revision="surveyed", to_revision="surveyed"))["empty"]

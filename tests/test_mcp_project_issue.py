"""project_issue over MCP stdio: the issue revision reaches every sheet and the register, and the rules gate refuses approval while errors remain (N13).
The project replays the close-e2e run 3 rooftop calls (scenario A) up to network_autobuild; the run's hand edits of the LT cables are left out, because the
autobuilt cables are sized for the voltage drop limit with the inverter breakers' terminal capacity, and the DISCOM service cable is by others."""

import asyncio
from pathlib import Path

import ezdxf
from test_mcp import connect

ROOT = Path(__file__).resolve().parents[1]
ACME = ROOT / "profiles" / "samples" / "acme"
MODULE = "tata-power-solar-tp600lg10tb-series-590-wp"
PID = "sgt-chakan"

ROOFTOP_A = [
    ("company_onboard", {"dxf_path": str(ACME / "acme-company.dxf"), "answers": str(ACME / "answers.yaml")}),
    ("project_create", {"name": "600 kWp Rooftop Solar PV Plant", "client": "Shree Ganesh Textiles Pvt Ltd", "segment": "ci_rooftop",
                        "site": "Shree Ganesh Textiles factory roof, MIDC Chakan", "grid_voltage_v": 415, "state": "Maharashtra",
                        "address": "Plot, MIDC Chakan, Pune, Maharashtra", "utility": "MSEDCL", "client_code": "SGT", "project_id": PID}),
    ("company_assign", {"project_id": PID, "profile_id": "acme"}),
    ("project_design_basis", {"project_id": PID, "ambient_min_c": 6, "ambient_max_c": 44, "altitude_m": 640, "vdrop_dc_max": 0.015, "vdrop_ac_max": 0.015,
                              "dc_ac_ratio_target": 1.15, "power_factor": 0.99,
                              "fault_levels": [{"voltage_v": 415, "fault_ka": 25, "clearing_s": 1, "source": "MSEDCL LT fault level (brief)"}],
                              "earthing": {"soil_resistivity_ohm_m": 100, "fault_current_ka": 25, "fault_clearing_s": 1},
                              "lps": {"level": "II", "method": "ese", "ese_delta_t_us": 60, "mast_height_m": 5}}),
    ("site_import", {"project_id": PID, "file_path": str(ROOT / "fixtures" / "sites" / "rooftop_midc_pune.kml")}),
    ("site_set_setbacks", {"project_id": PID, "roof_edge_m": 1.0, "obstacle_m": 0.6}),
    ("layout_place", {"project_id": PID, "module": MODULE, "mounting": "rooftop_flush", "table_rows": 1, "modules_per_string": 19, "strings_per_inverter": 11,
                      "target_dc_kwp": 616.55, "n_blocks": 1}),
    ("network_autobuild", {"project_id": PID, "template": "ci_rooftop", "ac_kw": 500, "module": MODULE, "inverter": "solis-s5-gc100k", "modules_per_string": 19,
                           "strings_per_inverter": 11, "connection": "lt_panel"}),
]  # fmt: skip

ISSUE = {"project_id": PID, "rev": "0", "drawn": "A. Kumar", "checked": "S. Rao", "approved": "P. Iyer", "date": "2026-10-28"}


def texts(path: Path) -> list[str]:
    doc = ezdxf.readfile(path)
    out = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    for block in doc.blocks:
        out += [e.dxf.text for e in block.query("TEXT ATTRIB")]
    for insert in doc.modelspace().query("INSERT"):
        out += [a.dxf.text for a in insert.attribs]
    return out


def test_project_issue_gates_on_errors_and_reaches_every_sheet(tmp_path):
    async def run():
        async with connect(tmp_path / "ws") as c:
            for tool, args in ROOFTOP_A:
                await c.call(tool, **args)
            built = next(e for e in (await c.call("project_get", project_id=PID, full=True))["project"]["network"]["edges"] if e["id"] == "e_brk_ct")
            await c.call("network_update", project_id=PID, id="e_brk_ct", fields={"cable": "kei-lt-xlpe-al-1c-16-arm", "runs": 1})
            refused = await c.fail("project_issue", issued_for="approval", **ISSUE)
            assert "cannot issue" in refused and "cables." in refused and "e_brk_ct" in refused

            info = await c.call("project_issue", issued_for="information", **(ISSUE | {"rev": "A", "remarks": "PRELIMINARY"}))
            assert info["gate"]["allowed"] and info["gate"]["errors"] >= 1 and info["problems"]

            await c.call("network_update", project_id=PID, id="e_brk_ct", fields={"cable": built["cable"], "runs": built.get("runs", 1)})
            issued = await c.call("project_issue", issued_for="approval", **ISSUE)
            assert issued["changed"] and issued["revisions"] == ["A", "0"] and issued["gate"]["errors"] == 0
            again = await c.call("project_issue", issued_for="approval", **ISSUE)
            assert not again["changed"]
            clash = await c.fail("project_issue", issued_for="approval", **(ISSUE | {"drawn": "B. Singh"}))
            assert "already issued" in clash

            drawn = await c.call("drawings_render", project_id=PID, kinds=["register", "sld", "cable_schedule"], formats=["dxf"])
            return drawn

    drawn = asyncio.run(asyncio.wait_for(run(), 300))
    out = Path(drawn["out_dir"])
    assert len(drawn["sheets"]) >= 3
    for s in drawn["sheets"]:
        t = texts(out / s["files"]["dxf"])
        joined = " | ".join(t)
        assert "A. Kumar" in t and "S. Rao" in t and "P. Iyer" in t, (s["number"], joined)
        assert "PRELIMINARY" in joined and "ISSUED FOR APPROVAL" in joined, (s["number"], joined)
        assert "NOT YET ISSUED" not in joined and t.count("AK") >= 2 and t.count("SR") >= 2, (s["number"], joined)
    register = next(s for s in drawn["sheets"] if s["kind"] == "register")
    rt = texts(out / register["files"]["dxf"])
    assert rt.count("APPROVAL") > len(drawn["sheets"]) and "0 - ISSUED FOR APPROVAL" in rt, " | ".join(rt)

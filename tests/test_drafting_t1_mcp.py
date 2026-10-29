"""Drafting follow-ups after fix wave 7 over MCP stdio: drawings_render applies the company's rule thresholds as validate, design_strings and the
schedule tools do (s1 follow-up)."""

import asyncio
from pathlib import Path

import ezdxf

from test_sets import connect, fixture

ROOT = Path(__file__).resolve().parents[1]
ACME = ROOT / "profiles" / "samples" / "acme"


def texts(dxf: str) -> list[str]:
    return [e.dxf.text for e in ezdxf.readfile(dxf).modelspace().query("TEXT")]


def test_drawings_render_proposes_string_fuses_with_the_company_isc_factor(tmp_path):
    from unicad.store import ProjectStore

    ws = tmp_path / "ws"
    ProjectStore(ws).save(fixture("utility_50mwac"))

    async def run():
        async with connect(ws) as c:
            base = await c.call("drawings_render", project_id="utility-50mwac", kinds=["strings"], formats=["dxf"])
            assert "20 A GPV (PROPOSED)" in texts(base["sheets"][0]["files"]["dxf"])
            await c.call("company_onboard", dxf_path=str(ACME / "acme-company.dxf"), answers=str(ACME / "answers.yaml"))
            await c.call("company_assign", project_id="utility-50mwac", profile_id="acme")
            await c.call("company_update", profile_id="acme", fields={"thresholds": {"isc_factor": {"value": 1.5, "source": "Acme DS-04 rev D"}}})
            company = await c.call("drawings_render", project_id="utility-50mwac", kinds=["strings"], formats=["dxf"])
            got = texts(company["sheets"][0]["files"]["dxf"])
            assert "20 A GPV (PROPOSED)" not in got and any(t.endswith("A GPV (PROPOSED)") for t in got), got

    asyncio.run(asyncio.wait_for(run(), 300))

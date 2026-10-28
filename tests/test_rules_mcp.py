"""The rules engine over MCP stdio, on projects imported from the fixtures."""

import asyncio
import json

from test_mcp import connect
from test_rules import CATALOG, FIXTURES, rooftop_broken

from unicad.domain.project import Project
from unicad.store import ProjectStore, parse_project


def _import(ws, *projects: Project) -> None:
    store = ProjectStore(ws)
    for p in projects:
        store.save(p)


def _run(tmp_path, fn, *projects):
    ws = tmp_path / "ws"
    _import(ws, *projects)

    async def run():
        async with connect(ws) as c:
            await fn(c)

    asyncio.run(asyncio.wait_for(run(), 120))


def _fixture(name: str) -> Project:
    return parse_project((FIXTURES / f"{name}.json").read_text(encoding="utf-8"), catalog=CATALOG)


def _broken() -> Project:
    data = json.loads((FIXTURES / "rooftop_500kwp.json").read_text(encoding="utf-8"))
    rooftop_broken(data)
    data["id"] = "rooftop-broken"
    return Project.model_validate(data, context={"catalog": CATALOG})


def test_validate_fixture_projects_over_stdio(tmp_path):
    async def check(c):
        r = await c.call("validate", project_id="rooftop-500kwp")
        assert r["revision"] == 1 and r["counts"]["error"] == 0 and not r["blocking"]
        assert r["gates"]["approval"] == {"allowed": True, "watermark": False}
        assert all(f["severity"] != "info" for f in r["findings"]) and r["counts"]["info"] > 0
        assert {f["subjects"][0] for f in r["findings"] if f["rule_id"] == "cables.missing"} == {"e_ct_poi", "e_ltp_load"}

        r = await c.call("validate", project_id="rooftop-500kwp", families=["vdrop"], include_info=True)
        totals = {tuple(f["subjects"]): f for f in r["findings"]}
        ac = totals[("inv", "poi")]
        assert ac["unit"] == "%" and ac["limit"] == 2.0 and 1.5 < ac["measured"] < 1.6

        r = await c.call("validate", project_id="rooftop-broken")
        assert r["blocking"] and r["counts"]["error"] == 2
        assert r["gates"]["approval"]["allowed"] is False and r["gates"]["construction"]["allowed"] is False
        assert r["gates"]["proposal"] == {"allowed": True, "watermark": True}
        mccb = next(f for f in r["findings"] if f["subjects"] == ["inv_mccb"])
        assert (mccb["measured"], round(mccb["limit"], 1), mccb["unit"]) == (160, 191.3, "A")

        msg = await c.fail("validate", project_id="rooftop-500kwp", families=["nope"])
        assert "'cables'" in msg and "'earthing'" in msg, msg  # the schema lists the allowed families

        r = await c.call("validate", project_id="utility-50mwac")
        errors = [f for f in r["findings"] if f["severity"] == "error"]
        assert not errors and r["gates"]["approval"]["allowed"] is True, errors

    _run(tmp_path, check, _fixture("rooftop_500kwp"), _fixture("utility_50mwac"), _broken())


def test_explain_finding_over_stdio(tmp_path):
    async def check(c):
        r = await c.call("explain_finding", project_id="rooftop-broken", rule_id="protection.rated_current", subject="inv_mccb")
        (ex,) = r["explanations"]
        assert ex["measured"] == 160 and ex["formula"].startswith("In >= k x Ib")
        assert "lk-dsine-dn1-250-c-160a-4p" in ex["sources"]

        r = await c.call("explain_finding", project_id="rooftop-500kwp", rule_id="cables.ampacity", subject="e_inv_mccb")
        (ex,) = r["explanations"]
        assert ex["title"] and "derating" in ex["message"]
        assert {q["name"] for q in ex["inputs"]} >= {"I_table", "k_derating", "runs", "V"}

        msg = await c.fail("explain_finding", project_id="rooftop-500kwp", rule_id="cables.protection", subject="inv_mccb")
        assert "no finding" in msg and "passed" in msg
        msg = await c.fail("explain_finding", project_id="rooftop-500kwp", rule_id="nope.x", subject="inv")
        assert "'cables.ampacity'" in msg and "'earthing.touch_step'" in msg, msg  # the schema lists the rule ids

    _run(tmp_path, check, _fixture("rooftop_500kwp"), _broken())

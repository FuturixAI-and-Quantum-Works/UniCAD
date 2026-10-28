"""String-inverter patches: compact contiguous groups of strings per inverter, the inverter at the run-minimising free spot, and a precise blocker."""

from test_design_e2e3 import C, C_HUAWEI, C_HUAWEI_PLACE, project, run, site_c
from test_mcp import connect

from unicad.arrays.geometry import frame
from unicad.design.patches import patches, reach
from unicad.design.placement import string_runs
from unicad.domain.layout import SlotSpan, StringAssignment
from unicad.domain.project import Project
from unicad.routing import RouteAssumptions
from unicad.routing.locate import Locator


def _grid_items(cells):
    return [((32.0 * c, 4.5 * r), StringAssignment(string=f"s{r}-{c}", spans=(SlotSpan(table=f"t{r}-{c}", row=0, col=0, n=28),))) for r, c in cells]


def _connected(cells):
    cells = set(cells)
    seen, todo = set(), [next(iter(cells))]
    while todo:
        r, c = todo.pop()
        if (r, c) in seen:
            continue
        seen.add((r, c))
        todo += [n for n in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)) if n in cells]
    return seen == cells


def test_patches_split_an_irregular_block_into_contiguous_compact_groups_of_the_exact_sizes():
    cells = [(r, c) for r in range(24) for c in range(6) if not (r >= 12 and c >= 3)]
    sizes = [19] * 5 + [len(cells) - 19 * 5]
    groups = patches(_grid_items(cells), sizes)
    assert [len(g) for g in groups] == sizes
    by_id = {a.string: (r, c) for (r, c), (_, a) in zip(cells, _grid_items(cells), strict=True)}
    for g in groups:
        mine = [by_id[a.string] for a in g]
        assert _connected(mine), sorted(mine)
        assert reach([(32.0 * c, 4.5 * r) for r, c in mine]) <= 60, sorted(mine)


def _patch_reach(p: Project) -> dict[str, float]:
    loc = Locator(p.array_layout, p.network.expand(), RouteAssumptions())
    out = {}
    for e in p.array_layout.equipment:
        if e.kind == "inverter" and (strings := loc.strings_below(e.node)):
            (ux, uy), (vx, vy) = frame(loc.strings[strings[0]].azimuth_deg)
            ends = [q for s in strings for q in (loc.strings[s].plus, loc.strings[s].minus)]
            out[e.node] = reach([(x * ux + y * uy, x * vx + y * vy) for x, y in ends])
    return out


def test_c1_each_string_inverter_takes_a_compact_patch_and_its_longest_string_run_is_bounded_by_the_patch(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            await c.call("layout_place", **C_HUAWEI_PLACE)
            built = await c.call("network_autobuild", **(C_HUAWEI | {"ac_kw": None}))
            assert not [b for b in built["blockers"] if "voltage drop" in b], built["blockers"]
            assert any("compact patches of neighbouring tables" in a for a in built["assumptions"])
            p = Project.model_validate(await project(c, C))
            runs, reaches = string_runs(p), _patch_reach(p)
            assert len(runs) == 68 and set(runs) == set(reaches)
            for inv, (length, n) in runs.items():
                assert n == 19 and reaches[inv] <= 90, (inv, reaches[inv])
                assert length <= reaches[inv] + 25, (inv, length, reaches[inv])

    run(go())


def test_c1_a_block_that_cannot_meet_the_drop_limit_names_its_worst_inverter_patch_and_the_options(tmp_path):
    async def go():
        async with connect(tmp_path / "ws") as c:
            await site_c(c)
            await c.call("project_design_basis", project_id=C, vdrop_dc_max=0.008)
            await c.call("layout_place", **C_HUAWEI_PLACE)
            built = await c.call("network_autobuild", **(C_HUAWEI | {"ac_kw": None}))
            hits = [b for b in built["blockers"] if b.startswith("DC string runs of INV-")]
            assert hits, built["blockers"]
            for b in hits:
                assert "against the 0.8 % limit" in b and "strings_per_inverter <= " in b and "inverters_per_block" in b and "vdrop_dc_max of at least" in b, b

    run(go())

import math

from unicad.arrays.blocks import compact


def spread(groups, pts):
    total = 0.0
    for g in groups:
        cx = sum(pts[i][0] for i in g) / len(g)
        cy = sum(pts[i][1] for i in g) / len(g)
        total += sum((pts[i][0] - cx) ** 2 + (pts[i][1] - cy) ** 2 for i in g)
    return total


def grid(cols, rows, dx=22.3, dy=7.74):
    return [(c * dx, r * dy) for r in range(rows) for c in range(cols)]


def test_compact_joins_a_block_split_in_two_pieces():
    pts = grid(4, 4)
    ring = [i for i in range(16) if i % 4 in (0, 3)]
    middle = [i for i in range(16) if i % 4 in (1, 2)]
    out = compact([ring, middle], pts, [("t", 2)] * 16)
    assert spread(out, pts) < spread([ring, middle], pts)
    assert sorted(i for g in out for i in g) == list(range(16))
    assert [len(g) for g in out] == [8, 8]
    for g in out:
        cols = sorted({i % 4 for i in g})
        assert cols == list(range(cols[0], cols[-1] + 1)), f"a block still spans separate columns: {cols}"


def test_compact_never_swaps_tables_of_different_class():
    pts = grid(4, 4)
    classes = [("big", 3) if i % 2 else ("small", 1) for i in range(16)]
    groups = [[i for i in range(16) if (i // 4) % 2 == 0], [i for i in range(16) if (i // 4) % 2 == 1]]
    out = compact(groups, pts, classes)
    for before, after in zip(groups, out):
        assert sorted(classes[i] for i in before) == sorted(classes[i] for i in after)


def test_compact_leaves_a_single_block_alone():
    pts = grid(3, 3)
    assert compact([list(range(9))], pts, [("t", 1)] * 9) == [list(range(9))]
    assert math.isclose(spread([list(range(9))], pts), spread(compact([list(range(9))], pts, [("t", 1)] * 9), pts))

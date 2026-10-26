"""Label placer: no two labels overlap, labels stay within the leader limit or go to overflow, and the result is deterministic."""

import math
import random

from unicad.drafting.labels import Label, LabelPlacer, overlapping
from unicad.drafting.sheets.canvas import Region


def _cluster(n: int, seed: int = 1, spread: float = 6.0) -> list[Label]:
    rnd = random.Random(seed)
    return [Label(f"EP{i}", (50 + rnd.uniform(-spread, spread), 50 + rnd.uniform(-spread, spread)), 6.0, 1.5, clear=0.8) for i in range(n)]


def _run(labels, bounds=Region(0, 0, 100, 100), max_leader=12.0, blocks=()):
    lp = LabelPlacer(bounds, max_leader, gap=0.2)
    for b in blocks:
        lp.block(b)
    lp.place_all(labels)
    return lp


def test_a_lone_label_sits_beside_its_anchor_without_a_leader():
    lp = _run([Label("A", (10, 10), 5, 1.5, clear=1)])
    (p,) = lp.labels
    assert p.leader is None and p.box.x0 > 10 and abs(p.box.centre[1] - 10) < 1e-9


def test_dense_cluster_has_no_overlapping_labels_and_leaders_within_the_limit():
    labels = _cluster(30)
    lp = _run(labels)
    boxes = [p.box for p in lp.labels]
    assert overlapping(boxes) == []
    assert len(lp.labels) + len(lp.overflow) == 30
    for p in lp.labels:
        tip = (min(max(p.label.anchor[0], p.box.x0), p.box.x1), min(max(p.label.anchor[1], p.box.y0), p.box.y1))
        assert math.dist(p.label.anchor, tip) <= 12.0 + 1e-6
        assert Region(0, 0, 100, 100).contains(p.box)
    assert any(p.leader is not None for p in lp.labels)


def test_labels_avoid_blocked_symbols():
    sym = Region(48, 48, 52, 52)
    lp = _run(_cluster(10, spread=3), blocks=[sym])
    assert not any(p.box.overlaps(sym) for p in lp.labels)


def test_labels_that_cannot_fit_go_to_overflow():
    lp = _run(_cluster(40, spread=1), bounds=Region(40, 40, 60, 60), max_leader=4.0)
    assert lp.overflow and overlapping([p.box for p in lp.labels]) == []
    assert {lb.key for lb in lp.overflow}.isdisjoint({p.label.key for p in lp.labels})


def test_placement_is_deterministic():
    a = [(p.label.key, p.box) for p in _run(_cluster(25, seed=7)).labels]
    b = [(p.label.key, p.box) for p in _run(_cluster(25, seed=7)).labels]
    assert a == b

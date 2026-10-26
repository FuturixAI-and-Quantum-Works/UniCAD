import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import ezdxf
import pytest
from ezdxf import bbox
from ezdxf.path import make_path

from unicad.drafting.symbols import GRID, REGISTRY, Category, PinKind, Side, insert, install, pin_at
from unicad.drafting.symbols.catalogue import build_catalogue, manifest

ROOT = Path(__file__).resolve().parents[1]
LIBRECAD = os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun")
ALLOWED = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "TEXT", "SOLID"}
TOL = 1e-6
NO_ATTRS = {"layout.module", "ann.junction", "ann.north"}
SHORT_ATTRS = {"REV": 1, "NUM": 2, "ID": 1}
TWO_AC_PINS_ON_A_SIDE = {"sw.changeover", "sw.rmu"}
CHAR_W = 0.8

ALL = list(REGISTRY.values())
ids = [s.key for s in ALL]


@pytest.fixture(scope="module")
def full_doc():
    doc = ezdxf.new("R2018", setup=False)
    install(doc)
    return doc


def geometry(doc, sym):
    return [e for e in doc.blocks[sym.block_name] if e.dxftype() != "ATTDEF"]


def polylines(entities):
    out = []
    for e in entities:
        if e.dxftype() == "TEXT":
            box = bbox.extents([e])
            lo, hi = box.extmin, box.extmax
            out.append([(lo.x, lo.y), (hi.x, lo.y), (hi.x, hi.y), (lo.x, hi.y), (lo.x, lo.y)])
        else:
            for sub in make_path(e).sub_paths():
                out.append([(v.x, v.y) for v in sub.flattening(0.01, segments=8)])
    return out


def segments(entities):
    for pts in polylines(entities):
        yield from zip(pts, pts[1:])


def seg_hits_rect(a, b, x0, y0, x1, y1):
    def inside(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    if max(a[0], b[0]) < x0 or min(a[0], b[0]) > x1 or max(a[1], b[1]) < y0 or min(a[1], b[1]) > y1:
        return False

    if inside(a) or inside(b):
        return True
    edges = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    return any(_cross(a, b, c, d) for c, d in edges)


def _cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return orient(a, b, c) * orient(a, b, d) <= 0 and orient(c, d, a) * orient(c, d, b) <= 0


def dist_point_segment(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    n = dx * dx + dy * dy
    t = 0.0 if n == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / n))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def on_grid(v):
    return abs(v / GRID - round(v / GRID)) < 1e-9


def test_keys_and_block_names_unique():
    assert len({s.key for s in ALL}) == len(ALL)
    assert len({s.block_name for s in ALL}) == len(ALL)
    assert all(s.key.count(".") == 1 for s in ALL)


def test_every_category_has_symbols():
    assert {s.category for s in ALL} == set(Category)


def test_required_symbols_exist():
    required = """
    pv.module pv.string pv.array dc.fuse dc.isolator dc.spd dc.mcb dc.diode dc.scb
    inv.string inv.central inv.micro inv.pcs inv.optimiser bat.battery bat.bess
    sw.mcb sw.mccb sw.acb sw.rccb sw.hrc_fuse sw.switch_fuse sw.isolator sw.isolator_es sw.lbs
    sw.contactor sw.vcb sw.sf6 sw.draw_out sw.changeover sw.rmu prot.spd_ac prot.lightning_arrester
    prot.relay tx.power_2w tx.power_3w tx.aux tx.ct tx.pt inst.kwh inst.net_meter inst.abt
    inst.ammeter inst.voltmeter inst.mfm inst.datalogger inst.weather_station grid.utility
    grid.oh_line grid.cable_end grid.dg grid.load earth.earth earth.pe earth.electrode earth.chassis
    ann.junction ann.offsheet ann.cable_tag ann.revision ann.north ann.note_marker
    ann.section_marker layout.module layout.inverter layout.earth_pit layout.ese_la
    layout.transformer layout.scb
    """.split()
    assert not [k for k in required if k not in REGISTRY]


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_pins_are_on_grid_and_on_their_edge(sym):
    assert len({p.name for p in sym.pins}) == len(sym.pins)
    for p in sym.pins:
        assert on_grid(p.x) and on_grid(p.y), p
        edge = {Side.N: p.y - sym.height, Side.S: p.y, Side.E: p.x - sym.width, Side.W: p.x}[p.side]
        assert abs(edge) < TOL, f"{p.name} is not on the {p.side} edge"
        along = {Side.N: p.x, Side.S: p.x, Side.E: p.y, Side.W: p.y}[p.side]
        assert 0 <= along <= (sym.width if p.side in (Side.N, Side.S) else sym.height)


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_each_pin_has_conductor_geometry_ending_on_it(sym, full_doc):
    segs = list(segments(geometry(full_doc, sym)))
    for p in sym.pins:
        assert min(dist_point_segment((p.x, p.y), a, b) for a, b in segs) < 1e-4, f"nothing is drawn at pin {p.name}"


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_geometry_stays_inside_declared_box(sym, full_doc):
    box = bbox.extents(geometry(full_doc, sym))
    assert box.has_data
    tol = 0.05
    assert box.extmin.x >= -tol and box.extmin.y >= -tol, box
    assert box.extmax.x <= sym.width + tol and box.extmax.y <= sym.height + tol, box


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_only_allowed_entities_on_symbol_layer(sym, full_doc):
    for e in geometry(full_doc, sym):
        assert e.dxftype() in ALLOWED, e.dxftype()
        assert e.dxf.layer == "G-SYMBOL"


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_ac_pins_one_per_side(sym):
    per_side = {}
    for p in sym.pins:
        if p.kind is PinKind.AC:
            per_side[p.side] = per_side.get(p.side, 0) + 1
    if sym.key not in TWO_AC_PINS_ON_A_SIDE:
        assert all(n == 1 for n in per_side.values())


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_attributes_are_unique_and_complete(sym, full_doc):
    tags = [a.tag for a in sym.attrs]
    assert len(tags) == len(set(tags))
    if sym.key not in NO_ATTRS and sym.category is not Category.ANNOTATION:
        assert {"TAG", "RATING"} <= set(tags)
    attdefs = [e for e in full_doc.blocks[sym.block_name] if e.dxftype() == "ATTDEF"]
    assert sorted(e.dxf.tag for e in attdefs) == sorted(tags)
    assert all(e.dxf.layer == "G-SYMBOL" for e in attdefs)


@pytest.mark.parametrize("sym", ALL, ids=ids)
def test_attribute_text_clears_geometry(sym, full_doc):
    segs = list(segments(geometry(full_doc, sym)))
    for a in sym.attrs:
        n = SHORT_ATTRS.get(a.tag, max(10, len(a.default)))
        x0, x1 = a.x, a.x + n * CHAR_W * a.height
        y0, y1 = a.y - a.height / 2, a.y + a.height / 2
        hit = [s for s in segs if seg_hits_rect(*s, x0, y0, x1, y1)]
        assert not hit, f"{a.tag} text box ({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f}) overlaps geometry"


def test_attribute_text_of_different_attrs_does_not_overlap():
    for sym in ALL:
        rects = [(a.tag, a.y - a.height / 2, a.y + a.height / 2, a.x, a.x + 8 * CHAR_W * a.height) for a in sym.attrs]
        for i, r in enumerate(rects):
            for q in rects[i + 1 :]:
                same_column = abs(r[3] - q[3]) < 1e-6
                if same_column:
                    assert r[2] <= q[1] + 1e-9 or q[2] <= r[1] + 1e-9, f"{sym.key}: {r[0]} overlaps {q[0]}"


def test_install_is_idempotent_and_selective():
    doc = ezdxf.new("R2018", setup=False)
    install(doc, ["sw.mcb"])
    assert "UC_SW_MCB" in doc.blocks and "UC_SW_MCCB" not in doc.blocks
    first = [e.dxftype() for e in doc.blocks["UC_SW_MCB"]]
    install(doc, ["sw.mcb", "sw.mccb"])
    install(doc)
    assert [e.dxftype() for e in doc.blocks["UC_SW_MCB"]] == first
    assert sum(1 for b in doc.blocks if b.name.startswith("UC_")) == len(REGISTRY)


def test_install_unknown_key_raises():
    with pytest.raises(KeyError):
        install(ezdxf.new("R2018"), ["nope.nothing"])


def test_insert_rejects_unknown_tag():
    doc = ezdxf.new("R2018")
    with pytest.raises(ValueError, match="BOGUS"):
        insert(doc.modelspace(), "sw.mcb", (0, 0), values={"TAG": "Q1", "BOGUS": "x"})


def test_insert_writes_values_defaults_and_visible_text():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    ref = insert(msp, "tx.power_2w", (100, 50), values={"TAG": "TR-1", "RATING": "2.5 MVA"})
    attribs = {a.dxf.tag: a for a in ref.attribs}
    assert attribs["TAG"].dxf.text == "TR-1"
    assert attribs["VECTOR"].dxf.text == "Dyn11"
    assert attribs["DESC"].dxf.text == ""
    texts = {t.dxf.text for t in msp.query("TEXT")}
    assert {"TR-1", "2.5 MVA", "Dyn11"} <= texts
    assert "" not in texts
    for a in ref.attribs:
        assert a.dxf.flags & 1


def test_insert_without_visible_text_keeps_attribs_visible():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    ref = insert(msp, "sw.mcb", (0, 0), values={"TAG": "Q1"}, visible_text=False)
    assert not msp.query("TEXT")
    assert all(not (a.dxf.flags & 1) for a in ref.attribs)


def test_insert_places_block_at_point_and_rotation():
    doc = ezdxf.new("R2018")
    ref = insert(doc.modelspace(), "sw.mcb", (12.5, 7.5), rotation=90)
    assert tuple(ref.dxf.insert)[:2] == (12.5, 7.5)
    assert ref.dxf.rotation == 90
    assert ref.dxf.name == "UC_SW_MCB"


def test_insert_text_is_right_aligned_when_flipped_west():
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    insert(msp, "sw.mcb", (0, 0), rotation=180, values={"TAG": "Q1"})
    text = next(iter(msp.query("TEXT")))
    assert text.dxf.insert.x < 0
    assert text.dxf.halign == 2


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pin_at_matches_cad_insert_transform(rotation):
    doc = ezdxf.new("R2018")
    for key in ("sw.changeover", "tx.power_3w", "inv.micro"):
        sym = REGISTRY[key]
        at = (40.0, -20.0)
        ref = insert(doc.modelspace(), key, at, rotation=rotation)
        m = ref.matrix44()
        for p in sym.pins:
            expect = m.transform((p.x, p.y, 0))
            got = pin_at(key, p.name, at, rotation)
            assert got == pytest.approx((expect.x, expect.y), abs=1e-6)


def test_pin_at_rotation_semantics():
    assert pin_at("sw.mcb", "in", (0, 0)) == (5.0, 20.0)
    assert pin_at("sw.mcb", "out", (0, 0), 90) == (0.0, 5.0)
    assert pin_at("sw.mcb", "in", (0, 0), 90) == (-20.0, 5.0)
    assert pin_at("sw.mcb", "in", (10, 10), 180) == (5.0, -10.0)
    with pytest.raises(KeyError):
        pin_at("sw.mcb", "nope", (0, 0))


def test_manifest_matches_registry_and_committed_file():
    m = manifest()
    assert [e["key"] for e in m] == list(REGISTRY)
    committed = ROOT / "library" / "unicad-symbols.json"
    if committed.exists():
        assert json.loads(committed.read_text()) == m, "run tools/build_symbol_library.py"


@pytest.fixture(scope="module")
def catalogue_dxf(tmp_path_factory):
    doc = build_catalogue()
    path = tmp_path_factory.mktemp("lib") / "unicad-symbols.dxf"
    doc.saveas(path)
    return doc, path


def test_catalogue_audits_clean_and_round_trips(catalogue_dxf):
    doc, path = catalogue_dxf
    audit = doc.audit()
    assert not audit.errors, [e.message for e in audit.errors]
    reloaded = ezdxf.readfile(path)
    assert not reloaded.audit().errors
    assert sum(1 for b in reloaded.blocks if b.name.startswith("UC_")) == len(REGISTRY)
    assert len(list(reloaded.modelspace().query("INSERT"))) == len(REGISTRY)


def test_catalogue_pins_are_marked_on_their_own_layer(catalogue_dxf):
    doc, _ = catalogue_dxf
    pins = sum(len(s.pins) for s in ALL)
    assert len(list(doc.modelspace().query("CIRCLE[layer=='G-PIN']"))) == pins


@pytest.mark.skipif(not (Path(LIBRECAD).exists() or shutil.which(LIBRECAD)), reason="LibreCAD not installed")
def test_librecad_renders_catalogue_to_pdf(catalogue_dxf, tmp_path):
    _, path = catalogue_dxf
    out = tmp_path / "out.pdf"
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    proc = subprocess.run(
        [LIBRECAD, "dxf2pdf", "-a", "-p", "841x594", "-o", str(out), str(path)],
        env=env,
        capture_output=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr.decode()[-500:]
    assert out.exists() and out.stat().st_size > 10_000
    assert out.read_bytes()[:5] == b"%PDF-"

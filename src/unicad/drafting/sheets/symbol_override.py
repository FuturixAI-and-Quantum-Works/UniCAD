"""A company's own symbol in place of a UniCAD one.

The company block is moved and scaled so its pins land on the UniCAD symbol's pins, because the diagram layout routes conductors to
those positions. Pins are named by POINT entities on layers `PIN_<pin name>` (an ATTDEF tagged `PIN_<pin name>` also marks one), or,
with `pins="fit"`, found by fitting the block's extents to the UniCAD symbol's box and checking each pin touches the geometry.
The block's geometry goes on the UniCAD symbol layer so the profile's layer map decides what it looks like.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.document import Drawing
from ezdxf.entities import DXFGraphic
from ezdxf.math import Matrix44, Vec3
from ezdxf.path import make_path

from unicad.drafting.dxfrules import HATCH_PATTERNS, plain
from unicad.drafting.sheets.model import CompanyProfile, SymbolOverride
from unicad.drafting.symbols import REGISTRY, install
from unicad.drafting.symbols.install import refresh_turned
from unicad.drafting.symbols.library.kit import LAYER, TEXT_STYLE
from unicad.drafting.symbols.model import SymbolDef

PIN_TOLERANCE = 0.25
TOUCH_TOLERANCE = 0.5
ASPECT_TOLERANCE = 0.05
SPILL_WARN = 1.0
SPILL_FAIL = 0.25
_PIN_LAYER = re.compile(r"^PIN[_-](.+)$", re.IGNORECASE)
_DRAWN = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SOLID", "HATCH", "TEXT"})


class SymbolOverrideError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class Prepared:
    entities: tuple[DXFGraphic, ...] = field(repr=False)
    matrix: Matrix44 = field(repr=False)
    scale: float
    residual: float
    warnings: tuple[str, ...] = ()


def _flatten(entities, depth: int = 0) -> list[DXFGraphic]:
    out: list[DXFGraphic] = []
    for e in entities:
        t = e.dxftype()
        if t == "INSERT" and depth < 6:
            out += _flatten(list(e.virtual_entities()), depth + 1)
        elif t in _DRAWN | {"POINT", "ATTDEF", "MTEXT"}:
            out.append(e)
    return out


def _named_pins(entities: list[DXFGraphic]) -> dict[str, Vec3]:
    pins: dict[str, Vec3] = {}
    for e in entities:
        name = None
        if e.dxftype() == "POINT" and (m := _PIN_LAYER.match(e.dxf.layer)):
            name, at = m.group(1), Vec3(e.dxf.location)
        elif e.dxftype() == "ATTDEF" and (m := _PIN_LAYER.match(e.dxf.tag)):
            name, at = m.group(1), Vec3(e.dxf.insert)
        if name is not None:
            pins[name.lower()] = at
    return pins


def _solve(pairs: list[tuple[Vec3, Vec3]]) -> tuple[float, Vec3]:
    """Scale and translation taking the first of each pair to the second, least squares."""
    n = len(pairs)
    cx = sum((c for c, _ in pairs), Vec3()) / n
    px = sum((p for _, p in pairs), Vec3()) / n
    if n == 1:
        return 1.0, px - cx
    den = sum((c - cx).dot(c - cx) for c, _ in pairs)
    if den < 1e-12:
        raise SymbolOverrideError(["the pin points all sit at one position"])
    s = sum((c - cx).dot(p - px) for c, p in pairs) / den
    if s <= 0:
        raise SymbolOverrideError(["the pins are in the opposite order to the UniCAD symbol's (the block would have to be turned or mirrored)"])
    return s, px - cx * s


def _distance_to_geometry(point: Vec3, entities: list[DXFGraphic]) -> float:
    best = float("inf")
    for e in entities:
        if e.dxftype() not in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE"):
            continue
        pts = list(make_path(e).flattening(0.05))
        for a, b in zip(pts, pts[1:], strict=False) if len(pts) > 1 else []:
            ab = b - a
            t = 0.0 if ab.magnitude < 1e-12 else max(0.0, min(1.0, (point - a).dot(ab) / ab.dot(ab)))
            best = min(best, (point - (a + ab * t)).magnitude)
    return best


def _geometry(entities: list[DXFGraphic]) -> list[DXFGraphic]:
    return [e for e in entities if e.dxftype() in _DRAWN and not (e.dxftype() == "HATCH" and e.dxf.pattern_name.upper() not in HATCH_PATTERNS)]


def prepare(sym: SymbolDef, ov: SymbolOverride) -> Prepared:
    """Parse the company block and work out how it must be moved and scaled; raises SymbolOverrideError listing every problem."""
    doc = ezdxf.readfile(ov.dxf)
    if ov.block not in doc.blocks:
        raise SymbolOverrideError([f"{ov.dxf.name} has no block {ov.block!r}; blocks: {sorted(b.name for b in doc.blocks if not b.name.startswith('*'))}"])
    entities = _flatten(list(doc.blocks.get(ov.block)))
    geometry = _geometry(entities)
    if not geometry:
        raise SymbolOverrideError([f"block {ov.block!r} has no drawable geometry"])
    want = {p.name.lower(): Vec3(p.x, p.y) for p in sym.pins}
    problems: list[str] = []
    if ov.pins == "named":
        found = _named_pins(entities)
        if sym.pins and not found:
            problems.append(f"block {ov.block!r} marks no pins; put a POINT on a layer PIN_<name> at each pin of {sym.key} ({', '.join(p.name for p in sym.pins)}) or use pins='fit'")
        unknown = sorted(set(found) - set(want))
        if unknown:
            problems.append(f"block {ov.block!r} marks pins {unknown} that {sym.key} does not have; its pins: {sorted(want)}")
        missing = sorted(set(want) - set(found))
        if found and missing:
            problems.append(f"block {ov.block!r} does not mark pins {missing} of {sym.key}")
        if problems:
            raise SymbolOverrideError(problems)
        scale, shift = _solve([(found[n], want[n]) for n in sorted(want)]) if want else (1.0, Vec3())
    else:
        box = bbox.extents(geometry)
        w, h = box.size.x, box.size.y
        if w < 1e-9 or h < 1e-9:
            raise SymbolOverrideError([f"block {ov.block!r} is flat; pins='fit' needs geometry with width and height"])
        sx, sy = sym.width / w, sym.height / h
        if abs(sx / sy - 1) > ASPECT_TOLERANCE:
            raise SymbolOverrideError([f"block {ov.block!r} is {w:g} x {h:g}, a different shape from {sym.key} ({sym.width:g} x {sym.height:g}); mark the pins with POINTs on PIN_<name> layers instead"])
        scale = (sx + sy) / 2
        shift = Vec3(-box.extmin.x * scale, -box.extmin.y * scale)
    matrix = Matrix44.scale(scale, scale, scale) @ Matrix44.translate(shift.x, shift.y, 0)
    moved = []
    for e in geometry:
        c = e.copy()
        c.transform(matrix)
        moved.append(c)
    warnings: list[str] = []
    residual = 0.0
    if ov.pins == "named":
        residual = max((matrix.transform(found[n]) - want[n]).magnitude for n in want) if want else 0.0
        if residual > PIN_TOLERANCE:
            raise SymbolOverrideError([f"block {ov.block!r}: pin points are {residual:.2f} mm from the UniCAD pins after scaling by {scale:.3f}; the pin spacing differs in shape from {sym.key}"])
    else:
        for name, at in want.items():
            d = _distance_to_geometry(Vec3(at), moved)
            if d > TOUCH_TOLERANCE:
                raise SymbolOverrideError([f"block {ov.block!r}: pin {name!r} of {sym.key} at ({at.x:g}, {at.y:g}) is {d:.2f} mm from the fitted geometry; mark the pins with POINTs on PIN_<name> layers"])
            residual = max(residual, d)
    ext = bbox.extents(moved)
    spill = max(-ext.extmin.x, -ext.extmin.y, ext.extmax.x - sym.width, ext.extmax.y - sym.height, 0.0)
    if spill > SPILL_FAIL * max(sym.width, sym.height):
        raise SymbolOverrideError([f"block {ov.block!r} extends {spill:.1f} mm outside the {sym.width:g} x {sym.height:g} mm box of {sym.key} once its pins are matched, which would overlap its neighbours"])
    if spill > SPILL_WARN:
        warnings.append(f"block {ov.block!r} extends {spill:.1f} mm outside the {sym.width:g} x {sym.height:g} mm box of {sym.key}")
    return Prepared(tuple(moved), matrix, scale, residual, tuple(warnings))


@lru_cache(maxsize=64)
def _prepared(key: str, ov: SymbolOverride, mtime: float) -> Prepared:
    return prepare(REGISTRY[key], ov)


def check_override(key: str, ov: SymbolOverride) -> Prepared:
    if key not in REGISTRY:
        raise SymbolOverrideError([f"unknown symbol {key!r}; keys: {sorted(REGISTRY)}"])
    return _prepared(key, ov, ov.dxf.stat().st_mtime)


def _install(doc: Drawing, key: str, prepared: Prepared) -> None:
    install(doc, [key])
    block = doc.blocks.get(REGISTRY[key].block_name)
    for e in list(block):
        if e.dxftype() != "ATTDEF":
            block.delete_entity(e)
    for e in prepared.entities:
        c = e.copy()
        c.dxf.layer = LAYER
        for attr in ("color", "true_color", "linetype", "lineweight"):
            c.dxf.discard(attr)
        if c.dxftype() == "TEXT":
            c.dxf.text = plain(c.dxf.text)
            c.dxf.style = TEXT_STYLE
        block.add_entity(c)
    refresh_turned(doc, key)


def apply_symbol_overrides(doc: Drawing, profile: CompanyProfile) -> list[str]:
    """Replace the profile's symbols in `doc`; an override that cannot be applied is skipped and reported."""
    warnings: list[str] = []
    for key in sorted(profile.symbol_overrides):
        ov = profile.symbol_overrides[key]
        try:
            prepared = check_override(key, ov)
        except (SymbolOverrideError, OSError, ezdxf.DXFError) as exc:
            warnings.append(f"company symbol for {key} not used, built-in symbol drawn: {exc}")
            continue
        _install(doc, key, prepared)
        warnings.extend(prepared.warnings)
    return warnings

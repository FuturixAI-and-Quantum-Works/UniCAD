"""What each sheet of a set is drawn from that is assumed rather than cited, so the sheet can print it in its ASSUMED block.

An input counts as assumed when the model says so (a design-basis assumption, or a site feature, node, edge or fault-level source that
carries the word 'assumed') or when it drives the sheet and the model holds no reference for it (a fault level without a source; soil
resistivity, earth fault current and lightning protection level, which the model stores without one)."""

import re

from unicad.domain.project import Project
from unicad.drafting.sld.annotate import prose

ASSUMED = re.compile(r"assum", re.IGNORECASE)

BASIS_TOPICS: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"ambient|cell temp|altitude|noct", re.I), frozenset({"sld", "strings", "cable_schedule"})),
    (re.compile(r"vdrop|voltage drop", re.I), frozenset({"sld", "cable_schedule"})),
    (re.compile(r"soil|earth|electrode", re.I), frozenset({"earthing"})),
    (re.compile(r"lightning|\blps\b|\bese\b", re.I), frozenset({"lps"})),
    (re.compile(r"fault|clearing", re.I), frozenset({"sld", "earthing", "cable_schedule"})),
    (re.compile(r"tilt|pitch|azimuth", re.I), frozenset({"layout", "strings"})),
    (re.compile(r"power.?factor", re.I), frozenset({"sld"})),
    (re.compile(r"insulation|cable", re.I), frozenset({"sld", "cable_schedule"})),
)
"""Which sheets a design-basis assumption drives, by what it is about; one about nothing listed here goes on the SLD."""

FAULT_SHEETS = frozenset({"sld", "earthing", "cable_schedule"})
SITE_SHEETS = frozenset({"layout", "earthing", "lps"})
NODE_SHEETS = frozenset({"sld", "bom"})
EDGE_SHEETS = frozenset({"cable_schedule"})


def _sheets_of_basis(text: str) -> frozenset[str]:
    hit = frozenset().union(*(kinds for rx, kinds in BASIS_TOPICS if rx.search(text)))
    return hit or frozenset({"sld"})


def _attr_text(attrs: dict) -> str:
    return " ".join(str(v) for v in attrs.values() if isinstance(v, str))


def _ka(v: float) -> str:
    return f"{v:g} kA"


def entries(project: Project) -> list[tuple[frozenset[str], str]]:
    """(drawing kinds it drives, text) for every assumed input of the project, in model order."""
    db = project.design_basis
    out: list[tuple[frozenset[str], str]] = [(_sheets_of_basis(a), prose(a, db)) for a in db.assumptions]
    out += [(_sheets_of_basis(path), text) for path, text in flagged(project)]
    for f in db.fault_levels:
        level = f"FAULT LEVEL {_ka(f.fault_ka)}" + (f" / {f.clearing_s:g} s" if f.clearing_s else "") + f" AT {f.voltage_v:g} V"
        if f.source is None:
            out.append((FAULT_SHEETS, f"{level}: NO FAULT STUDY OR UTILITY LETTER RECORDED"))
        elif ASSUMED.search(f.source):
            out.append((FAULT_SHEETS, f"{level}: {prose(ASSUMED_PREFIX.sub('', f.source))}"))
    if (geo := project.site.geometry) is not None:
        for feat in geo.features:
            text = " ".join(filter(None, [feat.name or "", _attr_text(feat.attributes)]))
            if ASSUMED.search(text):
                out.append((SITE_SHEETS, f"{feat.kind.value.replace('_', ' ').upper()} {prose(feat.name or feat.id)}"))
    if (eb := db.earthing) is not None and not getattr(eb, "source", None):
        parts = [f"SOIL RESISTIVITY {eb.soil_resistivity_ohm_m:g} OHM-M"]
        if eb.fault_current_ka:
            parts.append(f"EARTH FAULT CURRENT {_ka(eb.fault_current_ka)}" + (f" FOR {eb.fault_clearing_s:g} s" if eb.fault_clearing_s else ""))
        out.append((frozenset({"earthing"}), f"{', '.join(parts)}: NO WENNER TEST OR FAULT STUDY REFERENCE RECORDED"))
    if (lb := db.lps) is not None and not getattr(lb, "source", None):
        out.append((frozenset({"lps"}), f"LIGHTNING PROTECTION LEVEL {lb.level}: NO IS/IEC 62305-2 RISK ASSESSMENT REFERENCE RECORDED"))
    if (lay := project.array_layout) is not None:
        derived = [e for e in lay.equipment if DERIVED.search(e.basis)]
        for e in derived:
            out.append((SITE_SHEETS, f"{prose(e.tag)} POSITION NOT GIVEN, TAKEN AS {_basis(e.basis)}"))
        sy = lay.switchyard
        if sy is not None and DERIVED.search(sy.basis) and not any("switchyard location" in e.basis for e in derived):
            out.append((frozenset({"layout"}), f"SWITCHYARD POSITION NOT GIVEN, TAKEN AS {_basis(sy.basis)}"))
    net = project.network
    for n in net.nodes:
        text = " ".join(filter(None, [n.title or "", _attr_text(n.attributes)]))
        if ASSUMED.search(text):
            out.append((NODE_SHEETS, f"{_tag(n.tag)}: {prose(n.title or _attr_text(n.attributes))}"))
    for e in net.edges:
        if e.remarks and ASSUMED.search(e.remarks):
            a, b = net.node(e.src_node), net.node(e.dst_node)
            out.append((EDGE_SHEETS, f"{_tag(a.tag)} TO {_tag(b.tag)}: {prose(e.remarks)}"))
    return out


def _flags(obj: object) -> list[tuple[str, str]]:
    """(field path, note) for every input `obj` marks as assumed: its `assumed` field, a list of field paths or a path -> note map, or
    items carrying `field`/`path` and `note`/`reason`."""
    raw = getattr(obj, "assumed", None)
    if not raw:
        return []
    if isinstance(raw, dict):
        return [(str(k), str(v or "")) for k, v in raw.items()]
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append((item, ""))
        else:
            path = getattr(item, "field", None) or getattr(item, "path", None)
            if path:
                out.append((str(path), str(getattr(item, "note", None) or getattr(item, "reason", None) or "")))
    return out


def _value(obj: object, path: str) -> object:
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _shown(v: object) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(getattr(v, "value", v))


FIELD_TEXT = {
    "ambient_min_c": ("MINIMUM AMBIENT", " DEG C", 1),
    "ambient_max_c": ("MAXIMUM AMBIENT", " DEG C", 1),
    "altitude_m": ("SITE ALTITUDE", " M", 1),
    "vdrop_dc_max": ("DC VOLTAGE DROP LIMIT", " %", 100),
    "vdrop_ac_max": ("AC VOLTAGE DROP LIMIT", " %", 100),
    "power_factor": ("POWER FACTOR", "", 1),
    "dc_ac_ratio_target": ("DC/AC RATIO TARGET", "", 1),
    "tilt_deg": ("TABLE TILT", " DEG", 1),
    "azimuth_deg": ("TABLE AZIMUTH", " DEG", 1),
}
"""How an assumed field reads on a sheet: name, unit and the factor its stored value is shown with (fractions as percent)."""


def _text(path: str, v: object, note: str) -> str:
    field = path.split(".")[-1]
    name, unit, k = FIELD_TEXT.get(field, (field.replace("_", " ").upper(), "", 1))
    value = f" {float(v) * k:g}{unit}" if isinstance(v, int | float) else (f" {prose(_shown(v))}" if v is not None else "")
    return f"{name}{value} IS ASSUMED" + (f": {prose(note)}" if note else "")


def flagged(project: Project) -> list[tuple[str, str]]:
    """(field path, sheet text) for every design-basis or layout input the model marks as assumed although a value was set."""
    db = project.design_basis
    out = [(path, _text(path, _value(db, path), note)) for path, note in _flags(db)]
    lay = project.array_layout
    if lay is not None:
        tables = list(lay.table_by_id.values())
        for path, note in _flags(lay):
            v = _value(lay, path)
            if v is None and tables:
                v = _value(tables[0], path)
            out.append((path, _text(path, v, note)))
    return out


def layout_assumed(project: Project, field: str) -> bool:
    """Whether the layout (or its design basis) marks `field` as assumed."""
    lay = project.array_layout
    return any(p.split(".")[-1] == field for p, _ in (_flags(lay) if lay is not None else [])) or any(p.split(".")[-1] == field for p, _ in _flags(project.design_basis))


DERIVED = re.compile(r"\bderived\b|free ground|assum", re.IGNORECASE)
"""A layout position the layout placement worked out itself rather than was given."""
_HINT = re.compile(r"\s*\((?:pass|assumed electrical room)[^)]*\)|:\s*(?:set|pass)\b[^)]*", re.IGNORECASE)


def _basis(text: str) -> str:
    """A placement basis as drawing text: without the 'derived:' prefix and the hint naming the tool parameters that would give it."""
    return prose(_HINT.sub("", re.sub(r"^\s*derived:\s*", "", text, flags=re.IGNORECASE)))


ASSUMED_PREFIX = re.compile(r"^\s*assumed\s*[:\-]\s*", re.IGNORECASE)


def _tag(tag: str) -> str:
    return prose(re.sub(r"\{[^}]*\}", "xx", tag))


def sheet_assumptions(project: Project, kind: str) -> tuple[str, ...]:
    """The assumed inputs a sheet of drawing kind `kind` is drawn from; the register (kind 'register') lists every one of the set."""
    return tuple(dict.fromkeys(t for kinds, t in entries(project) if kind == "register" or kind in kinds))


def set_assumptions(project: Project) -> tuple[tuple[frozenset[str], str], ...]:
    """Every assumed input of the project once, with the drawing kinds it drives."""
    out: dict[str, frozenset[str]] = {}
    for kinds, text in entries(project):
        out[text] = out.get(text, frozenset()) | kinds
    return tuple((kinds, text) for text, kinds in out.items())

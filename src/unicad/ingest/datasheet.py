"""Pull labelled numbers out of a manufacturer datasheet PDF and draft a partial spec from them.

This helps an LLM agent read a datasheet; it is not an oracle. draft_spec fills a field only when exactly one value
matches, leaves everything else in `missing` and explains ambiguity in `warnings`. It never defaults or infers a value.

Needs poppler's pdftotext (and pdftoppm + tesseract for ocr=True).
"""

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

from unicad.domain.catalog import InverterSpec, ModuleSpec

UNITS = r"kVA|VA|kW|MW|Wp|Wp|W|mm|kgs|kg|cells?|kV|V|A|Hz|%\s*/\s*[°ºo℃]?\s*[CK]|mV\s*/\s*[°ºo]?\s*[CK]|[°º]\s*C|℃|%|Ω|ohm"
NUM = r"[-−–+]?\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−+]?\d+(?:\.\d+)?"
VALUE_RE = re.compile(rf"(?P<n>{NUM})\s*(?P<u>{UNITS})?(?![A-Za-z])")
RANGE_RE = re.compile(rf"(?P<lo>{NUM})\s*(?P<u1>{UNITS})?\s*(?:-|–|~|to)\s*(?P<hi>{NUM})\s*(?P<u2>{UNITS})?(?![A-Za-z])")
PAREN_UNIT_RE = re.compile(r"\(([%A-Za-zΩ°º℃/]{1,8})\)\s*$")
CELL_SPLIT = re.compile(r"\s{2,}")


@dataclass(frozen=True)
class Candidate:
    """One number (or low-high range) found next to a label.

    label is the column header when one could be matched by position, else the text left of the value.
    row_label is the leftmost text of a table row (a model name), column the position among the row's values.
    """

    label: str
    value: float | tuple[float, float] | str
    unit: str | None
    page: int
    row_label: str | None = None
    column: int | None = None
    condition: str | None = None
    line: str = ""
    origin: Literal["text", "ocr"] = "text"


@dataclass
class Extraction:
    path: Path
    pages: list[str]
    candidates: list[Candidate] = field(default_factory=list)
    image_only_pages: list[int] = field(default_factory=list)


class Draft(NamedTuple):
    fields: dict
    missing: list[str]
    warnings: list[str]


def _norm_unit(u: str | None) -> str | None:
    if not u:
        return None
    u = re.sub(r"\s+", "", u).replace("º", "°").replace("℃", "°C").replace("Wp", "W")
    return {"%/C": "%/°C", "%/K": "%/°C", "%/°K": "%/°C", "kgs": "kg", "cell": "cells"}.get(u, u)


def _num(s: str) -> float:
    return float(s.replace(",", "").replace("−", "-").replace("–", "-").replace(" ", ""))


def _cells(line: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for part in CELL_SPLIT.split(line.strip("\n")):
        start = line.index(part, pos) if part else pos
        pos = start + len(part)
        if part.strip():
            out.append((start, part.strip()))
    return out


def _values(cell: str) -> list[tuple[float | tuple[float, float], str | None]]:
    r = RANGE_RE.search(cell)
    if r and len(cell) <= 40 and not re.search(r"[A-Za-z]{4,}", RANGE_RE.sub("", cell, 1)):
        return [((_num(r["lo"]), _num(r["hi"])), _norm_unit(r["u2"] or r["u1"]))]
    found = [(_num(m["n"]), _norm_unit(m["u"])) for m in VALUE_RE.finditer(re.sub(r"\([^)]*\d[^)]*\)", "", cell))]
    return [v for k, v in enumerate(found) if k == 0 or v[1] is not None]


def _is_label(cell: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3,}", re.sub(rf"(?<![A-Za-z])(?:{UNITS})(?![A-Za-z])", "", cell)))


def _page_text(path: Path, page: int) -> str:
    return subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(path), "-"], capture_output=True, text=True, check=True).stdout


def _page_count(path: Path) -> int:
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", info)[1])


def _ocr_page(path: Path, page: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "300", "-png", "-f", str(page), "-l", str(page), str(path), f"{tmp}/p"], check=True)
        image = next(Path(tmp).glob("p*.png"))
        return subprocess.run(["tesseract", str(image), "-", "--psm", "6"], capture_output=True, text=True, check=True).stdout


FIELD_WORDS = re.compile(r"\b(Pmax|P\s?max|Vmp|Vmpp|Imp|Impp|Isc|Voc|Eff)", re.I)
CONDITION_WORDS = re.compile(r"^(STC|NOCT|NMOT|BSTC)$", re.I)


@dataclass
class _Header:
    fields: list[tuple[int, str]]
    conditions: list[tuple[int, str]]

    def columns(self) -> list[tuple[str, str | None]]:
        """(header, condition) per value column, left to right.

        Sub-header words (STC NOCT STC NOCT ...) are split into groups, a new group starting when a word repeats, and handed to
        the fields in order; fields past the last group have one column.
        """
        groups: list[list[str]] = []
        for _, word in sorted(self.conditions):
            if not groups or word.upper() in groups[-1]:
                groups.append([])
            groups[-1].append(word.upper())
        cols: list[tuple[str, str | None]] = []
        for k, (_, name) in enumerate(sorted(self.fields)):
            if k < len(groups):
                cols.extend((name, cond) for cond in groups[k])
            else:
                cols.append((name, None))
        return cols


def _header_at(lines: list[str], i: int) -> tuple[_Header | None, int]:
    """A header starts at a line with two or more field names; following name-only and STC/NOCT lines extend it."""
    cells = _cells(lines[i])
    if sum(bool(FIELD_WORDS.search(c)) for _, c in cells) < 2 or any(re.fullmatch(NUM, c) for _, c in cells):
        return None, i
    fields, conds, j = list(cells), [], i + 1
    while j < len(lines):
        nxt = _cells(lines[j])
        words = [(x + o, w) for x, c in nxt for o, w in _words(c)]
        if nxt and all(CONDITION_WORDS.match(w) for _, w in words):
            conds.extend(words)
        elif nxt and any(FIELD_WORDS.search(c) for _, c in nxt) and not any(re.search(r"\d{2}", c) for _, c in nxt):
            fields.extend(nxt)
        elif nxt:
            break
        j += 1
    return _Header(fields, conds), j


def _words(cell: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group()) for m in re.finditer(r"\S+", cell)]


def _lines_to_candidates(text: str, page: int, origin: Literal["text", "ocr"]) -> list[Candidate]:
    lines = text.splitlines()
    out: list[Candidate] = []
    header: _Header | None = None
    i = 0
    while i < len(lines):
        found, after = _header_at(lines, i)
        if found:
            header, i = found, after
            continue
        line, i = lines[i], i + 1
        cells = _cells(line)
        if not cells:
            continue
        numeric = [(x, c) for x, c in cells if not _is_label(c) and _values(c)]
        columns = header.columns() if header else []
        table_row = bool(header) and len(numeric) >= 3 and len(numeric) == len(columns) and _is_label(cells[0][1])
        if header and not table_row and len(numeric) < 3:
            header = header if not _is_label(cells[0][1]) or len(cells) < 2 else None
        label, row_label, col = "", None, 0
        for k, (x, cell) in enumerate(cells):
            if _is_label(cell):
                if k and _is_label(cells[k - 1][1]) and label == cells[k - 1][1]:
                    out.append(Candidate(label, cell, None, page, None, 0, None, line.strip(), origin))
                label, col = cell, 0
                row_label = cell if k == 0 else None
                continue
            for value, unit in _values(cell):
                head, cond = label, None
                if table_row:
                    head, cond = columns[col]
                    if unit is None and (m := PAREN_UNIT_RE.search(head)):
                        unit = _norm_unit(m[1])
                elif unit is None and (m := PAREN_UNIT_RE.search(label)):
                    unit = _norm_unit(m[1])
                if head:
                    out.append(Candidate(head, value, unit, page, row_label if row_label != head else None, col, cond, line.strip(), origin))
                col += 1
    return out


def extract_text_and_tables(pdf: str | Path, *, ocr: bool = False) -> Extraction:
    """Per-page layout text plus every label/number pair found on it.

    Pages with no text layer are listed in image_only_pages; with ocr=True they are read with tesseract (origin="ocr",
    expect misread digits and check each against the page image).
    """
    path = Path(pdf)
    if shutil.which("pdftotext") is None:
        raise RuntimeError("pdftotext (poppler) is required")
    ex = Extraction(path=path, pages=[])
    for page in range(1, _page_count(path) + 1):
        text = _page_text(path, page)
        origin: Literal["text", "ocr"] = "text"
        if len(text.strip()) < 40:
            ex.image_only_pages.append(page)
            if ocr and shutil.which("tesseract") and shutil.which("pdftoppm"):
                text, origin = _ocr_page(path, page), "ocr"
        ex.pages.append(text)
        ex.candidates.extend(_lines_to_candidates(text, page, origin))
    return ex


@dataclass(frozen=True)
class FieldDef:
    name: str
    label: re.Pattern
    units: tuple[str | None, ...]
    kind: Literal["scalar", "low", "high", "first", "second", "fraction"] = "scalar"
    scale: dict[str, float] = field(default_factory=dict)
    avoid: re.Pattern | None = None


def _f(name, label, units, kind="scalar", scale=None, avoid=None) -> FieldDef:
    return FieldDef(name, re.compile(label, re.I), tuple(units), kind, scale or {}, re.compile(avoid, re.I) if avoid else None)


MODULE_FIELDS = [
    _f("pmax", r"\b(p\s?max|maximum power|rated (maximum )?power|peak power|nominal (max\w* )?power)\b", ["W"], avoid=r"tolerance|bifacial|gain|output \(w\) *$"),
    _f("voc", r"\b(voc|open[- ]circuit voltage)\b", ["V"], avoid=r"temperature|coef|β|ß"),
    _f("isc", r"\b(isc|short[- ]circuit current)\b", ["A"], avoid=r"temperature|coef|α"),
    _f("vmp", r"\b(vmp|vmpp|maximum power voltage|voltage at (maximum|pmax))\b", ["V"]),
    _f("imp", r"\b(imp|impp|maximum power current|current at (maximum|pmax))\b", ["A"]),
    _f("temp_coeff_voc", r"temperature coef.{0,12}(voltage|voc)|\bβ\b|\bß\b|coef\w* of voc", ["%/°C", None]),
    _f("temp_coeff_isc", r"temperature coef.{0,12}(current|isc)|\bα\b|coef\w* of isc", ["%/°C", None]),
    _f("temp_coeff_pmax", r"temperature coef.{0,12}(power|pm\w*)|\bγ\b|coef\w* of p", ["%/°C", None]),
    _f("noct_c", r"\b(noct|nmot)\b", ["°C", None]),
    _f("max_system_voltage", r"max\w*\.?\s*system voltage|system voltage", ["V"]),
    _f("max_series_fuse", r"series fuse|max\w*\.?\s*fuse", ["A"]),
    _f("length", r"length\s*x\s*width|dimension", ["mm"], kind="first"),
    _f("width", r"length\s*x\s*width|dimension", ["mm"], kind="second"),
    _f("cells", r"cells per module|number of cells|solar cells|no\.? of cells", ["cells", None]),
    _f("bifaciality", r"bifaciality", ["%", None], kind="fraction", avoid=r"gain"),
]
INVERTER_FIELDS = [
    _f("ac_rated_kva", r"(rated|nominal) (ac )?(output |active )?power|rated ac output power", ["kW", "W", "kVA", "VA"], scale={"W": 0.001, "VA": 0.001}, avoid=r"apparent"),
    _f("ac_max_kva", r"max\w*\.?\s*(ac )?(output )?apparent power", ["kVA", "VA"], scale={"VA": 0.001}),
    _f("ac_voltage", r"(rated|nominal) (ac )?(output |grid )?voltage", ["V"]),
    _f("dc_max_voltage", r"max\w*\.?\s*(pv )?(dc )?input voltage", ["V"]),
    _f("mppt_min", r"mpp\w* (operating )?voltage range|mppt (operating )?(voltage )?range", ["V"], kind="low"),
    _f("mppt_max", r"mpp\w* (operating )?voltage range|mppt (operating )?(voltage )?range", ["V"], kind="high"),
    _f("start_voltage", r"start[- ]?up (input )?voltage|start voltage", ["V"]),
    _f("mppt_count", r"(number|no\.?) of (independent )?mpp\w*( inputs| trackers)?|number of mppt", [None]),
    _f("inputs_per_mppt", r"strings per mppt|input number per mpp|inputs? per mppt", [None]),
    _f("max_input_current_per_mppt", r"max\w*\.?\s*(input |dc )?current per mppt", ["A"]),
    _f("max_short_circuit_current_per_mppt", r"short[- ]circuit current per mppt", ["A"]),
    _f("max_dc_input_current", r"max\w*\.?\s*(pv |dc )?input current(?! per)", ["A"], avoid=r"per (mppt|input)|connector"),
    _f("max_dc_short_circuit_current", r"max\w*\.?\s*dc short[- ]circuit current(?! per)", ["A"], avoid=r"per mppt"),
    _f("max_efficiency", r"max\w*\.?\s*efficiency", ["%"], kind="fraction"),
]
DEFS = {"module": (ModuleSpec, MODULE_FIELDS), "inverter": (InverterSpec, INVERTER_FIELDS)}
TECH_WORDS = {"topcon": "topcon", "n-type": None, "perc": "mono_perc", "hjt": "hjt", "heterojunction": "hjt", "ibc": "ibc"}
IDENTITY = {"id", "source", "symbol", "kind"}


def _extract(cand: Candidate, fd: FieldDef) -> float | None:
    unit = cand.unit
    if isinstance(cand.value, str):
        return None
    if unit not in fd.units and not (unit is None and None in fd.units):
        return None
    if isinstance(cand.value, tuple):
        if fd.kind == "low":
            v = cand.value[0]
        elif fd.kind == "high":
            v = cand.value[1]
        else:
            return None
    else:
        if fd.kind in ("low", "high"):
            return None
        v = cand.value
    v *= fd.scale.get(unit or "", 1.0)
    if fd.kind == "fraction":
        if v > 1:
            v /= 100
    return v


def draft_spec(kind: str, candidates: list[Candidate], *, row: str | None = None, column: int | None = None) -> Draft:
    """Map candidates to ModuleSpec / InverterSpec field names.

    row narrows to table rows whose label contains the text (one model of a multi-model sheet), column to one position of
    label-first tables. NOCT-condition values are ignored for STC fields. Returns (fields, missing, warnings); fields holds only
    values that were unambiguous, in the spec's own units.
    """
    if kind not in DEFS:
        raise ValueError(f"unsupported kind {kind!r}: expected one of {sorted(DEFS)}")
    spec_cls, defs = DEFS[kind]
    pool = [c for c in candidates if row is None or c.row_label is None or row.lower() in c.row_label.lower()]
    if column is not None:
        pool = [c for c in pool if c.column == column]
    fields: dict = {}
    warnings: list[str] = []
    for fd in defs:
        hits: dict[float, list[Candidate]] = {}
        for c in pool:
            if c.condition and c.condition != "STC" and fd.name not in ("noct_c",):
                continue
            if not fd.label.search(c.label) or (fd.avoid and fd.avoid.search(c.label)):
                continue
            v = _extract(c, fd)
            if v is not None:
                hits.setdefault(round(v, 6), []).append(c)
        if fd.kind in ("first", "second"):
            hits = _dimension_hits(pool, fd)
        if len(hits) == 1:
            ((v, cs),) = hits.items()
            fields[fd.name] = int(v) if fd.name in ("cells", "mppt_count", "inputs_per_mppt") and float(v).is_integer() else v
            if any(c.origin == "ocr" for c in cs):
                warnings.append(f"{fd.name}={v} was read by OCR (page {cs[0].page}); check it against the page image")
        elif len(hits) > 1:
            shown = ", ".join(f"{v:g} (p.{cs[0].page}, {cs[0].label!r})" for v, cs in sorted(hits.items())[:8])
            warnings.append(f"{fd.name} is ambiguous, {len(hits)} distinct values: {shown}. Pass row= or column= or choose by hand")
    required = [n for n, f in spec_cls.model_fields.items() if f.is_required() and n not in IDENTITY]
    if kind == "inverter":
        required = [n for n in required if n != "max_input_current_per_mppt"]
        if "max_input_current_per_mppt" not in fields and "max_dc_input_current" not in fields:
            required.append("max_input_current_per_mppt or max_dc_input_current")
    missing = [n for n in required if n not in fields and n != "max_input_current_per_mppt or max_dc_input_current"] + [
        n for n in required if n == "max_input_current_per_mppt or max_dc_input_current"
    ]
    return Draft(fields, missing, warnings + _tech_hint(kind, candidates, fields))


def _dimension_hits(pool: list[Candidate], fd: FieldDef) -> dict[float, list[Candidate]]:
    """Dimensions arrive as consecutive mm candidates on one line: 'Length x Width x Thickness  2278 mm (L) x 1134 mm (W) x 33 mm (T)'."""
    by_line: dict[tuple[int, str], list[Candidate]] = {}
    for c in pool:
        if fd.label.search(c.label) and c.unit == "mm" and not isinstance(c.value, tuple):
            by_line.setdefault((c.page, c.line), []).append(c)
    hits: dict[float, list[Candidate]] = {}
    idx = 0 if fd.kind == "first" else 1
    for cs in by_line.values():
        if len(cs) >= 2:
            hits.setdefault(round(cs[idx].value, 6), []).append(cs[idx])
    return hits


def _tech_hint(kind: str, candidates: list[Candidate], fields: dict) -> list[str]:
    if kind != "module":
        return []
    text = " ".join(c.value.lower() for c in candidates if isinstance(c.value, str))
    found = sorted({w for w in ("topcon", "perc", "hjt", "heterojunction", "ibc") if w in text})
    return [f"tech not set; the text mentions {', '.join(found)}. Confirm against the datasheet before choosing a CellTech"] if found else []

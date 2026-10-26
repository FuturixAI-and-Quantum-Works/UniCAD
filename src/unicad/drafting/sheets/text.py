from dataclasses import dataclass

from unicad.drafting.sheets.textmetrics import ADVANCE

# LibreCAD's text boxes measured 1.07 to 1.12 wider than the summed .lff advances.
WIDTH_SAFETY = 1.12
LINE_PITCH = 1.5
DESCENDER = 0.3
ELLIPSIS = "..."

_CONDENSED = 0.85
_HEIGHT_STEPS = (1.0, 0.9, 0.8, 0.72, 0.65)


def text_width(text: str, height: float, width_factor: float = 1.0) -> float:
    return sum(ADVANCE.get(c, 1.0) for c in text) * height * width_factor * WIDTH_SAFETY


def block_height(lines: int, height: float) -> float:
    if lines <= 0:
        return 0.0
    return height * (1 + DESCENDER) + (lines - 1) * height * LINE_PITCH


def _split_long_word(word: str, max_w: float, h: float, wf: float) -> list[str]:
    parts: list[str] = []
    current = ""
    for ch in word:
        if current and text_width(current + ch, h, wf) > max_w:
            parts.append(current)
            current = ch
        else:
            current += ch
    parts.append(current)
    return parts


def wrap(text: str, max_w: float, h: float, wf: float = 1.0) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            pieces = (
                _split_long_word(word, max_w, h, wf)
                if text_width(word, h, wf) > max_w
                else [word]
            )
            for piece in pieces:
                trial = f"{current} {piece}" if current else piece
                if current and text_width(trial, h, wf) > max_w:
                    lines.append(current)
                    current = piece
                else:
                    current = trial
        lines.append(current)
    return lines or [""]


def _ellipsize(line: str, max_w: float, h: float, wf: float) -> str:
    line = line.rstrip()
    while line and text_width(line + ELLIPSIS, h, wf) > max_w:
        line = line[:-1]
    return line.rstrip() + ELLIPSIS


@dataclass(frozen=True)
class Fitted:
    lines: tuple[str, ...]
    height: float
    width_factor: float
    truncated: bool

    @property
    def block_height(self) -> float:
        return block_height(len(self.lines), self.height)


def fit(
    text: str,
    max_w: float,
    h: float,
    *,
    max_lines: int,
    max_h: float | None = None,
    min_h: float | None = None,
) -> Fitted:
    """Wrap, then condense, then shrink, then truncate so the text fits max_w x max_lines
    (and max_h of vertical room when given). Deterministic for a given input."""
    min_h = min_h if min_h is not None else h * _HEIGHT_STEPS[-1]
    candidates: list[tuple[float, float]] = []
    for step in _HEIGHT_STEPS:
        hh = h * step
        if hh < min_h - 1e-9:
            break
        candidates.append((hh, 1.0))
        candidates.append((hh, _CONDENSED))
    if not candidates:
        candidates = [(min_h, _CONDENSED)]
    for hh, wf in candidates:
        lines = wrap(text, max_w, hh, wf)
        if len(lines) <= max_lines and (max_h is None or block_height(len(lines), hh) <= max_h):
            return Fitted(tuple(lines), hh, wf, False)
    hh, wf = candidates[-1]
    lines = wrap(text, max_w, hh, wf)
    room = max_lines
    if max_h is not None:
        while room > 1 and block_height(room, hh) > max_h:
            room -= 1
    kept = lines[:room]
    kept[-1] = _ellipsize(kept[-1], max_w, hh, wf)
    return Fitted(tuple(kept), hh, wf, True)

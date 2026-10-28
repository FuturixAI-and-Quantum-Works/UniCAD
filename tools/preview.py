"""Render a DXF to PNG with ezdxf, drawing TEXT as LibreCAD's `standard` .lff glyph strokes.

LibreCAD's own dxf2png draws text as boxes and dxf2pdf emits a page 1000x oversized in the
AppImage this project pins, so this is the visual check for sheet layout.
"""

import math
import os
import re
import sys
from pathlib import Path

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration

CAP = 9.0
DEFAULT_LFF = Path(os.environ.get("LIBRECAD", "/tmp/opencode/proto/squashfs-root/AppRun")).parent / "usr" / "share" / "librecad" / "fonts" / "standard.lff"


def _arc_xs(x0: float, y0: float, x1: float, y1: float, bulge: float) -> list[float]:
    """Sample x along the arc given by a DXF-style bulge between two points."""
    chord = math.hypot(x1 - x0, y1 - y0)
    if chord == 0:
        return []
    theta = 4 * math.atan(abs(bulge))
    radius = chord / (2 * math.sin(theta / 2))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    d = radius * math.cos(theta / 2)
    nx, ny = -(y1 - y0) / chord, (x1 - x0) / chord
    sign = 1 if bulge > 0 else -1
    cx, cy = mx + sign * d * nx, my + sign * d * ny
    a0 = math.atan2(y0 - cy, x0 - cx)
    return [cx + radius * math.cos(a0 + sign * theta * t / 32) for t in range(33)]


class Font:
    def __init__(self, path: Path):
        self.glyphs: dict[str, list[list[tuple[float, float, float]]]] = {}
        self.letter_spacing = 3.0
        self.word_spacing = 6.75
        current = None
        refs: dict[str, list[str]] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.startswith("#"):
                m = re.match(r"#\s*(LetterSpacing|WordSpacing):\s*([\d.]+)", raw)
                if m:
                    setattr(self, "letter_spacing" if m.group(1) == "LetterSpacing" else "word_spacing", float(m.group(2)))
                continue
            m = re.match(r"\[([0-9a-fA-F]+)\]", raw)
            if m:
                current = chr(int(m.group(1), 16))
                self.glyphs[current] = []
                refs[current] = []
            elif current and raw.strip():
                if raw.startswith("C"):
                    refs[current].append(chr(int(raw[1:].strip(), 16)))
                    continue
                pts: list[tuple[float, float, float]] = []
                for tok in raw.strip().split(";"):
                    parts = tok.split(",")
                    bulge = float(parts[2][1:]) if len(parts) > 2 and parts[2].startswith("A") else 0.0
                    pts.append((float(parts[0]), float(parts[1]), bulge))
                self.glyphs[current].append(pts)
        for ch, rs in refs.items():
            for r in rs:
                self.glyphs[ch].extend(self.glyphs.get(r, []))

    def extent_x(self, ch: str) -> float:
        xs = [0.0]
        for stroke in self.glyphs.get(ch, []):
            for (x0, y0, _), (x1, y1, bulge) in zip(stroke, stroke[1:]):
                xs.append(x1)
                if bulge:
                    xs.extend(_arc_xs(x0, y0, x1, y1, bulge))
            xs.append(stroke[0][0])
        return max(xs)

    def advance(self, ch: str) -> float:
        if ch == " ":
            return self.word_spacing
        if not self.glyphs.get(ch):
            return 9.0
        return self.extent_x(ch) + self.letter_spacing


def expand_text(doc, font: Font) -> None:
    msp = doc.modelspace()
    for t in list(msp.query("TEXT")):
        h = t.dxf.height
        wf = t.dxf.get("width", 1.0)
        x, y = t.dxf.insert.x, t.dxf.insert.y
        layer = t.dxf.layer
        sx, sy = h / CAP * wf, h / CAP
        for ch in t.dxf.text:
            for stroke in font.glyphs.get(ch, []):
                pts = []
                for i, (px, py, _) in enumerate(stroke):
                    nxt = stroke[i + 1][2] if i + 1 < len(stroke) else 0.0
                    pts.append((x + px * sx, y + py * sy, 0, 0, nxt))
                msp.add_lwpolyline(pts, format="xyseb", dxfattribs={"layer": layer})
            x += font.advance(ch) * sx
        msp.delete_entity(t)


def render(dxf: Path, png: Path, dpi: int = 100, lff: Path = DEFAULT_LFF) -> None:
    doc = ezdxf.readfile(dxf)
    expand_text(doc, Font(lff))
    backend = pymupdf.PyMuPdfBackend()
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(doc.modelspace())
    png.write_bytes(backend.get_pixmap_bytes(layout.Page(0, 0), fmt="png", dpi=dpi))


if __name__ == "__main__":
    render(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 100)

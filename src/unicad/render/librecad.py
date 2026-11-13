"""LibreCAD 2.2.1.6 console renderer (dxf2pdf, dxf2svg) with guards for its known failure modes.

LibreCAD hangs on malformed DXF, exits 0 when the input file is missing, and sizes the page
1000x too large when `$INSUNITS` is not millimetres. This module rejects those inputs before
launching it, kills runaway processes, and checks the output it gets back.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ezdxf
import pymupdf
from ezdxf.lldxf.const import DXFError

from .backend import (
    PT_PER_MM,
    BackendUnavailable,
    InvalidDrawing,
    PageInfo,
    PaperSpec,
    RenderFailed,
    RenderTimeout,
    ScaleMode,
    SetResult,
    Sheet,
    merge_pdfs,
    paper_size_mm,
)

DEFAULT_BINARY = Path("/tmp/opencode/proto/squashfs-root/AppRun")
PAGE_TOLERANCE_PT = 2.0
INSUNITS_MM = 4
CRASH_ENTITIES = {"IMAGE": "LibreCAD 2.2.1.6 segfaults in dxf2pdf on IMAGE entities"}


def locate_librecad(binary: str | os.PathLike[str] | None = None) -> Path:
    explicit = binary or os.environ.get("LIBRECAD")
    if explicit:
        found = Path(explicit)
        if found.is_file() and os.access(found, os.X_OK):
            return found
        on_path = shutil.which(str(explicit))
        if on_path:
            return Path(on_path)
        source = "binary argument" if binary else "$LIBRECAD"
        raise BackendUnavailable(f"LibreCAD binary from {source} not found or not executable: {explicit}")
    if DEFAULT_BINARY.is_file():
        return DEFAULT_BINARY
    on_path = shutil.which("librecad")
    if on_path:
        return Path(on_path)
    raise BackendUnavailable("LibreCAD not found: set $LIBRECAD to the AppRun/librecad binary or put `librecad` on PATH")


def check_dxf(dxf: Path, *, strict_units: bool = True) -> None:
    if not dxf.is_file():
        raise InvalidDrawing(f"DXF not found: {dxf}")
    try:
        doc = ezdxf.readfile(dxf)
    except (DXFError, OSError, UnicodeDecodeError) as exc:
        raise InvalidDrawing(f"{dxf.name} is not a readable DXF ({exc}); LibreCAD would hang on it") from exc
    present = {e.dxftype() for block in doc.blocks for e in block}
    for kind, why in CRASH_ENTITIES.items():
        if kind in present:
            raise InvalidDrawing(f"{dxf.name} contains {kind}: {why}")
    if strict_units:
        units = doc.header.get("$INSUNITS", 0)
        if units != INSUNITS_MM:
            raise InvalidDrawing(
                f"{dxf.name} has $INSUNITS={units}, expected {INSUNITS_MM} (mm); LibreCAD rescales the page by the "
                "unit conversion (1000x for metres, which is the ezdxf default)"
            )


class LibreCadRenderer:
    def __init__(
        self,
        binary: str | os.PathLike[str] | None = None,
        *,
        timeout: float = 120.0,
        strict_units: bool = True,
        workers: int = 4,
    ) -> None:
        self._binary = binary
        self.timeout = timeout
        self.strict_units = strict_units
        self.workers = workers

    def _command_prefix(self) -> tuple[list[str], dict[str, str]]:
        binary = locate_librecad(self._binary)
        env = dict(os.environ)
        # Qt's offscreen platform needs no display (OpenCAD launches UniCAD this way), and only X11 desktops have DISPLAY
        if env.get("QT_QPA_PLATFORM", "").strip() == "offscreen" or sys.platform in ("win32", "darwin"):
            return [str(binary)], env
        if env.get("DISPLAY"):
            return [str(binary)], env
        xvfb = shutil.which("xvfb-run")
        if xvfb:
            return [xvfb, "-a", str(binary)], env
        raise BackendUnavailable(
            "LibreCAD needs an X display: DISPLAY is unset and xvfb-run is not installed "
            "(set DISPLAY, e.g. DISPLAY=:0, or install xvfb)"
        )

    def _run(self, args: Sequence[str], output: Path) -> None:
        prefix, env = self._command_prefix()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        proc = subprocess.Popen(
            [*prefix, *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            _, stderr = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise RenderTimeout(f"LibreCAD did not finish within {self.timeout:g} s and was killed") from exc
        detail = _tail(stderr.decode(errors="replace"))
        if proc.returncode != 0:
            hint = " (is DISPLAY reachable?)" if "display" in detail.lower() or "xcb" in detail.lower() else ""
            raise RenderFailed(f"LibreCAD exited with {proc.returncode}{hint}: {detail}")
        if not output.is_file() or output.stat().st_size == 0:
            raise RenderFailed(f"LibreCAD produced no {output.name}: {detail}")

    def to_pdf(self, dxf: Path, pdf: Path, paper: PaperSpec, scale_mode: ScaleMode = ScaleMode.ACTUAL) -> Path:
        dxf, pdf = Path(dxf).resolve(), Path(pdf).resolve()
        w, h = paper_size_mm(paper)
        self._command_prefix()
        check_dxf(dxf, strict_units=self.strict_units)
        flags = {
            ScaleMode.ACTUAL: ["-s", "1"],
            ScaleMode.CENTERED: ["-s", "1", "-c"],
            ScaleMode.FIT: ["-a"],
        }[ScaleMode(scale_mode)]
        self._run(["dxf2pdf", *flags, "-p", f"{w:g}x{h:g}", "-o", str(pdf), str(dxf)], pdf)
        _check_page(pdf, w, h)
        return pdf

    def to_svg(self, dxf: Path, svg: Path) -> Path:
        dxf, svg = Path(dxf).resolve(), Path(svg).resolve()
        self._command_prefix()
        check_dxf(dxf, strict_units=self.strict_units)
        self._run(["dxf2svg", "-o", str(svg), str(dxf)], svg)
        return svg

    def to_png(
        self, dxf: Path, png: Path, paper: PaperSpec, px: int = 3600, scale_mode: ScaleMode = ScaleMode.ACTUAL
    ) -> Path:
        from .preview import pdf_to_png

        png = Path(png)
        with tempfile.TemporaryDirectory(prefix="unicad-png-") as tmp:
            pdf = self.to_pdf(dxf, Path(tmp) / "page.pdf", paper, scale_mode)
            png.parent.mkdir(parents=True, exist_ok=True)
            png.write_bytes(pdf_to_png(pdf, px=px))
        return png

    def build_set(
        self,
        sheets: Sequence[Sheet],
        out_pdf: Path,
        scale_mode: ScaleMode = ScaleMode.ACTUAL,
        work_dir: Path | None = None,
    ) -> SetResult:
        if not sheets:
            raise ValueError("a sheet set needs at least one sheet")
        out_pdf = Path(out_pdf)
        with tempfile.TemporaryDirectory(prefix="unicad-set-") as scratch:
            work = Path(work_dir) if work_dir else Path(scratch)
            work.mkdir(parents=True, exist_ok=True)
            pdfs = [work / f"{i:03d}-{Path(s.dxf).stem}.pdf" for i, s in enumerate(sheets, 1)]
            with ThreadPoolExecutor(max_workers=max(1, min(self.workers, len(sheets)))) as pool:
                list(pool.map(lambda sp: self.to_pdf(sp[0].dxf, sp[1], sp[0].paper, scale_mode), zip(sheets, pdfs)))
            merge_pdfs(pdfs, [s.title for s in sheets], out_pdf)
            pages = tuple(
                PageInfo(sheet=s, pdf=p, width_mm=paper_size_mm(s.paper)[0], height_mm=paper_size_mm(s.paper)[1])
                for s, p in zip(sheets, pdfs)
            )
        return SetResult(pdf=out_pdf, pages=pages)


def _check_page(pdf: Path, w_mm: float, h_mm: float) -> None:
    try:
        with pymupdf.open(pdf) as doc:
            if len(doc) != 1:
                raise RenderFailed(f"{pdf.name} has {len(doc)} pages, expected 1")
            rect = doc[0].rect
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise RenderFailed(f"{pdf.name} is not a readable PDF: {exc}") from exc
    want_w, want_h = w_mm * PT_PER_MM, h_mm * PT_PER_MM
    if abs(rect.width - want_w) > PAGE_TOLERANCE_PT or abs(rect.height - want_h) > PAGE_TOLERANCE_PT:
        raise RenderFailed(
            f"{pdf.name} page is {rect.width:.1f}x{rect.height:.1f} pt, expected {want_w:.1f}x{want_h:.1f} pt"
        )


def _tail(text: str, lines: int = 4) -> str:
    noise = ("qt.qpa", "LC_CrashHandler", "RS_DEBUG", "/qm", "/translations")
    kept = [ln for ln in text.splitlines() if ln.strip() and not any(n in ln for n in noise)]
    return " | ".join(kept[-lines:])

"""DXF to DWG through an external converter, verified by converting back and counting entities.

Converters, in order of preference:
  * ODA File Converter via ezdxf's odafc add-on (free download, licence reviewed in docs/compat.md)
  * LibreDWG `dxf2dwg`/`dwg2dxf` (GPL, experimental, writes R12/R14/R2000 only)

A DWG is only reported as good when reading it back gives the same number of entities of
every type as the DXF it came from.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import ezdxf
from ezdxf import recover
from ezdxf.document import Drawing

from .backend import RenderError, RenderFailed, RenderTimeout

DwgVersion = Literal["R12", "R14", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"]

DEFAULT_LIBREDWG_BIN = Path("/tmp/opencode/libredwg/install/bin")
_LIBREDWG_WRITES: frozenset[str] = frozenset({"R12", "R14", "R2000"})


class DwgUnavailable(RenderError):
    """No DWG converter is installed, or the requested version is outside what it can write."""


class DwgMismatch(RenderError):
    """The DWG reads back with different entity counts than the DXF it was made from."""


@dataclass(frozen=True)
class DwgResult:
    dwg: Path
    converter: str
    version: str
    source_counts: dict[str, int]
    readback_counts: dict[str, int]

    @property
    def mismatches(self) -> dict[str, tuple[int, int]]:
        keys = self.source_counts.keys() | self.readback_counts.keys()
        return {
            k: (self.source_counts.get(k, 0), self.readback_counts.get(k, 0))
            for k in sorted(keys)
            if self.source_counts.get(k, 0) != self.readback_counts.get(k, 0)
        }

    @property
    def verified(self) -> bool:
        return not self.mismatches


def entity_counts(doc: Drawing) -> dict[str, int]:
    """Entity totals by type over modelspace, paperspace layouts and named blocks."""
    counts: Counter[str] = Counter()
    for block in doc.blocks:
        name = block.name
        if name.startswith("*") and not name.lower().startswith(("*model_space", "*paper_space")):
            continue
        counts.update(e.dxftype() for e in block)
    return dict(counts)


def _libredwg_tool(name: str) -> Path | None:
    env_dir = os.environ.get("LIBREDWG_BIN")
    for directory in filter(None, [env_dir, str(DEFAULT_LIBREDWG_BIN)]):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _oda_available() -> bool:
    try:
        from ezdxf.addons import odafc

        return bool(odafc.is_installed())
    except Exception:
        return False


def available_converter() -> str | None:
    if _oda_available():
        return "oda"
    if _libredwg_tool("dxf2dwg") and _libredwg_tool("dwg2dxf"):
        return "libredwg"
    return None


def unavailable_reason() -> str | None:
    if available_converter():
        return None
    return (
        "no DWG converter installed: ODA File Converter not configured in ezdxf's odafc add-on, and LibreDWG "
        "dxf2dwg/dwg2dxf not found (set LIBREDWG_BIN to the directory holding them)"
    )


class DwgConverter:
    def __init__(self, converter: Literal["oda", "libredwg"] | None = None, *, timeout: float = 120.0) -> None:
        self.timeout = timeout
        self._requested = converter

    @property
    def name(self) -> str:
        chosen = self._requested or available_converter()
        if chosen is None:
            raise DwgUnavailable(unavailable_reason() or "no DWG converter")
        if chosen == "oda" and not _oda_available():
            raise DwgUnavailable("ODA File Converter requested but ezdxf's odafc add-on cannot find it")
        if chosen == "libredwg" and not (_libredwg_tool("dxf2dwg") and _libredwg_tool("dwg2dxf")):
            raise DwgUnavailable("LibreDWG requested but dxf2dwg/dwg2dxf were not found (set LIBREDWG_BIN)")
        return chosen

    def to_dwg(
        self, dxf: Path, dwg: Path, version: DwgVersion = "R2000", *, verify: bool = True, strict: bool = True
    ) -> DwgResult:
        dxf, dwg = Path(dxf).resolve(), Path(dwg).resolve()
        converter = self.name
        if not dxf.is_file():
            raise RenderFailed(f"DXF not found: {dxf}")
        dwg.parent.mkdir(parents=True, exist_ok=True)
        dwg.unlink(missing_ok=True)
        if converter == "libredwg":
            self._libredwg_write(dxf, dwg, version)
        else:
            self._oda_write(dxf, dwg, version)
        if not dwg.is_file() or dwg.stat().st_size == 0:
            raise RenderFailed(f"{converter} produced no {dwg.name}")
        source = entity_counts(ezdxf.readfile(dxf))
        back = entity_counts(self.from_dwg(dwg)) if verify else source
        result = DwgResult(dwg, converter, version, source, back)
        if strict and not result.verified:
            raise DwgMismatch(f"{dwg.name} reads back differently from {dxf.name} (type: source, readback): {result.mismatches}")
        return result

    def from_dwg(self, dwg: Path) -> Drawing:
        dwg = Path(dwg).resolve()
        converter = self.name
        if converter == "oda":
            from ezdxf.addons import odafc

            return odafc.readfile(str(dwg))
        reader = _libredwg_tool("dwg2dxf")
        assert reader is not None
        with tempfile.TemporaryDirectory(prefix="unicad-dwg-") as tmp:
            out = Path(tmp) / "back.dxf"
            self._run([str(reader), "-y", "-o", str(out), str(dwg)], out)
            ezdxf_log = logging.getLogger("ezdxf")
            previous, ezdxf_log.disabled = ezdxf_log.disabled, True
            try:
                return recover.readfile(str(out))[0]
            finally:
                ezdxf_log.disabled = previous

    def _libredwg_write(self, dxf: Path, dwg: Path, version: str) -> None:
        if version not in _LIBREDWG_WRITES:
            raise DwgUnavailable(f"LibreDWG writes only {sorted(_LIBREDWG_WRITES)}, not {version}")
        writer = _libredwg_tool("dxf2dwg")
        assert writer is not None
        self._run([str(writer), "-y", "--as", version.lower(), "-o", str(dwg), str(dxf)], dwg)

    def _oda_write(self, dxf: Path, dwg: Path, version: str) -> None:
        from ezdxf.addons import odafc

        try:
            odafc.convert(str(dxf), str(dwg), version=version, replace=True)
        except odafc.ODAFCError as exc:
            raise RenderFailed(f"ODA File Converter failed: {exc}") from exc

    def _run(self, cmd: list[str], output: Path) -> None:
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=self.timeout, text=True)
        except subprocess.TimeoutExpired as exc:
            raise RenderTimeout(f"{Path(cmd[0]).name} did not finish within {self.timeout:g} s") from exc
        if done.returncode != 0 or not output.is_file():
            lines = [ln for ln in (done.stderr + done.stdout).splitlines() if "ERROR" in ln]
            raise RenderFailed(f"{Path(cmd[0]).name} exited with {done.returncode}: {' | '.join(lines[:3]) or 'no error text'}")

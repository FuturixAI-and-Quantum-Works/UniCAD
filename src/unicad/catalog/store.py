import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import get_args

from pydantic import TypeAdapter

from unicad.domain.catalog import EquipmentSpec, InverterSpec, ModuleSpec, PCSSpec, TransformerSpec

KIND_FILES = {
    "module": "modules.json",
    "inverter": "inverters.json",
    "battery": "batteries.json",
    "pcs": "pcs.json",
    "transformer": "transformers.json",
    "cable": "cables.json",
    "protection": "protection.json",
    "meter": "meters.json",
}
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "catalog"
_ADAPTER: TypeAdapter[EquipmentSpec] = TypeAdapter(EquipmentSpec)

Spec = get_args(EquipmentSpec)[0]


def power(spec: Spec) -> float | None:
    """Rated power used by range filters: Wp for modules, kVA for inverters, PCS and transformers, else None."""
    match spec:
        case ModuleSpec():
            return spec.pmax
        case InverterSpec():
            return spec.ac_rated_kva
        case PCSSpec() | TransformerSpec():
            return spec.rated_kva
        case _:
            return None


@dataclass(frozen=True)
class Catalog:
    entries: tuple[Spec, ...]

    def __post_init__(self) -> None:
        if len(self._index) != len(self.entries):
            ids = [e.id for e in self.entries]
            raise ValueError(f"duplicate catalog ids: {sorted({i for i in ids if ids.count(i) > 1})[:10]}")

    @cached_property
    def _index(self) -> dict[str, Spec]:
        return {e.id: e for e in self.entries}

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, id: "str | Spec") -> Spec:
        """An inline project spec (a spec object on a node) resolves to itself."""
        if not isinstance(id, str):
            return id
        return self._index[id]

    def filter(
        self,
        kind: str | None = None,
        manufacturer: str | None = None,
        min_power: float | None = None,
        max_power: float | None = None,
    ) -> list[Spec]:
        """Manufacturer is a case-insensitive exact match. Power bounds are inclusive and exclude kinds without a power rating."""
        out = []
        for e in self.entries:
            if kind is not None and e.kind != kind:
                continue
            if manufacturer is not None and e.manufacturer.lower() != manufacturer.lower():
                continue
            if min_power is not None or max_power is not None:
                p = power(e)
                if p is None or (min_power is not None and p < min_power) or (max_power is not None and p > max_power):
                    continue
            out.append(e)
        return out

    def search(self, text: str, limit: int = 20) -> list[Spec]:
        """Every whitespace-separated term must appear in id, kind, manufacturer, model or device. Results are ranked by id."""
        terms = text.lower().split()
        if not terms:
            return []
        hits = []
        for e in self.entries:
            hay = " ".join((e.id, e.kind, e.manufacturer, e.model, getattr(e, "device", "") or "")).lower()
            if all(t in hay for t in terms):
                hits.append(e)
        return sorted(hits, key=lambda e: e.id)[:limit]


def load_catalog(path: Path | str | None = None) -> Catalog:
    root = Path(path) if path is not None else DEFAULT_PATH
    entries = []
    for kind, name in KIND_FILES.items():
        for raw in json.loads((root / name).read_text(encoding="utf-8")):
            spec = _ADAPTER.validate_python(raw)
            if spec.kind != kind:
                raise ValueError(f"{name}: entry {spec.id!r} has kind {spec.kind!r}, expected {kind!r}")
            entries.append(spec)
    return Catalog(tuple(entries))

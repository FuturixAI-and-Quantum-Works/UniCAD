import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import get_args

from pydantic import TypeAdapter, ValidationError

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
    "logger": "loggers.json",
}
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "catalog"
ADDITIONS_FILE = "catalog-additions.json"
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
    added: frozenset[str] = frozenset()
    """Ids that came from a project's additions file (catalog_add), not from the catalog files."""
    shadowed: tuple[str, ...] = ()
    """Additions skipped because the catalog files now hold the same id (the catalog entry is used)."""

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


def validate_entry(raw: object) -> Spec:
    """One catalog entry of any kind, checked as the catalog files are. A ValueError names each failing field."""
    if not isinstance(raw, dict):
        raise ValueError(f"a catalog entry is a JSON object, got {type(raw).__name__}")  # noqa: TRY004 - a ValueError reaches the agent as its message
    if raw.get("kind") not in KIND_FILES:
        raise ValueError(f"kind: must be one of {', '.join(KIND_FILES)} (got {raw.get('kind')!r})")
    try:
        return _ADAPTER.validate_python(raw)
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            loc = [str(p) for p in err["loc"]]
            if loc and loc[0] == raw["kind"]:
                loc = loc[1:]
            text = err["msg"].removeprefix("Value error, ")
            got = "" if err["type"] in ("missing", "value_error") else f" (got {err['input']!r})"
            parts.append(f"{'.'.join(loc) or 'entry'}: {text}{got}")
        raise ValueError(f"not a valid {raw['kind']} entry: " + "; ".join(parts)) from None


def read_additions(path: Path) -> list[Spec]:
    """Entries of an additions file (a JSON list of catalog entries); an absent file has none."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    items = json.loads(text)
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a JSON list of catalog entries")  # noqa: TRY004
    out = []
    for i, raw in enumerate(items):
        try:
            out.append(validate_entry(raw))
        except ValueError as exc:
            raise ValueError(f"{path} entry {i}: {exc}") from None
    return out


def load_catalog(path: Path | str | None = None, additions: Path | None = None) -> Catalog:
    """The catalog files under `path` (default the shipped catalog), plus the entries of the `additions` file when given."""
    root = Path(path) if path is not None else DEFAULT_PATH
    entries = []
    for kind, name in KIND_FILES.items():
        for raw in json.loads((root / name).read_text(encoding="utf-8")):
            spec = _ADAPTER.validate_python(raw)
            if spec.kind != kind:
                raise ValueError(f"{name}: entry {spec.id!r} has kind {spec.kind!r}, expected {kind!r}")
            entries.append(spec)
    if additions is None:
        return Catalog(tuple(entries))
    base = {e.id for e in entries}
    extra = read_additions(additions)
    shadowed = tuple(e.id for e in extra if e.id in base)
    kept = [e for e in extra if e.id not in base]
    return Catalog(tuple(entries + kept), added=frozenset(e.id for e in kept), shadowed=shadowed)

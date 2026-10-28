from typing import Annotated, Any, get_args

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.catalog.store import Catalog, power
from unicad.mcp.errors import guarded
from unicad.mcp.schema import CatalogKind, CatalogKindArg
from unicad.mcp.workspace import Workspace

READ_ONLY = ToolAnnotations(readOnlyHint=True)
KINDS = get_args(CatalogKind)
KeyFields = {
    "module": ("pmax", "voc", "vmp", "isc", "imp", "tech", "almm_listed"),
    "inverter": ("topology", "ac_rated_kva", "ac_voltage", "mppt_count", "mppt_min", "mppt_max", "dc_max_voltage"),
    "battery": ("chemistry", "energy_kwh", "nominal_voltage"),
    "pcs": ("rated_kva", "ac_voltage", "dc_voltage_min", "dc_voltage_max"),
    "transformer": ("rated_kva", "hv_kv", "lv_kv", "vector_group", "impedance_pct"),
    "cable": ("service", "conductor", "cores", "size_mm2", "insulation", "armoured", "voltage_grade", "ampacity_air"),
    "protection": ("device", "rated_current", "rated_voltage", "poles", "breaking_capacity_ka"),
    "meter": ("function", "accuracy_class", "connection"),
}


def row(spec: Any) -> dict[str, Any]:
    key = {f: v for f in KeyFields[spec.kind] if (v := getattr(spec, f)) is not None}
    out = {"id": spec.id, "kind": spec.kind, "manufacturer": spec.manufacturer, "model": spec.model}
    if (p := power(spec)) is not None:
        out["power"] = p
        out["power_unit"] = "W" if spec.kind == "module" else "kVA"
    return out | {"key": key}


def _unit(kind: str) -> str:
    return "W" if kind == "module" else "kVA"


def _pick(spec: Any) -> dict[str, Any]:
    return {"id": spec.id, "power": power(spec)}


def _kind_report(kind: str, specs: list[Any], manufacturer: str | None, min_power: float | None, max_power: float | None) -> tuple[str, dict[str, Any]]:
    specs = sorted(specs, key=lambda s: (power(s), s.id))
    lo, hi, unit = power(specs[0]), power(specs[-1]), _unit(kind)
    shown = "Wp" if kind == "module" else unit
    below = next((s for s in reversed(specs) if min_power is not None and power(s) < min_power), None)
    above = next((s for s in specs if max_power is not None and power(s) > max_power), None)
    asked = f"between {min_power:g} and {max_power:g} {shown}" if min_power is not None and max_power is not None else f"of at least {min_power:g} {shown}" if min_power is not None else f"of at most {max_power:g} {shown}"
    who = f" from {specs[0].manufacturer}" if manufacturer else ""
    parts = [f"no {kind} {asked}{who}: {kind} power runs {lo:g} to {hi:g} {shown}"]
    if below and not above:
        parts.append(f"largest {kind} is {hi:g} {shown}")
    if above and not below:
        parts.append(f"smallest {kind} is {lo:g} {shown}")
    if below and above:
        parts.append(f"nearest below is {power(below):g} {shown} ({below.id}) and nearest above is {power(above):g} {shown} ({above.id})")
    return ", ".join(parts), {"kind": kind, "unit": unit, "min": lo, "max": hi, "count": len(specs), "nearest_below": _pick(below) if below else None, "nearest_above": _pick(above) if above else None}


def _nothing_found(catalog: Catalog, kind: str | None, manufacturer: str | None, text: str, min_power: float | None, max_power: float | None) -> dict[str, Any]:
    """Where the catalog's range of the searched kind ends, so an empty answer says why it is empty."""
    scope = catalog.filter(kind, manufacturer)
    if text.strip():
        wanted = {e.id for e in catalog.search(text, limit=len(catalog))}
        scope = [e for e in scope if e.id in wanted]
    by_kind: dict[str, list[Any]] = {}
    for e in scope:
        if power(e) is not None:
            by_kind.setdefault(e.kind, []).append(e)
    if (min_power is None and max_power is None) or not by_kind:
        total = len(catalog.filter(kind))
        return {"note": f"no {kind or 'catalog'} entry matches" + (f" from {manufacturer}" if manufacturer else "") + (f" with the words {text!r}" if text.strip() else "") + f"; the catalog holds {total} {kind or ''} entries, so loosen the filters".replace("  ", " ")}
    reports = [_kind_report(k, specs, manufacturer, min_power, max_power) for k, specs in by_kind.items()]
    note = "; ".join(sentence for sentence, _ in reports) + "."
    if len(reports) == 1:
        info = reports[0][1]
        return {"note": note, "range": {k: info[k] for k in ("kind", "unit", "min", "max", "count")}, "nearest_below": info["nearest_below"], "nearest_above": info["nearest_above"]}
    return {"note": note, "ranges": {info["kind"]: {k: info[k] for k in ("unit", "min", "max", "count")} for _, info in reports}}


def search_catalog(
    catalog: Catalog,
    text: str = "",
    kind: str | None = None,
    manufacturer: str | None = None,
    min_power: float | None = None,
    max_power: float | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if kind is not None and kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; use one of {', '.join(KINDS)}")
    hits = catalog.filter(kind, manufacturer, min_power, max_power)
    if text.strip():
        wanted = {e.id for e in catalog.search(text, limit=len(catalog))}
        hits = [e for e in hits if e.id in wanted]
    hits = sorted(hits, key=lambda e: e.id)
    out = {"total": len(hits), "shown": min(len(hits), limit), "rows": [row(e) for e in hits[:limit]]}
    if not hits:
        out |= _nothing_found(catalog, kind, manufacturer, text, min_power, max_power)
    return out


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool(annotations=READ_ONLY)
    @guarded
    def catalog_search(
        text: Annotated[str, Field(description="Words that must all appear in id, kind, manufacturer or model, e.g. 'growatt 100'")] = "",
        kind: CatalogKindArg = None,
        manufacturer: Annotated[str | None, Field(description="Exact manufacturer name, case-insensitive")] = None,
        min_power: Annotated[float | None, Field(description="Inclusive lower bound: W for modules, kVA for inverter, pcs and transformer")] = None,
        max_power: Annotated[float | None, Field(description="Inclusive upper bound, same units as min_power")] = None,
        limit: Annotated[int, Field(ge=1, le=200, description="Maximum rows returned, number")] = 20,
    ) -> dict[str, Any]:
        """Find equipment in the catalog; returns compact rows with the key ratings. Use the row `id` as a node `spec` or edge `cable`.

        When nothing matches, `note` says where the catalog's range of that kind ends (for example the largest module) and `range`, `nearest_below`, `nearest_above` give the numbers.

        Example: catalog_search(kind="inverter", min_power=90, max_power=110)."""
        return search_catalog(ws.catalog, text, kind, manufacturer, min_power, max_power, limit)

    @server.tool(annotations=READ_ONLY)
    @guarded
    def catalog_get(id: Annotated[str, Field(description="Catalog id from catalog_search")]) -> dict[str, Any]:
        """Full catalog entry with every rating and its source.

        Example: catalog_get(id="growatt-max-100ktl3-x-lv")."""
        try:
            spec = ws.catalog.get(id)
        except KeyError:
            near = [e.id for e in ws.catalog.search(id.replace("-", " "), limit=5)]
            raise KeyError(f"no catalog entry {id!r}" + (f"; similar: {near}" if near else "; use catalog_search")) from None
        return spec.model_dump(mode="json", exclude_none=True)

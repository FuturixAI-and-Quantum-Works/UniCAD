from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from unicad.catalog.store import power
from unicad.mcp.errors import guarded
from unicad.mcp.workspace import Workspace

READ_ONLY = ToolAnnotations(readOnlyHint=True)
KINDS = ("module", "inverter", "battery", "pcs", "transformer", "cable", "protection", "meter")
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


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool(annotations=READ_ONLY)
    @guarded
    def catalog_search(
        text: Annotated[str, Field(description="Words that must all appear in id, kind, manufacturer or model, e.g. 'growatt 100'")] = "",
        kind: Annotated[str | None, Field(description=f"One of: {', '.join(KINDS)}")] = None,
        manufacturer: Annotated[str | None, Field(description="Exact manufacturer name, case-insensitive")] = None,
        min_power: Annotated[float | None, Field(description="Inclusive lower bound: W for modules, kVA for inverter, pcs and transformer")] = None,
        max_power: Annotated[float | None, Field(description="Inclusive upper bound, same units as min_power")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 20,
    ) -> dict[str, Any]:
        """Find equipment in the catalog; returns compact rows with the key ratings. Use the row `id` as a node `spec` or edge `cable`.

        Example: catalog_search(kind="inverter", min_power=90, max_power=110)."""
        catalog = ws.catalog
        if kind is not None and kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; use one of {', '.join(KINDS)}")
        hits = catalog.filter(kind, manufacturer, min_power, max_power)
        if text.strip():
            wanted = {e.id for e in catalog.search(text, limit=len(catalog))}
            hits = [e for e in hits if e.id in wanted]
        hits = sorted(hits, key=lambda e: e.id)
        return {"total": len(hits), "shown": min(len(hits), limit), "rows": [row(e) for e in hits[:limit]]}

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

"""Adding equipment the catalog lacks: entries go to the workspace's additions file, never into the catalog files."""

import json
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from unicad.catalog.store import read_additions, validate_entry
from unicad.mcp.errors import guarded
from unicad.mcp.workspace import Workspace
from unicad.store.files import atomic_write


def _write(path, specs: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [s.model_dump(mode="json", exclude_none=True) for s in specs]
    atomic_write(path, (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


def add_entry(ws: Workspace, entry: dict[str, Any], replace: bool = False) -> dict[str, Any]:
    spec = validate_entry(entry)
    path = ws.additions_path
    with ws.lock:
        catalog = ws.catalog
        added = read_additions(path)
        mine = {s.id: i for i, s in enumerate(added)}
        if spec.id not in mine:
            try:
                have = catalog.get(spec.id)
            except KeyError:
                have = None
            if have is not None:
                raise ValueError(
                    f"id {spec.id!r} is already in the catalog ({have.kind}, {have.manufacturer} {have.model}); use that entry, or give the new one a different id"
                )
        elif not replace:
            raise ValueError(f"id {spec.id!r} was already added to this workspace's catalog; pass replace=true to overwrite that addition, or use a different id")
        replaced = spec.id in mine
        if replaced:
            added[mine[spec.id]] = spec
        else:
            added.append(spec)
        _write(path, added)
        ws.catalog.get(spec.id)  # the merged catalog loads with it
    return {
        "id": spec.id,
        "kind": spec.kind,
        "stored_in": str(path),
        "replaced": replaced,
        "entry": spec.model_dump(mode="json", exclude_none=True),
        "note": "Added to this workspace's catalog additions (the shipped catalog files are not changed); catalog_search and catalog_get see it now.",
    }


def remove_entry(ws: Workspace, id: str) -> dict[str, Any]:
    path = ws.additions_path
    with ws.lock:
        added = read_additions(path)
        if id not in {s.id for s in added}:
            base = id in {e.id for e in ws.catalog.entries} - ws.catalog.added
            raise KeyError(f"{id!r} is " + ("a shipped catalog entry; only entries added with catalog_add can be removed" if base else "not an entry added with catalog_add"))
        users = [p for p in ws.store.list() if f'"{id}"' in ws.store.read_text(p)]
        if users:
            raise ValueError(f"projects {users} use {id!r}; change their nodes to another spec before removing it")
        _write(path, [s for s in added if s.id != id])
    return {"id": id, "removed": True, "stored_in": str(path)}


def register(server: MCPServer, ws: Workspace) -> None:
    @server.tool()
    @guarded
    def catalog_add(
        entry: Annotated[dict[str, Any], Field(description="One catalog entry as a JSON object, with the same fields catalog_get returns for that kind: id (lowercase, a-z 0-9 . _ -), kind, manufacturer, model, source {kind, ref} and the kind's ratings")],
        replace: Annotated[bool, Field(description="Overwrite an entry this workspace added earlier with catalog_add (never a shipped catalog entry)")] = False,
    ) -> dict[str, Any]:
        """Add one piece of equipment the catalog lacks (a module, inverter, transformer, cable, ...). This is the only way to add equipment: never write or edit catalog JSON files.

        The entry is checked exactly as catalog files are (an error names each failing field), must have an id not already in the catalog, and is
        appended to the workspace's catalog-additions.json; the shipped catalog files are never rewritten. Copy a similar entry from catalog_get, change id, model, ratings
        and source (cite the datasheet: source.kind 'datasheet', source.ref its file or URL and page).

        Example: catalog_add(entry={"id": "acme-tx-1100kva-6.6-0.66", "kind": "transformer", "manufacturer": "Acme", "model": "1100 kVA 6.6/0.66 kV IDT",
        "source": {"kind": "datasheet", "ref": "Acme IDT datasheet rev 2, p.1"}, "rated_kva": 1100, "hv_kv": 6.6, "lv_kv": [0.66], "vector_group": "Dyn11", "impedance_pct": 6.25})."""
        return add_entry(ws, entry, replace)

    @server.tool()
    @guarded
    def catalog_remove_addition(id: Annotated[str, Field(description="Id of an entry added with catalog_add")]) -> dict[str, Any]:
        """Remove an entry this workspace added with catalog_add. Shipped catalog entries cannot be removed, and an entry a project still uses is refused.

        Example: catalog_remove_addition(id="acme-tx-1100kva-6.6-0.66")."""
        return remove_entry(ws, id)

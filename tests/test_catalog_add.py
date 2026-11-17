"""catalog_add: an agent adds equipment the catalog lacks to the workspace's additions file; the catalog files are never rewritten."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from test_mcp import connect

from unicad.catalog.store import ADDITIONS_FILE, DEFAULT_PATH, KIND_FILES
from unicad.mcp.config import Config
from unicad.mcp.tools.catalog_edit import add_entry, remove_entry
from unicad.mcp.workspace import Workspace

IDT = {
    "id": "test-idt-1100kva-6.6-0.66",
    "kind": "transformer",
    "manufacturer": "Test Transformers",
    "model": "1100 kVA 6.6/0.66 kV inverter duty",
    "source": {"kind": "datasheet", "ref": "test datasheet p.1"},
    "rated_kva": 1100,
    "hv_kv": 6.6,
    "lv_kv": [0.66],
    "vector_group": "Dyn11",
    "impedance_pct": 6.25,
}
MODULE_ID = "test-gs10-b144-gf-550"


def _hashes(root: Path) -> dict[str, str]:
    return {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in KIND_FILES.values()}


@pytest.fixture
def ws(tmp_path) -> Workspace:
    return Workspace(Config(workspace=tmp_path / "ws"))


def _module(ws: Workspace) -> dict:
    base = ws.catalog.filter("module")[0].model_dump(mode="json", exclude_none=True)
    return base | {"id": MODULE_ID, "manufacturer": "Test Solar", "model": "GS10-B144-GF-550", "source": {"kind": "datasheet", "ref": "test p.2"}}


def test_an_added_entry_is_appended_to_the_workspace_file_and_the_catalog_files_are_untouched(ws):
    before = _hashes(DEFAULT_PATH)
    total = len(ws.catalog)
    out = add_entry(ws, IDT)
    assert (out["id"], out["kind"], out["replaced"]) == (IDT["id"], "transformer", False)
    assert out["stored_in"] == str(ws.config.workspace / ADDITIONS_FILE)
    out = add_entry(ws, _module(ws))
    stored = json.loads(Path(out["stored_in"]).read_text(encoding="utf-8"))
    assert [e["id"] for e in stored] == [IDT["id"], MODULE_ID]
    assert len(ws.catalog) == total + 2
    assert ws.catalog.get(IDT["id"]).hv_kv == 6.6 and ws.catalog.added == {IDT["id"], MODULE_ID}
    assert _hashes(DEFAULT_PATH) == before


def test_a_new_server_sees_the_additions(ws):
    add_entry(ws, IDT)
    again = Workspace(Config(workspace=ws.config.workspace))
    assert again.catalog.get(IDT["id"]).rated_kva == 1100


def test_an_invalid_entry_names_the_failing_fields_and_writes_nothing(ws):
    with pytest.raises(ValueError, match=r"not a valid transformer entry: lv_kv: Field required; impedance_pct: Input should be less than 30 \(got 45\)"):
        add_entry(ws, {k: v for k, v in IDT.items() if k != "lv_kv"} | {"impedance_pct": 45})
    with pytest.raises(ValueError, match="kind: must be one of module, inverter"):
        add_entry(ws, IDT | {"kind": "panel"})
    with pytest.raises(ValueError, match="source: Field required"):
        add_entry(ws, {k: v for k, v in IDT.items() if k != "source"})
    assert not ws.additions_path.exists()


def test_duplicate_ids_are_refused(ws):
    shipped = ws.catalog.filter("transformer")[0]
    with pytest.raises(ValueError, match=f"id '{shipped.id}' is already in the catalog"):
        add_entry(ws, IDT | {"id": shipped.id}, replace=True)
    add_entry(ws, IDT)
    with pytest.raises(ValueError, match="already added to this workspace's catalog; pass replace=true"):
        add_entry(ws, IDT | {"rated_kva": 1250})
    out = add_entry(ws, IDT | {"rated_kva": 1250}, replace=True)
    assert out["replaced"] and ws.catalog.get(IDT["id"]).rated_kva == 1250
    assert len(json.loads(ws.additions_path.read_text(encoding="utf-8"))) == 1


def test_an_addition_the_catalog_later_ships_gives_way_to_the_catalog_entry(ws):
    shipped = ws.catalog.filter("transformer")[0]
    ws.additions_path.parent.mkdir(parents=True)
    ws.additions_path.write_text(json.dumps([IDT | {"id": shipped.id}, IDT]), encoding="utf-8")
    assert ws.catalog.get(shipped.id) == shipped and ws.catalog.shadowed == (shipped.id,)
    assert ws.catalog.added == {IDT["id"]}


def test_removing_an_addition(ws):
    add_entry(ws, IDT)
    with pytest.raises(KeyError, match="shipped catalog entry"):
        remove_entry(ws, ws.catalog.filter("module")[0].id)
    remove_entry(ws, IDT["id"])
    with pytest.raises(KeyError):
        ws.catalog.get(IDT["id"])


def test_catalog_add_over_mcp_then_used_by_a_node(tmp_path):
    async def run():
        async with connect(tmp_path / "ws") as c:
            out = await c.call("catalog_add", entry=IDT)
            assert out["stored_in"].endswith(ADDITIONS_FILE)
            assert (await c.call("catalog_get", id=IDT["id"]))["hv_kv"] == 6.6
            assert [r["id"] for r in (await c.call("catalog_search", text="test 1100"))["rows"]] == [IDT["id"]]
            text = await c.fail("catalog_add", entry=IDT | {"impedance_pct": "high"})
            assert "impedance_pct: Input should be a valid number" in text, text
            pid = (await c.call("project_create", name="Six six", client="C", segment="utility", site="S", grid_voltage_v=6600))["project_id"]
            await c.call("network_add_node", project_id=pid, id="tr", kind="transformer", spec=IDT["id"])
            text = await c.fail("catalog_remove_addition", id=IDT["id"])
            assert "use" in text and pid in text, text

    asyncio.run(asyncio.wait_for(run(), 120))

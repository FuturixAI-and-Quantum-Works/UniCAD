"""A long-lived server sees catalog entries added while it runs (OpenCAD keeps one UniCAD server for the session)."""

import json
import os
import shutil
from pathlib import Path

import pytest

from unicad.catalog.store import DEFAULT_PATH
from unicad.mcp.config import Config
from unicad.mcp.workspace import Workspace


def _bump_mtime(path: Path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_catalog_reloads_when_a_file_changes(tmp_path):
    catalog = tmp_path / "catalog"
    shutil.copytree(DEFAULT_PATH, catalog)
    ws = Workspace(Config(workspace=tmp_path / "ws", catalog=catalog))
    first = ws.catalog
    assert ws.catalog is first  # unchanged files: the same catalog

    modules = catalog / "modules.json"
    entries = json.loads(modules.read_text(encoding="utf-8"))
    added = dict(entries[0], id="test-added-module-550", model="TEST-ADDED-550")
    modules.write_text(json.dumps([*entries, added]), encoding="utf-8")
    _bump_mtime(modules)

    reloaded = ws.catalog
    assert reloaded is not first
    assert reloaded.get("test-added-module-550").model == "TEST-ADDED-550"


def test_a_broken_catalog_file_raises_rather_than_serving_stale_entries(tmp_path):
    catalog = tmp_path / "catalog"
    shutil.copytree(DEFAULT_PATH, catalog)
    ws = Workspace(Config(workspace=tmp_path / "ws", catalog=catalog))
    assert len(ws.catalog) > 0
    (catalog / "modules.json").write_text("[{", encoding="utf-8")
    _bump_mtime(catalog / "modules.json")
    with pytest.raises(ValueError):
        _ = ws.catalog

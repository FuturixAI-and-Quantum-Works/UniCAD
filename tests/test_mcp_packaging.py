"""Packaging: bundled data, the console script, and the static shape of the Docker files."""

import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

import unicad.mcp.config as config

ROOT = Path(__file__).resolve().parents[1]


def test_console_script_is_unicad_mcp():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"] == {"unicad-mcp": "unicad.mcp.cli:main"}


def test_bundled_catalog_is_the_default_only_when_present(tmp_path, monkeypatch):
    assert config.Config.from_env({}).catalog is None
    (tmp_path / "catalog").mkdir()
    monkeypatch.setattr(config, "BUNDLED_DATA", tmp_path)
    assert config.Config.from_env({}).catalog == tmp_path / "catalog"
    override = tmp_path / "mine"
    assert config.Config.from_env({"UNICAD_CATALOG": str(override)}).catalog == override.resolve()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_wheel_ships_catalog_and_profiles(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "-o", str(tmp_path), str(ROOT)], check=True, capture_output=True, timeout=300)
    (wheel,) = tmp_path.glob("unicad-*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())
    assert {"unicad/_data/catalog/modules.json", "unicad/_data/catalog/cables.json", "unicad/_data/profiles/default.json"} <= names
    assert "unicad/mcp/tools/site.py" in names and not any("tools_site" in n for n in names)
    entry = next(n for n in names if n.endswith("entry_points.txt"))
    assert "unicad-mcp = unicad.mcp.cli:main" in zipfile.ZipFile(wheel).read(entry).decode()


def test_help_runs_without_a_workspace(tmp_path):
    out = subprocess.run([sys.executable, "-m", "unicad", "--help"], capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert out.returncode == 0 and "--http" in out.stdout and not list(tmp_path.iterdir())


def test_dockerfile_pins_and_wires_the_toolchain():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM base" in text and "python:3.12-slim" in text
    assert "LIBRECAD_VERSION=2.2.1.6" in text and "--appimage-extract" in text and "fuse" not in text.lower()
    assert "LIBREDWG_VERSION=0.13." in text and "ftp.gnu.org/gnu/libredwg" in text
    assert text.count("sha256sum -c") == 2
    for needle in ("xvfb", "xauth", "poppler-utils", "LIBRECAD=", "LIBREDWG_BIN=", "UNICAD_WORKSPACE=", "USER unicad"):
        assert needle in text, needle
    assert 'CMD ["--http", "--host", "0.0.0.0", "--port", "8765"]' in text
    assert "docker/entrypoint.sh" in text and (ROOT / "docker" / "entrypoint.sh").is_file()


@pytest.mark.skipif(shutil.which("sh") is None or os.name == "nt", reason="POSIX sh and executable bits are Linux-only")
@pytest.mark.parametrize("script", ["entrypoint.sh", "smoke.sh"])
def test_docker_scripts_parse(script):
    path = ROOT / "docker" / script
    assert subprocess.run(["sh", "-n", str(path)], capture_output=True).returncode == 0
    assert path.stat().st_mode & 0o111


def test_dockerignore_keeps_build_inputs():
    ignored = {line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()}
    assert not ignored & {"src", "catalog", "profiles", "docker", "pyproject.toml", "README.md"}

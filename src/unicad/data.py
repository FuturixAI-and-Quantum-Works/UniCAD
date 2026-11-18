"""Where UniCAD's data folders (catalog, profiles) are: the repository's own in a source checkout, else the copies the wheel
ships under unicad/_data (pyproject's force-include). An installed UniCAD, such as the runtime OpenCAD bundles, has only the latter."""

from pathlib import Path

_HERE = Path(__file__).resolve().parent


def data_dir(name: str) -> Path:
    repo = _HERE.parents[1] / name  # <repo>/src/unicad -> <repo>/<name>
    if (_HERE.parents[1] / "pyproject.toml").is_file() and repo.is_dir():
        return repo
    return _HERE / "_data" / name

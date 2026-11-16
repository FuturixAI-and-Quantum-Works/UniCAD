"""Build a self-contained UniCAD runtime for this platform: a relocatable CPython (python-build-standalone, as uv
installs it) with UniCAD and its dependencies installed into it, ready to ship inside OpenCAD.

OpenCAD runs it as ``<runtime>/<python> -m unicad`` (the MCP server over stdio) and needs nothing else on the
user's computer: no Python, no uv, no network. ``runtime.json`` in the folder says which interpreter to run.

Usage (from the repository, with uv on PATH):

    uv run --no-project python tools/build_runtime.py [--wheel dist/unicad-X.whl] [--out build/unicad-runtime]
                                                      [--python 3.13] [--archive]

Without ``--wheel`` the wheel is built first (``uv build --wheel``). ``--archive`` also writes
``unicad-runtime-<version>-<os>-<arch>.tar.gz`` (``.zip`` on Windows) next to the folder.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = sys.platform == "win32"

# parts of the standard library a headless MCP server never imports: tests, the IDE, Tk, headers, static libraries
STDLIB_TRIM = ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3")


def run(*cmd: str | Path, **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def platform_tag() -> str:
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{arch}"


def project_version() -> str:
    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def build_wheel() -> Path:
    dist = ROOT / "dist"
    run("uv", "build", "--wheel", "-o", dist, cwd=ROOT)
    wheels = sorted(dist.glob(f"unicad-{project_version()}-*.whl"))
    if not wheels:
        sys.exit(f"no wheel for {project_version()} in {dist}")
    return wheels[-1]


def install_python(version: str, into: Path) -> Path:
    """A python-build-standalone CPython in ``into`` (relocatable: it may be moved anywhere)."""
    managed = into.parent / "_uv-python"
    shutil.rmtree(managed, ignore_errors=True)
    run("uv", "python", "install", version, "--install-dir", managed, "--no-bin")
    found = [p for p in managed.iterdir() if p.is_dir() and not p.is_symlink() and p.name.startswith("cpython-")]
    if len(found) != 1:
        sys.exit(f"expected one interpreter in {managed}, found {[p.name for p in found]}")
    shutil.rmtree(into, ignore_errors=True)
    shutil.copytree(found[0], into, symlinks=True)
    shutil.rmtree(managed, ignore_errors=True)
    return into / ("python.exe" if WINDOWS else "bin/python3")


def trim(python_home: Path) -> None:
    lib = python_home / "Lib" if WINDOWS else next((python_home / "lib").glob("python3.*"))
    for name in STDLIB_TRIM:
        shutil.rmtree(lib / name, ignore_errors=True)
    shutil.rmtree(python_home / "include", ignore_errors=True)
    for static in python_home.rglob("*.a"):
        static.unlink()
    # the interpreter's own copy of tcl/tk (python-build-standalone ships it under lib/)
    for tk in (python_home / "lib").glob("t[ck]*[0-9]*") if not WINDOWS else (python_home / "tcl").glob("*"):
        if tk.is_dir():
            shutil.rmtree(tk, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wheel", type=Path, help="the UniCAD wheel to install (default: build it)")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "unicad-runtime", help="the runtime folder to write")
    ap.add_argument("--python", default="3.13", help="CPython version (default 3.13)")
    ap.add_argument("--archive", action="store_true", help="also write a .tar.gz (.zip on Windows) of the folder")
    args = ap.parse_args()

    wheel = (args.wheel or build_wheel()).resolve()
    out: Path = args.out.resolve()
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    python = install_python(args.python, out / "python")
    # into the interpreter's own site-packages: no venv, whose paths would not survive the move
    run("uv", "pip", "install", "--python", python, "--break-system-packages", "--no-cache", wheel)
    trim(out / "python")
    run(python, "-m", "compileall", "-q", "-j", "0", out / "python")  # first start without compiling

    # the smoke test OpenCAD relies on: the module starts and answers
    run(python, "-m", "unicad", "--help", stdout=subprocess.DEVNULL)

    version = project_version()
    # the catalog inside the package: OpenCAD copies it once to a folder of the user's, which the AI may extend
    catalog = Path(subprocess.run([str(python), "-c", "from unicad.mcp.config import BUNDLED_DATA; print(BUNDLED_DATA / 'catalog')"],
                                  check=True, capture_output=True, text=True).stdout.strip())
    manifest = {
        "schema": 1,
        "unicad": version,
        "platform": platform_tag(),
        "python": python.relative_to(out).as_posix(),
        "module": "unicad",
        "catalog": catalog.relative_to(out).as_posix(),
    }
    (out / "runtime.json").write_text(json.dumps(manifest, indent=2) + "\n")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"runtime: {out} ({size / 1048576:.0f} MB)")

    if args.archive:
        name = f"unicad-runtime-{version}-{platform_tag()}"
        if WINDOWS:
            archive = out.parent / f"{name}.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                for f in sorted(out.rglob("*")):
                    z.write(f, Path("unicad-runtime") / f.relative_to(out))
        else:
            archive = out.parent / f"{name}.tar.gz"
            with tarfile.open(archive, "w:gz", compresslevel=9) as t:
                t.add(out, arcname="unicad-runtime")
        print(f"archive: {archive} ({archive.stat().st_size / 1048576:.0f} MB)")


if __name__ == "__main__":
    main()

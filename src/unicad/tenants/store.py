"""Company profiles on disk: a tenant's own `<workspace>/profiles/<id>.json` first, then the bundled ones."""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from unicad.drafting.sheets.model import PROFILE_VERSION, CompanyProfile
from unicad.rules.thresholds import Setting
from unicad.data import data_dir

BUNDLED_PROFILES = data_dir("profiles")
PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
DEMO_PROFILE = "default"


@dataclass(frozen=True)
class ProfileInfo:
    id: str
    name: str
    version: int
    origin: Literal["workspace", "bundled"]
    path: Path


def check_id(profile_id: str) -> str:
    if not PROFILE_ID.match(profile_id):
        raise ValueError(f"profile id {profile_id!r} must be 1 to 40 characters of a-z, 0-9, '-' and '_', starting with a letter or digit")
    return profile_id


def profiles_dir(workspace: Path) -> Path:
    return workspace / "profiles"


def search_path(workspace: Path | None) -> list[tuple[Literal["workspace", "bundled"], Path]]:
    dirs: list[tuple[Literal["workspace", "bundled"], Path]] = []
    if workspace is not None:
        dirs.append(("workspace", profiles_dir(workspace)))
    dirs += [("bundled", Path(d)) for d in os.environ.get("UNICAD_PROFILES", "").split(os.pathsep) if d]
    dirs.append(("bundled", BUNDLED_PROFILES))
    return dirs


def find_profile(profile_id: str, workspace: Path | None = None) -> tuple[Literal["workspace", "bundled"], Path]:
    check_id(profile_id)
    looked = search_path(workspace)
    for origin, d in looked:
        if (path := d / f"{profile_id}.json").is_file():
            return origin, path
    raise KeyError(f"no company profile {profile_id!r}; looked in {[str(d) for _, d in looked]}")


def load_profile(profile_id: str, workspace: Path | None = None) -> CompanyProfile:
    """The profile `profile_id`, relative file paths resolved. `workspace` is the tenant's workspace directory."""
    return CompanyProfile.load(find_profile(profile_id, workspace)[1])


def drawing_profile(profile_id: str, workspace: Path | None = None) -> CompanyProfile:
    """The profile a project's sheets are drawn with. A project still on the bundled demo profile (project_create's default) has no EPC
    company, so its sheets say so instead of printing the demo company's name, address and logo."""
    origin, path = find_profile(profile_id, workspace)
    profile = CompanyProfile.load(path)
    return profile.without_company() if profile_id == DEMO_PROFILE and origin == "bundled" else profile


def thresholds_of(profile_id: str, workspace: Path | None = None) -> dict[str, Setting]:
    """The company thresholds of a project's profile; none when the profile does not exist."""
    try:
        return load_profile(profile_id, workspace).threshold_settings()
    except KeyError:
        return {}


def list_profiles(workspace: Path | None = None) -> list[ProfileInfo]:
    seen: dict[str, ProfileInfo] = {}
    for origin, d in search_path(workspace):
        for path in sorted(d.glob("*.json")) if d.is_dir() else []:
            if path.stem in seen or not PROFILE_ID.match(path.stem):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            seen[path.stem] = ProfileInfo(path.stem, str(data.get("name", "")), int(data.get("version", 1)), origin, path)
    return sorted(seen.values(), key=lambda i: i.id)


def _relative(p: Path | None, base: Path) -> Path | None:
    if p is None or not p.is_absolute():
        return p
    try:
        return p.relative_to(base)
    except ValueError:
        return p


def to_json(profile: CompanyProfile, base: Path) -> dict:
    """The profile as a JSON-ready dict at the current format version, file paths under `base` made relative to it."""
    p = profile.model_copy(
        update={
            "version": PROFILE_VERSION,
            "logo": _relative(profile.logo, base),
            "symbol_overrides": {k: o.model_copy(update={"dxf": _relative(o.dxf, base)}) for k, o in profile.symbol_overrides.items()},
            "title_block": profile.title_block.model_copy(update={"templates": {s: t.model_copy(update={"dxf": _relative(t.dxf, base)}) for s, t in profile.title_block.templates.items()}}),
        }
    )
    data = p.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
    data["version"] = PROFILE_VERSION
    return {"version": data.pop("version"), **data}


def save_profile(profile: CompanyProfile, profile_id: str, workspace: Path) -> Path:
    """Write `profile` to the tenant's profile directory (created if needed), replacing a profile of the same id."""
    check_id(profile_id)
    d = profiles_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{profile_id}.json"
    data = to_json(profile, d.resolve())
    CompanyProfile.model_validate(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path

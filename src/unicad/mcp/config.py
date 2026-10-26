import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKSPACE = "./unicad-workspace"
BUNDLED_DATA = Path(__file__).resolve().parents[1] / "_data"


@dataclass(frozen=True)
class Config:
    workspace: Path
    catalog: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        catalog = env.get("UNICAD_CATALOG")
        bundled = BUNDLED_DATA / "catalog"
        return cls(
            workspace=Path(env.get("UNICAD_WORKSPACE") or DEFAULT_WORKSPACE).expanduser().resolve(),
            catalog=Path(catalog).expanduser().resolve() if catalog else bundled if bundled.is_dir() else None,
        )

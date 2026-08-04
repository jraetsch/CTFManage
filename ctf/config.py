"""Config file handling: ~/.config/ctftool/config.toml.

tomllib reads TOML but the stdlib cannot write it, so the writer below is
hand-rolled for exactly the shape we produce. It is not a general TOML emitter
and does not pretend to be.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP = "ctftool"

DEFAULTS = {
    "layout": "{platform}/{slug}",
    "scaffold": ["sol.txt"],
    "write_description": True,
    "default_platform": "picoCTF",
}


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / APP


def config_path() -> Path:
    return config_home() / "config.toml"


def index_dir() -> Path:
    return config_home() / "index"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    ctf_root: Path
    layout: str = DEFAULTS["layout"]
    scaffold: list[str] = field(default_factory=lambda: list(DEFAULTS["scaffold"]))
    write_description: bool = DEFAULTS["write_description"]
    default_platform: str = DEFAULTS["default_platform"]
    platforms: dict[str, dict] = field(default_factory=dict)

    # -- derived paths -----------------------------------------------------

    @property
    def state_dir(self) -> Path:
        """Lives inside the CTF root so it travels with the challenge tree."""
        return self.ctf_root / ".ctftool"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "ctf.db"

    def index_path(self, platform: str) -> Path:
        """Index cache location, overridable per platform in the config."""
        override = self.platforms.get(platform, {}).get("index")
        if override:
            return Path(override).expanduser()
        return index_dir() / f"{platform.lower()}.json"

    def challenge_dir(self, platform: str, slug: str) -> Path:
        rel = self.layout.format(platform=platform, slug=slug)
        return self.ctf_root / rel


def load(required: bool = True) -> Config | None:
    path = config_path()
    if not path.exists():
        if required:
            raise ConfigError(
                f"no config at {path} — run `ctf init` first"
            )
        return None
    with path.open("rb") as fh:
        data = tomllib.load(fh)

    root = data.get("ctf_root")
    if not root:
        raise ConfigError(f"{path}: `ctf_root` is missing")
    cfg = Config(
        ctf_root=Path(root).expanduser(),
        layout=data.get("layout", DEFAULTS["layout"]),
        scaffold=list(data.get("scaffold", DEFAULTS["scaffold"])),
        write_description=bool(data.get("write_description", True)),
        default_platform=data.get("default_platform", DEFAULTS["default_platform"]),
        platforms=data.get("platforms", {}) or {},
    )
    if not cfg.ctf_root.is_dir():
        raise ConfigError(f"ctf_root does not exist: {cfg.ctf_root}")
    return cfg


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(cfg: Config) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ctftool configuration",
        f"ctf_root = {_toml_str(str(cfg.ctf_root))}",
        f"layout   = {_toml_str(cfg.layout)}",
        "scaffold = [" + ", ".join(_toml_str(s) for s in cfg.scaffold) + "]",
        f"write_description = {'true' if cfg.write_description else 'false'}",
        f"default_platform = {_toml_str(cfg.default_platform)}",
    ]
    for name, opts in sorted(cfg.platforms.items()):
        lines.append("")
        lines.append(f"[platforms.{name}]")
        for k, v in sorted(opts.items()):
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, int):
                lines.append(f"{k} = {v}")
            elif isinstance(v, (list, tuple)):
                lines.append(f"{k} = [" + ", ".join(_toml_str(str(x)) for x in v) + "]")
            else:
                lines.append(f"{k} = {_toml_str(str(v))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

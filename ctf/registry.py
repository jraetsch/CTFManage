"""Platform plugin discovery. See docs/PLATFORMS.md § The contract."""

from __future__ import annotations

from pathlib import Path

from .models import Challenge

_REGISTRY: dict[str, "Platform"] = {}


class ManualStepRequired(Exception):
    """Not an error path — a supported outcome.

    Raised when a platform's catalogue cannot be fetched headlessly. The CLI
    prints the instructions and exits 0: the user is not stuck, they are being
    handed a two-minute task.
    """

    def __init__(self, reason: str, instructions: str, resume_with: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.instructions = instructions
        self.resume_with = resume_with


class PlatformError(Exception):
    pass


class Platform:
    name: str = ""
    url: str = ""

    def resolve(self, ref: str) -> list[Challenge]:
        """ref -> candidates. Pure local lookup against the index. No network."""
        raise NotImplementedError

    def refresh_index(self, source: Path | None = None) -> int:
        """Rebuild the local index; return the number of challenges indexed."""
        raise NotImplementedError

    def index_status(self) -> str:
        return "unknown"


def register(cls):
    inst = cls()
    if not inst.name:
        raise PlatformError(f"{cls.__name__} has no `name`")
    _REGISTRY[inst.name.lower()] = inst
    return cls


def get(name: str) -> Platform:
    try:
        return _REGISTRY[name.lower()]
    except KeyError:
        known = ", ".join(sorted(p.name for p in _REGISTRY.values())) or "(none)"
        raise PlatformError(f"unknown platform {name!r}; known: {known}") from None


def all_platforms() -> list[Platform]:
    return sorted(_REGISTRY.values(), key=lambda p: p.name.lower())


def load_all() -> None:
    """Import every module in ctf/platforms so @register fires."""
    from . import platforms  # noqa: F401  (its __init__ does the walking)

"""Plain data carried between the platform layer and the core.

These are what a platform's `resolve()` returns. They are deliberately dumb:
no network, no filesystem, no DB. See docs/PLATFORMS.md § The contract.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse


@dataclass
class Artifact:
    """A downloadable file belonging to a challenge."""

    url: str
    filename: str | None = None

    def default_filename(self) -> str:
        """Basename of the URL path, percent-decoded.

        NOT safe to use as a path on its own — `materialize.safe_target()` is
        the only thing allowed to turn this into a filesystem location.
        """
        return posixpath.basename(unquote(urlparse(self.url).path))

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc.lower()


@dataclass
class Endpoint:
    """A network service for the challenge, e.g. `nc saturn.picoctf.net 51234`.

    Challenges of this kind frequently have *no* artifacts at all, which is why
    "zero artifacts" alone is not an error condition. See docs/PLATFORMS.md.
    """

    label: str
    endpoint: str


@dataclass
class Challenge:
    platform: str
    name: str
    artifacts: list[Artifact] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    category: str | None = None
    difficulty: str | None = None
    points: int | None = None
    event: str | None = None
    url: str | None = None
    description: str | None = None
    author: str | None = None
    platform_id: str | None = None
    raw: dict | None = None

    @property
    def has_content(self) -> bool:
        """False means `ctf get` would create an empty directory.

        Deliberately counts endpoints as content: a pwn challenge delivered as
        `nc host port` is complete with no files. Treating it as a failure was
        a bug in the original spec.
        """
        return bool(self.artifacts) or bool(self.endpoints)

"""Turning untrusted names into filesystem locations.

This module is the boundary where platform-supplied data becomes paths, so it
is the one place in the project with a real threat model. See
docs/ARCHITECTURE.md § Security requirements. `slugify()` and `safe_target()`
are both deliberately strict and both have adversarial tests.
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

MAX_NAME = 120


class UnsafeName(ValueError):
    """A platform-supplied name could not be reduced to something safe."""


# -- directory names -------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """'Glory of the Garden' -> 'glory_of_the_garden'.

    Output is guaranteed to match [a-z0-9][a-z0-9_]* — which is what makes it
    safe as a directory name: no dots, no slashes, no leading dash, never empty.
    """
    # Fold accents so 'Café' -> 'cafe' rather than 'caf'.
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    slug = _NON_ALNUM.sub("_", folded.lower()).strip("_")
    slug = slug[:MAX_NAME].strip("_")
    if not slug:
        raise UnsafeName(f"name reduces to an empty slug: {name!r}")
    if slug[0].isdigit():
        # Legal, but a leading digit reads badly and `cd 2` is ambiguous with
        # a directory stack entry in some setups. Prefix rather than reject.
        slug = f"c_{slug}"
    return slug


# -- artifact filenames ----------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(url_or_name: str) -> str:
    """Reduce a URL (or a platform-supplied filename) to a bare, safe basename.

    Rejects rather than sanitises: a name we cannot vouch for is an error the
    user should see, not something to silently rewrite into a different file.
    """
    raw = url_or_name
    if "://" in raw:
        raw = urlparse(raw).path
    name = posixpath.basename(unquote(raw))

    if not name or name in (".", ".."):
        raise UnsafeName(f"no usable filename in {url_or_name!r}")
    if name.startswith("."):
        # .envrc / .gitconfig / .bashrc are the payloads worth planting, and no
        # legitimate challenge artifact is a dotfile.
        raise UnsafeName(f"refusing dotfile artifact: {name!r}")
    if "/" in name or "\\" in name:
        raise UnsafeName(f"path separator in filename: {name!r}")
    if _CONTROL.search(name):
        raise UnsafeName(f"control character in filename: {name!r}")
    if len(name.encode("utf-8")) > 255:
        raise UnsafeName(f"filename too long: {name[:40]}...")
    return name


def safe_target(dirpath: Path, url_or_name: str) -> Path:
    """Absolute path to write an artifact to, provably inside `dirpath`.

    The containment check is not redundant with `safe_filename()`: asserting the
    invariant directly is cheaper than reasoning about whether basename
    stripping is airtight on every platform.
    """
    name = safe_filename(url_or_name)
    root = dirpath.resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root):
        raise UnsafeName(f"{name!r} escapes {root}")
    return target


def assert_not_symlink(path: Path) -> None:
    """A pre-existing symlink must not redirect a download out of the tree."""
    if path.is_symlink():
        raise UnsafeName(f"refusing to write through symlink: {path}")


# -- materialising ---------------------------------------------------------


def ensure_dir(path: Path) -> bool:
    """mkdir -p. Returns True if it was created, False if it already existed."""
    if path.is_dir():
        return False
    path.mkdir(parents=True, exist_ok=True)
    return True


def scaffold(dirpath: Path, filenames: list[str]) -> list[str]:
    """Create empty scaffold files. Never truncates an existing file."""
    created = []
    for fn in filenames:
        target = dirpath / safe_filename(fn)
        if not target.exists():
            target.touch()
            created.append(target.name)
    return created


def write_description(dirpath: Path, challenge) -> str | None:
    """Drop challenge.md with the platform's own text. Never overwrites."""
    target = dirpath / "challenge.md"
    if target.exists():
        return None
    parts = [f"# {challenge.name}"]
    meta = [x for x in (challenge.category, challenge.difficulty, challenge.event) if x]
    if meta:
        parts.append(" · ".join(meta))
    if challenge.author:
        parts.append(f"Created by: {challenge.author}")
    if challenge.description:
        parts.append(challenge.description)
    if challenge.endpoints:
        parts.append("## Endpoints")
        parts.append("\n".join(f"- {e.label}: {e.endpoint}" for e in challenge.endpoints))
    if challenge.hints:
        parts.append("## Hints")
        parts.append("\n".join(f"- {h}" for h in challenge.hints))
    if challenge.url:
        parts.append(f"[Challenge page]({challenge.url})")
    target.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    return target.name


# -- adopting existing directories -----------------------------------------

# Things that live in a challenge directory but are not challenge artifacts.
IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".ctftool", ".idea"}


def scan_root(ctf_root: Path, platform: str | None = None) -> list[tuple[str, str, Path]]:
    """Find existing challenge directories as (platform, slug, path).

    Reads on-disk reality. The directory name is taken as the slug verbatim —
    existing directories are never renamed, because the user abbreviates by hand
    ('Can You See Me' -> can_you_see) and that is theirs to decide.
    """
    found = []
    for pdir in sorted(p for p in ctf_root.iterdir() if p.is_dir()):
        if pdir.name in IGNORED_DIRS or pdir.name.startswith("."):
            continue
        if platform and pdir.name.lower() != platform.lower():
            continue
        for cdir in sorted(c for c in pdir.iterdir() if c.is_dir()):
            if cdir.name in IGNORED_DIRS or cdir.name.startswith("."):
                continue
            found.append((pdir.name, cdir.name, cdir))
    return found

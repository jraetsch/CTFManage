"""Downloading artifacts. Generic across platforms — Layer 1.

Every write goes through materialize.safe_target(); nothing here constructs a
path from platform data directly. See docs/ARCHITECTURE.md § Security
requirements.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .materialize import assert_not_symlink, safe_target

UA = "ctftool/0.1 (+https://github.com/; personal CTF helper)"
TIMEOUT = 60
CHUNK = 128 * 1024


class DownloadError(Exception):
    pass


@dataclass
class Result:
    filename: str
    path: Path
    size: int
    sha256: str | None
    skipped: bool = False


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def download(
    url: str,
    dirpath: Path,
    *,
    filename: str | None = None,
    force: bool = False,
    quiet: bool = False,
) -> Result:
    """Fetch one artifact into `dirpath`. Additive: never clobbers by default."""
    target = safe_target(dirpath, filename or url)

    if target.exists() and not force:
        assert_not_symlink(target)
        size = target.stat().st_size
        if not quiet:
            print(f"  = {target.name}  ({human(size)}, already present)", file=sys.stderr)
        return Result(target.name, target, size, None, skipped=True)

    assert_not_symlink(target)
    part = target.with_name(target.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": UA})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            if not quiet:
                hint = f"  ({human(total)})" if total else ""
                print(f"  ↓ {target.name}{hint}", file=sys.stderr, flush=True)
            # O_NOFOLLOW: refuse to write through a symlink planted at .part.
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(part, flags, 0o644)
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(resp, out, CHUNK)
                out.flush()
                os.fsync(out.fileno())
    except urllib.error.HTTPError as e:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{url} -> HTTP {e.code} {e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{url} -> {e}") from e

    size = part.stat().st_size
    digest = _sha256(part)
    part.replace(target)
    return Result(target.name, target, size, digest)

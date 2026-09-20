"""Downloading artifacts. Generic across platforms — Layer 1.

Every write goes through materialize.safe_target(); nothing here constructs a
path from platform data directly. See docs/ARCHITECTURE.md § Security
requirements.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .materialize import assert_not_symlink, safe_target

UA = "ctftool/0.1 (+https://github.com/; personal CTF helper)"
TIMEOUT = 60
CHUNK = 128 * 1024

# Sub-block characters for 1/8-resolution bar fill, index 0 (empty) .. 8 (full).
_SUBBLOCKS = " ▏▎▍▌▋▊▉█"
_TRACK = "░"
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_BAR_WIDTH = 24
_REDRAW_INTERVAL = 0.08  # seconds; caps redraw rate on fast/local transfers
_MIN_BAR_SIZE = 1024 * 1024  # below this, a bar just flashes and disappears
_GREEN = "\x1b[32m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_NO_COLOR = "NO_COLOR" in os.environ


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


def _fmt_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _color(s: str, code: str) -> str:
    return s if _NO_COLOR else f"{code}{s}{_RESET}"


class _ProgressBar:
    """Single-line, in-place mamba-style download bar on a real TTY.

    Smoothing is a simple EMA on bytes/sec between redraws — enough to look
    stable without pulling in a dependency for it.
    """

    def __init__(self, name: str, total: int) -> None:
        self.name = name
        self.total = total
        self.done = 0
        self.start = time.monotonic()
        self._last_draw = 0.0
        self._last_bytes = 0
        self._last_time = self.start
        self._speed = 0.0
        self._frame = 0
        self._drawn = False

    def update(self, nread: int) -> None:
        self.done += nread
        now = time.monotonic()
        is_final = bool(self.total) and self.done >= self.total
        if not is_final and now - self._last_draw < _REDRAW_INTERVAL:
            return
        dt = now - self._last_time
        if dt > 0:
            inst = (self.done - self._last_bytes) / dt
            self._speed = inst if self._speed == 0 else 0.7 * self._speed + 0.3 * inst
        self._last_bytes = self.done
        self._last_time = now
        self._last_draw = now
        self._draw()

    def _draw(self) -> None:
        self._drawn = True
        speed = f"{human(int(self._speed))}/s" if self._speed > 0 else "?/s"
        if self.total:
            frac = min(1.0, self.done / self.total)
            units = round(frac * _BAR_WIDTH * 8)
            full, rem = divmod(units, 8)
            filled = "█" * full
            if full < _BAR_WIDTH and rem:
                filled += _SUBBLOCKS[rem]
            filled = filled[:_BAR_WIDTH]
            track = _TRACK * (_BAR_WIDTH - len(filled))
            bar = _color(filled, _GREEN) + _color(track, _DIM)
            eta = _fmt_eta((self.total - self.done) / self._speed) if self._speed > 0 else "?"
            line = (f"  ↓ {self.name}  {bar}  {frac * 100:3.0f}%  "
                    f"{human(self.done)}/{human(self.total)}  {speed}  eta {eta}")
        else:
            spin = _SPINNER[self._frame % len(_SPINNER)]
            self._frame += 1
            line = f"  {spin} {self.name}  {human(self.done)}  {speed}"
        print(f"\r\x1b[2K{line}", end="", file=sys.stderr, flush=True)

    def finish(self) -> None:
        if not self._drawn:
            return
        size = human(self.total or self.done)
        print(f"\r\x1b[2K  {_color('✓', _GREEN)} {self.name}  ({size})", file=sys.stderr)

    def abort(self) -> None:
        if self._drawn:
            print(file=sys.stderr)  # move off the in-progress bar line


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

    bar = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            # Below the threshold (or unknown size) a bar draws once and is
            # immediately replaced by finish() — pure flicker, no information.
            use_bar = (not quiet) and sys.stderr.isatty() and total > _MIN_BAR_SIZE
            if use_bar:
                bar = _ProgressBar(target.name, total)
            elif not quiet:
                hint = f"  ({human(total)})" if total else ""
                print(f"  ↓ {target.name}{hint}", file=sys.stderr, flush=True)
            # O_NOFOLLOW: refuse to write through a symlink planted at .part.
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(part, flags, 0o644)
            with os.fdopen(fd, "wb") as out:
                while chunk := resp.read(CHUNK):
                    out.write(chunk)
                    if bar is not None:
                        bar.update(len(chunk))
                out.flush()
                os.fsync(out.fileno())
    except urllib.error.HTTPError as e:
        if bar is not None:
            bar.abort()
        part.unlink(missing_ok=True)
        raise DownloadError(f"{url} -> HTTP {e.code} {e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if bar is not None:
            bar.abort()
        part.unlink(missing_ok=True)
        raise DownloadError(f"{url} -> {e}") from e

    if bar is not None:
        bar.finish()

    size = part.stat().st_size
    digest = _sha256(part)
    part.replace(target)
    return Result(target.name, target, size, digest)

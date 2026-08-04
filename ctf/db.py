"""SQLite storage. Source of truth; `ctf export` produces the spreadsheet view.

Schema lives at $CTF_ROOT/.ctftool/ctf.db so it travels with the challenge tree
and can be committed alongside it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

STATUSES = ("new", "started", "stuck", "solved", "abandoned")

SCHEMA = """
CREATE TABLE IF NOT EXISTS challenge (
  id          INTEGER PRIMARY KEY,
  platform    TEXT NOT NULL,
  slug        TEXT NOT NULL,
  name        TEXT NOT NULL,
  category    TEXT,
  difficulty  TEXT,
  points      INTEGER,
  event       TEXT,
  url         TEXT,
  author      TEXT,
  description TEXT,
  hints       TEXT,           -- JSON list
  endpoints   TEXT,           -- JSON list of {label, endpoint}
  raw         TEXT,           -- JSON: whole platform record, for later mapping
  platform_id TEXT,
  status      TEXT NOT NULL DEFAULT 'new',
  flag        TEXT,
  tags        TEXT,
  path        TEXT NOT NULL,
  added_at    TEXT NOT NULL,
  solved_at   TEXT,
  notes       TEXT,
  UNIQUE(platform, slug)
);

CREATE TABLE IF NOT EXISTS artifact (
  id            INTEGER PRIMARY KEY,
  challenge_id  INTEGER NOT NULL REFERENCES challenge(id) ON DELETE CASCADE,
  url           TEXT NOT NULL,
  filename      TEXT NOT NULL,
  bytes         INTEGER,
  sha256        TEXT,
  downloaded_at TEXT,
  UNIQUE(challenge_id, filename)
);

CREATE INDEX IF NOT EXISTS idx_challenge_status   ON challenge(status);
CREATE INDEX IF NOT EXISTS idx_challenge_category ON challenge(category);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


# -- writes ----------------------------------------------------------------

# Columns that come from the platform and are safe to refresh on re-index.
# `notes`, `flag`, `status`, `tags`, `solved_at` are the user's and are never
# touched here. See docs/ARCHITECTURE.md § Data model.
_PLATFORM_COLUMNS = (
    "name", "category", "difficulty", "points", "event", "url", "author",
    "description", "hints", "endpoints", "raw", "platform_id",
)


def upsert_challenge(
    conn: sqlite3.Connection,
    *,
    platform: str,
    slug: str,
    name: str,
    path: str,
    **fields,
) -> int:
    """Insert or refresh a challenge. Returns its id.

    Only platform-owned columns are overwritten, and only when the incoming
    value is not None — so a sparse re-index never blanks existing metadata.
    """
    for key in ("hints", "endpoints", "raw"):
        if key in fields and not isinstance(fields[key], (str, type(None))):
            fields[key] = json.dumps(fields[key])

    row = conn.execute(
        "SELECT id FROM challenge WHERE platform = ? AND slug = ?",
        (platform, slug),
    ).fetchone()

    if row is None:
        cols = ["platform", "slug", "name", "path", "added_at", "status"]
        vals = [platform, slug, name, path, now(), fields.pop("status", "new")]
        for col in _PLATFORM_COLUMNS:
            if col != "name" and fields.get(col) is not None:
                cols.append(col)
                vals.append(fields[col])
        placeholders = ", ".join("?" * len(cols))
        cur = conn.execute(
            f"INSERT INTO challenge ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        conn.commit()
        return cur.lastrowid

    cid = row["id"]
    sets, vals = ["name = ?", "path = ?"], [name, path]
    for col in _PLATFORM_COLUMNS:
        if col != "name" and fields.get(col) is not None:
            sets.append(f"{col} = ?")
            vals.append(fields[col])
    vals.append(cid)
    conn.execute(f"UPDATE challenge SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    return cid


def record_artifact(
    conn: sqlite3.Connection,
    challenge_id: int,
    *,
    url: str,
    filename: str,
    size: int | None = None,
    sha256: str | None = None,
    downloaded: bool = True,
) -> None:
    conn.execute(
        """INSERT INTO artifact (challenge_id, url, filename, bytes, sha256, downloaded_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(challenge_id, filename) DO UPDATE SET
             url = excluded.url,
             bytes = COALESCE(excluded.bytes, artifact.bytes),
             sha256 = COALESCE(excluded.sha256, artifact.sha256),
             downloaded_at = COALESCE(excluded.downloaded_at, artifact.downloaded_at)""",
        (challenge_id, url, filename, size, sha256, now() if downloaded else None),
    )
    conn.commit()


def set_status(conn: sqlite3.Connection, cid: int, status: str, flag: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {', '.join(STATUSES)}")
    if status == "solved":
        conn.execute(
            "UPDATE challenge SET status = ?, solved_at = COALESCE(solved_at, ?) WHERE id = ?",
            (status, now(), cid),
        )
    else:
        conn.execute("UPDATE challenge SET status = ? WHERE id = ?", (status, cid))
    if flag:
        conn.execute("UPDATE challenge SET flag = ? WHERE id = ?", (flag, cid))
    conn.commit()


def append_note(conn: sqlite3.Connection, cid: int, text: str) -> None:
    row = conn.execute("SELECT notes FROM challenge WHERE id = ?", (cid,)).fetchone()
    existing = (row["notes"] or "").rstrip()
    stamped = f"[{now()}] {text}"
    combined = f"{existing}\n{stamped}" if existing else stamped
    conn.execute("UPDATE challenge SET notes = ? WHERE id = ?", (combined, cid))
    conn.commit()


def add_tags(conn: sqlite3.Connection, cid: int, tags: list[str]) -> str:
    row = conn.execute("SELECT tags FROM challenge WHERE id = ?", (cid,)).fetchone()
    have = [t for t in (row["tags"] or "").split(",") if t]
    for t in tags:
        if t not in have:
            have.append(t)
    joined = ",".join(have)
    conn.execute("UPDATE challenge SET tags = ? WHERE id = ?", (joined, cid))
    conn.commit()
    return joined


# -- reads -----------------------------------------------------------------


def get(conn: sqlite3.Connection, cid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM challenge WHERE id = ?", (cid,)).fetchone()


def artifacts_for(conn: sqlite3.Connection, cid: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM artifact WHERE challenge_id = ? ORDER BY filename", (cid,)
    ).fetchall()


def query(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    category: str | None = None,
    platform: str | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM challenge WHERE 1=1"
    args: list = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if category:
        sql += " AND category = ? COLLATE NOCASE"
        args.append(category)
    if platform:
        sql += " AND platform = ? COLLATE NOCASE"
        args.append(platform)
    sql += " ORDER BY platform, category IS NULL, category, name"
    return conn.execute(sql, args).fetchall()


def find(conn: sqlite3.Connection, ref: str, platform: str | None = None) -> list[sqlite3.Row]:
    """Lenient lookup: exact slug → exact name → case-insensitive substring.

    Returns as soon as a tier matches, so an exact slug is never made ambiguous
    by some other challenge that merely contains it as a substring.
    """
    base = "SELECT * FROM challenge WHERE "
    tail = " AND platform = ? COLLATE NOCASE" if platform else ""
    args_tail = [platform] if platform else []

    for clause, arg in (
        ("slug = ? COLLATE NOCASE", ref),
        ("name = ? COLLATE NOCASE", ref),
        ("(name LIKE ? OR slug LIKE ?)", None),
    ):
        if arg is None:
            like = f"%{ref}%"
            rows = conn.execute(base + clause + tail, [like, like] + args_tail).fetchall()
        else:
            rows = conn.execute(base + clause + tail, [arg] + args_tail).fetchall()
        if rows:
            return rows
    return []

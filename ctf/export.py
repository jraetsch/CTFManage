"""Bookkeeping output. The spreadsheet is a *view*, not the storage format."""

from __future__ import annotations

import csv
import io
import json

COLUMNS = [
    "platform", "slug", "name", "category", "difficulty", "points",
    "event", "status", "flag", "tags", "path", "added_at", "solved_at",
]


def to_csv(rows) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r[c] for c in COLUMNS})
    return buf.getvalue()


def to_json(rows) -> str:
    return json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False) + "\n"


_MD_COLUMNS = ["status", "name", "category", "difficulty", "platform", "solved_at"]


def _cell(value) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|")


def to_markdown(rows) -> str:
    rows = list(rows)
    out = ["# CTF status", ""]

    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    if rows:
        summary = " · ".join(f"{k}: {v}" for k, v in sorted(by_status.items()))
        out += [f"{len(rows)} challenges — {summary}", ""]

    out.append("| " + " | ".join(c.replace("_", " ") for c in _MD_COLUMNS) + " |")
    out.append("|" + "|".join(["---"] * len(_MD_COLUMNS)) + "|")
    for r in rows:
        out.append("| " + " | ".join(_cell(r[c]) for c in _MD_COLUMNS) + " |")
    return "\n".join(out) + "\n"


def render(rows, fmt: str) -> str:
    if fmt == "csv":
        return to_csv(rows)
    if fmt == "json":
        return to_json(rows)
    if fmt == "md":
        return to_markdown(rows)
    raise ValueError(f"unknown format {fmt!r}")

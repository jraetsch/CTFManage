"""picoCTF — reference platform implementation.

The listing API is behind Cloudflare *and* a login; the artifacts are on a
public CDN. So index acquisition is a one-time manual browser step and
everything after it is local. See docs/PLATFORMS.md § picoCTF.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from ..config import load as load_config
from ..config import save as save_config
from ..materialize import slugify
from ..models import Artifact, Challenge, Endpoint
from ..registry import ManualStepRequired, Platform, register

# Hosts known to serve challenge files. Adding one is a one-line change with no
# browser re-run, because the snippet captures every URL it sees and discards
# nothing. That is the whole point — see docs/PLATFORMS.md § Host classification.
ARTIFACT_HOSTS = {
    "artifacts.picoctf.net",
    "challenge-files.picoctf.net",
}

# Hosts we have seen and deliberately ignore, so they do not show up as
# "unknown host" noise on every index run.
KNOWN_NON_ARTIFACT_HOSTS = {
    "play.picoctf.org",
    "picoctf.org",
    "www.picoctf.org",
    "primer.picoctf.org",
}


def classify(urls: list[str], hosts: set[str] | None = None) -> tuple[list[str], list[str]]:
    """-> (artifacts, unknown_picoctf_hosts). Everything else is dropped.

    `hosts` defaults to the built-in set; refresh_index() passes the built-in
    set plus anything the user has confirmed, so a newly accepted host takes
    effect in the same run that discovered it.
    """
    if hosts is None:
        hosts = ARTIFACT_HOSTS
    artifacts, suspect = [], []
    for u in urls:
        host = urlparse(u).netloc.lower()
        if host in hosts:
            if u not in artifacts:
                artifacts.append(u)
        elif host in KNOWN_NON_ARTIFACT_HOSTS:
            continue
        elif host.endswith("picoctf.net") or host.endswith("picoctf.org"):
            if u not in suspect:
                suspect.append(u)
    return artifacts, suspect


CONSOLE_SNIPPET = r"""
// 1. Log in to https://play.picoctf.org and open the practice page.
// 2. Press F12 -> Console, paste this, press Enter.
// 3. It takes a few minutes and downloads picoctf-index.json when done.

const PAGE_SIZE = 100;
const MAX_PAGES = 200;          // runaway guard, not an expected limit
const sleep = ms => new Promise(r => setTimeout(r, ms));
const j = async u => (await fetch(u, {headers: {accept: 'application/json'}})).json();

const listed = [];
const seen = new Set();
let expected = null;

for (let page = 1; page <= MAX_PAGES; page++) {
  const r = await j(`/api/challenges/?page_size=${PAGE_SIZE}&page=${page}`);
  if (page === 1) {
    // Print the real shape once: everything downstream is inference until
    // somebody looks at this line.
    console.log('response keys:',
      Array.isArray(r) ? '(bare array)' : Object.keys(r).join(', '));
    expected = r.count ?? r.total ?? r.total_count ?? null;
    if (expected !== null) console.log(`server reports ${expected} challenges`);
  }
  const items = Array.isArray(r)
    ? r
    : (r.results ?? r.data ?? r.challenges ?? r.items ?? []);
  if (!items.length) break;

  let added = 0;
  for (const c of items) {
    const key = c.id ?? c.pk ?? c.name;
    if (!seen.has(key)) { seen.add(key); listed.push(c); added++; }
  }
  console.log(`  page ${page}: ${items.length} items, ${added} new ` +
              `(${listed.length} total)`);

  // Stop on a SHORT page, never on a missing field. An absent `next` means
  // "shape I did not predict", not "no more pages" — assuming otherwise
  // capped this at exactly one page.
  if (items.length < PAGE_SIZE) break;
  if (added === 0) break;                 // page param ignored: same page again
  await sleep(150);
}

if (expected !== null && listed.length < expected) {
  console.warn(`WARNING: listed ${listed.length} of ${expected} — pagination ` +
               `stopped early, check the page params above`);
}
console.log(`${listed.length} challenges listed, fetching details...`);

const out = [];
const failed = {main: 0, instance: 0};
let lastError = null;

for (const [i, c] of listed.entries()) {
  const id = c.id ?? c.pk;
  let main = c, instance = null;
  // Failures are counted, never swallowed. A rate limit partway through would
  // otherwise yield a full-looking index whose descriptions — and therefore
  // whose artifact URLs — are missing.
  try { main = await j(`/api/challenges/${id}/`); }
  catch (e) { failed.main++; lastError = e; }
  try { instance = await j(`/api/challenges/${id}/instance/`); }
  catch (e) { failed.instance++; lastError = e; }
  const rec = {...main, _instance: instance};
  // Capture EVERY url. Classification happens in Python, not here.
  rec._urls = [...new Set(
    JSON.stringify(rec).match(/https?:\/\/[^"'\\ )<>\]]+/g) || []
  )];
  out.push(rec);
  if (i % 25 === 0) console.log(`  ${i}/${listed.length}`);
  await sleep(120);          // be polite; this runs once a year
}

// How many records actually carry a description? Artifact URLs live inside it,
// so this number is the real predictor of whether the index is usable.
const withDesc = out.filter(r => (r._instance && r._instance.description)
                                 || r.description).length;
const withUrls = out.filter(r => r._urls && r._urls.length).length;
console.log(`descriptions: ${withDesc}/${out.length}   with URLs: ${withUrls}`);
if (failed.main || failed.instance) {
  console.warn(`WARNING: ${failed.main} detail and ${failed.instance} instance ` +
               `requests failed — the index is incomplete. Last error:`, lastError);
  console.warn('Re-run after a pause, or raise the sleep() above.');
}
// Print one record's field names: the Python mapping in normalise() is written
// against these, and nobody had seen them until this line ran.
if (out.length) {
  console.log('challenge fields:', Object.keys(out[0]).join(', '));
  if (out[0]._instance)
    console.log('instance fields:', Object.keys(out[0]._instance).join(', '));
}

const blob = new Blob([JSON.stringify(
  {_ctftool_index: 1, fetched_at: new Date().toISOString(), challenges: out},
  null, 2)], {type: 'application/json'});
const a = document.createElement('a');
a.href = URL.createObjectURL(blob);
a.download = 'picoctf-index.json';
a.click();
console.log(`done: ${out.length} challenges`);
""".strip()


# -- field mapping ---------------------------------------------------------
# VERIFIED 2026-08-04 against the live API (525 challenges). Actual fields:
#
#   challenge: id, name, author, difficulty, event, category, tags, sponsor,
#              include_in_gym, rating_count, positive_rating_count,
#              users_solved, users_solved_during_event, event_points,
#              solved_by_user, solved_by_team, under_maintenance, bookmarked,
#              errata, active_assignments, retired
#   instance:  id, status, expires_in, description, hints, on_demand, endpoints
#
# Note there is NO `points` field — it is `event_points`. Accesses stay
# defensive anyway: a rename should degrade to a NULL column, not a traceback.


def _int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("name") or value.get("title") or None
    return str(value) or None


def _hints(rec: dict, inst: dict) -> list[str]:
    raw = inst.get("hints") or rec.get("hints") or []
    out = []
    for h in raw:
        if isinstance(h, dict):
            h = h.get("hint") or h.get("body") or h.get("text") or ""
        if h:
            out.append(str(h))
    return out


def _endpoints(rec: dict, inst: dict) -> list[Endpoint]:
    raw = inst.get("endpoints") or rec.get("endpoints") or []
    out = []
    for e in raw:
        if isinstance(e, dict):
            label = str(e.get("label") or e.get("name") or "endpoint")
            value = e.get("endpoint") or e.get("url") or e.get("value")
            if value:
                out.append(Endpoint(label, str(value)))
        elif e:
            out.append(Endpoint("endpoint", str(e)))
    return out


def normalise(rec: dict, hosts: set[str] | None = None) -> dict:
    """One platform record -> the normalised index entry we store on disk."""
    inst = rec.get("_instance") or {}
    urls = rec.get("_urls") or []
    artifacts, suspect = classify(urls, hosts)
    cid = rec.get("id") or rec.get("pk")
    name = rec.get("name") or rec.get("title") or ""
    return {
        "platform_id": str(cid) if cid is not None else None,
        "name": name,
        "category": _text(rec.get("category")),
        "difficulty": _text(rec.get("difficulty")),
        # `event_points`, not `points` — there is no `points` field (verified).
        "points": _int(rec.get("event_points") or rec.get("points")),
        "event": _text(rec.get("event")) or _text(rec.get("competition")),
        "author": _text(rec.get("author")),
        "description": inst.get("description") or rec.get("description"),
        "url": f"https://play.picoctf.org/practice/challenge/{cid}" if cid else None,
        "artifacts": artifacts,
        "endpoints": [{"label": e.label, "endpoint": e.endpoint}
                      for e in _endpoints(rec, inst)],
        "hints": _hints(rec, inst),
        # Platform-side flags worth keeping so they need no second browser run.
        # `tags` stays out of the DB `tags` column, which is the user's own —
        # see docs/ARCHITECTURE.md § Data model.
        "tags": [str(t) for t in (rec.get("tags") or []) if t],
        "retired": bool(rec.get("retired")),
        "on_demand": bool(inst.get("on_demand")),
        "solved_on_platform": bool(rec.get("solved_by_user")),
        "users_solved": _int(rec.get("users_solved")),
        "_suspect_urls": suspect,
        "raw": rec,
    }


def _try_slug(name: str) -> str | None:
    """slugify() that returns None instead of raising, for match-only use."""
    from ..materialize import UnsafeName
    try:
        return slugify(name)
    except UnsafeName:
        return None


def _to_challenge(entry: dict) -> Challenge:
    return Challenge(
        platform=PicoCTF.name,
        name=entry.get("name") or "",
        artifacts=[Artifact(u) for u in entry.get("artifacts", [])],
        endpoints=[Endpoint(e.get("label", "endpoint"), e.get("endpoint", ""))
                   for e in entry.get("endpoints", [])],
        hints=list(entry.get("hints") or []),
        category=entry.get("category"),
        difficulty=entry.get("difficulty"),
        points=entry.get("points"),
        event=entry.get("event"),
        url=entry.get("url"),
        description=entry.get("description"),
        author=entry.get("author"),
        platform_id=entry.get("platform_id"),
        tags=list(entry.get("tags") or []),
        retired=bool(entry.get("retired")),
        on_demand=bool(entry.get("on_demand")),
        solved_on_platform=bool(entry.get("solved_on_platform")),
        raw=entry.get("raw"),
    )


@register
class PicoCTF(Platform):
    name = "picoCTF"          # MUST equal the directory name under $CTF_ROOT
    url = "https://play.picoctf.org"

    # -- index ------------------------------------------------------------

    def _index_path(self) -> Path:
        cfg = load_config(required=False)
        if cfg:
            return cfg.index_path(self.name)
        from ..config import index_dir
        return index_dir() / "picoctf.json"

    def _load_index(self) -> list[dict]:
        path = self._index_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ManualStepRequired(
                reason=f"index at {path} is not valid JSON ({e})",
                instructions=CONSOLE_SNIPPET,
                resume_with=f"ctf index picoCTF --from-file ~/Downloads/picoctf-index.json",
            ) from e
        return data.get("challenges", []) if isinstance(data, dict) else data

    def index_status(self) -> str:
        path = self._index_path()
        if not path.exists():
            return "missing — run `ctf index picoCTF`"
        try:
            n = len(self._load_index())
        except Exception:
            return f"unreadable ({path})"
        return f"{n} challenges ({path})"

    # -- artifact hosts ----------------------------------------------------

    CONFIG_KEY = "artifact_hosts"

    def configured_hosts(self) -> list[str]:
        """Extra artifact hosts the user has confirmed, from config.toml."""
        cfg = load_config(required=False)
        if not cfg:
            return []
        return list(cfg.platforms.get(self.name, {}).get(self.CONFIG_KEY, []))

    def known_hosts(self) -> set[str]:
        return ARTIFACT_HOSTS | set(self.configured_hosts())

    def remember_hosts(self, hosts: list[str]) -> Path | None:
        """Persist newly accepted hosts to config.toml.

        Deliberately not written back into ARTIFACT_HOSTS in the source: the
        tool must not edit its own checkout, and a user's discovery is user
        data. The built-in set stays the shipped default.
        """
        if not hosts:
            return None
        cfg = load_config(required=False)
        if not cfg:
            return None
        opts = cfg.platforms.setdefault(self.name, {})
        merged = list(dict.fromkeys(list(opts.get(self.CONFIG_KEY, [])) + hosts))
        opts[self.CONFIG_KEY] = merged
        return save_config(cfg)

    # -- index build -------------------------------------------------------

    def refresh_index(self, source: Path | None = None, on_unknown_host=None) -> int:
        if source is None:
            raise ManualStepRequired(
                reason="play.picoctf.org/api is behind Cloudflare and requires a login.",
                instructions=CONSOLE_SNIPPET,
                resume_with="ctf index picoCTF --from-file ~/Downloads/picoctf-index.json",
            )
        raw = json.loads(Path(source).expanduser().read_text(encoding="utf-8"))
        records = raw.get("challenges", []) if isinstance(raw, dict) else raw
        if isinstance(records, dict):        # tolerate a {name: record} dump
            records = list(records.values())

        hosts = self.known_hosts()

        # First pass: find unrecognised picoCTF hosts before writing anything,
        # so an accepted host is applied in this same run rather than needing a
        # second index build.
        suspects: dict[str, list[str]] = {}
        for rec in records:
            _, suspect = classify(rec.get("_urls") or [], hosts)
            for u in suspect:
                suspects.setdefault(urlparse(u).netloc.lower(), []).append(u)

        accepted = []
        if suspects and on_unknown_host is not None:
            for host, urls in sorted(suspects.items(), key=lambda kv: -len(kv[1])):
                if on_unknown_host(host, urls):
                    accepted.append(host)
            if accepted:
                hosts = hosts | set(accepted)
                written = self.remember_hosts(accepted)
                if written:
                    print(f"added {len(accepted)} host(s) to {written}", file=sys.stderr)
        elif suspects:
            # No way to ask (not a tty, or a caller that does not prompt).
            # Stay loud — silence is the failure mode this guards against.
            for host, urls in sorted(suspects.items()):
                print(f"warning: {len(urls)} URLs on unseen host {host!r} — "
                      f"re-run `ctf index {self.name}` on a terminal to add it",
                      file=sys.stderr)
                print(f"         e.g. {urls[0]}", file=sys.stderr)

        # Second pass: build entries with the final host set.
        entries, empty, still_suspect = [], 0, 0
        for rec in records:
            entry = normalise(rec, hosts)
            if not entry["name"]:
                continue
            still_suspect += len(entry.pop("_suspect_urls", []))
            if not entry["artifacts"] and not entry["endpoints"]:
                empty += 1
            entries.append(entry)

        out = self._index_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"_ctftool_index": 1, "platform": self.name, "challenges": entries},
            indent=2, ensure_ascii=False), encoding="utf-8")

        if still_suspect:
            print(f"note: {still_suspect} URLs left on hosts you declined",
                  file=sys.stderr)
        if empty:
            print(f"note: {empty}/{len(entries)} challenges have neither artifacts "
                  f"nor endpoints (likely description-only)", file=sys.stderr)
        return len(entries)

    def catalogue(self) -> list[Challenge]:
        return [_to_challenge(e) for e in self._load_index()]

    # -- resolution -------------------------------------------------------

    def resolve(self, ref: str) -> list[Challenge]:
        entries = self._load_index()
        if not entries:
            return []
        needle = ref.strip().lower()
        slug_needle = _try_slug(ref)

        exact = [e for e in entries
                 if (e.get("name") or "").lower() == needle
                 or str(e.get("platform_id")) == ref
                 or (slug_needle and _try_slug(e.get("name") or "") == slug_needle)]
        if exact:
            return [_to_challenge(e) for e in exact]

        partial = [e for e in entries if needle in (e.get("name") or "").lower()]
        return [_to_challenge(e) for e in partial]

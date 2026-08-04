# Build log — resumable

Purpose: this file is the handoff. If a session is interrupted, a fresh Claude
instance reads `CLAUDE.md`, then this file, and continues at the first module
that is not `DONE`.

Build order is `docs/ARCHITECTURE.md` § Build order.

## Status

**All modules implemented. 53 tests passing. End-to-end verified against the
live picoCTF CDN, and index acquisition verified against the real API
(525 challenges).**

| # | Module | State | Notes |
|---|---|---|---|
| 0 | `pyproject.toml`, `ctf/__init__.py`, `ctf/__main__.py` | **DONE** | entry point `ctf`; runs uninstalled via `python3 -m ctf` |
| 0 | `ctf/models.py` | **DONE** | `Challenge`, `Artifact`, `Endpoint`, `has_content` |
| 1 | `ctf/config.py` | **DONE** | reads TOML via tomllib, hand-rolled writer |
| 1 | `ctf/db.py` | **DONE** | schema, upsert, lenient `find()` |
| 1 | `ctf init` | **DONE** | |
| 2 | `ctf/materialize.py` — `slugify` | **DONE** | 6 tests incl. adversarial |
| 2 | `ctf/materialize.py` — `safe_target` | **DONE** | 5 tests incl. symlink + traversal |
| 3 | `ctf adopt` | **DONE** | `--dry-run`; backfills metadata once an index exists |
| 4 | `ctf list` / `ctf export` | **DONE** | csv / md / json |
| 5 | `ctf/registry.py` + `platforms/picoctf.py` | **DONE** | + `platforms/ctflearn.py` stub |
| 6 | `ctf index picoCTF` | **DONE** | console snippet + `--from-file` |
| 7 | `ctf/fetch.py` + `ctf get` | **DONE** | O_NOFOLLOW, .part+fsync+rename, sha256 |

Also done: `show`, `start`/`stuck`/`abandon`, `solve`, `note`, `tag`, `path`,
`cd`, `open`, `platforms`.

## Verified end to end

Sandbox run with `XDG_CONFIG_HOME` redirected (the user's real config and CTF
root were not touched):

- `init` → `adopt --dry-run` → `adopt` → `list` → `export`, all with no network.
- `index picoCTF` with no `--from-file` prints the console snippet, exits 0.
- `index picoCTF --from-file tests/fixtures/picoctf-browser-dump.json` → 4
  indexed, **and warned** about the planted `files2.picoctf.net` host.
- `get "Glory of the Garden"` → real 5.0 KB download from
  `artifacts.picoctf.net`.
- `get "Timestamped Secrets"` → real 135 B download from
  `challenge-files.picoctf.net`. **Both hosts work.**
- `get "Sum-O-Primes"` (endpoints, no files) → exit **0**, not an error.
- `get "Future Host Challenge"` (no artifacts, no endpoints) → exit **1**, loud.
- Re-running `get` → `= Flag.pdf (already present)`, nothing re-downloaded.
- `$(ctf get …)` captures **exactly** the path; progress stayed on stderr.
- Re-running `adopt` after `solve` preserved status, flag, tags and notes.

## Deviations from the docs, decided during implementation

Fold these back into `docs/` — the code is now ahead of the spec.

1. **`Challenge.has_content` replaces the zero-artifact rule.** `ctf get` fails
   only when there are neither artifacts *nor* endpoints. The original rule
   would have failed every `nc host port` pwn challenge. From the picoctf-dl
   analysis.
2. **`description` is read from `_instance`, not the challenge record.** The
   browser snippet is now two-phase: list, then `/api/challenges/<id>/` plus
   `/api/challenges/<id>/instance/` per challenge, throttled 120 ms. The old
   single-call snippet would have produced an index with **zero artifact URLs
   for every challenge**, because the URLs live inside the description.
3. **Schema gained `author`, `hints`, `endpoints`, `raw`, `platform_id`.**
   `hints`/`endpoints`/`raw` are JSON columns — deferring the modelling rather
   than inventing tables for data whose shape is still a 2022-vintage guess.
4. **`slugify` prefixes a leading digit** (`2warm` → `c_2warm`).
5. **The containment check in `safe_target()` is currently unreachable**, since
   basename-stripping happens first. Kept deliberately, and
   `test_traversal_is_neutralised_not_escaped` documents why: a refactor that
   stops stripping must fail loudly rather than write outside the tree.
6. **`adopt` does not register pre-existing files as artifacts.** The artifact
   table means "the tool downloaded this"; guessing would make it meaningless.
7. **`KNOWN_NON_ARTIFACT_HOSTS`** suppresses `picoctf.org` / `play.picoctf.org`
   so the unknown-host warning stays signal.

## Known gaps

- ~~The console snippet has never been run against the live API.~~ **Closed
  2026-08-04.** Run against the real API: 525 challenges over 6 pages, response
  shape `{results, count}` with no `next` field, and the full record schema is
  now recorded in `docs/PLATFORMS.md`. Two bugs it surfaced, both silent:
  pagination capped the index at one page, and `points` was read from a field
  that does not exist (`event_points`). The prediction that `normalise()` would
  be the only place needing an edit held.
- **Artifact counts after classification are still unmeasured.** The snippet
  reports 322/525 records carrying *any* URL; how many survive host
  classification as real artifacts is not yet known. Compare against the
  `challenges have neither artifacts nor endpoints` line on the next
  `ctf index`.
- `ctfLearn` is a stub that raises `ManualStepRequired`. Adoption of existing
  ctfLearn directories works regardless.
- No `ctf rm`/`ctf archive`. Deleting is the user's job by design.
- `export --format csv` writes UTF-8 without BOM; Excel may need an import step.
  LibreOffice is fine.

## How to verify what exists

```bash
cd ~/src/ctftool
python3 -m unittest discover -s tests -t .     # 53 tests
python3 -m ctf --help                          # runs without installing
```

# ctftool — orientation for a fresh Claude instance

Read this first, then `docs/ARCHITECTURE.md`, then `docs/PLATFORMS.md`.
`README.md` is the user-facing quickstart and is the least important for you.

## What this is

A personal CLI for one user (jannes) that turns "I found a challenge in my
browser" into "the folder exists, the files are in it, and it's tracked".

    ctf get "Glory of the Garden"
      → ~/workspace/ctfs/picoCTF/glory_of_the_garden/ created
      → artifacts downloaded into it
      → row inserted in the tracking DB

Plus bookkeeping (`ctf list`, `ctf solve`, `ctf export`) and a platform plugin
layer so new CTF sites can be added without touching the core.

## Status

**Design only. No code has been written yet.** These documents are the spec.
If you are picking this up, you are probably implementing it for the first time.
Start with `docs/ARCHITECTURE.md` § Build order.

## The one insight that drives the whole design

Investigated live on 2026-08-04 against picoCTF, and it generalises:

> On most CTF platforms the **challenge listing is behind auth and bot
> protection, but the challenge artifacts are on a public, unauthenticated CDN.**

Concretely for picoCTF: `play.picoctf.org/api/challenges/` returns **403** to
curl (Cloudflare managed challenge, browser User-Agent does not help), while
`artifacts.picoctf.net/c/500/files.zip` returns **200** to a bare `curl` with no
cookies at all.

So the expensive, fragile, platform-specific part (getting the listing) is
**rare and cacheable**, and the frequent part (downloading files) is **trivial
and shared**. The architecture is built around that split — see the three-layer
model in `docs/ARCHITECTURE.md`. Do not design as if every `ctf get` must talk
to an authenticated API; it must not.

## Hard-won facts — do not re-derive these

Verified by direct probing on 2026-08-04:

| Fact | Evidence |
|---|---|
| picoCTF artifact hosts are public — no auth, no Cloudflare | `curl` with no cookies → 200 on four URLs across two hosts |
| There is **more than one** artifact host | `artifacts.picoctf.net` and `challenge-files.picoctf.net` both confirmed serving |
| `play.picoctf.org/api/` is Cloudflare-walled | 403 with plain curl **and** with a Chrome User-Agent |
| No public JSON index of picoCTF challenges exists | web search found none; every writeup hardcodes URLs |
| Artifact host *and* path shape vary by era/event | `/c/500/…`, `/c_mimas/75/…`, `/c_plain_mesa/<sha256>/…` — do **not** hardcode either |

### The host assumption was already wrong once

The first draft of this design assumed `artifacts.picoctf.net` was the only
artifact host and hardcoded it into the index-extraction regex. The user then
looked at a real challenge ("Timestamped Secrets") served from
`challenge-files.picoctf.net`, which that regex does not match. The failure mode
would have been **silent**: zero artifacts extracted, no error, an empty folder
reported as success.

The structural fix — capture all URLs in the browser snippet, classify hosts in
Python, warn on unknown hosts, fail loudly on zero artifacts — is in
`docs/PLATFORMS.md`. The general principle it encodes:

> **The component that is expensive to re-run must contain no judgement.**

The snippet needs a browser, a login and a human, so it only captures. Every
decision that might turn out wrong lives in Python, where fixing it is a
one-line edit and no manual step.

The workaround for the Cloudflare wall is a one-time DevTools-console fetch that
runs same-origin inside the user's logged-in browser. The snippet is in
`docs/PLATFORMS.md` § picoCTF. It is a **supported workflow step**, not a hack —
treat it as first-class.

## Things that are NOT verified

Be honest about these; do not write code that silently assumes them.

- **The exact shape of the picoCTF API JSON.** Nobody has logged in yet. The
  index builder must be defensive (`r.results ?? r`) and the artifact extractor
  is deliberately a regex over the whole serialized record, so it works
  regardless of field names. Keep it that way.
- **How ctfLearn's listing works.** Completely uninvestigated. The `ctflearn`
  platform is a stub.
- Whether the user wants xlsx. They said "maybe as a spreadsheet?" — the plan
  is CSV + Markdown export, no xlsx dependency unless asked.

## Conventions that come from the user's existing filesystem

These were read off `~/workspace/ctfs/` — they are observed reality, not
preference guesses. Match them; do not impose a tidier scheme.

- Layout is `$CTF_ROOT/<Platform>/<slug>/` — already how the user organises.
- Platform directory names use the platform's own branding casing: `picoCTF`,
  `ctfLearn`. Not lowercase.
- Slugs are `snake_case`: `glory_of_the_garden`, `format_string_0`, `disko_2`.
- **Artifacts live flat in the challenge directory**, mixed with solve files.
  There is no `artifacts/` subdirectory. Do not add one.
- The solve convention is a `sol.txt` containing the commands used, e.g.
  `strings disko-1.dd -n 8 | grep "picoCTF"`. Scaffold that filename, not
  `notes.md`.
- Per-challenge `.venv/` and `solver.py` appear where needed. Ignore them.

Because artifacts sit flat among the user's own files, the DB records which
files were downloaded — that is how "original vs mine" stays answerable without
changing the layout.

## Motivating bug

`picoCTF/disko_1/` and `picoCTF/disko1/` both exist, with overlapping contents.
Manual folder creation has already caused drift. Deterministic slugification is
a real requirement, not polish.

## Working agreements

- **Python 3.12, standard library only.** `sqlite3`, `urllib`, `argparse`,
  `csv`, `tomllib`, `dataclasses` cover everything. No pip install step.
- The user originally asked for a shell tool. It became Python when bookkeeping
  entered scope; shell + `jq` for a queryable status table is a bad trade. A
  thin zsh function still provides `cd` (a child process cannot chdir the
  parent shell — see `docs/ARCHITECTURE.md` § Shell integration).
- Never delete or overwrite anything under `$CTF_ROOT` that the tool did not
  create. `ctf get` on an existing directory is additive and skips files that
  are already present.
- The user's 24 existing challenge directories must be importable — see
  `ctf adopt`. A tool that only works for new challenges solves half the problem.
- **Artifact filenames and slugs are attacker-influenced input.** They come from
  a JSON index and from URLs embedded in challenge descriptions. `safe_target()`
  in `fetch.py` and `slugify()` in `materialize.py` are the two functions where
  that input reaches the filesystem, and both need adversarial tests. The rules
  are in `docs/ARCHITECTURE.md` § Security requirements — read it before writing
  `fetch.py`. The artifacts themselves are hostile by design (they are malware
  samples); the tool places them on disk and never executes or unpacks them.

## The zsh wrapper was audited, and it is fine

Asked and answered on 2026-08-04, so do not re-litigate it: the `cd` wrapper is
inert to define, fails closed, does not alter PATH resolution, and `cd` executes
nothing on this machine (no `chpwd` hooks, no `direnv`, no `.` in `PATH`).
Details and the two hardening tweaks that were adopted (`cd --`, absolute-path
guard) are in `docs/ARCHITECTURE.md` § Why the wrapper is not a security
surface. The one live caveat is recorded there: installing `direnv` would make a
downloaded `.envrc` meaningful.

## Open questions for the user

Flagged rather than silently decided:

1. Is `sol.txt` scaffolding wanted on `ctf get`, or only on `ctf start`?
2. ctfLearn: how does the user currently get files from it? Determines whether
   its index needs the same console-snippet treatment.

Resolved:

- `$CTF_ROOT/.ctftool/` (the DB) must **not** be git-committed, even if
  challenge files under `$CTF_ROOT` end up in a repo (2026-09-20). If
  auto-commit or `git init` support is ever added for `$CTF_ROOT`, it must
  write/extend a `.gitignore` entry for `.ctftool/` rather than committing it —
  `ctf.db` is SQLite, so its diffs are opaque binary blobs anyway.

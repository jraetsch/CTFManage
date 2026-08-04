# Architecture

## Three-layer model

The design follows from the observation in `CLAUDE.md`: listings are gated,
artifacts are not. Each layer has a different cost and a different change rate.

```
┌─ Layer 3 ── INDEX ACQUISITION ──────────────── platform-specific, auth-heavy, RARE
│  Get the platform's challenge catalogue into a local file.
│  May require a manual browser step. Runs maybe once a year.
│  Output: ~/.config/ctftool/index/<platform>.json
│
├─ Layer 2 ── RESOLUTION ─────────────────────── platform-specific, local, FAST
│  "Glory of the Garden" → Challenge(name, category, artifacts=[urls...])
│  Pure lookup against the local index. No network. No auth.
│
└─ Layer 1 ── FETCH + MATERIALIZE ────────────── generic, SHARED
   mkdir, download artifact URLs, scaffold sol.txt, write DB row.
   Identical for every platform. Written once.
```

A new platform implements Layer 3 and Layer 2 only. Layer 1 is inherited free.
This is the answer to requirement (3), "easy to add new platforms".

## Repository layout

The tool lives **outside** the CTF root, so the CTF root contains only
challenges and does not gain a directory that looks like a platform.

It is a *working copy*, not an installed artifact: `~/.local/bin/ctf` is a
three-line launcher that puts this directory on `PYTHONPATH` and runs it in
place, so edits take effect immediately and there is no install step. That is
why it lives in `~/src` rather than under `~/.local/share` — see README § Install.

```
~/src/ctftool/                ← this repo
  ctf/
    __init__.py
    cli.py           argparse subcommands, output formatting, exit codes
    config.py        load/write ~/.config/ctftool/config.toml
    models.py        Challenge, Artifact dataclasses
    registry.py      platform discovery + lookup
    db.py            schema, migrations, queries
    fetch.py         downloading: skip-existing, resume, sha256, progress
    materialize.py   slugify, mkdir, sol.txt scaffold, adopt-scan
    export.py        csv / markdown / json writers
    platforms/
      __init__.py    auto-imports every sibling module so @register fires
      picoctf.py     reference implementation
      ctflearn.py    stub — see docs/PLATFORMS.md
  docs/
  README.md
  CLAUDE.md

~/workspace/ctfs/             ← $CTF_ROOT, user data, untouched by the repo
  .ctftool/
    ctf.db                    tracking database
    STATUS.md                 optional generated overview
  picoCTF/
    glory_of_the_garden/
  ctfLearn/
    forensics_101/
```

The DB sits in `$CTF_ROOT/.ctftool/` rather than `~/.config` deliberately: it
describes the challenge tree, so it should travel with it and can be committed
to git alongside. The *index* cache stays in `~/.config/ctftool/index/` because
it is derived data that can always be rebuilt.

## Data model

SQLite, at `$CTF_ROOT/.ctftool/ctf.db`.

Chosen over a CSV or JSON file as source of truth because bookkeeping needs
filtered queries (`--status`, `--category`), in-place status mutation over time,
and no risk of a half-written file if the tool is interrupted mid-download. The
spreadsheet the user asked for is a **view**, produced by `ctf export`, not the
storage format. `sqlite3` is stdlib, so this costs nothing.

```sql
CREATE TABLE challenge (
  id          INTEGER PRIMARY KEY,
  platform    TEXT NOT NULL,          -- 'picoCTF' (branding casing, = dir name)
  slug        TEXT NOT NULL,          -- 'glory_of_the_garden' (= dir name)
  name        TEXT NOT NULL,          -- 'Glory of the Garden' (display)
  category    TEXT,                   -- 'Forensics'
  difficulty  TEXT,
  points      INTEGER,
  event       TEXT,                   -- 'picoCTF 2023' / 'picoGym'
  url         TEXT,                   -- challenge page, for `ctf open`
  description TEXT,                   -- verbatim from the platform (see below)
  status      TEXT NOT NULL DEFAULT 'new',
  flag        TEXT,
  tags        TEXT,                   -- free-form, comma-separated
  path        TEXT NOT NULL,          -- relative to $CTF_ROOT
  added_at    TEXT NOT NULL,          -- ISO 8601
  solved_at   TEXT,
  notes       TEXT,                   -- the user's own notes, never overwritten
  UNIQUE(platform, slug)
);

CREATE TABLE artifact (
  id            INTEGER PRIMARY KEY,
  challenge_id  INTEGER NOT NULL REFERENCES challenge(id) ON DELETE CASCADE,
  url           TEXT NOT NULL,
  filename      TEXT NOT NULL,
  bytes         INTEGER,
  sha256        TEXT,
  downloaded_at TEXT,
  UNIQUE(challenge_id, filename)
);
```

`status` is a small closed set: `new | started | stuck | solved | abandoned`.

`description` and `notes` are separate on purpose: the first is platform data
and is refreshed by re-indexing, the second is the user's and is never written
by anything except `ctf note`.

### Metadata provenance

Category, difficulty, points, event and description all arrive in the **same
index record that carries the artifact URLs** — there is no second lookup. The
artifact URLs are embedded *inside* the description text, so a platform that can
give you files can always give you the category too.

This is why the index snippet in `docs/PLATFORMS.md` stores `{...c}` — the
entire challenge record — rather than a hand-picked subset. Field names are
unverified, so `refresh_index()` maps them to the schema in **one place** in
Python, which is the only thing to fix when the real shape is known.

If a platform genuinely lacks a category, leave it `NULL` and let the user set
one with `ctf tag`. Do not guess it from the challenge name.

Challenges imported by `ctf adopt` have `NULL` category until an index exists to
match them against — adopt reads directories, and a directory has no metadata.
Re-running `ctf adopt` after `ctf index` backfills them.

The `artifact` table earns its place: because artifacts sit flat alongside the
user's own `solver.py` and `sol.txt`, it is otherwise impossible to answer
"which of these files came from the platform?" It also makes re-running
`ctf get` cheap — known filenames are skipped.

## Configuration

`~/.config/ctftool/config.toml`, created by `ctf init`.

```toml
ctf_root = "/home/jannes/workspace/ctfs"
layout   = "{platform}/{slug}"   # matches the user's existing tree
scaffold = ["sol.txt"]           # files created empty on `ctf get`
write_description = true         # also drop challenge.md with the description
default_platform = "picoCTF"     # used when --platform is omitted

[platforms.picoCTF]
index = "~/.config/ctftool/index/picoctf.json"
```

## Slugification

The `disko_1` / `disko1` drift makes this load-bearing. One function,
`materialize.slugify()`, is the only place directory names are produced:

```
lowercase → strip non-alphanumeric to spaces → collapse runs → join with '_'
"Glory of the Garden"  → glory_of_the_garden
"Disko 1"              → disko_1
"format-string-0"      → format_string_0
"can you see?"         → can_you_see
```

**Unverified:** whether this reproduces the user's existing directory names is
*not* established, because only the directory names are known — the original
challenge names they were derived from are not, and they require the index
(which requires the login step). There is already evidence of divergence:
picoCTF's challenge is "Can You See Me" but the directory is `can_you_see`, so
the user abbreviates by hand. `Coppersmith` is capitalised against the
convention, and `disko1` is the drift artifact.

Therefore: **never rename an existing user directory.** `ctf adopt` records the
on-disk name as the slug and stores the platform's name in `name`, so a
hand-abbreviated directory keeps working and still links to its catalogue entry.
Slugify governs *new* directories only.

Once an index exists, add a check that reports name→slug mismatches against the
existing tree so the user can decide case by case. Do not auto-resolve them.

## Command surface

```
ctf init                              interactive first-run: set ctf_root, create DB
ctf get <ref> [--platform P]          THE main command: (1a)(1b)(1c)
        [--force] [--no-download] [--dry-run]
ctf adopt [--platform P] [--dry-run]  backfill existing directories into the DB
ctf index <platform> [--from-file F]  Layer 3; prints instructions if manual
ctf list [--status S] [--category C] [--platform P] [--json]
ctf show <ref>                        one challenge in detail, incl. artifacts
ctf start|stuck|abandon <ref>
ctf solve <ref> [--flag F]            sets status=solved, solved_at=now
ctf note <ref> <text>                 append to notes
ctf tag <ref> <tags...>
ctf export [--format csv|md|json] [-o FILE]
ctf path <ref>                        print absolute path — for the cd shim
ctf open <ref>                        xdg-open the challenge URL
ctf platforms                         list registered platforms + index status
```

`<ref>` is resolved leniently in this order: exact slug → exact name →
case-insensitive substring of name. Multiple matches print the candidates and
exit `2` rather than guessing.

Exit codes: `0` ok, `1` error, `2` ambiguous ref, `3` not found in index
(message suggests `ctf index <platform>`), `4` network failure.

## `ctf get` flow

```
resolve ref via registry
   ↓  (0 matches → exit 3 "not in index, try: ctf index picoCTF")
   ↓  (>1 matches → exit 2, list candidates)
slugify name → dir = $CTF_ROOT/<Platform>/<slug>/
   ↓
mkdir -p  (existing dir is fine — additive, never cleared)
   ↓
for each artifact URL:
      skip if filename already on disk and not --force
      download to <name>.part, fsync, rename, sha256
   ↓
scaffold sol.txt if absent and configured
   ↓
upsert challenge row + artifact rows
   ↓
print the path (so it can be shell-substituted)
```

Idempotent: running it twice downloads nothing and clobbers nothing.

## Shell integration

A child process cannot change its parent shell's directory, so `cd` is provided
by a zsh function in `~/.zshrc` rather than by the binary:

```zsh
ctf() {
  case "$1" in
    get|cd)
      local d
      d=$(command ctf "$@" --print-path) || return
      [[ "$d" == /* && -d "$d" ]] && cd -- "$d"
      ;;
    *) command ctf "$@" ;;
  esac
}
```

### Why the wrapper is not a security surface

Audited 2026-08-04. Worth stating because a snippet pasted into `.zshrc` runs
in every interactive shell, so "it's only eight lines" is not an argument.

- Defining the function is inert — `.zshrc` stores the body, it does not run it.
- `command ctf` bypasses functions and aliases, so there is no recursion, and
  PATH resolution is **identical to having no wrapper at all**. The wrapper does
  not change which binary executes.
- Every expansion is quoted. zsh does not word-split or glob unquoted command
  substitutions in any case, so there is no injection surface.
- `local d` keeps the variable out of the user's shell.
- It **fails closed**: on tool error, empty output, or multi-line output, the
  guard rejects `d` and the shell simply does not move.
- `cd --` prevents a path starting with `-` being parsed as an option.
  `[[ "$d" == /* ]]` requires an absolute path, so a future bug that prints a
  relative fragment cannot chdir somewhere unexpected. Neither closes a live
  hole; both are insurance against bugs in the tool, which is where the
  variability lives.

**`cd` does not execute file contents.** It becomes an execution vector only
through directory-change hooks. On the user's machine (verified): no `chpwd` /
`chpwd_functions`, `direnv` not installed, oh-my-zsh plugins are `git`,
`colored-man-pages`, `command-not-found`, `zsh-autosuggestions`,
`zsh-history-substring-search`, `zsh-syntax-highlighting`, `you-should-use` —
none hook chpwd. `PATH` contains no `.` and no empty entry, so a file in a
challenge directory cannot be run by typing its bare name.

> **If `direnv` is ever installed, re-evaluate.** `ctf get` downloads
> attacker-supplied filenames into a directory and then chdirs into it. A
> downloaded `.envrc` is then code direnv wants to run. Its allow-list mitigates
> this, but the combination is exactly the pattern to watch for.

### Stream contract (load-bearing — do not violate)

Because the wrapper captures stdout, the streams must be split:

| Stream | Carries |
|---|---|
| **stdout** | machine-readable result only. Under `--print-path`, *exactly* the absolute path and nothing else. |
| **stderr** | all human output: progress, download lines, warnings, errors. |

Progress still appears on the terminal, because `$(...)` captures stdout only
and leaves stderr attached to the tty. This is the standard Unix split and it is
what makes the wrapper work. An earlier draft printed progress to stdout, which
silently breaks `cd` — the captured string contains the progress lines.

The same rule makes `ctf list --json` and `ctf export -o -` pipeable.

`ctf cd <ref>` is a subcommand that only resolves and prints a path; it is
`ctf path` with the wrapper contract applied.

## Bookkeeping output

`ctf list` is the terminal view. `ctf export` is the spreadsheet answer:

- `--format csv` → opens in LibreOffice; the literal "spreadsheet" ask.
- `--format md` → a table suitable for `$CTF_ROOT/.ctftool/STATUS.md`, so the
  overview is browsable on GitHub if the CTF tree is ever pushed.
- `--format json` → for anything else.

No xlsx writer: it would be the only non-stdlib dependency in the project, and
CSV opens in every spreadsheet program. Revisit only if the user asks.

## Security requirements

The threat model is narrow but real: **artifact filenames and directory names
are derived from data the tool did not author.** The index is JSON pulled out of
a browser session, and artifact URLs come from inside challenge descriptions. It
is as trustworthy as picoCTF — which is to say, trusted-ish, and not a reason to
skip validation. Everything below is a hard requirement, not a nicety.

### Filename handling in `fetch.py` — the actual injection point

The default filename is the basename of the URL. That is the one place hostile
input reaches the filesystem as a *path*. Required:

```python
def safe_target(dirpath: Path, url_or_name: str) -> Path:
    name = Path(unquote(urlparse(url_or_name).path)).name   # strip any directory part
    if not name or name in {".", ".."} or name.startswith("."):
        raise UnsafeArtifactName(url_or_name)
    target = (dirpath / name).resolve()
    if not target.is_relative_to(dirpath.resolve()):        # containment check
        raise UnsafeArtifactName(url_or_name)
    return target
```

- **Take only the basename.** A URL path of `/../../.zshrc` must never produce a
  write outside the challenge directory.
- **Reject dotfiles.** `.envrc`, `.gitconfig`, `.git/hooks/*` are the payloads
  worth planting. No legitimate picoCTF artifact is a dotfile; if one ever is,
  the user can rename it by hand.
- **Re-check containment after `resolve()`.** Basename-stripping alone is not
  sufficient reasoning to rely on; assert the invariant directly.
- **Do not follow symlinks when writing.** Open with `O_NOFOLLOW` (or verify
  `not target.is_symlink()` immediately before write). A pre-existing symlink in
  a challenge directory must not redirect a download.
- **Never overwrite an existing file** unless `--force`. Already required by the
  additive rule in `CLAUDE.md`; it also happens to be this control.
- Decompression is out of scope. `ctf get` downloads, it does **not** unpack
  archives. Zip-slip is therefore not reachable, and it stays that way.

`materialize.slugify()` gets the same treatment for the same reason: it turns a
platform-supplied *name* into a directory name. It must emit `[a-z0-9_]+` only,
reject an empty result, and never emit `.`, `..`, or a leading `-`.

`slugify()` and `safe_target()` are the two functions in this project that
warrant adversarial unit tests. Write them.

### What is deliberately not defended against

- **The artifacts themselves are hostile by design.** CTF challenges are malware
  samples, crafted files, and exploit binaries. That is the domain, not a
  vulnerability. The tool's job is to place them on disk and record that it did;
  it must not execute, unpack, or otherwise interpret them. Anything the user
  actually runs belongs in their Kali VM.
- **A compromised picoCTF** could serve arbitrary content at an artifact URL.
  Unsolvable here, and unchanged from downloading by hand in a browser.
- **`.ctftool/ctf.db`** holds flags in plaintext. It is a personal practice
  tracker; encryption would be theatre.

## Build order

1. `config.py` + `db.py` + `ctf init` — nothing works without a root and schema.
2. `materialize.slugify()` **with unit tests against the 24 real directory
   names** in `~/workspace/ctfs/picoCTF/`. This is the highest-risk function.
3. `ctf adopt` — immediately makes the tool useful on existing data, and
   validates the schema against 24 real rows before any network code exists.
4. `ctf list` / `ctf export` — requirement (2) is now met, standalone.
5. `registry.py` + `platforms/picoctf.py` resolution against a hand-written
   index fixture.
6. `ctf index picoCTF` — the console-snippet workflow.
7. `fetch.py` + `ctf get` — requirement (1) closes last, because it depends on
   everything above. `safe_target()` and its adversarial tests are part of this
   step, not a follow-up — see § Security requirements.

Steps 1–4 deliver working value with zero network and zero auth. Do them first.

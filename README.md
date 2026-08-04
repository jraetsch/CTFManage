# ctftool

Look up a challenge in your browser, then get it onto disk and into your
tracker with one command.

```console
$ ctf get "Glory of the Garden"
picoCTF · Glory of the Garden · Forensics
  ↓ garden.jpg  (2.1 MB)
  + sol.txt
/home/jannes/workspace/ctfs/picoCTF/glory_of_the_garden
```

With the shell function installed, that also drops you in the directory.

> **Status: design only.** Nothing is implemented yet. `CLAUDE.md` and `docs/`
> are the spec. See `docs/ARCHITECTURE.md` § Build order to start.

## What it does

1. **Fetch** — creates `$CTF_ROOT/<Platform>/<slug>/`, downloads every artifact
   into it, registers it.
2. **Track** — status, category, flag, and dates per challenge, queryable from
   the terminal or exportable as a spreadsheet.
3. **Extend** — new CTF platforms are one file implementing two methods.

## Install

Python 3.12+, **standard library only — no dependencies**, so there is nothing
to install into a virtualenv. Drop a launcher on your `PATH` and you are done:

```console
$ cat > ~/.local/bin/ctf <<'EOF'
#!/bin/sh
CTFTOOL_HOME="${CTFTOOL_HOME:-$HOME/src/ctftool}"
export PYTHONPATH="$CTFTOOL_HOME${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -P -m ctf "$@"
EOF
$ chmod +x ~/.local/bin/ctf
$ ctf init
```

`~/.local/bin` is on `PATH` on most distributions; check with `command -v ctf`.
Edits to the repo take effect immediately — there is no reinstall step. `-P`
keeps the working directory off `sys.path`, so a challenge folder containing a
stray `ctf.py` cannot shadow the package.

<details>
<summary>Alternative: a real package install</summary>

`pyproject.toml` declares the `ctf` entry point, so `pipx install -e .` works
where pipx is available. On PEP 668 systems (Debian/Ubuntu ≥ 23.04, Fedora ≥ 38)
`pip install --user` is blocked and you would need `pipx`, `uv`, or an explicit
venv. For a zero-dependency tool that is more machinery than it is worth — the
launcher above is the recommended route.
</details>

Add to `~/.zshrc` so `ctf get` and `ctf cd` change directory (a program cannot
chdir its parent shell, so this wrapper is required):

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

Progress output goes to stderr and the path to stdout, so you still see the
download running while the wrapper captures only the destination.

The wrapper does not change which binary runs — `command ctf` resolves through
`PATH` exactly as it would without it — and it fails closed, so a tool error
leaves you where you were. If you use `direnv`, read
`docs/ARCHITECTURE.md` § Why the wrapper is not a security surface first.

## First run

```console
$ ctf init                    # sets $CTF_ROOT, creates the database
$ ctf adopt                   # imports challenge folders you already have
$ ctf index picoCTF           # prints a one-time browser step, see below
```

`ctf index` exists because picoCTF's challenge listing sits behind Cloudflare
and a login, while the challenge *files* are on a public CDN. The tool has you
copy the catalogue out of your logged-in browser once; after that everything is
local and instant. Details in `docs/PLATFORMS.md`.

## Everyday use

```console
$ ctf get "Glory of the Garden"        # download + track + cd into it
$ ctf start                            # no ref needed — you are standing in it
$ ctf note tried strings, nothing
$ ctf solve --flag 'picoCTF{…}'
$ ctf list --status started
$ ctf list --category Forensics --platform picoCTF
$ ctf export --format csv -o ~/ctf-overview.csv
```

`<ref>` is optional on every command that acts on a challenge. Omit it and ctf
uses the challenge whose folder you are in — including from a subdirectory. To
act on a different one, name it: `ctf show garden`, `ctf open garden`.

`note` and `tag` take free text, so their ref is a flag instead:
`ctf note -r garden tried strings`. Otherwise a note starting with a challenge
name would be indistinguishable from a ref.

Statuses: `new`, `started`, `stuck`, `solved`, `abandoned`.

## Where things live

```
~/workspace/ctfs/              $CTF_ROOT — your challenges, one dir each
  .ctftool/ctf.db              the tracking database
  picoCTF/glory_of_the_garden/ artifacts sit flat, next to your sol.txt
~/.config/ctftool/config.toml  settings
~/.config/ctftool/index/       cached platform catalogues (rebuildable)
```

The layout matches how the challenges are already organised: nested by platform,
`snake_case` folder names, artifacts flat in the folder alongside `sol.txt`.

`ctf get` is additive and idempotent — re-running it re-downloads nothing and
never touches files it did not create.

## Adding a platform

One file in `ctf/platforms/`, two methods: `resolve()` maps a name to artifact
URLs, `refresh_index()` builds the local catalogue. Downloading, folder
creation, and bookkeeping are shared. Walkthrough and a checklist for reverse
engineering a new site are in `docs/PLATFORMS.md`.

## Documentation

| | |
|---|---|
| `CLAUDE.md` | Orientation, verified facts, conventions, open questions |
| `docs/ARCHITECTURE.md` | Layers, data model, command surface, build order |
| `docs/PLATFORMS.md` | Plugin contract, picoCTF reference, adding platforms |

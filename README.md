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

> **Status: working.** Every command is implemented, with 67 tests and an
> end-to-end run against the live picoCTF CDN. Index acquisition is verified
> against the real API (525 challenges); the remaining unknown is the field
> mapping — see `BUILD_LOG.md` § Known gaps.

## What it does

1. **Fetch** — creates `$CTF_ROOT/<Platform>/<slug>/`, downloads every artifact
   into it, registers it.
2. **Track** — status, category, flag, and dates per challenge, queryable from
   the terminal or exportable as a spreadsheet.
3. **Extend** — new CTF platforms are one file implementing two methods.

## Install

Python 3.12+, **standard library only — no dependencies**, so there is nothing
to install into a virtualenv.

```console
$ ./install.sh
$ ctf init
```

This copies `ctf/` into `~/.local/share/ctftool` and drops a launcher at
`~/.local/bin/ctf` (check `~/.local/bin` is on `PATH` with `command -v ctf`).
Re-run `./install.sh` after pulling changes to pick them up.

For working on ctftool itself, `./install.sh --symlink` links the install
directory back to this checkout instead of copying it, so edits take effect
immediately with no reinstall step.

`./install.sh --uninstall` removes the launcher and install directory it
created (never anything it didn't create) and asks before touching
`~/.config/ctftool`; pass `--purge-config` or `--keep-config` to answer that
non-interactively. It never touches `$CTF_ROOT` — your challenges and database
are not install artifacts. See `./install.sh --help` for `--prefix`/`--bin-dir`
overrides.

<details>
<summary>Alternative: a real package install</summary>

`pyproject.toml` declares the `ctf` entry point, so `pipx install -e .` works
where pipx is available. On PEP 668 systems (Debian/Ubuntu ≥ 23.04, Fedora ≥ 38)
`pip install --user` is blocked and you would need `pipx`, `uv`, or an explicit
venv. For a zero-dependency tool that is more machinery than it is worth — the
launcher above is the recommended route.
</details>

`ctf get` and `ctf cd` always print the challenge's directory — a program
can't `cd` its parent shell, so that's as far as the binary alone can go.
`install.sh` optionally adds the last step: a small shell function, sourced
into your shell, that captures the printed path and actually `cd`s there. It
asks which shell during install (`zsh`, `bash`, `fish`, or skip); answer
non-interactively with `--shell zsh|bash|fish|none`, and re-run `install.sh`
any time to add, replace, or change it. `install.sh --uninstall` removes
exactly the hook it installed.

Each is native to its shell (fish's function syntax shares nothing with
zsh/bash's), but all three do the same thing: run `ctf` for real, and only
`cd` into the result if it's an absolute, existing path — otherwise stay put.
The zsh/bash version (audited — see `docs/ARCHITECTURE.md` § Why the wrapper
is not a security surface):

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

and the fish version, installed as `~/.config/fish/functions/ctf.fish`:

```fish
function ctf
    switch $argv[1]
        case get cd
            set -l d (command ctf $argv --print-path)
            or return
            if string match -q '/*' -- "$d"; and test -d "$d"
                cd -- "$d"
            end
        case '*'
            command ctf $argv
    end
end
```

Progress output goes to stderr and the path to stdout, so you still see the
download running while the hook captures only the destination. Neither
wrapper changes which binary runs — `command ctf` resolves through `PATH`
exactly as it would without it — and both fail closed, so a tool error leaves
you where you were. If you use `direnv`, read `docs/ARCHITECTURE.md` § Why the
wrapper is not a security surface first.

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
$ ctf hint                             # hints are never shown by `ctf show`
$ ctf solve --flag 'picoCTF{…}'
$ ctf list                             # what you are tracking
$ ctf list --status started
$ ctf list --available                 # the whole catalogue, tracked ones marked
$ ctf list --available --untracked     # ... only what you have not done yet
$ ctf export --format csv -o ~/ctf-overview.csv
```

`<ref>` is optional on every command that acts on a challenge. Omit it and ctf
uses the challenge whose folder you are in — including from a subdirectory. To
act on a different one, name it: `ctf show garden`, `ctf open garden`.

`note` and `tag` take free text, so their ref is a flag instead:
`ctf note -r garden tried strings`. Otherwise a note starting with a challenge
name would be indistinguishable from a ref.

Hints are deliberately kept out of `ctf show`, which only reports how many
exist. Reading one is then a deliberate act — `ctf hint`, or `ctf hint -n 2`
for a single hint — rather than a side effect of checking a category.

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

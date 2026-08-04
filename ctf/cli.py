"""Command line interface.

STREAM CONTRACT (load-bearing — see docs/ARCHITECTURE.md):
    stdout = machine-readable result only. Under --print-path, exactly the
             absolute path and nothing else.
    stderr = all human output: progress, warnings, errors.
The zsh wrapper captures stdout via $(...), so violating this silently breaks
`cd`. Use out() and msg() below; do not call bare print().
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod
from . import db as dbmod
from . import export as exportmod
from . import fetch as fetchmod
from . import materialize as mat
from . import registry
from .registry import ManualStepRequired, PlatformError

EXIT_OK, EXIT_ERR, EXIT_AMBIGUOUS, EXIT_NOTFOUND, EXIT_NETWORK = 0, 1, 2, 3, 4


def out(s: str = "") -> None:
    print(s, file=sys.stdout)


def msg(s: str = "") -> None:
    print(s, file=sys.stderr)


class CLIError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERR):
        super().__init__(message)
        self.code = code


# -- helpers ---------------------------------------------------------------


def _open_db(cfg):
    return dbmod.connect(cfg.db_path)


def _from_cwd(conn, cfg):
    """The tracked challenge containing the working directory, or None.

    Matches the deepest challenge whose directory is the cwd or an ancestor of
    it, so being in `glory_of_the_garden/subdir/` still resolves.
    """
    try:
        cwd = Path.cwd().resolve()
    except OSError:                      # cwd was deleted underneath us
        return None
    best, best_len = None, -1
    for row in conn.execute("SELECT * FROM challenge").fetchall():
        d = (cfg.ctf_root / row["path"]).resolve()
        if cwd == d or d in cwd.parents:
            if len(str(d)) > best_len:
                best, best_len = row, len(str(d))
    return best


def _one(conn, cfg, ref: str | None, platform: str | None = None):
    """Resolve a ref against the DB to exactly one row, or raise.

    With no ref, fall back to the challenge the working directory is in — the
    common case is acting on the challenge you are standing in.
    """
    if not ref:
        row = _from_cwd(conn, cfg)
        if row is None:
            raise CLIError(
                "no <ref> given and the current directory is not a tracked "
                "challenge — pass a name, or cd into a challenge folder",
                EXIT_NOTFOUND,
            )
        return row

    rows = dbmod.find(conn, ref, platform)
    if not rows:
        raise CLIError(
            f"no tracked challenge matches {ref!r} — try `ctf list` or `ctf get {ref!r}`",
            EXIT_NOTFOUND,
        )
    if len(rows) > 1:
        msg(f"{ref!r} is ambiguous — {len(rows)} matches:")
        for r in rows:
            msg(f"  {r['platform']}/{r['slug']}  ({r['name']})")
        raise CLIError("refusing to guess; be more specific", EXIT_AMBIGUOUS)
    return rows[0]


def _status_mark(status: str) -> str:
    return {"solved": "✓", "started": "→", "stuck": "?",
            "abandoned": "✗", "new": "·"}.get(status, " ")


# -- commands --------------------------------------------------------------


def cmd_init(args) -> int:
    existing = cfgmod.load(required=False)
    default_root = str(existing.ctf_root) if existing else str(Path.cwd())

    root = args.root
    if not root:
        if not sys.stdin.isatty():
            raise CLIError("not a tty — pass --root explicitly")
        entered = input(f"CTF root directory [{default_root}]: ").strip()
        root = entered or default_root

    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise CLIError(f"not a directory: {path}")

    cfg = existing or cfgmod.Config(ctf_root=path)
    cfg.ctf_root = path
    saved = cfgmod.save(cfg)
    conn = _open_db(cfg)
    conn.close()

    msg(f"config  {saved}")
    msg(f"root    {cfg.ctf_root}")
    msg(f"db      {cfg.db_path}")
    msg("")
    msg("next:  ctf adopt          # import folders you already have")
    msg("       ctf index picoCTF  # one-time browser step")
    return EXIT_OK


def cmd_adopt(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    found = mat.scan_root(cfg.ctf_root, args.platform)
    if not found:
        msg(f"no challenge directories under {cfg.ctf_root}")
        return EXIT_OK

    added = updated = backfilled = 0
    registry.load_all()

    for platform, slug, path in found:
        rel = str(path.relative_to(cfg.ctf_root))
        existing = conn.execute(
            "SELECT id, category FROM challenge WHERE platform = ? AND slug = ?",
            (platform, slug),
        ).fetchone()

        # A directory has no metadata. If an index exists, try to match the
        # slug back to a catalogue entry so category/description get filled in.
        meta = {}
        name = slug
        try:
            plat = registry.get(platform)
            for cand in plat.resolve(slug):
                if mat.slugify(cand.name) == slug or cand.name.lower() == slug:
                    name = cand.name
                    meta = {
                        "category": cand.category, "difficulty": cand.difficulty,
                        "points": cand.points, "event": cand.event,
                        "url": cand.url, "author": cand.author,
                        "description": cand.description,
                        "hints": cand.hints, "platform_id": cand.platform_id,
                        "endpoints": [{"label": e.label, "endpoint": e.endpoint}
                                      for e in cand.endpoints],
                    }
                    break
        except (PlatformError, ManualStepRequired, mat.UnsafeName):
            pass

        if args.dry_run:
            state = "exists" if existing else "add"
            msg(f"  {state:6} {platform}/{slug}" + (f"  [{meta['category']}]"
                                                    if meta.get("category") else ""))
            continue

        cid = dbmod.upsert_challenge(
            conn, platform=platform, slug=slug, name=name, path=rel, **meta
        )
        if existing:
            updated += 1
            if meta.get("category") and not existing["category"]:
                backfilled += 1
        else:
            added += 1
            # Record files already on disk as artifacts of unknown origin? No —
            # the artifact table means "the tool downloaded this". Adoption
            # cannot know, and guessing would make the column meaningless.

    if args.dry_run:
        msg(f"\n{len(found)} directories scanned (dry run, nothing written)")
        return EXIT_OK

    msg(f"{added} added, {updated} already tracked" +
        (f", {backfilled} metadata backfilled" if backfilled else ""))
    if added and not backfilled:
        msg("categories are empty until an index exists — run `ctf index picoCTF`, "
            "then `ctf adopt` again to backfill")
    return EXIT_OK


def cmd_index(args) -> int:
    registry.load_all()
    plat = registry.get(args.platform)
    source = Path(args.from_file).expanduser() if args.from_file else None
    try:
        n = plat.refresh_index(source)
    except ManualStepRequired as e:
        msg(f"{plat.name}: {e.reason}\n")
        msg(e.instructions)
        if e.resume_with:
            msg(f"\nthen run:\n    {e.resume_with}")
        return EXIT_OK
    msg(f"{plat.name}: indexed {n} challenges")
    return EXIT_OK


def cmd_platforms(args) -> int:
    registry.load_all()
    for p in registry.all_platforms():
        msg(f"  {p.name:<12} {p.url:<32} {p.index_status()}")
    return EXIT_OK


def cmd_get(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    registry.load_all()
    platform = args.platform or cfg.default_platform
    plat = registry.get(platform)

    try:
        candidates = plat.resolve(args.ref)
    except ManualStepRequired as e:
        msg(f"{plat.name}: {e.reason}\n")
        msg(e.instructions)
        return EXIT_ERR

    if not candidates:
        raise CLIError(
            f"{args.ref!r} not found in the {plat.name} index — "
            f"run `ctf index {plat.name}` (or check the spelling)",
            EXIT_NOTFOUND,
        )
    if len(candidates) > 1:
        msg(f"{args.ref!r} matches {len(candidates)} challenges:")
        for c in candidates:
            msg(f"  {c.name}" + (f"  [{c.category}]" if c.category else ""))
        raise CLIError("refusing to guess; be more specific", EXIT_AMBIGUOUS)

    chal = candidates[0]
    slug = mat.slugify(chal.name)
    dirpath = cfg.challenge_dir(platform, slug)

    if args.dry_run:
        msg(f"{platform} · {chal.name}" + (f" · {chal.category}" if chal.category else ""))
        msg(f"  would create {dirpath}")
        for a in chal.artifacts:
            msg(f"  would download {a.url}")
        out(str(dirpath))
        return EXIT_OK

    header = f"{platform} · {chal.name}"
    if chal.category:
        header += f" · {chal.category}"
    msg(header)

    created = mat.ensure_dir(dirpath)
    if not created:
        msg(f"  (directory exists — additive, nothing will be overwritten)")

    rel = str(dirpath.relative_to(cfg.ctf_root))
    cid = dbmod.upsert_challenge(
        conn, platform=platform, slug=slug, name=chal.name, path=rel,
        category=chal.category, difficulty=chal.difficulty, points=chal.points,
        event=chal.event, url=chal.url, author=chal.author,
        description=chal.description, hints=chal.hints,
        endpoints=[{"label": e.label, "endpoint": e.endpoint} for e in chal.endpoints],
        raw=chal.raw, platform_id=chal.platform_id,
    )

    failures = 0
    if not args.no_download:
        for a in chal.artifacts:
            try:
                res = fetchmod.download(a.url, dirpath, filename=a.filename,
                                        force=args.force)
            except mat.UnsafeName as e:
                msg(f"  ! refused: {e}")
                failures += 1
                continue
            except fetchmod.DownloadError as e:
                msg(f"  ! failed: {e}")
                failures += 1
                continue
            dbmod.record_artifact(conn, cid, url=a.url, filename=res.filename,
                                  size=res.size, sha256=res.sha256)

    for e in chal.endpoints:
        msg(f"  ⇄ {e.label}: {e.endpoint}")

    if cfg.write_description:
        written = mat.write_description(dirpath, chal)
        if written:
            msg(f"  + {written}")
    for fn in mat.scaffold(dirpath, cfg.scaffold):
        msg(f"  + {fn}")

    # Zero *content* is the failure condition, not zero artifacts: a pwn
    # challenge delivered as `nc host port` is complete with no files.
    if not chal.has_content:
        msg(f"  ! no artifacts and no endpoints recorded for {chal.name!r} — "
            f"the index may predate a host change; re-run `ctf index {platform}`")
        out(str(dirpath))
        return EXIT_ERR

    if failures:
        out(str(dirpath))
        return EXIT_NETWORK

    out(str(dirpath))
    return EXIT_OK


def cmd_list(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    rows = dbmod.query(conn, status=args.status, category=args.category,
                       platform=args.platform)
    if args.json:
        out(exportmod.to_json(rows).rstrip())
        return EXIT_OK
    if not rows:
        msg("nothing tracked yet — try `ctf adopt`")
        return EXIT_OK

    w_name = max(len(r["name"]) for r in rows)
    w_cat = max((len(r["category"] or "") for r in rows), default=0)
    for r in rows:
        msg(f" {_status_mark(r['status'])} {r['name']:<{w_name}}  "
            f"{(r['category'] or ''):<{w_cat}}  {r['platform']}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    msg("")
    msg(f" {len(rows)} challenges — " + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return EXIT_OK


def cmd_show(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    r = _one(conn, cfg, args.ref, args.platform)
    msg(f"{r['name']}  [{r['status']}]")
    for label, key in (("platform", "platform"), ("category", "category"),
                       ("difficulty", "difficulty"), ("points", "points"),
                       ("event", "event"), ("author", "author"),
                       ("tags", "tags"), ("flag", "flag"),
                       ("added", "added_at"), ("solved", "solved_at"),
                       ("url", "url")):
        if r[key]:
            msg(f"  {label:<10} {r[key]}")
    msg(f"  {'path':<10} {cfg.ctf_root / r['path']}")

    if r["endpoints"]:
        items = json.loads(r["endpoints"])
        if items:
            msg("  endpoints:")
            for it in items:
                msg(f"    - {it['label']}: {it['endpoint']}"
                    if isinstance(it, dict) else f"    - {it}")

    # Hints are spoilers, so `show` only says they exist. `ctf hint` prints
    # them, which keeps revealing one a deliberate act rather than a side
    # effect of looking up the category.
    hints = json.loads(r["hints"]) if r["hints"] else []
    if hints:
        n = len(hints)
        msg(f"  {'hints':<10} {n} available — `ctf hint"
            f"{'' if not args.ref else ' ' + args.ref}` to read")

    arts = dbmod.artifacts_for(conn, r["id"])
    if arts:
        msg("  artifacts:")
        for a in arts:
            size = fetchmod.human(a["bytes"]) if a["bytes"] else "?"
            msg(f"    - {a['filename']}  ({size})")
    if r["description"]:
        msg("")
        msg(r["description"].strip())
    if r["notes"]:
        msg("")
        msg("notes:")
        msg(r["notes"].strip())
    return EXIT_OK


def cmd_status(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    r = _one(conn, cfg, args.ref, args.platform)
    flag = getattr(args, "flag", None)
    dbmod.set_status(conn, r["id"], args.status, flag)
    msg(f"{r['name']} → {args.status}" + (f"  {flag}" if flag else ""))
    return EXIT_OK


def cmd_hint(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    r = _one(conn, cfg, args.ref, args.platform)
    hints = json.loads(r["hints"]) if r["hints"] else []
    if not hints:
        msg(f"no hints recorded for {r['name']!r}")
        return EXIT_OK

    if args.number is not None:
        if not 1 <= args.number <= len(hints):
            raise CLIError(f"{r['name']!r} has {len(hints)} hints; "
                           f"{args.number} is out of range")
        hints = [hints[args.number - 1]]
        labels = [args.number]
    else:
        labels = range(1, len(hints) + 1)

    msg(f"{r['name']} — {len(hints)} of "
        f"{len(json.loads(r['hints']))} hints")
    for i, h in zip(labels, hints):
        msg("")
        msg(f"  [{i}] {h}")
    return EXIT_OK


def cmd_note(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    r = _one(conn, cfg, args.ref, args.platform)
    dbmod.append_note(conn, r["id"], " ".join(args.text))
    msg(f"noted on {r['name']}")
    return EXIT_OK


def cmd_tag(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    r = _one(conn, cfg, args.ref, args.platform)
    joined = dbmod.add_tags(conn, r["id"], args.tags)
    msg(f"{r['name']} tags: {joined}")
    return EXIT_OK


def cmd_export(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    rows = dbmod.query(conn)
    text = exportmod.render(rows, args.format)
    if args.output and args.output != "-":
        path = Path(args.output).expanduser()
        path.write_text(text, encoding="utf-8")
        msg(f"wrote {len(rows)} rows to {path}")
    else:
        out(text.rstrip())
    return EXIT_OK


def cmd_path(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    r = _one(conn, cfg, args.ref, args.platform)
    out(str(cfg.ctf_root / r["path"]))
    return EXIT_OK


def cmd_open(args) -> int:
    cfg = cfgmod.load()
    conn = _open_db(cfg)
    r = _one(conn, cfg, args.ref, args.platform)
    if not r["url"]:
        raise CLIError(f"no challenge URL recorded for {r['name']!r}")
    subprocess.Popen(["xdg-open", r["url"]],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    msg(f"opening {r['url']}")
    return EXIT_OK


# -- help ------------------------------------------------------------------

HELP = """\
ctf — fetch, organise and track CTF challenges

USAGE
  ctf <command> [args]          ctf help <command>   for details on one command

TYPICAL SESSION
  You found a challenge in your browser and want it on disk:

      ctf get "Glory of the Garden"     create the folder, download the files,
                                        start tracking it, and cd into it
      ctf start garden                  mark it as in progress
      ctf note garden "tried strings"   jot down where you got to
      ctf solve garden --flag 'picoCTF{...}'

  Refs are lenient: exact slug, then exact name, then substring. So `garden`,
  `glory_of_the_garden` and "Glory of the Garden" all work. If a ref matches
  more than one challenge, ctf lists the candidates and refuses to guess.

  <ref> is optional everywhere. Leave it out and ctf uses the challenge whose
  folder you are standing in, so once you have cd'd in, the ref is noise:

      ctf start                         these act on the current folder
      ctf note tried strings
      ctf hint
      ctf solve --flag 'picoCTF{...}'

  note and tag take free text, so their ref is a flag rather than a positional
  ('ctf note garden ...' could not be told apart from a note beginning with the
  word "garden"):  ctf note -r garden tried strings

FIRST RUN
  ctf init                       set the CTF root, create the database
  ctf adopt                      import challenge folders you already have
  ctf index picoCTF              one-time browser step, see INDEXING below

GETTING CHALLENGES
  ctf get <ref>                  download + track + cd
      --force                    re-download files that are already present
      --no-download              create and track, fetch nothing
      --dry-run                  show what would happen, write nothing
  ctf adopt [--dry-run]          import existing folders; re-run after indexing
                                 to backfill categories and descriptions

TRACKING
  ctf list                       everything, grouped and counted
      --status <s>  --category <c>  --platform <p>  --json
  ctf show [ref]                 one challenge in full, with artifacts + notes
  ctf hint [ref] [-n N]          print hints — deliberately NOT part of `show`,
                                 so looking up a category cannot spoil you
  ctf start|stuck|abandon [ref]  change status
  ctf solve [ref] [--flag F]     status=solved, stamps the solve time
  ctf note [-r ref] <text>       append a timestamped note
  ctf tag [-r ref] <tags...>     add free-form tags
  ctf export [--format csv|md|json] [-o FILE]

  Statuses: new · started · stuck · solved · abandoned

NAVIGATING
  ctf path <ref>                 print the absolute path
  ctf cd <ref>                   same, and cd there (needs the zsh function)
  ctf open <ref>                 open the challenge page in a browser

PLATFORMS
  ctf platforms                  what is registered, and index freshness
  ctf index <platform>           rebuild the catalogue

INDEXING
  On picoCTF the challenge *listing* is behind Cloudflare and a login, but the
  challenge *files* are on a public CDN. So `ctf index picoCTF` prints a snippet
  to paste into your browser console once; everything after that is local and
  instant. Re-run it maybe once a year, or during a live competition.

      ctf index picoCTF                                     prints the snippet
      ctf index picoCTF --from-file ~/Downloads/picoctf-index.json

WHERE THINGS LIVE
  ~/.config/ctftool/config.toml    settings
  ~/.config/ctftool/index/         cached catalogues (rebuildable)
  $CTF_ROOT/.ctftool/ctf.db        the tracking database
  $CTF_ROOT/<Platform>/<slug>/     one folder per challenge, artifacts flat

EXIT CODES
  0 ok    1 error    2 ambiguous ref    3 not found    4 download failed

NOTES
  Progress goes to stderr, the resulting path to stdout — that is what makes
  `cd "$(ctf path foo)"` safe. `ctf get` is additive and idempotent: it never
  overwrites or deletes anything it did not create.
"""


def cmd_help(args) -> int:
    topic = getattr(args, "topic", None)
    if not topic:
        out(HELP.rstrip())
        return EXIT_OK

    parser = build_parser()
    for action in parser._subparsers._group_actions:      # noqa: SLF001
        if topic in action.choices:
            action.choices[topic].print_help()
            return EXIT_OK
    msg(f"no such command: {topic}")
    msg("run `ctf help` for the command list")
    return EXIT_ERR


# -- argument parsing ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ctf",
        description="Fetch, organise and track CTF challenges.",
        epilog="`ctf help` gives a task-oriented overview with examples.",
    )
    sub = p.add_subparsers(dest="command", required=False)

    def with_platform(sp):
        sp.add_argument("--platform", "-p", help="restrict to one platform")
        return sp

    # Every command that acts on an existing challenge takes <ref> optionally;
    # omitted, it means "the challenge I am standing in". See _from_cwd().
    def with_ref(sp):
        sp.add_argument("ref", nargs="?",
                        help="challenge name/slug; default: the current folder")
        return with_platform(sp)

    sp = sub.add_parser("init", help="first-run setup")
    sp.add_argument("--root", help="CTF root directory")
    sp.set_defaults(func=cmd_init)

    sp = with_platform(sub.add_parser("get", help="download a challenge and track it"))
    sp.add_argument("ref")
    sp.add_argument("--force", action="store_true", help="re-download existing files")
    sp.add_argument("--no-download", action="store_true", help="create and track only")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--print-path", action="store_true",
                    help="(used by the zsh wrapper; path always goes to stdout)")
    sp.set_defaults(func=cmd_get)

    sp = with_platform(sub.add_parser("adopt", help="import existing directories"))
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_adopt)

    sp = sub.add_parser("index", help="rebuild a platform catalogue")
    sp.add_argument("platform")
    sp.add_argument("--from-file", help="JSON produced by the browser snippet")
    sp.set_defaults(func=cmd_index)

    sp = with_platform(sub.add_parser("list", help="tracked challenges"))
    sp.add_argument("--status", choices=dbmod.STATUSES)
    sp.add_argument("--category", "-c")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = with_ref(sub.add_parser("show", help="one challenge in detail"))
    sp.set_defaults(func=cmd_show)

    sp = with_ref(sub.add_parser("hint", help="print hints (kept out of `show`)"))
    sp.add_argument("--number", "-n", type=int, metavar="N",
                    help="print only hint N instead of all of them")
    sp.set_defaults(func=cmd_hint)

    for status in ("start", "stuck", "abandon"):
        canonical = {"start": "started", "stuck": "stuck", "abandon": "abandoned"}[status]
        sp = with_ref(sub.add_parser(status, help=f"mark as {canonical}"))
        sp.set_defaults(func=cmd_status, status=canonical)

    sp = with_ref(sub.add_parser("solve", help="mark as solved"))
    sp.add_argument("--flag", "-f")
    sp.set_defaults(func=cmd_status, status="solved")

    # note/tag take trailing free text, so <ref> cannot also be positional —
    # `ctf note garden ...` would be indistinguishable from a note that happens
    # to start with the word "garden". The ref moves to a flag; omitted, the
    # cwd rule applies as everywhere else.
    sp = with_platform(sub.add_parser("note", help="append a note"))
    sp.add_argument("--ref", "-r", help="challenge; default: the current folder")
    sp.add_argument("text", nargs="+")
    sp.set_defaults(func=cmd_note)

    sp = with_platform(sub.add_parser("tag", help="add tags"))
    sp.add_argument("--ref", "-r", help="challenge; default: the current folder")
    sp.add_argument("tags", nargs="+")
    sp.set_defaults(func=cmd_tag)

    sp = sub.add_parser("export", help="csv / markdown / json")
    sp.add_argument("--format", choices=("csv", "md", "json"), default="csv")
    sp.add_argument("--output", "-o", help="file, or - for stdout")
    sp.set_defaults(func=cmd_export)

    for name, help_text in (("path", "print the directory"), ("cd", "print the directory")):
        sp = with_ref(sub.add_parser(name, help=help_text))
        sp.add_argument("--print-path", action="store_true", help=argparse.SUPPRESS)
        sp.set_defaults(func=cmd_path)

    sp = with_ref(sub.add_parser("open", help="open the challenge page"))
    sp.set_defaults(func=cmd_open)

    sp = sub.add_parser("platforms", help="registered platforms")
    sp.set_defaults(func=cmd_platforms)

    sp = sub.add_parser("help", help="task-oriented overview with examples")
    sp.add_argument("topic", nargs="?", help="a command name, for its own help")
    sp.set_defaults(func=cmd_help)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Bare `ctf` shows the overview rather than an argparse usage error: the
    # thing a person types when they do not know what to type should teach them.
    if not getattr(args, "func", None):
        out(HELP.rstrip())
        return EXIT_OK
    try:
        return args.func(args)
    except CLIError as e:
        msg(f"error: {e}")
        return e.code
    except (cfgmod.ConfigError, PlatformError, mat.UnsafeName) as e:
        msg(f"error: {e}")
        return EXIT_ERR
    except ManualStepRequired as e:
        msg(f"{e.reason}\n")
        msg(e.instructions)
        return EXIT_OK
    except KeyboardInterrupt:
        msg("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())

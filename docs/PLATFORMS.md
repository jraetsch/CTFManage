# Platforms

How the plugin layer works, how to add a platform, and everything currently
known about the two that matter.

## The contract

A platform is one file in `ctf/platforms/`. `ctf/platforms/__init__.py`
auto-imports every sibling module at startup, so the `@register` decorator is
all that is needed to make one discoverable.

```python
from ctf.registry import register, Platform, ManualStepRequired
from ctf.models import Challenge, Artifact

@register
class PicoCTF(Platform):
    name = "picoCTF"          # MUST equal the directory name under $CTF_ROOT
    url  = "https://play.picoctf.org"

    def resolve(self, ref: str) -> list[Challenge]:
        """ref (name / slug / url / id) → candidate challenges.

        Pure local lookup against the index. No network, no auth.
        []           → not found      (CLI exits 3, suggests `ctf index`)
        [c]          → unambiguous
        [c1, c2, …]  → ambiguous      (CLI exits 2, prints candidates)
        """

    def refresh_index(self, source: Path | None = None) -> int:
        """Rebuild the local index; return the number of challenges indexed.

        If the platform cannot be scraped headlessly, raise ManualStepRequired
        with printable instructions instead of failing. That is a normal,
        supported outcome — not an error path.
        """
```

`Challenge` and `Artifact` are plain dataclasses:

```python
@dataclass
class Artifact:
    url: str
    filename: str | None = None      # defaults to basename of url

@dataclass
class Challenge:
    platform: str
    name: str
    artifacts: list[Artifact]
    category: str | None = None
    difficulty: str | None = None
    points: int | None = None
    event: str | None = None
    url: str | None = None
    description: str | None = None
```

Nothing else. Layer 1 (mkdir, download, DB write) is shared and needs no
per-platform code — see `docs/ARCHITECTURE.md`.

### `ManualStepRequired`

The escape hatch that makes auth-walled platforms tractable:

```python
raise ManualStepRequired(
    reason="play.picoctf.org/api is behind Cloudflare and requires login.",
    instructions=PICOCTF_CONSOLE_SNIPPET,
    resume_with="ctf index picoCTF --from-file ~/Downloads/index.json",
)
```

The CLI catches it, prints the instructions, and exits `0` — the user is not
stuck, they are being handed a two-minute task. Design new platforms to use this
rather than attempting to defeat bot protection.

## Adding a platform: checklist

1. Open the platform in a browser, DevTools → Network, and find the request that
   lists challenges. *Copy as cURL.*
2. Replay it with plain `curl`. If it works headlessly → implement
   `refresh_index` directly. If it 403s → use the console-snippet pattern below
   and `ManualStepRequired`.
3. Find one artifact URL and `curl -sI` it **with no cookies**. It is usually a
   public CDN. If so, Layer 1 already handles downloading; you are done early.
4. Write the module, set `name` to match the directory name the user already
   uses (check `ls $CTF_ROOT` first — do not invent a new casing).
5. Add a fixture index under `tests/fixtures/` and a resolution test.

## picoCTF — reference implementation

### Verified 2026-08-04

| | |
|---|---|
| Listing endpoint | `https://play.picoctf.org/api/challenges/?page_size=100&page=N` |
| Headless access | **Blocked.** 403, Cloudflare managed challenge. A Chrome `User-Agent` does not help. |
| Auth | Required (session cookie) in addition to the Cloudflare clearance. |
| Artifact hosts | `artifacts.picoctf.net` **and** `challenge-files.picoctf.net` — at least two, see below |
| Artifact auth | **None.** Bare `curl` with no cookies → 200 on both. |

Confirmed downloads, no credentials of any kind:

```
https://artifacts.picoctf.net/c/500/files.zip                 200   3995553 B
https://artifacts.picoctf.net/c/80/Flag.pdf                   200      5161 B
https://artifacts.picoctf.net/c_mimas/75/original.jpg         200   2851929 B
https://challenge-files.picoctf.net/c_plain_mesa/95b5340b…/message.txt
                                                              200       135 B
```

Both hosts are S3 behind CloudFront and behave identically.

### Do not hardcode host or path shape

Observed path shapes, all different:

```
artifacts.picoctf.net/c/500/files.zip                   small integer
artifacts.picoctf.net/c_mimas/75/original.jpg           event slug + integer
challenge-files.picoctf.net/c_plain_mesa/<64 hex>/…     event slug + sha256
```

Event slugs seen: `c`, `c_mimas`, `c_titan`, `c_atlas`, `c_plain_mesa`.
`challenge-files` with a sha256 path segment appears to be the newer scheme
(the example is from a 2026 challenge). **Never construct an artifact URL.**
Only ever use URLs extracted verbatim from the index.

This list is guaranteed to be incomplete. It was already wrong once: the design
originally assumed `artifacts.picoctf.net` was the only host, which would have
silently produced zero artifacts for any challenge served from
`challenge-files`. The mitigation is structural — see the next section.

### Index acquisition

`cf_clearance` is bound to IP *and* User-Agent, so exported cookies are brittle
and expire within days. Rather than fight that, borrow the session once from
inside the browser, where the request is same-origin and already cleared.

`ctf index picoCTF` prints this and exits:

```js
// Firefox/Chrome → F12 → Console, on https://play.picoctf.org/practice
const out = {};
for (let page = 1; ; page++) {
  const r = await (await fetch(`/api/challenges/?page_size=100&page=${page}`)).json();
  const items = r.results ?? r;
  if (!items?.length) break;
  for (const c of items) {
    // Capture EVERY url. Classification happens in Python, not here.
    const urls = [...new Set(
      JSON.stringify(c).match(/https?:\/\/[^"'\\ )<>\]]+/g) || []
    )];
    out[c.name] = { ...c, _urls: urls };
  }
}
console.log(Object.keys(out).length + " challenges");
const a = document.createElement('a');
a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], {type:'application/json'}));
a.download = 'picoctf-index.json'; a.click();
```

Then: `ctf index picoCTF --from-file ~/Downloads/picoctf-index.json`

Three deliberate choices, in increasing order of importance:

- **`r.results ?? r`** — the API response shape is unverified (nobody has logged
  in yet). This handles both a paginated envelope and a bare array.
- **A regex over `JSON.stringify(c)`** rather than reading a named field. The
  URLs live somewhere in the challenge description as HTML anchors, Markdown
  links, or bare text, and the field names are unknown. Scanning the serialized
  record finds them regardless of schema and survives field renames.
- **No host filtering in the snippet.** It captures every URL it sees, including
  ones that are obviously not artifacts. This is the single most important
  property of the snippet and it is deliberate: **the snippet is the only
  component that is expensive to re-run** — it needs a browser, a login, and a
  human. Anything baked into it that later turns out to be wrong costs a manual
  re-run to fix. So it must contain no judgement, only capture.

The whole record is kept (`...c`) so `category`, `difficulty`, description and
event data can be mapped once the real shape is known. Do that in Python.

### Host classification (Python side)

`refresh_index()` splits `_urls` into artifacts and everything else:

```python
ARTIFACT_HOSTS = {
    "artifacts.picoctf.net",
    "challenge-files.picoctf.net",
}

def classify(urls: list[str]) -> tuple[list[str], list[str]]:
    """→ (artifacts, unknown_picoctf_hosts). Everything else is dropped."""
    artifacts, suspect = [], []
    for u in urls:
        host = urlparse(u).netloc.lower()
        if host in ARTIFACT_HOSTS:
            artifacts.append(u)
        elif host.endswith(".picoctf.net") and host != "play.picoctf.org":
            suspect.append(u)          # a host we have not seen before
    return artifacts, suspect
```

Two required behaviours, both learned from the `challenge-files` miss:

1. **`ctf index` must report suspects loudly**, e.g.
   `warning: 12 URLs on unseen host 'files2.picoctf.net' — add to ARTIFACT_HOSTS?`
   A new host must surface as a visible prompt, never as silence.
2. **`ctf get` must fail loudly on zero artifacts.** If a challenge resolves but
   has an empty artifact list, exit non-zero with
   `no artifacts recorded for <name> — the index may predate a host change`.
   Creating an empty directory and reporting success is the worst outcome.

Adding a newly discovered host is then a one-line Python change with **no
browser re-run**, because the raw URLs were never discarded.

### Re-indexing cadence

picoGym challenges are effectively static, so once a year is enough. During a
live competition the user would re-run the snippet to pick up new releases.

## ctfLearn — stub

**Uninvestigated.** The user has `ctfLearn/forensics_101/` with a
`challenge.jpg`, so files are being obtained somehow, but by what mechanism is
unknown.

Before implementing, answer via the checklist above:
- Does ctfLearn have a JSON API, or is it server-rendered HTML?
- Are challenge files on a public CDN, or served behind the session?
- Is there a stable challenge id in the URL?

Until then `resolve()` should raise `ManualStepRequired` explaining that the
platform is not implemented, so `ctf get --platform ctfLearn` fails with a
useful message rather than a traceback.

## A platform that needs no index

If a platform's challenge page URL is enough to derive artifacts (some CTFd
instances expose `/api/v1/challenges/<id>` publicly), `resolve()` may fetch
directly and `refresh_index()` can be a no-op returning `0`. The three-layer
model permits collapsing Layer 3 — it does not require it. CTFd-based events are
the likely next platform, and they are typically much easier than picoCTF.

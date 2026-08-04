"""ctfLearn — stub.

Uninvestigated. The user has ctfLearn/forensics_101/ with a challenge.jpg, so
files are being obtained somehow, but by what mechanism is unknown. Before
implementing, work through the checklist in docs/PLATFORMS.md § Adding a
platform and answer: JSON API or server-rendered HTML? Files on a public CDN or
behind the session? Is there a stable challenge id in the URL?

Until then this fails with a useful message rather than a traceback, and
`ctf adopt` still imports existing ctfLearn directories — adoption reads the
filesystem and does not need a platform implementation.
"""

from __future__ import annotations

from pathlib import Path

from ..models import Challenge
from ..registry import ManualStepRequired, Platform, register

_NOT_IMPLEMENTED = """
ctfLearn is not implemented yet — nobody has looked at how it serves files.

To implement it, follow docs/PLATFORMS.md § Adding a platform:
  1. DevTools -> Network, open a challenge, find the listing request.
  2. Replay it with plain curl. Works headlessly? Implement refresh_index
     directly. 403? Use the console-snippet + ManualStepRequired pattern.
  3. curl -sI one artifact URL with NO cookies. If it is a public CDN, the
     download path already works and you are nearly done.

Meanwhile: `ctf adopt` imports ctfLearn folders you already have, and you can
create one by hand with `ctf adopt` after downloading files yourself.
""".strip()


@register
class CTFLearn(Platform):
    name = "ctfLearn"          # matches the existing directory name
    url = "https://ctflearn.com"

    def resolve(self, ref: str) -> list[Challenge]:
        raise ManualStepRequired(
            reason="ctfLearn is not implemented",
            instructions=_NOT_IMPLEMENTED,
            resume_with="",
        )

    def refresh_index(self, source: Path | None = None, on_unknown_host=None) -> int:
        raise ManualStepRequired(
            reason="ctfLearn is not implemented",
            instructions=_NOT_IMPLEMENTED,
            resume_with="",
        )

    def index_status(self) -> str:
        return "not implemented"

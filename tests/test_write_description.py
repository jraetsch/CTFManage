"""write_description() must never leak hint text into challenge.md: hints are
spoilers, and `ctf hint` is the only deliberate way to read them (see
cmd_show's comment in ctf/cli.py). This mirrors that same rule for the
scaffolded file.
"""

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from ctf.materialize import write_description


@dataclass
class FakeChallenge:
    name: str = "Glory of the Garden"
    category: str | None = None
    difficulty: str | None = None
    event: str | None = None
    author: str | None = None
    description: str | None = None
    endpoints: list = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    url: str | None = None


class TestWriteDescription(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dirpath = Path(self.tmp.name)

    def test_hint_text_is_not_written(self):
        chal = FakeChallenge(hints=["check the EXIF data", "it's a JPEG comment"])

        write_description(self.dirpath, chal)

        text = (self.dirpath / "challenge.md").read_text()
        self.assertNotIn("check the EXIF data", text)
        self.assertNotIn("it's a JPEG comment", text)

    def test_hint_count_and_pointer_are_written(self):
        chal = FakeChallenge(hints=["one", "two", "three"])

        write_description(self.dirpath, chal)

        text = (self.dirpath / "challenge.md").read_text()
        self.assertIn("## Hints", text)
        self.assertIn("3 available", text)
        self.assertIn("ctf hint", text)

    def test_no_hints_section_when_no_hints(self):
        chal = FakeChallenge(hints=[])

        write_description(self.dirpath, chal)

        text = (self.dirpath / "challenge.md").read_text()
        self.assertNotIn("## Hints", text)

    def test_never_overwrites_existing_file(self):
        target = self.dirpath / "challenge.md"
        target.write_text("user's own notes\n")

        result = write_description(self.dirpath, FakeChallenge(hints=["spoiler"]))

        self.assertIsNone(result)
        self.assertEqual(target.read_text(), "user's own notes\n")


if __name__ == "__main__":
    unittest.main()

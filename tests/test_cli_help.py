"""The command listing printed by bare `ctf` must not drift from the parser."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctf.cli import build_parser, usage_text  # noqa: E402


class TestCommandListing(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()
        self.listed = [n for n, _ in self.parser.ctf_commands if n]
        self.real = set(self.parser._subparsers._group_actions[0].choices)

    def test_listing_matches_the_parser(self):
        """A command added without a summary would be invisible in `ctf`."""
        self.assertEqual(set(self.listed), self.real)

    def test_no_duplicates(self):
        self.assertEqual(len(self.listed), len(set(self.listed)))

    def test_every_command_has_a_summary(self):
        for name, summary in self.parser.ctf_commands:
            if name:
                with self.subTest(cmd=name):
                    self.assertTrue(summary.strip())

    def test_usage_text_renders_all_commands(self):
        text = usage_text(self.parser)
        self.assertTrue(text.startswith("usage: ctf "))
        for name in self.listed:
            with self.subTest(cmd=name):
                self.assertIn(name, text)


class TestHelpLevels(unittest.TestCase):
    def test_bare_listing_is_shorter_than_the_guide(self):
        """The two levels must stay distinct — that is the whole point."""
        from ctf.cli import HELP
        listing = usage_text(build_parser())
        self.assertLess(len(listing.splitlines()), len(HELP.splitlines()))

    def test_guide_does_not_re_list_every_command(self):
        """`ctf help` duplicating the listing is what made it too long."""
        from ctf.cli import HELP
        listed = [n for n, _ in build_parser().ctf_commands if n]
        mentioned = sum(1 for n in listed if f"ctf {n}" in HELP)
        self.assertLess(mentioned, len(listed) / 2)


if __name__ == "__main__":
    unittest.main()

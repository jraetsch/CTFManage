"""Adversarial tests for the two functions that turn untrusted input into paths.

Required by docs/ARCHITECTURE.md § Security requirements. Run with:
    python3 -m unittest discover -s tests -t . -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctf.materialize import (  # noqa: E402
    UnsafeName,
    safe_filename,
    safe_target,
    scan_root,
    slugify,
)

# The user's actual directory names, read off ~/workspace/ctfs/picoCTF on
# 2026-08-04. Their *source* challenge names are unknown (they require the
# index), so these pin stability, not fidelity — see § Slugification.
REAL_DIRS = [
    "binary_digits", "can_you_see", "Coppersmith", "disko_1", "disko1",
    "disko_2", "disko_3", "disko_4", "forensics_git_0", "forensics_git_1",
    "forensics_git_2", "format_string_0", "glory_of_the_garden",
    "hidden_in_plainsight", "hideme", "information", "msb", "red",
    "riddle_registry", "scan_surprise", "st3go", "timeline_0", "timeline_1",
]


class TestSlugify(unittest.TestCase):
    def test_documented_examples(self):
        cases = {
            "Glory of the Garden": "glory_of_the_garden",
            "Disko 1": "disko_1",
            "format-string-0": "format_string_0",
            "can you see?": "can_you_see",
            "Can You See Me": "can_you_see_me",
            "Timestamped Secrets": "timestamped_secrets",
            "St3go": "st3go",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(slugify(name), expected)

    def test_idempotent_on_existing_dirs(self):
        """Re-slugging a lowercase existing directory must be a no-op.

        If this fails, `ctf adopt` followed by `ctf get` would produce a second
        directory for a challenge that is already on disk — the disko_1/disko1
        bug, reintroduced by the tool itself.
        """
        for d in REAL_DIRS:
            with self.subTest(dir=d):
                self.assertEqual(slugify(d.lower()), d.lower())

    def test_output_charset_is_closed(self):
        import re
        hostile = [
            "../../etc/passwd", "a/b", ".envrc", "-rf", "  ", "a\x00b",
            "C:\\Windows", "..", ".", "%2e%2e%2f", "a" * 500, "Café Über",
        ]
        for name in hostile:
            with self.subTest(name=name):
                try:
                    slug = slugify(name)
                except UnsafeName:
                    continue
                self.assertRegex(slug, r"^[a-z0-9][a-z0-9_]*$")
                self.assertNotIn("..", slug)

    def test_empty_is_rejected(self):
        for name in ("", "   ", "!!!", "../"):
            with self.subTest(name=name):
                with self.assertRaises(UnsafeName):
                    slugify(name)

    def test_collapses_runs(self):
        self.assertEqual(slugify("a   b"), "a_b")
        self.assertEqual(slugify("a---b"), "a_b")
        self.assertEqual(slugify("__a__b__"), "a_b")

    def test_leading_digit_is_prefixed(self):
        self.assertEqual(slugify("2warm"), "c_2warm")


class TestSafeFilename(unittest.TestCase):
    def test_normal_urls(self):
        cases = {
            "https://artifacts.picoctf.net/c/500/files.zip": "files.zip",
            "https://artifacts.picoctf.net/c_mimas/75/original.jpg": "original.jpg",
            "https://challenge-files.picoctf.net/c_plain_mesa/95b5/message.txt": "message.txt",
            "https://x/a%20b.txt": "a b.txt",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(safe_filename(url), expected)

    def test_traversal_rejected(self):
        for url in [
            "https://evil/../../.zshrc",
            "https://evil/..%2f..%2f.zshrc",
            "https://evil/a/../..",
            "https://evil/",
            "https://evil/.",
            "https://evil/..",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(UnsafeName):
                    safe_filename(url)

    def test_dotfiles_rejected(self):
        for url in [
            "https://evil/.envrc",
            "https://evil/x/.zshrc",
            "https://evil/%2Eenvrc",
            "https://evil/.git",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(UnsafeName):
                    safe_filename(url)

    def test_control_chars_and_separators_rejected(self):
        for name in ["a\x00b", "a\nb", "a\\b", "a\tb"]:
            with self.subTest(name=name):
                with self.assertRaises(UnsafeName):
                    safe_filename(name)


class TestSafeTarget(unittest.TestCase):
    def test_stays_inside(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            t = safe_target(root, "https://x/files.zip")
            self.assertEqual(t.parent, root.resolve())
            self.assertTrue(t.is_relative_to(root.resolve()))

    def test_traversal_is_neutralised_not_escaped(self):
        """Traversal collapses to a bare basename inside the directory.

        `.../../../etc/passwd` becomes `<chal>/passwd`, which is contained and
        therefore fine. The containment assertion in safe_target() is currently
        unreachable given basename-first ordering — it is kept deliberately, so
        that a future refactor which stops stripping the directory part fails
        loudly instead of silently writing outside the tree.
        """
        with tempfile.TemporaryDirectory() as td:
            root = (Path(td) / "chal").resolve()
            root.mkdir()
            for url, expected in [
                ("https://x/../../../etc/passwd", "passwd"),
                ("https://x/../sibling", "sibling"),
            ]:
                with self.subTest(url=url):
                    t = safe_target(root, url)
                    self.assertEqual(t.name, expected)
                    self.assertEqual(t.parent, root)
                    self.assertTrue(t.is_relative_to(root))

    def test_symlink_escape_is_refused(self):
        """A pre-existing symlink must not redirect a write out of the tree."""
        from ctf.materialize import assert_not_symlink
        with tempfile.TemporaryDirectory() as td:
            root = (Path(td) / "chal").resolve()
            root.mkdir()
            outside = Path(td) / "outside.txt"
            outside.write_text("original")
            (root / "files.zip").symlink_to(outside)
            with self.assertRaises(UnsafeName):
                assert_not_symlink(root / "files.zip")


class TestScanRoot(unittest.TestCase):
    def test_skips_dotdirs_and_venvs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "picoCTF" / "hideme").mkdir(parents=True)
            (root / "picoCTF" / ".venv").mkdir()
            (root / ".ctftool").mkdir()
            found = scan_root(root)
            self.assertEqual(found, [("picoCTF", "hideme", root / "picoCTF" / "hideme")])


if __name__ == "__main__":
    unittest.main()

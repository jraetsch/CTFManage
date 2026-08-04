"""`ctf list --available`: browsing the catalogue, not just what is tracked."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctf import config as cfgmod  # noqa: E402
from ctf.cli import main  # noqa: E402
from ctf.platforms.picoctf import PicoCTF  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "picoctf-browser-dump.json"


class ListAvailableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ctfs = root / "ctfs"
        self.ctfs.mkdir()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(root / "cfg")
        cfgmod.save(cfgmod.Config(ctf_root=self.ctfs))
        PicoCTF().refresh_index(FIXTURE, on_unknown_host=None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self.tmp.cleanup()

    def run_cli(self, *argv):
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            code = main(list(argv))
        return code, o.getvalue(), e.getvalue()

    # -- behaviour ---------------------------------------------------------

    def test_lists_catalogue_not_just_tracked(self):
        """Nothing is tracked yet, but the catalogue still has entries."""
        code, _, err = self.run_cli("list", "--available")
        self.assertEqual(code, 0)
        self.assertIn("Glory of the Garden", err)
        self.assertIn("Sum-O-Primes", err)
        self.assertIn("4 in the catalogue", err)

    def test_plain_list_is_empty_while_catalogue_is_not(self):
        """The two views are distinct — that is the whole feature."""
        _, _, plain = self.run_cli("list")
        self.assertIn("nothing tracked yet", plain)
        _, _, avail = self.run_cli("list", "--available")
        self.assertIn("Glory of the Garden", avail)

    def test_online_is_an_alias(self):
        _, _, a = self.run_cli("list", "--available")
        _, _, b = self.run_cli("list", "--online")
        self.assertEqual(a, b)

    def test_tracked_challenges_are_marked(self):
        self.run_cli("get", "Glory of the Garden", "--no-download")
        _, _, err = self.run_cli("list", "--available")
        self.assertIn("3 not", err)
        self.assertIn("1 tracked", err)

    def test_untracked_hides_what_you_have(self):
        self.run_cli("get", "Glory of the Garden", "--no-download")
        _, _, err = self.run_cli("list", "--available", "--untracked")
        self.assertNotIn("Glory of the Garden", err)
        self.assertIn("Sum-O-Primes", err)

    def test_category_filter(self):
        _, _, err = self.run_cli("list", "--available", "--category", "Cryptography")
        self.assertIn("Timestamped Secrets", err)
        self.assertNotIn("Sum-O-Primes", err)

    def test_json_goes_to_stdout_and_parses(self):
        code, sout, _ = self.run_cli("list", "--available", "--json")
        self.assertEqual(code, 0)
        data = json.loads(sout)
        self.assertEqual(len(data), 4)
        self.assertEqual({d["platform"] for d in data}, {"picoCTF"})
        self.assertIn("status", data[0])

    def test_no_index_suggests_indexing(self):
        PicoCTF()._index_path().unlink()
        _, _, err = self.run_cli("list", "--available", "--platform", "picoCTF")
        self.assertIn("ctf index picoCTF", err)

    def test_all_tracked_says_so(self):
        for name in ("Glory of the Garden", "Sum-O-Primes",
                     "Timestamped Secrets", "Future Host Challenge"):
            self.run_cli("get", name, "--no-download")
        _, _, err = self.run_cli("list", "--available", "--untracked")
        self.assertIn("nothing left", err)


if __name__ == "__main__":
    unittest.main()

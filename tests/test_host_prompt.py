"""Unknown artifact hosts: prompting, persistence, and safe defaults."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctf import config as cfgmod  # noqa: E402
from ctf.platforms.picoctf import ARTIFACT_HOSTS, PicoCTF, classify  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "picoctf-browser-dump.json"
NEW_HOST = "files2.picoctf.net"


class TestClassifyWithExtraHosts(unittest.TestCase):
    def test_extra_host_promotes_suspect_to_artifact(self):
        url = f"https://{NEW_HOST}/c_new/abc/data.bin"
        arts, suspect = classify([url])
        self.assertEqual((arts, suspect), ([], [url]))

        arts, suspect = classify([url], ARTIFACT_HOSTS | {NEW_HOST})
        self.assertEqual((arts, suspect), ([url], []))

    def test_extra_host_does_not_widen_to_third_parties(self):
        arts, suspect = classify(["https://evil.example/a.zip"],
                                 ARTIFACT_HOSTS | {NEW_HOST})
        self.assertEqual((arts, suspect), ([], []))


class TestRefreshIndexPrompting(unittest.TestCase):
    """Drives refresh_index with a stub callback — no tty needed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "ctfs").mkdir()
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(root / "cfg")
        cfgmod.save(cfgmod.Config(ctf_root=root / "ctfs"))
        self.plat = PicoCTF()

    def tearDown(self):
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self.tmp.cleanup()

    def _artifacts_for(self, name):
        path = self.plat._index_path()
        for c in json.loads(path.read_text())["challenges"]:
            if c["name"] == name:
                return c["artifacts"]
        return None

    def test_callback_is_offered_the_unknown_host(self):
        seen = []

        def ask(host, urls):
            seen.append((host, len(urls)))
            return False

        self.plat.refresh_index(FIXTURE, on_unknown_host=ask)
        self.assertEqual(seen, [(NEW_HOST, 1)])

    def test_declining_leaves_the_url_out_and_writes_no_config(self):
        self.plat.refresh_index(FIXTURE, on_unknown_host=lambda h, u: False)
        self.assertEqual(self._artifacts_for("Future Host Challenge"), [])
        self.assertEqual(self.plat.configured_hosts(), [])

    def test_accepting_applies_in_the_same_run(self):
        """The whole point: no second index build needed."""
        self.plat.refresh_index(FIXTURE, on_unknown_host=lambda h, u: True)
        self.assertEqual(self._artifacts_for("Future Host Challenge"),
                         [f"https://{NEW_HOST}/c_new/abc/data.bin"])

    def test_accepted_host_persists_and_is_not_re_asked(self):
        self.plat.refresh_index(FIXTURE, on_unknown_host=lambda h, u: True)
        self.assertEqual(self.plat.configured_hosts(), [NEW_HOST])

        def fail_if_called(host, urls):
            raise AssertionError(f"re-asked about a remembered host: {host}")

        self.plat.refresh_index(FIXTURE, on_unknown_host=fail_if_called)
        self.assertEqual(self._artifacts_for("Future Host Challenge"),
                         [f"https://{NEW_HOST}/c_new/abc/data.bin"])

    def test_no_callback_trusts_nothing(self):
        """Absent a way to ask, an unknown host must not be trusted."""
        self.plat.refresh_index(FIXTURE, on_unknown_host=None)
        self.assertEqual(self._artifacts_for("Future Host Challenge"), [])
        self.assertEqual(self.plat.configured_hosts(), [])

    def test_remembering_is_idempotent(self):
        self.plat.remember_hosts([NEW_HOST])
        self.plat.remember_hosts([NEW_HOST])
        self.assertEqual(self.plat.configured_hosts(), [NEW_HOST])

    def test_host_list_round_trips_through_toml(self):
        self.plat.remember_hosts(["a.picoctf.net", "b.picoctf.net"])
        reloaded = cfgmod.load()
        self.assertEqual(reloaded.platforms["picoCTF"]["artifact_hosts"],
                         ["a.picoctf.net", "b.picoctf.net"])


if __name__ == "__main__":
    unittest.main()

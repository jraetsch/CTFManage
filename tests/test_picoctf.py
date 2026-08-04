"""Platform-layer tests: host classification and schema-tolerant mapping."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctf.platforms.picoctf import classify, normalise  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "picoctf-browser-dump.json"


class TestClassify(unittest.TestCase):
    def test_both_known_hosts_are_artifacts(self):
        arts, suspect = classify([
            "https://artifacts.picoctf.net/c/500/files.zip",
            "https://challenge-files.picoctf.net/c_plain_mesa/95b5/message.txt",
        ])
        self.assertEqual(len(arts), 2)
        self.assertEqual(suspect, [])

    def test_docs_links_are_dropped_silently(self):
        arts, suspect = classify(["https://picoctf.org/learn",
                                  "https://play.picoctf.org/practice"])
        self.assertEqual(arts, [])
        self.assertEqual(suspect, [])

    def test_unknown_picoctf_host_becomes_a_suspect(self):
        """The regression that motivated the whole design.

        A new artifact host must surface as a visible prompt, never as silence.
        """
        arts, suspect = classify(["https://files2.picoctf.net/c_new/abc/data.bin"])
        self.assertEqual(arts, [])
        self.assertEqual(len(suspect), 1)

    def test_third_party_hosts_are_dropped(self):
        arts, suspect = classify(["https://github.com/x/y", "https://evil.example/a.zip"])
        self.assertEqual((arts, suspect), ([], []))

    def test_deduplicates(self):
        u = "https://artifacts.picoctf.net/c/1/a.zip"
        arts, _ = classify([u, u, u])
        self.assertEqual(arts, [u])


class TestNormalise(unittest.TestCase):
    def setUp(self):
        self.records = json.loads(FIXTURE.read_text())["challenges"]
        self.by_name = {r["name"]: normalise(r) for r in self.records}

    def test_description_is_taken_from_instance(self):
        """`description` lives on /instance/, not on the challenge record.

        Getting this wrong yields an index with zero artifact URLs for every
        challenge, because the URLs are embedded in the description text.
        """
        e = self.by_name["Glory of the Garden"]
        self.assertIn("garden", e["description"])
        self.assertEqual(e["artifacts"], ["https://artifacts.picoctf.net/c/80/Flag.pdf"])

    def test_nested_category_is_flattened(self):
        self.assertEqual(self.by_name["Glory of the Garden"]["category"], "Forensics")

    def test_endpoints_only_challenge_has_no_artifacts(self):
        e = self.by_name["Sum-O-Primes"]
        self.assertEqual(e["artifacts"], [])
        self.assertEqual(e["endpoints"], [{"label": "netcat",
                                           "endpoint": "nc saturn.picoctf.net 51234"}])

    def test_points_come_from_event_points(self):
        """There is no `points` field — reading it gave NULL for everything."""
        self.assertEqual(self.by_name["Glory of the Garden"]["points"], 50)
        self.assertEqual(self.by_name["Sum-O-Primes"]["points"], 300)

    def test_platform_flags_are_captured(self):
        garden = self.by_name["Glory of the Garden"]
        self.assertEqual(garden["tags"], ["forensics", "beginner"])
        self.assertTrue(garden["solved_on_platform"])
        self.assertFalse(garden["retired"])
        self.assertTrue(self.by_name["Timestamped Secrets"]["retired"])
        self.assertTrue(self.by_name["Sum-O-Primes"]["on_demand"])

    def test_platform_tags_do_not_reach_the_user_tags_column(self):
        """`tags` in the DB is the user's; the platform's live in the index."""
        from ctf.db import _PLATFORM_COLUMNS
        self.assertNotIn("tags", _PLATFORM_COLUMNS)

    def test_hints_survive(self):
        self.assertEqual(self.by_name["Sum-O-Primes"]["hints"],
                         ["RSA is fragile when p and q are close."])

    def test_missing_fields_degrade_to_none(self):
        """A field rename must produce a NULL column, not a traceback."""
        e = normalise({"id": 1, "name": "Bare", "_instance": {}, "_urls": []})
        self.assertEqual(e["name"], "Bare")
        self.assertIsNone(e["category"])
        self.assertIsNone(e["description"])
        self.assertEqual(e["artifacts"], [])

    def test_survives_a_completely_alien_shape(self):
        e = normalise({"title": "Renamed", "pk": 7, "_urls": []})
        self.assertEqual(e["name"], "Renamed")
        self.assertEqual(e["platform_id"], "7")


class TestChallengeModel(unittest.TestCase):
    def test_has_content_counts_endpoints(self):
        from ctf.models import Challenge, Endpoint, Artifact
        self.assertFalse(Challenge(platform="p", name="n").has_content)
        self.assertTrue(Challenge(platform="p", name="n",
                                  artifacts=[Artifact("https://x/a.zip")]).has_content)
        self.assertTrue(Challenge(platform="p", name="n",
                                  endpoints=[Endpoint("nc", "nc h 1")]).has_content)


if __name__ == "__main__":
    unittest.main()

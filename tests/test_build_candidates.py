"""scripts/build_candidates.py (plan Q9, 10.11.2026): announced release, distributed build and observed build kept apart.

Fictional messages only; no Slack, no device, nothing installed.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import build_candidates as bc

LINK = "https://example.invalid/archives/C0/p1"


def msg(text, **kw):
    return {"text": text, "channel": "#builds", "ts": "2026-11-10T09:00:00", "author": "dev-a", "link": LINK, **kw}


def one(text, observed="1.0.0 (38)", platform="android"):
    rows = bc.report([msg(text)], observed, platform)
    assert len(rows) == 1, rows
    return rows[0]


class Candidates(unittest.TestCase):
    def test_release_and_build_number_in_the_message_that_equal_the_observed_ones_match(self):
        r = one("New build Android 1.0.0 (38) on dev, APK in the channel")
        self.assertEqual((r["platform"], r["environment"], r["release"], r["build"], r["channel"]),
                         ("android", "dev", "1.0.0", "38", "apk"))
        self.assertEqual((r["result"], r["reasons"]), ("matches", []))

    def test_a_release_without_a_build_number_is_never_taken_for_the_build(self):
        r = one("Release 1.0.0 is out for Android on dev")
        self.assertEqual((r["release"], r["build"], r["result"]), ("1.0.0", None, "to confirm"))
        self.assertIn("the release is not the build", " ".join(r["reasons"]))

    def test_the_same_release_with_another_build_number_differs(self):
        r = one("Android 1.0.0 (39) on dev")
        self.assertEqual(r["result"], "differs")
        self.assertIn("announced 1.0.0 (39) vs observed 1.0.0 (38)", r["reasons"][0])

    def test_a_missing_platform_or_environment_is_to_confirm(self):
        self.assertIn("platform not stated", one("Build 1.0.0 (38) on dev")["reasons"])
        self.assertIn("environment not stated", one("Android 1.0.0 (38) available")["reasons"])

    def test_production_is_to_confirm_even_when_everything_else_is_equal(self):
        r = one("Android 1.0.0 (38) in production")
        self.assertEqual((r["environment"], r["result"]), ("production", "to confirm"))
        self.assertIn("production is not authorised by default", r["reasons"])

    def test_a_message_with_two_platforms_gives_two_candidates_and_only_the_observed_platform_can_match(self):
        rows = bc.report([msg("Android 1.0.0 (38) and iOS 1.0.0 (37) on TestFlight, dev")], "1.0.0 (38)", "android")
        by = {r["platform"]: r for r in rows}
        self.assertEqual(set(by), {"android", "ios"})
        self.assertEqual((by["android"]["build"], by["android"]["result"]), ("38", "matches"))
        self.assertEqual((by["ios"]["build"], by["ios"]["channel"], by["ios"]["result"]), ("37", "testflight", "to confirm"))
        self.assertIn("other platform", by["ios"]["reasons"][0])

    def test_a_platform_named_elsewhere_in_the_message_does_not_give_the_platform_of_the_build(self):
        # Real case (10.11.2026): a PR about an iOS crash that «is in the latest build 1.0.0 (34)». The word iOS describes the
        # fix; the message does not say which platform the build is for.
        r = one("PR 7 fixes the crash on iOS in the chat, it also covers AB#1001 and it is in the latest build 1.0.0 (34)")
        self.assertEqual((r["platform"], r["platform_hint"], r["build"], r["result"]), (None, "ios", "34", "to confirm"))
        self.assertIn("does not prove the platform of the build", " ".join(r["reasons"]))
        self.assertEqual(one("Android 1.0.0 (38) on dev")["platform_hint"], None)         # next to the build: stated

    def test_a_platform_next_to_the_build_is_stated_after_it_too(self):
        r = one("Build 1.0.0 (38) iOS on dev", platform="ios")
        self.assertEqual((r["platform"], r["platform_hint"], r["result"]), ("ios", None, "matches"))

    def test_tickets_and_prs_are_listed_as_mentions_and_never_as_proof(self):
        r = one("Android 1.0.0 (38) dev includes AB#1001 and PR 7")
        self.assertEqual(r["mentions"], ["AB#1001", "PR 7"])
        self.assertIn("do not prove", r["note"])

    def test_the_source_link_is_kept_and_unrelated_messages_give_no_candidate(self):
        self.assertEqual(one("Android 1.0.0 (38) dev")["source"]["link"], LINK)
        self.assertEqual(bc.report([msg("Good morning team, meeting at 10")], "1.0.0 (38)", "android"), [])

    def test_observed_text_is_read_in_both_forms_and_an_unknown_build_is_to_confirm(self):
        self.assertEqual(bc.parse_observed("1.0.0 (38)"), {"release": "1.0.0", "build": "38"})
        self.assertEqual(bc.parse_observed("release 1.0.0 build 38"), {"release": "1.0.0", "build": "38"})
        r = one("Android 1.0.0 (38) dev", observed="1.0.0")
        self.assertEqual(r["result"], "to confirm")
        self.assertIn("observed build number unknown", r["reasons"])

    def test_cli_prints_a_line_per_candidate_and_json_on_request_and_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([msg("Android 1.0.0 (38) dev")], f)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(bc.main([path, "--observed", "1.0.0 (38)", "--platform", "android"]), 0)
            self.assertIn("MATCHES", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                bc.main([path, "--observed", "1.0.0 (38)", "--platform", "android", "--json"])
            self.assertEqual(json.loads(out.getvalue())[0]["result"], "matches")
            self.assertEqual(os.listdir(d), ["m.json"])


if __name__ == "__main__":
    unittest.main()

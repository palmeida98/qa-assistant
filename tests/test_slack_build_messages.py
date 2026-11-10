"""scripts/slack_build_messages.py and the deployment handling of scripts/build_candidates.py (phase B, 10.11.2026).

Fictional connector output only; no Slack, no device, nothing installed. The parser is offline and read only.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import build_candidates as bc
import slack_build_messages as sbm

CH = "C0FICTION1"
RAW = """Channel: #builds (C0FICTION1)

=== Message from Dev A <dev.a@example.invalid> (U111AAA) at 2026-10-30 10:01:33 GMT ===
Message TS: 1793354493.683729
<@U222BBB|Dev B> fix is in the latest build 1.0.0 (34), see <mailto:qa@example.invalid|qa@example.invalid>
Reactions: white_check_mark (3)

=== Message from CI (B0CI0001) at 2026-11-02 19:22:13 GMT ===
Message TS: 1793647333.892259

Thread: 34 replies (latest: 2026-11-03 11:11:26 GMT)
Attachment: Deployment on stage <https://ci.example.invalid/env|Development> succeeded.
Deployment status: Succeeded
Release: <https://ci.example.invalid/r?releaseId=33&amp;_a=summary|Release-33>
Release pipeline: <https://ci.example.invalid/p|[DEV] Mobile Release>

=== Message from Dev C (U333CCC) at 2026-11-02 19:35:30 GMT ===
a new build is out
"""


class Parse(unittest.TestCase):
    def setUp(self):
        self.msgs, self.skipped = sbm.parse(RAW, CH)

    def test_exact_ts_is_kept_and_a_block_without_it_is_skipped_never_approximated(self):
        self.assertEqual([m["ts"] for m in self.msgs], ["1793354493.683729", "1793647333.892259"])
        self.assertEqual(len(self.skipped), 1)
        self.assertIn("no exact Message TS", self.skipped[0]["reason"])

    def test_names_emails_and_user_ids_are_not_kept(self):
        blob = repr(self.msgs)
        for secret in ("Dev A", "Dev B", "dev.a@example.invalid", "qa@example.invalid", "U111AAA", "U222BBB"):
            self.assertNotIn(secret, blob)
        self.assertEqual([m["author"] for m in self.msgs], ["member", "bot"])
        self.assertIn("@member", self.msgs[0]["text"])
        self.assertIn("[email]", self.msgs[0]["text"])

    def test_thread_replies_and_the_link_are_only_what_is_known(self):
        self.assertEqual(self.msgs[1]["thread_replies"], 34)
        self.assertIsNone(self.msgs[0]["link"])                     # the connector does not expose the domain
        msgs, _ = sbm.parse(RAW, CH, "acme")
        self.assertEqual(msgs[0]["link"], f"https://acme.slack.com/archives/{CH}/p1793354493683729")

    def test_the_concise_output_has_no_ts_and_yields_nothing(self):
        msgs, skipped = sbm.parse("Dev A <dev.a@example.invalid>: build 1.0.0 (34) [2026-10-30 10:01:33 GMT]", CH)
        self.assertEqual((msgs, skipped), ([], []))

    def test_the_module_has_no_network_or_slack_client(self):
        with open(os.path.join(ROOT, "scripts", "slack_build_messages.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | \
                   {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(imported & {"requests", "urllib", "http", "socket", "slack_sdk", "subprocess", "shutil"})


class Deployments(unittest.TestCase):
    def setUp(self):
        self.msgs, _ = sbm.parse(RAW, CH)

    def test_a_pipeline_notification_is_a_deployment_never_a_build_candidate(self):
        deps = bc.deployments(self.msgs)
        self.assertEqual(len(deps), 1)
        d = deps[0]
        self.assertEqual((d["pipeline_release_id"], d["environment"], d["status"], d["pipeline"]),
                         ("Release-33", "dev", "succeeded", "[DEV] Mobile Release"))
        self.assertIsNone(d["app_build"])
        self.assertIsNone(d["platform"])
        self.assertIn("not an app build number", d["note"])
        self.assertEqual(d["source"]["ts"], "1793647333.892259")
        rows = bc.report(self.msgs, "1.0.0 (34)", "android")
        self.assertEqual([r["source"]["ts"] for r in rows], ["1793354493.683729"])

    def test_the_announced_build_keeps_platform_and_distribution_to_confirm(self):
        rows = bc.report(self.msgs, "1.0.0 (34)", "android")
        self.assertEqual((rows[0]["release"], rows[0]["build"], rows[0]["platform"], rows[0]["result"]),
                         ("1.0.0", "34", None, "to confirm"))
        self.assertIn("platform not stated", rows[0]["reasons"])

    def test_a_release_id_is_not_compared_with_the_observed_build(self):
        # Release-33 must never be read as build 33, even when the observed build is 33.
        self.assertEqual([r for r in bc.report(self.msgs, "1.0.0 (33)", "android") if r["build"] == "33"], [])


if __name__ == "__main__":
    unittest.main()

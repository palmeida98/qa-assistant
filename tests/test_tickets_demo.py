"""The fictional ticket demo of examples/demo (plan F19): /analyze-ticket and /test-pbi can be shown with no
credentials and no tracker.

The demo's `tickets:` source uses the browser route, so a bundle goes through `ingest`: two fictional checks
(examples/demo/tickets/bundle_check1.json, bundle_check2.json) of the same PBI, a few days apart. The test copies the demo to a
temporary folder (the engine writes `memory/` into the workspace it reads; the versioned demo stays as it is), ingests
both and checks the comparison and the preflight of the two skills. It checks the engine's contract, not an agent.

python -m unittest tests.test_tickets_demo   (from the root of qa-assistant)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "examples", "demo")
T1, T2 = "2026-11-02T10:00:00", "2026-11-05T16:00:00"


def run(*args, home=None):
    env = {**os.environ, "HOME": home} if home else None
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          check=False, env=env)


class TicketDemo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self.tmp.name, "demo")
        shutil.copytree(DEMO, self.ws, ignore=shutil.ignore_patterns("memory", ".env", "node_modules"))

    def tearDown(self):
        self.tmp.cleanup()

    def ingest(self, name, now, *flags):
        return run("-m", "adapters.tickets", self.ws, "ingest",
                   os.path.join(self.ws, "tickets", f"bundle_{name}.json"), "--now", now, *flags)

    def check(self, stamp):
        with open(os.path.join(self.ws, "memory", "checks", f"{stamp}.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_source_needs_no_credentials(self):
        r = run("-m", "adapters.tickets", self.ws, "sources")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("demo-boards: azure-devops, access browser", r.stdout)
        self.assertIn("credentials: none", r.stdout)

    def test_a_dry_run_writes_nothing(self):
        r = self.ingest("check1", T1, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "memory")))

    def test_the_second_check_says_what_changed_since_the_first(self):
        first = self.ingest("check1", T1)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.check("20261102-100000")["conclusion"], "first")
        second = self.ingest("check2", T2)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        check = self.check("20261105-160000")
        self.assertEqual(check["conclusion"], "changes")
        changes = {t["ref"]["id"]: t["changes"] for t in check["tickets"]}
        kinds = {(c["component"], c["kind"]) for c in changes["1001"]}
        self.assertIn(("fields", "changed"), kinds)       # state Active -> Resolved, the acceptance criteria
        self.assertIn(("comments", "added"), kinds)       # the developer's comment
        self.assertEqual([(c["component"], c["kind"]) for c in changes["1002"]], [("fields", "changed")])
        text = json.dumps(check)
        self.assertIn("Resolved", text)

    def test_nothing_was_written_to_a_tracker_or_to_the_versioned_demo(self):
        self.ingest("check1", T1)
        self.assertTrue(os.path.isdir(os.path.join(self.ws, "memory")))
        self.assertFalse(os.path.exists(os.path.join(DEMO, "memory")))    # the engine wrote into the copy
        self.assertFalse(os.path.exists(os.path.join(self.ws, "tracker")))

    def test_the_preflight_of_both_skills_passes_on_the_demo(self):
        for skill in ("analyze-ticket", "test-pbi"):
            with self.subTest(skill=skill):
                r = run(os.path.join("scripts", "workspace_select.py"), "preflight", "--skill", skill,
                        "--workspace", self.ws, home=self.tmp.name)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("Configuration: valid for this skill", r.stdout)

    def test_the_bundles_are_fictional(self):
        for name in ("check1", "check2"):
            with open(os.path.join(DEMO, "tickets", f"bundle_{name}.json"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("dev.azure.com/demo-shop", text)
            self.assertNotIn("@", text.replace("@odata", ""), "a bundle keeps display names only, never e-mails")


if __name__ == "__main__":
    unittest.main()

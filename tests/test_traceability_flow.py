"""The path of the five improvements, end to end, on temporary copies of fictional data (nothing leaves the temporary
folder: no network, no tracker, no device, no LibreOffice):

context/configuration -> batch -> an observation made earlier -> a bug proposal and the priority decided by the user ->
the Question answered -> dry run -> an apply with other options refused -> the approved apply -> a second apply changes
nothing -> report -> resume in a "new session" (the files are all that is left).

Two layouts and rule sets: the demo workbook (English words, `Critical..Low`) and the golden fixture configured like a
project that writes in Portuguese (`Crítica..Baixa`). The approvals are simulated inputs of the test:
this is not a human approval and not an evaluation of the agent.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tests", "golden")]
from build_fixture import build

DEMO = os.path.join(ROOT, "examples", "demo")
with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    GOLDEN = yaml.safe_load(_f)
NOW = "2026-11-08T20:00:00"


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Flow:
    cases = ("", "", "")                      # question case, failing case, passing case
    priorities = ()
    book = ""

    def make(self):
        raise NotImplementedError

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = os.path.join(self.tmp.name, "a workspace")        # a path with a space
        self.make()
        self.workbook = os.path.join(self.ws, self.book)

    def cli(self, *args):
        env = {**os.environ, "HOME": self.tmp.name}
        return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              env=env, check=False)

    def rl(self, *args):
        return self.cli("scripts/run_log.py", self.ws, *args)

    def apply(self, *args):
        return self.cli("-m", "adapters.excel.apply", self.ws, "runs/batch_flow.json", *args)

    def write_json(self, name, doc):
        path = os.path.join(self.ws, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return path

    def test_the_whole_path(self):
        question, failing, passing = self.cases
        high, other = self.priorities[1], "Nonsense"
        before_book = sha(self.workbook)

        # 1. the batch, how it runs and where the results come from
        r = self.rl("mode", "flow", "manual: documented flow with fictional data")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.rl("defect-rule")
        self.assertIn("REQUIRED", r.stdout)
        self.assertIn(self.priorities[0], r.stdout)

        # 2. an observation made EARLIER, with where its time comes from; a live one; an unknown time
        r = self.rl("add", "flow", passing, "Pass", "ok", "--environment", "test", "--build", "web-2026.11.05",
                    "--executed-at", "2026-11-05T16:21", "--executed-source", "fictional tester notes of 05.11")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.rl("add", "flow", question, "Question", "is the limit 2 or 3?",
                    "--executed-at", "unknown", "--executed-source", "notes without a time")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.rl("add", "flow", failing, "Fail", "message missing")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.rl("add", "flow", failing, "Fail", "again", "--executed-at", "2999-01-01T00:00",
                    "--executed-source", "x", "--replace")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)          # a future time is refused

        # 3. the bug: a proposal may wait; an approved one needs the user's priority, from the project's own list
        draft = {"title": "Message missing", "description": "Expected a message. Observed none.", "area": "Login",
                 "related_tc": failing}
        r = self.rl("attach", "flow", failing, "--new-defect", self.write_json("d.json", {**draft, "proposed": True}))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        audit = self.cli("scripts/qa_report.py", self.ws, "audit")
        self.assertIn("priority decision", audit.stdout)
        for bad in ({**draft}, {**draft, "priority": other}):
            r = self.rl("attach", "flow", failing, "--new-defect", self.write_json("d.json", bad), "--replace-link")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn(self.priorities[0], r.stderr)
        r = self.rl("attach", "flow", failing, "--new-defect", self.write_json("d.json", {**draft, "priority": high}),
                    "--replace-link")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # 4. the Question is still open until answered: the dry run refuses it; then the decision is recorded
        r = self.apply("--dry-run", "--detail", "--no-recalc")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("Question", r.stdout)
        r = self.rl("resolve", "flow", question, "Pass", "--reason", "the PO answered: 3 is the limit",
                    "--decided-by", "PO (fictional)", "--decision-source", "fictional message of 06.11")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(self.ws, "runs", "batch_flow.json"), encoding="utf-8") as f:
            items = {x["tc"]: x for x in json.load(f)["results"]}
        self.assertNotIn("ts", items[question])                       # the unknown time was never invented
        self.assertEqual(items[question]["resolution"]["original"]["status"], "Question")
        self.assertEqual(items[passing]["ts"], "2026-11-05T16:21:00")
        self.assertEqual(items[passing]["executed_source"], "fictional tester notes of 05.11")

        # 5. the preview, the options it covers, and an apply with other options refused before any write
        r = self.apply("--dry-run", "--detail", "--no-recalc", "--now", NOW, "--label", "[flow]", "--reported-by", "QA (fictional)")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        token = next(line.split()[2] for line in r.stdout.splitlines() if line.startswith("Preview token:"))
        self.assertTrue(token.startswith("v2-"))
        self.assertEqual(sha(self.workbook), before_book)             # a dry run writes nothing
        r = self.apply("--no-recalc", "--now", NOW, "--label", "[flow]", "--reported-by", "Someone else",
                       "--expect", token)
        self.assertEqual(r.returncode, 4)
        self.assertIn("out of date", r.stdout + r.stderr)
        self.assertEqual(sha(self.workbook), before_book)
        r = self.apply("--no-recalc", "--now", NOW, "--label", "[flow]", "--reported-by", "QA (fictional)",
                       "--expect", token)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        applied = sha(self.workbook)
        self.assertNotEqual(applied, before_book)
        r = self.apply("--no-recalc", "--now", NOW, "--label", "[flow]", "--reported-by", "QA (fictional)")
        self.assertEqual(r.returncode, 0)                             # the same batch again: nothing changes
        self.assertEqual(sha(self.workbook), applied)

        # 6. the report, and the "new session": only the files are left
        r = self.cli("scripts/qa_report.py", self.ws, "totals")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Reconciled (statuses = builds = cases): yes", r.stdout)
        self.assertIn("Cases with a current result: 3", r.stdout)
        audit = self.cli("scripts/qa_report.py", self.ws, "audit")
        self.assertIn("Question resolved", audit.stdout)
        self.assertNotIn("Question open", audit.stdout)
        self.assertNotIn("priority decision", audit.stdout)
        # an applied batch is never touched, and a resolution of it goes to a new batch
        r = self.rl("add", "flow", "TC-LOGIN-007" if self.cases[2].startswith("TC-CHK") else "TC-SHOP-003", "Pass", "x")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("never changed", r.stderr)


class DemoLayout(Flow, unittest.TestCase):
    cases = ("TC-CHK-001", "TC-LOGIN-004", "TC-CHK-002")
    priorities = ("Critical", "High", "Medium", "Low")
    book = os.path.join("workbook", "demo-plan.xlsx")

    def make(self):
        shutil.copytree(DEMO, self.ws, ignore=shutil.ignore_patterns("memory", ".env", "node_modules", "runs"))


class GoldenLayoutWithPortugueseRules(Flow, unittest.TestCase):
    cases = ("TC-SHOP-001", "TC-SHOP-002", "TC-ACCOUNT-001")
    priorities = ("Crítica", "Alta", "Média", "Baixa")
    book = GOLDEN["file"]

    def make(self):
        os.makedirs(self.ws)
        build(os.path.join(self.ws, GOLDEN["file"]))
        excel = copy.deepcopy(GOLDEN)
        excel["defects"]["priorities"] = list(self.priorities)
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"project": "fictional", "environments": {"test": "https://x.test"}, "tracker": "excel",
                            "excel": excel}, f)


if __name__ == "__main__":
    unittest.main()

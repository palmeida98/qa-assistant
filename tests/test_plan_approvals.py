"""The QA's plan decision (increment 2B, docs/procedures/approvals.md §5): the review of a draft, the approval of its
coverage by a canonical hash, and the gate in run_log.py plan / add, the CI and the PBI's record. Through the real entry
points (the command lines) where it matters, on fictional Demo Shop workspaces in temporary folders only.

python -m unittest tests.test_plan_approvals   (from the root of qa-assistant)
"""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import approvals
import pbi_state
import quarantine
import run_log as rl
import workspace_ci
import yaml
from apk_kit import make_apk
from workspace_kit import workspace

IDENT = {"id": "shop", "kind": "real"}
PLATFORMS = {"platforms": {"android": ["batch_and_*"], "ios": ["batch_ios_*"]}}
BUILD = "1.0.0 (39)"
PLAN = {"approvals": {"plan": "required"}}
BOTH = {"approvals": {"plan": "required", "run": "per-platform"}}
MOBILE = {"platform": "android", "flows": "flows", "app_id": "com.demoshop.app",
          "build": {"source": "release build-1.0.0-39", "sha256": "a" * 64, "version": BUILD}}
CI = {"timeout_minutes": 60, "max_turns": 80, "secrets": []}

DRAFT = """\
# Draft of the Android batch of PBI 42434 (a comment: never part of the approval)
pbi: "42434"
run:
  platform: android
  environment: test
  build: "1.0.0 (39)"    # the intended build: not part of the coverage hash
cases: [TC-SHOP-001, TC-SHOP-002, TC-SHOP-004]
notes:
  - "TC-SHOP-002: record the reminder's initial state apart from the verdict"
"""


def sha(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Base(unittest.TestCase):
    policy = PLAN
    ident = IDENT

    def setUp(self):
        extra = {"workspace": self.ident, "report": PLATFORMS, "mobile": MOBILE, "ci": CI}
        if self.policy:
            extra["policy"] = self.policy
        self._tmp = workspace(**extra)
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.ws, "flows"), exist_ok=True)

    # --- helpers: the command lines as the skills run them ----------------------------------------------------------
    def cli(self, script, *args, ws=None):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        env.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", script), ws or self.ws, *args],
                              cwd=ws or self.ws, env=env, capture_output=True, text=True, encoding="utf-8",
                              check=False)

    def write(self, rel, text):
        path = os.path.join(self.ws, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def review(self, batch="batch_and_01", draft="analysis/plan_and_01.yaml", *extra):
        r = self.cli("approvals.py", "review-plan", "--batch", batch, "--draft", draft, "--pbi", "42434", *extra)
        found = re.search(r"Coverage hash \(plan-canonical-v1\): `([0-9a-f]{64})`", r.stdout)
        return r, (found.group(1) if found else None)

    def approve(self, digest, batch="batch_and_01", draft="analysis/plan_and_01.yaml", review=None, ws=None):
        args = ["approve-plan", "--pbi", "42434", "--batch", batch, "--draft", draft, "--sha", digest,
                "--declared-by", "QA (fictional)", "--message-ref", "session test-0002 · 10:15Z",
                "--quote", "Aprovo a cobertura do plano", "--ticket", "42434=24", "--ticket", "42732=5"]
        if review:
            args += ["--review", review]
        return self.cli("approvals.py", *args, ws=ws)

    def approved(self, text=DRAFT, batch="batch_and_01", draft="analysis/plan_and_01.yaml"):
        self.write(draft, text)
        r, digest = self.review(batch, draft)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.approve(digest, batch, draft, review=f"analysis/{os.path.basename(draft)[:-5]}.review.md")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return digest

    def check(self, batch="batch_and_01", *extra):
        r = self.cli("approvals.py", "check-plan", "--batch", batch, "--json", *extra)
        return r.returncode, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else r.stderr)

    def ledger(self):
        path = approvals.ledger_path(self.ws)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def log(self, batch="batch_and_01"):
        return rl.run_log_path(self.ws, batch)

    def plan_cli(self, batch="batch_and_01"):
        return self.cli("run_log.py", "plan", batch, f"batches/{batch}.yaml")

    def add_cli(self, case="TC-SHOP-001", batch="batch_and_01", build=BUILD):
        return self.cli("run_log.py", "add", batch, case, "Pass", "seen", "--environment", "test", "--build", build)

    def ci_batch(self, batch="batch_and_01", run_id="9"):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": os.path.join(self.ws, "gh_output")}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = workspace_ci.main([self.ws, "batch", "--input", batch, "--mode", "conversational",
                                      "--run-id", run_id])
        return code, out.getvalue()

    def quarantine_q1(self, case="TC-SHOP-004"):
        quarantine.propose(self.ws, "Q-1", "BUG-1", [case], ["android"], [], ["test"], "known failure",
                           "the fix is in a build", None)
        quarantine.decide(self.ws, "Q-1", "activate", "QA (fictional)", "session test-0001")


class Approval(Base):
    def test_review_then_approval_saves_the_presented_version_and_records_it_as_declared(self):
        self.write("analysis/plan_and_01.yaml", DRAFT)
        r, digest = self.review()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("authorises no run", r.stdout)
        md = os.path.join(self.ws, "analysis", "plan_and_01.review.md")
        with open(md, encoding="utf-8") as f:
            self.assertIn(digest, f.read())
        self.assertFalse(os.path.exists(os.path.join(self.ws, "batches", "batch_and_01.yaml")))   # not before the QA
        self.assertEqual(self.ledger(), [])
        r = self.approve(digest, review="analysis/plan_and_01.review.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sha(os.path.join(self.ws, "batches", "batch_and_01.yaml")),
                         sha(os.path.join(self.ws, "analysis", "plan_and_01.yaml")))      # the presented bytes
        [e] = self.ledger()
        self.assertEqual((e["gate"], e["workspace"], e["pbi"], e["batch"], e["platform"], e["environment"]),
                         ("plan", IDENT, "42434", "batch_and_01", "android", "test"))
        self.assertEqual((e["plan"], e["plan_sha256"], e["hash_method"]),
                         ("batches/batch_and_01.yaml", digest, "plan-canonical-v1"))
        self.assertEqual(e["coverage"]["cases"], ["TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-004"])
        self.assertNotIn("build", e["coverage"]["run"])
        self.assertEqual(e["intended_build"], BUILD)
        self.assertEqual((e["confirmation"], e["message_ref_verified"], e["authorises_run"]),
                         ("declared-in-chat", False, False))
        self.assertIn("not a signature", e["limitation"])
        self.assertEqual(e["basis"], {"tickets": {"42434": "24", "42732": "5"}})
        self.assertEqual(e["presented"]["review"], "analysis/plan_and_01.review.md")
        self.assertEqual(e["presented"]["review_sha256"], sha(md))
        code, res = self.check()
        self.assertEqual((code, res["status"]), (0, "valid"), res)

    def test_a_draft_changed_after_the_review_is_refused_and_nothing_is_written(self):
        self.write("analysis/plan_and_01.yaml", DRAFT)
        _, digest = self.review()
        self.write("analysis/plan_and_01.yaml", DRAFT.replace("TC-SHOP-004]", "TC-SHOP-003]"))
        r = self.approve(digest)
        self.assertEqual(r.returncode, 1)
        self.assertIn("changed since it was presented", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "batches", "batch_and_01.yaml")))
        self.assertEqual(self.ledger(), [])

    def test_an_invalid_plan_is_never_approved(self):
        for text, why in (("run: {platform: android}\ncases: [TC-SHOP-001]\n", "run.environment"),
                          ("run: {platform: android, environment: test}\ncases: [TC-NOPE-001]\n", "not in the project"),
                          ("run: {platform: android, environment: test}\ncases: [TC-SHOP-001, TC-SHOP-001]\n",
                           "repeated"),
                          ("pbi: '9'\nrun: {platform: android, environment: test}\ncases: [TC-SHOP-001]\n",
                           "declares pbi")):
            self.write("analysis/bad.yaml", text)
            r, digest = self.review(draft="analysis/bad.yaml")
            self.assertEqual(r.returncode, 1, text)
            self.assertIn(why, r.stdout)
            r = self.approve(digest, draft="analysis/bad.yaml")
            self.assertEqual(r.returncode, 1, text)
            self.assertIn(why, r.stderr)
        self.assertEqual(self.ledger(), [])

    def test_an_interrupted_approval_is_named_on_resuming_and_finished_once(self):
        self.write("analysis/plan_and_01.yaml", DRAFT)
        _, digest = self.review()
        os.makedirs(os.path.join(self.ws, "batches"), exist_ok=True)       # the copy done, the record not
        shutil.copyfile(os.path.join(self.ws, "analysis", "plan_and_01.yaml"),
                        os.path.join(self.ws, "batches", "batch_and_01.yaml"))
        code, res = self.check()
        self.assertEqual((code, res["status"]), (1, "missing"))
        self.assertIn("analysis/plan_and_01.yaml", res["next"])
        self.assertIn(f"--sha {digest}", res["next"])
        r = self.plan_cli()
        self.assertEqual(r.returncode, 1)                       # the run stays blocked until a valid decision
        self.assertFalse(os.path.exists(self.log()))
        r = self.approve(digest)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.approve(digest)                                # a resume that asks nothing again: one line
        self.assertEqual(r.returncode, 0)
        self.assertIn("already recorded", r.stdout)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.check()[1]["status"], "valid")


class Gate(Base):
    def test_without_an_approval_plan_add_and_ci_are_refused_writing_nothing(self):
        self.write("batches/batch_and_01.yaml", DRAFT)
        code, res = self.check()
        self.assertEqual((code, res["status"]), (1, "missing"))
        r = self.plan_cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("policy.approvals.plan", r.stderr)
        self.assertFalse(os.path.exists(self.log()))
        r = self.add_cli()
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(self.log()) and rl.read_log(self.log())["results"])
        code, out = self.ci_batch()
        self.assertEqual(code, 1, out)
        self.assertIn("plan approval missing", out)
        self.assertFalse(os.path.exists(self.log("batch_and_01_r9")))           # no run log, no flows
        self.assertIn("plan", workspace_ci.plan_problem(self.ws, "adhoc", "android", BUILD, "test"))

    def test_a_valid_approval_lets_the_batch_run_and_resume(self):
        self.approved()
        self.assertEqual(self.plan_cli().returncode, 0)
        self.assertEqual(self.add_cli().returncode, 0)
        self.assertEqual(self.add_cli("TC-SHOP-002").returncode, 0)            # the resume reuses the decision
        code, out = self.ci_batch()
        self.assertEqual(code, 0, out)
        log = rl.read_log(self.log("batch_and_01_r9"))
        self.assertEqual(log["plan"], "batches/batch_and_01.yaml")
        self.assertEqual(self.check("batch_and_01_r9")[1]["status"], "valid")   # a remote run log: its base's decision
        self.assertEqual(len(self.ledger()), 1)

    def test_revoked_or_unreadable_register_refuses_without_changing_results(self):
        self.approved()
        self.plan_cli()
        self.assertEqual(self.add_cli().returncode, 0)
        before = sha(self.log())
        [e] = self.ledger()
        r = self.cli("approvals.py", "revoke", e["id"], "--declared-by", "QA (fictional)", "--message-ref", "s3",
                     "--reason", "wrong scope")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.add_cli("TC-SHOP-002")
        self.assertEqual(r.returncode, 1)
        self.assertIn("revoked", r.stderr)
        self.assertEqual(sha(self.log()), before)
        with open(approvals.ledger_path(self.ws), "a", encoding="utf-8") as f:
            f.write("{not json\n")
        r = self.add_cli("TC-SHOP-002")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not JSON", r.stderr)
        self.assertEqual(sha(self.log()), before)
        self.assertEqual(self.cli("approvals.py", "check-plan", "--batch", "batch_and_01").returncode, 2)

    def test_reorder_retest_and_notes_are_stale_with_the_difference(self):
        self.quarantine_q1()
        excluded = DRAFT.replace("cases: [TC-SHOP-001, TC-SHOP-002, TC-SHOP-004]",
                                 "cases: [TC-SHOP-001, TC-SHOP-002]\nexcluded:\n  - {case: TC-SHOP-004, quarantine: Q-1}")
        self.approved(excluded)
        plan = "batches/batch_and_01.yaml"
        for text, words in (
                (excluded.replace("[TC-SHOP-001, TC-SHOP-002]", "[TC-SHOP-002, TC-SHOP-001]"),
                 "order of the cases: TC-SHOP-001 > TC-SHOP-002  ->  TC-SHOP-002 > TC-SHOP-001"),
                (excluded.replace("[TC-SHOP-001, TC-SHOP-002]", "[TC-SHOP-001, TC-SHOP-002, TC-SHOP-004]")
                 .replace("excluded:", "retest:"), "TC-SHOP-004: excluded (Q-1) -> directed retest (Q-1)"),
                (excluded.replace("apart from the verdict", "only when it is visible"), "notes:")):
            self.write(plan, text)
            code, res = self.check()
            self.assertEqual((code, res["status"]), (1, "stale"), text)
            self.assertTrue(any(words in d for d in res["diff"]), (words, res["diff"]))
            self.assertEqual(self.plan_cli().returncode, 1)
            self.write("analysis/v2.yaml", text)
            r, _ = self.review(draft="analysis/v2.yaml")
            self.assertIn(words, r.stdout)                      # the review shows the same difference to the QA

    def test_formatting_comments_old_block_and_run_build_keep_the_coverage(self):
        digest = self.approved()
        data = yaml.safe_load(DRAFT)
        data["run"]["build"] = "1.0.0 (41)"
        data["approval"] = {"decided_by": "QA", "at": "2026-11-15", "ref": "old block"}
        self.write("batches/batch_and_01.yaml", "# another comment\n" + yaml.safe_dump(data, default_flow_style=True))
        code, res = self.check()
        self.assertEqual((code, res["status"], res["plan_sha256"]), (0, "valid", digest), res)

    def test_a_quarantine_decision_that_no_longer_holds_reopens_the_review_with_the_same_hash(self):
        self.quarantine_q1()
        excluded = DRAFT.replace("cases: [TC-SHOP-001, TC-SHOP-002, TC-SHOP-004]",
                                 "cases: [TC-SHOP-001, TC-SHOP-002]\nexcluded:\n  - {case: TC-SHOP-004, quarantine: Q-1}")
        digest = self.approved(excluded)
        quarantine.decide(self.ws, "Q-1", "lift", "QA (fictional)", "session test-0003")
        code, res = self.check()
        self.assertEqual((code, res["status"], res["plan_sha256"]), (1, "stale", digest))
        self.assertIn("quarantine decision no longer holds", res["reason"])
        self.assertEqual(self.plan_cli().returncode, 1)

    def test_a_build_assumption_is_part_of_the_coverage_and_checked_against_the_observed_build(self):
        text = DRAFT + 'assumptions:\n  - {build: "1.0.0 (39)", why: "the build with the FE change"}\n'
        self.approved(text)
        self.assertEqual(self.check("batch_and_01", "--build", BUILD)[1]["status"], "valid")
        res = self.check("batch_and_01", "--build", "1.0.0 (40)")[1]
        self.assertEqual(res["status"], "stale")
        self.assertIn("assumes the build 1.0.0 (39)", res["reason"])
        self.assertEqual(self.plan_cli().returncode, 0)
        r = self.add_cli(build="1.0.0 (40)")
        self.assertEqual(r.returncode, 1)
        self.assertIn("assumes the build", r.stderr)
        self.cli("run_log.py", "plan", "batch_and_01_r5", "batches/batch_and_01.yaml")
        rl.write_log(self.log("batch_and_01_r5"), {"run": {"environment": "test", "build": BUILD},
                                                   "remote": {"run_id": "5", "batch": "batch_and_01"},
                                                   "plan": "batches/batch_and_01.yaml", "results": []})
        apk = os.path.join(self.ws, "b40.apk")
        make_apk(apk, package="com.demoshop.app", version_name="1.0.0", version_code=40)
        r = self.cli("workspace_ci.py", "build-check", "--run-log", "batch_and_01_r5", "--apk", apk)
        self.assertEqual(r.returncode, 1)                       # the APK that would really run: before the emulator
        self.assertIn("assumes the build", r.stderr)

    def test_a_legacy_block_is_shown_as_legacy_until_the_qa_confirms_it(self):
        legacy = DRAFT + "approval: {decided_by: QA, at: '2026-11-14', ref: 'old session'}\n"
        path = self.write("batches/batch_and_01.yaml", legacy)
        code, res = self.check()
        self.assertEqual((code, res["status"]), (1, "legacy"))
        self.assertEqual(self.plan_cli().returncode, 1)
        before = sha(path)
        r, digest = self.review(draft="batches/batch_and_01.yaml")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "analysis", "batch_and_01.review.md")))
        r = self.approve(digest, draft="batches/batch_and_01.yaml")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sha(path), before)                    # confirming changes nothing in the plan
        self.assertEqual(self.check()[1]["status"], "valid")


class Copied(Base):
    def test_a_register_and_plan_copied_from_another_workspace_are_not_valid(self):
        self.approved()
        other = workspace(workspace={"id": "shop-copy", "kind": "real"}, report=PLATFORMS, policy=PLAN)
        self.addCleanup(other.cleanup)
        ows = os.path.realpath(other.name)
        for rel in ("batches/batch_and_01.yaml", "memory/approvals.jsonl"):
            os.makedirs(os.path.join(ows, os.path.dirname(rel)), exist_ok=True)
            shutil.copyfile(os.path.join(self.ws, rel), os.path.join(ows, rel))
        r = self.cli("approvals.py", "check-plan", "--batch", "batch_and_01", "--json", ws=ows)
        res = json.loads(r.stdout)
        self.assertEqual(res["status"], "stale")
        self.assertIn("workspace shop-copy", res["reason"])
        r = self.cli("run_log.py", "plan", "batch_and_01", "batches/batch_and_01.yaml", ws=ows)
        self.assertEqual(r.returncode, 1)


class BothPolicies(Base):
    policy = BOTH

    def test_both_decisions_must_pass_and_a_new_build_asks_only_the_run(self):
        self.approved()
        self.assertEqual(self.plan_cli().returncode, 0)
        r = self.add_cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("policy.approvals.run", r.stderr)          # the plan is approved, the run is not
        approvals.record_run(self.ws, "42434", "batch_and_01", "android", "test", BUILD, "batches/batch_and_01.yaml",
                             "QA (fictional)", "session test-0004")
        self.assertEqual(self.add_cli().returncode, 0)
        rl.write_log(self.log("batch_and_02"), {"results": []})
        shutil.copyfile(os.path.join(self.ws, "batches", "batch_and_01.yaml"),
                        os.path.join(self.ws, "batches", "batch_and_02.yaml"))
        r = self.cli("run_log.py", "plan", "batch_and_02", "batches/batch_and_02.yaml")
        self.assertEqual(r.returncode, 1)                       # another batch: its own plan decision
        r = self.add_cli("TC-SHOP-002", build="1.0.0 (40)")
        self.assertEqual(r.returncode, 1)                       # the run block keeps 39: the build changed mid-batch
        self.assertEqual(self.check("batch_and_01", "--build", "1.0.0 (40)")[1]["status"], "valid")
        self.assertEqual(approvals.check_run(self.ws, approvals.context_of(
            self.ws, rl.read_project(self.ws), "batch_and_01", build="1.0.0 (40)",
            doc={"plan": "batches/batch_and_01.yaml", "run": {"platform": "android", "environment": "test",
                                                               "build": "1.0.0 (40)"}}))["status"], "stale")
        self.assertEqual([e["gate"] for e in self.ledger()], ["plan", "run"])
        self.assertEqual(set(self.ledger()[1]) - {"quote"}, {   # the run record keeps the 2A format
            "id", "gate", "workspace", "pbi", "batch", "platform", "environment", "build", "plan", "plan_sha256",
            "declared_by", "confirmation", "message_ref", "message_ref_verified", "limitation", "recorded_by",
            "recorded_at"})


class PbiRecord(Base):
    def test_the_plan_step_is_done_only_with_every_platform_approved_or_formally_skipped(self):
        pbi_state.start(self.ws, "42434", None, ["android", "ios"])
        self.approved()
        r = self.cli("pbi_state.py", "mark", "42434", "plan", "done", "--ref", "batches/batch_and_01.yaml")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ios: plan approval missing", r.stderr)
        self.assertEqual(pbi_state.load(self.ws, "42434")["steps"].get("plan", {}).get("status", "pending"),
                         "pending")
        r = self.cli("pbi_state.py", "show", "42434")
        self.assertIn("Plan approval (android): valid", r.stdout)
        self.assertIn("Plan approval (ios): missing", r.stdout)
        self.assertEqual(self.cli("pbi_state.py", "mark", "42434", "run:ios", "skipped", "--note",
                                  "iOS out of scope: the QA's decision").returncode, 0)
        r = self.cli("pbi_state.py", "mark", "42434", "plan", "done", "--ref", "batches/batch_and_01.yaml")
        self.assertEqual(r.returncode, 0, r.stderr)


class NoPolicy(Base):
    policy = None

    def test_old_projects_and_batches_work_as_before(self):
        legacy = DRAFT + "approval: {decided_by: QA, at: '2026-11-14', ref: 'old session'}\n"
        self.write("batches/batch_and_01.yaml", legacy)
        self.assertEqual(self.plan_cli().returncode, 0)
        self.assertEqual(self.add_cli().returncode, 0)
        self.assertIsNone(workspace_ci.plan_problem(self.ws, "batch_and_01", "android", BUILD, "test"))
        self.assertIsNone(workspace_ci.plan_problem(self.ws, "adhoc", "android", BUILD, "test"))
        r = self.cli("approvals.py", "check-plan", "--batch", "batch_and_01")
        self.assertEqual(r.returncode, 1)
        self.assertIn("LEGACY", r.stdout)
        self.assertIn("information, not a gate", r.stdout)
        pbi_state.start(self.ws, "7", None, ["android"])
        self.assertEqual(self.cli("pbi_state.py", "mark", "7", "plan", "done", "--ref", "x").returncode, 0)
        self.assertNotIn("Plan approval", self.cli("pbi_state.py", "show", "7").stdout)

    def test_the_canonical_hash_ignores_only_what_is_not_coverage(self):
        a = approvals.canonical_plan(yaml.safe_load(DRAFT))
        b = yaml.safe_load(DRAFT)
        b["run"]["build"], b["approval"] = "2.0.0 (1)", {"decided_by": "x"}
        self.assertEqual(approvals.canonical_sha(a), approvals.canonical_sha(approvals.canonical_plan(b)))
        for change in (lambda d: d["cases"].reverse(), lambda d: d["run"].update(environment="staging"),
                       lambda d: d["run"].update(platform="ios"), lambda d: d.update(notes=["other"]),
                       lambda d: d.update(assumptions=[{"build": "1.0.0 (39)"}]), lambda d: d.update(pbi="1")):
            c = yaml.safe_load(DRAFT)
            change(c)
            self.assertNotEqual(approvals.canonical_sha(a), approvals.canonical_sha(approvals.canonical_plan(c)))

    def test_the_policy_values_are_validated(self):
        base = {"project": "x", "workspace": IDENT}
        self.assertEqual(approvals.policy_problems({**base, "policy": PLAN}), [])
        self.assertEqual(approvals.policy_problems({**base, "policy": BOTH}), [])
        self.assertTrue(approvals.policy_problems({**base, "policy": {"approvals": {"plan": "maybe"}}}))
        self.assertTrue(approvals.policy_problems({"project": "x", "policy": PLAN}))       # no identity


class Binding(Base):
    """What the QA saw is what the batch runs: the review, the hash presented, and the context it is bound to."""

    def test_the_review_shows_the_whole_operational_coverage(self):
        self.quarantine_q1()
        text = DRAFT.replace("cases: [TC-SHOP-001, TC-SHOP-002, TC-SHOP-004]",
                             "cases: [TC-SHOP-002, TC-SHOP-001]\nexcluded:\n  - {case: TC-SHOP-004, quarantine: Q-1}")
        text += ('assumptions:\n  - {build: "1.0.0 (39)", why: "the FE change is in it"}\n'
                 "preconditions: [account ready]\nmodule: mobile\n")
        self.write("analysis/plan_and_01.yaml", text)
        r, digest = self.review()
        self.assertEqual(r.returncode, 0, r.stdout)
        for seen in ("1. TC-SHOP-002", "2. TC-SHOP-001", "TC-SHOP-004: excluded (Q-1)", '"build": "1.0.0 (39)"',
                     "record the reminder's initial state", "preconditions:", "module:", "platform: android",
                     "environment: test", "PBI: 42434", digest, "authorises no run"):
            self.assertIn(seen, r.stdout, seen)

    def test_a_review_of_another_version_is_refused(self):
        self.write("analysis/plan_and_01.yaml", DRAFT)
        self.review()                                           # the review of v1
        self.write("analysis/v2.yaml", DRAFT.replace("TC-SHOP-004]", "TC-SHOP-003]"))
        _, digest2 = self.review("batch_and_01", "analysis/v2.yaml", "--out", "analysis/v2.review.md")
        r = self.approve(digest2, draft="analysis/v2.yaml", review="analysis/plan_and_01.review.md")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not the review of this version", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "batches", "batch_and_01.yaml")))
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.approve(digest2, draft="analysis/v2.yaml", review="analysis/v2.review.md").returncode, 0)

    def test_run_build_alone_keeps_the_plan_unless_the_quarantine_or_an_assumption_changes_with_it(self):
        quarantine.propose(self.ws, "Q-1", "BUG-1", ["TC-SHOP-004"], ["android"], [BUILD], ["test"], "known failure",
                           "fixed in a later build", None)
        quarantine.decide(self.ws, "Q-1", "activate", "QA (fictional)", "session test-0001")
        text = DRAFT.replace("cases: [TC-SHOP-001, TC-SHOP-002, TC-SHOP-004]",
                             "cases: [TC-SHOP-001, TC-SHOP-002]\nexcluded:\n  - {case: TC-SHOP-004, quarantine: Q-1}")
        digest = self.approved(text)
        self.write("batches/batch_and_01.yaml", text.replace('build: "1.0.0 (39)"', 'build: "1.0.0 (40)"'))
        res = self.check("batch_and_01", "--build", BUILD)[1]
        self.assertEqual((res["status"], res["plan_sha256"]), ("valid", digest))
        res = self.check("batch_and_01", "--build", "1.0.0 (40)")[1]         # Q-1 holds for 39 only
        self.assertEqual((res["status"], res["plan_sha256"]), ("stale", digest))
        self.assertIn("quarantine decision no longer holds", res["reason"])

    def test_the_decision_is_bound_to_pbi_batch_platform_workspace_and_remote_suffix(self):
        self.approved()
        st = approvals.plan_status_by_platform(self.ws, "99", ["android"])
        self.assertEqual(st["android"]["status"], "missing")                   # another PBI
        self.assertEqual(approvals.plan_status_by_platform(self.ws, "42434", ["android"])["android"]["status"],
                         "valid")
        shutil.copyfile(os.path.join(self.ws, "batches", "batch_and_01.yaml"),
                        os.path.join(self.ws, "batches", "batch_and_02.yaml"))
        self.assertEqual(self.check("batch_and_02")[1]["status"], "missing")    # another batch, the same bytes
        res = approvals.check_plan(self.ws, "batch_and_01", doc={"plan": "batches/batch_and_01.yaml",
                                                                 "run": {"platform": "ios"}})
        self.assertEqual(res["status"], "stale")                                # another platform
        self.assertIn("platform ios", res["reason"])
        for log, base, status in (("batch_and_01_r9", "batch_and_01", "valid"),
                                  ("batch_and_01_rx", "batch_and_01", "missing"),
                                  ("batch_and_02_r9", "batch_and_01", "missing")):
            rl.write_log(self.log(log), {"run": {"environment": "test", "build": BUILD},
                                         "remote": {"run_id": "9", "batch": base},
                                         "plan": "batches/batch_and_01.yaml", "results": []})
            self.assertEqual(self.check(log)[1]["status"], status, log)


class Interruption(Base):
    def test_after_the_copy_and_before_the_decision_everything_stays_blocked_and_one_repeat_finishes(self):
        self.write("analysis/plan_and_01.yaml", DRAFT)
        _, digest = self.review()
        real_append = approvals._append

        def crash(*a, **k):
            raise KeyboardInterrupt("interrupted between the copy and the decision")
        approvals._append = crash
        try:
            with self.assertRaises(KeyboardInterrupt):
                approvals.approve_plan(self.ws, "42434", "batch_and_01", "analysis/plan_and_01.yaml", digest,
                                       "QA (fictional)", "session test-0002 · 10:15Z")
        finally:
            approvals._append = real_append
        self.assertTrue(os.path.exists(os.path.join(self.ws, "batches", "batch_and_01.yaml")))
        self.assertEqual(self.ledger(), [])
        code, res = self.check()
        self.assertEqual(res["status"], "missing")
        self.assertIn("analysis/plan_and_01.yaml", res["next"])
        self.assertEqual(self.plan_cli().returncode, 1)                       # association
        self.assertEqual(self.add_cli().returncode, 1)                        # results
        self.assertFalse(os.path.exists(self.log()) and rl.read_log(self.log())["results"])
        code, out = self.ci_batch()                                           # CI: no remote run log, no flows
        self.assertEqual(code, 1, out)
        self.assertFalse(os.path.exists(self.log("batch_and_01_r9")))
        r = self.approve(digest)                                              # the same explicit approval again
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("recorded: AP-0001", r.stdout)
        r = self.approve(digest)
        self.assertIn("already recorded: AP-0001", r.stdout)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.plan_cli().returncode, 0)
        self.assertEqual(self.add_cli().returncode, 0)

    def test_another_version_never_replaces_a_plan_that_already_has_results(self):
        self.approved()
        self.plan_cli()
        self.assertEqual(self.add_cli().returncode, 0)
        plan, log = os.path.join(self.ws, "batches", "batch_and_01.yaml"), self.log()
        before = (sha(plan), sha(log))
        self.write("analysis/v2.yaml", DRAFT.replace("TC-SHOP-004]", "TC-SHOP-003]"))
        _, digest2 = self.review("batch_and_01", "analysis/v2.yaml")
        r = self.approve(digest2, draft="analysis/v2.yaml")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already has results", r.stderr)
        self.assertIn("new batch, e.g. batch_and_02", r.stderr)
        self.assertEqual((sha(plan), sha(log)), before)
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.check()[1]["status"], "valid")

    def test_results_of_a_remote_run_also_protect_the_base_plan(self):
        self.approved()
        rl.write_log(self.log("batch_and_01_r9"), {"run": {"environment": "test", "build": BUILD},
                                                   "remote": {"run_id": "9", "batch": "batch_and_01"},
                                                   "plan": "batches/batch_and_01.yaml",
                                                   "results": [{"tc": "TC-SHOP-001", "status": "Pass"}]})
        self.write("analysis/v2.yaml", DRAFT.replace("TC-SHOP-004]", "TC-SHOP-003]"))
        _, digest2 = self.review("batch_and_01", "analysis/v2.yaml")
        r = self.approve(digest2, draft="analysis/v2.yaml")
        self.assertEqual(r.returncode, 1)
        self.assertIn("batch_and_01_r9", r.stderr)


class Concurrency(Base):
    def test_a_draft_changed_while_waiting_for_the_lock_is_refused(self):
        self.write("analysis/plan_and_01.yaml", DRAFT)
        _, digest = self.review()
        real_lock = approvals.workspace_lock

        @contextlib.contextmanager
        def changed_meanwhile(ws, *a, **k):
            self.write("analysis/plan_and_01.yaml", DRAFT.replace("TC-SHOP-004]", "TC-SHOP-003]"))
            with real_lock(ws, *a, **k) as path:
                yield path
        with mock.patch.object(approvals, "workspace_lock", changed_meanwhile), \
                self.assertRaises(approvals.Refused) as c:
            approvals.approve_plan(self.ws, "42434", "batch_and_01", "analysis/plan_and_01.yaml", digest,
                                   "QA (fictional)", "ref")
        self.assertIn("changed since it was presented", str(c.exception))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "batches", "batch_and_01.yaml")))
        self.assertEqual(self.ledger(), [])

    def test_two_concurrent_approvals_leave_the_plan_bound_to_the_last_decision(self):
        self.write("analysis/a.yaml", DRAFT)
        self.write("analysis/b.yaml", DRAFT.replace("TC-SHOP-004]", "TC-SHOP-003]"))
        _, ha = self.review("batch_and_01", "analysis/a.yaml")
        _, hb = self.review("batch_and_01", "analysis/b.yaml")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        env.pop("PYTHONPATH", None)
        procs = [subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "approvals.py"), self.ws,
                                   "approve-plan", "--pbi", "42434", "--batch", "batch_and_01", "--draft", d,
                                   "--sha", h, "--declared-by", "QA (fictional)", "--message-ref", f"ref {d}"],
                                  cwd=self.ws, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for d, h in (("analysis/a.yaml", ha), ("analysis/b.yaml", hb))]
        codes = [p.wait(60) for p in procs]
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(codes, [0, 0])
        decisions = self.ledger()
        self.assertEqual(sorted(e["plan_sha256"] for e in decisions), sorted([ha, hb]))
        on_disk = approvals.plan_digest(self.ws, "batches/batch_and_01.yaml")[1]
        self.assertEqual(on_disk, decisions[-1]["plan_sha256"])                # the plan and the last decision agree
        self.assertEqual(self.check()[1]["status"], "valid")


class OnlyPlan(Base):
    def test_only_the_plan_policy_checks_the_coverage_and_keeps_the_projects_run_confirmation(self):
        self.approved()
        self.assertEqual(self.plan_cli().returncode, 0)
        self.assertEqual(self.add_cli().returncode, 0)                        # no run decision is required
        self.assertEqual([e["gate"] for e in self.ledger()], ["plan"])
        for skill in ("run-tests", "test-pbi"):
            with open(os.path.join(ROOT, ".claude", "skills", skill, "SKILL.md"), encoding="utf-8") as f:
                text = " ".join(f.read().split())
            self.assertIn("check-plan", text)
            self.assertIn("run confirmation the", text)                        # the project's own one is still asked


RUN_ONLY = {"approvals": {"run": "per-platform"}}


class OnlyRun(Base):
    policy = RUN_ONLY

    def test_only_the_run_policy_keeps_the_2a_contract(self):
        legacy = DRAFT + "approval: {decided_by: QA, at: '2026-11-14', ref: 'old session'}\n"
        self.write("batches/batch_and_01.yaml", legacy)
        self.assertEqual(self.plan_cli().returncode, 0)                       # no plan decision is required
        r = self.add_cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("policy.approvals.run", r.stderr)
        self.assertNotIn("policy.approvals.plan", r.stderr)
        approvals.record_run(self.ws, "42434", "batch_and_01", "android", "test", BUILD, "batches/batch_and_01.yaml",
                             "QA (fictional)", "session test-0005")
        self.assertEqual(self.add_cli().returncode, 0)
        self.assertIsNone(workspace_ci.plan_problem(self.ws, "batch_and_01", "android", BUILD, "test"))


class BothCi(Base):
    policy = BOTH

    def test_a_missing_plan_decision_stops_the_ci_even_with_a_valid_run_decision(self):
        self.write("batches/batch_and_01.yaml", DRAFT)
        approvals.record_run(self.ws, "42434", "batch_and_01", "android", "test", BUILD, "batches/batch_and_01.yaml",
                             "QA (fictional)", "session test-0006")
        code, out = self.ci_batch()
        self.assertEqual(code, 1, out)
        self.assertIn("policy.approvals.plan", out)
        self.assertFalse(os.path.exists(self.log("batch_and_01_r9")))


class PbiDispensation(Base):
    def test_a_platform_skipped_without_a_note_is_not_a_formal_dispensation(self):
        pbi_state.start(self.ws, "42434", None, ["android", "ios"])
        self.approved()
        self.assertEqual(self.cli("pbi_state.py", "mark", "42434", "run:ios", "skipped").returncode, 0)
        r = self.cli("pbi_state.py", "mark", "42434", "plan", "done", "--ref", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("skipped without a note", r.stderr)
        approvals.revoke(self.ws, self.ledger()[0]["id"], "QA (fictional)", "ref", "scope changed")
        self.cli("pbi_state.py", "mark", "42434", "run:ios", "skipped", "--note", "iOS out of scope: the QA")
        r = self.cli("pbi_state.py", "mark", "42434", "plan", "done", "--ref", "x")
        self.assertEqual(r.returncode, 1)                                      # android's decision was revoked
        self.assertIn("android: plan approval revoked", r.stderr)


if __name__ == "__main__":
    unittest.main()

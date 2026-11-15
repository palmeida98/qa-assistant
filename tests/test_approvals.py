"""The QA's run decisions (scripts/approvals.py) and their check in run_log.py add, the CI's «Resolve the batch» and the
PBI's record (docs/procedures/approvals.md). Fictional Demo Shop workspaces in temporary folders only.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import approvals
import pbi_state
import run_log as rl
import validate_project
import workspace_ci
import yaml
from apk_kit import make_apk
from workspace_kit import workspace

POLICY = {"approvals": {"run": "per-platform"}}
IDENT = {"id": "shop", "kind": "real"}
PLATFORMS = {"platforms": {"android": ["batch_and_*"], "ios": ["batch_ios_*"]}}
BUILD = "1.0.0 (39)"


def sha(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Base(unittest.TestCase):
    policy = POLICY

    def setUp(self):
        extra = {"workspace": IDENT, "report": PLATFORMS}
        if self.policy:
            extra["policy"] = self.policy
        self._tmp = workspace(**extra)
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.realpath(self._tmp.name)
        self.plan("batch_and_01", "android")

    def plan(self, name, platform, build=BUILD, environment="test", cases=("TC-SHOP-001", "TC-SHOP-002")):
        os.makedirs(os.path.join(self.ws, "batches"), exist_ok=True)
        path = os.path.join(self.ws, "batches", f"{name}.yaml")
        with open(path, "w", encoding="utf-8") as f:
            run = {k: v for k, v in (("platform", platform), ("environment", environment), ("build", build)) if v}
            yaml.safe_dump({"run": run,
                            "cases": list(cases), "approval": {"decided_by": "QA (fictional)", "at": "2026-11-15"}}, f)
        rl.set_plan(self.ws, name, f"batches/{name}.yaml")
        return f"batches/{name}.yaml"

    def record(self, batch="batch_and_01", platform="android", environment="test", build=BUILD, plan=None, ws=None):
        return approvals.record_run(ws or self.ws, "42434", batch, platform, environment, build,
                                    plan or f"batches/{batch}.yaml", "QA (fictional)", "session test-0001 · 23:58Z",
                                    quote="Aprovo a execução em Android")

    def cli(self, script, *args, cwd=None):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        env.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", script), *args], cwd=cwd or self.ws,
                              env=env, capture_output=True, text=True, encoding="utf-8", check=False)

    def add(self, batch="batch_and_01", case="TC-SHOP-001", build=BUILD, environment="test"):
        return rl.add(self.ws, batch, case, "Pass", "seen", environment=environment, build=build)

    def log(self, batch="batch_and_01"):
        return rl.run_log_path(self.ws, batch)

    def refused(self, batch="batch_and_01", build=BUILD, environment="test", case="TC-SHOP-001", contains=""):
        before = sha(self.log(batch))
        with self.assertRaises(rl.Refused) as c:
            self.add(batch, case, build, environment)
        self.assertIn("nothing was written", str(c.exception))
        self.assertIn(contains, str(c.exception))
        self.assertEqual(sha(self.log(batch)), before)
        return str(c.exception)


class Contract(Base):
    def test_the_policy_is_validated_and_needs_an_identity(self):
        base = {"project": "x"}
        self.assertEqual(approvals.policy_problems(base), [])
        self.assertEqual(approvals.policy_problems({**base, "workspace": IDENT, "policy": POLICY}), [])
        self.assertTrue(approvals.policy_problems({**base, "policy": POLICY}))          # no identity
        self.assertTrue(approvals.policy_problems({**base, "workspace": IDENT, "policy": {"approvals": {"run": "x"}}}))
        self.assertTrue(approvals.policy_problems({**base, "workspace": IDENT, "policy": {"approvals": {"plan": "y"}}}))
        problems, _ = validate_project.base_problems({"project": "x", "tracker": "excel", "bug_format": "B",
                                                      "environments": {"test": "https://x.example/"},
                                                      "policy": POLICY})
        self.assertTrue(any("identity" in p for p in problems))

    def test_a_record_holds_every_field_and_says_it_is_declared(self):
        e = self.record()
        for key in ("id", "workspace", "pbi", "batch", "platform", "environment", "build", "plan", "plan_sha256",
                    "declared_by", "message_ref", "recorded_at"):
            self.assertTrue(e.get(key), key)
        self.assertEqual((e["id"], e["confirmation"], e["message_ref_verified"]), ("AP-0001", "declared-in-chat", False))
        self.assertIn("not a signature", e["limitation"])
        self.assertNotIn("prompt", json.dumps(e))       # never a permission-prompt confirmation
        with self.assertRaises(approvals.UsageError):
            approvals.record_run(self.ws, "42434", "batch_and_01", "android", "test", BUILD,
                                 "batches/batch_and_01.yaml", "QA", "")     # no message reference


class RunGate(Base):
    def test_a_valid_approval_lets_the_batch_start_and_resume(self):
        self.record()
        self.add()
        self.add(case="TC-SHOP-002")                    # the resume: the same decision is reused
        r = self.cli("approvals.py", self.ws, "check", "--batch", "batch_and_01")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RUN APPROVAL VALID", r.stdout)
        self.assertIn("nothing to ask again", r.stdout)

    def test_without_a_decision_the_first_result_is_refused(self):
        self.refused(contains="missing")
        self.assertFalse(os.path.exists(os.path.join(self.ws, "memory", "approvals.jsonl")))

    def test_another_build_environment_platform_or_plan_asks_again(self):
        self.record()
        self.refused(build="1.0.0 (40)", contains="build 1.0.0 (40) ≠ approved 1.0.0 (39)")
        self.refused(environment="staging", contains="environment staging ≠ approved test")
        self.plan("batch_ios_01", "ios")
        self.refused(batch="batch_ios_01", contains="missing")       # no decision for the iOS batch
        with self.assertRaises(approvals.Refused):                      # the plan says ios: never recorded as android
            self.record(batch="batch_ios_01", platform="android")
        self.plan("batch_ios_02", None)                                 # a plan without run: (platform from report:)
        self.record(batch="batch_ios_02", platform="android")
        self.refused(batch="batch_ios_02", contains="platform ios ≠ approved android")
        with open(os.path.join(self.ws, "batches", "batch_and_01.yaml"), "a", encoding="utf-8") as f:
            f.write("# a case added after the approval\n")
        self.refused(contains="plan batches/batch_and_01.yaml")
        r = self.cli("approvals.py", self.ws, "check", "--batch", "batch_and_01")
        self.assertEqual(r.returncode, 1)
        self.assertIn("RUN APPROVAL STALE", r.stdout)

    def test_a_change_after_results_keeps_them_and_refuses_the_next(self):
        self.record()
        self.add()
        before = sha(self.log())
        with open(os.path.join(self.ws, "batches", "batch_and_01.yaml"), "a", encoding="utf-8") as f:
            f.write("# changed\n")
        self.refused(case="TC-SHOP-002", contains="stale")
        self.assertEqual(sha(self.log()), before)
        self.assertEqual([x["tc"] for x in rl.read_log(self.log())["results"]], ["TC-SHOP-001"])

    def test_a_revoked_decision_is_refused_and_kept(self):
        e = self.record()
        approvals.revoke(self.ws, e["id"], "QA (fictional)", "session test-0001 · 00:10Z", "wrong device")
        self.refused(contains="revoked")
        listed = self.cli("approvals.py", self.ws, "list")
        self.assertIn("AP-0001", listed.stdout)
        self.assertIn("AP-0002 revokes AP-0001", listed.stdout)
        with self.assertRaises(approvals.Refused):
            approvals.revoke(self.ws, e["id"], "QA", "ref", "again")

    def test_an_unreadable_register_refuses_with_its_error(self):
        os.makedirs(os.path.join(self.ws, "memory"), exist_ok=True)
        with open(os.path.join(self.ws, "memory", "approvals.jsonl"), "w", encoding="utf-8") as f:
            f.write("{not json\n")
        self.refused(contains="line 1: not JSON")
        r = self.cli("approvals.py", self.ws, "check", "--batch", "batch_and_01")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not JSON", r.stderr)

    def test_a_register_copied_to_another_workspace_identity_is_refused(self):
        self.record()
        other = workspace(workspace={"id": "shop-sim", "kind": "simulation"}, report=PLATFORMS, policy=POLICY)
        self.addCleanup(other.cleanup)
        ows = os.path.realpath(other.name)
        shutil.copytree(os.path.join(self.ws, "batches"), os.path.join(ows, "batches"))
        rl.set_plan(ows, "batch_and_01", "batches/batch_and_01.yaml")
        os.makedirs(os.path.join(ows, "memory"), exist_ok=True)
        shutil.copy2(os.path.join(self.ws, "memory", "approvals.jsonl"), os.path.join(ows, "memory"))
        before = sha(rl.run_log_path(ows, "batch_and_01"))
        with self.assertRaises(rl.Refused) as c:
            rl.add(ows, "batch_and_01", "TC-SHOP-001", "Pass", "seen", environment="test", build=BUILD)
        self.assertIn("workspace shop-sim (simulation) ≠ approved shop (real)", str(c.exception))
        self.assertEqual(sha(rl.run_log_path(ows, "batch_and_01")), before)

    def test_checking_again_and_again_keeps_the_register_byte_for_byte(self):
        self.record()
        path = os.path.join(self.ws, "memory", "approvals.jsonl")
        before = sha(path)
        for _ in range(3):
            self.cli("approvals.py", self.ws, "check", "--batch", "batch_and_01")
            self.cli("approvals.py", self.ws, "list", "--json")
            approvals.run_status_by_platform(self.ws, "42434", ["android", "ios"])
        self.assertEqual(sha(path), before)

    def test_the_pbi_record_shows_each_platform_without_changing_the_others(self):
        pbi_state.start(self.ws, "42434", None, ["android", "ios"])
        self.record()
        r = self.cli("pbi_state.py", self.ws, "show", "42434")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Run approval (android): valid AP-0001 · test · 1.0.0 (39)", r.stdout)
        self.assertIn("Run approval (ios): missing", r.stdout)
        with open(os.path.join(self.ws, "batches", "batch_and_01.yaml"), "a", encoding="utf-8") as f:
            f.write("# changed\n")
        r = self.cli("pbi_state.py", self.ws, "show", "42434")
        self.assertIn("Run approval (android): stale", r.stdout)
        doc = pbi_state.load(self.ws, "42434")
        self.assertEqual({k: v["status"] for k, v in doc["steps"].items() if k.startswith("run:")},
                         {"run:android": "pending", "run:ios": "pending"})

    def test_the_ci_refuses_before_the_run_log_the_emulator_and_the_flows(self):
        proj = workspace_ci.project(self.ws)
        problem = workspace_ci.plan_problem(self.ws, "batch_and_01", "android", BUILD, "test")
        self.assertIn("missing", problem)
        self.assertIn("policy.approvals.run", workspace_ci.plan_problem(self.ws, "adhoc", "android", BUILD, "test"))
        self.record()
        self.assertIsNone(workspace_ci.plan_problem(self.ws, "batch_and_01", "android", BUILD, "test"))
        self.assertIn("build 1.0.0 (40)", workspace_ci.plan_problem(self.ws, "batch_and_01", "android", "1.0.0 (40)",
                                                                   "test"))
        self.assertTrue(proj)


class Review(Base):
    """Gaps found in the final review of increment 2A (15.11.2026)."""

    def remote(self, name, base):
        path = rl.run_log_path(self.ws, name)
        rl.write_log(path, {"mode": "flows: remote run", "run": {"environment": "test", "build": BUILD},
                            "remote": {"run_id": "77", "batch": base, "cases": ["TC-SHOP-001"]},
                            "plan": f"batches/{base}.yaml", "results": []})
        return path

    def test_a_remote_run_log_carries_only_its_own_base_batch_decision(self):
        self.record()
        self.remote("batch_and_01_r77", "batch_and_01")
        rl.add(self.ws, "batch_and_01_r77", "TC-SHOP-001", "Pass", "flow ok")
        self.remote("batch_and_99", "batch_and_01")            # another batch claiming the decision
        self.refused(batch="batch_and_99", build=None, environment=None, contains="missing")
        self.remote("batch_and_01_r77x", "batch_and_01")       # not <batch>_r<digits>
        self.refused(batch="batch_and_01_r77x", build=None, environment=None, contains="missing")

    def test_a_decision_is_recorded_only_for_the_batchs_own_plan_and_its_declared_pbi(self):
        self.plan("batch_and_02", "android")
        with self.assertRaises(approvals.Refused) as c:
            self.record(batch="batch_and_01", plan="batches/batch_and_02.yaml")
        self.assertIn("is not the plan of batch_and_01", str(c.exception))
        with open(os.path.join(self.ws, "batches", "batch_and_02.yaml"), "a", encoding="utf-8") as f:
            f.write('pbi: "999"\n')
        with self.assertRaises(approvals.Refused) as c:
            self.record(batch="batch_and_02")
        self.assertIn("declares pbi '999'", str(c.exception))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "memory", "approvals.jsonl")))

    def test_the_same_decision_twice_is_one_line(self):
        first = self.record()
        again = self.record()
        self.assertEqual((again["id"], again.get("repeated")), (first["id"], True))
        with open(os.path.join(self.ws, "memory", "approvals.jsonl"), encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 1)
        self.assertEqual(self.record(build="1.0.0 (40)")["id"], "AP-0002")      # another context: a new decision

    def test_the_build_must_be_the_observed_one_not_the_plans(self):
        self.record()
        self.refused(build=None, contains="OBSERVED on the target is not known")
        self.add()                                              # with --build: the observed build
        rl.add(self.ws, "batch_and_01", "TC-SHOP-002", "Pass", "seen")      # then the run log's build is used

    def test_check_and_list_never_write_even_with_invalid_data(self):
        os.makedirs(os.path.join(self.ws, "memory"), exist_ok=True)
        path = os.path.join(self.ws, "memory", "approvals.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"id": "AP-0001", "gate": "run"}\n[1, 2]\n')
        before = sha(path)
        for args in (("check", "--batch", "batch_and_01"), ("list",), ("list", "--json")):
            r = self.cli("approvals.py", self.ws, *args)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn("line 2", r.stderr)
        self.assertEqual(sha(path), before)

    def ci(self, *args):
        return self.cli("workspace_ci.py", self.ws, *args)

    def test_the_ci_stops_before_the_flows_on_a_missing_revoked_stale_or_other_build(self):
        apk39, apk40 = os.path.join(self.ws, "b39.apk"), os.path.join(self.ws, "b40.apk")
        make_apk(apk39, package="com.demoshop.app", version_name="1.0.0", version_code=39)
        make_apk(apk40, package="com.demoshop.app", version_name="1.0.0", version_code=40)
        self.remote("batch_and_01_r77", "batch_and_01")
        r = self.ci("build-check", "--run-log", "batch_and_01_r77", "--apk", apk39)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("missing", r.stderr)
        self.assertIn("Nothing reaches the device", r.stderr)
        e = self.record()
        r = self.ci("build-check", "--run-log", "batch_and_01_r77", "--apk", apk39)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("build 1.0.0 (39): the one validated", r.stdout)
        r = self.ci("build-check", "--run-log", "batch_and_01_r77", "--apk", apk40)    # the fetched APK differs
        self.assertEqual(r.returncode, 1)
        self.assertIn("1.0.0 (40), but «Resolve the batch» validated the build 1.0.0 (39)", r.stderr)
        approvals.revoke(self.ws, e["id"], "QA", "ref", "wrong build")
        self.assertIn("revoked", workspace_ci.plan_problem(self.ws, "batch_and_01", "android", BUILD, "test"))
        r = self.ci("build-check", "--run-log", "batch_and_01_r77", "--apk", apk39)
        self.assertEqual(r.returncode, 1)
        self.assertIn("revoked", r.stderr)
        log = rl.read_log(rl.run_log_path(self.ws, "batch_and_01_r77"))
        self.assertEqual(log["results"], [])                    # nothing was recorded

    def test_the_workflow_checks_the_build_after_fetching_and_before_the_emulator(self):
        with open(os.path.join(ROOT, "docs", "workspace", "ci", "mobile.yml"), encoding="utf-8") as f:
            text = f.read()
        order = [text.index(x) for x in ("name: Resolve the batch", "name: The build (release asset",
                                          "name: The build is the one authorised", "name: The emulator",
                                          "name: Maestro flows", "name: Claude Code headless")]
        self.assertEqual(order, sorted(order))


class NoPolicy(Base):
    policy = None

    def test_a_different_apk_is_only_a_warning_without_the_option(self):
        apk = os.path.join(self.ws, "b40.apk")
        make_apk(apk, package="com.demoshop.app", version_name="1.0.0", version_code=40)
        rl.write_log(rl.run_log_path(self.ws, "batch_and_01_r1"), {"run": {"environment": "test", "build": BUILD},
                                                                  "remote": {"batch": "batch_and_01"}, "results": []})
        r = self.cli("workspace_ci.py", self.ws, "build-check", "--run-log", "batch_and_01_r1", "--apk", apk)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning", r.stderr)

    def test_without_the_option_everything_works_as_before(self):
        self.add()
        self.add(case="TC-SHOP-002", build="1.0.0 (39)")
        self.assertIsNone(workspace_ci.plan_problem(self.ws, "batch_and_01", "android", BUILD, "test"))
        self.assertIsNone(workspace_ci.plan_problem(self.ws, "adhoc", "android", BUILD, "test"))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "memory", "approvals.jsonl")))
        pbi_state.start(self.ws, "7", None, ["android"])
        self.assertNotIn("Run approval", pbi_state.render(pbi_state.load(self.ws, "7"), self.ws))


if __name__ == "__main__":
    unittest.main()

"""docs/workspace/ci/apply.yml, the template of a workspace's `QA apply` workflow: it applies one run log only when asked
(workflow_dispatch), after a dry run, with the approval typed and (Excel) the reviewed preview token, and it pushes a NEW branch,
never main. The structure is checked always; the shell blocks of the template are run end to end on the fictional demo
workspace with a local fake remote when LibreOffice is there (the golden job). Nothing real, no network.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]
TEMPLATE = os.path.join(ROOT, "docs", "workspace", "ci", "apply.yml")
DEMO = os.path.join(ROOT, "examples", "demo")
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def load():
    with open(TEMPLATE, encoding="utf-8") as f:
        raw = f.read()
    return raw, yaml.safe_load(raw)


def has_libreoffice() -> bool:
    try:
        from adapters.excel.recalc import find_soffice
        return bool(find_soffice())
    except (ImportError, OSError):
        return False


def out_of(*cmd):
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False).stdout


class Structure(unittest.TestCase):
    def setUp(self):
        self.raw, self.wf = load()
        self.on = self.wf.get(True) or self.wf.get("on")

    def test_the_only_trigger_is_a_manual_dispatch(self):
        # a push to main starts nothing
        self.assertEqual(list(self.on), ["workflow_dispatch"])
        for t in ("push", "pull_request", "pull_request_target", "schedule", "workflow_run"):
            self.assertNotIn(t, self.on)

    def test_dry_run_is_the_default_mode(self):
        mode = self.on["workflow_dispatch"]["inputs"]["mode"]
        self.assertEqual(mode["default"], "dry-run")
        self.assertEqual(mode["options"], ["dry-run", "apply"])

    def test_permissions_are_the_least_needed(self):
        self.assertEqual(self.wf["permissions"], {"contents": "read"})
        self.assertEqual(self.wf["jobs"]["dry-run"]["permissions"], {"contents": "read"})
        self.assertEqual(self.wf["jobs"]["apply"]["permissions"], {"contents": "write"})
        self.assertIn("inputs.mode == 'apply'", self.wf["jobs"]["apply"]["if"])
        self.assertIn("github.actor == github.repository_owner", self.wf["jobs"]["dry-run"]["if"])

    def test_it_never_pushes_to_main_nor_opens_a_pull_request(self):
        self.assertNotIn("push origin HEAD", self.raw)
        self.assertNotIn("refs/heads/main", self.raw)
        self.assertNotIn("gh pr create", self.raw)
        self.assertNotIn("pull-requests:", self.raw)
        self.assertNotIn("apply-merged", self.raw)

    def test_the_credentials_of_the_trackers_are_only_for_non_excel_projects(self):
        steps = [s for j in self.wf["jobs"].values() for s in j["steps"]]
        with_secrets = [s for s in steps if "toJSON(secrets)" in str(s)]
        self.assertTrue(with_secrets)
        for s in with_secrets:
            self.assertIn("!= 'excel'", s["if"])

    def test_the_framework_is_configurable_with_a_public_default(self):
        env = self.wf["env"]
        self.assertIn("vars.QA_ASSISTANT_REPOSITORY", env["QA_REPOSITORY"])
        self.assertIn("vars.QA_ASSISTANT_REF", env["QA_REF"])
        self.assertIn("secrets.QA_ASSISTANT_TOKEN || github.token", self.raw)

    def test_the_excel_dry_run_fails_when_it_prints_no_preview_token(self):
        block = step(self.wf, "dry-run", "Dry run")
        self.assertRegex(block, r'test -n "\$token" \|\|[^\n]*no preview token[^\n]*exit 1')

    def test_the_apply_job_checks_the_token_format_and_uses_expect(self):
        steps = self.wf["jobs"]["apply"]["steps"]
        tracker = next(s["run"] for s in steps if s.get("id") == "tracker")
        self.assertIn("^v2-[0-9a-f]+$", tracker)
        apply = next(s["run"] for s in steps if s.get("name", "").startswith("Apply"))
        self.assertIn('--expect "$EXPECT"', apply)

    def test_every_action_is_pinned_to_a_commit(self):
        uses = re.findall(r"uses: (\S+)", self.raw)
        self.assertTrue(uses)
        for u in uses:
            with self.subTest(action=u):
                self.assertRegex(u, r"@[0-9a-f]{40}$")


def step(wf, job, prefix):
    for s in wf["jobs"][job]["steps"]:
        if s.get("name", "").startswith(prefix):
            return s["run"]
    raise KeyError(prefix)


@unittest.skipUnless(has_libreoffice(), "needs LibreOffice (runs in the CI's golden job)")
@unittest.skipIf(sys.platform == "win32", "the bash blocks of a Linux runner")
class EndToEnd(unittest.TestCase):
    """The template's own shell blocks, on a copy of the fictional demo with a local fake remote."""

    @classmethod
    def setUpClass(cls):
        cls.raw, cls.wf = load()
        cls._dir = tempfile.TemporaryDirectory(prefix="qa_apply_")
        cls.addClassCleanup(cls._dir.cleanup)
        cls.tmp = cls._dir.name
        cls.bindir = os.path.dirname(sys.executable)

    def sh(self, script, env=None, cwd=None):
        e = dict(os.environ)
        e["PATH"] = self.bindir + os.pathsep + e["PATH"]
        e.update(env or {})
        r = subprocess.run(["bash", "-eo", "pipefail", "-c", script], capture_output=True, text=True, env=e, cwd=cwd,
                           check=False, timeout=900)
        return r.returncode, r.stdout + r.stderr

    def workspace(self, name):
        ws = os.path.join(self.tmp, name, "ws")
        shutil.copytree(DEMO, ws, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        remote = os.path.join(self.tmp, name, "remote.git")
        subprocess.run(["git", "init", "-q", "-b", "main", ws], check=True)
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        subprocess.run(["git", *GIT_ID, "-C", ws, "add", "-A"], check=True)
        subprocess.run(["git", *GIT_ID, "-C", ws, "commit", "-q", "-m", "base"], check=True)
        subprocess.run(["git", "-C", ws, "remote", "add", "origin", remote], check=True)
        subprocess.run(["git", "-C", ws, "push", "-q", "origin", "main"], check=True)
        return ws, remote

    def runlog(self, ws, batch, status="Pass"):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "run_log.py"), ws, "add", batch, "TC-CHK-002", status,
                            "fixture", "--environment", "test", "--build", "fixture", "--mode", "manual: fixture"],
                           capture_output=True, text=True, cwd=ROOT, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        subprocess.run(["git", *GIT_ID, "-C", ws, "add", "-A"], check=True)
        subprocess.run(["git", *GIT_ID, "-C", ws, "commit", "-q", "-m", batch], check=True)

    def env(self, ws, batch, **extra):
        e = {"WS": ws, "QA": ROOT, "BATCH": batch, "TRACKER": "excel", "RUNNER_TEMP": os.path.join(self.tmp, "rt"),
             "GITHUB_STEP_SUMMARY": os.path.join(self.tmp, "summary.md"), "GITHUB_OUTPUT": os.path.join(self.tmp, "out.txt"),
             "GITHUB_RUN_ID": "77", "GITHUB_REPOSITORY": "o/r"}
        e.update(extra)
        os.makedirs(e["RUNNER_TEMP"], exist_ok=True)
        return e

    def status(self, ws):
        return out_of("git", "-C", ws, "status", "--porcelain").strip()

    def heads(self, remote):
        return out_of("git", "-C", remote, "for-each-ref", "--format=%(refname)", "refs/heads").split()

    def dry_run(self, ws, batch):
        summary = os.path.join(self.tmp, "summary.md")
        if os.path.exists(summary):
            os.remove(summary)
        rc, out = self.sh(step(self.wf, "dry-run", "Dry run"), self.env(ws, batch), cwd=ws)
        with open(summary, encoding="utf-8") as f:
            tokens = re.findall(r"v2-[0-9a-f]+", f.read())
        return rc, out, (tokens[-1] if tokens else "")

    def test_a_batch_name_that_is_not_a_run_log_is_refused(self):
        block = step(self.wf, "dry-run", "The batch name")
        for bad in ("foo", "batch_../x", "batch_a b", "batch_x;rm"):
            with self.subTest(batch=bad):
                self.assertNotEqual(self.sh(block, {"BATCH": bad})[0], 0)
        self.assertEqual(self.sh(block, {"BATCH": "batch_ok_01"})[0], 0)

    def test_the_approval_must_be_typed_exactly(self):
        block = step(self.wf, "apply", "The approval")
        self.assertEqual(self.sh(block, {"BATCH": "batch_a", "APPROVAL": "APPLY batch_a"})[0], 0)
        for appr in ("", "apply batch_a", "APPLY batch_b", "APPLY batch_a "):
            with self.subTest(approval=appr):
                self.assertNotEqual(self.sh(block, {"BATCH": "batch_a", "APPROVAL": appr})[0], 0)

    def test_a_question_or_a_missing_run_log_is_refused(self):
        ws, _ = self.workspace("refusals")
        self.runlog(ws, "batch_q01", "Question")
        block = step(self.wf, "dry-run", "A run log with a proposal")
        rc, out = self.sh(block, self.env(ws, "batch_q01"))
        self.assertNotEqual(rc, 0)
        self.assertIn("still Question", out)
        rc, out = self.sh(block, self.env(ws, "batch_missing"))
        self.assertNotEqual(rc, 0)
        self.assertIn("does not exist", out)

    def test_dry_run_then_apply_with_the_reviewed_token_pushes_only_a_branch(self):
        ws, remote = self.workspace("happy")
        self.runlog(ws, "batch_h01")
        rc, out, token = self.dry_run(ws, "batch_h01")
        self.assertEqual(rc, 0, out)
        self.assertTrue(token, "the dry run printed no token")
        self.assertEqual(self.status(ws), "", "the dry run wrote something")
        main_before = out_of("git", "-C", remote, "rev-parse", "main")
        env = self.env(ws, "batch_h01", EXPECT=token)
        rc, out = self.sh(step(self.wf, "apply", "Apply"), env, cwd=ws)
        self.assertEqual(rc, 0, out)
        rc, out = self.sh(step(self.wf, "apply", "Push a new branch"), env, cwd=ws)
        self.assertEqual(rc, 0, out)
        self.assertIn("refs/heads/qa-apply/batch_h01-77", self.heads(remote))
        self.assertEqual(main_before, out_of("git", "-C", remote, "rev-parse", "main"), "main moved")
        files = out_of("git", "-C", remote, "show", "--name-only", "--format=", "qa-apply/batch_h01-77").split()
        self.assertEqual(files, ["workbook/demo-plan.xlsx"])

    def test_a_wrong_token_is_refused_without_writing(self):
        ws, _ = self.workspace("wrong")
        self.runlog(ws, "batch_w01")
        rc, out = self.sh(step(self.wf, "apply", "Apply"), self.env(ws, "batch_w01", EXPECT="v2-deadbeefdeadbeef"), cwd=ws)
        self.assertNotEqual(rc, 0)
        self.assertIn("Nothing was written", out)
        self.assertEqual(self.status(ws), "")

    def test_a_token_that_went_stale_is_refused_without_writing(self):
        ws, _ = self.workspace("stale")
        self.runlog(ws, "batch_s01")
        rc, out, token = self.dry_run(ws, "batch_s01")
        self.assertEqual(rc, 0, out)
        # the batch changes after the dry run was reviewed: its token no longer matches
        path = os.path.join(ws, "runs", "batch_s01.json")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace("fixture", "changed after the review", 1))
        subprocess.run(["git", *GIT_ID, "-C", ws, "commit", "-qam", "edit"], check=True)
        rc, out = self.sh(step(self.wf, "apply", "Apply"), self.env(ws, "batch_s01", EXPECT=token), cwd=ws)
        self.assertNotEqual(rc, 0)
        self.assertIn("changed since the dry run", out)
        self.assertEqual(self.status(ws), "")

    def test_a_file_outside_the_workbook_blocks_the_push(self):
        ws, remote = self.workspace("unexpected")
        self.runlog(ws, "batch_u01")
        rc, out, token = self.dry_run(ws, "batch_u01")
        self.assertEqual(rc, 0, out)
        env = self.env(ws, "batch_u01", EXPECT=token)
        self.assertEqual(self.sh(step(self.wf, "apply", "Apply"), env, cwd=ws)[0], 0)
        with open(os.path.join(ws, "runs", "unexpected.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        rc, out = self.sh(step(self.wf, "apply", "Push a new branch"), env, cwd=ws)
        self.assertNotEqual(rc, 0)
        self.assertIn("unexpected files changed", out)
        self.assertEqual(self.heads(remote), ["refs/heads/main"])


if __name__ == "__main__":
    unittest.main()

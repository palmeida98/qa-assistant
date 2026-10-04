"""scripts/workspace_ci.py and the workflow templates of docs/workspace/ci/ (plan phase 11; technique-mobile.md §11):
install (private repository only, dry run, secrets never on a command line), check (a dry run of the workflow
against the workspace), the workflow's own steps (batch, write-env, blocked, import-flows) and the templates'
shape (triggers, permissions, standard runners only, no API key, nothing of a project).

python -m unittest discover -s tests   (from the root of qa-assistant)

gh is always a fake; every repository is temporary (test_workspace_git.isolate_git): nothing reaches GitHub.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

import workspace_ci as wc
import workspace_git as wg
from test_workspace_git import isolate_git, restore_git
from tracker_kit import JIRA

SLUG = "demo-shop/demo-shop-qa"
CI_DIR = os.path.join(ROOT, "docs", "workspace", "ci")
PROJECT = {"project": "demo-shop", "tracker": "jira", "environments": {"test": "https://shop.demo"},
           "modules": ["mobile"],
           "jira": JIRA,
           "mobile": {"platform": "android", "flows": "flows", "app_id": "com.demoshop.app",
                      "variables": ["SHOP_APP_USER"],
                      "build": {"source": "release build-1.4.0-140", "sha256": "a" * 64, "version": "1.4.0 (140)"}},
           "ci": {"timeout_minutes": 60, "max_turns": 80, "secrets": ["SHOP_APP_USER"],
                  "login": {"mode": "manual-issue", "flow": "flows/_request_link.yaml", "timeout_minutes": 20}}}
USER_EMAIL = "tester@demoshop.example"


def setUpModule():
    isolate_git()


def tearDownModule():
    restore_git()


class FakeGh:
    def __init__(self, visibility="PRIVATE", secrets=(), billing=None, budgets=None):
        self.visibility, self.secrets = visibility, set(secrets)
        self.billing, self.budgets = billing, budgets
        self.calls = []

    def __call__(self, *args, interactive=False):
        self.calls.append((args, interactive))
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        fail = SimpleNamespace(returncode=1, stdout="", stderr="HTTP 404")
        if args[:2] == ("repo", "view"):
            return None if self.visibility is None else SimpleNamespace(
                returncode=0, stdout=json.dumps({"visibility": self.visibility}), stderr="")
        if args[:2] == ("secret", "list"):
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"name": n} for n in sorted(self.secrets)]),
                                   stderr="")
        if args[:2] == ("secret", "set"):
            assert interactive, "a secret's value is only ever typed by the user"
            self.secrets.add(args[2])
            return ok
        if args[:2] == ("variable", "set"):
            return ok
        if args[0] == "api" and "billing/usage" in args[1]:
            return fail if self.billing is None else SimpleNamespace(returncode=0, stdout=json.dumps(self.billing),
                                                                     stderr="")
        if args[0] == "api" and "budgets" in args[1]:
            return fail if self.budgets is None else SimpleNamespace(returncode=0, stdout=json.dumps(self.budgets),
                                                                     stderr="")
        raise AssertionError(f"unexpected gh {args}")

    def commands(self, *prefix):
        return [a for a, _ in self.calls if a[:len(prefix)] == prefix]


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=True).stdout


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self.tmp.name, "demo-shop")
        os.makedirs(os.path.join(self.ws, "flows"))
        for name in ("TC-MOB-001_login.yaml", "_request_link.yaml"):
            open(os.path.join(self.ws, "flows", name), "w").close()
        self.project()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in ("GITHUB_OUTPUT", "QA_SECRETS_JSON"):
            os.environ.pop(k, None)
        os.environ["GITHUB_REPOSITORY"] = SLUG

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, **changes):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({**PROJECT, **changes}, f)

    def run_(self, gh, *args, terminal=False):
        out = io.StringIO()
        with mock.patch.object(wc, "gh", gh), mock.patch.object(wg, "gh", lambda *a: gh(*a)), \
                mock.patch.object(wc, "is_terminal", lambda: terminal), \
                mock.patch.object(wc, "now", lambda: datetime(2026, 9, 29, 12)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = wc.main([self.ws, *args])
        return code, out.getvalue()


class Install(Base):
    def test_dry_run_writes_nothing_and_lists_the_secrets(self):
        gh = FakeGh(secrets={"CLAUDE_CODE_OAUTH_TOKEN"})
        code, out = self.run_(gh, "install")
        self.assertEqual(code, 0, out)
        self.assertIn(f"repository: {SLUG} (PRIVATE)", out)
        self.assertIn("would copy: mobile.yml -> .github/workflows/qa-mobile.yml (new)", out)
        self.assertIn("secret CLAUDE_CODE_OAUTH_TOKEN: exists", out)
        self.assertIn("secret QA_ASSISTANT_TOKEN: missing", out)
        self.assertIn("secret SHOP_APP_USER: missing", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".github")))
        self.assertEqual(gh.commands("secret", "set") + gh.commands("variable", "set"), [])

    def test_yes_copies_the_templates_and_the_user_types_the_secrets(self):
        gh = FakeGh()
        code, out = self.run_(gh, "install", "--yes", terminal=True)
        self.assertEqual(code, 0, out)
        for name, dest in wc.TEMPLATES.items():
            with open(os.path.join(CI_DIR, name), "rb") as a, open(os.path.join(self.ws, dest), "rb") as b:
                self.assertEqual(a.read(), b.read())
        sets = gh.commands("secret", "set")
        self.assertEqual([c[2] for c in sets], ["CLAUDE_CODE_OAUTH_TOKEN", "QA_ASSISTANT_TOKEN", "SHOP_APP_USER"])
        for c in sets:
            self.assertEqual(c[3:], ("--repo", SLUG))             # the name only: never a value
        self.assertTrue(all(interactive for a, interactive in gh.calls if a[:2] == ("secret", "set")))

    def test_outside_a_terminal_the_commands_are_printed_for_the_user(self):
        gh = FakeGh()
        code, out = self.run_(gh, "install", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn(f"to run by the user, in a terminal: gh secret set SHOP_APP_USER --repo {SLUG}", out)
        self.assertEqual(gh.commands("secret", "set"), [])

    def test_only_a_private_repository(self):
        for vis, extra, code in [("PUBLIC", ("--i-confirm-private",), 1), ("INTERNAL", (), 1), (None, (), 1),
                                 (None, ("--i-confirm-private",), 0)]:
            with self.subTest(vis=vis, extra=extra):
                self.assertEqual(self.run_(FakeGh(visibility=vis), "install", *extra)[0], code)

    def test_no_ci_section_or_an_invalid_one(self):
        proj = dict(PROJECT)
        del proj["ci"]
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f)
        self.assertEqual(self.run_(FakeGh(), "install")[0], 2)
        self.project(ci={"runner": "ubuntu-24.04-8core"})
        code, out = self.run_(FakeGh(), "install")
        self.assertEqual(code, 2)
        self.assertIn("ci.runner", out)


class Check(Base):
    def installed(self):
        gh = FakeGh(secrets={"CLAUDE_CODE_OAUTH_TOKEN", "QA_ASSISTANT_TOKEN", "SHOP_APP_USER"})
        self.assertEqual(self.run_(gh, "install", "--yes")[0], 0)
        return gh

    def test_everything_in_place(self):
        gh = self.installed()
        os.makedirs(os.path.join(self.ws, "batches"))
        with open(os.path.join(self.ws, "batches", "chat-smoke.yaml"), "w", encoding="utf-8") as f:
            f.write("cases: [TC-MOB-001, TC-MOB-002]\n")
        gh.billing = {"usageItems": [{"product": "actions", "unitType": "Minutes", "grossQuantity": 312,
                                      "netAmount": 0}]}
        code, out = self.run_(gh, "check", "--batch", "chat-smoke")
        self.assertEqual(code, 0, out)
        self.assertIn("batch chat-smoke: 2 case(s), 1 with a flow", out)
        self.assertIn("Actions this month: 312 minutes used", out)
        self.assertIn("cannot be read through the API", out)                # a user account: no budgets API

    def test_problems(self):
        gh = self.installed()
        with open(os.path.join(self.ws, ".github", "workflows", "qa-mobile.yml"), "a", encoding="utf-8") as f:
            f.write("# edited by hand\n")
        gh.secrets.discard("SHOP_APP_USER")
        os.remove(os.path.join(self.ws, "flows", "_request_link.yaml"))
        self.project(mobile={**PROJECT["mobile"], "build": {"source": "Downloads folder"}})
        gh.billing = {"usageItems": [{"unitType": "Minutes", "grossQuantity": 2100, "netAmount": 0.8}]}
        gh.budgets = {"budgets": [{"budget_product_sku": "actions", "budget_amount": 10,
                                   "prevent_further_usage": False}]}
        code, out = self.run_(gh, "check", "--batch", "nothing-planned")
        self.assertEqual(code, 1)
        for needle in ("differs from the template", "secrets missing: SHOP_APP_USER", "ci.login.flow",
                       "not a release", "neither a planned batch", "0.80 billed", "no Actions budget of 0"):
            self.assertIn(needle, out)

    def test_not_installed(self):
        code, out = self.run_(FakeGh(), "check")
        self.assertEqual(code, 1)
        self.assertIn("is not installed", out)


class WorkflowSteps(Base):
    def outputs(self):
        with open(os.environ["GITHUB_OUTPUT"], encoding="utf-8") as f:
            return dict(line.split("=", 1) for line in f.read().splitlines())

    def test_batch_from_a_plan_or_case_ids_split_by_mode(self):
        os.environ["GITHUB_OUTPUT"] = os.path.join(self.tmp.name, "out")
        os.makedirs(os.path.join(self.ws, "batches"))
        with open(os.path.join(self.ws, "batches", "chat-smoke.yaml"), "w", encoding="utf-8") as f:
            f.write("cases: [TC-MOB-001, TC-MOB-002, TC-MOB-001]\n")
        code, out = self.run_(FakeGh(), "batch", "--input", "chat-smoke", "--mode", "both", "--run-id", "4242")
        self.assertEqual(code, 0, out)
        o = self.outputs()
        self.assertEqual((o["name"], o["run_log"], o["branch"]), ("chat-smoke", "batch_chat-smoke_r4242",
                                                                  "run/chat-smoke-4242"))
        self.assertEqual((o["cases"], o["flow_cases"], o["agent_cases"]),
                         ("TC-MOB-001 TC-MOB-002", "TC-MOB-001", "TC-MOB-002"))
        self.assertEqual((o["login"], o["max_turns"]), ("manual-issue", "80"))
        with open(os.path.join(self.ws, "runs", "batch_chat-smoke_r4242.json"), encoding="utf-8") as f:
            log = json.load(f)
        self.assertEqual(log["run"], {"environment": "test", "build": "1.4.0 (140)"})
        self.assertEqual(log["results"], [])
        for mode, flows, agent in [("flows", "TC-MOB-001", ""), ("conversational", "", "TC-MOB-001 TC-MOB-002")]:
            os.remove(os.environ["GITHUB_OUTPUT"])
            self.run_(FakeGh(), "batch", "--input", "TC-MOB-001, TC-MOB-002", "--mode", mode, "--run-id", "7")
            o = self.outputs()
            self.assertEqual((o["name"], o["flow_cases"], o["agent_cases"]), ("adhoc", flows, agent), mode)
        self.assertEqual(self.run_(FakeGh(), "batch", "--input", "rm -rf", "--run-id", "7")[0], 2)
        self.assertEqual(self.run_(FakeGh(), "batch", "--input", "TC-MOB-001", "--mode", "x", "--run-id", "7")[0], 2)

    def test_write_env_takes_only_ci_secrets_and_never_prints_a_value(self):
        os.environ["QA_SECRETS_JSON"] = json.dumps({"SHOP_APP_USER": USER_EMAIL, "CLAUDE_CODE_OAUTH_TOKEN": "x" * 40,
                                                    "github_token": "y" * 40})
        code, out = self.run_(FakeGh(), "write-env")
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.ws, ".env"), encoding="utf-8") as f:
            self.assertEqual(f.read(), f"SHOP_APP_USER={USER_EMAIL}\n")
        self.assertNotIn(USER_EMAIL, out)
        os.environ["QA_SECRETS_JSON"] = json.dumps({"OTHER": "z"})
        code, out = self.run_(FakeGh(), "write-env")
        self.assertEqual(code, 2)
        self.assertIn("SHOP_APP_USER", out)

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_write_env_refuses_when_env_is_not_ignored(self):
        git(self.ws, "init", "-q")
        os.environ["QA_SECRETS_JSON"] = json.dumps({"SHOP_APP_USER": USER_EMAIL})
        self.assertEqual(self.run_(FakeGh(), "write-env")[0], 1)
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".env")))
        with open(os.path.join(self.ws, ".gitignore"), "w", encoding="utf-8") as f:
            f.write(".env\n")
        self.assertEqual(self.run_(FakeGh(), "write-env")[0], 0)

    def test_blocked_and_import_flows(self):
        self.run_(FakeGh(), "batch", "--input", "TC-MOB-001 TC-MOB-002", "--run-id", "9")
        code, out = self.run_(FakeGh(), "blocked", "--run-log", "batch_adhoc_r9", "--cases", "TC-MOB-001",
                              "TC-MOB-002", "--reason", "login: no link in time")
        self.assertEqual(code, 0, out)
        report = os.path.join(self.tmp.name, "flows.json")
        with open(report, "w", encoding="utf-8") as f:
            json.dump({"target": "android: emulator-5554", "results": [
                {"case": "TC-MOB-001", "flow": "TC-MOB-001_login.yaml", "suggested_status": "Pass", "message": ""},
                {"case": None, "flow": "x.yaml", "suggested_status": "Blocked", "message": "no case"}]}, f)
        self.assertEqual(self.run_(FakeGh(), "import-flows", "--run-log", "batch_adhoc_r9", "--report", report)[0], 0)
        with open(os.path.join(self.ws, "runs", "batch_adhoc_r9.json"), encoding="utf-8") as f:
            results = {r["tc"]: r for r in json.load(f)["results"]}
        self.assertEqual(results["TC-MOB-001"]["status"], "Pass")
        self.assertIn("flow TC-MOB-001_login.yaml on android: emulator-5554", results["TC-MOB-001"]["actual_result"])
        self.assertEqual(results["TC-MOB-002"]["status"], "Blocked")
        self.assertEqual(self.run_(FakeGh(), "import-flows", "--run-log", "batch_adhoc_r9", "--report", "none")[0], 2)


class Agent(Base):
    """plan 11.5: the prompt and the checks after the agent."""

    def test_the_prompt_is_the_installed_template_filled_in(self):
        self.assertEqual(self.run_(FakeGh(), "prompt", "--run-log", "adhoc_r9", "--cases", "TC-MOB-001")[0], 2)
        self.assertEqual(self.run_(FakeGh(), "install", "--yes")[0], 0)
        code, out = self.run_(FakeGh(), "prompt", "--run-log", "adhoc_r9", "--cases", "TC-MOB-001", "TC-MOB-002")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("/test-mobile\n"))                     # the skill, expanded by claude -p
        self.assertNotRegex(out, r"\{\{[a-z_]+\}\}")
        for needle in ("Cases, in this order: TC-MOB-001 TC-MOB-002", "runs/batch_adhoc_r9.json",
                       "--batch batch_adhoc_r9", f"python {ROOT}/scripts/run_log.py {self.ws} add batch_adhoc_r9",
                       "Limits: 80 turns", '"proposed": true', "Never apply", "runs/batch_adhoc_r9.summary.md"):
            self.assertIn(needle, out)

    def post(self, results, evidence=b"screen text"):
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write(f"SHOP_APP_USER={USER_EMAIL}\nSHORT=ab\n")
        os.makedirs(os.path.join(self.ws, "runs"), exist_ok=True)
        os.makedirs(os.path.join(self.ws, "evidence", "batch_adhoc_r9"), exist_ok=True)
        with open(os.path.join(self.ws, "runs", "batch_adhoc_r9.json"), "w", encoding="utf-8") as f:
            json.dump({"run": {"environment": "test", "build": "1.4.0 (140)"}, "results": results}, f)
        with open(os.path.join(self.ws, "evidence", "batch_adhoc_r9", "TC-MOB-002_t1.png"), "wb") as f:
            f.write(evidence)
        report = os.path.join(self.tmp.name, "post.json")
        code, out = self.run_(FakeGh(), "post-check", "--run-log", "adhoc_r9", "--report", report)
        with open(report, encoding="utf-8") as f:
            return code, out, json.load(f)

    def test_a_value_of_env_in_a_file_to_commit_stops_everything(self):
        for evidence in (USER_EMAIL.encode("utf-8"), b"\x89PNG" + USER_EMAIL.encode("utf-16-le")):
            with self.subTest(evidence=evidence[:6]):
                code, out, report = self.post([{"tc": "TC-MOB-001", "status": "Pass", "actual_result": "ok"}],
                                              evidence)
                self.assertEqual(code, 1)
                self.assertFalse(report["safe"])
                self.assertEqual(report["leaks"], ["evidence/batch_adhoc_r9/TC-MOB-002_t1.png: SHOP_APP_USER"])
                self.assertIn("safe=false", out)
                self.assertNotIn(USER_EMAIL, out + json.dumps(report))

    def test_new_defects_are_proposals_and_the_run_log_is_validated_without_applying(self):
        results = [{"tc": "DS-1", "status": "Pass", "actual_result": "ok"},
                   {"tc": "DS-2", "status": "Fail", "actual_result": "wrong answer",
                    "new_defect": {"title": "Wrong total", "expected": "12", "observed": "21"}},
                   {"tc": "DS-3", "status": "Question", "actual_result": "is 2 the limit?"},
                   {"tc": "DS-4", "status": "Passed", "actual_result": "?"}]
        code, out, report = self.post(results)
        self.assertEqual(code, 0, out)
        self.assertTrue(report["safe"])
        self.assertEqual(report["proposed_added"], ["DS-2"])
        with open(os.path.join(self.ws, "runs", "batch_adhoc_r9.json"), encoding="utf-8") as f:
            self.assertIs(json.load(f)["results"][1]["new_defect"]["proposed"], True)
        self.assertEqual(len(report["problems"]), 1, report["problems"])     # Question is not a problem here
        self.assertIn("'Passed'", report["problems"][0])
        self.assertEqual(report["files"], ["evidence/batch_adhoc_r9/TC-MOB-002_t1.png", "runs/batch_adhoc_r9.json"])
        self.assertIn("safe=true", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "tracker", "sent")))   # nothing sent


class Review(Base):
    """plan 11.6: the pull request's body."""

    def test_the_body_has_the_totals_the_proposed_bugs_the_questions_and_the_limits(self):
        self.assertEqual(self.run_(FakeGh(), "install", "--yes")[0], 0)
        os.makedirs(os.path.join(self.ws, "runs"))
        log = {"mode": "both: remote run 4242 (technique-mobile.md §11)",
               "run": {"environment": "test", "build": "1.4.0 (140)"},
               "remote": {"run_id": "4242", "batch": "chat", "cases": ["TC-MOB-001", "TC-MOB-002", "TC-MOB-003",
                                                                       "TC-MOB-004"]},
               "results": [{"tc": "TC-MOB-001", "status": "Pass", "actual_result": "ok"},
                           {"tc": "TC-MOB-002", "status": "Fail", "actual_result": "wrong total",
                            "evidence": ["evidence/batch_chat_r4242/TC-MOB-002_t1.png"],
                            "new_defect": {"title": "Wrong total in the cart", "expected": "12", "observed": "21",
                                           "proposed": True}},
                           {"tc": "TC-MOB-003", "status": "Question", "actual_result": "Is 2 the limit?"}]}
        with open(os.path.join(self.ws, "runs", "batch_chat_r4242.json"), "w", encoding="utf-8") as f:
            json.dump(log, f)
        with open(os.path.join(self.ws, "runs", "batch_chat_r4242.summary.md"), "w", encoding="utf-8") as f:
            f.write("TC-MOB-004 not run: out of turns.\n")
        agent, checks = os.path.join(self.tmp.name, "claude.json"), os.path.join(self.tmp.name, "checks.json")
        with open(agent, "w", encoding="utf-8") as f:
            json.dump({"num_turns": 80, "subtype": "error_max_turns", "result": "..."}, f)
        with open(checks, "w", encoding="utf-8") as f:
            json.dump({"safe": True, "files": ["a", "b"], "proposed_added": ["TC-MOB-002"],
                       "problems": ["item 9: x"]}, f)
        os.environ["GITHUB_RUN_ID"] = "4242"
        started = str(datetime(2026, 9, 29, 11, 25).timestamp())
        code, out = self.run_(FakeGh(), "pr-body", "--run-log", "chat_r4242", "--build", "build-1.4.0-140",
                              "--branch", "run/chat-4242", "--started", started, "--agent", agent, "--checks", checks)
        self.assertEqual(code, 0, out)
        self.assertNotRegex(out, r"\{\{[a-z_]+\}\}")
        for needle in ("## QA run: chat (4242)", "Build **build-1.4.0-140** · environment `test` · both",
                       "Time: 35 of 60 minutes · agent turns: 80 (error_max_turns) of 80",
                       f"https://github.com/{SLUG}/actions/runs/4242", "| Fail | 1 |", "| Question | 1 |",
                       "| Not run | 1 (TC-MOB-004) |",
                       ("- **Wrong total in the cart** — case TC-MOB-002: expected 12 → observed 21 · evidence: "
                        f"[TC-MOB-002_t1.png](https://github.com/{SLUG}/blob/run/chat-4242/evidence/batch_chat_r4242/"
                        "TC-MOB-002_t1.png)"), "- **TC-MOB-003**: Is 2 the limit?",
                       "- 2 file(s) committed; no value of .env in them", "- run log: item 9: x",
                       "TC-MOB-004 not run: out of turns.", "`runs/batch_chat_r4242.json`"):
            self.assertIn(needle, out)


@unittest.skipUnless(shutil.which("git"), "no git")
class ApplyMerged(Base):
    """plan 11.6: after the merge, each run log added or changed is applied, or refused and named."""

    def setUp(self):
        super().setUp()
        self.project(tracker="excel", excel={"file": "demo.xlsx"})
        with open(os.path.join(self.ws, "demo.xlsx"), "w", encoding="utf-8") as f:
            f.write("workbook v1")
        self.remote = os.path.join(self.tmp.name, "remote.git")
        git(self.tmp.name, "init", "-q", "--bare", "--initial-branch=main", self.remote)
        git(self.ws, "init", "-q", "--initial-branch=main")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "base")
        git(self.ws, "remote", "add", "origin", self.remote)
        git(self.ws, "push", "-q", "-u", "origin", "main")
        self.before = git(self.ws, "rev-parse", "HEAD").strip()
        os.makedirs(os.path.join(self.ws, "runs"))
        new = {"title": "Wrong total", "expected": "12", "observed": "21"}
        for name, items in [("batch_a_r1", [{"tc": "TC-MOB-001", "status": "Pass", "actual_result": "ok"}]),
                            ("batch_b_r1", [{"tc": "TC-MOB-002", "status": "Fail", "actual_result": "x",
                                             "new_defect": {**new, "proposed": True}}]),
                            ("batch_c_r1", [{"tc": "TC-MOB-003", "status": "Question", "actual_result": "?"}])]:
            with open(os.path.join(self.ws, "runs", f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump({"results": items}, f)
        with open(os.path.join(self.ws, "runs", "batch_a_r1.summary.md"), "w", encoding="utf-8") as f:
            f.write("summary\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "merge run/a-1")
        git(self.ws, "push", "-q", "origin", "main")
        self.after = git(self.ws, "rev-parse", "HEAD").strip()
        self.applied = []

    def adapter(self, workspace, tracker, path):
        self.applied.append((tracker, os.path.basename(path)))
        with open(os.path.join(workspace, "demo.xlsx"), "w", encoding="utf-8") as f:
            f.write("workbook v2")
        return 0, "Cross-check: 0 problems. Saved."

    def gh(self):
        comments = []

        def fake(*args, interactive=False):
            if args[0] == "api" and args[1].endswith("/pulls"):
                return SimpleNamespace(returncode=0, stdout=json.dumps([{"number": 7}]), stderr="")
            if args[:2] == ("pr", "comment"):
                comments.append(args)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            raise AssertionError(args)
        return fake, comments

    def test_only_the_clean_run_log_is_applied_the_others_are_named(self):
        fake, comments = self.gh()
        with mock.patch.object(wc, "run_adapter", self.adapter):
            code, out = self.run_(fake, "apply-merged", "--before", self.before, "--after", self.after)
        self.assertEqual(code, 1, out)
        self.assertEqual(self.applied, [("excel", "batch_a_r1.json")])
        self.assertIn('`runs/batch_b_r1.json`: NOT APPLIED -- TC-MOB-002: the new_defect still has "proposed"', out)
        self.assertIn("`runs/batch_c_r1.json`: NOT APPLIED -- TC-MOB-003: still Question", out)
        self.assertEqual(git(self.remote, "log", "-1", "--format=%s", "main").strip(),
                         "QA apply: batch_a_r1.json, batch_b_r1.json, batch_c_r1.json")
        self.assertEqual(git(self.remote, "show", "main:demo.xlsx"), "workbook v2")
        self.assertEqual(comments[0][2], "7")
        self.assertIn("applied -- Cross-check: 0 problems. Saved.", comments[0][comments[0].index("--body") + 1])

    def test_an_adapter_failure_is_not_committed_as_success_and_nothing_merged_is_nothing_to_do(self):
        fake, _ = self.gh()
        with mock.patch.object(wc, "run_adapter", lambda *a: (4, "NOTHING APPLIED -- invalid batch")):
            code, out = self.run_(fake, "apply-merged", "--before", self.before, "--after", self.after)
        self.assertEqual(code, 1)
        self.assertIn("adapter exit 4 -- NOTHING APPLIED -- invalid batch", out)
        with mock.patch.object(wc, "run_adapter", self.adapter):
            code, out = self.run_(fake, "apply-merged", "--before", self.after, "--after", self.after)
        self.assertEqual((code, self.applied), (0, []))
        self.assertIn("nothing to apply", out)

    def test_a_first_push_reads_the_commit_itself(self):
        fake, _ = self.gh()
        with mock.patch.object(wc, "run_adapter", self.adapter):
            self.run_(fake, "apply-merged", "--before", "0" * 40, "--after", self.after)
        self.assertEqual(self.applied, [("excel", "batch_a_r1.json")])


class Templates(unittest.TestCase):
    """The templates hold nothing of a project and only use what is free."""

    def load(self, name):
        with open(os.path.join(CI_DIR, name), encoding="utf-8") as f:
            return f.read()

    def test_mobile_workflow_shape(self):
        text = self.load("mobile.yml")
        wf = yaml.safe_load(text)
        trigger = wf.get("on", wf.get(True))                           # YAML 1.1 reads `on` as true
        self.assertEqual(set(trigger), {"workflow_dispatch"})
        self.assertEqual(set(trigger["workflow_dispatch"]["inputs"]), {"batch", "build", "mode"})
        self.assertEqual(trigger["workflow_dispatch"]["inputs"]["mode"]["options"], ["conversational", "flows", "both"])
        self.assertEqual(wf["permissions"], {"contents": "write", "pull-requests": "write", "issues": "write"})
        self.assertEqual(wf["jobs"]["plan"]["runs-on"], "ubuntu-24.04")
        self.assertEqual(wf["jobs"]["run"]["runs-on"], "${{ needs.plan.outputs.runner }}")
        self.assertIn("fromJSON(needs.plan.outputs.timeout)", wf["jobs"]["run"]["timeout-minutes"])
        self.assertIn(r"^ubuntu-(latest|[0-9]{2}\.04)$", text)            # the same rule as validate_project.py
        for runner, ok in [("ubuntu-24.04", True), ("ubuntu-latest", True), ("ubuntu-24.04-4core", False),
                           ("self-hosted", False)]:
            self.assertEqual(bool(re.fullmatch(r"ubuntu-(latest|[0-9]{2}\.04)", runner)), ok)

    def test_templates_use_only_free_services_and_no_project_data(self):
        for name in sorted(os.listdir(CI_DIR)):
            if name == "README.md":                                    # the settings, with Demo Shop examples
                continue
            text = self.load(name)
            with self.subTest(template=name):
                self.assertNotIn("ANTHROPIC_API_KEY", text.replace("never an API key", ""))
                self.assertNotRegex(text.lower(), r"demoshop|demo-shop|shop\.demo")
                secrets = set(re.findall(r"secrets\.([A-Z_]+)", text))
                self.assertLessEqual(secrets, {"CLAUDE_CODE_OAUTH_TOKEN", "QA_ASSISTANT_TOKEN"})

    def test_every_script_the_workflows_call_exists_with_its_subcommand(self):
        for name in sorted(os.listdir(CI_DIR)):
            if not name.endswith(".yml"):
                continue
            text = self.load(name)
            for script in set(re.findall(r"\$QA/scripts/([a-z_]+\.py)", text)):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", script)), script)
            parser_help = io.StringIO()
            for sub in set(re.findall(r"workspace_ci\.py\" \"\$WS\" ([a-z-]+)", text)):
                with self.subTest(template=name, subcommand=sub), contextlib.redirect_stdout(parser_help), \
                        self.assertRaises(SystemExit) as e:
                    wc.main([ROOT, sub, "--help"])
                self.assertEqual(e.exception.code, 0)


if __name__ == "__main__":
    unittest.main()

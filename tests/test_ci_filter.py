"""The `paths` filter of .github/workflows/qa-audit.yml decides whether the browser tests run on a push or a pull request.
This test keeps it honest: everything those tests exercise or execute matches it, push and pull request use the same list,
and documentation or unrelated code does not match. It also checks the shape of ci.yml (docs/ci.md).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import fnmatch
import os
import re
import subprocess
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
QA_AUDIT = os.path.join(WORKFLOWS, "qa-audit.yml")
CI = os.path.join(WORKFLOWS, "ci.yml")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def load(path):
    data = yaml.safe_load(read(path))
    # PyYAML reads the bare key `on` as the boolean True
    data["on"] = data.pop(True, data.get("on"))
    return data


def paths(event="pull_request"):
    return load(QA_AUDIT)["on"][event]["paths"]


def matches(path):
    # in a GitHub filter `**` crosses folders and `*` does not; fnmatch's `*` crosses them too, which is enough here
    return any(fnmatch.fnmatchcase(path, p.replace("**", "*")) for p in paths())


def tracked():
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False)
    return [f for f in r.stdout.splitlines() if f] if r.returncode == 0 else []


class EnginePathFilter(unittest.TestCase):
    def test_push_and_pull_request_share_the_same_paths(self):
        self.assertEqual(paths("push"), paths("pull_request"))

    def test_every_file_of_the_engine_its_tests_and_its_fixtures_matches(self):
        files = tracked()
        if not files:
            self.skipTest("not a git checkout")
        engine = [f for f in files if f.startswith(("src/", "tests/integration/", "tests/fixtures/"))]
        self.assertGreater(len(engine), 20)
        self.assertEqual([f for f in engine if not matches(f)], [])

    def test_the_dependency_files_and_the_workflow_itself_match(self):
        for f in ("pyproject.toml", "requirements.txt", ".github/workflows/qa-audit.yml"):
            with self.subTest(path=f):
                self.assertTrue(matches(f))

    def test_every_script_or_test_path_the_workflow_runs_matches(self):
        text = read(QA_AUDIT)
        used = set(re.findall(r"(?:scripts|tests)/[\w/.\-]+\.(?:py|yml|json)", text[text.index("\njobs:"):]))
        self.assertIn("scripts/ci_test_summary.py", used)
        for f in sorted(used):
            if os.path.exists(os.path.join(ROOT, f)):
                with self.subTest(path=f):
                    self.assertTrue(matches(f), f"{f} is run by qa-audit.yml but a change to it would not trigger it")

    def test_documentation_and_unrelated_code_do_not_match(self):
        for f in ("README.md", "CHANGELOG.md", "docs/qa-audit.md", "docs/ci.md", "scripts/device.py",
                  "adapters/excel/apply.py", "tests/test_run_log.py", "examples/demo/project.yaml", "ruff.toml",
                  ".github/workflows/ci.yml"):
            with self.subTest(path=f):
                self.assertFalse(matches(f))


class Workflows(unittest.TestCase):
    def test_only_the_two_workflows_of_docs_ci_exist(self):
        found = sorted(f for f in os.listdir(WORKFLOWS) if f.endswith((".yml", ".yaml")))
        self.assertEqual(found, ["ci.yml", "qa-audit.yml"])

    def test_ci_jobs_and_read_only_token(self):
        ci = load(CI)
        self.assertEqual(set(ci["jobs"]), {"lint-secrets", "tests", "install"})
        self.assertEqual(ci["permissions"], {"contents": "read"})
        self.assertEqual(load(QA_AUDIT)["permissions"], {"contents": "read"})

    def test_the_tests_job_runs_the_golden_and_allows_only_windows_skips(self):
        steps = "\n".join(s.get("run", "") for s in load(CI)["jobs"]["tests"]["steps"])
        self.assertIn("libreoffice-calc-nogui", steps)
        self.assertIn("scripts/golden_check.py", steps)
        allowed = re.findall(r'--allow-skip "([^"]+)"', steps)
        self.assertEqual(allowed, ["Windows"])

    def test_install_does_not_run_on_pull_requests(self):
        self.assertEqual(load(CI)["jobs"]["install"]["if"], "${{ github.event_name != 'pull_request' }}")

    def test_no_step_keeps_credentials(self):
        for path in (CI, QA_AUDIT):
            for name, job in load(path)["jobs"].items():
                for step in job["steps"]:
                    if str(step.get("uses", "")).startswith("actions/checkout@"):
                        with self.subTest(workflow=os.path.basename(path), job=name):
                            self.assertIs(step["with"]["persist-credentials"], False)


if __name__ == "__main__":
    unittest.main()

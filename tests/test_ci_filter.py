"""The `changes` job of .github/workflows/ci.yml decides, for a pull request, whether the browser jobs run. This test keeps its
PATTERN honest: everything the browser jobs test or execute matches it, and documentation does not.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CI = os.path.join(ROOT, ".github", "workflows", "ci.yml")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def pattern():
    m = re.search(r"^\s+PATTERN: '(.+)'\s*$", read(CI), re.MULTILINE)
    assert m, "no PATTERN in the changes job"
    return re.compile(m.group(1))


def tracked():
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False)
    return [f for f in r.stdout.splitlines() if f] if r.returncode == 0 else []


def engine_job():
    text = read(CI)
    return text[text.index("  qa-audit-engine:"):text.index("  qa-audit:\n")]


class EnginePathFilter(unittest.TestCase):
    def test_every_file_of_the_engine_its_tests_and_its_fixtures_matches(self):
        rx = pattern()
        files = tracked()
        if not files:
            self.skipTest("not a git checkout")
        engine = [f for f in files if f.startswith(("src/", "tests/integration/", "tests/fixtures/"))]
        self.assertGreater(len(engine), 20)
        self.assertEqual([f for f in engine if not rx.match(f)], [])

    def test_the_dependency_files_and_the_ci_itself_match(self):
        rx = pattern()
        for f in ("pyproject.toml", "requirements.txt", ".github/workflows/ci.yml", ".github/workflows/demo-e2e.yml",
                  ".github/dependabot.yml", ".github/actions/x/action.yml"):
            with self.subTest(path=f):
                self.assertTrue(rx.match(f))

    def test_every_script_or_test_path_the_browser_job_runs_matches(self):
        rx = pattern()
        used = set(re.findall(r"(?:scripts|tests)/[\w/.\-]+\.(?:py|yml|json)", engine_job()))
        self.assertIn("scripts/ci_test_summary.py", used)
        for f in sorted(used):
            if os.path.exists(os.path.join(ROOT, f)):
                with self.subTest(path=f):
                    self.assertTrue(rx.match(f), f"{f} is run by the browser job but a change to it would not trigger it")

    def test_documentation_and_unrelated_code_do_not_match(self):
        rx = pattern()
        for f in ("README.md", "CHANGELOG.md", "docs/qa-audit.md", "docs/README.md", "scripts/device.py", "adapters/excel/apply.py",
                  "tests/test_run_log.py", "examples/demo/project.yaml", "ruff.toml"):
            with self.subTest(path=f):
                self.assertIsNone(rx.match(f))

    def test_the_browser_jobs_wait_for_the_changes_job(self):
        job = engine_job()
        self.assertIn("needs: changes", job)
        self.assertIn("needs.changes.outputs.engine == 'true'", job)


if __name__ == "__main__":
    unittest.main()

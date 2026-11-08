"""scripts/qa_python.py (A-08): the interpreter the framework's scripts run with, found without PyYAML, without the
current directory and without a user-specific path; nothing is installed or created.

Fake interpreters are small shell scripts (POSIX); the real probe is also run against this test's own interpreter.
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import qa_python as qp

POSIX = os.name == "posix"


def fake(path, ok=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"#!/bin/sh\nexit {0 if ok else 1}\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


@unittest.skipUnless(POSIX, "fake interpreters are shell scripts")
class Find(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "my qa (checkout)")           # spaces and parentheses
        os.makedirs(os.path.join(self.root, "scripts"))
        self.empty = os.path.join(self.tmp.name, "empty-path")
        os.makedirs(self.empty)
        gone = os.path.join(self.tmp.name, "no-such-python")
        patcher = mock.patch.object(qp.sys, "executable", gone)               # the running interpreter is not a candidate
        patcher.start()
        self.addCleanup(patcher.stop)

    def find(self, **env):
        return qp.find({"PATH": self.empty, **env}, self.root)

    def test_nothing_usable_gives_instructions_and_creates_nothing(self):
        before = sorted(os.listdir(self.root))
        found = self.find()
        self.assertIsNone(found["command"])
        text = qp.instructions(self.root, found["tried"])
        self.assertIn("-m venv", text)
        self.assertIn(f'"{os.path.join(self.root, ".venv")}"', text)            # a path with spaces is quoted
        self.assertIn("requirements.txt", text)
        self.assertIn("Do not install into the system Python", text)
        self.assertEqual(sorted(os.listdir(self.root)), before)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".venv")))

    def test_the_checkouts_venv_wins_and_is_quoted_when_printed(self):
        venv = fake(qp.venv_python(self.root))
        found = self.find()
        self.assertEqual((found["command"], found["source"]), ([venv], ".venv of the checkout"))
        self.assertEqual(qp.quote(venv), f'"{venv}"')

    def test_an_unusable_venv_falls_to_one_that_is_found_on_path(self):
        fake(qp.venv_python(self.root), ok=False)
        good = fake(os.path.join(self.empty, "python3.12"))
        found = self.find()
        self.assertEqual((found["command"], found["source"]), ([good], "python3.12 on PATH"))
        self.assertEqual(found["tried"][0][0], ".venv of the checkout")

    def test_an_explicit_choice_is_never_silently_replaced(self):
        fake(qp.venv_python(self.root))
        bad = fake(os.path.join(self.tmp.name, "other", "python"), ok=False)
        found = self.find(QA_PYTHON=bad)
        self.assertIsNone(found["command"])
        self.assertEqual([t[0] for t in found["tried"]], ["QA_PYTHON"])
        good = fake(os.path.join(self.tmp.name, "other", "good-python"))
        self.assertEqual(self.find(QA_PYTHON=good)["command"], [good])
        missing = self.find(QA_PYTHON=os.path.join(self.tmp.name, "absent"))
        self.assertEqual(missing["tried"][0][2], "not found")

    def test_a_python_without_the_dependencies_first_on_path_does_not_shadow_a_good_one_later(self):
        # a pre-commit hook puts its own environment first on the PATH: its `python3.12` has no PyYAML
        hook_env = os.path.join(self.tmp.name, "hook-env", "bin")
        activated = os.path.join(self.tmp.name, "activated", "bin")
        fake(os.path.join(hook_env, "python3.12"), ok=False)
        fake(os.path.join(hook_env, "python"), ok=False)
        good = fake(os.path.join(activated, "python3.12"))
        found = qp.find({"PATH": os.pathsep.join([hook_env, activated])}, self.root)
        self.assertEqual(found["command"], [good])
        self.assertEqual(qp.which_all("python3.12", os.pathsep.join([hook_env, activated])),
                         [os.path.join(hook_env, "python3.12"), good])

    def test_a_worktree_uses_the_main_checkouts_venv_without_touching_it(self):
        main = os.path.join(self.tmp.name, "main")
        os.makedirs(os.path.join(main, ".git", "worktrees", "wt"))
        venv = fake(qp.venv_python(main))
        with open(os.path.join(self.root, ".git"), "w", encoding="utf-8") as f:
            f.write(f"gitdir: {os.path.join(main, '.git', 'worktrees', 'wt')}\n")
        self.assertEqual(qp.main_checkout(self.root), main)
        found = self.find()
        self.assertEqual(found["command"], [venv])
        self.assertIn("main checkout", found["source"])
        self.assertIsNone(qp.main_checkout(main))


class Probe(unittest.TestCase):
    def test_the_real_probe_accepts_an_environment_with_the_dependencies_and_rejects_one_without(self):
        ok, why = qp.usable([sys.executable])
        self.assertTrue(ok, why)
        # -S: no site-packages, so yaml/openpyxl cannot be imported by this interpreter (a "generic" Python)
        ok, why = qp.usable([sys.executable, "-S"])
        self.assertFalse(ok)
        self.assertIn("No module named", why)

    def test_a_python_that_cannot_start_is_not_usable(self):
        ok, why = qp.usable([os.path.join(tempfile.gettempdir(), "definitely-not-a-python")])
        self.assertFalse(ok)
        self.assertIn("cannot be started", why)


class Launcher(unittest.TestCase):
    def run_launcher(self, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "qa_python.py"), *args],
                              capture_output=True, text=True, cwd=cwd, env=env, timeout=120, check=False)

    def test_it_runs_from_another_directory_and_prints_one_interpreter(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            done = self.run_launcher(cwd=elsewhere)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(done.stdout.strip().splitlines()), 1)

    def test_run_passes_the_arguments_and_finds_the_scripts_from_anywhere(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            done = self.run_launcher("run", "scripts/run_log.py", "--help", cwd=elsewhere)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("workspace", done.stdout)
            done = self.run_launcher("run", "-m", "adapters.excel.apply", "--help", cwd=elsewhere)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("--expect", done.stdout)

    def test_a_missing_explicit_choice_exits_3_with_what_to_do(self):
        env = {**os.environ, "QA_PYTHON": os.path.join(tempfile.gettempdir(), "absent-python")}
        done = self.run_launcher(env=env)
        self.assertEqual(done.returncode, 3)
        self.assertIn("QA_PYTHON", done.stderr)
        self.assertIn("-m venv", done.stderr)

    def test_the_hook_entries_work_in_a_shell_with_no_python_on_the_path_and_nothing_activated(self):
        # what pre-commit runs for the golden and unit-test hooks: `python scripts/qa_python.py run <args>`; here with
        # an empty PATH, no QA_PYTHON, no VIRTUAL_ENV, a fresh HOME and a different working directory
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as empty:
            env = {"PATH": empty, "HOME": home, "USERPROFILE": home}
            done = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "qa_python.py"), "run", "-c",
                                   "import yaml, openpyxl; print('hook ok')"],
                                  capture_output=True, text=True, cwd=empty, env=env, timeout=120, check=False)
        # the checkout's .venv, or the interpreter running this test, or a clear exit 3 with what to do: never a bare
        # "Executable python not found". The test's own interpreter has the dependencies, so it must succeed.
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("hook ok", done.stdout)

    def test_a_usage_error_is_exit_4(self):
        self.assertEqual(self.run_launcher("nonsense").returncode, 4)
        self.assertEqual(self.run_launcher("run").returncode, 4)

    def test_the_script_itself_needs_only_the_standard_library(self):
        with open(os.path.join(ROOT, "scripts", "qa_python.py"), encoding="utf-8") as f:
            text = f.read()
        for module in ("yaml", "openpyxl", "common"):
            self.assertNotIn(f"\nimport {module}", text)
            self.assertNotIn(f"\nfrom {module} import", text)


if __name__ == "__main__":
    unittest.main()

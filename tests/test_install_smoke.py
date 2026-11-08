"""Isolated smoke of the skills' installation, sync and preflight (plan F20).

Nothing here touches the user's `~/.claude/skills/`, the configured root of workspaces or a real workspace: the sync goes
to a temporary `--target`, HOME is a temporary folder, and the preflight reads the fictional demo through `--workspace`.
It proves the eight skills install from this checkout, that a second sync finds them the same, that the record of origin
is written next to them, and that every skill's preflight runs on a synthetic workspace. The CI repeats it on a clean
clone (the `install` job). It checks the engine, not an agent's obedience to a skill.

python -m unittest tests.test_install_smoke   (from the root of qa-assistant)
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = ["analyze-ticket", "checkpoint", "manage-bugs", "plan-tests", "project-context", "qa-report", "run-tests",
          "test-pbi"]
DEMO = os.path.join(ROOT, "examples", "demo")


class InstallSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = os.path.join(cls.tmp.name, "home")
        cls.target = os.path.join(cls.tmp.name, "skills")
        os.makedirs(cls.home)
        os.makedirs(cls.target)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_script(self, *args):
        env = {**os.environ, "HOME": self.home, "USERPROFILE": self.home}
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", *args[0].split("/")), *args[1:]],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False, env=env)

    def test_1_sync_installs_the_eight_skills_into_a_temporary_target(self):
        dry = self.run_script("sync_skills.py", "--target", self.target)
        self.assertEqual(dry.returncode, 1, dry.stdout + dry.stderr)   # 1: there is something to sync
        self.assertEqual(sorted(os.listdir(self.target)), [])          # a dry run copies nothing
        for skill in SKILLS:
            self.assertIn(f"missing    {skill}", dry.stdout)
        applied = self.run_script("sync_skills.py", "--apply", "--target", self.target)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        installed = [d for d in os.listdir(self.target) if os.path.isdir(os.path.join(self.target, d))]
        self.assertEqual(sorted(installed), SKILLS)
        for skill in SKILLS:
            self.assertTrue(os.path.isfile(os.path.join(self.target, skill, "SKILL.md")), skill)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")))   # the real location was never used

    def test_2_a_second_sync_finds_them_the_same_and_the_origin_is_recorded(self):
        again = self.run_script("sync_skills.py", "--target", self.target)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        for skill in SKILLS:
            self.assertIn(f"same       {skill}", again.stdout)
        self.assertIn("8 skill(s) in sync", again.stdout)
        records = [n for n in os.listdir(self.target) if not os.path.isdir(os.path.join(self.target, n))]
        self.assertEqual(len(records), 1, records)                    # the record of the checkout they came from
        with open(os.path.join(self.target, records[0]), encoding="utf-8") as f:
            record = json.load(f)
        self.assertTrue(os.path.samefile(record["root"], ROOT))

    def test_3_the_preflight_of_every_skill_runs_on_the_synthetic_workspace(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                r = self.run_script("workspace_select.py", "preflight", "--skill", skill, "--workspace", DEMO)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("Configuration: valid for this skill", r.stdout)
                self.assertIn(f"Task: /{skill}", r.stdout)

    def test_4_a_skill_the_workspace_cannot_serve_stops_with_an_instruction(self):
        empty = os.path.join(self.tmp.name, "empty-project")
        os.makedirs(empty)
        r = self.run_script("workspace_select.py", "preflight", "--skill", "plan-tests", "--workspace", empty)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("/project-context", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()

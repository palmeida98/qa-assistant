"""Project skills (.claude/skills): valid and consistent frontmatter.

python -m unittest discover -s tests   (from the root of qa-assistant)
A frontmatter with invalid YAML (e.g. ': ' in the description without quotes) makes
Claude Code show the first line of the body instead of the description.
"""

import os
import re
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, ".claude", "skills")
CLAUDE_MD = os.path.join(ROOT, "CLAUDE.md")


def frontmatter(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        text = f.read().replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        raise AssertionError(f"{path}: no --- frontmatter --- block at the start")
    return yaml.safe_load(m.group(1))


class Skills(unittest.TestCase):
    def names(self):
        return sorted(d for d in os.listdir(SKILLS) if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md")))

    def test_valid_frontmatter_with_name_and_description(self):
        for name in self.names():
            with self.subTest(skill=name):
                fm = frontmatter(os.path.join(SKILLS, name, "SKILL.md"))
                self.assertIsInstance(fm, dict)
                self.assertEqual(fm.get("name"), name)
                self.assertIsInstance(fm.get("description"), str)
                self.assertGreater(len(fm["description"]), 20)

    def test_each_skill_is_in_the_commands_of_claude_md(self):
        with open(CLAUDE_MD, encoding="utf-8-sig") as f:
            commands = f.read()
        for name in self.names():
            with self.subTest(skill=name):
                self.assertIn(f"/{name}", commands)


def skill_text(name: str) -> str:
    with open(os.path.join(SKILLS, name, "SKILL.md"), encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


TRACKER_LINE = re.compile(r"python -m adapters\.tracker <workspace> (\S+)([^\n]*)")


class TrackerSkills(unittest.TestCase):
    """Plan 10.4: the skills drive `python -m adapters.tracker` the way its command line and the techniques say."""

    def test_every_tracker_command_in_the_skills_parses(self):
        from adapters.tracker.cli import parser
        found = set()
        for name in sorted(os.listdir(SKILLS)):
            if not os.path.isfile(os.path.join(SKILLS, name, "SKILL.md")):
                continue
            for command, rest in TRACKER_LINE.findall(skill_text(name)):
                found.add(command)
                argv = ["ws", command] + (["input.json"] if command in ("apply", "export-cases", "bug-search") else [])
                argv += re.findall(r"--[a-z][a-z-]*", rest)
                with self.subTest(skill=name, command=command):
                    try:
                        parser().parse_args(argv)
                    except SystemExit:
                        self.fail(f"{name}: 'python -m adapters.tracker <workspace> {command}{rest}' does not parse")
        self.assertEqual(found, {"check", "import-cases", "export-cases", "apply", "bug-search"})

    def test_the_skills_that_use_each_direction(self):
        expected = {"project-onboarding": ("> check", "> import-cases"), "checkpoint": ("> apply",),
                    "log-bug": ("> bug-search",), "generate-cases": ("> export-cases",),
                    "plan-batch": ("> import-cases", "tracker/cases.json"), "run-batch": ("tracker/cases.json",),
                    "end-session": ("tracker/sent/",)}
        for name, needles in expected.items():
            for needle in needles:
                with self.subTest(skill=name, needle=needle):
                    self.assertIn(needle, skill_text(name))

    def test_generate_cases_adds_excel_cases_through_the_adapter(self):
        # plan 10.6 (technique-excel.md 4.1): the dry run first, never by hand
        text = skill_text("generate-cases")
        m = re.search(r"python -m adapters\.excel\.add_cases <workspace> (\S+)( --dry-run)", text)
        self.assertIsNotNone(m)
        from adapters.excel import add_cases
        with mock.patch.object(add_cases, "add", return_value=0) as add:
            self.assertEqual(add_cases.main(["ws", m.group(1), "--dry-run"]), 0)
        self.assertTrue(add.call_args.args[2])
        for code in ("0 =", "4 =", "2 =", "3 =", "5 ="):
            self.assertIn(code, text[text.index("10. With the excel tracker"):])
        self.assertIn("Never add a case by hand", text)

    def test_writing_commands_dry_run_first_then_yes_after_approval(self):
        for name in ("checkpoint", "generate-cases"):
            text = skill_text(name)
            with self.subTest(skill=name):
                self.assertIn("--yes", text)
                self.assertRegex(text, r"(?i)dry run")
                self.assertRegex(text, r"(?i)approv")
                # the command as written in the skill never sends: --yes only after the approval
                self.assertNotRegex(text, r"adapters\.tracker <workspace> (apply|export-cases) \S+ --yes")

    def test_checkpoint_explains_every_exit_code_of_apply(self):
        text = skill_text("checkpoint")
        block = text[text.index("7a."):text.index("\n8.")]
        for code in range(6):
            with self.subTest(code=code):
                self.assertRegex(block, rf"- {code} = ")
        self.assertIn('"run"', text)

    def test_onboarding_asks_for_blocked_and_na_and_the_process(self):
        text = skill_text("project-onboarding")
        for needle in ("Blocked and N/A", "skip", "Basic", "Agile or Scrum", "results.configurations", "*_var",
                       "technique-jira-xray.md", "technique-azure-devops.md"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_dashboard_says_excel_only_for_the_other_trackers(self):
        text = skill_text("dashboard")
        self.assertIn("jira or azure-devops", text)
        self.assertIn("Excel only", text)

    def test_log_bug_retest_changes_a_case_only_when_its_own_expected_result_was_checked(self):
        # plan 11.2: a real retest turned six Pass cases into Fail by linking maintained bugs to them
        text = skill_text("log-bug")
        for needle in ("changes a CASE only", "own expected result was checked again", '"proposed": true',
                       "scripts/run_log.py"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_scripts_the_skills_run_exist(self):
        for name in sorted(os.listdir(SKILLS)):
            if not os.path.isfile(os.path.join(SKILLS, name, "SKILL.md")):
                continue
            for script in set(re.findall(r"scripts/([a-z_]+\.py)", skill_text(name))):
                with self.subTest(skill=name, script=script):
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", script)))

    def test_referenced_techniques_exist(self):
        for name in sorted(os.listdir(SKILLS)):
            if not os.path.isfile(os.path.join(SKILLS, name, "SKILL.md")):
                continue
            for doc in set(re.findall(r"technique-[a-z-]+\.md", skill_text(name))):
                with self.subTest(skill=name, doc=doc):
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", doc)))


if __name__ == "__main__":
    unittest.main()

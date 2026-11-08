"""Project skills (.claude/skills, version 2: docs/skills-spec.md): valid and consistent frontmatter, contracts that
name only what exists, and the procedures they moved to docs/procedures/.

python -m unittest discover -s tests   (from the root of qa-assistant)
A frontmatter with invalid YAML (e.g. ': ' in the description without quotes) makes
Claude Code show the first line of the body instead of the description.
"""

import os
import re
import shlex
import sys
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import sync_skills

SKILLS = os.path.join(ROOT, ".claude", "skills")
DOCS = os.path.join(ROOT, "docs")
PROCEDURES = os.path.join(DOCS, "procedures")
CONTRACT = ("intent", "inputs", "config", "tools", "outputs", "writes", "approvals", "failures", "resume")
METHODOLOGY = os.path.join(DOCS, "methodology.md")
EIGHT = ["analyze-ticket", "checkpoint", "manage-bugs", "plan-tests", "project-context", "qa-report", "run-tests",
         "test-pbi"]
VERSION_1 = ("project-onboarding", "analyze-tickets", "analyze-feature", "generate-cases", "plan-batch", "run-batch",
             "test-api", "audit-accessibility", "test-mobile", "log-bug", "end-session", "dashboard")


def frontmatter(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        text = f.read().replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        raise AssertionError(f"{path}: no --- frontmatter --- block at the start")
    return yaml.safe_load(m.group(1))


def skill_text(name: str) -> str:
    with open(os.path.join(SKILLS, name, "SKILL.md"), encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


def procedure(name: str) -> str:
    with open(os.path.join(PROCEDURES, name), encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


def flat(text: str) -> str:
    """The text with its line breaks and repeated blanks as one space: a phrase may be wrapped anywhere."""
    return " ".join(text.split())


def names() -> list[str]:
    return sorted(d for d in os.listdir(SKILLS) if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md")))


def all_procedures() -> list[str]:
    return sorted(f for f in os.listdir(PROCEDURES) if f.endswith(".md"))


class Skills(unittest.TestCase):
    def test_the_eight_skills_and_nothing_else(self):
        self.assertEqual(names(), EIGHT)

    def test_valid_frontmatter_with_name_and_description(self):
        for name in names():
            with self.subTest(skill=name):
                fm = frontmatter(os.path.join(SKILLS, name, "SKILL.md"))
                self.assertIsInstance(fm, dict)
                self.assertEqual(fm.get("name"), name)
                self.assertIsInstance(fm.get("description"), str)
                self.assertGreater(len(fm["description"]), 20)

    def test_each_description_says_when_to_use_it_in_natural_language(self):
        # routing is by description (skills-guide.md section 3): each one carries the requests it answers
        for name in names():
            with self.subTest(skill=name):
                self.assertRegex(frontmatter(os.path.join(SKILLS, name, "SKILL.md"))["description"], r"Use (for|when)")

    def test_each_skill_is_in_the_work_cycle_of_the_methodology(self):
        # the methodology's stage table (section 3) is the list of the skills; CLAUDE.md does not repeat it
        with open(METHODOLOGY, encoding="utf-8-sig") as f:
            commands = f.read()
        table = commands[commands.index("| Stage or request | Skill | Result |"):commands.index("The user need not know")]
        for name in names():
            with self.subTest(skill=name):
                self.assertIn(f"`/{name}`", table)
        for old in VERSION_1:
            with self.subTest(retired=old):
                self.assertNotIn(f"/{old}`", table)

    def test_each_skill_has_its_whole_contract_in_the_frontmatter(self):
        for name in names():
            metadata = frontmatter(os.path.join(SKILLS, name, "SKILL.md")).get("metadata") or {}
            for key in CONTRACT:
                with self.subTest(skill=name, key=key):
                    self.assertTrue(str(metadata.get(key) or "").strip())
            for key, value in metadata.items():
                with self.subTest(skill=name, key=key):
                    self.assertIsInstance(value, str)        # a map of plain strings, as the skills format asks

    def test_each_requirement_can_be_checked(self):
        import validate_project
        known_tools = {"git", "python", ".env", "skills", "libreoffice", "node", "qa-audit", "browser", "maestro", "adb",
                       "emulator", "android-driver", "mobile", "xcrun", "ios-wda", "build", "mcp"}
        for name in names():
            doc = sync_skills.skill_doc(os.path.join(SKILLS, name, "SKILL.md"))
            with self.subTest(skill=name):
                self.assertEqual(sync_skills.missing_code(ROOT, doc), [])
                self.assertLessEqual(set(sync_skills.listed(doc, "requires-tools")), known_tools)
                self.assertLessEqual(set(sync_skills.listed(doc, "requires-config")),
                                     {"tickets", "excel", *(s for s, _ in validate_project.MODULES.values() if s)})
                self.assertIn((doc.get("metadata") or {}).get("requires-project", "valid"), ("valid", "any"))

    def test_every_skill_reads_the_shared_rules_and_declares_its_documents(self):
        for name in names():
            doc = sync_skills.skill_doc(os.path.join(SKILLS, name, "SKILL.md"))
            declared = set(sync_skills.listed(doc, "requires-docs"))
            text = skill_text(name)
            with self.subTest(skill=name):
                self.assertIn("procedures/rules.md", declared)
                self.assertIn("<qa>/docs/procedures/rules.md apply", text)
                # every procedure the body names is declared, so a missing one stops the installation
                for named in set(re.findall(r"procedures/[a-z-]+\.md", text)):
                    self.assertIn(named, declared, f"{name} names {named} without requires-docs")

    def test_each_skill_starts_by_finding_the_framework_and_the_project(self):
        import workspace_select
        for name in names():
            text = skill_text(name)
            m = re.search(r"python <qa>/scripts/workspace_select\.py (preflight --skill (\S+)) ", text)
            with self.subTest(skill=name):
                self.assertIsNotNone(m)
                self.assertEqual(m.group(2), name)
                workspace_select.parser().parse_args(m.group(1).split())
                self.assertLess(text.index("preflight"), text.index("\n1. "))
                self.assertLess(text.index("<qa>/docs/"), text.index("\n1. "), "say where the documents are in step 0")

    def test_nothing_in_a_skill_depends_only_on_the_old_relative_path(self):
        for name in names():
            with self.subTest(skill=name):
                self.assertEqual(skill_text(name).count("../../../qa-assistant"), 1)   # the last option, nowhere else

    def test_a_skill_folder_holds_only_its_skill(self):
        # scripts/sync_skills.py copies SKILL.md alone: a helper file here would never reach a workspace
        for name in names():
            with self.subTest(skill=name):
                self.assertEqual(os.listdir(os.path.join(SKILLS, name)), ["SKILL.md"])

    def test_no_skill_or_procedure_sends_to_a_version_1_skill(self):
        for label, text in [(n, skill_text(n)) for n in names()] + [(p, procedure(p)) for p in all_procedures()]:
            body = re.sub(r"\(from /[a-z-]+\)", "", text)       # the headings that say where a procedure came from
            for old in VERSION_1:
                with self.subTest(file=label, old=old):
                    self.assertNotRegex(body, rf"(?<![\w/.-])/{re.escape(old)}(?![\w-])")

    def test_the_scripts_and_documents_named_exist(self):
        for label, text in [(n, skill_text(n)) for n in names()] + [(p, procedure(p)) for p in all_procedures()]:
            for script in set(re.findall(r"scripts/([a-z_]+\.py)", text)):
                with self.subTest(file=label, script=script):
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, "scripts", script)))
            for doc in set(re.findall(r"(?:technique-[a-z-]+|methodology|heuristics|skills-(?:spec|guide))\.md", text)):
                with self.subTest(file=label, doc=doc):
                    self.assertTrue(os.path.isfile(os.path.join(DOCS, doc)))
            for doc in set(re.findall(r"procedures/([a-z-]+\.md)", text)):
                with self.subTest(file=label, doc=doc):
                    self.assertTrue(os.path.isfile(os.path.join(PROCEDURES, doc)))


class Routing(unittest.TestCase):
    def test_run_tests_has_a_route_for_every_module_and_each_exists(self):
        import validate_project
        import workspace_select
        doc = sync_skills.skill_doc(os.path.join(SKILLS, "run-tests", "SKILL.md"))
        table = workspace_select.parse_routes(doc["metadata"]["routes"])
        self.assertEqual(set(table), set(validate_project.MODULES))
        for module, proc in table.items():
            with self.subTest(module=module):
                self.assertTrue(os.path.isfile(os.path.join(DOCS, *proc.split("/"))))
                self.assertIn(proc, sync_skills.listed(doc, "requires-docs"))
        self.assertEqual(doc["metadata"].get("next-check"), "yes")

    def test_run_tests_keeps_app_tool_block_and_inconclusive_apart(self):
        text = flat(skill_text("run-tests") + procedure("rules.md"))
        for needle in ("Blocked (environment)", "Blocked (tool)", "never a defect", "Question", "OBSERVED",
                       "intended", "a new batch"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


class Flow(unittest.TestCase):
    def test_test_pbi_coordinates_the_other_skills_without_copying_them(self):
        text = skill_text("test-pbi")
        for other in EIGHT:
            if other != "test-pbi":
                with self.subTest(skill=other):
                    self.assertIn(f"/{other}", text)
        self.assertNotIn("adapters.excel.apply", text)         # applying is /checkpoint's (procedures/apply.md)
        self.assertNotIn("bug-search", text)                    # the search is /manage-bugs'

    def test_the_helpers_named_in_the_skills_parse(self):
        import pbi_state
        import qa_report
        for name in names():
            for script, mod in (("pbi_state.py", pbi_state), ("qa_report.py", qa_report)):
                for rest in re.findall(rf"python <qa>/scripts/{script} <workspace> ([^\n]+)", skill_text(name)):
                    line = re.sub(r"<[^>]+>", "x", rest.replace("[", " ").replace("]", " ").replace("...", " "))
                    argv = ["ws"] + shlex.split(line.split("|")[0] if "|" in line.split()[0] else line)
                    with self.subTest(skill=name, line=rest):
                        try:
                            mod.parser().parse_args(argv)
                        except SystemExit:
                            self.fail(f"{name}: {script} {rest} does not parse")

    def test_checkpoint_does_not_commit_on_its_own(self):
        text = flat(skill_text("checkpoint"))
        self.assertIn("does not commit or push", text)
        self.assertIn("a past time is history", text)

    def test_project_context_never_turns_a_past_time_into_a_schedule(self):
        text = flat(skill_text("project-context"))
        self.assertIn("never turn it into a schedule", text)
        self.assertIn("configured, not verified", text)

    def test_analyze_ticket_keeps_four_lists_and_never_concludes_from_a_partial_read(self):
        text = flat(skill_text("analyze-ticket"))
        for needle in ("content read", "only listed", "inaccessible", "out of scope", "first analysis",
                       "never proves that something was removed"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_manage_bugs_one_bug_for_both_platforms_and_similar_is_not_same(self):
        text = flat(skill_text("manage-bugs"))
        for needle in ("one bug with the platforms", "Similar symptoms do not prove the same defect",
                       "Present all items together", "pbi_state.py published"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_qa_report_never_adds_platforms(self):
        text = flat(skill_text("qa-report"))
        for needle in ("denominator", "never added into one figure", "historical", "retests", "coverage still missing"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


class Autonomy(unittest.TestCase):
    """The skills configuration of 08.11.2026 (methodology v2.13): a request to test authorises its own scope; the
    project's own rules win where they ask for more; writes to a workbook or a tracker keep their approval; evidence
    by risk; one bug per problem."""

    def rules(self) -> str:
        return flat(procedure("rules.md"))

    def test_a_request_to_test_needs_no_second_approval_of_its_plan(self):
        rules = self.rules()
        self.assertIn("The request itself: a plan made for it is shown and the run follows, without a second approval "
                      "of the same plan or run", rules)
        plan = flat(skill_text("plan-tests"))
        step7 = plan[plan.index("7. Save the analysis"):]
        go_on = step7.index("go on with /run-tests, without asking to approve the same plan again")
        stop = step7.index("STOP")
        self.assertLess(go_on, stop)                       # the STOP is the second branch, not the default
        self.assertIn("a workspace whose rules ask for a confirmation", step7[go_on:stop + 5])
        pbi = flat(skill_text("test-pbi"))
        self.assertNotIn("STOP for the user's approval. Once approved", pbi)
        self.assertIn("The request to test the PBI covers the plan", pbi)
        run = flat(skill_text("run-tests"))
        self.assertIn("the run follows the plan without asking to approve it again", run)
        self.assertNotIn("after the user approves it for the platform named", plan)

    def test_a_request_to_plan_or_to_analyse_runs_nothing(self):
        self.assertIn('"plan the tests of X" stops at the plan', self.rules())
        self.assertIn("a request to plan only", flat(skill_text("plan-tests")))
        self.assertIn("a request to analyse only stops here and runs nothing", flat(skill_text("analyze-ticket")))

    def test_the_project_rules_win_where_they_ask_for_more_and_are_named(self):
        rules = self.rules()
        section = rules[rules.index("## 8. Defaults and the project's own rules"):]
        for needle in ("and what it imports", "an approval of each platform's run", "the project's rule applies",
                       "says which rule it followed", "never removes the approval of a write",
                       "never applied to another"):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)
        self.assertIn("each rule of the workspace's CLAUDE.md, or of a file it imports, that asks for more",
                      flat(skill_text("project-context")))
        for name in ("plan-tests", "run-tests", "test-pbi"):
            with self.subTest(skill=name):
                self.assertRegex(flat(skill_text(name)), r"CLAUDE\.md asks for (more|a confirmation|that approval|one)")

    def test_writes_to_a_workbook_or_tracker_wait_for_their_approval(self):
        rules = self.rules()
        self.assertIn("Every write to a workbook or a tracker (cases, results, bugs, comments, attachments) shows its "
                      "exact content first, applies only the set approved, and is read back", rules)
        self.assertIn("The autonomy of the tests never covers it, nor a commit, a push or a sync of the skills", rules)
        checkpoint = flat(skill_text("checkpoint"))
        self.assertIn("the user confirms the batch after the dry run", checkpoint)
        self.assertIn("does not commit or push", checkpoint)
        self.assertIn("approval before they enter the library", flat(skill_text("plan-tests")))

    def test_a_valid_approval_is_reused_and_a_stale_one_is_asked_again(self):
        rules = self.rules()
        self.assertIn("It is reused, in this skill or the next, while that same set and its preview are still valid",
                      rules)
        self.assertIn("a changed set or a stale preview is asked again", rules)
        self.assertIn("An approval still valid (§5) is not asked again on resuming", rules)
        self.assertIn("an approval of that same preview, still valid, is not asked a second time",
                      flat(skill_text("checkpoint")))

    def test_resuming_keeps_what_was_recorded(self):
        rules = self.rules()
        self.assertIn("keeps its status, its observed text, its time, its evidence and the run block "
                      "(`run_log.py attach`)", rules)
        bugs = flat(skill_text("manage-bugs"))
        self.assertIn("python <qa>/scripts/run_log.py <workspace> attach <batch> <case> --new-defect", bugs)
        self.assertNotIn("add <batch> <case> Falhou|Fail", bugs)       # the replace that dropped the evidence
        self.assertIn("Never `add --replace` only to link a bug", flat(procedure("bugs.md")))

    def test_bugs_are_grouped_only_on_evidence_and_differences_stay_apart(self):
        bugs = flat(skill_text("manage-bugs"))
        for needle in ("One bug per problem", "The same defect shown by the evidence",
                       "separate bugs, related when a link is known", "A link not shown stays uncertain",
                       "A platform not tested is never named",
                       "needs no root cause", "A tool failure, a block or inconclusive evidence is not a bug"):
            with self.subTest(needle=needle):
                self.assertIn(needle, bugs)

    def test_a_tool_block_is_never_a_defect_and_evidence_is_by_risk(self):
        rules = self.rules()
        for needle in ("A failed capture or an automation timeout is never a defect of the application on its own",
                       "a capture of the app is not required when it does not show the block",
                       "one artefact may cover several cases", "the part that is its own (a timestamp, an interval",
                       "no video of every case by default", "every file named exists",
                       "A platform, build or environment not tested is never said to be affected or validated"):
            with self.subTest(needle=needle):
                self.assertIn(needle, rules)

    def test_the_preflight_checks_even_when_the_paths_are_given(self):
        for name in names():
            text = flat(skill_text(name))
            with self.subTest(skill=name):
                self.assertNotIn("Preflight, unless the prompt of an unattended run already names", text)
                self.assertIn("a launcher that already validated the workspace", text)
                self.assertIn("a check that cannot run leaves its cases Blocked, never assumed", text)

    def test_the_report_names_every_file_it_writes(self):
        metadata = frontmatter(os.path.join(SKILLS, "qa-report", "SKILL.md"))["metadata"]
        self.assertIn("dashboard.html", metadata["writes"])
        self.assertIn("never saves it", metadata["writes"])
        self.assertIn("Never a workbook", metadata["writes"])
        self.assertIn("/checkpoint", metadata["approvals"])


TRACKER_LINE = re.compile(r"python -m adapters\.tracker <workspace> (\S+)([^\n]*)")


class Procedures(unittest.TestCase):
    """The rules of version 1 (plan 10.4 and later) live on in docs/procedures/: the same checks, on their new home."""

    def test_every_tracker_command_parses(self):
        from adapters.tracker.cli import parser
        found = set()
        for name in all_procedures():
            for command, rest in TRACKER_LINE.findall(procedure(name)):
                found.add(command)
                argv = ["ws", command] + (["input.json"] if command in ("apply", "export-cases", "bug-search") else [])
                argv += re.findall(r"--[a-z][a-z-]*", rest)
                with self.subTest(procedure=name, command=command):
                    try:
                        parser().parse_args(argv)
                    except SystemExit:
                        self.fail(f"{name}: 'python -m adapters.tracker <workspace> {command}{rest}' does not parse")
        self.assertEqual(found, {"check", "import-cases", "export-cases", "apply", "bug-search"})

    def test_the_procedures_that_use_each_direction(self):
        expected = {"onboarding.md": ("> check", "> import-cases"), "apply.md": ("> apply",),
                    "bugs.md": ("> bug-search",), "planning.md": ("> export-cases", "> import-cases",
                                                                  "tracker/cases.json"),
                    "run-web.md": ("tracker/cases.json",), "report.md": ("tracker/sent/",)}
        for name, needles in expected.items():
            for needle in needles:
                with self.subTest(procedure=name, needle=needle):
                    self.assertIn(needle, procedure(name))

    def test_planning_adds_excel_cases_through_the_adapter(self):
        # plan 10.6 (technique-excel.md 4.1): the dry run first, never by hand
        text = procedure("planning.md")
        m = re.search(r"python -m adapters\.excel\.add_cases <workspace> (\S+)( --dry-run)", text)
        self.assertIsNotNone(m)
        from adapters.excel import add_cases
        with mock.patch.object(add_cases, "add", return_value=0) as add:
            self.assertEqual(add_cases.main(["ws", m.group(1), "--dry-run"]), 0)
        self.assertTrue(add.call_args.args[2])
        for code in ("0 =", "4 =", "2 =", "3 =", "5 ="):
            self.assertIn(code, text[text.index("10. With the excel tracker"):])
        self.assertIn("Never add a case by hand", text)

    def test_an_analysis_outlives_the_session_and_stays_a_proposal(self):
        # audit finding S-03: case design read the analysis from the conversation
        text = procedure("planning.md")
        for word in ("analysis/", "Date:", "Sources:", "Status:", "Open questions", "never a requirement"):
            self.assertIn(word, text)
        self.assertIn("analysis/", skill_text("plan-tests"))

    def test_writing_commands_dry_run_first_then_yes_after_approval(self):
        for name in ("apply.md", "planning.md"):
            text = procedure(name)
            with self.subTest(procedure=name):
                self.assertIn("--yes", text)
                self.assertRegex(text, r"(?i)dry run")
                self.assertRegex(text, r"(?i)approv")
                self.assertNotRegex(text, r"adapters\.tracker <workspace> (apply|export-cases) \S+ --yes")

    def test_apply_explains_every_exit_code(self):
        text = procedure("apply.md")
        block = text[text.index("7a."):text.index("\n8.")]
        for code in range(6):
            with self.subTest(code=code):
                self.assertRegex(block, rf"- {code} = ")
        self.assertIn('"run"', text)

    def test_onboarding_asks_for_blocked_and_na_and_the_process(self):
        text = procedure("onboarding.md")
        for needle in ("Blocked and N/A", "skip", "Basic", "Agile or Scrum", "results.configurations", "*_var",
                       "technique-jira-xray.md", "technique-azure-devops.md"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_the_dashboard_says_excel_only_for_the_other_trackers(self):
        text = procedure("report.md")
        self.assertIn("jira or azure-devops", text)
        self.assertIn("Excel only", text)

    def test_bugs_retest_changes_a_case_only_when_its_own_expected_result_was_checked(self):
        # plan 11.2: a real retest turned six Pass cases into Fail by linking maintained bugs to them
        text = procedure("bugs.md")
        for needle in ("changes a CASE only", "own expected result was checked again", '"proposed": true',
                       "scripts/run_log.py"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_run_mobile_names_the_one_check_of_the_target(self):
        self.assertIn("device.py <workspace> check", procedure("run-mobile.md"))


if __name__ == "__main__":
    unittest.main()

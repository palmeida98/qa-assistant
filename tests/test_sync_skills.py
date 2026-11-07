"""scripts/sync_skills.py: the global copy of the skills (~/.claude/skills),
which is the one that runs in the workspaces, must equal the repository's.

python -m unittest discover -s tests   (from the root of qa-assistant)
Uses a temporary folder as the target: never touches the real ~/.claude/skills.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from typing import ClassVar
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import sync_skills as ss


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = ss.main(["--target", self.target, *args])
        return rc, out.getvalue()

    def test_empty_target_is_out_of_sync_without_writing(self):
        rc, out = self.run_it()
        self.assertEqual(rc, 1)
        self.assertIn("missing", out)
        self.assertEqual(os.listdir(self.target), [])

    def test_apply_copies_and_then_it_is_the_same(self):
        self.assertEqual(self.run_it("--apply")[0], 0)
        state = ss.compare(ss.SOURCE, self.target)
        self.assertTrue(state)
        self.assertEqual(set(state.values()), {"same"})
        self.assertEqual(self.run_it()[0], 0)

    def test_only_different_line_endings_count_as_the_same(self):
        self.run_it("--apply")
        name = min(ss.compare(ss.SOURCE, self.target))
        path = os.path.join(self.target, name, "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read().replace("\r\n", "\n")
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)
        self.assertEqual(ss.compare(ss.SOURCE, self.target)[name], "same")

    def test_an_edited_copy_is_detected_and_restored(self):
        self.run_it("--apply")
        name = min(ss.compare(ss.SOURCE, self.target))
        path = os.path.join(self.target, name, "SKILL.md")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nextra line\n")
        rc, out = self.run_it()
        self.assertEqual(rc, 1)
        self.assertIn(f"different  {name}", out)
        self.assertEqual(self.run_it("--apply")[0], 0)
        self.assertEqual(ss.compare(ss.SOURCE, self.target)[name], "same")

    def test_global_skills_from_other_sources_are_ignored(self):
        os.makedirs(os.path.join(self.target, "something-else"))
        with open(os.path.join(self.target, "something-else", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: something-else\n---\n")
        self.run_it("--apply")
        self.assertNotIn("something-else", ss.compare(ss.SOURCE, self.target))
        self.assertTrue(os.path.isfile(os.path.join(self.target, "something-else", "SKILL.md")))

    def test_the_old_names_of_the_framework_skills_are_removed(self):
        for name in ("executar-lote", "painel"):
            os.makedirs(os.path.join(self.target, name))
            with open(os.path.join(self.target, name, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(f"---\nname: {name}\n---\n")
        # only what this framework's record says it installed is retired (audit 06.11.2026)
        with open(os.path.join(self.target, ss.RECORD), "w", encoding="utf-8") as f:
            json.dump({"skills": {"executar-lote": "x", "painel": "y"}}, f)
        rc, out = self.run_it()
        self.assertEqual(rc, 1)
        self.assertIn("retired    executar-lote", out)
        self.assertEqual(self.run_it("--apply")[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.target, "executar-lote")))
        self.assertFalse(os.path.exists(os.path.join(self.target, "painel")))

    def test_apply_records_the_checkout_the_copies_came_from(self):
        self.assertIsNone(ss.read_record(self.target))
        rc, out = self.run_it("--apply")
        self.assertEqual(rc, 0)
        record = ss.read_record(self.target)
        self.assertTrue(ss.same_path(record["root"], ROOT))
        self.assertEqual(set(record["skills"]), set(ss.compare(ss.SOURCE, self.target)))
        self.assertIn("synced_at", record)
        self.assertIn("recorded as their source", out)
        self.assertNotIn(".qa-assistant-source.json", ss.compare(ss.SOURCE, self.target))

    def test_only_the_skills_are_copied(self):
        self.run_it("--apply")
        copied = sorted(os.listdir(os.path.join(self.target, "analyze-ticket")))
        self.assertEqual(copied, ["SKILL.md"])

    def test_a_copy_already_the_same_still_gets_its_record(self):
        self.run_it("--apply")
        os.remove(os.path.join(self.target, ss.RECORD))
        self.assertEqual(self.run_it("--apply")[0], 0)
        self.assertIsNotNone(ss.read_record(self.target))

    def test_checking_only_names_a_record_of_another_checkout_and_writes_nothing(self):
        self.run_it("--apply")
        ss.write_record(self.target, {"root": os.path.join(self.tmp.name, "other"), "branch": "feature", "commit": "abc"})
        before = os.path.getmtime(os.path.join(self.target, ss.RECORD))
        rc, out = self.run_it()
        self.assertEqual(rc, 0)
        self.assertIn("last synced from", out)
        self.assertIn("feature", out)
        self.assertEqual(os.path.getmtime(os.path.join(self.target, ss.RECORD)), before)

    def test_a_worktree_or_another_branch_is_said_aloud(self):
        with mock.patch("common.git_state", return_value={"commit": "abc", "branch": "feature", "dirty": False}):
            rc, out = self.run_it("--apply")
        self.assertEqual(rc, 0)
        self.assertIn("nothing was merged", out)
        with mock.patch("common.git_state", return_value={"commit": "abc", "branch": "main", "dirty": False}), \
                mock.patch("common.main_checkout", return_value=ss.ROOT):
            rc, out = self.run_it("--apply")
        self.assertNotIn("nothing was merged", out)

    def test_a_skill_whose_code_is_not_in_this_checkout_is_refused(self):
        with tempfile.TemporaryDirectory() as src:
            os.makedirs(os.path.join(src, "needs-code"))
            with open(os.path.join(src, "needs-code", "SKILL.md"), "w", encoding="utf-8") as f:
                f.write('---\nname: needs-code\nmetadata:\n  requires-modules: "adapters.nothing"\n'
                        '  requires-scripts: "scripts/nothing.py"\n---\nbody\n')
            err = io.StringIO()
            with mock.patch.object(ss, "SOURCE", src), contextlib.redirect_stderr(err):
                rc, _ = self.run_it("--apply")
        self.assertEqual(rc, 2)
        self.assertIn("module adapters.nothing", err.getvalue())
        self.assertIn("script scripts/nothing.py", err.getvalue())
        self.assertEqual(os.listdir(self.target), [])

    def test_the_requirements_of_this_checkout_exist(self):
        for name in os.listdir(ss.SOURCE):
            path = os.path.join(ss.SOURCE, name, "SKILL.md")
            if os.path.isfile(path):
                with self.subTest(skill=name):
                    self.assertEqual(ss.missing_code(ROOT, ss.skill_doc(path)), [])

    def test_a_module_is_looked_at_as_files(self):
        self.assertTrue(ss.module_exists(ROOT, "adapters.tickets"))
        self.assertTrue(ss.module_exists(ROOT, "adapters.tickets.memory"))
        self.assertTrue(ss.module_exists(ROOT, "qa_audit"))
        self.assertFalse(ss.module_exists(ROOT, "adapters.nothing"))

    def test_the_record_is_json_kept_whole(self):
        ss.write_record(self.target, {"root": "x"})
        with open(os.path.join(self.target, ss.RECORD), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"root": "x"})
        self.assertFalse(os.path.exists(os.path.join(self.target, ss.RECORD + ".tmp")))


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class Version2(unittest.TestCase):
    """Skills version 2 (docs/skills-spec.md): the eight skills installed, repeatably, with their origin."""
    EIGHT: ClassVar[set] = {"project-context", "analyze-ticket", "plan-tests", "run-tests", "manage-bugs", "qa-report", "checkpoint",
             "test-pbi"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            rc = ss.main(["--target", self.target, *args])
        return rc, out.getvalue()

    def test_the_eight_are_installed_and_a_second_run_changes_nothing(self):
        self.assertEqual(self.run_it("--apply")[0], 0)
        installed = {n for n in os.listdir(self.target) if os.path.isfile(os.path.join(self.target, n, "SKILL.md"))}
        self.assertEqual(installed, self.EIGHT)
        first = {n: read(os.path.join(self.target, n, "SKILL.md")) for n in installed}
        rc, out = self.run_it("--apply")
        self.assertEqual(rc, 0)
        self.assertIn("in sync", out)
        self.assertEqual(first, {n: read(os.path.join(self.target, n, "SKILL.md")) for n in installed})

    def test_version_1_copies_are_retired_and_other_origins_kept(self):
        for name in ("test-mobile", "log-bug", "dashboard", "my-own-skill"):
            os.makedirs(os.path.join(self.target, name))
            with open(os.path.join(self.target, name, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(f"---\nname: {name}\n---\n")
        with open(os.path.join(self.target, ss.RECORD), "w", encoding="utf-8") as f:
            json.dump({"skills": {"test-mobile": "x", "log-bug": "y"}}, f)        # installed by this framework
        self.assertEqual(self.run_it("--apply")[0], 0)
        left = set(os.listdir(self.target))
        self.assertFalse({"test-mobile", "log-bug"} & left)
        self.assertIn("my-own-skill", left)                         # a skill of another origin is never touched
        self.assertIn("dashboard", left)    # an old name the record does not list belongs to another origin: kept

    def test_a_linked_skill_is_never_written_through_nor_removed(self):
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(elsewhere)
        with open(os.path.join(elsewhere, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: theirs\n---\n")
        os.symlink(elsewhere, os.path.join(self.target, "run-tests"))
        os.symlink(elsewhere, os.path.join(self.target, "dashboard"))
        with open(os.path.join(self.target, ss.RECORD), "w", encoding="utf-8") as f:
            json.dump({"skills": {"dashboard": "x"}}, f)
        rc, out = self.run_it("--apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("linked", out)
        self.assertEqual(read(os.path.join(elsewhere, "SKILL.md")), "---\nname: theirs\n---\n")
        self.assertTrue(os.path.islink(os.path.join(self.target, "dashboard")))

    def test_the_record_names_origin_commit_and_what_is_not_committed(self):
        with mock.patch.object(ss, "uncommitted", return_value=[".claude/skills/run-tests/SKILL.md", "docs/x.md"]):
            rc, out = self.run_it("--apply")
        self.assertEqual(rc, 0)
        record = ss.read_record(self.target)
        self.assertEqual(set(record["skills"]), self.EIGHT)
        for key in ("root", "branch", "commit", "dirty", "synced_at"):
            self.assertIn(key, record)
        self.assertEqual(record["uncommitted_skills"], ["run-tests"])
        self.assertIn("PLUS 2 file(s) not committed", out)          # the commit is never shown as the whole content
        with mock.patch.object(ss, "uncommitted", return_value=None):
            _, out = self.run_it("--apply")
        self.assertIn("git could not say", out)

    def test_a_missing_document_refuses_the_installation(self):
        with tempfile.TemporaryDirectory() as src:
            os.makedirs(os.path.join(src, "x"))
            with open(os.path.join(src, "x", "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: x\ndescription: d\nmetadata:\n  requires-docs: \"procedures/nothing.md\"\n---\n")
            err = io.StringIO()
            with mock.patch.object(ss, "SOURCE", src), contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = ss.main(["--target", self.target, "--apply"])
        self.assertEqual(rc, 2)
        self.assertIn("document docs/procedures/nothing.md", err.getvalue())
        self.assertEqual(os.listdir(self.target), [])

    def test_no_skills_in_the_checkout_is_a_clear_error(self):
        err = io.StringIO()
        with mock.patch.object(ss, "SOURCE", os.path.join(self.target, "nowhere")), contextlib.redirect_stderr(err):
            self.assertEqual(ss.main(["--target", self.target]), 2)
        self.assertIn("has no skills", err.getvalue())

    def test_paths_are_built_for_any_system(self):
        # Mac and Windows: the record and the copies use os.path, never a hard-coded separator
        with open(ss.__file__, encoding="utf-8") as f:
            code = f.read()
        self.assertNotIn("'/'.join", code)
        self.assertNotIn("\"/\".join", code)


if __name__ == "__main__":
    unittest.main()

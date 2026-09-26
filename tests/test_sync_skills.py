"""scripts/sync_skills.py: the global copy of the skills (~/.claude/skills),
which is the one that runs in the workspaces, must equal the repository's.

python -m unittest discover -s tests   (from the root of qa-assistant)
Uses a temporary folder as the target: never touches the real ~/.claude/skills.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

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
        rc, out = self.run_it()
        self.assertEqual(rc, 1)
        self.assertIn("retired    executar-lote", out)
        self.assertEqual(self.run_it("--apply")[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.target, "executar-lote")))
        self.assertFalse(os.path.exists(os.path.join(self.target, "painel")))


if __name__ == "__main__":
    unittest.main()

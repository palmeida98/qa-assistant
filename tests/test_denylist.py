"""Privacy guard scripts/check_denylist.py: text, file names and Office files
(ZIP) -- a client workbook must not pass unseen.

python -m unittest discover -s tests   (from the root of qa-assistant)
Uses a fictional denylist through QA_DENYLIST; never reads the personal list.
"""

import os
import subprocess
import sys
import tempfile
import unittest

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_denylist.py")
TERM = "fictionalcompanyxyz"


class ListCase(unittest.TestCase):
    """A fictional list in a temporary folder and the helpers to run the guard on it; no tests of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.list = os.path.join(self.tmp.name, "denylist.txt")
        with open(self.list, "w", encoding="utf-8") as f:
            f.write(f"# comment\n{TERM.upper()}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, **extra):
        """The owner's own settings never reach a test: no QA_DENYLIST_REQUIRE, no global git configuration."""
        env = {k: v for k, v in os.environ.items() if k != "QA_DENYLIST_REQUIRE"}
        empty = os.path.join(self.tmp.name, "gitconfig-empty")
        open(empty, "w").close()
        env.update({"QA_DENYLIST": self.list, "GIT_CONFIG_GLOBAL": empty, "GIT_CONFIG_NOSYSTEM": "1"})
        env.update(extra)
        return env

    def run_it(self, *files, **extra_env):
        return subprocess.run([sys.executable, SCRIPT, *files], env=self.env(**extra_env),
                              capture_output=True, text=True, check=False)

    def file(self, name, text=""):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def xlsx(self, text):
        path = os.path.join(self.tmp.name, "plan.xlsx")
        wb = openpyxl.Workbook()
        wb.active["B7"] = text
        wb.save(path)
        return path


class Denylist(ListCase):
    def test_clean_text_passes(self):
        self.assertEqual(self.run_it(self.file("a.md", "nothing here")).returncode, 0)

    def test_term_in_the_text_blocks(self):
        r = self.run_it(self.file("a.md", f"client {TERM} x"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("BLOCKED", r.stdout)

    def test_term_in_the_file_name_blocks(self):
        self.assertEqual(self.run_it(self.file(f"plan-{TERM}.md", "clean")).returncode, 1)

    def test_term_inside_an_xlsx_blocks(self):
        self.assertEqual(self.run_it(self.xlsx(f"Portal {TERM} - login")).returncode, 1)

    def test_clean_xlsx_passes(self):
        self.assertEqual(self.run_it(self.xlsx("Demo Shop")).returncode, 0)

    def test_commit_message(self):
        # the commit-msg hook passes the message file (.git/COMMIT_EDITMSG) to the same script
        msg = os.path.join(self.tmp.name, "COMMIT_EDITMSG")
        with open(msg, "w", encoding="utf-8") as f:
            f.write(f"8.99: example\n\nvalidated in the workspace {TERM.upper()}\n")
        env = self.env()
        r = subprocess.run([sys.executable, SCRIPT, msg], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(r.returncode, 1, r.stdout)
        with open(msg, "w", encoding="utf-8") as f:
            f.write("8.99: example without names\n")
        r = subprocess.run([sys.executable, SCRIPT, msg], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(r.returncode, 0, r.stdout)


    def test_without_a_list_it_says_nothing_was_checked(self):
        env = self.env(QA_DENYLIST=os.path.join(self.tmp.name, "missing.txt"))
        path = self.file("notes.txt", f"about {TERM}")
        r = subprocess.run([sys.executable, SCRIPT, path], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0)                       # a commit without a list still goes through
        self.assertIn("NOT CONFIGURED", r.stderr)
        self.assertIn("were NOT checked", r.stderr)
        self.assertNotIn("clean", r.stdout)
        r = subprocess.run([sys.executable, SCRIPT, "--require", path], env=env, capture_output=True, text=True,
                           check=False)
        self.assertEqual(r.returncode, 3)                       # a validation never reports it as passed

    def test_a_clean_run_says_what_it_checked(self):
        r = self.run_it("--require", self.file("ok.txt", "nothing here"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("1 term(s) checked in 1 file(s): clean", r.stdout)
        r = self.run_it("--require", self.file("bad.txt", f"made for {TERM}"))
        self.assertEqual(r.returncode, 1)

    # --- the guard never calls clean what it did not check, and never prints the terms -----------------------------

    def test_the_log_never_carries_the_term(self):
        r = self.run_it(self.file("a.md", f"client {TERM} x"), self.file(f"b-{TERM}.md", "clean"))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(TERM, r.stdout.lower() + r.stderr.lower())
        self.assertIn("line 2 of the list", r.stdout)           # the line is enough for the owner to find the term

    def test_a_list_without_terms_is_not_configured_and_says_which_kind(self):
        for content, kind in (("", "empty"), ("# only a comment\n\n   \n", "empty")):
            with open(self.list, "w", encoding="utf-8") as f:
                f.write(content)
            r = self.run_it(self.file("n.txt", f"about {TERM}"))
            self.assertEqual(r.returncode, 0)
            self.assertIn(f"the list is {kind}", r.stderr)
            self.assertNotIn("clean", r.stdout)
            self.assertEqual(self.run_it("--require", self.file("n.txt", "x")).returncode, 3)
        r = self.run_it(self.file("n.txt", "x"), QA_DENYLIST=os.path.join(self.tmp.name, "gone.txt"))
        self.assertIn("the list is missing", r.stderr)

    def test_required_by_the_environment_or_the_owners_git_setting(self):
        missing = os.path.join(self.tmp.name, "gone.txt")
        path = self.file("n.txt", "x")
        self.assertEqual(self.run_it(path, QA_DENYLIST=missing).returncode, 0)
        r = self.run_it(path, QA_DENYLIST=missing, QA_DENYLIST_REQUIRE="1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("required here", r.stderr)
        cfg = os.path.join(self.tmp.name, "gitconfig-owner")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("[qa-assistant]\n\tdenylistRequired = true\n")
        self.assertEqual(self.run_it(path, QA_DENYLIST=missing, GIT_CONFIG_GLOBAL=cfg).returncode, 3)
        # with a list that works, the setting changes nothing
        self.assertEqual(self.run_it(path, GIT_CONFIG_GLOBAL=cfg).returncode, 0)

    def test_an_unreadable_list_fails_even_when_not_required(self):
        with open(self.list, "wb") as f:
            f.write(b"\xff\xfe\x00bad bytes \x80\x81\n")
        r = self.run_it(self.file("n.txt", f"about {TERM}"))
        self.assertEqual(r.returncode, 4)
        self.assertIn("UNREADABLE", r.stderr)
        self.assertIn("NOT checked", r.stderr)

    def test_a_corrupt_or_encrypted_office_file_is_not_called_clean(self):
        bad = os.path.join(self.tmp.name, "broken.xlsx")
        with open(bad, "wb") as f:
            f.write(b"not a zip at all")
        r = self.run_it(bad)
        self.assertEqual(r.returncode, 4)
        self.assertIn("NOT VERIFIED", r.stderr)
        self.assertNotIn("clean", r.stdout)
        # the other files of the same call are still checked, and a match still wins with exit 1
        r = self.run_it(bad, self.file("a.md", f"client {TERM}"))
        self.assertEqual(r.returncode, 1)

    def test_an_unreadable_file_is_not_called_clean(self):
        path = self.file("secret.md", "x")
        os.chmod(path, 0)
        try:
            if os.access(path, os.R_OK):
                self.skipTest("this account reads files with no permissions")
            r = self.run_it(path)
        finally:
            os.chmod(path, 0o600)
        self.assertEqual(r.returncode, 4)
        self.assertIn("NOT VERIFIED", r.stderr)

    def test_formats_it_cannot_read_have_the_name_checked_and_are_listed(self):
        pdf = os.path.join(self.tmp.name, "report.pdf")
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.4 compressed streams")
        r = self.run_it(pdf)
        self.assertEqual(r.returncode, 0)
        self.assertIn("content NOT inspected", r.stderr)
        self.assertIn("1 name-only", r.stdout)
        named = os.path.join(self.tmp.name, f"{TERM}.pdf")
        with open(named, "wb") as f:
            f.write(b"%PDF")
        self.assertEqual(self.run_it(named).returncode, 1)

    def test_paths_that_are_not_regular_files_are_listed_as_skipped(self):
        r = self.run_it(self.file("ok.md", "x"), os.path.join(self.tmp.name, "does-not-exist.md"), self.tmp.name)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.count("skipped:"), 2)
        self.assertIn("2 skipped", r.stdout)


WORD = "shopx"          # fictional short name that is also the start of longer words


class WholeWord(ListCase):
    """`word:` entries: a match only where no letter or digit touches the term; hyphen and underscore separate."""

    def setUp(self):
        super().setUp()
        with open(self.list, "w", encoding="utf-8") as f:
            f.write(f"# comment\n{TERM}\nword:{WORD.upper()}\n")

    def blocked(self, text, name="a.md"):
        return self.run_it(self.file(name, text)).returncode == 1

    def test_case_and_punctuation_around_the_term(self):
        for text in (WORD, WORD.upper(), "Shopx", f"project {WORD}.", f"({WORD})", f"«{WORD}»", f'"{WORD}"',
                     f"{WORD}, next", f"{WORD}:", f"id: {WORD}\n", f"<t>{WORD}</t>", f"x\t{WORD}\ty"):
            self.assertTrue(self.blocked(text), text)

    def test_hyphen_underscore_slash_and_dot_are_separators(self):
        for text in (f"{WORD}-sim-q1", f"qa-{WORD}", f"{WORD}_sim", f"KEY_{WORD.upper()}", f"qa_{WORD}_id",
                     f"acme/{WORD}/runs", f"{WORD}.yaml", f"__{WORD}__"):
            self.assertTrue(self.blocked(text), text)

    def test_inside_a_longer_word_or_identifier_it_does_not_match(self):
        for text in (f"{WORD}es", f"big{WORD}", f"{WORD}2", f"v2{WORD}", f"KEY_{WORD.upper()}Y", f"my_{WORD}ing_id",
                     f"pre-{WORD}ed", "shop x", "shop-x", "shop_x"):
            self.assertFalse(self.blocked(text), text)

    def test_letters_with_accents_are_letters(self):
        self.assertFalse(self.blocked(f"{WORD}é"))
        self.assertFalse(self.blocked(f"ã{WORD}"))

    def test_file_names_office_files_and_commit_messages(self):
        self.assertEqual(self.run_it(self.file(f"plan-{WORD}.md", "clean")).returncode, 1)
        self.assertEqual(self.run_it(self.file(f"plan-{WORD}s.md", "clean")).returncode, 0)
        self.assertEqual(self.run_it(self.xlsx(f"Portal {WORD.upper()} - login")).returncode, 1)
        self.assertEqual(self.run_it(self.xlsx(f"Portal {WORD}ing")).returncode, 0)
        msg = self.file("COMMIT_EDITMSG", f"9.1: example\n\nvalidated in {WORD.upper()}\n")
        self.assertEqual(self.run_it(msg).returncode, 1)

    def test_plain_entries_keep_matching_inside_words(self):
        self.assertTrue(self.blocked(f"x{TERM}y"))
        self.assertTrue(self.blocked(f"KEY_{TERM.upper()}_2"))

    def test_the_log_never_carries_the_term_and_names_its_line(self):
        r = self.run_it(self.file("a.md", f"client {WORD}"), self.file(f"b-{WORD}-c.md", "clean"))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(WORD, r.stdout.lower() + r.stderr.lower())
        self.assertIn("line 3 of the list", r.stdout)
        self.assertIn("b-<term 3>-c.md", r.stdout)

    def test_a_term_with_spaces_and_extra_spaces_after_the_prefix(self):
        with open(self.list, "w", encoding="utf-8") as f:
            f.write(f"word:   {WORD} ledger  \n")
        self.assertTrue(self.blocked(f"The {WORD.title()} Ledger."))
        self.assertFalse(self.blocked(f"{WORD} ledgers"))
        self.assertFalse(self.blocked(f"{WORD}\nledger"))

    def test_a_whole_word_entry_without_a_term_is_a_broken_list(self):
        with open(self.list, "w", encoding="utf-8") as f:
            f.write(f"{TERM}\nword:   \n")
        r = self.run_it(self.file("n.txt", "x"))
        self.assertEqual(r.returncode, 4)
        self.assertIn("UNREADABLE", r.stderr)
        self.assertIn("line 2", r.stderr)

    def test_a_clean_run_counts_both_kinds(self):
        r = self.run_it("--require", self.file("ok.txt", f"{WORD}s and big{WORD}"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("2 term(s) checked in 1 file(s): clean", r.stdout)


if __name__ == "__main__":
    unittest.main()

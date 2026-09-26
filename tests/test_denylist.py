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


class Denylist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.list = os.path.join(self.tmp.name, "denylist.txt")
        with open(self.list, "w", encoding="utf-8") as f:
            f.write(f"# comment\n{TERM.upper()}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, *files):
        env = {**os.environ, "QA_DENYLIST": self.list}
        return subprocess.run([sys.executable, SCRIPT, *files], env=env,
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
        env = dict(os.environ, QA_DENYLIST=self.list)
        r = subprocess.run([sys.executable, SCRIPT, msg], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(r.returncode, 1, r.stdout)
        with open(msg, "w", encoding="utf-8") as f:
            f.write("8.99: example without names\n")
        r = subprocess.run([sys.executable, SCRIPT, msg], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()

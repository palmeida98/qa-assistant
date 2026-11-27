"""scripts/archive_check.py: lists what _arquivo/ holds without a reason; never deletes."""
import contextlib
import datetime
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import archive_check as ac


class ArchiveCheck(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = os.path.realpath(self._tmp.name)
        self.ws = os.path.join(base, "repo")
        self.arch = os.path.join(base, "_arquivo")
        os.makedirs(self.ws)
        os.makedirs(self.arch)
        self.today = datetime.date(2026, 12, 31)

    def make(self, name, age_days, text="x"):
        path = os.path.join(self.arch, name)
        with open(path, "w") as f:
            f.write(text)
        stamp = datetime.datetime.combine(self.today - datetime.timedelta(days=age_days), datetime.time(12)).timestamp()
        os.utime(path, (stamp, stamp))

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ac.main([self.ws, *args], today=self.today)
        return code, out.getvalue()

    def test_old_unindexed_items_are_candidates_and_nothing_is_deleted(self):
        self.make("old.tar", 45)
        self.make("recent.tar", 3)
        self.make("noted.tar", 90)
        self.make("expired.tar", 90)
        with open(os.path.join(self.arch, "INDICE.md"), "w") as f:
            f.write("| Path | What | Delete after |\n|---|---|---|\n| noted.tar | a copy | 2027-06-30 |\n"
                    "| expired.tar | a copy | 2026-11-01 |\n")
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("candidate: old.tar", out)
        self.assertIn("candidate: expired.tar", out)
        self.assertNotIn("recent.tar", out)
        self.assertNotIn("noted.tar", out)
        self.assertEqual(sorted(os.listdir(self.arch)), ["INDICE.md", "expired.tar", "noted.tar", "old.tar", "recent.tar"])

    def test_not_a_repo_folder_is_refused(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ac.main([os.path.dirname(self.ws)], today=self.today), 2)


if __name__ == "__main__":
    unittest.main()

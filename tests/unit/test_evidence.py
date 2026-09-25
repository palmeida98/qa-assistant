"""Run folder: protected from Git, unique, cleanable (docs/qa-audit.md §7); redaction."""

import datetime as dt
import os
import tempfile
import unittest

from qa_audit.core.evidence import RunFolder
from qa_audit.core.privacy import REDACTED, Redactor


class Folder(unittest.TestCase):
    def test_root_gets_a_gitignore_that_ignores_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            RunFolder(os.path.join(tmp, "qa-output"))
            with open(os.path.join(tmp, "qa-output", ".gitignore"), encoding="utf-8") as f:
                lines = [x.strip() for x in f if x.strip() and not x.startswith("#")]
        self.assertEqual(lines, ["*"])

    def test_two_runs_in_the_same_second_do_not_collide(self):
        now = dt.datetime(2026, 9, 25, 8, 50, 0)
        with tempfile.TemporaryDirectory() as tmp:
            a, b = RunFolder(tmp, now=now), RunFolder(tmp, now=now)
            self.assertNotEqual(a.path, b.path)
            self.assertTrue(a.path.endswith("2026-09-25_085000"))

    def test_evidence_names_are_safe_and_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = RunFolder(tmp)
            path, rel = run.new_file("screenshots", "https://shop.example/a?b=<c>", "png")
            self.assertTrue(rel.startswith("screenshots/0001_https-shop-example-a-b-c"))
            self.assertTrue(os.path.isdir(os.path.dirname(path)))
            run.cleanup()
            self.assertFalse(os.path.exists(run.path))


class Redaction(unittest.TestCase):
    def test_secret_headers_and_values(self):
        r = Redactor({"SHOP_TOKEN": "abc123"})
        h = r.headers({"Set-Cookie": "sid=1", "Authorization": "Bearer x", "X-Debug": "token abc123"})
        self.assertEqual(h["Set-Cookie"], REDACTED)
        self.assertEqual(h["Authorization"], REDACTED)
        self.assertEqual(h["X-Debug"], "token «SHOP_TOKEN»")


if __name__ == "__main__":
    unittest.main()

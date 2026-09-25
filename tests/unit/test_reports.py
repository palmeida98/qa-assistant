"""Reports: complete, escaped and self-contained (docs/qa-audit.md §7, §8)."""

import csv
import json
import os
import re
import tempfile
import unittest

from qa_audit.reports import csv_report, html_report, md_report, write_all

from .helpers import HOSTILE, fake_result


class Reports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.result = fake_result(self.tmp.name)
        self.paths = write_all(self.result, ["html", "json", "csv", "md"])

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, fmt):
        with open(self.paths[fmt], encoding="utf-8-sig") as f:
            return f.read()

    def test_all_formats_written(self):
        self.assertEqual(sorted(self.paths), ["csv", "html", "json", "md"])

    def test_json_is_complete(self):
        data = json.loads(self.read("json"))
        self.assertEqual(data["summary"]["by_status"], {"problem": 1, "warning": 1, "needs-human": 1})
        self.assertEqual([f["id"] for f in data["findings"]], ["ACC-001", "ACC-002", "ACC-003"])
        self.assertEqual(data["tool"]["name"], "qa-audit")

    def test_html_is_self_contained_and_escaped(self):
        h = self.read("html")
        self.assertIn('http-equiv="Content-Security-Policy"', h)
        self.assertIn("default-src 'none'", h)
        self.assertNotIn("<script", h.lower())
        self.assertNotIn("<img src=x", h)
        self.assertIn(html_report.esc(HOSTILE), h)
        # nothing loaded from outside: no remote src, no stylesheet links
        self.assertIsNone(re.search(r'src="(https?:)?//', h))
        self.assertNotIn("<link", h)
        # a javascript: reference from audited data is never a link
        self.assertNotIn('href="javascript:', h)
        self.assertIn('src="screenshots/0001_img.png"', h)

    def test_evidence_paths_cannot_escape_the_run_folder(self):
        for bad in ("/etc/passwd", "C:/x.png", "../x.png", "a/../../x.png"):
            with self.subTest(path=bad):
                self.assertEqual(html_report._local_path(bad), "")
        self.assertEqual(html_report._local_path("screenshots/a.png"), "screenshots/a.png")

    def test_csv_neutralises_formulas(self):
        rows = list(csv.reader(self.read("csv").splitlines()))
        self.assertEqual(rows[0], csv_report.COLUMNS)
        titles = [r[csv_report.COLUMNS.index("title")] for r in rows[1:]]
        self.assertIn("'=HYPERLINK(\"http://evil.example\")", titles)
        for v in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx"):
            with self.subTest(value=v):
                self.assertTrue(csv_report.safe_cell(v).startswith("'"))
        self.assertEqual(csv_report.safe_cell("plain"), "plain")

    def test_markdown_escapes_and_fences(self):
        md = self.read("md")
        outside_code = re.sub(r"```+text\n.*?\n```+", "", md, flags=re.DOTALL)
        self.assertNotIn("<script>", outside_code)
        self.assertIn("\\<b\\>walk\\</b\\>", md)
        self.assertEqual(md_report.fence("a ```` b").splitlines()[0], "`````text")
        self.assertIn("# QA audit", md)

    def test_only_requested_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = fake_result(tmp)
            self.assertEqual(sorted(write_all(r, ["json"])), ["json"])
            self.assertEqual(os.listdir(tmp), ["results.json"])

    def test_empty_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = fake_result(tmp, findings=[])
            paths = write_all(r, ["html", "md"])
            with open(paths["html"], encoding="utf-8") as f:
                self.assertIn("No findings.", f.read())


if __name__ == "__main__":
    unittest.main()

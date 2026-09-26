"""Dashboard (scripts/build_dashboard.py, plan 5.1): the numbers match the
golden's expected result (written by hand) and the HTML is a single, offline file.

python -m unittest discover -s tests   (from the root of qa-assistant)
Does not need LibreOffice: counts the statuses directly, without recalculating.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden"), os.path.join(ROOT, "scripts")]

import build_dashboard
from build_fixture import build

from adapters.excel import ExcelAdapter, load_batch

GOLDEN = os.path.join(ROOT, "tests", "golden")
STATES = {"To Do": "pending", "Pass": "ok", "Fail": "fail", "Blocked": "blocked", "N/A": "na"}
CONTEXT = """# Context
Updated: 2026-09-24 10:00

## Execution point
- batch 01, `TC-SHOP-004`
  - sub-item **strong**

## Pending work
- 2.3 <script>x</script>
"""


class _Tags(HTMLParser):
    """Records tags left open or badly nested."""

    VOID = frozenset({"meta", "br", "img", "link", "input", "hr"})

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(tag)


def well_formed(html_txt: str) -> bool:
    p = _Tags()
    p.feed(html_txt)
    return not p.errors and not p.stack


class DashboardGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = tempfile.mkdtemp()
        with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        build(os.path.join(cls.ws, cfg["file"]))
        ad = ExcelAdapter.from_dict(cfg, base_dir=cls.ws)
        ad.load()
        ad.apply_batch(load_batch(os.path.join(GOLDEN, "batch.json")), label="[golden]")
        ad.regenerate_active_defects()
        assert ad.save_if_clean()["saved_original"]
        proj = {"project": "golden", "tracker": "excel", "excel": cfg, "dashboard": {"states": STATES}}
        with open(os.path.join(cls.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f, allow_unicode=True, sort_keys=False)
        with open(os.path.join(cls.ws, "context.md"), "w", encoding="utf-8") as f:
            f.write(CONTEXT)
        with contextlib.redirect_stdout(io.StringIO()):
            assert build_dashboard.main([cls.ws]) == 0
        with open(os.path.join(cls.ws, "dashboard.html"), encoding="utf-8") as f:
            cls.html = f.read()
        cls.num = json.loads(re.search(r'id="numbers">(.*?)</script>', cls.html, re.DOTALL).group(1))
        with open(os.path.join(GOLDEN, "expected.json"), encoding="utf-8") as f:
            cls.exp = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ws)

    def test_totals_per_area_match_the_expected_summary(self):
        ts = self.exp["summary_totals"]
        for a in self.num["cases"]["by_area"]:
            s = ts[a["area"]]
            self.assertEqual(a["total"], s["Total"], a["area"])
            self.assertEqual(a["run"], s["Total"] - s["To Do"], a["area"])
            for status in STATES:
                self.assertEqual(a["by_status"].get(status, 0), s[status], f"{a['area']}/{status}")
        self.assertEqual(self.num["cases"]["total"], ts["Total"]["Total"])
        self.assertEqual(self.num["cases"]["run"], ts["Total"]["Total"] - ts["Total"]["To Do"])

    def test_areas_in_the_configurations_order(self):
        self.assertEqual([a["area"] for a in self.num["cases"]["by_area"]], ["Shop", "Account", "Admin"])

    def test_defects_match_the_expected_view(self):
        # expected: "3 open (2 High, 0 Medium, 1 Low) - 1 closed"
        d = self.num["defects"]
        self.assertEqual((d["open"], d["closed"]), (3, 1))
        self.assertEqual(d["by_severity"], {"High": 2, "Low": 1})
        order = re.findall(r"<tr><td>(BUG-\d+)</td>", self.html)
        self.assertEqual(order, [row[0] for row in self.exp["view"]["rows"]])

    def test_single_offline_file(self):
        self.assertIsNone(re.search(r'(src|href)\s*=\s*"?(https?:)?//', self.html))
        self.assertNotIn("<link", self.html)
        self.assertNotIn("@import", self.html)

    def test_well_formed_html_and_escaped_context(self):
        self.assertTrue(well_formed(self.html))
        self.assertIn("Last checkpoint: 2026-09-24 10:00", self.html)
        self.assertIn("<code>TC-SHOP-004</code>", self.html)
        self.assertNotIn("<script>x</script>", self.html)


class StatusTypes(unittest.TestCase):
    def test_without_a_dashboard_section_the_first_status_is_pending(self):
        self.assertEqual(build_dashboard.status_types(["A", "B"], None), {"A": "pending", "B": "other"})

    def test_an_invalid_section_is_refused(self):
        with self.assertRaises(ValueError):
            build_dashboard.status_types(["A"], {"states": {"Z": "ok"}})
        with self.assertRaises(ValueError):
            build_dashboard.status_types(["A"], {"states": {"A": "green"}})


CFG_NOT_RUN = {"summary": {"not_run_status": "To Do"}}


class ConsistentNotRun(unittest.TestCase):
    """methodology 16.3: the dashboard and the summary use the same 'not run' status."""

    def test_consistent(self):
        build_dashboard.check_not_run(STATES, CFG_NOT_RUN)

    def test_the_dashboard_treats_not_run_as_run(self):
        with self.assertRaises(ValueError):
            build_dashboard.check_not_run({**STATES, "To Do": "ok"}, CFG_NOT_RUN)

    def test_the_dashboard_has_another_pending(self):
        with self.assertRaises(ValueError):
            build_dashboard.check_not_run({**STATES, "N/A": "pending"}, CFG_NOT_RUN)

    def test_without_a_percentage_in_the_summary_it_does_not_check(self):
        build_dashboard.check_not_run({**STATES, "To Do": "ok"}, {"summary": None})


class CommandLine(unittest.TestCase):
    """What the /dashboard skill uses (plan 5.2): --open, exit codes, state of the summary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        build(os.path.join(self.tmp.name, cfg["file"]))
        self.proj = {"project": "t", "tracker": "excel", "excel": cfg, "dashboard": {"states": STATES}}
        self.write_project()

    def tearDown(self):
        self.tmp.cleanup()

    def write_project(self):
        with open(os.path.join(self.tmp.name, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(self.proj, f, allow_unicode=True)

    def run_it(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(build_dashboard.webbrowser, "open") as opened:
            rc = build_dashboard.main([self.tmp.name, *args])
        return rc, out.getvalue(), err.getvalue(), opened

    def test_open_opens_the_local_file(self):
        rc, _, _, opened = self.run_it("--open")
        self.assertEqual(rc, 0)
        uri = opened.call_args.args[0]
        self.assertTrue(uri.startswith("file:") and uri.endswith("/dashboard.html"), uri)

    def test_without_open_it_does_not_open(self):
        self.assertFalse(self.run_it()[3].called)

    def test_it_says_the_state_of_the_summary(self):
        self.assertIn("workbook summary: not checked", self.run_it()[1])

    def test_another_tracker_exits_with_2_without_building(self):
        self.proj["tracker"] = "jira"
        self.write_project()
        rc, _, err, opened = self.run_it("--open")
        self.assertEqual(rc, 2)
        self.assertIn("tracker", err)
        self.assertFalse(opened.called)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "dashboard.html")))


class MinimalMarkdown(unittest.TestCase):
    def test_nested_lists_well_formed(self):
        for md in ["- a\n  - b\n    - c\n- d\n", "  - starts indented\n- back\n", "text\n\n- x\ncontinues\n"]:
            self.assertTrue(well_formed("<div>" + build_dashboard.minimal_markdown(md) + "</div>"), md)


if __name__ == "__main__":
    unittest.main()

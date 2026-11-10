"""add_cases --platform, per-destination checks and reserved IDs (plan Q4, 10.11.2026).

Fictional workspace with two workbooks (the default one and an `ios` entry). Never recalculates; needs no LibreOffice.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

import openpyxl
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden")]

from build_fixture import build

from adapters.excel import add_cases as cli

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)

IOS_FILE = "ios-plan.xlsx"


def case(cid="TC-SHOP-005", title="Remove item from cart", **extra):
    return {"id": cid, "title": title, **extra}


class Platform(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        build(os.path.join(self.ws, CFG["file"]))
        shutil.copy(os.path.join(self.ws, CFG["file"]), os.path.join(self.ws, IOS_FILE))
        cfg = copy.deepcopy(CFG)
        cfg["workbooks"] = {"ios": {"file": IOS_FILE, "batches": ["batch_ios*"]}}
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"tracker": "excel", "excel": cfg}, f)
        os.makedirs(os.path.join(self.ws, "cases"))
        os.environ.pop("QA_WORKBOOK", None)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, cases):
        with open(os.path.join(self.ws, "cases", name), "w", encoding="utf-8") as f:
            json.dump({"cases": cases}, f)
        return f"cases/{name}"

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([self.ws, *args, "--no-recalc", "--now", "2026-10-04T10:00:00", "--detail"])
        return code, out.getvalue() + err.getvalue()

    def shop_ids(self, file):
        ws = openpyxl.load_workbook(os.path.join(self.ws, file))["Shop"]
        return [ws.cell(r, 1).value for r in range(2, ws.max_row + 1)]

    def token(self, out):
        m = re.search(r"(Combined token|Preview token): (v1-[0-9a-f]+)", out)
        self.assertTrue(m, out)
        return m.group(2)

    def test_ios_goes_to_its_own_workbook_without_qa_workbook(self):
        f = self.write("c.json", [case(platform="iOS")])
        code, out = self.run_cli(f, "--platform", "ios", "--dry-run")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli(f, "--platform", "ios", "--expect", self.token(out))
        self.assertEqual(code, 0, out)
        self.assertIn("TC-SHOP-005", self.shop_ids(IOS_FILE))
        self.assertNotIn("TC-SHOP-005", self.shop_ids(CFG["file"]))

    def test_a_platform_without_an_entry_uses_the_default_workbook(self):
        f = self.write("c.json", [case(platform="Android")])
        _, out = self.run_cli(f, "--platform", "android", "--dry-run")
        code, out = self.run_cli(f, "--platform", "android", "--expect", self.token(out))
        self.assertEqual(code, 0, out)
        self.assertIn("TC-SHOP-005", self.shop_ids(CFG["file"]))
        self.assertNotIn("TC-SHOP-005", self.shop_ids(IOS_FILE))

    def test_a_case_that_says_another_platform_is_refused_for_this_destination(self):
        f = self.write("c.json", [case(platform="iOS")])
        code, out = self.run_cli(f, "--platform", "android", "--dry-run")
        self.assertEqual(code, 4, out)
        self.assertIn("says platform 'iOS', not android", out)
        self.assertNotIn("TC-SHOP-005", self.shop_ids(CFG["file"]))

    def test_both_needs_every_case_to_say_both_and_writes_nothing_otherwise(self):
        f = self.write("c.json", [case(platform="Android")])
        code, out = self.run_cli(f, "--platform", "both", "--dry-run")
        self.assertEqual(code, 4, out)
        self.assertIn("not ios", out)
        for file in (CFG["file"], IOS_FILE):
            self.assertNotIn("TC-SHOP-005", self.shop_ids(file))

    def test_both_adds_to_every_workbook_with_one_combined_token(self):
        f = self.write("c.json", [case(platform="Android e iOS")])
        code, out = self.run_cli(f, "--platform", "both", "--dry-run")
        self.assertEqual(code, 0, out)
        token = self.token(out)
        self.assertIn("Combined token", out)
        # without the token, or with a stale one, nothing is written
        code, out2 = self.run_cli(f, "--platform", "both")
        self.assertEqual(code, 4, out2)
        code, out2 = self.run_cli(f, "--platform", "both", "--expect", "v1-0000000000000000")
        self.assertEqual(code, 4, out2)
        for file in (CFG["file"], IOS_FILE):
            self.assertNotIn("TC-SHOP-005", self.shop_ids(file))
        code, out3 = self.run_cli(f, "--platform", "both", "--expect", token)
        self.assertEqual(code, 0, out3)
        for file in (CFG["file"], IOS_FILE):
            self.assertIn("TC-SHOP-005", self.shop_ids(file))

    def test_both_validates_every_destination_before_writing_any(self):
        # the iOS workbook already has the ID: the add to BOTH is refused and the default workbook stays untouched
        self.run_cli(self.write("first.json", [case(platform="iOS")]), "--platform", "ios", "--expect",
                     self.token(self.run_cli("cases/first.json", "--platform", "ios", "--dry-run")[1]))
        f = self.write("again.json", [case(title="Another title", platform="Android e iOS")])
        code, out = self.run_cli(f, "--platform", "both", "--dry-run")
        self.assertEqual(code, 4, out)
        self.assertNotIn("TC-SHOP-005", self.shop_ids(CFG["file"]))

    def test_an_id_proposed_with_different_content_in_another_file_is_refused(self):
        self.write("a.json", [case(title="One thing")])
        f = self.write("b.json", [case(title="A different thing")])
        code, out = self.run_cli(f, "--platform", "android", "--dry-run")
        self.assertEqual(code, 4, out)
        self.assertIn("also proposed in cases/a.json", out)

    def test_an_id_already_in_the_workbook_is_not_reported_as_a_reserved_clash(self):
        self.write("a.json", [case(title="One thing")])
        f = self.write("b.json", [case("TC-SHOP-001", "Something else")])      # TC-SHOP-001 is in the fixture workbook
        self.write("c.json", [case("TC-SHOP-001", "A third title")])
        code, out = self.run_cli(f, "--platform", "android", "--dry-run")
        self.assertEqual(code, 4, out)
        self.assertNotIn("also proposed", out)          # the existing ID is refused by the usual check instead

    def test_the_same_content_in_a_sibling_file_is_not_a_clash(self):
        self.write("a_ios.json", [case(platform="iOS")])
        f = self.write("a.json", [case(platform="Android")])        # same case, only the platform differs
        code, out = self.run_cli(f, "--platform", "android", "--dry-run")
        self.assertEqual(code, 0, out)

    def test_without_platform_and_with_several_workbooks_a_notice_is_printed(self):
        code, out = self.run_cli(self.write("c.json", [case()]), "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("no --platform was given", out)

    def test_an_unknown_platform_goes_to_the_default_workbook_but_both_needs_two_workbooks(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"tracker": "excel", "excel": CFG}, f)       # one workbook only
        code, out = self.run_cli(self.write("c.json", [case()]), "--platform", "both", "--dry-run")
        self.assertEqual(code, 4, out)
        self.assertIn("more than one workbook", out)


if __name__ == "__main__":
    unittest.main()

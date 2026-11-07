"""ExcelAdapter.from_project: `file` / QA_WORKBOOK replace excel.file for one command (technique-excel.md, one
workbook per platform). No workbook is read: only the path the adapter resolves.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

from adapters.excel import ExcelAdapter

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)


class Override(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"excel": CFG}, f)
        self.environ = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "QA_WORKBOOK"},
                                       clear=True)
        self.environ.start()

    def tearDown(self):
        self.environ.stop()
        self.tmp.cleanup()

    def path(self, **kw):
        return ExcelAdapter.from_project(self.ws, **kw).path

    def test_default_is_excel_file(self):
        self.assertEqual(self.path(), os.path.join(self.ws, CFG["file"]))

    def test_the_environment_variable_replaces_it_relative_to_the_workspace(self):
        with mock.patch.dict(os.environ, {"QA_WORKBOOK": "workbook/app-ios.xlsx"}):
            self.assertEqual(self.path(), os.path.join(self.ws, "workbook/app-ios.xlsx"))

    def test_an_absolute_path_is_kept_and_the_argument_wins(self):
        other = os.path.join(self.ws, "elsewhere.xlsx")
        with mock.patch.dict(os.environ, {"QA_WORKBOOK": other}):
            self.assertEqual(self.path(), other)
            self.assertEqual(self.path(file="arg.xlsx"), os.path.join(self.ws, "arg.xlsx"))

    def test_an_empty_variable_counts_as_unset(self):
        with mock.patch.dict(os.environ, {"QA_WORKBOOK": "  "}):
            self.assertEqual(self.path(), os.path.join(self.ws, CFG["file"]))

    def test_the_configuration_of_the_workspace_is_not_changed_by_the_override(self):
        with mock.patch.dict(os.environ, {"QA_WORKBOOK": "x.xlsx"}):
            self.path()
        self.assertEqual(self.path(), os.path.join(self.ws, CFG["file"]))


if __name__ == "__main__":
    unittest.main()

"""scripts/common.py (plan 7.8): reading project.yaml and .env, shared by a
workspace's scripts. Redaction (Redactor) is covered by the tests of
api_request and run_mobile.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import api_request
import audit_a11y
import common
import run_mobile


class Common(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text, encoding="utf-8"):
        with open(os.path.join(self.ws, name), "w", encoding=encoding) as f:
            f.write(text)

    def test_env_with_bom_comments_export_and_quotes(self):
        self.write(".env", "# comment\n\nexport A=1\nB = 'two'\nC=\"three = 3\"\nNO_EQUALS\nD=\n",
                   encoding="utf-8-sig")
        self.assertEqual(common.read_workspace_env(self.ws), {"A": "1", "B": "two", "C": "three = 3", "D": ""})

    def test_missing_env_is_empty(self):
        self.assertEqual(common.read_workspace_env(self.ws), {})

    def test_empty_project_is_a_dict_and_missing_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            common.read_project(self.ws)
        self.write("project.yaml", "")
        self.assertEqual(common.read_project(self.ws), {})
        self.write("project.yaml", "project: shop\napi:\n  base_url: https://shop.demo/\n", encoding="utf-8-sig")
        self.assertEqual(common.read_project(self.ws)["api"]["base_url"], "https://shop.demo/")

    def test_a_workspace_from_before_the_translation_says_how_to_convert_it(self):
        self.write("projeto.yaml", "projeto: shop\n")
        with self.assertRaises(FileNotFoundError) as e:
            common.read_project(self.ws)
        self.assertIn("migrate_workspace.py", str(e.exception))

    def test_the_scripts_use_the_common_code(self):
        self.assertIs(api_request.Redactor, common.Redactor)
        self.assertIs(api_request.read_env, common.read_env)
        self.assertEqual(api_request.REDACTED, common.REDACTED)
        self.assertIs(run_mobile.Redactor, common.Redactor)
        self.assertFalse(hasattr(audit_a11y, "api_request"))

    def test_missing_project_keeps_the_scripts_messages(self):
        with self.assertRaises(audit_a11y.ConfigError) as a:
            audit_a11y.configuration(self.ws)
        with self.assertRaises(run_mobile.UsageError) as m:
            run_mobile.configuration(self.ws)
        for e in (a.exception, m.exception):
            self.assertIn("does not exist", str(e))
            self.assertIn("project.yaml", str(e))
        self.assertIsNone(api_request.base_url(self.ws))


if __name__ == "__main__":
    unittest.main()


class FindAdb(unittest.TestCase):
    """The 11.8 pilot: a GitHub-hosted runner has the SDK (ANDROID_HOME) but not adb on the PATH."""

    def test_on_the_path_first_then_in_the_sdk(self):
        with tempfile.TemporaryDirectory() as sdk:
            os.makedirs(os.path.join(sdk, "platform-tools"))
            adb = os.path.join(sdk, "platform-tools", "adb.exe" if os.name == "nt" else "adb")
            open(adb, "w").close()
            with mock.patch.object(common.shutil, "which", return_value=None),                     mock.patch.dict(os.environ, {"ANDROID_HOME": sdk}):
                self.assertEqual(common.find_adb(), adb)
            with mock.patch.object(common.shutil, "which", return_value="/usr/bin/adb"):
                self.assertEqual(common.find_adb(), "/usr/bin/adb")

    def test_nowhere_is_none(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(common.shutil, "which", return_value=None),                 mock.patch.object(common.os.path, "expanduser", lambda p: p.replace("~", home)),                 mock.patch.dict(os.environ, {"ANDROID_HOME": "", "ANDROID_SDK_ROOT": "", "LOCALAPPDATA": home}):
            self.assertIsNone(common.find_adb())

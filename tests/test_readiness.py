"""The preflight separates a valid configuration from being ready to run (audit of 2026-11-06): each route says,
per mobile platform, whether its cases can run now and what blocks them; Maestro is required only where it is used;
an iOS section adds the iPhone's tools. No device is contacted: the tool lines are given or the lookups are mocked.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import validate_project as vp
import workspace_select as wsel


def line(tool, ok, detail="d"):
    return {"tool": tool, "ok": ok, "detail": detail}


class Readiness(unittest.TestCase):
    def test_each_mobile_platform_has_its_own_verdict(self):
        lines = [line("adb", True), line("android-driver", False, "no device answered"), line("emulator", True),
                 line("xcrun", True), line("ios-wda", True), line("build", True)]
        got = {r["platform"]: r for r in wsel._readiness("mobile", lines)}
        self.assertFalse(got["android"]["ready"])
        self.assertEqual(got["android"]["blocked"], [("android-driver", "no device answered")])
        self.assertTrue(got["ios"]["ready"])

    def test_a_missing_build_blocks_every_platform(self):
        lines = [line("adb", True), line("xcrun", True), line("ios-wda", True), line("build", False, "no source")]
        self.assertTrue(all(not r["ready"] for r in wsel._readiness("mobile", lines)))

    def test_without_ios_tools_there_is_no_ios_verdict(self):
        self.assertEqual([r["platform"] for r in wsel._readiness("mobile", [line("adb", True)])], ["android"])


class Environment(unittest.TestCase):
    def lines(self, mobile):
        proj = {"modules": ["mobile"], "mobile": mobile}
        with mock.patch.object(vp, "which", return_value=None), \
                mock.patch.object(vp, "android_tools", return_value={"adb": "/x/adb"}), \
                mock.patch.object(vp, "driver_on_device", return_value=(True, "on")), \
                mock.patch.object(vp, "wda_reachable", return_value=(False, "ios-wda", "not answering")):
            return {t: (ok, d) for ok, t, d in vp.environment("/nowhere", proj) if t in ("maestro", "xcrun", "ios-wda")}

    def test_maestro_is_not_required_with_the_driver(self):
        got = self.lines({"platform": "android", "app_id": "a", "serial": "S1", "input": "driver"})
        self.assertTrue(got["maestro"][0])
        self.assertIn("not needed", got["maestro"][1])

    def test_maestro_is_required_with_adb_input(self):
        got = self.lines({"platform": "android", "app_id": "a", "serial": "S1", "input": "adb"})
        self.assertFalse(got["maestro"][0])

    def test_an_ios_section_adds_the_iphone_tools(self):
        got = self.lines({"platform": "android", "app_id": "a", "serial": "S1", "input": "driver",
                          "ios": {"app_id": "a"}})
        self.assertIn("xcrun", got)
        self.assertEqual(got["ios-wda"], (False, "not answering"))


class Names(unittest.TestCase):
    def test_an_unknown_or_retired_skill_says_what_exists(self):
        _, instruction, _, _ = wsel.check_skill("tset-pbi", ROOT, "/nowhere", ROOT)
        self.assertIn("/test-pbi", instruction)
        self.assertNotIn("git -C", instruction)                 # no update would help a typo
        _, instruction, _, _ = wsel.check_skill("test-api", ROOT, "/nowhere", ROOT)
        self.assertIn("/run-tests", instruction)

    def test_the_readme_and_the_template_name_no_removed_skill(self):
        import re
        removed = re.compile(r"(?<![\w-])/(analyze-tickets|project-onboarding|test-api|test-mobile|log-bug|end-session|"
                             r"run-batch|plan-batch|generate-cases|analyze-feature|audit-accessibility)\b|--skill "
                             r"(test-api|test-mobile|log-bug|dashboard|run-batch)\b")
        for rel in ("README.md", os.path.join("docs", "project.yaml"), os.path.join("docs", "procedures", "rules.md")):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                self.assertIsNone(removed.search(f.read()), rel)

if __name__ == "__main__":
    unittest.main()

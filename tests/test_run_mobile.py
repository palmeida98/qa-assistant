"""scripts/run_mobile.py (technique-mobile.md): case from the flow's name, reading
Maestro's JUnit with redaction, credentials only through MAESTRO_<VAR>, no target =
Blocked. No Maestro nor device: Maestro and adb are simulated.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import run_mobile as rm
from common import Redactor

# real format of `maestro test --format junit` (2.10), with a fictional secret in the message
JUNIT = """<?xml version='1.0' encoding='UTF-8'?>
<testsuites>
  <testsuite name="Test Suite" tests="2" failures="1" time="19.9">
    <testcase id="TC-MOB-002_locked" name="TC-MOB-002_locked" file="TC-MOB-002_locked.yaml" time="19.5"
      status="ERROR">
      <failure>Assertion is false: "fictional-password-9" is visible</failure>
    </testcase>
    <testcase id="TC-MOB-001_login" name="TC-MOB-001_login" file="TC-MOB-001_login.yaml" time="13.3"
      status="SUCCESS"/>
  </testsuite>
</testsuites>"""


class Units(unittest.TestCase):
    def test_case_from_the_flows_name(self):
        self.assertEqual(rm.case_of_flow("flows/TC-MOB-003_cart.yaml"), "TC-MOB-003")
        self.assertEqual(rm.case_of_flow("TC-LOGIN-001.yaml"), "TC-LOGIN-001")
        self.assertEqual(rm.case_of_flow("CT-MOB-002_bloqueado.yaml"), "CT-MOB-002")   # any project prefix
        self.assertIsNone(rm.case_of_flow("login.yaml"))

    def test_junit_with_redaction(self):
        res = rm.read_junit(JUNIT, Redactor({"PASSWORD": "fictional-password-9"}))
        self.assertEqual([(r["case"], r["suggested_status"]) for r in res],
                         [("TC-MOB-002", "Fail"), ("TC-MOB-001", "Pass")])
        self.assertNotIn("fictional-password-9", json.dumps(res))
        self.assertIn("«PASSWORD»", res[0]["message"])


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        os.makedirs(os.path.join(self.ws, "flows"))
        for name in ("TC-MOB-001_login.yaml", "TC-MOB-002_locked.yaml"):
            with open(os.path.join(self.ws, "flows", name), "w", encoding="utf-8") as f:
                f.write("appId: shop.demo\n---\n- launchApp\n")
        self.project("android")
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write("SHOP_U=ana\nSHOP_P=fictional-password-9\n")

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, platform, variables="[SHOP_U, SHOP_P]"):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write(f"mobile:\n  platform: {platform}\n  flows: flows\n  variables: {variables}\n")

    def run_it(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = rm.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    def test_credentials_only_in_maestro_variables(self):
        env, secrets = rm.maestro_environment(self.ws, ["SHOP_U", "SHOP_P"])
        self.assertEqual(env, {"MAESTRO_SHOP_U": "ana", "MAESTRO_SHOP_P": "fictional-password-9"})
        self.assertEqual(set(secrets), {"SHOP_U", "SHOP_P"})

    def test_a_variable_without_a_value_exits_with_2(self):
        self.project("web", "[DOES_NOT_EXIST]")
        with mock.patch.object(rm.shutil, "which", return_value="maestro"):
            rc, _, err = self.run_it()
        self.assertEqual(rc, 2)
        self.assertIn("DOES_NOT_EXIST", err)

    def test_an_invalid_platform_exits_with_2(self):
        self.project("windows-phone")
        self.assertEqual(self.run_it()[0], 2)

    def test_a_flow_without_a_case_id_exits_with_2(self):
        with open(os.path.join(self.ws, "flows", "no_id.yaml"), "w", encoding="utf-8") as f:
            f.write("---\n")
        rc, _, err = self.run_it(os.path.join(self.ws, "flows", "no_id.yaml"))
        self.assertEqual(rc, 2)
        self.assertIn("without a case ID", err)

    def test_a_missing_flow_exits_with_2_without_running_maestro(self):
        # before: Maestro did not run it and the case was Blocked "the flow did not run"
        with mock.patch.object(rm.subprocess, "run") as run:
            rc, out, err = self.run_it("TC-MOB-009", "flows/TC-MOB-001_logn.yaml")
        self.assertEqual((rc, out), (2, ""))
        run.assert_not_called()
        self.assertIn("TC-MOB-009", err)
        self.assertIn("did you mean ['TC-MOB-001_login.yaml']", err)

    def test_flow_by_case_id_or_relative_path(self):
        folder = os.path.join(self.ws, "flows")
        expected = [os.path.abspath(os.path.join(folder, "TC-MOB-001_login.yaml")),
                    os.path.abspath(os.path.join(folder, "TC-MOB-002_locked.yaml"))]
        for names in (["TC-MOB-001", "TC-MOB-002"],                               # case ID
                      ["flows/TC-MOB-001_login.yaml", "TC-MOB-002_locked.yaml"],  # workspace / folder
                      expected):                                                  # absolute
            with self.subTest(names=names):
                self.assertEqual(rm.resolve_flows(names, self.ws, folder), expected)

    def test_a_case_id_with_two_flows_is_ambiguous(self):
        with open(os.path.join(self.ws, "flows", "TC-MOB-001_other.yaml"), "w", encoding="utf-8") as f:
            f.write("---\n")
        with self.assertRaises(rm.UsageError) as cm:
            rm.resolve_flows(["TC-MOB-001"], self.ws, os.path.join(self.ws, "flows"))
        self.assertIn("TC-MOB-001_login.yaml", str(cm.exception))
        self.assertIn("TC-MOB-001_other.yaml", str(cm.exception))

    def test_by_default_every_flow_named_after_a_case(self):
        with open(os.path.join(self.ws, "flows", "helper.yaml"), "w", encoding="utf-8") as f:
            f.write("---\n")     # a flow other flows use: not a case, not run on its own

        def which(name):
            return None if name == "adb" else "maestro"
        with mock.patch.object(rm.shutil, "which", side_effect=which):
            rc, out, _ = self.run_it()
        self.assertEqual(rc, 3)
        self.assertIn("TC-MOB-001: Blocked", out)
        self.assertIn("TC-MOB-002: Blocked", out)
        self.assertNotIn("helper", out)

    def test_no_android_device_gives_blocked_and_does_not_run_maestro(self):
        def which(name):
            return None if name == "adb" else "maestro"
        with mock.patch.object(rm.shutil, "which", side_effect=which), \
                mock.patch.object(rm.subprocess, "run") as run:
            rc, out, _ = self.run_it("--output", "runs/m.json")
        self.assertEqual(rc, 3)
        run.assert_not_called()
        with open(os.path.join(self.ws, "runs", "m.json"), encoding="utf-8") as f:
            rep = json.load(f)
        self.assertEqual({r["suggested_status"] for r in rep["results"]}, {"Blocked"})
        self.assertIn("adb not found", out)

    def test_web_runs_maestro_with_the_variables_in_the_environment(self):
        self.project("web")

        def run_maestro(cmd, **kw):
            target = cmd[cmd.index("--output") + 1]
            with open(target, "w", encoding="utf-8") as f:
                f.write(JUNIT)
            self.assertEqual(kw["env"]["MAESTRO_SHOP_P"], "fictional-password-9")
            self.assertNotIn("fictional-password-9", " ".join(cmd))  # never on the command line
            return mock.Mock(stdout="", stderr="")

        with mock.patch.object(rm.shutil, "which", return_value="maestro"), \
                mock.patch.object(rm.subprocess, "run", side_effect=run_maestro):
            rc, out, _ = self.run_it()
        self.assertEqual(rc, 0)
        self.assertIn("TC-MOB-001: Pass", out)
        self.assertIn("TC-MOB-002: Fail", out)
        self.assertNotIn("fictional-password-9", out)

    # --- helper flows (plan 11.2)

    def helper(self, name="_request_link.yaml"):
        with open(os.path.join(self.ws, "flows", name), "w", encoding="utf-8") as f:
            f.write("appId: com.demoshop.app\n---\n- tapOn: E-mail\n- inputText: ${MAESTRO_SHOP_U}\n")
        return f"flows/{name}"

    def test_a_helper_flow_runs_with_the_variables_redacted_and_records_nothing(self):
        flow = self.helper()
        self.project("web")
        seen = {}

        def maestro(cmd, env, timeout):
            seen.update(cmd=cmd, env=env)
            return 1, "Tapping on E-mail\nInput text ana\nFAILED: fictional-password-9 not accepted\n"

        with mock.patch.object(rm.shutil, "which", return_value="maestro"), \
                mock.patch.object(rm, "run_maestro", side_effect=maestro):
            rc, out, err = self.run_it("--helper", flow, "--serial", "emulator-5554")
        self.assertEqual(rc, 3)
        self.assertEqual(seen["cmd"][:4], ["maestro", "--device", "emulator-5554", "test"])
        self.assertEqual(seen["env"]["MAESTRO_SHOP_U"], "ana")
        self.assertNotIn("fictional-password-9", out + err)
        self.assertIn("«SHOP_P»", err)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs")))             # nothing recorded
        with mock.patch.object(rm.shutil, "which", return_value="maestro"), \
                mock.patch.object(rm, "run_maestro", return_value=(0, "ok")):
            rc, out, _ = self.run_it("--helper", flow)
        self.assertEqual(rc, 0)
        self.assertIn("passed (helper flow, no result recorded)", out)

    def test_a_helper_flow_is_not_a_case_and_a_case_is_not_a_helper(self):
        self.helper("request_link.yaml")
        with mock.patch.object(rm.shutil, "which", return_value="maestro"):
            self.assertEqual(self.run_it("--helper", "flows/request_link.yaml")[0], 2)      # no '_'
            self.assertEqual(self.run_it("--helper", "flows/TC-MOB-001_login.yaml")[0], 2)  # a case
            self.assertEqual(self.run_it("--helper", "flows/_missing.yaml")[0], 2)
            rc, _, err = self.run_it(self.helper())                                         # as a case: refused
        self.assertEqual(rc, 2)
        self.assertIn("--helper", err)
        with self.assertRaises(SystemExit):
            self.run_it("--helper", "flows/_request_link.yaml", "TC-MOB-001")

    def test_a_helper_flow_without_a_target_is_3(self):
        flow = self.helper()

        def which(name):
            return None if name == "adb" else "maestro"
        with mock.patch.object(rm.shutil, "which", side_effect=which), \
                mock.patch.object(rm, "run_maestro") as maestro:
            rc, _, err = self.run_it("--helper", flow)
        self.assertEqual(rc, 3)
        self.assertIn("no target", err)
        maestro.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""The read-only diagnosis of an iPhone target (scripts/ios_diagnose.py; docs/pilot-improvements-2026-11-07.md C),
with fictional probes: the causes seen in the pilot (an expired profile, no Xcode account, a locked iPhone, usbmux not
listing a device devicectl sees), each finding confirmed / probable / unknown with its source, the main block and the
next action first, nothing of the keychain beyond counts, and `device.py doctor`.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import device as dv
import device_ios as wda
import ios_diagnose as dg

NOW = datetime(2026, 11, 7, 7, 0, tzinfo=UTC)
UDID = "00008110-FICTIONAL0001"
CFG = {"serial": UDID, "wda_url": "http://localhost:8100", "app_id": "com.demoshop.app"}


class Fake:
    """A healthy target unless a test changes a field."""

    def __init__(self, **changes):
        self.devices = [{"identifier": "X", "hardwareProperties": {"udid": UDID, "marketingName": "iPhone 13 Pro"},
                         "connectionProperties": {"transportType": "wired", "pairingState": "paired",
                                                  "tunnelState": "connected"},
                         "deviceProperties": {"osVersionNumber": "26.7.1", "name": "Fictional person's iPhone"}}]
        self.lock = {"result": {"passcodeRequired": False}}
        self.mux = UDID + "\n"
        self.open, self.iproxy = True, True
        self.status = {"ready": True}
        self.profile_list = [{"app_id": "TEAM123456.com.fictional.WebDriverAgentRunner.xctrunner",
                              "expires": NOW + timedelta(days=6), "team": "TEAM123456"}]
        self.ids = ('  1) ABCDEF "Apple Development: Fictional Person (XYZ987)"\n     1 valid identities found\n')
        self.accounts = 1
        self.session = {"ok": True}
        self.log = None
        self.__dict__.update(changes)

    def __call__(self, cfg):
        return self

    def devicectl_devices(self):
        return None if self.devices is None else {"result": {"devices": self.devices}}

    def lock_state(self, udid):
        return self.lock

    def usbmux(self):
        return self.mux

    def port_open(self, host, port):
        return self.open

    def iproxy_running(self):
        return self.iproxy

    def wda_status(self):
        return self.status

    def wda_session(self):
        return self.session

    def profiles(self):
        return self.profile_list

    def identities(self):
        return self.ids

    def xcode_accounts(self):
        return self.accounts

    def wda_log(self):
        return None if self.log is None else {"path": "/fictional/wda_run.log", "text": self.log,
                                              "mtime": NOW.timestamp()}


def verdict(fake):
    findings = dg.diagnose(CFG, fake, now=NOW)
    return dg.verdict(findings), findings


class Diagnosis(unittest.TestCase):
    def test_a_healthy_target_is_ready(self):
        (key, _, _), findings = verdict(Fake())
        self.assertEqual(key, "ready")
        self.assertEqual({f.name: f.state for f in findings}["profile"], "confirmed")

    def test_an_expired_profile_from_the_log_and_from_the_profiles(self):
        down = {"status": {"_error": "no answer"}, "open": False}
        (key, block, action), _ = verdict(Fake(log="Error: Provisioning profile \"iOS Team\" has expired.", **down))
        self.assertEqual(key, "profile_expired")
        self.assertIn("/fictional/wda_run.log", block)
        self.assertIn("-allowProvisioningUpdates", action)
        old = [{"app_id": "T.com.x.WebDriverAgentRunner.xctrunner", "expires": NOW - timedelta(days=1), "team": "T"}]
        (key, block, _), findings = verdict(Fake(profile_list=old, **down))
        self.assertEqual(key, "profile_expired")
        self.assertIn("EXPIRED on", {f.name: f.value for f in findings}["profile"])

    def test_no_account_in_xcode(self):
        (key, _, action), _ = verdict(Fake(status={"_error": "x"}, log="error: No Accounts: Add a new account in "
                                                                        "Accounts settings."))
        self.assertEqual(key, "no_account")
        self.assertIn("Xcode > Settings > Accounts", action)
        (key, block, _), findings = verdict(Fake(status={"_error": "x"}, accounts=0, profile_list=[]))
        self.assertEqual(key, "no_account")
        self.assertIn("probably", block)
        self.assertEqual({f.name: f.state for f in findings}["account"], "probable")

    def test_a_locked_iphone(self):
        (key, block, action), _ = verdict(Fake(status={"_error": "x"}, lock={"result": {"passcodeRequired": True}}))
        self.assertEqual(key, "locked")
        self.assertIn("devicectl device info lockState", block)
        self.assertIn("unlock the iPhone", action)

    def test_usbmux_not_listing_a_device_devicectl_sees_is_never_a_cable_problem(self):
        (key, _, action), findings = verdict(Fake(mux="", status={"_error": "x"}))
        self.assertNotEqual(key, "not_detected")
        self.assertNotIn("cable", action)
        usbmux = {f.name: f for f in findings}["usbmux"]
        self.assertIn("not a cable problem by itself", usbmux.value)

    def test_a_device_nobody_sees_is_not_detected(self):
        (key, _, action), _ = verdict(Fake(devices=[], mux="", status={"_error": "x"}, open=False))
        self.assertEqual(key, "not_detected")
        self.assertIn("cable", action)

    def test_no_port_forward_and_a_wda_down_with_everything_else_fine(self):
        (key, _, action), _ = verdict(Fake(status={"_error": "x"}, open=False, iproxy=False))
        self.assertEqual(key, "no_forward")
        self.assertIn("iproxy 8100 8100", action)
        (key, _, _), _ = verdict(Fake(status={"_error": "x"}, open=False))
        self.assertEqual(key, "wda_down")

    def test_ui_automation_refused_now_blocks_even_with_status_ready(self):
        refused = {"_error": 'WDA /session: Error Domain=XCTDaemonErrorDomain Code=41 "Not authorized for performing '
                             'UI testing actions."'}
        (key, block, action), findings = verdict(Fake(session=refused))           # /status ready
        self.assertEqual(key, "ui_not_authorized")
        self.assertIn("POST /session now", block)
        self.assertIn("Enable UI Automation", action)
        self.assertTrue({f.name: f.value for f in findings}["automation"].startswith("REFUSED"))

    def test_an_old_refusal_in_the_log_is_history_once_a_session_opens(self):
        line = 'Error Domain=XCTDaemonErrorDomain Code=41 "Not authorized for performing UI testing actions."'
        (key, block, _), _ = verdict(Fake(log=line))                               # the log is recent
        self.assertEqual(key, "ready")
        self.assertIn("an earlier refusal in the WDA log is history", block)
        (key, block, _), _ = verdict(Fake(log=line, status={"_error": "x"}))       # no session could be tried
        self.assertEqual(key, "ui_not_authorized")
        self.assertIn("probably", block)

    def test_a_session_that_does_not_open_for_another_reason_is_not_ready(self):
        (key, block, _), _ = verdict(Fake(session={"_error": "WDA /session: timed out"}))
        self.assertEqual(key, "wda_not_ready")
        self.assertIn("no session opens", block)

    def test_untrusted_certificate_from_the_log(self):
        (key, _, action), _ = verdict(Fake(status={"_error": "x"}, log="The application could not be verified: "
                                                                        "the developer is not trusted"))
        self.assertEqual(key, "not_trusted")
        self.assertIn("VPN & Device Management", action)

    def test_missing_tools_are_unknown_never_a_guess(self):
        nothing = Fake(devices=None, lock=None, mux=None, iproxy=None, status={"_error": "x"}, open=False,
                       profile_list=None, ids=None, accounts=None)
        findings = dg.diagnose(CFG, nothing, now=NOW)
        states = {f.name: f.state for f in findings}
        for name in ("devicectl", "lock", "usbmux", "profile", "certificate", "account", "wda_log"):
            self.assertEqual(states[name], "unknown", name)
        self.assertEqual(dg.verdict(findings)[0], "wda_down")

    def test_nothing_of_the_keychain_or_the_device_name_is_shown(self):
        lines = "\n".join(dg.render(dg.diagnose(CFG, Fake(status={"_error": "x"}), now=NOW)))
        self.assertIn("1 code-signing identity (Apple Development)", lines)
        for secret in ("Fictional Person", "XYZ987", "ABCDEF", "Fictional person's iPhone"):
            self.assertNotIn(secret, lines)

    def test_the_block_and_the_next_action_come_first(self):
        lines = dg.render(dg.diagnose(CFG, Fake(status={"_error": "x"}, lock={"result": {"passcodeRequired": True}}),
                                      now=NOW))
        self.assertTrue(lines[0].startswith("blocked by: the iPhone is locked"))
        self.assertTrue(lines[1].startswith("next: unlock"))
        self.assertTrue(any("[confirmed: xcrun devicectl device info lockState]" in x for x in lines))


class RealProbes(unittest.TestCase):
    """The probes read with a time limit and never fail the diagnosis."""

    def test_the_accounts_are_counted_from_the_account_list_never_shown(self):
        listing = ('{\n    "IDE.Identifiers.Prod" =     (\n                {\n            identifier = '
                   '"person@fictional.example";\n        }\n    );\n}\n')
        with mock.patch.object(dg, "_run", return_value=(0, listing)):
            self.assertEqual(dg.Probes(CFG).xcode_accounts(), 1)
        with mock.patch.object(dg, "_run", return_value=(1, "")):          # the key does not exist
            self.assertEqual(dg.Probes(CFG).xcode_accounts(), 0)
        with mock.patch.object(dg, "_run", return_value=(0, listing)):
            lines = "\n".join(dg.render(dg.diagnose(CFG, Fake(status={"_error": "x"}), now=NOW)))
        self.assertNotIn("person@", lines)

    def test_a_missing_tool_is_none_and_a_timeout_is_none(self):
        p = dg.Probes(CFG)
        with mock.patch.object(dg.shutil, "which", return_value=None):
            self.assertIsNone(p.usbmux())
            self.assertIsNone(p.identities())
            self.assertIsNone(p.devicectl_devices())
        with mock.patch.object(dg.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(dg.subprocess, "run", side_effect=dg.subprocess.TimeoutExpired("x", 1)):
            self.assertIsNone(p.usbmux())

    def test_the_log_is_read_from_its_end_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wda.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write("x" * (dg.LOG_TAIL_BYTES * 2) + "\nprofile has expired\n")
            with mock.patch.dict(os.environ, {dg.LOG_ENV: path}):
                log = dg.Probes(CFG).wda_log()
        self.assertLessEqual(len(log["text"]), dg.LOG_TAIL_BYTES)
        self.assertIn("profile has expired", log["text"])


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write(f"mobile:\n  platform: ios\n  ios:\n    app_id: com.demoshop.app\n    udid: {UDID}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, fake, *args):
        out, err = io.StringIO(), io.StringIO()

        def down(cfg, method, path, body=None, timeout=30):
            raise wda.Unreachable("WebDriverAgent did not answer")
        with mock.patch.object(dv, "ios_probes", fake), mock.patch.object(wda, "request", down), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dv.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    def test_doctor_reports_and_exits_3_when_blocked(self):
        rc, out, err = self.run_it(Fake(status={"_error": "x"}, lock={"result": {"passcodeRequired": True}}), "doctor")
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("blocked by: the iPhone is locked"))
        self.assertIn("NO TARGET", err)

    def test_doctor_ready_is_0_and_check_down_shows_the_block_first(self):
        rc, out, _ = self.run_it(Fake(), "doctor")
        self.assertEqual(rc, 0)
        rc, out, _ = self.run_it(Fake(status={"_error": "x"}, log="Provisioning profile has expired"), "check")
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("blocked by: the WebDriverAgent provisioning profile has expired"), out)
        self.assertIn("ready: NOT READY", out)

    def test_no_session_is_passive_and_says_what_it_did_not_prove(self):
        calls = []

        class Counting(Fake):
            def wda_session(self):
                calls.append(1)
                return {"ok": True}
        rc, out, _ = self.run_it(Counting(), "doctor", "--no-session")
        self.assertEqual((rc, calls), (0, []))
        self.assertIn("automation: not tried (--no-session)", out)
        rc, out, _ = self.run_it(Counting(), "doctor")
        self.assertEqual(calls, [1])

    def test_doctor_is_for_ios_only(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: android\n  app_id: com.demoshop.app\n")
        with mock.patch.object(dv, "resolve_serial", return_value="emulator-5554"):
            rc, _, err = self.run_it(Fake(), "doctor")
        self.assertEqual(rc, 2)
        self.assertIn("doctor is for --platform ios", err)


if __name__ == "__main__":
    unittest.main()

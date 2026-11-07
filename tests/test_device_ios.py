"""scripts/device.py with --platform ios and scripts/device_ios.py (technique-mobile.md §1.3): selecting the
platform, the foreground guard read from WebDriverAgent, the accessibility tree (/source) as lines, the gestures,
typing, `back`, the screenshot, and the transport against a real local HTTP server. No iPhone: the single
`request` function is replaced by a fake WDA.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import common
import device as dv
import device_ios as wda

APP = "com.demoshop.app"
PNG = b"\x89PNG\r\n\x1a\nfictional"
SID = "S-1"


def el(type_, label="", rect=(0, 0, 100, 50), value=None, name=None, visible="1", children=()):
    return {"type": type_, "label": label, "name": name, "value": value, "isVisible": visible,
            "rect": {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]}, "children": list(children)}


HOME = el("XCUIElementTypeApplication", "Demo Shop", (0, 0, 390, 844), children=[
    el("XCUIElementTypeStaticText", "Demo Shop", (0, 50, 390, 40)),
    el("XCUIElementTypeOther", "Cart", (0, 100, 200, 40), children=[el("XCUIElementTypeStaticText", "Cart", (0, 100, 200, 40))]),
    el("TextField", "Message", (0, 700, 300, 40), value="Where is my order QA-1?"),
    el("XCUIElementTypeButton", "Send", (310, 700, 70, 40)),
    el("XCUIElementTypeStaticText", "Two\nlines", (0, 200, 200, 40)),
    el("XCUIElementTypeStaticText", "hidden", (0, 300, 200, 40), visible="0"),
    el("XCUIElementTypeStaticText", "flat", (0, 400, 0, 0)),
    el("XCUIElementTypeIcon", "", (0, 500, 50, 50), name="menu"),
])


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class QuietProbes:
    """The iOS diagnosis's probes with nothing to read (no Xcode, no log): every finding unknown, but the device
    seen, so these tests never run a real command."""

    def __init__(self, cfg):
        self.cfg = cfg

    def devicectl_devices(self):
        return {"result": {"devices": [{"identifier": "X", "hardwareProperties": {"udid": "UDID-1",
                                                                                 "marketingName": "iPhone"},
                                        "connectionProperties": {"transportType": "wired", "pairingState": "paired"},
                                        "deviceProperties": {"osVersionNumber": "26.7.1"}}]}}

    def lock_state(self, udid):
        return None

    def usbmux(self):
        return None

    def port_open(self, host, port):
        return False

    def iproxy_running(self):
        return True

    def wda_session(self):
        return {"_error": "not tried"}

    def wda_status(self):
        return {"_error": "no answer"}

    def profiles(self):
        return None

    def identities(self):
        return None

    def xcode_accounts(self):
        return None

    def wda_log(self):
        return None


class FakeWda:
    """Canned WDA answers, in the shape WDA uses; every call is recorded."""

    def __init__(self, bundle=APP, tree=HOME, focused=True, status=None):
        self.bundle, self.tree, self.is_focused = bundle, tree, focused
        self.status = status or {"ready": True, "os": {"version": "26.7.1"}, "build": {"version": "16.17.6"}}
        self.calls = []

    def __call__(self, cfg, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        if path == "/status":
            return {"value": self.status, "sessionId": None}
        if path == "/wda/activeAppInfo":
            return {"value": {"bundleId": self.bundle}}
        if path == "/session":
            return {"value": {"sessionId": SID}, "sessionId": SID}
        if path == "/source?format=json":
            if self.is_focused:   # the keyboard is up
                return {"value": {**self.tree, "children": [*self.tree["children"],
                                                             el("Keyboard", "", (0, 600, 390, 244))]}}
            return {"value": self.tree}
        if path == "/screenshot":
            return {"value": base64.b64encode(PNG).decode()}
        if path.endswith("/window/size"):
            return {"value": {"width": 390, "height": 844}}
        return {"value": None}

    def paths(self):
        return [(m, p) for m, p, _ in self.calls]

    def posts(self, suffix):
        return [b for m, p, b in self.calls if m == "POST" and p.endswith(suffix)]


class Platform(unittest.TestCase):
    def test_flag_then_environment_then_yaml_then_android(self):
        m = {"platform": "ios"}
        self.assertEqual(common.mobile_platform(m, environ={}), "ios")
        self.assertEqual(common.mobile_platform(m, environ={"QA_MOBILE_PLATFORM": "android"}), "android")
        self.assertEqual(common.mobile_platform(m, "android", {"QA_MOBILE_PLATFORM": "ios"}), "android")
        self.assertEqual(common.mobile_platform({}, environ={}), "android")
        self.assertEqual(common.mobile_platform({}, environ={"QA_MOBILE_PLATFORM": "  "}), "android")

    def test_an_unknown_platform_names_where_it_came_from(self):
        with self.assertRaisesRegex(ValueError, "mobile.platform 'windows'"):
            common.mobile_platform({"platform": "windows"}, environ={})
        with self.assertRaisesRegex(ValueError, "QA_MOBILE_PLATFORM"):
            common.mobile_platform({}, environ={"QA_MOBILE_PLATFORM": "tvos"})


class Commands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: ios\n  app_id: com.demoshop.android\n  send_label: Send\n"
                    "  ios:\n    app_id: com.demoshop.app\n    udid: UDID-1\n    input: assisted\n"
                    "  evidence_batch: batch_ios\n")
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write("SHOP_P=fictional-password-9\n")
        keep = {k: v for k, v in os.environ.items()
                if k not in ("QA_MOBILE_SERIAL", "QA_MOBILE_INPUT", "QA_MOBILE_PLATFORM", "QA_WDA_URL")}
        self.environ = mock.patch.dict(os.environ, keep, clear=True)
        self.environ.start()

    def tearDown(self):
        self.environ.stop()
        self.tmp.cleanup()

    def run_it(self, fake, *args):
        out, err = io.StringIO(), io.StringIO()
        clock = Clock()
        with mock.patch.object(wda, "request", fake), mock.patch.object(wda, "installed_version",
                                                                         return_value="1.0.0 (34)"), \
                mock.patch.object(dv, "ios_probes", QuietProbes), \
                mock.patch.object(dv.time, "sleep", clock.sleep), mock.patch.object(dv.time, "monotonic", clock.now), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dv.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    # configuration
    def test_the_ios_section_gives_the_bundle_the_udid_and_the_default_url(self):
        cfg = dv.configuration(self.ws, None)
        self.assertEqual((cfg["platform"], cfg["app_id"], cfg["serial"], cfg["input"], cfg["wda_url"]),
                         ("ios", "com.demoshop.app", "UDID-1", "wda", "http://localhost:8100"))

    def test_url_from_the_environment_and_the_bundle_falls_back_to_mobile_app_id(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: ios\n  app_id: com.demoshop.same\n  ios: {wda_url: 'http://10.0.0.5:8100'}\n")
        self.assertEqual(dv.configuration(self.ws, None)["wda_url"], "http://10.0.0.5:8100")
        with mock.patch.dict(os.environ, {"QA_WDA_URL": "http://127.0.0.1:9000"}):
            cfg = dv.configuration(self.ws, "UDID-2")
        self.assertEqual((cfg["app_id"], cfg["serial"], cfg["wda_url"]),
                         ("com.demoshop.same", "UDID-2", "http://127.0.0.1:9000"))

    def test_the_platform_flag_overrides_the_yaml(self):
        self.assertEqual(dv.configuration(self.ws, None, None, "android")["platform"], "android")

    def test_a_bad_platform_is_exit_2_and_no_bundle_is_exit_2(self):
        with mock.patch.dict(os.environ, {"QA_MOBILE_PLATFORM": "tvos"}):
            rc, _, err = self.run_it(FakeWda(), "dump")
        self.assertEqual(rc, 2)
        self.assertIn("QA_MOBILE_PLATFORM", err)
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: ios\n  ios: {udid: U}\n")
        rc, _, err = self.run_it(FakeWda(), "dump")
        self.assertEqual(rc, 2)
        self.assertIn("mobile.ios.app_id", err)

    # reading
    def test_dump_lists_visible_elements_once_with_text_value_or_name(self):
        rc, out, _ = self.run_it(FakeWda(), "dump")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(),
                         ["[0,0][390,844] Demo Shop",
                          "[0,50][390,90] Demo Shop",
                          "[0,100][200,140] Cart",
                          "[0,700][300,740] Where is my order QA-1?",
                          "[310,700][380,740] Send",
                          "[0,200][200,240] Two / lines",
                          "[0,500][50,550] menu"])

    def test_a_refused_foreground_reads_nothing_and_opens_no_session(self):
        fake = FakeWda(bundle="com.example.private")
        for cmd in (["dump"], ["tap", "Cart"], ["shot", "x"], ["swipe", "1", "2", "3", "4"], ["back"], ["clear"],
                    ["type", "hi"]):
            rc, out, err = self.run_it(fake, *cmd)
            self.assertEqual((rc, out), (3, ""), cmd)
            self.assertIn("com.example.private", err)
        self.assertNotIn(("GET", "/source?format=json"), fake.paths())
        self.assertNotIn(("POST", "/session"), fake.paths())
        self.assertNotIn(("GET", "/screenshot"), fake.paths())

    def test_springboard_is_never_read_not_even_for_dump_or_tap(self):
        """A notification banner or the app switcher is SpringBoard: the person's own content (30.10.2026)."""
        fake = FakeWda(bundle="com.apple.springboard")
        for cmd in (["dump"], ["tap", "Cart"], ["shot", "x"], ["swipe", "1", "2", "3", "4"], ["back"]):
            rc, out, err = self.run_it(fake, *cmd)
            self.assertEqual((rc, out), (3, ""), cmd)
            self.assertIn("never read", err)
        self.assertNotIn(("GET", "/source?format=json"), fake.paths())
        self.assertNotIn(("POST", "/session"), fake.paths())

    # acting
    def test_tap_by_text_uses_the_centre_after_the_guard(self):
        fake = FakeWda()
        rc, out, _ = self.run_it(fake, "tap", "Cart")
        self.assertEqual(rc, 0)
        self.assertIn("tapped: [0,100][200,140] Cart", out)
        self.assertEqual(fake.posts("/wda/tap"), [{"x": 100, "y": 120}])
        order = fake.paths()
        self.assertLess(order.index(("GET", "/wda/activeAppInfo")), order.index(("POST", "/session")))

    def test_session_opens_once_without_a_bundle(self):
        fake = FakeWda()
        self.run_it(fake, "tap", "--xy", "5", "6")
        self.assertEqual(fake.posts("/session"), [{"capabilities": {"alwaysMatch": {}}}])

    def test_swipe_in_seconds_and_back_is_an_edge_swipe(self):
        fake = FakeWda()
        self.assertEqual(self.run_it(fake, "swipe", "10", "700", "10", "200", "--ms", "400")[0], 0)
        self.assertEqual(fake.posts("/wda/dragfromtoforduration"),
                         [{"fromX": 10, "fromY": 700, "toX": 10, "toY": 200, "duration": 0.4}])
        fake = FakeWda()
        rc, out, _ = self.run_it(fake, "back")
        self.assertEqual((rc, out), (0, "back\n"))
        self.assertEqual(fake.posts("/wda/dragfromtoforduration"),
                         [{"fromX": 2, "fromY": 422, "toX": 292, "toY": 422, "duration": 0.25}])

    def test_hold_is_a_touch_and_hold_in_seconds_after_the_guard(self):
        fake = FakeWda()
        rc, out, _ = self.run_it(fake, "hold", "Cart", "--ms", "1500")
        self.assertEqual(rc, 0)
        self.assertIn("held: [0,100][200,140] Cart for 1500 ms", out)
        self.assertEqual(fake.posts("/wda/touchAndHold"), [{"x": 100, "y": 120, "duration": 1.5}])
        rc, _, err = self.run_it(FakeWda(bundle="com.example.private"), "hold", "--xy", "1", "2")
        self.assertEqual(rc, 3)
        self.assertIn("hold refused: the foreground is 'com.example.private'", err)
        self.assertEqual(self.run_it(FakeWda(), "hold")[0], 2)

    def test_type_goes_to_the_focused_field_in_chunks_and_any_unicode(self):
        fake = FakeWda()
        rc, out, _ = self.run_it(fake, "type", "Olá 🙂" + "x" * 300)
        self.assertEqual(rc, 0)
        self.assertIn("wda keys, 2 chunks", out)
        keys = fake.posts("/wda/keys")
        self.assertEqual("".join("".join(k["value"]) for k in keys), "Olá 🙂" + "x" * 300)
        self.assertEqual([len(k["value"]) for k in keys], [300, 5])

    def test_type_with_a_field_taps_it_first_and_nothing_focused_is_exit_4(self):
        fake = FakeWda()
        self.assertEqual(self.run_it(fake, "type", "hi", "--field", "Where is my order")[0], 0)
        self.assertEqual(fake.posts("/wda/tap"), [{"x": 150, "y": 720}])
        fake = FakeWda(focused=False)       # the one text field is tapped, but the keyboard never comes up
        rc, _, err = self.run_it(fake, "type", "hi")
        self.assertEqual(rc, 4)
        self.assertIn("did not take the focus", err)
        self.assertIn("outcome: nothing typed", err)
        self.assertEqual(fake.posts("/wda/keys"), [])

    def test_type_refuses_a_env_value_and_a_via_route(self):
        fake = FakeWda()
        rc, _, err = self.run_it(fake, "type", "my fictional-password-9")
        self.assertEqual(rc, 2)
        self.assertNotIn("fictional-password-9", err)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.run_it(FakeWda(), "type", "hi", "--via", "adb")[0], 2)

    def test_clear_empties_the_focused_element(self):
        fake = FakeWda()
        rc, out, _ = self.run_it(fake, "clear")
        self.assertEqual((rc, out), (0, "cleared (wda clear)\n"))
        self.assertEqual(fake.posts("/wda/keys"), [{"value": ["\b"] * 250}])

    def test_shot_writes_the_png_to_the_evidence_folder(self):
        rc, out, _ = self.run_it(FakeWda(), "shot", "home")
        self.assertEqual(rc, 0)
        path = os.path.join(self.ws, "evidence", "batch_ios", "home.png")
        self.assertIn(path, out)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), PNG)

    def test_send_types_taps_send_and_prints_what_is_new(self):
        sent = el("XCUIElementTypeApplication", "Demo Shop", (0, 0, 390, 844), children=[
            el("XCUIElementTypeStaticText", "Where is my order QA-1?", (100, 400, 280, 40)),
            el("XCUIElementTypeStaticText", "Your order ships tomorrow.", (0, 460, 300, 40)),
            el("XCUIElementTypeButton", "Send", (310, 700, 70, 40))])
        fake = FakeWda()
        screens = [HOME, sent, sent]
        real = fake.__call__

        def swap(cfg, method, path, body=None, timeout=30):
            if path == "/source?format=json" and len(screens) > 1:
                fake.tree = screens.pop(0)
            return real(cfg, method, path, body, timeout)
        rc, out, _ = self.run_it(swap, "send", "Where is my order QA-1?", "--timeout", "60")
        self.assertEqual(rc, 0)
        self.assertIn("Your order ships tomorrow.", out)
        self.assertEqual(fake.posts("/wda/tap")[-1], {"x": 345, "y": 720})

    def test_launch_opens_the_bundle_and_restart_terminates_it_first(self):
        fake = FakeWda()
        rc, out, _ = self.run_it(fake, "launch", "--restart")
        self.assertEqual(rc, 0)
        self.assertIn("launched: com.demoshop.app", out)
        self.assertEqual(fake.posts("/wda/apps/terminate"), [{"bundleId": APP}])
        self.assertEqual(fake.posts("/session")[-1], {"capabilities": {"alwaysMatch": {"bundleId": APP}}})

    def test_link_is_not_available_and_never_prints_the_url(self):
        rc, out, err = self.run_it(FakeWda(), "link", "https://x.test/t?token=SECRET")
        self.assertEqual(rc, 2)
        self.assertNotIn("SECRET", out + err)

    def test_check_reports_os_wda_foreground_and_app_version_without_reading_the_screen(self):
        fake = FakeWda(bundle="com.apple.Preferences")
        rc, out, _ = self.run_it(fake, "check")
        self.assertEqual(rc, 0)
        self.assertIn("iOS 26.7.1", out)
        self.assertIn("WebDriverAgent 16.17.6 ready", out)
        self.assertIn("foreground: com.apple.Preferences", out)
        self.assertIn("app: com.demoshop.app 1.0.0 (34)", out)
        self.assertNotIn(("GET", "/source?format=json"), fake.paths())

    def test_check_with_wda_not_ready_is_exit_3(self):
        rc, _, err = self.run_it(FakeWda(status={"ready": False, "message": "starting"}), "check")
        self.assertEqual(rc, 3)
        self.assertIn("not ready", err)

    def test_check_with_wda_down_is_a_report_with_ready_and_next_lines(self):
        def down(cfg, method, path, body=None, timeout=30):
            raise wda.Unreachable(f"WebDriverAgent did not answer at {cfg['wda_url']} ({wda.WDA_HELP})")
        rc, out, err = self.run_it(down, "check")
        self.assertEqual(rc, 3)
        self.assertIn("device: not reachable", out)
        self.assertIn("ready: NOT READY", out)
        # the device is seen by devicectl: never "connect the cable"; the main block and the next action come first
        self.assertTrue(out.startswith("blocked by: WebDriverAgent does not answer"), out)
        self.assertIn("next: start WebDriverAgent", out)
        self.assertNotIn("cable", out)
        self.assertIn("NO TARGET", err)

    def test_check_compares_the_observed_build_with_the_intended_one(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: ios\n  ios:\n    app_id: com.demoshop.app\n    udid: UDID-1\n"
                    "    build: {version: '1.0.0 (35)'}\n")
        rc, out, _ = self.run_it(FakeWda(), "check")
        self.assertEqual(rc, 0)
        self.assertIn("build: observed com.demoshop.app 1.0.0 (34), intended 1.0.0 (35): DIFFERS", out)
        self.assertIn("ready: yes", out)

    def test_wda_down_is_exit_3_with_the_help(self):
        def down(cfg, method, path, body=None, timeout=30):
            raise wda.Unreachable(f"WebDriverAgent did not answer at {cfg['wda_url']} ({wda.WDA_HELP})")
        rc, _, err = self.run_it(down, "dump")
        self.assertEqual(rc, 3)
        self.assertIn("iproxy", err)


class Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list] = []

    def log_message(self, *a):
        pass

    def reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/status":
            self.reply(200, {"value": {"ready": True}, "sessionId": None})
        elif self.path == "/gone":
            self.reply(404, {"value": {"error": "no such element", "message": "nothing"}})
        elif self.path == "/boom":
            self.reply(500, {"value": {"error": "unknown error", "message": "x" * 500}})
        else:
            self.reply(200, "not a dict")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Handler.seen.append((self.path, json.loads(self.rfile.read(n))))
        self.reply(500, {"value": {"error": "invalid argument", "message": "bad"}})


class Transport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.cfg = {"wda_url": f"http://127.0.0.1:{cls.server.server_port}/"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_a_get_returns_the_json(self):
        self.assertEqual(wda.status(self.cfg), {"ready": True})

    def test_no_such_element_is_no_element_and_other_errors_are_unreachable_and_short(self):
        with self.assertRaises(wda.NoElement):
            wda.request(self.cfg, "GET", "/gone")
        with self.assertRaises(wda.Unreachable) as cm:
            wda.request(self.cfg, "GET", "/boom")
        self.assertLess(len(str(cm.exception)), 220)

    def test_an_error_never_carries_the_request_body(self):
        with self.assertRaises(wda.Unreachable) as cm:
            wda.request(self.cfg, "POST", "/session/S/wda/keys", {"value": list("SECRET-TEXT")})
        self.assertNotIn("SECRET", str(cm.exception))
        self.assertEqual(Handler.seen[-1][1], {"value": list("SECRET-TEXT")})

    def test_nothing_listening_is_unreachable_with_the_help(self):
        with self.assertRaisesRegex(wda.Unreachable, "iproxy"):
            wda.request({"wda_url": "http://127.0.0.1:9"}, "GET", "/status", timeout=2)

    def test_status_that_is_not_an_object_is_unreachable(self):
        with self.assertRaises(wda.Unreachable):
            wda.status({"wda_url": self.cfg["wda_url"] + "other"})


if __name__ == "__main__":
    unittest.main()

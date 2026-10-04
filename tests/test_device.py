"""scripts/device.py (technique-mobile.md §4.1; plan 9.1): reading the uiautomator XML, exact-before-contains
matching, the foreground guard, "stable screen" detection, `send` printing only what is new, the routes of `type`
and `link` never printing the URL. No device nor Maestro: the single adb function is replaced by a fake device.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import functools
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import device as dv

APP = "com.demoshop.app/com.demoshop.app.MainActivity"
DIALOG = "com.google.android.permissioncontroller/com.android.permissioncontroller.GrantPermissionsActivity"
OTHER = "com.example.mail/com.example.mail.Inbox"
PNG = b"\x89PNG\r\n\x1a\nfictional"


def screen(*nodes) -> str:
    """A uiautomator dump: each node is (text, content-desc, bounds), already escaped as uiautomator writes it."""
    body = "".join(f'<node index="{i}" text="{t}" resource-id="" class="android.widget.TextView" '
                   f'package="com.demoshop.app" content-desc="{d}" bounds="{b}" />' for i, (t, d, b) in enumerate(nodes))
    return ("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
            f'<node index="0" text="" content-desc="" bounds="[0,0][1080,2400]">{body}</node></hierarchy>')


HOME = screen(("Demo Shop", "", "[0,0][1080,120]"),
              ("Cart", "", "[0,200][540,300]"),
              ("Add to cart", "", "[0,400][540,500]"),
              ("", "Search products", "[900,0][1080,120]"),
              ("hidden", "", "[0,0][0,0]"))
CHAT_TYPED = screen(("Demo Shop", "", "[0,0][1080,120]"),
                    ("Hello", "", "[0,200][800,300]"),
                    ("Where is my order QA-1?", "", "[0,2200][900,2300]"),
                    ("", "Send", "[950,2200][1080,2300]"))
CHAT_SENT = screen(("Demo Shop", "", "[0,0][1080,120]"),
                   ("Hello", "", "[0,200][800,300]"),
                   ("Where is my order QA-1?", "", "[200,400][1080,500]"),
                   ("Your order", "", "[0,600][800,700]"),
                   ("Type a message", "", "[0,2200][900,2300]"),
                   ("", "Send", "[950,2200][1080,2300]"))
CHAT_ANSWERED = CHAT_SENT.replace('text="Your order"', 'text="Your order ships tomorrow.&#10;Track it in Orders."')


class FakeDevice:
    """Canned adb answers; each dump takes the next screen of the list (the last one repeats)."""

    def __init__(self, dumps=(HOME,), focus=APP, devices="emulator-5554\tdevice\n"):
        self.dumps, self.focus, self.devices = list(dumps), focus, devices
        self.calls = []

    def __call__(self, serial, *args, binary=False, timeout=60):
        self.calls.append(args)
        if args == ("devices",):
            return "List of devices attached\n" + self.devices + "\n"
        if args[:3] == ("shell", "dumpsys", "window"):
            return f"  mCurrentFocus=Window{{4f1c2d u0 {self.focus}}}\n  mFocusedApp=null\n"
        if args[:3] == ("shell", "uiautomator", "dump"):
            return "UI hierchary dumped to: /sdcard/qa-device-dump.xml\n"
        if args[:2] == ("exec-out", "cat"):
            return self.dumps.pop(0) if len(self.dumps) > 1 else self.dumps[0]
        if args[:2] == ("exec-out", "screencap"):
            return PNG
        if args[:2] == ("shell", "getprop"):
            return {"ro.build.version.release": "14\n", "ro.build.version.sdk": "34\n",
                    "ro.product.model": "sdk_gphone64_x86_64\n"}[args[2]]
        if args[:3] == ("shell", "dumpsys", "package"):
            return ("Packages:\n  Package [com.demoshop.app] (1a2b):\n    versionCode=140 minSdk=26\n"
                    "    versionName=1.4.0\n" if args[3] == "com.demoshop.app" else "")
        return ""

    def taps(self):
        return [a[3:] for a in self.calls if a[:3] == ("shell", "input", "tap")]

    def of(self, *prefix):
        return [a for a in self.calls if a[:len(prefix)] == prefix]


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Parsing(unittest.TestCase):
    def test_text_content_desc_entities_and_zero_size(self):
        xml = screen(("Tom &amp; Jerry &quot;live&quot; &lt;b&gt;", "", "[0,0][100,50]"),
                     ("", "Open menu", "[0,60][100,110]"),
                     ("Line one&#10;Line two", "", "[0,120][100,170]"),
                     ("Café", "ignored when there is text", "[0,180][100,230]"),
                     ("off screen", "", "[0,0][0,0]"),
                     ("", "", "[0,240][100,290]"),
                     ("bad&#1;char", "", "[0,300][100,350]"))
        self.assertEqual([n.line() for n in dv.parse_nodes(xml)],
                         ['[0,0][100,50] Tom & Jerry "live" <b>',
                          "[0,60][100,110] Open menu",
                          "[0,120][100,170] Line one / Line two",
                          "[0,180][100,230] Café",
                          "[0,300][100,350] badchar"])

    def test_the_dump_is_retried_when_it_has_no_hierarchy(self):
        fake = FakeDevice(dumps=["", "ERROR: could not get idle state.", HOME])
        with mock.patch.object(dv, "adb", fake), mock.patch.object(dv.time, "sleep"):
            xml = dv.dump_xml("emulator-5554")
        self.assertTrue(xml.startswith("<?xml"))
        self.assertEqual(len(fake.of("shell", "uiautomator", "dump")), 3)
        self.assertEqual(len(fake.of("shell", "rm")), 1)   # the dump does not stay on the device

    def test_three_empty_dumps_are_no_target(self):
        with mock.patch.object(dv, "adb", FakeDevice(dumps=[""])), mock.patch.object(dv.time, "sleep"), \
                self.assertRaises(dv.NoTarget):
            dv.dump_xml("emulator-5554")


class Matching(unittest.TestCase):
    nodes = dv.parse_nodes(screen(("Add to cart", "", "[0,0][100,100]"),
                                  ("Cart", "", "[0,200][100,300]"),
                                  ("Cart (2)", "", "[0,400][100,500]"),
                                  ("cart", "", "[0,600][100,700]")))

    def test_exact_before_contains_case_insensitive(self):
        self.assertEqual(dv.find(self.nodes, "CART").text, "Cart")
        self.assertEqual(dv.find(self.nodes, "cart", 1).text, "cart")        # index among the exact ones
        self.assertEqual(dv.find(self.nodes, "(2)").text, "Cart (2)")        # contains when no exact
        self.assertEqual(dv.find(self.nodes, "to c").text, "Add to cart")

    def test_not_found(self):
        with self.assertRaises(dv.NotFound):
            dv.find(self.nodes, "Checkout")
        with self.assertRaises(dv.NotFound):
            dv.find(self.nodes, "cart", 2)


class StableScreen(unittest.TestCase):
    def test_changes_then_stable(self):
        clock = Clock()
        readings = iter(["a", "ab", "abc", "abc", "abc", "abc"])
        last, stable = dv.wait_stable(lambda: next(readings), 200, clock=clock.now, sleep=clock.sleep)
        self.assertEqual((last, stable), ("abc", True))
        self.assertEqual(clock.t, 15)

    def test_identical_early_is_not_enough(self):
        # two identical readings before 15 s (the answer has not started) do not end the wait
        clock = Clock()
        readings = iter(["x", "x", "x", "x"])
        _, stable = dv.wait_stable(lambda: next(readings), 200, clock=clock.now, sleep=clock.sleep)
        self.assertTrue(stable)
        self.assertEqual(clock.t, 15)

    def test_never_stable_is_a_timeout(self):
        clock = Clock()
        counter = iter(range(1000))
        _, stable = dv.wait_stable(lambda: next(counter), 30, clock=clock.now, sleep=clock.sleep)
        self.assertFalse(stable)
        self.assertEqual(clock.t, 30)

    def test_only_the_lines_after_the_message(self):
        lines = ["[0,0][1,1] Demo Shop", "[0,1][1,2] Where is my order QA-1?", "[0,2][1,3] Old answer",
                 "[0,3][1,4] Where is my order QA-1?", "[0,4][1,5] New answer", "[0,5][1,6] Send"]
        self.assertEqual(dv.after_message(lines, "where is my order QA-1?", {"send"}),
                         (["[0,4][1,5] New answer"], True))
        self.assertEqual(dv.after_message(lines, "not on screen", {"demo shop", "send"})[1], False)


class Commands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: android\n  app_id: com.demoshop.app\n  send_label: Send\n"
                    "  ignore_labels: [Demo Shop, Send, Type a message]\n  evidence_batch: batch_20260928_01\n")
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write("SHOP_U=ana\nSHOP_P=fictional-password-9\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, fake, *args, clock=None):
        out, err = io.StringIO(), io.StringIO()
        clock = clock or Clock()
        stable = functools.partial(dv.wait_stable, clock=clock.now, sleep=clock.sleep)
        with mock.patch.object(dv, "adb", fake), mock.patch.object(dv, "wait_stable", stable), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dv.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    # --- target and configuration

    def test_check(self):
        rc, out, _ = self.run_it(FakeDevice(), "check")
        self.assertEqual(rc, 0)
        self.assertIn("Android 14 (SDK 34)", out)
        self.assertIn("app: com.demoshop.app 1.4.0 (code 140)", out)
        self.assertIn(f"foreground: {APP}", out)

    def test_no_device_or_app_not_installed_is_3(self):
        self.assertEqual(self.run_it(FakeDevice(devices=""), "check")[0], 3)
        self.assertEqual(self.run_it(FakeDevice(devices="emulator-5554\toffline\n"), "dump")[0], 3)
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  app_id: com.demoshop.other\n")
        rc, _, err = self.run_it(FakeDevice(), "check")
        self.assertEqual(rc, 3)
        self.assertIn("not installed", err)

    def test_configuration_problems_are_2(self):
        rc, _, err = self.run_it(FakeDevice(devices="emulator-5554\tdevice\nR58M\tdevice\n"), "dump")
        self.assertEqual(rc, 2)
        self.assertIn("--serial", err)
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: android\n")
        self.assertEqual(self.run_it(FakeDevice(), "check")[0], 2)

    def test_serial_from_the_command_line(self):
        fake = FakeDevice(devices="emulator-5554\tdevice\nR58M\tdevice\n")
        self.assertEqual(self.run_it(fake, "--serial", "R58M", "dump")[0], 0)
        self.assertEqual(self.run_it(fake, "dump", "--serial", "R58M")[0], 0)

    # --- the foreground guard

    def test_dump_prints_one_line_per_node(self):
        rc, out, _ = self.run_it(FakeDevice(), "dump")
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), ["[0,0][1080,120] Demo Shop", "[0,200][540,300] Cart",
                                            "[0,400][540,500] Add to cart", "[900,0][1080,120] Search products"])

    def test_another_app_in_the_foreground_is_refused(self):
        for command in (["dump"], ["shot", "home"], ["tap", "Cart"], ["send", "hi"], ["type", "hi"]):
            with self.subTest(command=command):
                fake = FakeDevice(focus=OTHER)
                rc, out, err = self.run_it(fake, *command)
                self.assertEqual((rc, out), (3, ""))
                self.assertIn("com.example.mail", err)          # says what the foreground is
                self.assertEqual(fake.of("exec-out"), [])       # nothing read from the screen
                self.assertEqual(fake.taps(), [])

    def test_a_permission_dialog_can_be_read_and_tapped_but_never_captured(self):
        dialog = screen(("Allow Demo Shop to send you notifications?", "", "[60,900][1020,1000]"),
                        ("Allow", "", "[60,1100][1020,1200]"), ("Don't allow", "", "[60,1250][1020,1350]"))
        self.assertEqual(self.run_it(FakeDevice([dialog], focus=DIALOG), "dump")[0], 0)
        fake = FakeDevice([dialog], focus=DIALOG)
        self.assertEqual(self.run_it(fake, "tap", "Allow")[0], 0)
        self.assertEqual(fake.taps(), [("540", "1150")])
        fake = FakeDevice([dialog], focus=DIALOG)
        rc, _, err = self.run_it(fake, "shot", "dialog")
        self.assertEqual(rc, 3)
        self.assertIn("never captured", err)
        self.assertEqual(fake.of("exec-out", "screencap"), [])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "evidence")))

    def test_nothing_focused_is_refused(self):
        fake = FakeDevice()
        fake.focus = ""
        self.assertEqual(self.run_it(fake, "dump")[0], 3)

    # --- tap and shot

    def test_tap_exact_then_contains_index_and_xy(self):
        fake = FakeDevice()
        self.assertEqual(self.run_it(fake, "tap", "cart")[0], 0)           # "Cart", not "Add to cart"
        self.assertEqual(self.run_it(fake, "tap", "to CART")[0], 0)
        self.assertEqual(self.run_it(fake, "tap", "Search")[0], 0)         # content-desc
        self.assertEqual(self.run_it(fake, "tap", "--xy", "10", "20")[0], 0)
        self.assertEqual(fake.taps(), [("270", "250"), ("270", "450"), ("990", "60"), ("10", "20")])
        fake = FakeDevice()
        self.assertEqual(self.run_it(fake, "tap", "art", "--index", "1")[0], 0)
        self.assertEqual(fake.taps(), [("270", "450")])

    def test_tap_not_found_is_4(self):
        fake = FakeDevice()
        rc, _, err = self.run_it(fake, "tap", "Checkout")
        self.assertEqual(rc, 4)
        self.assertIn("Checkout", err)
        self.assertEqual(fake.taps(), [])
        self.assertEqual(self.run_it(FakeDevice(), "tap", "Cart", "--index", "5")[0], 4)

    def test_shot_goes_to_the_evidence_of_the_batch(self):
        rc, out, _ = self.run_it(FakeDevice(), "shot", "TC-MOB-001_home")
        self.assertEqual(rc, 0)
        path = os.path.join(self.ws, "evidence", "batch_20260928_01", "TC-MOB-001_home.png")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), PNG)
        self.assertIn(path, out)
        self.run_it(FakeDevice(), "dump", "--capture", "home", "--batch", "b2")
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "evidence", "b2", "home.png")))
        self.assertEqual(self.run_it(FakeDevice(), "shot", "../outside")[0], 2)

    # --- type

    def test_type_ascii_through_adb_escaped_for_the_device_shell(self):
        fake = FakeDevice()
        with mock.patch.object(dv, "run_maestro") as maestro:
            self.assertEqual(self.run_it(fake, "type", "Hi (all) & 'you' $5?")[0], 0)
        maestro.assert_not_called()
        self.assertEqual(fake.of("shell", "input", "text"),
                         [("shell", "input", "text", "Hi%s\\(all\\)%s\\&%s\\'you\\'%s\\$5\\?")])

    def maestro(self, *answers):
        """A fake Maestro: records each command, flow and extra environment; answers in turn (the last repeats)."""
        seen = []
        answers = list(answers) or [(0, "")]

        def run(cmd, env=None):
            with open(cmd[cmd.index("test") + 1], encoding="utf-8") as f:
                seen.append({"cmd": cmd, "flow": f.read(), "env": env or {}})
            code, out = answers.pop(0) if len(answers) > 1 else answers[0]
            return mock.Mock(returncode=code, stdout=out, stderr="")
        return seen, run

    def test_type_unicode_through_maestro_with_the_text_in_the_utf8_flow_never_on_the_command_line(self):
        fake = FakeDevice()
        seen, run = self.maestro()
        with mock.patch.object(dv.shutil, "which", return_value="maestro"), \
                mock.patch.object(dv, "run_maestro", side_effect=run):
            rc, out, _ = self.run_it(fake, "type", "Olá, café? 🙂", "--field", "Search")
        self.assertEqual(rc, 0)
        self.assertIn("maestro inputText", out)
        cmd = seen[0]["cmd"]
        self.assertIsInstance(cmd, list)                                   # an argument list, no shell
        self.assertEqual(cmd[:3], ["maestro", "--device", "emulator-5554"])
        self.assertNotIn("-e", cmd)
        self.assertFalse(any("café" in c for c in cmd))                    # never on the command line
        self.assertIn('- inputText: "Olá, café? 🙂"', seen[0]["flow"])     # written as UTF-8, not escaped
        self.assertEqual(fake.taps(), [("990", "60")])                     # the field focused first
        self.assertEqual(fake.of("shell", "input", "text"), [])

    def test_a_long_text_goes_in_chunks(self):
        fake = FakeDevice()
        text = "Tell me about order QA-1 " * 40                            # 1 000 ASCII characters
        rc, out, _ = self.run_it(fake, "type", text)
        self.assertEqual(rc, 0)
        self.assertIn("4 chunks", out)
        calls = fake.of("shell", "input", "text")
        self.assertEqual(len(calls), 4)
        self.assertEqual("".join(c[3] for c in calls), dv.input_text_arg(text[:300]) + dv.input_text_arg(
            text[300:600]) + dv.input_text_arg(text[600:900]) + dv.input_text_arg(text[900:]))
        seen, run = self.maestro()
        with mock.patch.object(dv.shutil, "which", return_value="maestro"), \
                mock.patch.object(dv, "run_maestro", side_effect=run):
            rc, out, _ = self.run_it(FakeDevice(), "type", "é" * 650)
        self.assertEqual(rc, 0)
        self.assertIn("3 steps", out)
        self.assertEqual(seen[0]["flow"].count("- inputText:"), 3)

    def test_type_from_a_utf8_file(self):
        path = os.path.join(self.ws, "message.txt")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("Olá 🙂 — obrigado\r\n")
        seen, run = self.maestro()
        with mock.patch.object(dv.shutil, "which", return_value="maestro"), \
                mock.patch.object(dv, "run_maestro", side_effect=run):
            rc, _, _ = self.run_it(FakeDevice(), "type", "--file", path)
        self.assertEqual(rc, 0)
        self.assertIn('- inputText: "Olá 🙂 — obrigado"\n', seen[0]["flow"])   # the final line break dropped
        self.assertEqual(self.run_it(FakeDevice(), "type", "hi", "--file", path)[0], 2)      # not both
        self.assertEqual(self.run_it(FakeDevice(), "type")[0], 2)
        self.assertEqual(self.run_it(FakeDevice(), "type", "--file", os.path.join(self.ws, "none.txt"))[0], 2)
        with open(path, "w", encoding="utf-8") as f:
            f.write("fictional-password-9")
        rc, out, err = self.run_it(FakeDevice(), "type", "--file", path)                       # still refused
        self.assertEqual(rc, 2)
        self.assertNotIn("fictional-password-9", out + err)

    def test_maestro_would_evaluate_a_dollar_brace_so_it_goes_by_environment(self):
        seen, run = self.maestro()
        with mock.patch.object(dv.shutil, "which", return_value="maestro"), \
                mock.patch.object(dv, "run_maestro", side_effect=run):
            self.run_it(FakeDevice(), "type", "preço ${total}")
        self.assertIn("- inputText: ${MAESTRO_QA_TEXT_1}", seen[0]["flow"])
        self.assertEqual(seen[0]["env"], {"MAESTRO_QA_TEXT_1": "preço ${total}"})

    def test_ime_not_active_is_retried_once_then_ascii_falls_back_to_adb(self):
        ime = (1, "java.io.IOException: Maestro IME is not active")
        seen, run = self.maestro(ime, (0, ""))
        fake = FakeDevice()
        with mock.patch.object(dv.shutil, "which", return_value="maestro"), \
                mock.patch.object(dv, "run_maestro", side_effect=run):
            rc, out, _ = self.run_it(fake, "type", "café", "--field", "Search")
        self.assertEqual((rc, len(seen)), (0, 2))
        self.assertEqual(fake.taps(), [("990", "60"), ("990", "60")])        # the field tapped again
        # twice not active: ASCII typed through adb, and said so
        seen, run = self.maestro(ime)
        fake = FakeDevice()
        with mock.patch.object(dv.shutil, "which", return_value="maestro"), \
                mock.patch.object(dv, "run_maestro", side_effect=run):
            rc, out, err = self.run_it(fake, "type", "hello there", "--via", "maestro")
        self.assertEqual((rc, len(seen)), (0, 2))
        self.assertIn("fallback: Maestro IME not active", out)
        self.assertIn("typed through adb instead", err)
        self.assertEqual(fake.of("shell", "input", "text"), [("shell", "input", "text", "hello%sthere")])
        # twice not active with non-ASCII text: no target
        seen, run = self.maestro(ime)
        with mock.patch.object(dv.shutil, "which", return_value="maestro"), \
                mock.patch.object(dv, "run_maestro", side_effect=run):
            rc, _, err = self.run_it(FakeDevice(), "type", "café")
        self.assertEqual((rc, len(seen)), (3, 2))
        self.assertIn("IME is not active", err)

    def test_via_adb_refuses_non_ascii(self):
        self.assertEqual(self.run_it(FakeDevice(), "type", "café", "--via", "adb")[0], 2)

    def test_type_refuses_a_value_of_the_env(self):
        for text in ("fictional-password-9", "my password is fictional-password-9 ok", "ana"):
            with self.subTest(text=text):
                fake = FakeDevice()
                with mock.patch.object(dv, "run_maestro") as maestro:
                    rc, out, err = self.run_it(fake, "type", text)
                self.assertEqual(rc, 2)
                self.assertIn("SHOP_", err)
                self.assertNotIn("fictional-password-9", out + err)
                self.assertEqual(fake.of("shell", "input"), [])
                maestro.assert_not_called()
        # a short value is only refused when it is the whole text ("ana" inside "banana" is not a credential)
        self.assertEqual(self.run_it(FakeDevice(), "type", "banana")[0], 0)

    # --- send

    def test_send_prints_only_what_is_new_after_the_message(self):
        fake = FakeDevice([CHAT_TYPED, CHAT_SENT, CHAT_ANSWERED])
        rc, out, _ = self.run_it(fake, "send", "Where is my order QA-1?", "--capture", "TC-SHOP-001_t1")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "[0,600][800,700] Your order ships tomorrow. / Track it in Orders.")
        self.assertTrue(lines[1].startswith("capture: "))
        self.assertEqual(len(lines), 2)                                    # no chrome, no earlier turns
        self.assertEqual(fake.of("shell", "input", "text"),
                         [("shell", "input", "text", "Where%sis%smy%sorder%sQA-1\\?")])
        self.assertEqual(fake.taps(), [("1015", "2250")])                  # the send control, by its content-desc
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "evidence", "batch_20260928_01",
                                                    "TC-SHOP-001_t1.png")))

    def test_send_timeout_is_3_with_the_last_reading(self):
        streaming = [CHAT_SENT.replace('text="Your order"', f'text="Your order {"." * i}"') for i in range(100)]
        fake = FakeDevice([CHAT_TYPED, *streaming])
        rc, out, err = self.run_it(fake, "send", "Where is my order QA-1?", "--timeout", "30")
        self.assertEqual(rc, 3)
        self.assertIn("did not settle in 30 s", err)
        self.assertIn("Your order", out)

    def test_send_without_a_send_control_is_4(self):
        fake = FakeDevice([HOME])
        self.assertEqual(self.run_it(fake, "send", "hello")[0], 4)
        self.assertEqual(fake.taps(), [])

    def test_send_does_not_tap_the_field_that_shows_the_message(self):
        # the typed message contains the send label; no exact "Send" control: the field is never tapped
        typed = screen(("please send the invoice", "", "[0,2200][900,2300]"))
        fake = FakeDevice([typed])
        self.assertEqual(self.run_it(fake, "send", "please send the invoice")[0], 4)
        self.assertEqual(fake.taps(), [])

    # --- link

    def test_link_unwraps_and_never_prints_the_url(self):
        target = "https://auth.demoshop.example/callback?code=fictional-one-time-42&next=app"
        wrapped = ("https://www.google.com/url?q=https%3A%2F%2Fauth.demoshop.example%2Fcallback%3Fcode%3D"
                   "fictional-one-time-42%26next%3Dapp&sa=D&source=editors")
        self.assertEqual(dv.unwrap(wrapped), target)
        self.assertEqual(dv.unwrap(target), target)
        for url in (wrapped, target):
            with self.subTest(url=url[:30]):
                fake = FakeDevice()
                rc, out, err = self.run_it(fake, "link", url)
                self.assertEqual(rc, 0)
                self.assertEqual(out, "auth.demoshop.example: opened\n")
                self.assertNotIn("fictional-one-time-42", out + err)
                am = fake.of("shell", "am", "start")
                self.assertEqual(len(am), 1)
                self.assertEqual(am[0][am[0].index("-d") + 1], "'" + target + "'")   # quoted for the device's sh
        self.assertFalse(os.path.exists(os.path.join(self.ws, "evidence")))

    def test_link_error_does_not_leak_the_url(self):
        fake = FakeDevice()
        original = fake.__call__

        def failing(serial, *args, **kw):
            if args[:3] == ("shell", "am", "start"):
                return ("Starting: Intent { act=android.intent.action.VIEW dat=https://auth.demoshop.example/... }\n"
                        "Error: Activity not started, unable to resolve Intent { dat=https://auth.demoshop.example/"
                        "callback?code=fictional-one-time-42 }\n")
            return original(serial, *args, **kw)

        rc, out, err = self.run_it(failing, "link", "https://auth.demoshop.example/callback?code=fictional-one-time-42",
                                   "--package", "com.android.chrome")
        self.assertEqual(rc, 3)
        self.assertNotIn("fictional-one-time-42", out + err)
        self.assertIn("auth.demoshop.example: not opened", err)


if __name__ == "__main__":
    unittest.main()

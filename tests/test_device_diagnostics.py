"""scripts/device.py, Android diagnostics (technique-mobile.md §1.3 and §4.1): why a check is not ready (one action per
case, as advice), never tapping on system UI by coordinates, personal data in a capture (categories and counts, never the
values), the notification permission and the captions of a capture. No device and no adb: the one adb function is
replaced by a fake that answers canned `adb devices` / `dumpsys` text. The fixtures are fictional.

python -m unittest tests.test_device_diagnostics   (from the root of qa-assistant)
"""

import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "scripts")]

import device as dv
from test_device import APP, CHAT_TYPED, HOME, OTHER, PNG, Commands, FakeDevice, screen

EMAIL = "qa@example.invalid"
PHONE = "+000 000 000 000"
SHADE = "NotificationShade"
DIALOG = "com.google.android.permissioncontroller/com.android.permissioncontroller.GrantPermissionsActivity"
DEV_SETTINGS = "com.android.settings/com.android.settings.Settings$DevelopmentSettingsDashboardActivity"
USB_PROMPT = "com.android.systemui/com.android.systemui.usb.UsbDebuggingActivity"

POWER_AWAKE = "Power Manager State:\n  mWakefulness=Awake\n  mWakefulnessChanging=false\n"
POWER_ASLEEP = "Power Manager State:\n  mWakefulness=Asleep\n"
POWER_DOZING = "Power Manager State:\n  mWakefulness=Dozing\n"
KEYGUARD_UP = "  mShowingLockscreen=true\n  isKeyguardShowing=true\n"
PERM_GRANTED = "    runtime permissions:\n      android.permission.POST_NOTIFICATIONS: granted=true, flags=[ USER_SET]\n"
PERM_DENIED = "    runtime permissions:\n      android.permission.POST_NOTIFICATIONS: granted=false, flags=[ USER_SET]\n"


def activity_text(component: str) -> str:
    return f"  mResumedActivity: ActivityRecord{{1a2b3c u0 {component} t42}}\n"


class DiagDevice(FakeDevice):
    """FakeDevice that also answers dumpsys power / activity, a configurable focus and permission text."""

    def __init__(self, dumps=(HOME,), power=POWER_AWAKE, window_extra="", activity="", package_extra="", **kw):
        super().__init__(dumps, **kw)
        self.power, self.window_extra, self.activity, self.package_extra = power, window_extra, activity, package_extra

    def __call__(self, serial, *args, binary=False, timeout=60):
        if args[:3] == ("shell", "dumpsys", "power"):
            self.calls.append(args)
            return self.power
        if args[:3] == ("shell", "dumpsys", "activity"):
            self.calls.append(args)
            return self.activity
        if args[:3] == ("shell", "dumpsys", "window"):
            self.calls.append(args)
            return super().__call__(serial, *args, binary=binary, timeout=timeout) + self.window_extra
        out = super().__call__(serial, *args, binary=binary, timeout=timeout)
        if args[:3] == ("shell", "dumpsys", "package") and out:
            out += self.package_extra
        return out

    def touched(self):
        """Every call that would act on the device: input, keyevents, adb server and settings changes."""
        return [a for a in self.calls if a[:2] == ("shell", "input") or a[:1] in (("kill-server",), ("start-server",),
                ("reconnect",)) or a[:3] in (("shell", "settings", "put"), ("shell", "pm", "grant"))
                or a[:3] == ("shell", "content", "call")]


class Devices(unittest.TestCase):
    def test_no_device_at_all(self):
        daemon = ("* daemon not running; starting now at tcp:5037\n"
                  "* daemon started successfully\nList of devices attached\n\n")
        for text in ("List of devices attached\n\n", "", daemon):
            d = dv.diagnose_devices(text)
            self.assertEqual((d.kind, d.serial), ("no-device", None), text)
            self.assertIn("USB debugging", d.action)
            self.assertIn("no Android device", d.message)

    def test_unauthorized_offline_and_other_states(self):
        d = dv.diagnose_devices("List of devices attached\nR58M\tunauthorized usb:1-1 transport_id:2\n")
        self.assertEqual((d.kind, d.serial), ("unauthorized", None))
        self.assertIn("unauthorized", d.message)
        self.assertIn("accept", d.action)
        d = dv.diagnose_devices("List of devices attached\nR58M\toffline\n")
        self.assertEqual(d.kind, "offline")
        self.assertIn("kill-server", d.action)
        self.assertIn("user's call", d.action)                      # advice: the tool does not run it
        self.assertEqual(dv.diagnose_devices("List of devices attached\nR58M\tauthorizing\n").kind, "offline")
        d = dv.diagnose_devices("List of devices attached\nR58M\tno permissions (user in group plugdev)\n")
        self.assertEqual(d.kind, "other")

    def test_several_ready_devices_and_one_ready_among_others(self):
        d = dv.diagnose_devices("List of devices attached\nA1\tdevice\nB2\tdevice\n")
        self.assertEqual((d.kind, d.serial), ("several", None))
        self.assertIn("--serial", d.action)
        self.assertIn("A1", d.message)
        d = dv.diagnose_devices("List of devices attached\nA1\tdevice product:x model:y\nB2\tunauthorized\n")
        self.assertEqual((d.kind, d.serial), ("ok", "A1"))

    def test_a_wanted_serial_is_ok_or_says_its_own_state(self):
        text = "List of devices attached\nA1\tdevice\nB2\tunauthorized\n"
        self.assertEqual(dv.diagnose_devices(text, "A1").serial, "A1")
        self.assertEqual(dv.diagnose_devices(text, "B2").kind, "unauthorized")
        d = dv.diagnose_devices(text, "C3")
        self.assertEqual(d.kind, "no-device")
        self.assertIn("device C3: not connected", d.message)

    def test_resolve_serial_raises_what_the_diagnosis_says(self):
        with mock.patch.object(dv, "adb", FakeDevice(devices="R58M\tunauthorized\n")), \
                self.assertRaises(dv.NoDevice) as ctx:
            dv.resolve_serial(None)
        self.assertEqual(ctx.exception.kind, "unauthorized")
        with mock.patch.object(dv, "adb", FakeDevice(devices="A1\tdevice\nB2\tdevice\n")), \
                self.assertRaises(dv.SeveralDevices):
            dv.resolve_serial(None)


class ScreenAndNotifications(unittest.TestCase):
    def test_screen_state(self):
        window = "  mCurrentFocus=Window{4f1c2d u0 com.demoshop.app/com.demoshop.app.MainActivity}\n"
        self.assertEqual(dv.parse_screen_state(POWER_AWAKE, window), "awake")
        self.assertEqual(dv.parse_screen_state(POWER_AWAKE, window + KEYGUARD_UP), "locked")
        self.assertEqual(dv.parse_screen_state(POWER_AWAKE, "  mCurrentFocus=Window{1 u0 Keyguard}\n"), "locked")
        self.assertEqual(dv.parse_screen_state(POWER_ASLEEP, window), "asleep")
        self.assertEqual(dv.parse_screen_state(POWER_DOZING, window + KEYGUARD_UP), "asleep")
        self.assertEqual(dv.parse_screen_state("", window), "unknown")          # nothing says: never guessed
        self.assertEqual(dv.parse_screen_state("", ""), "unknown")
        self.assertEqual(dv.parse_screen_state(POWER_AWAKE, "  isKeyguardShowing=false\n"), "awake")

    def test_notification_permission(self):
        self.assertEqual(dv.parse_notification_permission(PERM_GRANTED), "granted")
        self.assertEqual(dv.parse_notification_permission(PERM_DENIED), "denied")
        self.assertEqual(dv.parse_notification_permission(PERM_GRANTED + PERM_DENIED), "granted")    # the first one
        self.assertEqual(dv.parse_notification_permission("android.permission.INTERNET: granted=true\n"), "unknown")
        self.assertEqual(dv.parse_notification_permission(""), "unknown")
        self.assertEqual(dv.parse_notification_permission(None), "unknown")


class SystemUi(unittest.TestCase):
    @staticmethod
    def focus(component):
        return f"  mCurrentFocus=Window{{4f1c2d u0 {component}}}\n"

    def test_each_kind_is_recognised(self):
        cases = [
            (self.focus(SHADE), "", "", "notification-shade"),
            (self.focus("StatusBar"), "", "", "notification-shade"),
            (self.focus("com.android.systemui/com.android.systemui.recents.RecentsActivity"), "", "", "system-dialog"),
            (self.focus(DIALOG), "", "", "permission-dialog"),
            (self.focus(APP), activity_text(DIALOG), "", "permission-dialog"),
            (self.focus(APP), activity_text(DEV_SETTINGS), "", "developer-settings"),
            (self.focus(DEV_SETTINGS), "", "", "developer-settings"),
            (self.focus(APP), activity_text(USB_PROMPT), "", "system-dialog"),
            (self.focus("HeadsUpNotification"), "", "", "heads-up"),
            (self.focus(APP), "", self.heads_up_xml(), "heads-up"),
            (self.focus(APP), "", self.shade_xml(), "notification-shade"),
        ]
        for window, activity, xml, kind in cases:
            with self.subTest(kind=kind, window=window.strip(), activity=activity.strip()):
                found = dv.classify_system_ui(window, activity, xml)
                self.assertIsNotNone(found)
                self.assertEqual(found[0], kind)

    def test_the_app_and_ordinary_screens_are_not_system_ui(self):
        app = self.focus(APP)
        self.assertIsNone(dv.classify_system_ui(app, activity_text(APP)))
        self.assertIsNone(dv.classify_system_ui(app, activity_text("com.android.settings/.Settings$WifiSettingsActivity")))
        self.assertIsNone(dv.classify_system_ui(self.focus(OTHER)))
        self.assertIsNone(dv.classify_system_ui("", "", ""))
        self.assertIsNone(dv.classify_system_ui(app, "", screen(("Demo Shop", "", "[0,0][1080,120]"))))
        other_systemui = ('<hierarchy><node text="" resource-id="com.android.systemui:id/clock" '
                          'package="com.android.systemui" bounds="[0,0][10,10]" /></hierarchy>')
        self.assertIsNone(dv.classify_system_ui(app, "", other_systemui))   # the status bar clock is not a banner

    @staticmethod
    def heads_up_xml():
        return ('<hierarchy><node text="USB debugging connected" resource-id="com.android.systemui:id/heads_up_container" '
                'package="com.android.systemui" bounds="[0,0][1080,300]" /></hierarchy>')

    @staticmethod
    def shade_xml():
        return ('<hierarchy><node text="" resource-id="com.android.systemui:id/quick_settings_panel" '
                'package="com.android.systemui" bounds="[0,0][1080,900]" /></hierarchy>')


class SyntheticIdsAndPhones(unittest.TestCase):
    """The synthetic test identifiers (`QA-AUTO-<date>-<n>`) are not phone numbers, and real numbers are still found next to
    letters, identifiers and punctuation (regression of the false positive observed on a real screen, 10.11.2026)."""

    SYNTHETIC = ("QA-AUTO-20261110-1001", "QA-AUTO-20261109-P3C", "qa-auto-20261110-1", "QA-AUTO-20261110-1001b2",
                 "QA-AUTO-20261109-2002b2", "QA-AUTO-20261110-1001-draft-C")
    REAL = ("912345678", "912 345 678", "912-345-678", "912.345.678", "+351 912 345 678", "+351912345678", "00351 912345678",
            "(+351) 912 345 678", "+44 20 7946 0958", "(000) 000-000-000")

    def phones(self, text):
        return dv.find_personal_data(text).get("phone", 0)

    def test_the_observed_identifiers_alone_are_not_personal_data(self):
        for ident in self.SYNTHETIC:
            self.assertEqual(dv.find_personal_data(ident), {}, ident)
            self.assertEqual(dv.find_personal_data(f"{ident} draft C existing conversation"), {}, ident)
            self.assertEqual(dv.find_personal_data(f"Message {ident}: answer only OK."), {}, ident)

    def test_real_numbers_alone_are_found_with_and_without_country_code_and_with_any_separator(self):
        for number in self.REAL:
            self.assertEqual(self.phones(number), 1, number)

    def test_real_numbers_next_to_letters_and_punctuation_are_found(self):
        for text in ("Tel: 912345678", "tel:912345678", "Mobile 912345678", "tel.912345678", "Contact:912345678",
                     "call me on 912345678 today", "call 912345678, thanks", "Call (912345678)", "ref AB#1001 tel 912345678"):
            self.assertEqual(self.phones(text), 1, text)

    def test_a_real_number_next_to_a_synthetic_identifier_counts_once_in_any_arrangement(self):
        for ident in self.SYNTHETIC:
            for number in self.REAL:
                for text in (f"{ident} call {number}", f"{number} {ident}", f"{ident}, {number}.", f"({ident}) {number}",
                             f"{ident}/{number}", f"{ident}: {number}", f"{number} ({ident})"):
                    self.assertEqual(self.phones(text), 1, text)

    def test_two_real_numbers_around_an_identifier_count_twice(self):
        self.assertEqual(self.phones("912345678 QA-AUTO-20261110-1001 +351 913 456 789"), 2)
        self.assertEqual(self.phones("QA-AUTO-20261110-1 912345678 and QA-AUTO-20261110-2 913456789"), 2)

    def test_an_email_next_to_an_identifier_is_still_found(self):
        self.assertEqual(dv.find_personal_data("QA-AUTO-20261110-1001 qa@example.invalid"), {"email": 1})
        self.assertEqual(dv.find_personal_data("QA-AUTO-20261110-1001 qa@example.invalid 912345678"), {"email": 1, "phone": 1})

    def test_what_is_still_not_a_number(self):
        for text in ("10 Nov 2026", "2026-11-10", "09:30", "AB#1001", "version 1.0.0 (38)", "20261110", "1001", "12/11/2026",
                     "1234 5678"):
            self.assertEqual(dv.find_personal_data(text), {}, text)

    def test_a_number_glued_to_the_identifier_by_a_hyphen_reads_as_part_of_it(self):
        # documented limit: nothing separates the number from the identifier, so it is not reported
        self.assertEqual(self.phones("QA-AUTO-20261110-912345678"), 0)
        self.assertEqual(self.phones("QA-AUTO-20261110-1001-912345678"), 0)
        self.assertEqual(self.phones("QA-AUTO-20261110-1001 -912345678"), 1)     # a space before the hyphen: a number again

    def test_the_synthetic_id_is_removed_before_counting_but_the_text_is_never_returned(self):
        found = dv.find_personal_data("QA-AUTO-20261110-1001 912345678")
        self.assertEqual(found, {"phone": 1})
        self.assertNotIn("QA-AUTO", repr(found))


class DeviceCommands(unittest.TestCase):
    setUp = Commands.setUp
    tearDown = Commands.tearDown
    run_it = Commands.run_it

    # --- check: one concrete action per case

    def test_no_device_visible(self):
        fake = DiagDevice(devices="")
        rc, out, err = self.run_it(fake, "check")
        self.assertEqual(rc, 3)
        self.assertIn("device state: no-device", out)
        self.assertIn("next: check the USB cable", out)
        self.assertIn("USB debugging", out)
        self.assertIn("no Android device", err)
        self.assertEqual(fake.touched(), [])

    def test_unauthorized(self):
        rc, out, err = self.run_it(DiagDevice(devices="R58M\tunauthorized\n"), "check")
        self.assertEqual(rc, 3)
        self.assertIn("device state: unauthorized", out)
        self.assertIn("next: unlock the phone and accept", out)
        self.assertIn("unauthorized", err)

    def test_offline_is_advice_and_the_adb_server_is_never_touched(self):
        fake = DiagDevice(devices="R58M\toffline\n")
        rc, out, _ = self.run_it(fake, "check")
        self.assertEqual(rc, 3)
        self.assertIn("device state: offline", out)
        self.assertIn("next: unplug and plug the cable again", out)
        self.assertIn("kill-server", out)
        self.assertEqual(fake.calls, [("devices",)])                 # nothing but the listing was asked of adb

    def test_several_devices_keep_the_exit_code_2_and_say_what_to_do(self):
        rc, out, err = self.run_it(DiagDevice(devices="A1\tdevice\nB2\tdevice\n"), "check")
        self.assertEqual(rc, 2)
        self.assertIn("device state: several", out)
        self.assertIn("next: choose one with --serial", out)
        self.assertIn("several devices connected", err)
        self.assertEqual(self.run_it(DiagDevice(devices="A1\tdevice\nB2\tdevice\n"), "dump")[0], 2)   # as before

    def test_a_non_check_command_says_the_action_too(self):
        rc, _, err = self.run_it(DiagDevice(devices="R58M\tunauthorized\n"), "dump")
        self.assertEqual(rc, 3)
        self.assertIn("unauthorized", err)
        self.assertIn("accept", err)

    def test_locked_screen_is_a_blocker_and_nothing_is_tried(self):
        fake = DiagDevice(window_extra=KEYGUARD_UP)
        rc, out, err = self.run_it(fake, "check")
        self.assertEqual(rc, 3)
        self.assertIn("screen: locked", out)
        self.assertIn("next: the user unlocks the phone", out)
        self.assertIn("never unlocks", out)
        self.assertIn("the screen is locked", err)
        self.assertEqual(fake.touched(), [])                         # no keyevent, no swipe, no wake-up

    def test_dozing_and_asleep(self):
        for power in (POWER_ASLEEP, POWER_DOZING):
            with self.subTest(power=power):
                fake = DiagDevice(power=power)
                rc, out, _ = self.run_it(fake, "check")
                self.assertEqual(rc, 3)
                self.assertIn("screen: asleep", out)
                self.assertIn("wakes and unlocks", out)
                self.assertEqual(fake.touched(), [])

    def test_ready_phone_says_awake_and_in_foreground(self):
        rc, out, err = self.run_it(DiagDevice(), "check")
        self.assertEqual((rc, err), (0, ""))
        for line in ("device state: ok", "screen: awake", f"foreground: {APP}", "app in foreground: yes", "ready: yes"):
            self.assertIn(line, out.splitlines())

    def test_app_not_in_foreground_is_one_advice_and_not_a_blocker(self):
        rc, out, _ = self.run_it(DiagDevice(focus=OTHER), "check")
        self.assertEqual(rc, 0)                                      # as before: the exit code does not change
        self.assertIn("app in foreground: no", out.splitlines())
        advice = [ln for ln in out.splitlines() if ln.startswith("next:")]
        self.assertEqual(len(advice), 1)
        self.assertIn("launch", advice[0])

    def test_notification_permission_line(self):
        for extra, word in ((PERM_GRANTED, "granted"), (PERM_DENIED, "denied"), ("", "unknown")):
            with self.subTest(word=word):
                rc, out, _ = self.run_it(DiagDevice(package_extra=extra), "check")
                self.assertEqual(rc, 0)
                line = next(ln for ln in out.splitlines() if ln.startswith("notifications:"))
                self.assertTrue(line.split()[1] == word, line)
        _, out, _ = self.run_it(DiagDevice(package_extra=PERM_DENIED), "check")
        self.assertIn("never grants it", out)

    def test_notification_permission_unreadable_package_is_unknown_not_an_error(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  app_id: com.demoshop.other\n")
        rc, out, _ = self.run_it(DiagDevice(package_extra=PERM_GRANTED), "check")
        self.assertEqual(rc, 3)                                      # not installed, said as before
        self.assertNotIn("notifications: granted", out)

    # --- never tap on system UI by coordinates

    def assert_nothing_touched(self, fake):
        self.assertEqual(fake.touched(), [])
        self.assertEqual(fake.of("exec-out"), [])                    # no dump read either

    def test_coordinates_are_refused_in_front_of_system_ui(self):
        scenarios = {
            "shade": {"focus": SHADE},
            "permission dialog": {"focus": DIALOG},
            "developer settings": {"activity": activity_text(DEV_SETTINGS)},
            "usb prompt": {"activity": activity_text(USB_PROMPT)},
        }
        commands = (("tap", "--xy", "10", "20"), ("hold", "--xy", "10", "20"), ("swipe", "1", "2", "3", "4"))
        for name, kw in scenarios.items():
            for command in commands:
                with self.subTest(scenario=name, command=command[0]):
                    fake = DiagDevice(**kw)
                    rc, out, err = self.run_it(fake, *command)
                    self.assertEqual((rc, out), (3, ""))
                    self.assertTrue(err.startswith("REFUSED:"), err)
                    self.assertIn("by coordinates refused", err)
                    self.assertIn("nothing was touched", err)
                    self.assert_nothing_touched(fake)

    def test_coordinates_still_work_on_the_app_and_a_permission_dialog_by_text(self):
        fake = DiagDevice()
        self.assertEqual(self.run_it(fake, "tap", "--xy", "10", "20")[0], 0)
        self.assertEqual(fake.taps(), [("10", "20")])
        dialog = screen(("Allow", "", "[60,1100][1020,1200]"))
        fake = DiagDevice([dialog], focus=DIALOG)
        self.assertEqual(self.run_it(fake, "tap", "Allow")[0], 0)    # read from the dialog on purpose
        self.assertEqual(fake.taps(), [("540", "1150")])

    def test_heads_up_over_the_app_stops_send_before_the_tap(self):
        banner = ('<node index="9" text="USB debugging connected" resource-id="com.android.systemui:id/heads_up_container" '
                  'class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" '
                  'bounds="[0,0][1080,300]" />')
        typed = CHAT_TYPED.replace("</node></hierarchy>", banner + "</node></hierarchy>")
        fake = DiagDevice([typed])
        rc, _, err = self.run_it(fake, "send", "Where is my order QA-1?")
        self.assertEqual(rc, 3)
        self.assertIn("a heads-up notification is in front of the app", err)
        self.assertIn("typed but not sent", err)
        self.assertIn("outcome: typed, not sent", err)
        self.assertEqual(fake.taps(), [])                            # the send control was not tapped

    def test_send_without_system_ui_still_sends(self):
        fake = DiagDevice([CHAT_TYPED])
        rc, _, _ = self.run_it(fake, "send", "Where is my order QA-1?")
        self.assertEqual(len(fake.taps()), 1, rc)

    # --- personal data in a capture

    def test_find_personal_data_counts_categories_and_never_returns_values(self):
        text = f"Hello\nMail {EMAIL} and {EMAIL}\nCall {PHONE} or 912 345 678\nOrder QA-12\n2026-11-10 12:30:15"
        found = dv.find_personal_data(text)
        self.assertEqual(found, {"email": 2, "phone": 2})
        self.assertNotIn(EMAIL, repr(found))
        self.assertNotIn("000", repr(found))
        self.assertEqual(dv.find_personal_data("Total 12,50 EUR\nOrder QA-12\n10.11.2026\n14:05"), {})
        self.assertEqual(dv.find_personal_data(""), {})
        self.assertEqual(dv.find_personal_data(None), {})
        self.assertEqual(dv.find_personal_data("a.b+c@sub.example.invalid"), {"email": 1})
        self.assertEqual(dv.find_personal_data("(000) 000-000-000"), {"phone": 1})
        self.assertEqual(dv.find_personal_data("1234 5678"), {})        # 8 digits are not a phone number
        self.assertEqual(dv.find_personal_data("ID 20261110123015"), {"phone": 1})   # over-reports: a long id, by design

    def with_personal(self):
        return screen(("Demo Shop", "", "[0,0][1080,120]"), (f"Signed in as {EMAIL}", "", "[0,200][1080,300]"),
                      (f"Phone {PHONE}", "", "[0,300][1080,400]"))

    def png_path(self, name="c1"):
        return os.path.join(self.ws, "evidence", "batch_20260928_01", f"{name}.png")

    def test_a_capture_after_a_dump_with_personal_data_warns_with_counts_only(self):
        fake = DiagDevice([self.with_personal()])
        rc, _, err = self.run_it(fake, "dump", "--capture", "c1")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(self.png_path()))
        self.assertIn("WARNING", err)
        self.assertIn("email: 1", err)
        self.assertIn("phone: 1", err)
        self.assertNotIn(EMAIL, err)
        self.assertNotIn("000", err)

    def test_require_clean_refuses_to_save_and_says_what_it_found(self):
        fake = DiagDevice([self.with_personal()])
        rc, _, err = self.run_it(fake, "dump", "--capture", "c1", "--require-clean")
        self.assertEqual(rc, 3)
        self.assertTrue(err.startswith("REFUSED:"), err)
        self.assertIn("personal data (email: 1, phone: 1)", err)
        self.assertNotIn(EMAIL, err)
        self.assertFalse(os.path.exists(self.png_path()))
        self.assertEqual(fake.of("exec-out", "screencap"), [])       # not even captured
        fake = DiagDevice([HOME])
        rc, _, err = self.run_it(fake, "dump", "--capture", "c1", "--require-clean")
        self.assertEqual((rc, err), (0, ""))
        self.assertTrue(os.path.isfile(self.png_path()))

    def synthetic_screen(self, extra=""):
        rows = [("Message QA-AUTO-20261110-1001 draft C existing conversation", "", "[0,200][1080,300]"),
                ("Give feedback about the meeting. Today", "", "[0,300][1080,400]")]
        if extra:
            rows.append((extra, "", "[0,400][1080,500]"))
        return screen(*rows)

    def test_a_screen_with_only_a_synthetic_identifier_passes_require_clean(self):
        rc, _, err = self.run_it(DiagDevice([self.synthetic_screen()]), "shot", "c1", "--require-clean", "--caption", "test")
        self.assertEqual((rc, err), (0, ""))
        self.assertTrue(os.path.isfile(self.png_path()))

    def test_a_synthetic_identifier_does_not_hide_a_real_number_from_require_clean(self):
        rc, _, err = self.run_it(DiagDevice([self.synthetic_screen("Call 912 345 678")]), "shot", "c1", "--require-clean")
        self.assertEqual(rc, 3)
        self.assertIn("personal data (phone: 1)", err)
        self.assertNotIn("912", err)
        self.assertFalse(os.path.exists(self.png_path()))

    def test_a_shot_on_its_own_reads_the_screen_first(self):
        rc, _, err = self.run_it(DiagDevice([self.with_personal()]), "shot", "c1")
        self.assertEqual(rc, 0)
        self.assertIn("email: 1", err)
        self.assertNotIn(EMAIL, err)
        os.remove(self.png_path())
        rc, _, err = self.run_it(DiagDevice([self.with_personal()]), "shot", "c1", "--require-clean")
        self.assertEqual(rc, 3)
        self.assertFalse(os.path.exists(self.png_path()))
        rc, _, err = self.run_it(DiagDevice([HOME]), "shot", "c1", "--require-clean")
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(self.run_it(DiagDevice([HOME]), "shot", "c2")[2], "")       # clean: no warning

    def test_a_screen_that_cannot_be_read_is_unchecked_and_refused_only_when_clean_is_required(self):
        unreadable = ""                                              # no hierarchy: the dump is retried, then given up
        with mock.patch.object(dv.time, "sleep"):
            rc, _, err = self.run_it(DiagDevice([unreadable]), "shot", "c1")
            self.assertEqual((rc, err), (0, ""))
            self.assertTrue(os.path.isfile(self.png_path()))
            os.remove(self.png_path())
            rc, _, err = self.run_it(DiagDevice([unreadable]), "shot", "c1", "--require-clean")
        self.assertEqual(rc, 3)
        self.assertIn("none could be read", err)
        self.assertFalse(os.path.exists(self.png_path()))

    def test_the_state_of_one_run_does_not_leak_into_the_next(self):
        self.run_it(DiagDevice([self.with_personal()]), "dump")
        self.assertEqual(dv._LAST_SCREEN["personal"], {"email": 1, "phone": 1})
        rc = self.run_it(DiagDevice([HOME]), "back")[0]
        self.assertEqual(rc, 0)
        self.assertIsNone(dv._LAST_SCREEN["personal"])

    # --- captions

    def manifest(self):
        path = os.path.join(self.ws, "evidence", "batch_20260928_01", dv.MANIFEST_NAME)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f.read().splitlines()]

    def test_without_a_caption_there_is_no_manifest(self):
        self.assertEqual(self.run_it(DiagDevice(), "shot", "c1")[0], 0)
        self.assertIsNone(self.manifest())

    def test_a_caption_is_kept_next_to_the_capture(self):
        self.assertEqual(self.run_it(DiagDevice(), "shot", "TC-1_step2", "--caption", "Step 2:  the cart\nis empty")[0], 0)
        self.assertEqual(self.run_it(DiagDevice([self.with_personal()]), "shot", "TC-1_step3", "--caption", "Step 3")[0], 0)
        rows = self.manifest()
        self.assertEqual([r["file"] for r in rows], ["TC-1_step2.png", "TC-1_step3.png"])
        self.assertEqual(rows[0]["caption"], "Step 2: the cart is empty")        # one line
        self.assertEqual(rows[0]["personal_data"], {})
        self.assertEqual(rows[1]["personal_data"], {"email": 1, "phone": 1})     # counts, never the values
        self.assertIn("ts", rows[0])
        with open(os.path.join(self.ws, "evidence", "batch_20260928_01", dv.MANIFEST_NAME), encoding="utf-8") as f:
            self.assertNotIn(EMAIL, f.read())

    def test_a_caption_with_personal_data_or_a_secret_is_refused_before_anything_is_captured(self):
        for caption in (f"Logged in as {EMAIL}", f"Call {PHONE}", "password fictional-password-9", "", "x" * 301):
            with self.subTest(caption=caption[:20]):
                fake = DiagDevice()
                rc, _, err = self.run_it(fake, "shot", "c1", "--caption", caption)
                self.assertEqual(rc, 2)
                self.assertNotIn(EMAIL, err)
                self.assertEqual(fake.of("exec-out"), [])
                self.assertFalse(os.path.exists(self.png_path()))
                self.assertIsNone(self.manifest())

    def test_the_caption_is_stored_by_dump_and_send_captures_too(self):
        self.assertEqual(self.run_it(DiagDevice(), "dump", "--capture", "d1", "--caption", "Home")[0], 0)
        self.assertEqual([r["caption"] for r in self.manifest()], ["Home"])

    def test_unchecked_is_recorded_when_no_screen_was_read(self):
        with mock.patch.object(dv.time, "sleep"):
            self.assertEqual(self.run_it(DiagDevice([""]), "shot", "c1", "--caption", "Blank")[0], 0)
        self.assertEqual(self.manifest()[0]["personal_data"], "unchecked")

    def test_png_is_what_the_device_returned(self):
        self.run_it(DiagDevice(), "shot", "c1")
        with open(self.png_path(), "rb") as f:
            self.assertEqual(f.read(), PNG)


if __name__ == "__main__":
    unittest.main()

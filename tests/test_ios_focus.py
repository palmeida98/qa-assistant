"""The focus before typing on an iPhone and what a stopped send says (docs/pilot-improvements-2026-11-07.md D), with a
fictional WebDriverAgent: one field is chosen and tapped from the screen just read, several or none are refused with
the list, a disabled field is not a candidate, a late field is waited for, the focus is confirmed by the keyboard;
the outcome of an interruption (nothing typed, partly typed, typed not sent, sent, uncertain), and never a second send.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import device as dv
import device_ios as wda
from test_device_ios import APP, SID, Clock, QuietProbes, el

MESSAGE = "Where is my order QA-1?"


def screen(*children, keyboard=False):
    kids = list(children) + ([el("Keyboard", "", (0, 600, 390, 244))] if keyboard else [])
    return el("XCUIElementTypeApplication", "Demo Shop", (0, 0, 390, 844), children=kids)


SEND = el("XCUIElementTypeButton", "Send", (310, 700, 70, 40))
TITLE = el("XCUIElementTypeStaticText", "Demo Shop", (0, 50, 390, 40))


def field(label="Message", value="", rect=(0, 700, 300, 40), enabled="1"):
    f = el("TextField", label, rect, value=value)
    f["isEnabled"] = enabled
    return f


class Wda:
    """A scripted WDA: the screen changes with the taps and the keys, and every call is recorded."""

    def __init__(self, fields, focus_on_tap=True, late=0, fail_keys_at=None, fail_send=False, keep_text=False,
                 answer="Your order ships tomorrow."):
        self.fields, self.focus_on_tap, self.late = fields, focus_on_tap, late
        self.fail_keys_at, self.fail_send, self.keep_text, self.answer = fail_keys_at, fail_send, keep_text, answer
        self.keyboard, self.typed, self.sent, self.reads, self.calls = False, "", False, 0, []

    def tree(self):
        self.reads += 1
        if self.reads <= self.late:
            return screen(TITLE)
        kids = [TITLE]
        if self.sent:
            kids.append(el("XCUIElementTypeStaticText", self.typed, (100, 400, 280, 40)))
            kids.append(el("XCUIElementTypeStaticText", self.answer, (0, 460, 300, 40)))
        for f in self.fields:
            value = self.typed if (self.keyboard and f.get("label") == "Message" and
                                   (not self.sent or self.keep_text)) else f.get("value")
            kids.append({**f, "value": value})
        return screen(*kids, SEND, keyboard=self.keyboard)

    def __call__(self, cfg, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        if path == "/wda/activeAppInfo":
            return {"value": {"bundleId": APP}}
        if path == "/session":
            return {"value": {"sessionId": SID}, "sessionId": SID}
        if path == "/source?format=json":
            return {"value": self.tree()}
        if path.endswith("/wda/tap"):
            if body == {"x": 345, "y": 720}:            # the send button
                if self.fail_send:
                    raise wda.Unreachable("WDA /wda/tap: no answer")
                self.sent = True
            elif self.focus_on_tap:
                self.keyboard = True
            return {"value": None}
        if path.endswith("/wda/keys"):
            n = len([c for c in self.calls if c[1].endswith("/wda/keys")])
            if self.fail_keys_at is not None and n > self.fail_keys_at:
                raise wda.Unreachable("WDA /wda/keys: connection reset")
            self.typed += "".join(body["value"])
            return {"value": None}
        return {"value": None}

    def posts(self, suffix):
        return [b for m, p, b in self.calls if m == "POST" and p.endswith(suffix)]


class Focus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: ios\n  ios:\n    app_id: com.demoshop.app\n    udid: UDID-1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, fake, *args):
        out, err = io.StringIO(), io.StringIO()
        clock = Clock()
        with mock.patch.object(wda, "request", fake), mock.patch.object(dv, "ios_probes", QuietProbes), \
                mock.patch.object(dv.time, "sleep", clock.sleep), mock.patch.object(dv.time, "monotonic", clock.now), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dv.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    def test_the_only_field_is_tapped_from_the_fresh_screen_then_the_text_typed(self):
        fake = Wda([field()])
        rc, out, _ = self.run_it(fake, "type", "hello QA-1")
        self.assertEqual(rc, 0)
        self.assertIn("into the only text field 'Message'", out)
        self.assertEqual(fake.posts("/wda/tap"), [{"x": 150, "y": 720}])
        self.assertEqual(fake.typed, "hello QA-1")

    def test_an_explicit_field_wins(self):
        fake = Wda([field(), field("Subject", rect=(0, 300, 300, 40))])
        rc, _, _ = self.run_it(fake, "type", "x", "--field", "Subject")
        self.assertEqual(rc, 0)
        self.assertEqual(fake.posts("/wda/tap"), [{"x": 150, "y": 320}])

    def test_several_fields_are_listed_and_nothing_is_tapped_or_typed(self):
        fake = Wda([field(), field("Subject", rect=(0, 300, 300, 40))])
        rc, _, err = self.run_it(fake, "type", "x")
        self.assertEqual(rc, 4)
        self.assertIn("2 editable fields on the screen ('Message', 'Subject'): say which with --field", err)
        self.assertIn("outcome: nothing typed", err)
        self.assertEqual((fake.posts("/wda/tap"), fake.posts("/wda/keys")), ([], []))

    def test_a_disabled_field_is_not_a_candidate(self):
        fake = Wda([field(enabled="0")])
        rc, _, err = self.run_it(fake, "type", "x")
        self.assertEqual(rc, 4)
        self.assertIn("no enabled text field on the screen (1 disabled: 'Message')", err)
        fake = Wda([field(enabled="0"), field("Search", rect=(0, 100, 300, 40))])
        rc, _, _ = self.run_it(fake, "type", "x")
        self.assertEqual(rc, 0)
        self.assertEqual(fake.posts("/wda/tap"), [{"x": 150, "y": 120}])

    def test_a_field_that_appears_late_is_waited_for(self):
        fake = Wda([field()], late=3)
        rc, _, _ = self.run_it(fake, "type", "x")
        self.assertEqual(rc, 0)
        self.assertEqual(fake.typed, "x")
        fake = Wda([field()], late=100)                # it never appears
        rc, _, err = self.run_it(fake, "type", "x")
        self.assertEqual(rc, 4)
        self.assertIn("no enabled text field", err)

    def test_a_field_that_does_not_take_the_focus_types_nothing(self):
        fake = Wda([field()], focus_on_tap=False)
        rc, _, err = self.run_it(fake, "type", "x")
        self.assertEqual(rc, 4)
        self.assertIn("did not take the focus", err)
        self.assertEqual(fake.posts("/wda/keys"), [])

    def test_a_failure_after_the_first_chunk_is_partly_typed(self):
        fake = Wda([field()], fail_keys_at=1)
        rc, _, err = self.run_it(fake, "type", "x" * 700)
        self.assertEqual(rc, 3)
        self.assertIn("outcome: partly typed", err)
        self.assertIn("1 of 3 chunks typed", err)
        self.assertEqual(len(fake.posts("/wda/keys")), 2)          # the failed call, never a retry

    def test_a_failure_on_the_first_chunk_is_uncertain(self):
        fake = Wda([field()], fail_keys_at=0)
        rc, _, err = self.run_it(fake, "type", "x")
        self.assertEqual(rc, 3)
        self.assertIn("outcome: uncertain", err)

    def test_send_confirms_that_the_message_left_the_field(self):
        fake = Wda([field()])
        rc, out, _ = self.run_it(fake, "send", "Where is my order QA-1?", "--timeout", "60")
        self.assertEqual(rc, 0, out)
        self.assertIn("outcome: sent (the field no longer holds the message)", out)
        self.assertIn("Your order ships tomorrow.", out)
        self.assertEqual(len(fake.posts("/wda/keys")), 1)

    def test_the_send_reads_the_screen_three_times_before_the_tap_and_says_its_timing(self):
        """Backlog 3: focus (1 reading), keyboard confirmed (1), after typing (1): no second check of the keyboard
        before the keys, no separate reading for the baseline."""
        fake = Wda([field()])
        rc, out, _ = self.run_it(fake, "send", "Where is my order QA-1?", "--timeout", "60")
        self.assertEqual(rc, 0)
        calls = [p for _, p, _ in fake.calls]
        tap_send = next(i for i, (_, p, b) in enumerate(fake.calls) if p.endswith("/wda/tap") and b == {"x": 345, "y": 720})
        self.assertEqual(calls[:tap_send].count("/source?format=json"), 3)
        self.assertRegex(out, r"timing: focus [\d.]+ s, keys [\d.]+ s, until the tap on send [\d.]+ s")

    def test_a_tap_on_send_without_answer_is_uncertain_and_never_repeated(self):
        fake = Wda([field()], fail_send=True)
        rc, _, err = self.run_it(fake, "send", "Where is my order QA-1?")
        self.assertEqual(rc, 3)
        self.assertIn("outcome: uncertain", err)
        self.assertEqual(len([b for b in fake.posts("/wda/tap") if b == {"x": 345, "y": 720}]), 1)
        self.assertEqual(len(fake.posts("/wda/keys")), 1)

    def test_text_still_in_the_field_after_send_is_typed_not_sent(self):
        fake = Wda([field()], keep_text=True)
        rc, _, err = self.run_it(fake, "send", "Where is my order QA-1?")
        self.assertEqual(rc, 4)
        self.assertIn("outcome: typed, not sent", err)
        self.assertEqual(len([b for b in fake.posts("/wda/tap") if b == {"x": 345, "y": 720}]), 1)

    def test_no_send_control_is_typed_not_sent(self):
        fake = Wda([field()])
        rc, _, err = self.run_it(fake, "send", "hi", "--send-label", "Post")
        self.assertEqual(rc, 4)
        self.assertIn("outcome: typed, not sent", err)



class DeclaredField(unittest.TestCase):
    """An app whose composer is not a text field (`Other`, seen on a React Native app): found through
    mobile.field_label; a field that never shows its text confirms the send by the message on the screen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, with_label=True):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: ios\n" + ("  field_label: Input Field\n" if with_label else "")
                    + "  ios:\n    app_id: com.demoshop.app\n")

    def composer(self, shows_message=True):
        other = el("XCUIElementTypeOther", "Input Field How can I help?", (56, 755, 238, 22))
        fake = Wda([other])
        if not shows_message:
            fake.tree_real = fake.tree

            def tree():
                t = fake.tree_real()
                t["children"] = [c for c in t["children"] if c.get("label") != MESSAGE]
                return t
            fake.tree = tree
        return fake

    def run_it(self, fake, *args):
        return Focus.run_it(self, fake, *args)

    def test_a_declared_label_is_the_field_and_the_message_on_screen_confirms_the_send(self):
        self.project()
        fake = self.composer()
        rc, out, err = self.run_it(fake, "send", MESSAGE, "--timeout", "60")
        self.assertEqual(rc, 0, err)
        self.assertEqual(fake.posts("/wda/tap")[0], {"x": 175, "y": 766})
        self.assertIn("outcome: sent (the message is shown on the screen)", out)

    def test_without_the_label_the_hint_names_the_setting(self):
        self.project(with_label=False)
        rc, _, err = self.run_it(self.composer(), "type", "x")
        self.assertEqual(rc, 4)
        self.assertIn("mobile.field_label", err)

    def test_a_message_never_shown_is_uncertain_not_sent_again(self):
        self.project()
        fake = self.composer(shows_message=False)
        _, out, _ = self.run_it(fake, "send", MESSAGE, "--timeout", "60")
        self.assertIn("outcome: uncertain (the field does not expose its text", out)
        self.assertEqual(len(fake.posts("/wda/keys")), 1)


if __name__ == "__main__":
    unittest.main()

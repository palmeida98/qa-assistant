"""Evidence of a streamed answer (docs/pilot-improvements-2026-11-07.md E), on a fictional iPhone app: the sampler says
what its readings could show (partial text, readable without an intermediate state, not readable, insufficient) and
never turns "not readable" into "no streaming"; `send --burst` and `shot-burst` take timed screenshots with limits and
a manifest, starting at the tap on send, record failed captures and gaps, and never send again.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import device as dv
import device_ios as wda
from test_device_ios import PNG, Clock, QuietProbes, el
from test_ios_focus import SEND, TITLE, Wda, field, screen

MESSAGE = "Where is my order QA-1?"


class Streamed(Wda):
    """After the send, the answer goes through `steps` (one per reading of the screen); `readable=False` hides the
    bubble's text from the accessibility tree (as in the pilot); `bad_shots` are screenshot calls that fail."""

    def __init__(self, steps, readable=True, bad_shots=()):
        super().__init__([field()])
        self.steps, self.readable, self.bad_shots, self.after, self.shots = list(steps), readable, set(bad_shots), 0, 0

    def tree(self):
        if not self.sent:
            return super().tree()
        self.after += 1
        answer = self.steps[min(self.after - 1, len(self.steps) - 1)]
        kids = [TITLE, el("XCUIElementTypeStaticText", MESSAGE, (100, 400, 280, 40))]
        if answer:
            bubble = el("XCUIElementTypeOther", answer if self.readable else "", (0, 460, 300, 40))
            kids.append(bubble)
        kids.append(field())
        return screen(*kids, SEND, keyboard=True)

    def __call__(self, cfg, method, path, body=None, timeout=30):
        if path == "/screenshot":
            self.shots += 1
            self.calls.append((method, path, body))
            if self.shots in self.bad_shots:
                return {"value": ""}
            return {"value": base64.b64encode(PNG + bytes(self.shots)).decode()}
        return super().__call__(cfg, method, path, body, timeout)


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("mobile:\n  platform: ios\n  evidence_batch: batch_demo_01\n  ios:\n    app_id: com.demoshop.app\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, fake, *args):
        out, err = io.StringIO(), io.StringIO()
        clock = Clock()
        with mock.patch.object(wda, "request", fake), mock.patch.object(dv, "ios_probes", QuietProbes), \
                mock.patch.object(dv.Burst, "threaded", False), \
                mock.patch.object(dv.time, "sleep", clock.sleep), mock.patch.object(dv.time, "monotonic", clock.now), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dv.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    def send(self, fake, *extra):
        return self.run_it(fake, "send", MESSAGE, "--capture", "TC-SHOP-001_t1", "--timeout", "60", *extra)

    def folder(self):
        return os.path.join(self.ws, "evidence", "batch_demo_01")

    def load(self, name):
        with open(os.path.join(self.folder(), name), encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------ the sampler's honest summary

    def test_a_growing_answer_is_partial_text_observed(self):
        rc, out, _ = self.send(Streamed(["", "Your", "Your order", "Your order ships tomorrow."]),
                               "--samples", "20", "--sample-interval", "2")
        self.assertEqual(rc, 0)
        self.assertIn("evidence: partial text observed (partial text observed: yes)", out)
        self.assertEqual(self.load("TC-SHOP-001_t1_samples.json")["summary"]["evidence"], "partial text observed")

    def test_an_immediate_answer_is_readable_without_an_intermediate_state(self):
        rc, out, _ = self.send(Streamed(["Your order ships tomorrow."]), "--samples", "20", "--sample-interval", "2")
        self.assertEqual(rc, 0)
        self.assertIn("evidence: answer readable, no intermediate state captured", out)
        summary = self.load("TC-SHOP-001_t1_samples.json")["summary"]
        self.assertEqual((summary["partial_text_observed"], summary["first_readable_s"] is not None), (False, True))

    def test_an_answer_the_tree_does_not_expose_is_not_readable_never_no_streaming(self):
        rc, out, err = self.send(Streamed(["", "Your", "Your order ships tomorrow."], readable=False),
                                 "--samples", "20", "--sample-interval", "2")
        self.assertEqual(rc, 0)
        self.assertIn("evidence: answer text not readable (partial text observed: not known)", out)
        self.assertNotIn("partial text observed: no)", out)
        self.assertIn("a limit of the reading, not a result of the app", err)
        self.assertIsNone(self.load("TC-SHOP-001_t1_samples.json")["summary"]["partial_text_observed"])

    def test_controls_that_appear_after_the_send_are_never_the_answer(self):
        """As seen on a real iPhone (07.11.2026): after the send, feedback buttons, a scroll bar and the composer appear,
        while the answer's elements are in the tree but invisible: the answer is not readable."""
        fake = Streamed(["", "Your", "Your order ships tomorrow."], readable=False)
        real = fake.tree

        def tree():
            t = real()
            if fake.sent:
                t["children"] += [el("XCUIElementTypeButton", "Like this answer", (16, 559, 20, 20)),
                                  el("XCUIElementTypeButton", "Copy the answer", (80, 559, 20, 20)),
                                  el("XCUIElementTypeOther", "Vertical scroll bar, 1 page", (357, 47, 30, 680)),
                                  el("XCUIElementTypeOther", "Your order ships tomorrow.", (0, 0, 0, 0), visible="0")]
            return t
        fake.tree = tree
        rc, out, _ = self.send(fake, "--samples", "20", "--sample-interval", "2")
        self.assertEqual(rc, 0)
        self.assertIn("evidence: answer text not readable", out)
        answers = [x["answer"] for x in self.load("TC-SHOP-001_t1_samples.json")["samples"] if x["kind"] == "read"]
        self.assertFalse(any("Like this answer" in a or "scroll bar" in a for a in answers), answers)

    def hidden_answer(self, steps):
        """The answer only in invisible elements (as on the device), growing over the readings."""
        fake = Streamed(steps, readable=False)
        real = fake.tree

        def tree():
            t = real()
            if fake.sent:
                answer = fake.steps[min(fake.after - 1, len(fake.steps) - 1)]
                if answer:
                    t["children"].insert(2, el("XCUIElementTypeOther", answer, (0, 0, 0, 0), visible="0"))
            return t
        fake.tree = tree
        return fake

    def test_read_hidden_reports_an_invisible_answer_apart(self):
        rc, out, _ = self.send(self.hidden_answer(["", "Your", "Your order ships tomorrow."]),
                               "--samples", "20", "--sample-interval", "2", "--read-hidden")
        self.assertEqual(rc, 0)
        self.assertIn("evidence: partial text observed, hidden in the tree", out)
        reads = [x for x in self.load("TC-SHOP-001_t1_samples.json")["samples"] if x["kind"] == "read"]
        self.assertTrue(all(x["answer"] == "" for x in reads))                     # never mixed with the visible
        self.assertIn("Your", [x["hidden_answer"] for x in reads])

    def test_without_read_hidden_the_invisible_answer_stays_not_readable(self):
        _, out, _ = self.send(self.hidden_answer(["", "Your order ships tomorrow."]),
                               "--samples", "20", "--sample-interval", "2")
        self.assertIn("evidence: answer text not readable", out)

    def test_read_hidden_without_samples_is_refused_before_typing(self):
        fake = self.hidden_answer(["x"])
        rc, _, _ = self.send(fake, "--read-hidden")
        self.assertEqual(rc, 2)
        self.assertEqual(fake.posts("/wda/keys"), [])

    def test_a_slow_reading_with_one_sample_is_insufficient(self):
        rc, out, _ = self.send(Streamed(["Your order ships tomorrow."]), "--samples", "1", "--sample-interval", "2")
        self.assertEqual(rc, 0)
        self.assertIn("evidence: sampling failed or insufficient", out)

    # ------------------------------------------------------------------ the burst

    def test_send_burst_starts_at_the_tap_and_writes_a_manifest(self):
        fake = Streamed(["", "Your", "Your order ships tomorrow."], readable=False)
        rc, out, _ = self.send(fake, "--burst", "5", "--burst-interval", "2")
        self.assertEqual(rc, 0, out)
        doc = self.load("TC-SHOP-001_t1_burst.json")
        self.assertEqual(len(doc["files"]), 5)
        self.assertEqual(doc["stopped_by"], "files")
        self.assertIsNotNone(doc["sent_after_s"])
        self.assertLessEqual(doc["files"][0]["elapsed_s"] - doc["sent_after_s"], 0.5)   # right after the tap
        self.assertTrue(doc["attempt"].startswith("TC-SHOP-001_t1@"))
        self.assertIn("do not prove streaming", doc["note"])
        for f in doc["files"]:
            self.assertTrue(os.path.isfile(os.path.join(self.ws, f["file"])), f)
        self.assertEqual(sorted(n for n in os.listdir(self.folder()) if n.endswith(".part")), [])
        self.assertEqual(len(fake.posts("/wda/keys")), 1)                       # never sent again
        self.assertEqual(len([b for b in fake.posts("/wda/tap") if b == {"x": 345, "y": 720}]), 1)
        self.assertIn("burst: 5 screenshot(s), 0 failed", out)

    def test_failed_captures_are_recorded_and_the_others_kept(self):
        fake = Streamed(["Your order ships tomorrow."], bad_shots={2})
        rc, _, _ = self.send(fake, "--burst", "4", "--burst-interval", "2")
        self.assertEqual(rc, 0)
        doc = self.load("TC-SHOP-001_t1_burst.json")
        self.assertEqual((len(doc["files"]), len(doc["failed"])), (3, 1))      # 4 attempts: a failure counts
        self.assertIn("no PNG", doc["failed"][0]["reason"])

    def test_the_limits_stop_the_burst(self):
        rc, _, _ = self.run_it(Streamed(["x"]), "shot-burst", "TC-SHOP-001_watch", "--count", "50",
                                 "--interval", "1", "--seconds", "4")
        self.assertEqual(rc, 0)
        doc = self.load("TC-SHOP-001_watch_burst.json")
        self.assertEqual(doc["stopped_by"], "seconds")
        self.assertLessEqual(doc["observed_s"][1], 4)
        rc, _, _ = self.run_it(Streamed(["x"]), "shot-burst", "TC-SHOP-001_small", "--count", "50",
                               "--interval", "1", "--max-mb", "0.00002")
        self.assertEqual(self.load("TC-SHOP-001_small_burst.json")["stopped_by"], "megabytes")
        rc, _, _ = self.run_it(Streamed(["x"]), "shot-burst", "x", "--count", "999")
        self.assertEqual(rc, 2)
        rc, _, _ = self.run_it(Streamed(["x"]), "shot-burst", "../x")
        self.assertEqual(rc, 2)

    def test_a_burst_that_could_capture_nothing_is_exit_3(self):
        rc, _, _ = self.run_it(Streamed(["x"], bad_shots=set(range(1, 100))), "shot-burst", "nothing", "--count", "3",
                                 "--interval", "1")
        self.assertEqual(rc, 3)
        self.assertEqual(len(self.load("nothing_burst.json")["failed"]), 3)

    def test_gaps_larger_than_the_interval_are_listed(self):
        b = dv.Burst({"workspace": self.ws, "batch": "batch_demo_01"}, "", "g", None, 5, 60, 1, 5)
        b.files = [{"file": "a", "elapsed_s": 0.0}, {"file": "b", "elapsed_s": 1.0}, {"file": "c", "elapsed_s": 4.5}]
        self.assertEqual(b.manifest()["gaps"], [{"after": "b", "seconds": 3.5}])



class OwnClock(unittest.TestCase):
    """The burst on its own clock (backlog 1): with slow readings of the screen going on, the captures keep their
    interval (real time, short limits)."""

    def test_the_cadence_does_not_depend_on_the_readings(self):
        import threading
        import time
        with tempfile.TemporaryDirectory() as ws:
            cfg = {"workspace": ws, "batch": "batch_demo_01"}

            def capture(cfg_, serial):
                time.sleep(0.03)
                return PNG
            with mock.patch.object(dv, "BURST_MIN_INTERVAL", 0.05), mock.patch.object(dv, "capture_png", capture):
                b = dv.Burst(cfg, "", "clock", None, 50, 1.0, 0.2, 5)
                b.mark_sent()
                b.start()
                stop = time.monotonic() + 1.2
                while time.monotonic() < stop:          # the main loop reads the screen slowly meanwhile
                    time.sleep(0.4)
                b.stop("settled")
            self.assertFalse(b._thread.is_alive())
            m = b.manifest()
            self.assertGreaterEqual(len(m["files"]), 4)            # about 1.0 s / 0.2 s, never one per 0.4 s reading
            self.assertEqual(m["gaps"], [])
            self.assertIn(m["stopped_by"], ("seconds", "settled"))
            self.assertTrue(threading.active_count() >= 1)


if __name__ == "__main__":
    unittest.main()

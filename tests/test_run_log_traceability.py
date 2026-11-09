"""Traceable results (phase 17.1): evidence given with a repeated option is never lost, the end of an execution's
window, correcting the time of a result without rewriting it (`retime`) and marking an evidence as invalid
(`attach --invalid`). scripts/run_log.py, read by the Excel adapter and by scripts/qa_report.py.

Everything is fictional (the golden fixture's workbook, a temporary workspace); the clock is injected, never changed.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests", "golden")]

import qa_report as qr
import run_log as rl
from build_fixture import build

from adapters.excel import apply as ap

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
NOW = datetime(2026, 11, 8, 19, 43, 0)        # the moment of the record, injected
LATER = datetime(2026, 11, 8, 20, 10, 0)      # the moment of a correction, injected
RUN = {"environment": "test", "build": "1.4.0 (140)"}
BATCH = "20261108_01"
NAME = "batch_" + BATCH


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        build(os.path.join(self.ws, CFG["file"]))
        self.proj = {"project": "demo-shop", "environments": {"test": "https://shop.demo"}, "tracker": "excel",
                     "excel": copy.deepcopy(CFG)}
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(self.proj, f)

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, name=NAME):
        with open(os.path.join(self.ws, "runs", name + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def item(self, case="TC-SHOP-001", name=NAME):
        return next(x for x in self.log(name)["results"] if x["tc"] == case)

    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rl.main([self.ws, *args])
        return code, out.getvalue()

    def digests(self):
        out = {}
        for folder, _, files in os.walk(self.ws):
            for n in files:
                with open(os.path.join(folder, n), "rb") as f:
                    out[os.path.relpath(os.path.join(folder, n), self.ws)] = hashlib.sha256(f.read()).hexdigest()
        return out

    def add(self, case="TC-SHOP-001", status="Pass", now=NOW, **kw):
        return rl.add(self.ws, BATCH, case, status, "observed", environment="test", build="1.4.0 (140)", now=now, **kw)

    def apply_batch(self):
        path = os.path.join(self.ws, "runs", NAME + ".json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = ap.main([self.ws, path, "--no-recalc", "--now", NOW.isoformat()])
        self.assertEqual(rc, 0, out.getvalue())


class RepeatedEvidenceOption(Base):
    """A result written with one `--evidence` per file kept only the last path (a real test, 09.11.2026)."""

    def test_a_repeated_option_accumulates_the_paths_in_the_order_given(self):
        code, out = self.run_("add", BATCH, "TC-SHOP-001", "Pass", "ok", "--environment", "test", "--build",
                              "1.4.0 (140)", "--evidence", "evidence/b/a.png", "--evidence", "evidence/b/b.png",
                              "evidence/b/c.png")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.item()["evidence"], ["evidence/b/a.png", "evidence/b/b.png", "evidence/b/c.png"])

    def test_a_path_named_twice_is_kept_once_and_a_single_path_stays_a_string(self):
        self.run_("add", BATCH, "TC-SHOP-001", "Pass", "ok", "--environment", "test", "--build", "1.4.0 (140)",
                  "--evidence", "evidence/b/a.png", "--evidence", "evidence/b/a.png")
        self.assertEqual(self.item()["evidence"], "evidence/b/a.png")

    def test_attach_accumulates_the_repeated_option_after_the_recorded_paths(self):
        self.add(evidence=["evidence/b/a.png"])
        code, out = self.run_("attach", BATCH, "TC-SHOP-001", "--evidence", "evidence/b/b.png", "--evidence",
                              "evidence/b/c.png")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.item()["evidence"], ["evidence/b/a.png", "evidence/b/b.png", "evidence/b/c.png"])

    def test_the_add_says_how_many_paths_it_kept(self):
        code, out = self.run_("add", BATCH, "TC-SHOP-001", "Pass", "ok", "--environment", "test", "--build",
                              "1.4.0 (140)", "--evidence", "evidence/b/a.png", "--evidence", "evidence/b/b.png")
        self.assertEqual(code, 0, out)
        self.assertIn("2 evidence", out)


class EndOfTheWindow(Base):
    """`ts` is the start of the execution; `--ended-at` says when the case ended (the window of a case of many steps)."""

    def test_the_window_is_kept_next_to_the_start_and_the_record(self):
        self.add(executed_at="2026-11-08T19:20", executed_source="the session's commands", ended_at="2026-11-08T19:31")
        item = self.item()
        self.assertEqual((item["ts"], item["ended_at"], item["recorded_at"]),
                         ("2026-11-08T19:20:00", "2026-11-08T19:31:00", "2026-11-08T19:43:00"))

    def test_without_an_end_nothing_is_added(self):
        self.add(executed_at="2026-11-08T19:20", executed_source="the session's commands")
        self.assertNotIn("ended_at", self.item())

    def test_an_end_needs_a_known_start(self):
        before = self.digests()
        with self.assertRaises(rl.UsageError):
            self.add(ended_at="2026-11-08T19:31")                                  # no start: the start is now
        with self.assertRaises(rl.UsageError):
            self.add(executed_at="unknown", executed_source="notes", ended_at="2026-11-08T19:31")
        self.assertEqual(self.digests(), before)

    def test_an_end_before_the_start_or_in_the_future_is_refused_and_nothing_is_written(self):
        before = self.digests()
        for end in ("2026-11-08T19:10", "2026-11-08T19:44", "yesterday", "2026-11-08"):
            with self.assertRaises(rl.UsageError, msg=end):
                self.add(executed_at="2026-11-08T19:20", executed_source="notes", ended_at=end)
        self.assertEqual(self.digests(), before)

    def test_the_cli_takes_the_option(self):
        code, out = self.run_("add", BATCH, "TC-SHOP-001", "Pass", "ok", "--environment", "test", "--build",
                              "1.4.0 (140)", "--executed-at", "2026-11-08T19:20", "--executed-source", "notes",
                              "--ended-at", "2026-11-08T19:31")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.item()["ended_at"], "2026-11-08T19:31:00")
        code, out = self.run_("add", BATCH, "TC-SHOP-002", "Pass", "ok", "--ended-at", "2026-11-08T19:31")
        self.assertEqual(code, 2)
        self.assertIn("--executed-at", out)


class Retime(Base):
    """Correcting only the time of a result: everything else is kept and the previous time is recorded."""

    def setUp(self):
        super().setUp()
        self.add(evidence=["evidence/b/a.png", "evidence/b/b.png"], bug="BUG-1")        # recorded "now": ts = record

    def retime(self, **kw):
        kw.setdefault("executed_at", "2026-11-08T19:20")
        kw.setdefault("executed_source", "first command of the case in the session's log")
        return rl.retime(self.ws, BATCH, "TC-SHOP-001", now=LATER, **kw)

    def test_only_the_time_changes_and_the_rest_of_the_result_stays(self):
        before_item = self.item()
        _path, before, _after = self.retime(ended_at="2026-11-08T19:31")
        self.assertEqual(before, before_item)
        new = self.item()
        for key in ("tc", "status", "actual_result", "evidence", "bug", "recorded_at"):
            self.assertEqual(new[key], before_item[key], key)
        self.assertEqual((new["ts"], new["ended_at"]), ("2026-11-08T19:20:00", "2026-11-08T19:31:00"))
        self.assertEqual(new["executed_source"], "first command of the case in the session's log")
        self.assertEqual(self.log()["run"], RUN)

    def test_the_previous_time_is_kept_in_the_result(self):
        self.retime()
        self.assertEqual(self.item()["retimed"],
                         [{"at": "2026-11-08T20:10:00", "previous_ts": "2026-11-08T19:43:00"}])

    def test_a_second_correction_adds_to_the_history_and_never_replaces_it(self):
        self.retime()
        rl.retime(self.ws, BATCH, "TC-SHOP-001", "2026-11-08T19:25", "the instrument's log", now=LATER)
        history = self.item()["retimed"]
        self.assertEqual([h["previous_ts"] for h in history], ["2026-11-08T19:43:00", "2026-11-08T19:20:00"])
        self.assertEqual(history[1]["previous_source"], "first command of the case in the session's log")

    def test_an_unknown_time_removes_the_ts_and_says_so(self):
        self.retime(executed_at="unknown", executed_source="the notes have no time")
        item = self.item()
        self.assertNotIn("ts", item)
        self.assertTrue(item["ts_unknown"])
        self.assertEqual(item["retimed"][0]["previous_ts"], "2026-11-08T19:43:00")

    def test_the_same_time_again_changes_nothing(self):
        self.retime()
        before = self.digests()
        _path, b, a = self.retime()
        self.assertEqual(b, a)
        self.assertEqual(self.digests(), before)

    def test_a_time_needs_a_source_and_is_not_in_the_future_or_after_its_end(self):
        before = self.digests()
        with self.assertRaises(rl.UsageError):
            self.retime(executed_source=None)
        with self.assertRaises(rl.UsageError):
            self.retime(executed_at="2026-11-08T20:11")                              # after the injected clock
        with self.assertRaises(rl.UsageError):
            self.retime(ended_at="2026-11-08T19:10")                                 # before its own start
        with self.assertRaises(rl.UsageError):
            self.retime(executed_at="unknown", executed_source="notes", ended_at="2026-11-08T19:31")
        self.assertEqual(self.digests(), before)

    def test_an_unknown_batch_or_case_is_refused(self):
        before = self.digests()
        with self.assertRaises(rl.UsageError):
            rl.retime(self.ws, "20261108_99", "TC-SHOP-001", "2026-11-08T19:20", "notes", now=LATER)
        with self.assertRaises(rl.UsageError):
            rl.retime(self.ws, BATCH, "TC-SHOP-002", "2026-11-08T19:20", "notes", now=LATER)
        self.assertEqual(self.digests(), before)

    def test_an_applied_batch_is_never_touched(self):
        self.apply_batch()
        before = self.digests()
        with self.assertRaises(rl.Refused) as ctx:
            self.retime()
        self.assertIn("already", str(ctx.exception))
        self.assertEqual(self.digests(), before)

    def test_the_cli_corrects_the_time_and_prints_before_and_after(self):
        code, out = self.run_("retime", BATCH, "TC-SHOP-001", "--executed-at", "2026-11-08T19:20", "--executed-source",
                              "the session's log", "--ended-at", "2026-11-08T19:31")
        self.assertEqual(code, 0, out)
        self.assertIn("2026-11-08T19:43:00", out)
        self.assertIn("2026-11-08T19:20:00", out)
        self.assertEqual(self.item()["ended_at"], "2026-11-08T19:31:00")
        code, out = self.run_("retime", BATCH, "TC-SHOP-001", "--executed-at", "2026-11-08T19:20")
        self.assertEqual(code, 2)
        self.assertIn("--executed-source", out)

    def test_the_report_and_the_dry_run_read_a_retimed_result_as_any_other(self):
        self.retime(ended_at="2026-11-08T19:31")
        proj = qr.read_project(self.ws)
        audit = qr.audit(self.ws, proj, qr.load_batches(self.ws, proj))
        self.assertEqual(audit["batches"][0]["without_ts"], [])
        res = ap.dry_run(self.ws, os.path.join(self.ws, "runs", NAME + ".json"), recalc=False, now=NOW)
        self.assertEqual((res["problems"], res["code"]), ([], 0))


class InvalidEvidence(Base):
    """An evidence that shows nothing useful (a step that failed) stays on file and is marked, with the reason."""

    def setUp(self):
        super().setUp()
        self.add(evidence=["evidence/b/a.png", "evidence/b/bad.png"])

    def mark(self, paths=("evidence/b/bad.png",), reason="the step failed: the screen was unchanged", **kw):
        return rl.attach(self.ws, BATCH, "TC-SHOP-001", invalid=list(paths), reason=reason, now=LATER, **kw)

    def test_the_path_stays_in_the_evidence_and_is_listed_as_invalid_with_its_reason(self):
        _path, before, after = self.mark()
        self.assertEqual(after["evidence"], before["evidence"])
        self.assertEqual(after["evidence_invalid"],
                         [{"path": "evidence/b/bad.png", "reason": "the step failed: the screen was unchanged",
                           "at": "2026-11-08T20:10:00"}])
        for key in ("status", "actual_result", "ts", "recorded_at"):
            self.assertEqual(after[key], before[key], key)

    def test_the_same_mark_again_changes_nothing_and_another_reason_is_refused(self):
        self.mark()
        before = self.digests()
        _path, b, a = self.mark()
        self.assertEqual(b, a)
        with self.assertRaises(rl.Refused):
            self.mark(reason="another reason")
        self.assertEqual(self.digests(), before)

    def test_a_path_that_is_not_in_the_result_and_a_missing_reason_are_refused(self):
        before = self.digests()
        with self.assertRaises(rl.UsageError):
            self.mark(paths=("evidence/b/other.png",))
        with self.assertRaises(rl.UsageError):
            self.mark(reason=None)
        self.assertEqual(self.digests(), before)

    def test_an_applied_batch_is_never_touched(self):
        self.apply_batch()
        before = self.digests()
        with self.assertRaises(rl.Refused):
            self.mark()
        self.assertEqual(self.digests(), before)

    def test_the_cli_marks_one_or_several_paths(self):
        code, out = self.run_("attach", BATCH, "TC-SHOP-001", "--invalid", "evidence/b/bad.png", "--reason", "failed step")
        self.assertEqual(code, 0, out)
        self.assertEqual([e["path"] for e in self.item()["evidence_invalid"]], ["evidence/b/bad.png"])
        code, out = self.run_("attach", BATCH, "TC-SHOP-001", "--invalid", "evidence/b/a.png")
        self.assertEqual(code, 2)
        self.assertIn("--reason", out)

    def test_the_report_and_the_dry_run_read_the_marked_result_as_any_other(self):
        self.mark()
        proj = qr.read_project(self.ws)
        audit = qr.audit(self.ws, proj, qr.load_batches(self.ws, proj))
        self.assertEqual(audit["batches"][0]["without_ts"], [])
        res = ap.dry_run(self.ws, os.path.join(self.ws, "runs", NAME + ".json"), recalc=False, now=NOW)
        self.assertEqual((res["problems"], res["code"]), ([], 0))


if __name__ == "__main__":
    unittest.main()

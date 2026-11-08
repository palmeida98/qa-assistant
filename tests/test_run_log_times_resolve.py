"""The time of an execution against the time of its record (A-01), and the traceable resolution of a Question (A-03):
scripts/run_log.py `add --executed-at` and `resolve`, read by scripts/qa_report.py and applied by the Excel adapter.

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
from common import parse_when, ts_key

from adapters.excel import apply as ap

with open(os.path.join(ROOT, "adapters", "excel", "example.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
NOW = datetime(2026, 11, 8, 19, 43, 0)        # the moment of the record, injected
RUN = {"environment": "test", "build": "1.4.0 (140)"}


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

    def log(self, name):
        with open(os.path.join(self.ws, "runs", name + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def write(self, name, results, run=RUN):
        os.makedirs(os.path.join(self.ws, "runs"), exist_ok=True)
        with open(os.path.join(self.ws, "runs", name + ".json"), "w", encoding="utf-8") as f:
            json.dump({"run": run, "results": results}, f)

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

    def totals(self):
        proj = qr.read_project(self.ws)
        return qr.totals(self.ws, proj, qr.load_batches(self.ws, proj))

    def audit(self):
        proj = qr.read_project(self.ws)
        return {b["batch"]: b for b in qr.audit(self.ws, proj, qr.load_batches(self.ws, proj))["batches"]}

    def current(self, case):
        platform = next(iter(self.totals()["platforms"].values()))
        return platform


class ExecutionTime(Base):
    def add(self, case="TC-SHOP-001", status="Pass", **kw):
        return rl.add(self.ws, "20261105_01", case, status, "observed", environment="test", build="1.4.0 (140)",
                      now=NOW, **kw)

    def test_without_the_option_the_execution_is_now_and_the_record_says_so(self):
        self.add()
        item = self.log("batch_20261105_01")["results"][0]
        self.assertEqual((item["ts"], item["recorded_at"]), ("2026-11-08T19:43:00", "2026-11-08T19:43:00"))
        self.assertNotIn("executed_source", item)

    def test_an_earlier_observation_keeps_its_time_and_names_its_source(self):
        self.add(executed_at="2026-11-05T16:21", executed_source="tester notes of 05.11")
        item = self.log("batch_20261105_01")["results"][0]
        self.assertEqual(item["ts"], "2026-11-05T16:21:00")
        self.assertEqual(item["recorded_at"], "2026-11-08T19:43:00")
        self.assertEqual(item["executed_source"], "tester notes of 05.11")
        self.assertNotIn("ts_unknown", item)

    def test_an_unknown_time_is_not_invented(self):
        self.add(executed_at="unknown", executed_source="notes without a time")
        item = self.log("batch_20261105_01")["results"][0]
        self.assertNotIn("ts", item)
        self.assertTrue(item["ts_unknown"])
        self.assertEqual(item["recorded_at"], "2026-11-08T19:43:00")
        self.assertEqual(self.audit()["batch_20261105_01"]["without_ts"], ["TC-SHOP-001"])

    def test_a_time_needs_a_source_and_a_source_needs_a_time(self):
        before = self.digests()
        with self.assertRaises(rl.UsageError):
            self.add(executed_at="2026-11-05T16:21")
        with self.assertRaises(rl.UsageError):
            self.add(executed_source="notes")
        self.assertEqual(self.digests(), before)

    def test_invalid_future_and_incomplete_times_are_refused_and_nothing_is_written(self):
        before = self.digests()
        for bad in ("2026-11-05", "yesterday", "2026-13-40T10:00", "2026-11-09T10:00", "2026-11-08T19:44",
                    "2026-11-05T25:00", "05.11.2026 16:21"):
            with self.assertRaises(rl.UsageError, msg=bad):
                self.add(executed_at=bad, executed_source="notes")
        self.assertEqual(self.digests(), before)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs")))

    def test_the_zone_is_kept_as_given_and_never_assigned(self):
        self.assertEqual(parse_when("2026-11-05T16:21", NOW), "2026-11-05T16:21:00")         # no zone: none added
        self.assertEqual(parse_when("2026-11-05 16:21:09", NOW), "2026-11-05T16:21:09")
        self.assertEqual(parse_when("2026-11-05T16:21:00Z", NOW), "2026-11-05T16:21:00+00:00")
        self.assertEqual(parse_when("2026-11-05T16:21+01:00", NOW), "2026-11-05T16:21:00+01:00")
        # the future is judged in the same scale: 20:00 at +05:00 is 15:00 UTC, before 19:43 local only when local >= UTC
        with self.assertRaises(ValueError):
            parse_when("2026-11-08T23:59+00:00", datetime(2026, 11, 8, 12, 0))   # later than any local reading of 12:00

    def test_a_time_with_a_zone_and_one_without_compare_in_one_scale(self):
        self.assertEqual(ts_key("2026-11-05T16:21:00"), "2026-11-05T16:21:00")
        self.assertEqual(ts_key(""), "")
        self.assertEqual(ts_key("not a time"), "not a time")             # an old, odd value orders as before
        self.assertEqual(ts_key("2026-11-05T16:21:00+00:00"), ts_key("2026-11-05T17:21:00+01:00"))

    def test_the_record_time_never_makes_an_old_execution_look_newer(self):
        # TC-SHOP-001: run on 06.11 and recorded at once; an older observation (05.11) recorded LATER (08.11)
        self.write("batch_20261106_01", [{"tc": "TC-SHOP-001", "status": "Fail", "actual_result": "late run",
                                          "ts": "2026-11-06T10:00:00"}])
        self.add(executed_at="2026-11-05T16:21", executed_source="tester notes of 05.11")
        platform = self.current("TC-SHOP-001")
        self.assertEqual(platform["statuses"], {"Fail": 1})                  # the 06.11 execution stays current
        self.assertEqual(platform["historical_results"], 1)
        # and the other way: a later execution recorded earlier does not lose to a late record of an older one
        self.write("batch_20261107_01", [{"tc": "TC-SHOP-002", "status": "Pass", "actual_result": "x",
                                          "ts": "2026-11-07T10:00:00", "recorded_at": "2026-11-07T10:00:00"}])
        rl.add(self.ws, "20261105_02", "TC-SHOP-002", "Fail", "old notes", environment="test", build="1.4.0 (140)",
               now=NOW, executed_at="2026-11-05T09:00", executed_source="notes")
        self.assertEqual(self.current("TC-SHOP-002")["statuses"]["Pass"], 1)

    def test_an_unknown_time_never_becomes_current_over_a_dated_execution(self):
        self.write("batch_20261106_01", [{"tc": "TC-SHOP-001", "status": "Pass", "actual_result": "dated",
                                          "ts": "2026-11-06T10:00:00"}])
        self.add(status="Fail", executed_at="unknown", executed_source="notes without a time")
        platform = self.current("TC-SHOP-001")
        self.assertEqual(platform["statuses"], {"Pass": 1})
        self.assertEqual(platform["without_ts"], ["TC-SHOP-001"])

    def test_run_logs_from_before_the_new_fields_are_read_as_always(self):
        self.write("batch_20260927_01", [{"tc": "TC-SHOP-001", "status": "Pass", "actual_result": "old",
                                          "ts": "2026-09-27T10:00:00"},
                                         {"tc": "TC-SHOP-002", "status": "Fail", "actual_result": "old",
                                          "ts": "2026-09-27T10:05:00"}])
        platform = self.current("TC-SHOP-001")
        self.assertEqual((platform["cases"], platform["statuses"], platform["historical_results"]),
                         (2, {"Fail": 1, "Pass": 1}, 0))
        self.assertTrue(platform["reconciled"])

    def test_the_cli_takes_the_new_options(self):
        code, out = self.run_("add", "20261105_03", "TC-SHOP-001", "Pass", "ok", "--environment", "test", "--build",
                              "1.4.0 (140)", "--executed-at", "2026-11-05T16:21", "--executed-source", "notes")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.log("batch_20261105_03")["results"][0]["ts"], "2026-11-05T16:21:00")
        code, out = self.run_("add", "20261105_03", "TC-SHOP-002", "Pass", "ok", "--executed-at", "2026-11-05T16:21")
        self.assertEqual(code, 2)
        self.assertIn("--executed-source", out)
        code, out = self.run_("add", "20261105_03", "TC-SHOP-002", "Pass", "ok", "--executed-at", "2099-01-01T00:00",
                              "--executed-source", "x")
        self.assertEqual(code, 2)
        self.assertIn("future", out)


class ResolveQuestion(Base):
    def question_batch(self, name="batch_20261105_01"):
        self.write(name, [
            {"tc": "TC-SHOP-001", "status": "Question", "actual_result": "is 2 the limit?", "ts": "2026-11-05T16:21:00",
             "recorded_at": "2026-11-05T16:30:00", "evidence": ["evidence/b/TC-SHOP-001_a.txt"]},
            {"tc": "TC-SHOP-002", "status": "Pass", "actual_result": "ok", "ts": "2026-11-05T16:22:00"}])
        return name

    def resolve(self, batch="20261105_01", case="TC-SHOP-001", status="Pass", **kw):
        kw.setdefault("reason", "the PO confirmed 2 is allowed")
        kw.setdefault("decided_by", "PO (fictional)")
        kw.setdefault("now", NOW)
        return rl.resolve(self.ws, batch, case, status, **kw)

    def applied_batch_with_a_question(self, name="batch_20261105_01"):
        """A legacy batch: applied (its name is in the control sheet) and whose run log still holds a Question. The
        adapter refuses to apply a Question, so the batch is applied with Pass and the log edited afterwards."""
        self.write(name, [{"tc": "TC-SHOP-001", "status": "Pass", "actual_result": "was answered elsewhere",
                           "ts": "2026-11-05T16:21:00"}])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(ap.main([self.ws, os.path.join(self.ws, "runs", name + ".json"), "--no-recalc"]), 0,
                             out.getvalue())
        doc = self.log(name)
        doc["results"][0].update({"status": "Question", "actual_result": "is 2 the limit?",
                                  "recorded_at": "2026-11-05T16:30:00", "evidence": ["evidence/b/TC-SHOP-001_a.txt"]})
        with open(os.path.join(self.ws, "runs", name + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return name

    # --- a batch not applied: in place, the original kept inside the item
    def test_a_pending_question_is_listed_as_open_until_it_is_resolved(self):
        self.question_batch()
        self.assertEqual(self.audit()["batch_20261105_01"]["questions"], ["TC-SHOP-001"])
        self.assertTrue(self.audit()["batch_20261105_01"]["to_fix"])

    def test_resolving_in_place_keeps_the_observation_time_and_evidence(self):
        self.question_batch()
        _path, how, _before, item = self.resolve()
        self.assertEqual(how, "in place")
        stored = self.log("batch_20261105_01")["results"][0]
        self.assertEqual(stored, item)
        self.assertEqual((stored["status"], stored["actual_result"], stored["ts"], stored["evidence"],
                          stored["recorded_at"]),
                         ("Pass", "is 2 the limit?", "2026-11-05T16:21:00", ["evidence/b/TC-SHOP-001_a.txt"],
                          "2026-11-05T16:30:00"))
        res = stored["resolution"]
        self.assertEqual((res["from_status"], res["reason"], res["decided_by"], res["decided_at"]),
                         ("Question", "the PO confirmed 2 is allowed", "PO (fictional)", "2026-11-08T19:43:00"))
        self.assertEqual(res["original"]["status"], "Question")
        self.assertEqual(res["original"]["ts"], "2026-11-05T16:21:00")
        self.assertEqual(self.log("batch_20261105_01")["results"][1]["status"], "Pass")     # the others untouched

    def test_after_the_resolution_the_audit_lists_it_and_the_batch_is_ready_to_apply(self):
        self.question_batch()
        self.resolve()
        entry = self.audit()["batch_20261105_01"]
        self.assertEqual((entry["questions"], entry["resolved"]), ([], ["TC-SHOP-001"]))
        res = ap.dry_run(self.ws, os.path.join(self.ws, "runs", "batch_20261105_01.json"), recalc=False, now=NOW)
        self.assertEqual((res["problems"], res["code"]), ([], 0))

    def test_a_resolved_question_counts_once_and_keeps_the_observation_time(self):
        self.question_batch()
        self.resolve(status="Fail")
        platform = self.current("TC-SHOP-001")
        self.assertEqual((platform["cases"], platform["statuses"], platform["historical_results"]),
                         (2, {"Fail": 1, "Pass": 1}, 0))
        self.assertTrue(platform["reconciled"])

    def test_a_later_real_execution_is_current_over_a_resolution(self):
        self.question_batch()
        self.resolve(status="Pass")
        self.write("batch_20261107_01", [{"tc": "TC-SHOP-001", "status": "Fail", "actual_result": "broke again",
                                          "ts": "2026-11-07T09:00:00"}])
        platform = self.current("TC-SHOP-001")
        self.assertEqual(platform["statuses"], {"Fail": 1, "Pass": 1})
        self.assertEqual(platform["historical_results"], 1)
        # a resolution decided today for an observation of 05.11 does not beat an execution of 07.11, as above;
        # the reverse also holds: an execution BEFORE the observation does not beat the resolution
        self.write("batch_20261101_01", [{"tc": "TC-SHOP-002", "status": "Fail", "actual_result": "older",
                                          "ts": "2026-11-01T09:00:00"}])
        self.assertEqual(self.current("TC-SHOP-002")["statuses"]["Pass"], 1)

    # --- a batch already applied: a new batch, the old one untouched
    def test_an_applied_batch_is_never_touched_the_decision_goes_to_a_new_batch(self):
        name = self.applied_batch_with_a_question()
        original = self.digests()["runs/" + name + ".json"]
        with self.assertRaises(rl.Refused) as ctx:
            self.resolve()
        self.assertIn("--into", str(ctx.exception))
        _path, how, _, _item = self.resolve(into="20261108_01")
        self.assertEqual(how, "new batch")
        self.assertEqual(self.digests()["runs/" + name + ".json"], original)
        new = self.log("batch_20261108_01")
        self.assertEqual(new["run"], RUN)
        self.assertTrue(new["mode"].startswith("manual: resolution"))
        got = new["results"][0]
        self.assertEqual((got["status"], got["actual_result"], got["ts"], got["evidence"]),
                         ("Pass", "is 2 the limit?", "2026-11-05T16:21:00", ["evidence/b/TC-SHOP-001_a.txt"]))
        self.assertEqual(got["revision_of"], {"batch": name, "tc": "TC-SHOP-001"})
        self.assertEqual(got["recorded_at"], "2026-11-08T19:43:00")
        self.assertEqual(got["resolution"]["original"]["status"], "Question")

    def test_the_new_batch_is_read_by_the_audit_and_totals_without_duplicating(self):
        name = self.applied_batch_with_a_question()
        self.resolve(into="20261108_01", status="Fail")
        audit = self.audit()
        self.assertEqual(audit[name]["questions"], [])
        self.assertEqual(audit[name]["resolved"], ["TC-SHOP-001 (in batch_20261108_01)"])
        self.assertEqual(audit["batch_20261108_01"]["resolved"], [])
        platform = self.current("TC-SHOP-001")
        self.assertEqual((platform["cases"], platform["statuses"]), (1, {"Fail": 1}))
        self.assertEqual((platform["historical_results"], platform["retests"]), (0, {}))
        self.assertEqual(platform["resolved_questions"], {"TC-SHOP-001": [name]})
        self.assertTrue(platform["reconciled"])

    def test_the_new_batch_applies_with_its_own_dry_run_and_changes_the_workbook_only_then(self):
        name = self.applied_batch_with_a_question()
        self.resolve(into="20261108_01", status="Fail")
        new = os.path.join(self.ws, "runs", "batch_20261108_01.json")
        before = self.digests()
        res = ap.dry_run(self.ws, new, recalc=False, now=NOW)
        self.assertEqual((res["problems"], res["code"]), ([], 0))
        self.assertTrue(any(c["case"] == "TC-SHOP-001" and c["after"] == "Fail" for c in res["cells"]), res["cells"])
        self.assertEqual(self.digests(), before)                                   # a dry run writes nothing
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = ap.main([self.ws, new, "--no-recalc", "--now", "2026-11-09T08:00:00", "--expect", res["token"]])
        self.assertEqual(rc, 4)                                  # the token covers --now: a different option refuses
        self.assertEqual(self.digests()["runs/" + name + ".json"], before["runs/" + name + ".json"])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = ap.main([self.ws, new, "--no-recalc", "--now", NOW.isoformat(), "--expect", res["token"]])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(self.digests()["runs/" + name + ".json"], before["runs/" + name + ".json"])

    # --- refusals: nothing is written
    def test_the_refusals_write_nothing(self):
        self.question_batch()
        self.write("batch_other", [{"tc": "TC-SHOP-001", "status": "Pass", "actual_result": "x", "ts": "2026-11-05T10:00:00"}])
        before = self.digests()
        cases = [
            (rl.UsageError, {"case": "TC-SHOP-002"}),                         # not a Question
            (rl.UsageError, {"case": "TC-NOPE-001"}),                         # no such case in the batch
            (rl.UsageError, {"batch": "20990101_01"}),                        # no such batch
            (rl.UsageError, {"status": "Question"}),                          # not a decision
            (rl.UsageError, {"status": "Whatever"}),                          # outside the vocabulary
            (rl.UsageError, {"reason": "  "}),                                # no reason
            (rl.UsageError, {"decided_by": ""}),                              # nobody decided
            (rl.UsageError, {"into": "20261105_01"}),                         # into itself
            (rl.UsageError, {"into": "../elsewhere"}),                        # a path out of the workspace
            (rl.UsageError, {"batch": "../elsewhere"}),
            (rl.UsageError, {"into": "other"}),                                  # the target already has the case
        ]
        for exc, kw in cases:
            with self.assertRaises(exc, msg=kw):
                self.resolve(**kw)
            self.assertEqual(self.digests(), before, kw)

    def test_a_question_is_resolved_once_and_a_resolution_is_not_resolved_again(self):
        name = self.applied_batch_with_a_question()
        self.resolve(into="20261108_01", status="Fail")
        with self.assertRaises(rl.Refused) as ctx:
            self.resolve(into="20261108_02", status="Pass")
        self.assertIn("already resolved", str(ctx.exception))
        before = self.digests()
        with self.assertRaises((rl.Refused, rl.UsageError)):         # the resolution is not a Question: no cycle
            rl.resolve(self.ws, "20261108_01", "TC-SHOP-001", "Pass", reason="again", decided_by="x", into="20261108_03")
        self.assertEqual(self.digests(), before)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "runs", "batch_20261108_03.json")))
        # in place: the same
        self.question_batch("batch_20261106_01")
        self.resolve("20261106_01")
        with self.assertRaises(rl.UsageError):
            self.resolve("20261106_01")
        self.assertTrue(name)

    def test_a_revision_that_points_nowhere_is_a_problem_the_audit_shows(self):
        self.write("batch_20261108_01", [{"tc": "TC-SHOP-001", "status": "Pass", "actual_result": "x",
                                          "ts": "2026-11-05T10:00:00",
                                          "revision_of": {"batch": "batch_ghost", "tc": "TC-SHOP-001"},
                                          "resolution": {"from_status": "Question", "reason": "r"}}])
        entry = self.audit()["batch_20261108_01"]
        self.assertEqual(len(entry["invalid_revisions"]), 1)
        self.assertIn("does not exist", entry["invalid_revisions"][0])
        self.assertTrue(entry["to_fix"])

    def test_the_cli_resolves_and_says_the_workbook_still_needs_its_approval(self):
        self.question_batch()
        code, out = self.run_("resolve", "20261105_01", "TC-SHOP-001", "Pass", "--reason", "PO confirmed",
                              "--decided-by", "PO (fictional)", "--decision-source", "message of 06.11")
        self.assertEqual(code, 0, out)
        self.assertIn("dry run", out)
        self.assertEqual(self.log("batch_20261105_01")["results"][0]["resolution"]["source"], "message of 06.11")
        code, out = self.run_("resolve", "20261105_01", "TC-SHOP-002", "Pass", "--reason", "r", "--decided-by", "x")
        self.assertEqual(code, 2)
        self.assertIn("not a Question", out)
        with self.assertRaises(SystemExit):                          # --reason and --decided-by are required
            self.run_("resolve", "20261105_01", "TC-SHOP-001", "Pass")


if __name__ == "__main__":
    unittest.main()

"""scripts/pbi_state.py: the resume record of /test-pbi (docs/skills-spec.md section 5). A fictional workspace in a
temporary folder; nothing real is read or written.

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
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import pbi_state as ps
import workspace_kit as kit


class PbiState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write('project: "Demo Shop"\ntracker: "excel"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ps.main([self.ws, *args])
        return code, out.getvalue()

    def record(self, pbi="1234"):
        with open(os.path.join(self.ws, "memory", "pbi", f"{pbi}.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_start_creates_the_steps_per_platform_and_resuming_adds_without_removing(self):
        code, out = self.run_("start", "1234", "--task", "1240", "--platform", "android")
        self.assertEqual(code, 0)
        self.assertIn("created", out)
        self.assertIn("Next: context", out)
        self.assertIn("run:android", self.record()["steps"])
        code, out = self.run_("start", "1234", "--platform", "ios")
        self.assertIn("resumed", out)
        doc = self.record()
        self.assertEqual(doc["platforms"], ["android", "ios"])
        self.assertEqual(doc["task"], "1240")
        self.assertIn("run:ios", doc["steps"])

    def test_a_link_gives_the_id_and_another_host_shape_is_refused(self):
        with open(os.path.join(self.ws, "project.yaml"), "a", encoding="utf-8") as f:
            f.write("tickets:\n  sources:\n    boards: {service: azure-devops, organization_url: "
                    "'https://dev.azure.com/demo-shop'}\n    jira: {service: jira, base_url: "
                    "'https://demo-shop.atlassian.net'}\n")
        self.assertEqual(ps.pbi_id("https://dev.azure.com/demo-shop/Demo%20Shop/_workitems/edit/1234/", self.ws), "1234")
        self.assertEqual(ps.pbi_id("https://demo-shop.atlassian.net/browse/DS-12", self.ws), "DS-12")
        with self.assertRaises(ps.UsageError):        # another host (audit 06.11.2026: it was accepted)
            ps.pbi_id("https://evil.example.com/browse/1234", self.ws)
        code, _ = self.run_("start", "../escape")
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "memory")))

    def test_done_needs_proof_and_marking_it_again_changes_nothing(self):
        self.run_("start", "1234", "--platform", "android")
        self.assertEqual(self.run_("mark", "1234", "context", "done")[0], 2)          # no --ref
        self.assertEqual(self.run_("mark", "1234", "context", "done", "--ref", "context.md")[0], 0)
        before = self.record()
        code, out = self.run_("mark", "1234", "context", "done", "--ref", "context.md")
        self.assertEqual(code, 0)
        self.assertIn("nothing changed", out)
        self.assertEqual(self.record()["history"], before["history"])                # nothing repeated, nothing added

    def test_resume_goes_to_the_first_step_not_done(self):
        self.run_("start", "1234", "--platform", "android", "--platform", "ios")
        for step, ref in (("context", "context.md"), ("analyze", "memory/checks/20261101-100000.json"),
                          ("plan", "analysis/coupon_20261101.md"), ("run:android", "batch_20261101_DS12_android")):
            self.run_("mark", "1234", step, "done", "--ref", ref)
        self.run_("mark", "1234", "run:ios", "blocked", "--note", "no iPhone connected")
        code, out = self.run_("show", "1234")
        self.assertEqual(code, 0)
        self.assertIn("Next: bugs", out)                       # the blocked platform does not stop the rest
        self.assertIn("no iPhone connected", out)

    def test_a_step_of_a_platform_not_started_is_refused(self):
        self.run_("start", "1234", "--platform", "android")
        code, out = self.run_("mark", "1234", "run:ios", "done", "--ref", "x")
        self.assertEqual(code, 2)
        self.assertIn("--platform", out)

    def test_check_names_what_changed_and_what_to_review(self):
        self.run_("start", "1234", "--platform", "android")
        code, out = self.run_("check", "1234", "--build", "android=1.0.0 (7)")
        self.assertEqual(code, 3)                               # no baseline: not concluded, never "nothing changed"
        self.assertNotIn("nothing changed", out)
        self.run_("baseline", "1234", "--ticket", "1234=12", "--build", "android=1.0.0 (7)", "--environment", "test")
        self.assertEqual(self.run_("check", "1234", "--ticket", "1234=12", "--build", "android=Demo 1.0.0 (code 7)",
                                   "--environment", "test")[0],
                         0)                                     # the same build written another way
        code, out = self.run_("check", "1234", "--ticket", "1234=13", "--build", "android=1.0.1 (8)")
        self.assertEqual(code, 1)
        self.assertIn("CHANGED ticket 1234: 12 -> 13", out)
        self.assertIn("CHANGED build android: 1.0.0 (7) -> 1.0.1 (8)", out)
        self.assertIn("stay valid for 1.0.0 (7)", out)          # old results are not thrown away

    def test_a_bug_is_never_published_twice(self):
        self.run_("start", "1234", "--platform", "android")
        self.assertEqual(self.run_("published", "1234", "draft-1", "BUG-9")[0], 2)    # no confirmation from the tracker
        ok = ("--confirmed-by", "memory/checks/20261106-100000.json")
        self.assertEqual(self.run_("published", "1234", "draft-1", "BUG-9", *ok)[0], 0)
        code, out = self.run_("published", "1234", "draft-1", "BUG-9", *ok)
        self.assertEqual(code, 0)
        self.assertIn("nothing changed", out)
        self.assertEqual(self.run_("published", "1234", "draft-2", "BUG-9", *ok)[0], 1)    # one key, two drafts
        self.assertEqual(self.run_("published", "1234", "", "BUG-11", *ok)[0], 2)
        self.assertEqual(self.run_("published", "1234", "draft-3", " ", *ok)[0], 2)
        code, out = self.run_("published", "1234", "draft-1", "BUG-10", *ok)
        self.assertEqual(code, 1)
        self.assertIn("never publish a bug twice", out)
        self.assertEqual(self.record()["published"]["draft-1"]["key"], "BUG-9")

    def test_check_says_what_it_could_not_compare_and_reopens_on_request(self):
        self.run_("start", "1234", "--platform", "android")
        for step in ("context", "analyze", "plan", "run:android"):
            self.run_("mark", "1234", step, "done", "--ref", "x")
        self.run_("baseline", "1234", "--ticket", "1234=12", "--ticket", "1240=3", "--build", "android=1.0.0 (7)")
        code, out = self.run_("check", "1234", "--ticket", "1234=12", "--build", "android=1.0.0 (7)")
        self.assertEqual(code, 3)
        self.assertIn("NOT COMPARED: ticket 1240", out)
        code, out = self.run_("check", "1234", "--ticket", "1234=13", "--ticket", "1240=3", "--build",
                              "android=1.0.0 (7)", "--reopen")
        self.assertEqual(code, 1)
        self.assertIn("reopened", out)
        self.assertIn("Next: analyze", self.run_("show", "1234")[1])        # back to the first step to review
        self.assertEqual(self.record()["steps"]["plan"]["status"], "pending")

    def test_a_blocked_step_needs_its_reason(self):
        self.run_("start", "1234", "--platform", "ios")
        self.assertEqual(self.run_("mark", "1234", "run:ios", "blocked")[0], 2)

    def test_a_second_writer_waits_then_writes_nothing(self):
        self.run_("start", "1234", "--platform", "android")
        before = self.record()
        with mock.patch.dict(os.environ, {"QA_LOCK_WAIT": "0.2"}), kit.held_by_another_writer(self.ws):
            code, _ = self.run_("mark", "1234", "context", "done", "--ref", "context.md")
        self.assertEqual(code, 5)
        self.assertEqual(self.record(), before)

    def test_a_broken_record_is_said_and_left_alone(self):
        os.makedirs(os.path.join(self.ws, "memory", "pbi"))
        path = os.path.join(self.ws, "memory", "pbi", "1234.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        code, out = self.run_("show", "1234")
        self.assertEqual(code, 2)
        self.assertIn("nothing was changed", out)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{broken")

    def test_not_a_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as other:
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                self.assertEqual(ps.main([other, "start", "1234"]), 2)
            self.assertEqual(os.listdir(other), [])


class OpenItems(unittest.TestCase):
    """Progress saved with work open, and the flow closed only when nothing is left (skills-spec.md §5.1). The shape
    is that of a real PBI resumed several times (iOS run, write guard out of reach, Android without a device, a build
    without a source and a product question), with fictional ids and texts."""

    PBI = "DS-34"
    B1, B2 = "batch_ios_20261113_ds34_p2/TC-SHOP-112", "batch_ios_20261113_ds34_p2/TC-SHOP-115"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write('project: "Demo Shop"\ntracker: "excel"\n')
        self.path = os.path.join(self.ws, "memory", "pbi", f"{self.PBI}.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ps.main([self.ws, *args])
        return code, out.getvalue()

    def ok(self, *args):
        code, out = self.run_(*args)
        self.assertEqual(code, 0, out)
        return out

    def raw(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()

    def record(self) -> dict:
        return json.loads(self.raw())

    def ios_done(self):
        """iOS run and report of what exists done; Android without a device."""
        self.ok("start", self.PBI, "--task", "DS-40", "--platform", "ios", "--platform", "android")
        for step, ref in (("context", "context.md"), ("analyze", "memory/checks/20261113-011600.json"),
                          ("plan", "analysis/plan_ds34_20261113.md"), ("run:ios", "runs/batch_ios_20261113_ds34.json")):
            self.ok("mark", self.PBI, step, "done", "--ref", ref)
        self.ok("mark", self.PBI, "run:android", "blocked", "--note", "no Android device connected; QA account to "
                "confirm. Unblocks: the QA connects the device and confirms the account")

    @staticmethod
    def block(out: str) -> str:
        """The state block: from the line Next: to the end."""
        return out[out.index("Next:"):]

    # --- iOS done, the write guard out of reach, a dry run of the workbook waiting on the QA --------------------------

    def test_ios_done_guard_unreachable_and_a_dry_run_waiting_on_the_qa(self):
        self.ios_done()
        self.ok("mark", self.PBI, "bugs", "blocked", "--note", "B1 ready; the write guard failed to connect in this "
                "session. Unblocks: the QA reconnects it (/mcp)")
        self.ok("open", self.PBI, self.B1, "--kind", "publish", "--step", "bugs", "--by", "qa",
                "--missing", "publish B1 (review approved)", "--unblocks", "QA: reconnect the write guard")
        self.ok("open", self.PBI, "wb-add-111-117", "--kind", "approval", "--missing",
                "approve the dry run v1-0000000000000001 (7 cases, sheet Agent)", "--unblocks", "QA")
        out = self.ok("show", self.PBI)
        self.assertIn("Next: report", out)                     # the independent work goes on
        self.assertNotIn("nothing pending", out)
        for needle in ("Waiting on the QA (a manual confirmation or action):", f"  - {self.B1} [publish, bugs]: publish B1", "wb-add-111-117 [approval]",
                       "Blocked (go back to them when unblocked): run:android, bugs",
                       "  - bugs: B1 ready; the write guard failed to connect",
                       "Closing the flow: not yet (1 step(s) pending, 2 blocked, 2 open item(s))"):
            self.assertIn(needle, out)
        # the progress is saved; the closing is refused and writes nothing
        self.ok("mark", self.PBI, "report", "done", "--ref", "runs/L78_ds34_ios.md")
        out = self.ok("saved", self.PBI, "--ref", "context.md", "--note", "iOS done; B1 and the workbook wait")
        self.assertIn("the flow stays open", out)
        self.assertIn("Last intermediate checkpoint:", out)
        self.assertIn("(progress saved: it approves no plan, run or write)", out)
        self.assertEqual(self.record()["steps"]["checkpoint"]["status"], "pending")
        self.assertIn("no work available in this session", self.ok("show", self.PBI))
        before = self.raw()
        code, out = self.run_("mark", self.PBI, "checkpoint", "done", "--ref", "context.md")
        self.assertEqual(code, 4)
        for needle in ("NOT CLOSED", "step run:android blocked", "step bugs blocked", f"item {self.B1} open",
                       "item wb-add-111-117 open", "saved"):
            self.assertIn(needle, out)
        self.assertEqual(self.raw(), before)
        # the same intermediate checkpoint again changes nothing
        self.assertIn("nothing changed", self.ok("saved", self.PBI, "--ref", "context.md", "--note",
                                                 "iOS done; B1 and the workbook wait"))
        self.assertEqual(self.raw(), before)

    def test_an_approval_is_closed_only_with_who_gave_it(self):
        self.ios_done()
        self.ok("open", self.PBI, "wb-add-111-117", "--kind", "approval", "--missing", "approve the dry run",
                "--unblocks", "QA")
        self.assertEqual(self.run_("close", self.PBI, "wb-add-111-117", "--decided-by", "QA")[0], 2)    # no proof
        self.assertEqual(self.run_("close", self.PBI, "wb-add-111-117", "--ref", "token v1-01")[0], 2)  # nobody
        out = self.ok("close", self.PBI, "wb-add-111-117", "--ref", "applied with --expect v1-01; read back",
                      "--decided-by", "QA (session 13.11)")
        self.assertIn("closed", out)
        closed = self.record()["closed"]["wb-add-111-117"]
        self.assertEqual(closed["decided_by"], "QA (session 13.11)")
        self.assertNotIn("open", self.record())                 # an empty open list is not left behind
        history = self.record()["history"]
        self.assertIn("nothing changed", self.ok("close", self.PBI, "wb-add-111-117", "--ref",
                                                 "applied with --expect v1-01; read back", "--decided-by", "QA"))
        self.assertEqual(self.run_("close", self.PBI, "wb-add-111-117", "--ref", "other")[0], 1)
        self.assertEqual(self.run_("open", self.PBI, "wb-add-111-117", "--kind", "approval", "--missing", "x",
                                   "--unblocks", "QA")[0], 1)    # a closed id is not reused
        self.assertEqual(self.record()["history"], history)

    def test_resuming_updates_the_same_item_and_refuses_a_second_one(self):
        self.ios_done()
        args = ("--kind", "publish", "--step", "bugs", "--missing", "publish B1", "--unblocks", "QA: reconnect")
        self.ok("open", self.PBI, self.B1, *args)
        history = self.record()["history"]
        self.assertIn("nothing changed", self.ok("open", self.PBI, self.B1, *args))
        self.assertEqual(self.record()["history"], history)
        self.assertEqual(self.run_("open", self.PBI, self.B1, "--kind", "publish", "--missing", "other",
                                   "--unblocks", "QA")[0], 1)   # same id, other content: update it
        out = self.ok("update", self.PBI, self.B1, "--missing", "publish B1: 3rd attempt failed to connect", "--by", "qa")
        self.assertIn("updated (by, missing)", out)
        item = self.record()["open"][self.B1]
        self.assertEqual((item["missing"], item["by"], item["kind"]),
                         ("publish B1: 3rd attempt failed to connect", "qa", "publish"))
        self.assertIn("nothing changed", self.ok("update", self.PBI, self.B1, "--by", "qa"))
        for bad in (("--kind", "wish"), ("--by", "someone"), ("--step", "run:web"), ("--missing", " ")):
            with self.subTest(bad=bad):
                self.assertEqual(self.run_("update", self.PBI, self.B1, *bad)[0], 2)
        self.assertEqual(self.run_("update", self.PBI, "never-opened", "--by", "qa")[0], 1)
        self.assertEqual(self.run_("open", self.PBI, "../x", "--kind", "question", "--missing", "x",
                                   "--unblocks", "x")[0], 2)

    # --- partial publication: two drafts, one published --------------------------------------------------------------

    def test_a_publication_closes_only_its_own_item_and_a_resume_never_repeats_it(self):
        self.ios_done()
        for draft in (self.B1, self.B2):
            self.ok("open", self.PBI, draft, "--kind", "publish", "--step", "bugs", "--missing", "publish after review",
                    "--unblocks", "the write guard reachable")
        proof = ("--confirmed-by", "tracker read back 13.11 06:14: bug 9770, parent DS-34")
        out = self.ok("published", self.PBI, self.B1, "9770", *proof)
        self.assertIn("its open item is closed", out)
        doc = self.record()
        self.assertEqual(list(doc["open"]), [self.B2])           # only the published draft's item
        self.assertIn(self.B1, doc["closed"])
        self.assertIn("published as 9770", doc["closed"][self.B1]["ref"])
        before = self.raw()
        code, out = self.run_("published", self.PBI, self.B1, "9770", *proof)  # the same confirmation again
        self.assertEqual(code, 0)
        self.assertIn("nothing changed", out)
        self.assertEqual(self.raw(), before)
        code, out = self.run_("published", self.PBI, self.B1, "9771", *proof)   # another key for the same draft
        self.assertEqual(code, 1)
        self.assertIn("never publish a bug twice", out)
        code, out = self.run_("open", self.PBI, self.B1, "--kind", "publish", "--missing", "publish B1",
                              "--unblocks", "QA")
        self.assertEqual(code, 1)                                 # a resume cannot ask to publish it again
        self.assertEqual(self.raw(), before)
        # a new session: start again; nothing written, the state says what is published and what is left
        out = self.ok("start", self.PBI, "--platform", "ios")
        self.assertEqual(self.raw(), before)
        self.assertIn(f"Published: {self.B1} -> 9770", out)
        self.assertIn(f"  - {self.B2} [publish, bugs]", out)
        # B2 is work the assistant can do once the guard is reachable: bugs is not hidden as waiting on someone
        self.assertIn("Next: bugs", out)
        self.assertIn("The assistant can continue (within the approvals already given):", out)

    def test_a_publication_recorded_before_its_item_closes_it_on_the_same_confirmation(self):
        self.ios_done()
        proof = ("--confirmed-by", "tracker/sent/batch_ios_20261113_ds34_p2.json")
        self.ok("published", self.PBI, self.B1, "9770", *proof)
        self.assertEqual(self.run_("open", self.PBI, self.B1, "--kind", "publish", "--missing", "x",
                                   "--unblocks", "x")[0], 1)
        self.ok("open", self.PBI, self.B1, "--kind", "dependency", "--missing", "the dev answers the comment",
                "--unblocks", "team")                             # another kind of work on the same id is not closed
        self.assertIn("nothing changed", self.ok("published", self.PBI, self.B1, "9770", *proof))
        self.assertIn(self.B1, self.record()["open"])

    # --- nothing to run: Android blocked, a build without source and a product question -------------------------------

    def test_no_runnable_work_but_real_pending_items_stay_visible(self):
        self.ios_done()
        for step, ref in (("bugs", "tracker read back: bug 9770"), ("report", "runs/L78_ds34_ios.md")):
            self.ok("mark", self.PBI, step, "done", "--ref", ref)
        self.ok("open", self.PBI, "fe-build", "--kind", "dependency", "--missing", "which build has the front-end "
                "changes (results on 1.0.0 (39) are provisional)", "--unblocks", "the team (FE)")
        self.ok("open", self.PBI, "s8-o1", "--kind", "question", "--missing", "the action's scope keeps «morning» after "
                "it is moved to the afternoon: product decision", "--unblocks", "product")
        out = self.ok("show", self.PBI)
        self.assertIn("Next: no work available in this session", out)
        self.assertNotIn("nothing pending", out)
        self.assertNotIn("Next: checkpoint", out)                # the checkpoint cannot close the flow here
        for needle in ("Waiting on the team (outside this session):", "  - fe-build [dependency]: which build", "  - s8-o1 [question]",
                       "unblocks: product", "Blocked (go back to them when unblocked): run:android",
                       "  - run:android: no Android device connected",
                       "Closing the flow: not yet (1 blocked, 2 open item(s))"):
            self.assertIn(needle, out)
        self.assertIsNone(ps.next_step(self.record()))

    # --- closing: refused, closed by the QA's decision, and records from before ---------------------------------------

    def test_closing_is_refused_then_closed_by_the_qa_with_what_stayed_open(self):
        self.test_no_runnable_work_but_real_pending_items_stay_visible()
        before = self.raw()
        self.assertEqual(self.run_("mark", self.PBI, "checkpoint", "done", "--ref", "context.md")[0], 4)
        self.assertEqual(self.run_("mark", self.PBI, "checkpoint", "done", "--ref", "context.md",
                                   "--close-with-open")[0], 2)    # nobody decided
        self.assertEqual(self.run_("mark", self.PBI, "report", "done", "--ref", "x", "--close-with-open",
                                   "--decided-by", "QA")[0], 2)    # only for the closing checkpoint
        self.assertEqual(self.raw(), before)
        out = self.ok("mark", self.PBI, "checkpoint", "done", "--ref", "context.md §6", "--close-with-open",
                      "--decided-by", "QA (session 13.11 07:00)")
        self.assertIn("closed by the QA's decision", out)
        doc = self.record()
        self.assertEqual(doc["closure"]["decided_by"], "QA (session 13.11 07:00)")
        self.assertEqual(doc["closure"]["left_open"],
                         ["step run:android blocked", "item fe-build open", "item s8-o1 open"])
        self.assertIn("flow closed by the QA's decision", doc["history"][-1]["event"])
        out = self.ok("show", self.PBI)
        self.assertIn("Next: nothing to run: the flow was closed by the QA's decision (QA (session 13.11 07:00)", out)
        self.assertIn("  - fe-build [dependency]", out)          # what stayed open is still shown
        self.assertNotIn("nothing pending", out)
        # going back to the flow drops the closure (kept in the history)
        self.ok("mark", self.PBI, "checkpoint", "pending", "--note", "Android device connected")
        self.assertNotIn("closure", self.record())
        self.assertIn("no longer holds", self.record()["history"][-1]["event"])

    def test_nothing_pending_only_when_nothing_is(self):
        self.ios_done()
        self.ok("open", self.PBI, "s8-o1", "--kind", "question", "--missing", "product decision", "--unblocks", "product")
        for step in ("bugs", "report"):
            self.ok("mark", self.PBI, step, "done", "--ref", "x")
        self.ok("mark", self.PBI, "run:android", "skipped", "--note", "out of scope: QA decision 13.11")
        self.assertIn("Next: no work available in this session", self.ok("show", self.PBI))
        self.ok("close", self.PBI, "s8-o1", "--ref", "product answer in the thread of 13.11", "--decided-by", "PO")
        self.assertIn("Next: checkpoint", self.ok("show", self.PBI))     # now it can close the flow
        out = self.ok("mark", self.PBI, "checkpoint", "done", "--ref", "context.md")
        self.assertIn("Next: nothing pending", out)
        self.assertNotIn("closure", self.record())

    def write_old(self, steps: dict, published=None):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        doc = {"pbi": self.PBI, "task": "DS-40", "platforms": ["ios"], "created": "2026-11-12T16:00:00+00:00",
               "steps": steps, "baseline": {"tickets": {self.PBI: "6"}, "builds": {"ios": "39"}},
               "published": published or {}, "history": [{"at": "2026-11-12T16:00:00+00:00", "event": "started"}],
               "updated": "2026-11-12T19:17:10+00:00"}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.utime(self.path, (1_700_000_000, 1_700_000_000))

    def test_an_old_record_is_read_as_it_is_and_never_rewritten(self):
        done = {"status": "done", "at": "2026-11-12T16:23:57+00:00", "ref": "x"}
        self.write_old({"context": done, "analyze": done, "plan": done, "run:ios": done, "report": done,
                        "bugs": {"status": "blocked", "note": "F1 and F2 wait on the QA's decision"},
                        "checkpoint": {"status": "done", "ref": "context.md"}})
        before, mtime = self.raw(), os.stat(self.path).st_mtime
        for args in (("show", self.PBI), ("start", self.PBI), ("start", self.PBI, "--platform", "ios"),
                     ("show", self.PBI, "--json")):
            with self.subTest(args=args):
                out = self.ok(*args)
                self.assertNotIn("Next: nothing pending", out)
        out = self.ok("show", self.PBI)
        self.assertIn("Next: no work available in this session", out)
        self.assertIn("NOTE: the checkpoint was marked done while work was still open", out)
        self.assertIn("Blocked (go back to them when unblocked): bugs", out)
        self.assertEqual(self.raw(), before)
        self.assertEqual(os.stat(self.path).st_mtime, mtime)
        # an old record with everything done is still "nothing pending"
        self.write_old({n: done for n in ("context", "analyze", "plan", "run:ios", "bugs", "report", "checkpoint")},
                       {"b/TC-1": {"key": "9768", "at": "2026-11-13T00:44:00+00:00"}})
        out = self.ok("show", self.PBI)
        self.assertIn("Next: nothing pending", out)
        self.assertIn("Published: b/TC-1 -> 9768", out)

    # --- one state block for start, show and mark ---------------------------------------------------------------------

    def test_start_show_and_mark_print_the_same_state_block(self):
        self.ios_done()
        self.ok("open", self.PBI, self.B1, "--kind", "publish", "--step", "bugs", "--by", "qa", "--missing", "publish",
                "--unblocks", "QA")
        self.ok("saved", self.PBI, "--ref", "context.md")
        start = self.block(self.ok("start", self.PBI))
        show = self.block(self.ok("show", self.PBI))
        mark = self.block(self.ok("mark", self.PBI, "plan", "done", "--ref", "analysis/plan_ds34_20261113.md"))
        expected = "\n".join(ps.state_lines(self.record())) + "\n"
        self.assertEqual(start, expected)
        self.assertEqual(show, expected)
        self.assertEqual(mark, expected)
        self.assertIn("Pending, waiting on an open item: bugs", expected)    # bugs waits on the QA, report can run
        self.assertTrue(expected.startswith("Next: report"))

    # --- an approval open on the plan holds back the run that depends on it -------------------------------------------

    PLAN_ITEM = "plan-q-TC-9"

    def plan_waiting(self):
        """Context and analysis done; the plan waits on the QA's choice for a covered case."""
        self.ok("start", self.PBI, "--platform", "ios")
        for step, ref in (("context", "context.md"), ("analyze", "memory/checks/20261113-160900.json")):
            self.ok("mark", self.PBI, step, "done", "--ref", ref)
        return self.ok("open", self.PBI, self.PLAN_ITEM, "--kind", "approval", "--step", "plan", "--missing",
                       "the QA's choice for TC-9 (excluded or directed retest) and the plan's approval", "--unblocks",
                       "QA")

    def test_an_approval_open_on_the_plan_holds_the_run_back_in_start_show_and_mark(self):
        self.plan_waiting()
        mark = self.block(self.ok("mark", self.PBI, "analyze", "done", "--ref", "memory/checks/20261113-160900.json"))
        start = self.block(self.ok("start", self.PBI, "--platform", "ios"))
        show = self.block(self.ok("show", self.PBI))
        for out in (mark, start, show):
            with self.subTest(out=out[:40]):
                self.assertNotIn("Next: run:ios", out)
                self.assertIn("Next: no work available in this session", out)
                self.assertIn("Pending, waiting on an open item: plan", out)
                self.assertIn(f"run:ios (after plan: waits on the QA, {self.PLAN_ITEM})", out)
                self.assertIn("bugs (after run:ios: not done yet)", out)
        self.assertEqual(start, show)
        self.assertEqual(mark, show)
        self.assertIsNone(ps.next_step(self.record()))

    def test_closing_only_the_item_does_not_make_the_run_available(self):
        self.plan_waiting()
        out = self.ok("close", self.PBI, self.PLAN_ITEM, "--ref", "QA chose excluded in the session",
                      "--decided-by", "QA")
        self.assertIn("Next: plan (the assistant can continue)", out)       # the plan still has to be written
        self.assertIn("run:ios (after plan: not done yet)", out)
        self.assertNotIn("Next: run:ios", out)
        self.ok("mark", self.PBI, "plan", "blocked", "--note", "scope --plan exit 1: the plan file has no decision")
        out = self.ok("show", self.PBI)
        self.assertIn("run:ios (after plan: blocked)", out)
        self.assertNotIn("Next: run:ios", out)

    def test_once_decided_validated_and_done_the_run_is_next(self):
        self.plan_waiting()
        self.ok("close", self.PBI, self.PLAN_ITEM, "--ref", "QA chose excluded; scope --plan exit 0",
                "--decided-by", "QA")
        out = self.ok("mark", self.PBI, "plan", "done", "--ref", "batches/batch_ios_20261113_ds34_q.yaml")
        self.assertIn("Next: run:ios (the assistant can continue)", out)
        self.assertNotIn("waiting on an earlier step: run:ios", out)
        self.assertIn("bugs (after run:ios: not done yet)", out)              # bugs and report still follow the run

    def test_an_independent_approval_does_not_hold_back_authorised_local_work(self):
        self.plan_waiting()
        self.ok("close", self.PBI, self.PLAN_ITEM, "--ref", "QA approved", "--decided-by", "QA")
        self.ok("mark", self.PBI, "plan", "done", "--ref", "batches/batch_ios_20261113_ds34_q.yaml")
        # the workbook write waits on the QA, on no step: the run, then the bugs and the report go on
        self.ok("open", self.PBI, "wb-apply-ds34", "--kind", "approval", "--missing", "approve the workbook dry run",
                "--unblocks", "QA")
        self.assertIn("Next: run:ios", self.ok("show", self.PBI))
        out = self.ok("mark", self.PBI, "run:ios", "done", "--ref", "runs/batch_ios_20261113_ds34_q.json")
        self.assertIn("Next: bugs (the assistant can continue)", out)
        self.assertNotIn("waiting on an earlier step", out)
        self.assertIn("wb-apply-ds34 [approval]", out)


if __name__ == "__main__":
    unittest.main()

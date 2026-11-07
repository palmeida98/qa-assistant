"""/analyze-ticket: what the audit of 2026-11-06 found, with the fictional fakes, no network.

- An analysis whose basis is not a list of texts is refused before anything is written, and a later collect works.
- A source that names one comment is a statement however it is written; the collected comments as a whole are not.
- The same text recorded from another source keeps both sources.
- Content that could not be opened stays not read, with its reason; a URL that is not in the check is refused.
- A collect without expansion lists the children it did not read (`not-expanded`).
- The context block says when a ticket changed after its analysis, and a suggestion stays labelled as one.
- A Removed ticket is not suggested as a verification candidate.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests"), os.path.join(ROOT, "tests", "golden")]

from test_tickets import T1, T2
from test_tickets_memory import Base
from tickets_fake import FakeAdo

from adapters.tickets import analysis

A, B, C = "20261101-100000", "20261102-100000", "20261103-100000"


class Analysis(Base):
    def setUp(self):
        super().setUp()
        self.fake = FakeAdo()
        self.fake.add(100)
        self.collect(self.fake, "100", now=T1)

    def test_a_basis_that_is_not_a_list_of_texts_is_refused_and_nothing_is_written(self):
        before = self.snapshot()
        for basis in (1, True, "a sentence", [], [""]):
            with self.subTest(basis=basis):
                code = self.record(self.fake, A, {"tickets": {"100": {
                    "qa_situation": {"value": "needs-clarification", "basis": basis}}}})
                self.assertEqual(code, 4)
        after = self.snapshot()
        after.pop("input_analysis.json", None)
        before.pop("input_analysis.json", None)
        self.assertEqual(after, before)
        self.assertEqual(self.collect(self.fake, "100", now=T2), 0)

    def test_comment_sources_are_statements_and_the_collection_is_a_fact(self):
        comment = ("comment:9", "https://dev.azure.com/x/_workitems/edit/100#comment-9", "ADO comment 9 by PO",
                   "Comentário 9", ["field:state", "comment:9"])
        for source in comment:
            with self.subTest(source=source):
                self.assertTrue(analysis._is_comment(source))
        for source in ("component:comments", "comments component complete, 0 items", "field:state",
                       "check 20261106-214613: comments listed in full, 0 items"):
            with self.subTest(source=source):
                self.assertFalse(analysis._is_comment(source))

    def test_the_same_text_from_another_source_keeps_both_sources(self):
        merged = analysis.merge(None, {"statements": [{"text": "It may be 60 days", "source": "comment:9"}]},
                                {"id": A, "at": T1}, "a.json", T1, lambda r: r["at"])
        merged = analysis.merge(merged, {"statements": [{"text": "It may be 60 days", "source": "comment:12"}]},
                                {"id": B, "at": T2}, "b.json", T2, lambda r: r["at"])
        self.assertEqual(merged["statements"][0]["source"], ["comment:9", "comment:12"])

    def test_content_that_could_not_be_opened_stays_not_read(self):
        check = {"content_not_read": [{"url": "https://figma.example/f/1"}]}
        problems = analysis.validate({"tickets": {"100": {
            "content_unavailable": [{"url": "https://figma.example/f/1", "reason": "403, no access"}],
            "content_read": [{"url": "https://evil.example/x", "result": "read"}]}}}, {"100"}, check)
        self.assertEqual(len(problems), 1)
        self.assertIn("not a link or attachment of this check", problems[0])
        merged = analysis.merge(None, {"content_unavailable": [{"url": "https://figma.example/f/1",
                                                                "reason": "403, no access"}]},
                                {"id": A, "at": T1}, "a.json", T1, lambda r: r["at"])
        self.assertEqual(merged["content_read"], [])
        self.assertEqual(merged["content_unavailable"][0]["reason"], "403, no access")


class Expansion(Base):
    def test_children_not_expanded_are_listed(self):
        fake = FakeAdo()
        fake.add(100, System__WorkItemType="Product Backlog Item")
        fake.add(101)
        fake.add(102)
        fake.link_family(100, 101, 102)
        self.collect(fake, "100", "--no-expand", now=T1)
        omitted = {(o["id"], o["reason"]) for o in self.last_check()["omitted"]}
        self.assertEqual(omitted, {("101", "not-expanded"), ("102", "not-expanded")})
        self.assertEqual(self.last_check()["outcome"], "complete")      # declared, so not partial


class ContextBlock(Base):
    def test_a_change_after_the_analysis_is_said_in_the_block(self):
        fake = FakeAdo()
        fake.add(100)
        self.collect(fake, "100", now=T1)
        self.record(fake, A, {"tickets": {"100": {"qa_situation": {"value": "awaiting-implementation-or-build",
                                                                   "basis": ["state To Do"]}}}})
        self.assertNotIn("CHANGED SINCE", self.context())
        fake.set(100, System__State="Doing")
        self.collect(fake, "100", now=T2)
        self.assertIn("CHANGED SINCE THAT ANALYSIS", self.context())

    def test_a_suggestion_is_labelled_as_one_and_removed_is_not_a_candidate(self):
        fake = FakeAdo()
        fake.add(100, System__State="Removed")
        self.collect(fake, "100", now=T1)
        t = self.last_check()["tickets"][0]
        self.assertEqual(t["suggested_situation"]["value"], "needs-clarification")
        self.assertIn("(suggested, not analysed; removed)", self.context())


if __name__ == "__main__":
    unittest.main()

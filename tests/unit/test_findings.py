"""Findings: the evidence rule, severities and summaries (docs/qa-audit.md §9)."""

import unittest

from qa_audit.core.findings import (
    CheckTally,
    Evidence,
    Finding,
    assign_ids,
    severity_rank,
    sort_key,
    summarize,
)


def finding(**kw):
    base = {"module": "accessibility", "check": "c", "title": "t", "status": "problem", "severity": "high",
            "url": "https://shop.example/", "evidence": [Evidence(kind="text", value="seen")]}
    base.update(kw)
    return Finding(**base)


class EvidenceRule(unittest.TestCase):
    def test_problem_and_warning_without_evidence_are_refused(self):
        for status in ("problem", "warning"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "without evidence"):
                finding(status=status, evidence=[])

    def test_needs_human_may_have_no_evidence(self):
        self.assertEqual(finding(status="needs-human", evidence=[]).status, "needs-human")

    def test_evidence_needs_a_value_or_a_path(self):
        with self.assertRaises(ValueError):
            Evidence(kind="screenshot")
        with self.assertRaises(ValueError):
            Evidence(kind="video", path="x.webm")

    def test_unknown_status_severity_or_confidence_are_refused(self):
        for kw in ({"status": "bug"}, {"severity": "blocker"}, {"confidence": "certain"}):
            with self.subTest(**kw), self.assertRaises(ValueError):
                finding(**kw)

    def test_required_fields(self):
        with self.assertRaises(ValueError):
            finding(url="")


class Summary(unittest.TestCase):
    def test_counts_by_status_and_severity(self):
        fs = [finding(), finding(severity="low", status="warning"), finding(status="needs-human", evidence=[])]
        t = CheckTally()
        t.add("accessibility", 7)
        s = summarize(fs, t)
        self.assertEqual(s["passed"], 7)
        self.assertEqual(s["by_status"], {"problem": 1, "warning": 1, "needs-human": 1})
        # needs-human is not counted by severity: nobody has confirmed it
        self.assertEqual(s["by_severity"]["high"], 1)
        self.assertEqual(s["by_severity"]["medium"], 0)
        self.assertEqual(s["by_severity"]["low"], 1)

    def test_ids_follow_report_order(self):
        fs = sorted([finding(severity="low"), finding(status="needs-human", evidence=[]), finding()], key=sort_key)
        assign_ids(fs, {"accessibility": "ACC"})
        self.assertEqual([f.id for f in fs], ["ACC-001", "ACC-002", "ACC-003"])
        self.assertEqual((fs[0].severity, fs[2].status), ("high", "needs-human"))

    def test_severity_rank(self):
        self.assertLess(severity_rank("critical"), severity_rank("info"))


if __name__ == "__main__":
    unittest.main()

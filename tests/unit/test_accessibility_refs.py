"""WCAG criteria, version and level taken from axe tags (docs/qa-audit.md §11): what the workspace script
(scripts/auditar_a11y.py) reads back from the findings' references."""

import unittest

from qa_audit.modules.accessibility.module import wcag_levels, wcag_refs


class WcagReferences(unittest.TestCase):
    def test_criteria(self):
        self.assertEqual(wcag_refs(["cat.color", "wcag2aa", "wcag143", "wcag1410"]), ["WCAG 1.4.3", "WCAG 1.4.10"])
        self.assertEqual(wcag_refs(["best-practice"]), [])

    def test_version_and_level_of_each_tag(self):
        self.assertEqual(wcag_levels(["wcag2aa", "wcag143"]), ["WCAG 2.0 AA"])
        self.assertEqual(wcag_levels(["wcag2a", "wcag21aa", "wcag412"]), ["WCAG 2.0 A", "WCAG 2.1 AA"])
        self.assertEqual(wcag_levels(["wcag22aa", "wcag258", "best-practice"]), ["WCAG 2.2 AA"])
        self.assertEqual(wcag_levels(["wcag2aaa", "wcag146"]), ["WCAG 2.0 AAA"])

    def test_nothing_outside_wcag(self):
        self.assertEqual(wcag_levels(["cat.keyboard", "best-practice"]), [])
        self.assertEqual(wcag_levels(None), [])


if __name__ == "__main__":
    unittest.main()

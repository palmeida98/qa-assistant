"""WCAG criteria, version and level taken from axe tags (docs/qa-audit.md §11): what the workspace script
(scripts/auditar_a11y.py) reads back from the findings' references."""

import unittest

from qa_audit.modules.accessibility.checks import shorten_data_uris
from qa_audit.modules.accessibility.module import listed, wcag_levels, wcag_refs


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


class FindingText(unittest.TestCase):
    """What the first real use showed (plan 8.17): a list cut at five without saying so, and a base64 image
    filling an element's HTML evidence."""

    def test_list_says_how_many_more(self):
        items = [{"selector": f"#b{i}"} for i in range(6)]
        self.assertEqual(listed(items), "#b0, #b1, #b2, #b3, #b4 and 1 more")
        self.assertEqual(listed(items[:2]), "#b0, #b1")

    def test_data_uri_payload_dropped(self):
        html = '<img alt="" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAA">Back'
        self.assertEqual(shorten_data_uris(html), '<img alt="" src="data:image/png;…">Back')
        style = "background:url(data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%22%3E%3C%2Fsvg%3E)"
        self.assertEqual(shorten_data_uris(style), "background:url(data:image/svg+xml,…)")
        self.assertEqual(shorten_data_uris('<a href="data:,x">'), '<a href="data:,x">')   # short: kept


if __name__ == "__main__":
    unittest.main()

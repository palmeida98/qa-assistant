"""Performance thresholds and metrics in the reports (docs/qa-audit.md §17). No browser."""

import csv
import os
import tempfile
import unittest

from qa_audit.modules.performance import checks
from qa_audit.modules.performance.module import MODULE
from qa_audit.reports import metric_value, write_all

from .helpers import fake_result

WEB_DEV = {"ttfb_ms": (800, 1800), "fcp_ms": (1800, 3000), "lcp_ms": (2500, 4000), "cls": (0.1, 0.25)}


class Thresholds(unittest.TestCase):
    def test_defaults_are_the_web_dev_values_and_the_rest_only_measured(self):
        t = MODULE.default_config()["thresholds"]
        for name, (good, poor) in WEB_DEV.items():
            with self.subTest(metric=name):
                self.assertEqual((t[name]["good"], t[name]["poor"]), (good, poor))
        others = {k: v for k, v in t.items() if k not in WEB_DEV}
        self.assertTrue(others)
        self.assertEqual(set(others.values()), {None})          # no arbitrary defaults (risk 8)
        self.assertEqual(set(t), set(checks.METRICS))

    def test_evaluate(self):
        t = {"good": 800, "poor": 1800}
        self.assertEqual(checks.evaluate(800, t), "good")
        self.assertEqual(checks.evaluate(801, t), "needs-improvement")
        self.assertEqual(checks.evaluate(1800, t), "needs-improvement")
        self.assertEqual(checks.evaluate(1801, t), "poor")
        self.assertEqual(checks.evaluate(None, t), "n/a")       # not measurable here: never a guess
        self.assertEqual(checks.evaluate(10, None), "measured")
        self.assertEqual(checks.evaluate(10, 20), "within-budget")
        self.assertEqual(checks.evaluate(21, 20), "over-budget")

    def test_describe(self):
        self.assertEqual(checks.describe({"good": 0.1, "poor": 0.25}), "good <= 0.1, poor > 0.25")
        self.assertEqual(checks.describe(500), "budget <= 500")
        self.assertEqual(checks.describe(None), "")

    def test_validation(self):
        self.assertEqual(MODULE.validate_config(MODULE.default_config()), [])
        problems = checks.validate_thresholds({"speed": 1, "ttfb_ms": {"good": 900, "poor": 100},
                                               "fcp_ms": "fast", "lcp_ms": {"good": 1}})
        self.assertEqual(len(problems), 4)


class MetricsInReports(unittest.TestCase):
    def test_every_format_carries_the_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_all(fake_result(tmp), ["html", "md", "csv", "json"])
            with open(paths["html"], encoding="utf-8") as f:
                h = f.read()
            self.assertIn("<h2>Metrics</h2>", h)
            self.assertIn("1 above a threshold", h)
            with open(paths["md"], encoding="utf-8") as f:
                self.assertIn("| https://shop.example/ | ttfb | 912 ms |", f.read())
            with open(os.path.join(tmp, "metrics.csv"), encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["result"], "needs-improvement")
            self.assertEqual(rows[1]["source"], "'=cmd")          # formula-safe like results.csv
            self.assertEqual(rows[1]["value"], "")

    def test_value_formatting(self):
        self.assertEqual(metric_value({"value": None}), "n/a")
        self.assertEqual(metric_value({"value": 0.1234, "unit": ""}), "0.123")
        self.assertEqual(metric_value({"value": 912.4, "unit": "ms"}), "912 ms")


if __name__ == "__main__":
    unittest.main()

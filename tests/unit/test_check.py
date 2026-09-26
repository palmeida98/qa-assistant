"""qa-audit check (docs/qa-audit.md §22): argument validation, tab choice and exit codes. No browser and no
Playwright (the CI unit job does not install it): connecting is tested in tests/integration (CheckOpenPage)."""

import contextlib
import io
import types
import unittest

from qa_audit import cli
from qa_audit.core import check


class Validation(unittest.TestCase):
    def test_cdp_only_on_loopback(self):
        for ok in ("http://127.0.0.1:9222", "http://localhost:9222", "ws://[::1]:9222/devtools/browser/x"):
            with self.subTest(ok=ok):
                self.assertEqual(check.validate_cdp(ok), ok)
        for bad in ("http://10.0.0.5:9222", "http://shop.example:9222", "file:///tmp/x", "127.0.0.1:9222"):
            with self.subTest(bad=bad), self.assertRaises(check.CheckUsageError):
                check.validate_cdp(bad)

    def test_checks_axe_always_the_others_opt_in(self):
        self.assertEqual(check.parse_checks("axe"), ["axe"])
        self.assertEqual(check.parse_checks("axe, keyboard,reflow"), ["axe", "keyboard", "reflow"])
        self.assertEqual(check.parse_checks(""), ["axe"])
        for bad in ("keyboard", "axe,security", "performance"):
            with self.subTest(bad=bad), self.assertRaises(check.CheckUsageError):
                check.parse_checks(bad)

    def test_most_recent_matching_tab(self):
        pages = [types.SimpleNamespace(url=u) for u in ("https://shop.example/cart", "https://shop.example/checkout",
                                                        "https://shop.example/checkout?step=2")]
        self.assertIs(check.pick_page(pages, r"/checkout"), pages[2])
        self.assertIsNone(check.pick_page(pages, r"/account"))

    def test_exit_code_only_with_fail_on(self):
        result = {"findings": [{"status": "problem", "severity": "medium"}]}
        self.assertEqual(check.exit_code(result, "none"), 0)
        self.assertEqual(check.exit_code(result, "high"), 0)
        self.assertEqual(check.exit_code(result, "medium"), 1)


class Cli(unittest.TestCase):
    def run_cli(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["check", *argv])
        return code, err.getvalue()

    def test_remote_cdp_is_a_usage_error(self):
        code, err = self.run_cli(["--cdp", "http://192.168.1.20:9222", "--page-url", "x", "--label", "a"])
        self.assertEqual(code, 2)
        self.assertIn("loopback", err)



if __name__ == "__main__":
    unittest.main()

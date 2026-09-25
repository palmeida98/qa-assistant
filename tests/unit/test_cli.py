"""Command line: version, help, usage errors and exit codes that need no browser (docs/qa-audit.md §2)."""

import contextlib
import io
import os
import tempfile
import types
import unittest
from unittest import mock

from qa_audit import __version__, cli
from qa_audit.core.console import Console


def never_audit(*args, **kwargs):
    raise AssertionError("a unit test reached the runner: it would launch a browser and use the network")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with (contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),
          mock.patch("qa_audit.core.runner.run", never_audit)):
        try:
            code = cli.main(argv) if argv[:1] in (["version"], ["--version"]) else cli.audit(argv, Console(out, False))
        except SystemExit as e:        # argparse
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Cli(unittest.TestCase):
    def test_version(self):
        for argv in (["version"], ["--version"]):
            code, out, _ = run(argv)
            self.assertEqual((code, out.strip()), (0, f"qa-audit {__version__}"))

    def test_help_lists_the_options(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, 0)
        for opt in ("--type", "--browser", "--headed", "--screenshots", "--report", "--cleanup", "--fail-on",
                    "authorized"):
            self.assertIn(opt, out)

    def test_usage_errors_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = os.path.join(tmp, "c.yaml")
            with open(secret, "w", encoding="utf-8") as f:
                f.write("login: {password: x}\n")
            bad = os.path.join(tmp, "t.yaml")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("modules: {performance: {thresholds: {ttfb_ms: {good: 900, poor: 100}, speed: 1}}}\n")
            for argv in (["https://shop.example", "--config", bad], ["not-a-url"],
                         ["https://shop.example", "--type", "visual"],
                         ["https://shop.example", "--type", "nope"], ["https://shop.example", "--viewport", "big"],
                         ["https://shop.example", "--report", "pdf"], ["https://shop.example", "--config", secret],
                         ["https://shop.example", "--browser", "ie"], ["https://shop.example", "--exclude", "("]):
                with self.subTest(argv=argv):
                    code, _, err = run(argv)
                    self.assertEqual(code, 2, err)

    def test_overrides_only_what_was_given(self):
        a = cli.build_parser().parse_args(["https://shop.example", "--max-pages", "3", "--screenshots"])
        self.assertEqual(cli.overrides_from_args(a),
                         {"crawl": {"max_pages": 3}, "evidence": {"screenshots": "all"}})

    def test_exit_code_by_fail_on(self):
        f = types.SimpleNamespace
        result = f(findings=[f(status="problem", severity="medium"), f(status="needs-human", severity="high")])
        self.assertEqual(cli.exit_code(result, "high"), 0)     # needs-human never fails the run
        self.assertEqual(cli.exit_code(result, "medium"), 1)
        self.assertEqual(cli.exit_code(result, "none"), 0)


if __name__ == "__main__":
    unittest.main()

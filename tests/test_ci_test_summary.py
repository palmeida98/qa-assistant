"""scripts/ci_test_summary.py: the CI's summary of a unittest log, and the failures it adds on top of the plain
"OK": no test ran, a skip with no allowed reason, too many skips. Fictional logs, no network.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import ci_test_summary as cts

OK_LOG = """test_a (mod.A.test_a) ... ok
test_b (mod.A.test_b) ... ok

----------------------------------------------------------------------
Ran 2 tests in 0.010s

OK
"""

SKIP_LOG = """test_a (mod.A.test_a) ... ok
test_b (mod.A.test_b) ... skipped "needs LibreOffice (runs in the CI's golden job)"
test_c (mod.A.test_c) ... skipped 'the NUL device of Windows'
test_d (mod.A.test_d) ... skipped 'a reason nobody planned'

----------------------------------------------------------------------
Ran 4 tests in 0.010s

OK (skipped=3)
"""

FAIL_LOG = """test_a (mod.A.test_a) ... FAIL
test_b (mod.A.test_b) ... ERROR
test_c (mod.A.test_c) ... ok

======================================================================
FAIL: test_a (mod.A.test_a)
----------------------------------------------------------------------
AssertionError: 1 != 2

======================================================================
ERROR: test_b (mod.A.test_b)
----------------------------------------------------------------------
ValueError: boom

----------------------------------------------------------------------
Ran 3 tests in 0.010s

FAILED (failures=1, errors=1)
"""

EMPTY_LOG = """
----------------------------------------------------------------------
Ran 0 tests in 0.000s

NO TESTS RAN
"""


def run(log, *args):
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "unit.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write(log)
        summary = os.path.join(tmp, "summary.md")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cts.main([p, "--summary-file", summary, *args])
        text = ""
        if os.path.exists(summary):
            with open(summary, encoding="utf-8") as f:
                text = f.read()
        return code, out.getvalue(), err.getvalue(), text


class Summary(unittest.TestCase):
    def test_all_ok_passes_and_reports_counts(self):
        code, out, _, summary = run(OK_LOG, "--title", "unit tests")
        self.assertEqual(code, 0)
        self.assertIn("Ran: 2; failed or errored: 0; skipped: 0", out)
        self.assertEqual(summary.strip(), out.strip())

    def test_failures_and_errors_fail_and_are_listed(self):
        code, out, err, _ = run(FAIL_LOG)
        self.assertEqual(code, 1)
        self.assertIn("failed or errored: 2", out)
        self.assertIn("FAIL mod.A.test_a", out)
        self.assertIn("ERROR mod.A.test_b", out)
        self.assertIn("did not end with OK", out)
        self.assertIn("ERROR:", err)

    def test_no_test_ran_is_a_failure(self):
        code, out, _, _ = run(EMPTY_LOG)
        self.assertEqual(code, 1)
        self.assertIn("0 tests ran", out)

    def test_not_a_unittest_log_is_a_failure(self):
        code, out, _, _ = run("some other output\n")
        self.assertEqual(code, 1)
        self.assertIn("no 'Ran N tests' line", out)

    def test_skips_are_accepted_without_a_policy(self):
        code, out, _, _ = run(SKIP_LOG)
        self.assertEqual(code, 0)
        self.assertIn("skipped: 3", out)

    def test_skip_reasons_are_grouped_with_quotes_inside(self):
        _, out, _, _ = run(SKIP_LOG)
        self.assertIn("1 × needs LibreOffice (runs in the CI's golden job)", out)
        self.assertIn("1 × the NUL device of Windows", out)

    def test_only_allowed_reasons_pass(self):
        code, out, err, _ = run(SKIP_LOG, "--allow-skip", "needs LibreOffice", "--allow-skip", "Windows")
        self.assertEqual(code, 1)
        self.assertIn("not allowed: a reason nobody planned", out)
        self.assertIn("not allowed", err)

    def test_all_reasons_allowed_passes(self):
        code, _, _, _ = run(SKIP_LOG, "--allow-skip", "LibreOffice", "--allow-skip", "Windows", "--allow-skip", "nobody planned")
        self.assertEqual(code, 0)

    def test_max_skips_zero_fails_on_any_skip(self):
        code, out, _, _ = run(SKIP_LOG, "--max-skips", "0")
        self.assertEqual(code, 1)
        self.assertIn("3 skip(s), more than the 0 allowed", out)

    def test_max_skips_zero_passes_without_skips(self):
        code, _, _, _ = run(OK_LOG, "--max-skips", "0")
        self.assertEqual(code, 0)

    def test_count_without_readable_reason_fails_when_a_policy_exists(self):
        log = OK_LOG.replace("OK\n", "OK (skipped=2)\n")
        code, out, _, _ = run(log, "--allow-skip", "anything")
        self.assertEqual(code, 1)
        self.assertIn("without a readable reason", out)

    def test_unreadable_log_is_a_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = cts.main(["/nonexistent/unit.log"])
        self.assertEqual(code, 2)

    def test_invalid_regex_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "unit.log")
            with open(p, "w", encoding="utf-8") as f:
                f.write(OK_LOG)
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cts.main([p, "--allow-skip", "("]), 2)

    def test_summary_goes_to_the_github_variable_when_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "unit.log")
            s = os.path.join(tmp, "step.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(OK_LOG)
            old = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = s
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cts.main([p, "--title", "x"]), 0)
            finally:
                if old is None:
                    del os.environ["GITHUB_STEP_SUMMARY"]
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = old
            with open(s, encoding="utf-8") as f:
                self.assertIn("### x", f.read())

    def test_real_unittest_output_is_understood(self):
        """The format is the one unittest writes, not only the fixtures above: run a tiny suite and parse it."""
        suite = unittest.TestSuite()

        class T(unittest.TestCase):
            def test_ok(self):
                pass

            @unittest.skip("a planned reason")
            def test_skipped(self):
                pass

            def test_bad(self):
                self.fail("boom")

        suite.addTests([T("test_ok"), T("test_skipped"), T("test_bad")])
        buf = io.StringIO()
        unittest.TextTestRunner(stream=buf, verbosity=2).run(suite)
        info = cts.parse(buf.getvalue())
        self.assertEqual(info["ran"], 3)
        self.assertEqual(info["status"], "FAILED")
        self.assertEqual(info["counts"].get("skipped"), 1)
        self.assertEqual(dict(info["reasons"]), {"a planned reason": 1})
        self.assertEqual(len(info["bad"]), 1)


if __name__ == "__main__":
    unittest.main()

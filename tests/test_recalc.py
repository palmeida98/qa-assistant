"""Safe recalculation (adapters/excel/recalc.py; technique-excel.md section 2): the
timeout must be effective even when the process starts children (soffice ->
soffice.bin), and a failure leaves the original untouched.

python -m unittest discover -s tests   (from the root of qa-assistant)
Does not need LibreOffice.
"""

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from adapters.excel import recalc

# a parent that starts a grandchild with the same pipes, and both hang
PARENT_WITH_CHILD = ("import subprocess, sys, time; "
                     "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)")


class Recalc(unittest.TestCase):
    def test_timeout_kills_the_process_tree(self):
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            recalc._run_with_timeout([sys.executable, "-c", PARENT_WITH_CHILD], timeout=2)
        # without killing the grandchild, communicate() waited for it (60 s)
        self.assertLess(time.monotonic() - t0, 30)

    def test_unknown_mechanism_does_not_touch_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.xlsx")
            with open(path, "wb") as f:
                f.write(b"original")
            r = recalc.recalc_safe(path, mechanism="magic-calc")
            self.assertFalse(r["ok"])
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"original")

    def test_a_failing_mechanism_leaves_the_original_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.xlsx")
            with open(path, "wb") as f:
                f.write(b"original")
            original = recalc._recalc_libreoffice

            def broken(p, timeout):
                with open(p, "wb") as f:
                    f.write(b"half written")
                raise RuntimeError("LibreOffice failed")

            recalc._recalc_libreoffice = broken
            try:
                r = recalc.recalc_safe(path)
            finally:
                recalc._recalc_libreoffice = original
            self.assertFalse(r["ok"])
            self.assertIn("failed", r["error"])
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"original")


if __name__ == "__main__":
    unittest.main()

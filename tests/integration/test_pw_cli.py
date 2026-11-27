"""scripts/pw_cli.py with the real Playwright CLI of the demo workspace and a real browser (technique-browser.md §16).

    cd examples/demo/e2e && npm ci      # once: installs the pinned @playwright/cli
    python -m unittest discover -s tests/integration

Skipped when the CLI is not installed in examples/demo/e2e or a browser cannot start. The page is a data: URL, so
nothing reaches the network. Run outside the Bash sandbox (the browser must start).
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pw_cli

DEMO = os.path.join(ROOT, "examples", "demo")
PAGE = "data:text/html,<h1>Demo</h1><label>Name<input></label><button>Save</button>"


class RealCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proj = pw_cli.common.read_project(DEMO)
        ok, why = pw_cli.readiness(DEMO, proj)
        if not ok:
            raise unittest.SkipTest(why)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ws = os.path.join(os.path.realpath(cls.tmp.name), "repo")     # a copy: the demo is never written to
        os.makedirs(cls.ws)
        shutil.copy(os.path.join(DEMO, "project.yaml"), cls.ws)
        os.symlink(os.path.join(DEMO, "e2e"), os.path.join(cls.ws, "e2e"))
        cls.session = "qa-integration"

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "ws"):
            with contextlib.suppress(Exception):
                pw_cli.run(cls.ws, ["close"], "b", cls.session, False)
            cls.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pw_cli.run(self.ws, list(args), "b", self.session, False)
        return code, out.getvalue() + err.getvalue()

    def test_open_snapshot_and_screenshot_are_saved_as_evidence(self):
        code, text = self.cli("open", PAGE)
        if code != 0:
            self.skipTest(f"the browser did not start: {text[-200:]}")
        code, text = self.cli("snapshot")
        self.assertEqual(code, 0, text)
        path = os.path.join(self.ws, "evidence", "b", "cli", "001-snapshot.md")
        with open(path, encoding="utf-8") as f:
            self.assertIn('textbox "Name"', f.read())
        code, text = self.cli("screenshot")
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "evidence", "b", "cli", "002-screenshot.png")))


if __name__ == "__main__":
    unittest.main()

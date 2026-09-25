"""qa-audit against the local fixture site, per browser engine (docs/qa-audit.md §15).

    python -m unittest discover -s tests/integration

Engines: QA_AUDIT_TEST_ENGINES (default "chromium,firefox"; CI adds webkit). An engine that cannot start on this
machine is skipped with the reason, or fails with QA_AUDIT_REQUIRE_ENGINES=1 (CI) (e.g. WebKit blocked by Windows application control, docs/qa-audit.md §13).
Nothing here reaches the network: the site is served on 127.0.0.1 and one test routes the browser through a
recording proxy that refuses anything else.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(ROOT, "src"), os.path.join(ROOT, "tests", "fixtures")):
    if p not in sys.path:
        sys.path.insert(0, p)

from server import FixtureSite, RecordingProxy

from qa_audit import cli
from qa_audit.browser.session import Session
from qa_audit.core import config, registry, runner
from qa_audit.core.console import Console

ENGINES = [e.strip() for e in os.environ.get("QA_AUDIT_TEST_ENGINES", "chromium,firefox").split(",") if e.strip()]
TRAPS = ("/logout", "/account/delete", "/private/", "/files/brochure.pdf", "/deep/level3.html")
_unavailable: dict[str, str] = {}


def engine_available(engine: str) -> str:
    """'' if the engine starts here, else the reason."""
    if engine not in _unavailable:
        from playwright.sync_api import Error, sync_playwright
        try:
            with sync_playwright() as p:
                getattr(p, engine).launch().close()
            _unavailable[engine] = ""
        except Error as e:
            from qa_audit.browser.session import launch_hint
            _unavailable[engine] = launch_hint(engine, str(e))
    if _unavailable[engine] and os.environ.get("QA_AUDIT_REQUIRE_ENGINES"):
        # CI: an engine that cannot start is a failure, never a silent skip
        raise AssertionError(f"{engine} required but unavailable: {_unavailable[engine]}")
    return _unavailable[engine]


def make_cfg(engine: str, out: str, **over) -> dict:
    mods = registry.discover(use_entry_points=False)
    base = {"browser": {"engine": engine}, "crawl": {"delay_ms": 0}, "output": {"directory": out}}
    return config.build(None, config.deep_merge(base, over),
                        module_defaults={n: m.default_config() for n, m in mods.items()})


class EngineTests:
    """Mixed into one TestCase per engine (below)."""

    engine = ""

    @classmethod
    def setUpClass(cls):
        reason = engine_available(cls.engine)
        if reason:
            raise unittest.SkipTest(f"{cls.engine} unavailable here: {reason}")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.site = FixtureSite().__enter__()
        mods = registry.discover(use_entry_points=False)
        cls.result = runner.run(cls.site.url, make_cfg(cls.engine, cls.tmp.name), [mods["accessibility"]])
        from qa_audit.reports import write_all
        cls.paths = write_all(cls.result, ["html", "json", "csv", "md"])
        cls.requests = list(cls.site.requests)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "site"):
            cls.site.__exit__(None, None, None)
            cls.tmp.cleanup()

    def by_page(self, page: str):
        return [f for f in self.result.findings if f.url.endswith("/" + page)]

    # --- discovery safety (risk 3, 4) ---------------------------------------------------------------------
    def test_only_get_and_no_trap_requested(self):
        self.assertEqual({m for m, _ in self.requests}, {"GET"})
        for method, path in self.requests:
            for trap in TRAPS:
                self.assertFalse(path.startswith(trap), f"{method} {path} must never be requested")

    def test_pages_visited_within_limits(self):
        visited = {p.url.split("/", 3)[3] for p in self.result.pages}
        self.assertEqual(visited, {"", "index.html", "about.html", "a11y.html", "form.html", "xss.html",
                                   "csp.html", "deep/level1.html", "deep/level2.html"})
        reasons = {s["url"].split("/", 3)[3]: s["reason"] for s in self.result.crawl["skipped"]}
        self.assertEqual(reasons["private/secret.html"], "disallowed by robots.txt")
        self.assertEqual(reasons["deep/level3.html"], "beyond max depth")
        self.assertEqual(reasons["files/brochure.pdf"], "not an HTML page")
        self.assertIn("logout", reasons["logout"])
        self.assertIn("delete", reasons["account/delete?id=1"])
        self.assertEqual(self.result.crawl["robots"], "respected")

    def test_identifiable_user_agent(self):
        self.assertIn("qa-audit/", self.result.browser["user_agent"])

    # --- accessibility (evidence rule, no invented bugs) ---------------------------------------------------
    def test_planted_defects_are_found(self):
        checks = {f.check for f in self.by_page("a11y.html") if f.status in ("problem", "warning")}
        for expected in ("axe/image-alt", "axe/label", "axe/color-contrast", "axe/button-name", "keyboard-trap",
                         "reflow"):
            self.assertIn(expected, checks)

    def test_strict_csp_page(self):
        checks = {f.check for f in self.by_page("csp.html")}
        if self.engine == "chromium":
            self.assertNotIn("axe-not-run", checks)       # evaluate is not subject to the page CSP
        self.assertFalse({c for c in checks if c.startswith("axe/")} & {"axe/image-alt", "axe/label"})
        self.assertEqual([f for f in self.by_page("csp.html") if f.status == "problem"], [])

    def test_clean_page_has_no_problem_or_warning(self):
        bad = [(f.check, f.status) for f in self.by_page("about.html") if f.status != "needs-human"]
        self.assertEqual(bad, [])

    def test_every_problem_has_evidence_and_no_critical(self):
        for f in self.result.findings:
            if f.status in ("problem", "warning"):
                self.assertTrue(f.evidence, f.check)
            self.assertNotEqual(f.severity, "critical")
        shots = [e for f in self.result.findings for e in f.evidence if e.kind == "screenshot"]
        self.assertTrue(shots)
        for e in shots:
            self.assertTrue(os.path.isfile(os.path.join(self.result.run_folder, e.path)), e.path)

    def test_no_module_errors(self):
        self.assertEqual(self.result.errors, [])

    # --- reports (risk 5, 6) -------------------------------------------------------------------------------
    def test_reports_are_written_and_escaped(self):
        with open(self.paths["html"], encoding="utf-8") as f:
            h = f.read()
        self.assertNotIn("<script", h.lower())       # xss.html's hostile title and attribute are escaped
        self.assertIn("&lt;script&gt;", h)
        with open(self.paths["json"], encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data["findings"]), len(self.result.findings))
        root = os.path.dirname(self.result.run_folder)
        with open(os.path.join(root, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("*", f.read().split())

    def test_trace_and_video_off_by_default(self):
        for sub in ("traces", "videos"):
            self.assertFalse(os.path.exists(os.path.join(self.result.run_folder, sub)))


class NoExternalTraffic:
    """Risk 6: every request of the page and of the browser itself stays on the fixture site."""

    engine = ""

    def test_all_browser_traffic_stays_local(self):
        reason = engine_available(self.engine)
        if reason:
            self.skipTest(reason)
        with FixtureSite() as site, RecordingProxy(f"127.0.0.1:{site.port}") as proxy, \
                tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(self.engine, tmp)
            mods = registry.discover(use_entry_points=False)

            def factory(c, r, t):
                return Session(c, r, t, proxy={"server": proxy.server, "bypass": ""},
                               launch_args=["--proxy-bypass-list=<-loopback>"] if self.engine == "chromium" else None,
                               firefox_prefs={"network.proxy.allow_hijacking_localhost": True})

            runner.run(site.url, cfg, [mods["accessibility"]], session_factory=factory)
            hosts = set(proxy.hosts)
        self.assertTrue(hosts, "the proxy saw no traffic: the test would prove nothing")
        self.assertEqual(hosts, {f"127.0.0.1:{site.port}"})


class CliTests:
    engine = ""

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.audit(list(argv), Console(out, False))
        return code, out.getvalue(), err.getvalue()

    def test_cli_end_to_end_exit_codes_and_cleanup(self):
        reason = engine_available(self.engine)
        if reason:
            self.skipTest(reason)
        with FixtureSite() as site, tempfile.TemporaryDirectory() as tmp:
            common = ["--browser", self.engine, "--delay-ms", "0", "--output", tmp, "--max-pages", "3"]
            code, out, _ = self.cli(site.url, *common)
            self.assertEqual(code, 1, out)                          # high-severity problems on a11y.html
            self.assertIn("[1/3] Discovery", out)
            self.assertIn("Only audit sites you own or are authorized to test.", out)
            self.assertIn("report.html", out)
            code, out, _ = self.cli(site.url, *common, "--fail-on", "none", "--cleanup", "--report", "json")
            self.assertEqual(code, 0)
            self.assertIn("Run folder deleted", out)
            runs = [d for d in os.listdir(tmp) if not d.startswith(".")]
            self.assertEqual(len(runs), 1)                          # only the first run is left
            self.assertEqual(self.cli(site.url + "missing.html", *common)[0], 3)
        with tempfile.TemporaryDirectory() as tmp:
            # nothing listens on port 9 (discard) on 127.0.0.1 in CI or locally
            self.assertEqual(self.cli("http://127.0.0.1:9/", "--browser", self.engine, "--output", tmp)[0], 3)


class ReportInBrowser(unittest.TestCase):
    """The HTML report opens offline, loads its screenshots and runs no script (Chromium only)."""

    def test_report_renders(self):
        reason = engine_available("chromium")
        if reason:
            self.skipTest(reason)
        from playwright.sync_api import sync_playwright
        with FixtureSite() as site, tempfile.TemporaryDirectory() as tmp:
            mods = registry.discover(use_entry_points=False)
            cfg = make_cfg("chromium", tmp, crawl={"max_pages": 3})
            result = runner.run(site.url, cfg, [mods["accessibility"]])
            from qa_audit.reports import write_all
            path = write_all(result, ["html"])["html"]
            with sync_playwright() as p:
                b = p.chromium.launch()
                page = b.new_page()
                requests = []
                page.on("request", lambda r: requests.append(r.url))
                page.goto("file:///" + path.replace("\\", "/"))
                page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
                page.wait_for_timeout(500)
                loaded = page.evaluate("[...document.images].map(i => i.complete && i.naturalWidth > 0)")
                scripts = page.evaluate("document.scripts.length")
                b.close()
        self.assertTrue(loaded and all(loaded), loaded)
        self.assertEqual(scripts, 0)
        self.assertTrue(all(u.startswith("file:") for u in requests), requests)


for _engine in ENGINES:
    for _mixin, _suffix in ((EngineTests, "Audit"), (NoExternalTraffic, "Network"), (CliTests, "Cli")):
        _name = f"{_engine.capitalize()}{_suffix}"
        globals()[_name] = type(_name, (_mixin, unittest.TestCase), {"engine": _engine})
del _engine, _mixin, _suffix, _name

if __name__ == "__main__":
    unittest.main()

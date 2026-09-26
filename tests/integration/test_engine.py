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
from typing import ClassVar
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(ROOT, "src"), os.path.join(ROOT, "tests", "fixtures")):
    if p not in sys.path:
        sys.path.insert(0, p)

from server import FixtureSite, RecordingProxy, find_openssl

from qa_audit import cli
from qa_audit.browser.session import Session
from qa_audit.core import config, registry, runner
from qa_audit.core.console import Console

ENGINES = [e.strip() for e in os.environ.get("QA_AUDIT_TEST_ENGINES", "chromium,firefox").split(",") if e.strip()]
# never requested in any way (safety exclusions and robots.txt)
TRAPS = ("/logout", "/account/delete", "/private/")
# not visited as pages, only checked as links with HEAD (docs/qa-audit.md §18)
HEAD_ONLY = ("/files/brochure.pdf", "/deep/level3.html", "/deep/level-missing.html")
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
    # visual baselines always under the test's temporary folder, never in the user's ~/.qa-audit
    base = {"browser": {"engine": engine}, "crawl": {"delay_ms": 0}, "output": {"directory": out},
            "modules": {"visual": {"baseline_dir": os.path.join(out, "baselines")}}}
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
        cls.result = runner.run(cls.site.url, make_cfg(cls.engine, cls.tmp.name),
                                registry.select("full", mods))
        from qa_audit.reports import write_all
        cls.paths = write_all(cls.result, ["html", "json", "csv", "md"])
        cls.requests = list(cls.site.requests)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "site"):
            cls.site.__exit__(None, None, None)
            cls.tmp.cleanup()

    def by_page(self, page: str, module: str = "accessibility"):
        return [f for f in self.result.findings if f.url.endswith("/" + page) and f.module == module]

    def security(self) -> dict:
        return {f.check: f for f in self.result.findings if f.module == "security"}

    # --- discovery safety (risk 3, 4) ---------------------------------------------------------------------
    def test_only_get_and_no_trap_requested(self):
        self.assertEqual({m for m, _ in self.requests}, {"GET", "HEAD"})      # never POST: no --allow-submit
        for method, path in self.requests:
            for trap in TRAPS:
                self.assertFalse(path.startswith(trap), f"{method} {path} must never be requested")
            if path.startswith(HEAD_ONLY):
                self.assertEqual(method, "HEAD", path)

    def test_pages_visited_within_limits(self):
        visited = {p.url.split("/", 3)[3] for p in self.result.pages}
        self.assertEqual(visited, {"", "index.html", "about.html", "a11y.html", "form.html", "xss.html",
                                   "csp.html", "sec.html", "secure.html", "mixed.html", "slow.html", "shift.html",
                                   "broken.html", "gone.html", "error500.html", "r1", "visual.html",
                                   "deep/level1.html", "deep/level2.html"})
        errors = {p.url.split("/", 3)[3]: p for p in self.result.pages if not p.audited}
        self.assertEqual({k: v.error for k, v in errors.items() if k != "r1"},
                         {"gone.html": "HTTP 404", "error500.html": "HTTP 500"})      # not audited as content
        # /r1 redirects to about.html, already audited: recorded, not audited twice
        self.assertTrue(errors["r1"].error.startswith("same page as "), errors["r1"].error)
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

    # --- security, passive (docs/qa-audit.md §16) over plain HTTP --------------------------------------------
    def test_security_planted_defects_over_http(self):
        sec = self.security()
        self.assertEqual((sec["not-https"].status, sec["not-https"].severity), ("problem", "high"))  # password page
        self.assertIn("sec.html", sec["not-https"].description)
        self.assertIn("Apache/2.4.1", " ".join(e.value for e in sec["version-disclosure"].evidence))
        self.assertIn("sec.html", sec["version-disclosure"].url + sec["version-disclosure"].description)
        self.assertIn("'unsafe-eval'", sec["csp-unsafe"].actual)
        self.assertEqual(sec["clickjacking"].severity, "medium")          # sec.html has a password field
        for check in ("csp-missing", "clickjacking", "nosniff-missing"):
            with self.subTest(check=check):
                self.assertNotIn("secure.html", sec[check].description)   # the page with every header
        self.assertIn("sessionid", sec["cookie-session-not-httponly"].actual)
        self.assertNotIn("theme", sec["cookie-session-not-httponly"].actual)   # HttpOnly, and not session-like
        # HTTPS-only checks do not apply to an HTTP site
        for check in ("hsts-missing", "mixed-content-active", "tls-cert-expiring", "cookie-not-secure"):
            self.assertNotIn(check, sec)

    def test_security_findings_are_one_per_check(self):
        checks = [f.check for f in self.result.findings if f.module == "security"]
        self.assertEqual(len(checks), len(set(checks)))

    # --- performance (docs/qa-audit.md §17) ---------------------------------------------------------------
    def perf(self) -> dict:
        return {f.check: f for f in self.result.findings if f.module == "performance"}

    def test_slow_server_response_is_found(self):
        f = self.perf()["ttfb_ms"]
        self.assertEqual((f.status, f.severity, f.confidence), ("warning", "medium", "medium"))   # > 1800 ms: poor
        self.assertTrue(f.url.endswith("/slow.html"))
        self.assertIn("web.dev", " ".join(f.references))

    def test_layout_shift_only_where_the_engine_measures_it(self):
        cls_rows = [m for m in self.result.metrics if m["metric"] == "CLS"]
        shift = next(m for m in cls_rows if m["url"].endswith("/shift.html"))
        if self.engine == "chromium":
            self.assertGreater(shift["value"], 0.1)
            self.assertTrue(self.perf()["cls"].url.endswith("/shift.html"))
            # measured before the accessibility module resizes the viewport of a11y.html (module order)
            a11y = next(m for m in cls_rows if m["url"].endswith("/a11y.html"))
            self.assertEqual(a11y["value"], 0)
            self.assertNotIn("a11y.html", self.perf()["cls"].description)
        else:
            self.assertIsNone(shift["value"])                       # never estimated
            self.assertEqual(shift["result"], f"n/a in {self.engine}")
            self.assertNotIn("cls", self.perf())

    def test_fast_pages_are_not_blamed(self):
        for f in self.perf().values():
            self.assertNotIn("about.html", f.description)
        # metrics without a cited threshold are measured, never turned into findings
        self.assertLessEqual(set(self.perf()), {"ttfb_ms", "fcp_ms", "lcp_ms", "cls"})

    def test_every_audited_page_has_its_metrics(self):
        audited = {p.final_url for p in self.result.pages if p.audited}
        measured = {m["url"] for m in self.result.metrics if m["metric"] == "TTFB"}
        self.assertEqual(measured, audited)
        self.assertTrue(any("cold cache" in n for n in self.result.notes))

    # --- functional (docs/qa-audit.md §18) ----------------------------------------------------------------
    def fun(self) -> dict:
        return {f.check: f for f in self.result.findings if f.module == "functional"}

    def evidence(self, check: str) -> str:
        return " | ".join(e.value for e in self.fun()[check].evidence)

    def test_broken_links_with_the_pages_that_link_to_them(self):
        ev = self.evidence("broken-link")
        for missing in ("gone.html: HTTP 404", "level-missing.html: HTTP 404", "brochure.pdf: HTTP 404"):
            self.assertIn(missing, ev)
        self.assertIn("linked from", ev)
        self.assertIn("broken.html", ev)
        f = self.fun()["broken-link-server-error"]
        self.assertEqual((f.status, f.severity), ("problem", "high"))
        self.assertIn("error500.html: HTTP 500", self.evidence("broken-link-server-error"))
        self.assertNotIn("level3.html", ev)                               # exists: checked and fine

    def test_resources_errors_redirects_and_forms(self):
        self.assertIn("missing-photo.png", self.evidence("broken-resource"))
        self.assertIn("fixture uncaught exception", self.evidence("js-exception"))
        console = self.evidence("console-error")
        self.assertIn("fixture console error", console)
        self.assertNotIn("Failed to load resource", console)               # that is broken-resource
        self.assertIn("/r1 -> ", self.evidence("redirect-chain"))
        emails = self.evidence("form-email-type")
        self.assertIn('name="email" type="text"', emails)
        self.assertNotIn("form.html", emails)                              # type=email there

    def test_functional_blames_nothing_else(self):
        for f in self.fun().values():
            for clean in ("about.html", "secure.html", "form.html"):
                self.assertNotIn(clean, f.description, f.check)
        self.assertLessEqual(set(self.fun()), {"broken-link", "broken-link-server-error", "broken-resource",
                                               "broken-resource-third-party", "js-exception", "console-error",
                                               "redirect-chain", "form-email-type"})

    # --- visual: first run and responsive checks (docs/qa-audit.md §19) -----------------------------------
    def vis(self) -> dict:
        return {f.check: f for f in self.result.findings if f.module == "visual"}

    def test_first_run_creates_baselines_and_reports_no_difference(self):
        self.assertNotIn("visual-diff", self.vis())
        rows = [m for m in self.result.metrics if m["module"] == "visual"]
        self.assertTrue(rows)
        self.assertEqual({m["result"] for m in rows}, {"baseline created"})
        self.assertTrue(any("baselines in" in n and self.tmp.name in n for n in self.result.notes))

    def test_responsive_checks(self):
        overflow = self.vis()["horizontal-overflow"]
        self.assertIn("a11y.html", overflow.actual)                 # the 1200 px block
        self.assertIn("mobile 390px", overflow.actual)
        self.assertNotIn("about.html", overflow.actual)
        self.assertEqual(overflow.severity, "medium")
        meta = self.vis()["viewport-meta-missing"]
        self.assertNotIn("/about.html", meta.description)           # has the meta tag
        self.assertIn("/sec.html", meta.description)

    def test_cookie_values_never_reach_the_results(self):
        with open(self.paths["json"], encoding="utf-8") as f:
            self.assertNotIn("fixture-not-a-secret", f.read())

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
            # keep the browser open for over a minute: background timers (updates, telemetry) fire after a while;
            # a short audit hid Firefox's update check until 8.7
            cfg = make_cfg(self.engine, tmp, crawl={"delay_ms": 3500})
            mods = registry.discover(use_entry_points=False)

            def factory(c, r, t):
                return Session(c, r, t, proxy={"server": proxy.server, "bypass": ""},
                               launch_args=["--proxy-bypass-list=<-loopback>"] if self.engine == "chromium" else None,
                               firefox_prefs={"network.proxy.allow_hijacking_localhost": True})

            runner.run(site.url, cfg, [mods["accessibility"], mods["security"]], session_factory=factory)
            hosts = set(proxy.hosts)
        self.assertTrue(hosts, "the proxy saw no traffic: the test would prove nothing")
        self.assertIn(f"127.0.0.1:{site.port}", hosts)
        # mixed.html references 127.0.0.1:9 on purpose; everything must stay on this machine
        self.assertEqual({h for h in hosts if not h.startswith("127.0.0.1:")}, set())


class FormSubmitTests:
    """--allow-submit: only listed forms, only with the flag (docs/qa-audit.md §6, §18)."""

    engine = ""
    FORMS: ClassVar[list] = [{"page": r"/form\.html$", "selector": "form", "fields": {"name": "QA-AUTO Test",
                                                                      "email": "qa-auto@example.com"}}]

    def audit(self, allow: bool):
        reason = engine_available(self.engine)
        if reason:
            self.skipTest(reason)
        with FixtureSite() as site, tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(self.engine, tmp, safety={"allow_submit": allow},
                           modules={"functional": {"forms": self.FORMS}})
            mods = registry.discover(use_entry_points=False)
            result = runner.run(site.url, cfg, [mods["functional"]])
            return result, list(site.requests)

    def test_listed_form_is_not_submitted_without_the_flag(self):
        result, requests = self.audit(allow=False)
        self.assertNotIn("POST", {m for m, _ in requests})
        self.assertTrue(any("not submitted (--allow-submit not given)" in n for n in result.notes))

    def test_listed_form_is_submitted_with_the_flag_and_only_that_one(self):
        result, requests = self.audit(allow=True)
        posts = [p for m, p in requests if m == "POST"]
        self.assertEqual(posts, ["/submit"])                   # form.html only; sec.html and broken.html not
        f = next(f for f in result.findings if f.check == "form-submit-failed")
        self.assertEqual((f.status, f.severity), ("problem", "medium"))    # the fixture answers 405
        self.assertIn("HTTP 405", f.actual)


class VisualTests:
    """Baselines: created, unchanged, a planted change found only where it happened, then updated."""

    engine = ""

    def test_baseline_lifecycle(self):
        reason = engine_available(self.engine)
        if reason:
            self.skipTest(reason)
        mods = registry.discover(use_entry_points=False)
        with FixtureSite() as site, tempfile.TemporaryDirectory() as tmp:
            def audit(**visual):
                cfg = make_cfg(self.engine, tmp, crawl={"max_depth": 1},
                               modules={"visual": {"baseline_dir": os.path.join(tmp, "b"), **visual}})
                r = runner.run(site.url + "visual.html", cfg, [mods["visual"]])
                return r, {m["url"].rsplit("/", 1)[-1] + " " + m["metric"]: m["result"] for m in r.metrics}

            _, first = audit()
            self.assertEqual(set(first.values()), {"baseline created"})
            self.assertEqual(len(first), 3 * 3)                    # 3 pages x 3 viewports
            again, second = audit()
            self.assertEqual(set(second.values()), {"unchanged"}, second)   # rendering is deterministic
            self.assertNotIn("visual-diff", {f.check for f in again.findings})
            site.variant = True                                     # the hero turns red: "Winter sale"
            changed, third = audit()
            diffs = {k for k, v in third.items() if v == "changed"}
            self.assertEqual({k.split(" ")[0] for k in diffs}, {"visual.html"})
            self.assertEqual(len(diffs), 3)                         # every viewport of that page only
            f = next(f for f in changed.findings if f.check == "visual-diff")
            self.assertEqual((f.status, f.severity), ("needs-human", "medium"))
            shots = [e.path for e in f.evidence if e.kind == "screenshot"]
            self.assertTrue(any("baseline" in p for p in shots) and any("diff" in p for p in shots))
            for p in shots:
                self.assertTrue(os.path.isfile(os.path.join(changed.run_folder, p)), p)
            updated, fourth = audit(update_baseline=True)
            self.assertEqual(set(fourth.values()), {"baseline updated"})
            self.assertNotIn("visual-diff", {f.check for f in updated.findings})
            _, fifth = audit()
            self.assertEqual(set(fifth.values()), {"unchanged"})


class AuthTests:
    """Login before the crawl (docs/qa-audit.md §20), with the fixture's fictional account."""

    engine = ""
    ENV: ClassVar[dict] = {"QA_FIXTURE_USER": "qa-auto-fixture", "QA_FIXTURE_PASS": "open-sesame-fixture"}
    FORM: ClassVar[dict] = {"method": "form", "login_url": "/login.html", "username_env": "QA_FIXTURE_USER",
                            "pass_env": "QA_FIXTURE_PASS", "success_url": "/account/"}

    def audit(self, auth_section, env, modules=("accessibility", "security")):
        reason = engine_available(self.engine)
        if reason:
            self.skipTest(reason)
        mods = registry.discover(use_entry_points=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with FixtureSite() as site, mock.patch.dict(os.environ, env):
            cfg = make_cfg(self.engine, self.tmp.name, auth=auth_section, browser={"timeout_ms": 8000})
            try:
                result = runner.run(site.url + "account/index.html", cfg, [mods[m] for m in modules])
            finally:
                self.requests = list(site.requests)
        return result

    def posts(self):
        return [path for method, path in self.requests if method == "POST"]

    def test_form_login_audits_the_protected_area_once(self):
        r = self.audit(self.FORM, self.ENV)
        self.assertEqual(self.posts(), ["/login"])                          # one login, nothing else posted
        audited = {p.url.rsplit("/", 1)[-1] for p in r.pages if p.audited}
        self.assertTrue({"index.html", "settings.html", "expire.html"} <= audited, audited)
        self.assertTrue(r.crawl["session_expired"].endswith("/account/after.html"))
        self.assertTrue(any("session ended" in n for n in r.notes))
        self.assertNotIn("/logout", [path for _, path in self.requests])   # the session is never ended by us

    def test_credentials_never_reach_any_output(self):
        r = self.audit(self.FORM, self.ENV)
        from qa_audit.reports import write_all
        write_all(r, ["html", "json", "csv", "md"])
        seen_placeholder = False
        for folder, _, files in os.walk(r.run_folder):
            for name in files:
                if name.endswith((".json", ".csv", ".md", ".html", ".log")):
                    with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    for value in self.ENV.values():
                        self.assertNotIn(value, text, f"{value!r} in {name}")
                    seen_placeholder |= "«QA_FIXTURE_USER»" in text
        self.assertTrue(seen_placeholder, "the user name shown on settings.html was not redacted anywhere")

    def test_wrong_password_stops_after_one_attempt(self):
        from qa_audit.core.auth import AuthError
        with self.assertRaises(AuthError):
            self.audit(self.FORM, {**self.ENV, "QA_FIXTURE_PASS": "wrong-on-purpose"})
        self.assertEqual(self.posts(), ["/login"])
        self.assertFalse([p for _, p in self.requests if p.startswith("/account/") and p != "/account/index.html"])

    def test_manual_login_waits_for_the_person(self):
        manual = {"method": "manual", "login_url": "/login-auto.html", "success_url": "/account/",
                  "expired_url": "/login", "manual_timeout_s": 30}
        r = self.audit(manual, {}, modules=("security",))                 # the page plays the person
        self.assertEqual(self.posts(), ["/login"])
        self.assertIn("index.html", {p.url.rsplit("/", 1)[-1] for p in r.pages if p.audited})


def https_available(engine: str) -> str:
    """'' if this engine loads the HTTPS fixture here, else the reason (e.g. Firefox with broken cryptography)."""
    reason = engine_available(engine)
    if reason:
        return reason
    if not find_openssl():
        reason = "openssl not found (needed for the HTTPS fixture)"
    else:
        from playwright.sync_api import Error, sync_playwright
        with FixtureSite(tls=True) as site, sync_playwright() as p:
            b = getattr(p, engine).launch()
            try:
                b.new_context(ignore_https_errors=True).new_page().goto(site.url)
            except Error as e:
                reason = f"{engine} cannot load HTTPS here: {str(e).splitlines()[0]}"
            finally:
                b.close()
    if reason and os.environ.get("QA_AUDIT_REQUIRE_ENGINES"):
        raise AssertionError(reason)
    return reason


class HttpsTests:
    """Security checks that only exist over HTTPS: fixture with a self-signed certificate valid for two days."""

    engine = ""

    @classmethod
    def setUpClass(cls):
        reason = https_available(cls.engine)
        if reason:
            raise unittest.SkipTest(reason)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.site = FixtureSite(tls=True).__enter__()
        mods = registry.discover(use_entry_points=False)
        cfg = make_cfg(cls.engine, cls.tmp.name, browser={"ignore_https_errors": True})
        cls.result = runner.run(cls.site.url, cfg, [mods["security"]])
        cls.sec = {f.check: f for f in cls.result.findings}

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "site"):
            cls.site.__exit__(None, None, None)
            cls.tmp.cleanup()

    def test_https_planted_defects(self):
        s = self.sec
        self.assertNotIn("not-https", s)
        self.assertIn("expires in", s["tls-cert-expiring"].actual)
        self.assertEqual((s["form-to-http"].status, s["form-to-http"].severity), ("problem", "high"))
        self.assertIn("mixed.html", s["mixed-content-active"].url)
        self.assertIn("fixture-insecure.js", " ".join(e.value for e in s["mixed-content-active"].evidence))
        self.assertIn("fixture-insecure.png", " ".join(e.value for e in s["mixed-content-passive"].evidence))
        self.assertIn("sessionid", s["cookie-not-secure"].actual)
        self.assertNotIn("secure.html", s["hsts-missing"].description)   # the page that sends HSTS

    def test_https_nothing_invented(self):
        self.assertNotIn("tls-old-protocol", self.sec)          # the fixture negotiates TLS 1.2+
        self.assertNotIn("http-not-redirected", self.sec)       # nothing answers plain HTTP on that port
        self.assertTrue(any("ignore-https-errors" in n for n in self.result.notes))
        self.assertEqual(self.result.errors, [])


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
            common = ["--browser", self.engine, "--delay-ms", "0", "--output", tmp, "--max-pages", "3",
                      "--baseline-dir", os.path.join(tmp, "baselines")]
            code, out, _ = self.cli(site.url, *common)
            self.assertEqual(code, 1, out)                          # high-severity problems on a11y.html
            self.assertIn("[1/7] Discovery", out)   # + performance, security, functional, visual, accessibility, report
            self.assertIn("Security", out)
            self.assertIn("Only audit sites you own or are authorized to test.", out)
            self.assertIn("report.html", out)
            code, out, _ = self.cli(site.url, *common, "--fail-on", "none", "--cleanup", "--report", "json")
            self.assertEqual(code, 0)
            self.assertIn("Run folder deleted", out)
            runs = [d for d in os.listdir(tmp) if not d.startswith(".") and d != "baselines"]
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
    for _mixin, _suffix in ((EngineTests, "Audit"), (NoExternalTraffic, "Network"), (CliTests, "Cli"),
                            (HttpsTests, "Https"), (FormSubmitTests, "Forms"), (VisualTests, "Visual"),
                            (AuthTests, "Auth")):
        _name = f"{_engine.capitalize()}{_suffix}"
        globals()[_name] = type(_name, (_mixin, unittest.TestCase), {"engine": _engine})
del _engine, _mixin, _suffix, _name

if __name__ == "__main__":
    unittest.main()

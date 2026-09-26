"""Several engines in one run (docs/qa-audit.md §21): the comparison, the engine list and the combined exit code.
No browser: runs are fakes with pages and findings."""

import contextlib
import io
import os
import tempfile
import types
import unittest
from unittest import mock

from qa_audit import cli
from qa_audit.core import compatibility, config
from qa_audit.core.findings import Evidence, Finding

SITE = "https://shop.example"


def page(path, status=200, audited=True, error=""):
    return types.SimpleNamespace(url=SITE + path, status=status, audited=audited, error=error)


def finding(path, module="accessibility", check="axe/image-alt", selector="img", status="problem"):
    return Finding(module=module, check=check, title=f"title {check}", status=status, severity="medium",
                   url=SITE + path, element={"selector": selector, "snippet": "<img>"},
                   evidence=[Evidence(kind="html", value="<img>")])


def run(pages, findings):
    return types.SimpleNamespace(pages=pages, findings=findings)


class Compare(unittest.TestCase):
    def test_same_results_everywhere_no_difference(self):
        same = [finding("/a")]
        runs = {"chromium": run([page("/"), page("/a")], same), "firefox": run([page("/"), page("/a/")], same)}
        self.assertEqual(compatibility.compare(runs), [])

    def test_a_finding_in_one_engine_only(self):
        runs = {"chromium": run([page("/a")], [finding("/a")]),
                "firefox": run([page("/a")], []),
                "webkit": run([page("/a")], [finding("/a")])}
        diffs = compatibility.compare(runs)
        self.assertEqual(len(diffs), 1)
        d = diffs[0]
        self.assertEqual((d.module, d.check, d.status, d.severity), ("compatibility", "engine-difference",
                                                                     "needs-human", "info"))
        self.assertEqual(d.actual, "present in: chromium, webkit; absent in: firefox")
        self.assertEqual(d.element["selector"], "img")

    def test_pages_found_audited_or_answered_differently(self):
        runs = {"chromium": run([page("/"), page("/only-here"), page("/x", status=200), page("/e")], []),
                "firefox": run([page("/"), page("/x", status=404, audited=False, error="HTTP 404"),
                                page("/e", audited=False, error="timeout")], [])}
        titles = sorted(d.title for d in compatibility.compare(runs))
        self.assertEqual(titles, ["Different HTTP status per engine", "Page audited in some engines only",
                                  "Page audited in some engines only", "Page visited in some engines only"])

    def test_findings_on_pages_not_audited_everywhere_are_not_compared_twice(self):
        runs = {"chromium": run([page("/e")], [finding("/e")]),
                "firefox": run([page("/e", audited=False, error="timeout")], [])}
        self.assertEqual([d.title for d in compatibility.compare(runs)], ["Page audited in some engines only"])

    def test_performance_and_visual_baselines_are_not_compatibility(self):
        runs = {"chromium": run([page("/")], [finding("/", module="performance", check="cls", selector=""),
                                              finding("/", module="visual", check="visual-diff", selector="")]),
                "firefox": run([page("/")], [])}
        self.assertEqual(compatibility.compare(runs), [])
        runs["chromium"].findings.append(finding("/", module="visual", check="viewport-overflow", selector=""))
        self.assertEqual(len(compatibility.compare(runs)), 1)       # the visual overflow checks are compared

    def test_one_engine_nothing_to_compare(self):
        self.assertEqual(compatibility.compare({"chromium": run([page("/")], [finding("/")])}), [])


class Engines(unittest.TestCase):
    def test_parse_and_validate(self):
        self.assertEqual(config.parse_engines("firefox"), "firefox")
        self.assertEqual(config.parse_engines("chromium, webkit"), ["chromium", "webkit"])
        self.assertEqual(config.engines(config.build(None, {"browser": {"engine": "all"}})),
                         ["chromium", "firefox", "webkit"])
        self.assertEqual(config.engines(config.build()), ["chromium"])
        for bad in (["chromium", "chromium"], ["chromium", "edge"], [], "ie"):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.build(None, {"browser": {"engine": bad}})

    def test_manual_login_is_refused_with_several_engines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "auth.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("auth: {method: manual, login_url: /login, success_url: /account}\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = cli.main([SITE, "--config", path, "--browser", "chromium,firefox", "--no-color"])
        self.assertEqual(code, 2)
        self.assertIn("storage-state", err.getvalue())


class ExitCode(unittest.TestCase):
    """The worst of the runs; 4 only when no engine starts; 3 when the target was reached nowhere."""

    def many(self, outcomes: dict):
        def fake(target, cfg, modules, console, err, run_name=None):
            code, label, findings = outcomes[run_name]
            if code in (3, 4):
                return code, None, {}, label
            result = types.SimpleNamespace(pages=[page("/")], findings=findings, summary={"by_status": {}},
                                           browser={"version": "1"})
            return code, result, {"html": "x"}, label
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cli, "_audit_one", side_effect=fake), \
                contextlib.redirect_stdout(io.StringIO()):
            code = cli.main([SITE, "--browser", ",".join(outcomes), "--output", tmp, "--delay-ms", "0",
                             "--report", "json", "--no-color"])
            folders = os.listdir(tmp)
            run_dir = os.path.join(tmp, next(f for f in folders if not f.startswith(".")))
            return code, sorted(os.listdir(run_dir))

    def test_worst_of_the_runs(self):
        problem = [Finding(module="security", check="x", title="t", status="problem", severity="high", url=SITE + "/",
                           evidence=[Evidence(kind="text", value="v")])]
        code, files = self.many({"chromium": (0, "audited", []), "firefox": (1, "audited", problem)})
        self.assertEqual(code, 1)
        self.assertIn("compatibility.json", files)

    def test_no_engine_started_is_4_unreachable_everywhere_is_3(self):
        self.assertEqual(self.many({"chromium": (4, "browser could not start", []),
                                    "webkit": (4, "browser could not start", [])})[0], 4)
        self.assertEqual(self.many({"chromium": (3, "target unreachable", []),
                                    "webkit": (4, "browser could not start", [])})[0], 3)

    def test_some_engines_failing_do_not_hide_the_others(self):
        self.assertEqual(self.many({"chromium": (0, "audited", []), "webkit": (4, "browser could not start", [])})[0],
                         0)


if __name__ == "__main__":
    unittest.main()

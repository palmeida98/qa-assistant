"""qa-ui server and page (docs/qa-ui.md sections 2 and 11; plan 12.4): loopback only, the token, the Host check, the
routes with their status codes, the whole flow over HTTP with a fake `claude`, and a page with no external address.
Fictional data only (Demo Shop).

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

import qa_ui_kit as kit

from qa_ui import __main__ as cli
from qa_ui import runs, server
from qa_ui import workspace as wk

STATIC = os.path.join(ROOT, "qa_ui", "static")
READY = [(True, t, "") for t in ("git", "python", ".env", "skills", "node")]
TEXT = ("The checkout has a new payment form.\nTC-SHOP-001 Add item to cart → the cart shows 1 item\n"
        "TC-SHOP-002 Pay with a valid card → order created\nTC-SHOP-003 Apply an expired coupon → refused\n"
        "API-002 POST /orders without a token → 401\n")


class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = kit.workspace()
        self.ws = self.tmp.name
        self.bin = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "ok"})
        self.env.start()
        self.ready = mock.patch.object(wk, "readiness", return_value=READY)
        self.ready.start()
        self.runner = runs.Runner(self.ws, claude=kit.fake_claude(self.bin.name), grace=1)
        self.srv, self.url = server.serve(self.ws, self.runner, 0)
        self.base, self.token = self.url.split("/?token=")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.runner.shutdown()
        self.srv.shutdown()
        self.srv.server_close()
        self.ready.stop()
        self.env.stop()
        self.bin.cleanup()
        self.tmp.cleanup()

    def call(self, method, path, body=None, token=True, headers=None, raw=None):
        h = {"X-QA-Token": self.token} if token is True else ({"X-QA-Token": token} if token else {})
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}"), r.headers
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}"), e.headers

    def test_loopback_only_and_a_token_per_start(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        self.assertGreaterEqual(len(self.token), 24)
        other, url = server.serve(self.ws, self.runner, 0)
        self.addCleanup(other.server_close)
        self.assertNotEqual(url.split("token=")[1], self.token)

    def test_api_needs_the_token_and_this_servers_host(self):
        for token in (False, "wrong", self.token[:-1]):
            with self.subTest(token=token):
                status, body, _ = self.call("GET", "/api/state", token=token)
                self.assertEqual(status, 403)
                self.assertIn("token", body["error"])
                self.assertNotIn("project", body)
        self.assertEqual(self.call("POST", "/api/intake", {"text": TEXT}, token=False)[0], 403)
        status, body, _ = self.call("GET", "/api/state", headers={"Host": "evil.example"})
        self.assertEqual((status, body), (403, {"error": "this address is not the interface's"}))
        self.assertEqual(self.call("GET", "/", token=False, headers={"Host": "evil.example"})[0], 403)
        port = self.srv.server_address[1]
        self.assertEqual(self.call("GET", "/api/state", headers={"Host": f"localhost:{port}"})[0], 200)

    def test_the_page_is_served_without_data_and_with_a_strict_policy(self):
        for path, kind in (("/", "text/html"), ("/static/app.js", "text/javascript"), ("/static/app.css", "text/css")):
            req = urllib.request.Request(self.base + path)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8")
                self.assertIn(kind, r.headers["Content-Type"])
                self.assertEqual(r.headers["Cache-Control"], "no-store")
                self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")
                if path == "/":
                    csp = r.headers["Content-Security-Policy"]
                    self.assertIn("default-src 'none'", csp)
                    self.assertIn("script-src 'self'", csp)
                    self.assertIn("frame-ancestors 'none'", csp)
                    self.assertNotIn("unsafe", csp)
            self.assertNotIn(self.token, body)
            self.assertNotIn("demo-shop", body)
        for path in ("/static/../server.py", "/static/app.py", "/project.yaml", "/.env"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path, token=False)[0], 404)

    def test_bad_requests_are_400_and_unknown_routes_404(self):
        self.assertEqual(self.call("POST", "/api/intake", raw=b"{", headers={"Content-Type": "application/json"})[0],
                         400)
        self.assertEqual(self.call("POST", "/api/intake", raw=b"text=x",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 400)
        for path, body, text in (("/api/intake", {}, "nothing to read"),
                                 ("/api/intake", {"file": "cases.csv"}, "`file` must be"),
                                 ("/api/intake", {"file": {"name": "x.pdf", "data": ""}}, ".pdf is not read"),
                                 ("/api/suggest", {"cases": "none"}, "`cases` must be a list"),
                                 ("/api/suggest", {"cases": [{"title": "no id"}]}, "needs an `id`"),
                                 ("/api/plan", {"cases": [], "assignments": [], "cards": []}, "`assignments`"),
                                 ("/api/runs", {}, "`batch`"),
                                 ("/api/runlogs/b1/items/TC-SHOP-001", {}, "`action`")):
            with self.subTest(path=path, body=body):
                status, out, _ = self.call("POST", path, body)
                self.assertEqual(status, 400, out)
                self.assertIn(text, out["error"])
        for method, path in (("GET", "/api/nope"), ("POST", "/api/state"), ("GET", "/api/runs/nope"),
                             ("GET", "/api/runlogs/nope"), ("POST", "/api/runs/nope/stop"), ("GET", "/api")):
            with self.subTest(path=path):
                self.assertEqual(self.call(method, path, {} if method == "POST" else None)[0], 404)

    def test_state_has_no_value_of_env_and_no_url(self):
        status, st, _ = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual((st["project"]["name"], st["project"]["tracker"], st["claude"], st["run"]),
                         ("demo-shop", "excel", True, None))
        self.assertEqual(st["test_environment"], "test")
        self.assertEqual(st["limits"], {"max_turns": 150, "timeout_minutes": 60})
        self.assertEqual([c["tool"] for c in st["checks"]], [t for _, t, _ in READY])
        text = json.dumps(st)
        self.assertNotIn(kit.SECRET, text)
        self.assertNotIn("shop.example", text)

    def test_the_whole_flow_over_http(self):
        # 1. intake
        status, got, _ = self.call("POST", "/api/intake", {"text": TEXT})
        self.assertEqual(status, 200)
        self.assertEqual(([c["id"] for c in got["cases"]], len(got["notes"])),
                         (["TC-SHOP-001", "TC-SHOP-002", "TC-SHOP-003", "API-002"], 1))
        # 2. suggestion, with one case moved by the user
        status, sug, _ = self.call("POST", "/api/suggest", {"cases": got["cases"], "notes": got["notes"],
                                                            "moved": {"TC-SHOP-003": "exploratory"}})
        cards = {c["key"]: c for c in sug["cards"]}
        self.assertEqual((cards["functional-web"]["cases"], cards["api"]["cases"], cards["exploratory"]["cases"]),
                         (["TC-SHOP-001", "TC-SHOP-002"], ["API-002"], ["TC-SHOP-003"]))
        self.assertTrue(cards["functional-web"]["ready"])
        self.assertFalse(cards["exploratory"]["ready"])                 # not active in project.yaml
        # 3. plan: shown, then saved; the case that is not in the library is listed apart
        body = {"cases": got["cases"], "assignments": sug["assignments"], "cards": ["functional-web", "api"]}
        status, plan, _ = self.call("POST", "/api/plan", body)
        self.assertEqual(([b["cases"] for b in plan["batches"]], [a["id"] for a in plan["apart"]]),
                         ([["TC-SHOP-001", "TC-SHOP-002"]], ["API-002"]))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "batches")))
        status, saved, _ = self.call("POST", "/api/plan", dict(body, save=True))
        name = saved["saved"][0]
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "batches", f"{name}.yaml")))
        self.assertEqual(self.call("POST", "/api/plan", dict(body, cards=[], save=True))[0], 409)   # nothing to save
        # 4. run: the fake agent writes a Pass and a Fail with a proposed bug
        status, run, _ = self.call("POST", "/api/runs", {"batch": name, "environment": "test", "build": "2.4.1"})
        self.assertEqual((status, run["state"], run["batch"]), (200, "running", f"batch_{name}"))
        self.assertTrue(self.runner.runs[run["id"]].done.wait(30))
        status, out, _ = self.call("GET", f"/api/runs/{run['id']}")
        self.assertEqual((out["state"], [i["status"] for i in out["run_log"]["items"]]), ("finished", ["Pass", "Fail"]))
        self.assertNotIn(kit.SECRET, json.dumps(out))
        # 5. what needs the user, then the edit
        status, log, _ = self.call("GET", f"/api/runlogs/{name}")
        self.assertEqual((log["planned"], [n["kind"] for n in log["needs"]], log["check"]["ready"]),
                         (["TC-SHOP-001", "TC-SHOP-002"], ["defect"], False))
        status, apply_, _ = self.call("POST", f"/api/runlogs/{name}/apply", {"confirm": True})
        self.assertEqual((status, apply_["done"], apply_["ready"]), (200, False, False))             # refused
        self.assertEqual(self.call("POST", f"/api/runlogs/{name}/items/TC-SHOP-002", {"action": "nope"})[0], 409)
        self.assertEqual(self.call("POST", f"/api/runlogs/{name}/items/TC-SHOP-999", {"action": "approve"})[0], 404)
        status, log, _ = self.call("POST", f"/api/runlogs/{name}/items/TC-SHOP-002", {"action": "approve"})
        self.assertEqual((status, log["needs"]), (200, []))
        status, check, _ = self.call("POST", f"/api/runlogs/{name}/apply", {})
        self.assertEqual((check["ready"], check["dry_run"], check["done"]), (True, True, False))     # nothing written
        self.assertEqual(self.call("GET", "/api/state")[1]["run_logs"][0]["needs"], 0)

    def test_one_run_at_a_time_is_409_and_stop_works(self):
        self.call("POST", "/api/plan", {"cases": [{"id": "TC-SHOP-001", "title": "Add item to cart"}],
                                        "assignments": {"TC-SHOP-001": {"module": "functional-web"}},
                                        "cards": ["functional-web"], "save": True})
        name = wk.batches(self.ws)[0]["name"]
        with mock.patch.dict(os.environ, {"QA_FAKE_CLAUDE": "slow"}):
            status, run, _ = self.call("POST", "/api/runs", {"batch": name})
            self.assertEqual(status, 200)
            status, out, _ = self.call("POST", "/api/runs", {"batch": name})
            self.assertEqual(status, 409)
            self.assertIn("a run is in progress", out["error"])
            self.assertEqual(self.call("POST", "/api/runs", {"audit": "site"})[0], 409)
            self.assertEqual(self.call("GET", "/api/state")[1]["run"]["id"], run["id"])
            status, out, _ = self.call("POST", f"/api/runs/{run['id']}/stop", {})
        self.assertEqual((status, out["state"], out["reason"]), (200, "stopped", "stopped by the user"))
        self.assertIsNone(self.call("GET", "/api/state")[1]["run"])

    def test_new_cases_dry_run_over_http(self):
        status, dry, _ = self.call("POST", "/api/cases", {"cases": [{"id": "TC-SHOP-005", "title": "Pay with an "
                                                                    "expired card", "expected": "refused"}]})
        self.assertEqual((status, dry["dry_run"], dry["can_confirm"]), (200, True, True))
        self.assertEqual(self.call("POST", "/api/cases", {"confirm": "cases/other.json"})[0], 409)
        self.assertEqual(self.call("POST", "/api/cases", {"cases": []})[0], 409)

    def test_ask_claude_over_http(self):
        status, out, _ = self.call("POST", "/api/ask-claude", {"cases": [{"id": "API-002", "title": "POST /orders"}],
                                                               "notes": ["a note"], "proposals": False})
        self.assertEqual((status, out["ok"], out["assignments"]["API-002"]["module"], out["proposals"]),
                         (200, True, "api", []))

    def test_a_broken_project_yaml_is_an_answer_and_not_a_dropped_connection(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("project: [unclosed\n")
        status, out, _ = self.call("GET", "/api/state")
        self.assertEqual(status, 500)
        self.assertIn("the request failed", out["error"])
        os.remove(os.path.join(self.ws, "project.yaml"))
        self.assertEqual(self.call("GET", "/api/state")[0], 409)


class Page(unittest.TestCase):
    def read(self, name: str) -> str:
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            return f.read()

    def test_the_page_has_no_external_address(self):
        for name in sorted(os.listdir(STATIC)):
            text = self.read(name)
            with self.subTest(file=name):
                self.assertEqual(re.findall(r"(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text), [])
                self.assertNotRegex(text, r"@import|url\(")
        self.assertEqual(sorted(os.listdir(STATIC)), ["app.css", "app.js", "index.html"])
        self.assertEqual(sorted(n for n, _ in server.STATIC.values()), ["app.css", "app.js", "index.html"])

    def test_no_inline_script_or_style_and_data_never_as_html(self):
        html = self.read("index.html")
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)", html), [])
        self.assertNotRegex(html, r"<style|\sstyle=|\son[a-z]+=")
        script = self.read("app.js")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".style."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, script)

    def test_the_page_calls_only_routes_the_server_has(self):
        called = set(re.findall(r"'(/api/[a-z-]+)", self.read("app.js"))) | \
            set(re.findall(r"`(/api/[a-z-]+)", self.read("app.js")))
        self.assertEqual(called, {"/api/state", "/api/intake", "/api/suggest", "/api/plan", "/api/cases",
                                  "/api/ask-claude", "/api/runs", "/api/runlogs"})


class CommandLine(unittest.TestCase):
    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_configuration_problems_are_2(self):
        with tempfile.TemporaryDirectory() as empty:
            code, out = self.run_(empty, "--no-open")
            self.assertEqual(code, 2)
            self.assertIn("project.yaml does not exist", out)
        tmp = kit.workspace()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(self.run_(tmp.name, "--no-open", "--max-turns", "0")[0], 2)
        self.assertEqual(self.run_(tmp.name, "--no-open", "--timeout-minutes", "0")[0], 2)
        busy, _ = server.serve(tmp.name, runs.Runner(tmp.name), 0)
        self.addCleanup(busy.server_close)
        code, out = self.run_(tmp.name, "--no-open", "--port", str(busy.server_address[1]))
        self.assertEqual(code, 2)
        self.assertIn("cannot be used", out)

    def test_it_prints_the_address_with_the_token_and_stops_with_0(self):
        tmp = kit.workspace()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(server.ThreadingHTTPServer, "serve_forever", side_effect=KeyboardInterrupt), \
                mock.patch.object(cli.webbrowser, "open") as opened:
            code, out = self.run_(tmp.name, "--port", "0")
        self.assertEqual(code, 0)
        url = re.search(r"Open: (http://127\.0\.0\.1:\d+/\?token=\S{24,})", out).group(1)
        opened.assert_called_once_with(url)
        self.assertIn("demo-shop", out)


if __name__ == "__main__":
    unittest.main()

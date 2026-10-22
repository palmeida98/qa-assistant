"""qa-ui hub (docs/qa-ui.md section 14; plan 13.1): the discovery of the projects and its refusals, the summaries of
the Projects screen, the pins, one run at a time on the computer, the hub's routes, a project's routes under
/api/p/<id>/ (plan 13.2) and its command line. A fictional
root built by the test; Demo Shop data only.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import copy
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
import run_log

from qa_ui import __main__ as cli
from qa_ui import hub, runs, server


def make_project(root, pid, **changes):
    path = os.path.join(root, *pid.split("/"))
    os.makedirs(path)
    proj = copy.deepcopy(kit.PROJECT)
    proj["excel"] = copy.deepcopy(kit.EXCEL)
    proj.update(changes)
    if proj.get("tracker") == "excel":
        kit.build(os.path.join(path, kit.EXCEL["file"]))
    kit.write_project(path, proj)
    return path


def add_run_log(path, name, results):
    run_log.write_log(run_log.run_log_path(path, name), {"run": {"environment": "test"}, "results": results})


PASS = {"tc": "TC-SHOP-001", "status": "Pass", "actual_result": "as expected"}
QUESTION = {"tc": "TC-SHOP-002", "status": "Question", "actual_result": "which message is expected?"}
PROPOSED = {"tc": "TC-SHOP-003", "status": "Fail", "actual_result": "the coupon is accepted",
            "new_defect": {"title": "Expired coupon accepted", "proposed": True}}


class Root(unittest.TestCase):
    """A fictional root: demo/shop (excel, with run logs), acme/api (jira), demo/broken (project.yaml not valid),
    and what is never a project."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "qa-workspaces")
        self.shop = make_project(self.root, "demo/shop", project="demo-shop")
        self.api = make_project(self.root, "acme/api", project="acme-api", tracker="jira", modules=["api"])
        broken = os.path.join(self.root, "demo", "broken")
        os.makedirs(broken)
        with open(os.path.join(broken, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("project: [unclosed\n")
        # never a project: no project.yaml, a hidden or private folder, a third level, a file in the root
        os.makedirs(os.path.join(self.root, "demo", "empty"))
        make_project(self.root, ".hidden/shop")
        make_project(self.root, "_scratch/shop")
        make_project(self.root, "demo/shop/deeper")
        with open(os.path.join(self.root, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("not a project\n")
        self.prefs = hub.Prefs(os.path.join(self.tmp.name, "home", ".qa-assistant", "qa-ui.json"))
        self.hub = hub.Hub(self.root, self.prefs)

    def tearDown(self):
        for runner in self.hub.gate.runners:
            runner.shutdown()
        self.tmp.cleanup()


class Discovery(Root):
    def test_two_levels_with_a_project_yaml_sorted(self):
        self.assertEqual([p.id for p in hub.discover(self.root)], ["acme/api", "demo/broken", "demo/shop"])
        shop = hub.find(self.root, "demo/shop")
        self.assertEqual((shop.company, shop.folder), ("demo", "shop"))
        self.assertEqual(os.path.normcase(shop.path), os.path.normcase(os.path.abspath(self.shop)))

    def test_an_id_is_looked_up_never_joined(self):
        for bad in ("../qa-workspaces/demo/shop", "demo/../demo/shop", "demo", "demo/shop/deeper", ".hidden/shop",
                    "_scratch/shop", "demo/empty", os.path.abspath(self.shop), "demo\\shop", "demo/shop/", ""):
            with self.subTest(bad=bad), self.assertRaises(hub.NotFound):
                hub.find(self.root, bad)

    def test_a_link_is_never_followed(self):
        outside = make_project(self.tmp.name, "outside/secret")
        try:
            os.symlink(os.path.dirname(outside), os.path.join(self.root, "linked"), target_is_directory=True)
            os.symlink(outside, os.path.join(self.root, "demo", "linked"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links cannot be created here")
        self.assertEqual([p.id for p in hub.discover(self.root)], ["acme/api", "demo/broken", "demo/shop"])

    def test_a_root_that_does_not_exist_has_no_project(self):
        self.assertEqual(hub.discover(os.path.join(self.tmp.name, "nothing")), [])

    def test_the_default_root_is_next_to_this_repository(self):
        self.assertEqual(hub.default_root(), os.path.join(os.path.dirname(ROOT), "qa-workspaces"))


class Summaries(Root):
    def test_a_card_has_the_project_its_last_run_and_what_needs_the_user(self):
        add_run_log(self.shop, "20261020_web_01", [PASS])
        add_run_log(self.shop, "20261021_web_02", [PASS, QUESTION, PROPOSED])
        os.utime(run_log.run_log_path(self.shop, "20261020_web_01"), (1_700_000_000, 1_700_000_000))
        card = hub.summary(hub.find(self.root, "demo/shop"))
        self.assertEqual((card["name"], card["tracker"], card["modules"]), ("demo-shop", "excel",
                                                                            ["functional-web", "api"]))
        self.assertEqual(card["runs"], 2)
        self.assertEqual(card["last_run"]["batch"], "20261021_web_02")
        self.assertEqual(card["needs"], 2)
        self.assertIsNone(card["error"])

    def test_an_applied_run_log_needs_nobody(self):
        add_run_log(self.api, "20261021_api_01", [QUESTION])
        self.assertEqual(hub.summary(hub.find(self.root, "acme/api"))["needs"], 1)
        os.makedirs(os.path.join(self.api, "tracker", "sent"))
        with open(os.path.join(self.api, "tracker", "sent", "batch_20261021_api_01.json"), "w") as f:
            f.write("{}")
        self.assertEqual(hub.summary(hub.find(self.root, "acme/api"))["needs"], 0)

    def test_a_broken_project_yaml_does_not_stop_the_hub(self):
        state = self.hub.state()
        broken = next(p for p in state["projects"] if p["id"] == "demo/broken")
        self.assertIn("project.yaml cannot be read", broken["error"])
        self.assertNotIn(self.root, broken["error"])                  # said relative to the project
        self.assertEqual(len(state["projects"]), 3)

    def test_no_value_of_env_in_the_hub_state(self):
        with open(os.path.join(self.shop, ".env"), "w", encoding="utf-8") as f:
            f.write("".join(f"{k}={v}\n" for k, v in kit.ENV.items()))
        self.assertNotIn(kit.SECRET, json.dumps(self.hub.state()))


class Pins(Root):
    def test_pins_are_kept_outside_the_root_and_only_for_discovered_projects(self):
        self.hub.pin("demo/shop", True)
        self.hub.pin("acme/api", True)
        self.hub.pin("acme/api", False)
        self.assertEqual(self.hub.state()["pinned"], ["demo/shop"])
        with open(self.prefs.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"pinned": ["demo/shop"]})
        with self.assertRaises(hub.NotFound):
            self.hub.pin("demo/../acme/api", True)
        self.assertFalse(any(name.endswith(".json") for _, _, files in os.walk(self.root) for name in files
                             if name == "qa-ui.json"))

    def test_an_unreadable_prefs_file_is_no_pin(self):
        os.makedirs(os.path.dirname(self.prefs.path))
        with open(self.prefs.path, "w", encoding="utf-8") as f:
            f.write("not json")
        self.assertEqual(self.prefs.pinned(), [])
        self.assertEqual(self.hub.pin("demo/shop", True), {"id": "demo/shop", "pinned": True})


class OneRunOnTheComputer(Root):
    def runner(self, pid):
        return runs.Runner(hub.find(self.root, pid).path, gate=self.hub.gate, name=pid)

    def test_a_run_in_one_project_refuses_a_run_in_another(self):
        shop, api = self.runner("demo/shop"), self.runner("acme/api")
        busy = runs.Run("r1", "batch", "20261021_web_01 (functional-web, 3 case(s))", [], "r1.log")
        shop.runs[busy.id] = busy
        with self.assertRaises(runs.Refused) as e:
            api.start_audit({"modules": ["accessibility"]}, "a11y")
        self.assertIn("in demo/shop", str(e.exception))
        self.assertIn("one run at a time on this computer", str(e.exception))
        with self.assertRaises(runs.Refused) as e:
            shop.start_audit({"modules": ["accessibility"]}, "a11y")
        self.assertIn("one run at a time per workspace", str(e.exception))
        state = self.hub.state()
        self.assertEqual((state["run"]["project"], state["run"]["id"]), ("demo/shop", "r1"))
        busy.state = "finished"
        self.assertIsNone(self.hub.state()["run"])

    def test_a_runner_alone_keeps_its_own_gate(self):
        a, b = runs.Runner(self.shop), runs.Runner(self.api)
        self.assertIsNot(a.gate, b.gate)
        a.runs["r1"] = runs.Run("r1", "batch", "b1", [], "r1.log")
        self.assertIsNone(b.gate.current())


class Routes(Root):
    def setUp(self):
        super().setUp()
        self.app = server.HubApp(self.hub)

    def test_the_hub_routes(self):
        self.assertEqual(self.app.handle("GET", "/api/mode"), (200, {"mode": "hub"}))
        status, state = self.app.handle("GET", "/api/hub")
        self.assertEqual(status, 200)
        self.assertEqual([p["id"] for p in state["projects"]], ["acme/api", "demo/broken", "demo/shop"])
        self.assertEqual(self.app.handle("POST", "/api/hub/pins", {"id": "acme/api", "pinned": True}),
                         (200, {"id": "acme/api", "pinned": True}))
        self.assertEqual(self.app.handle("GET", "/api/hub")[1]["pinned"], ["acme/api"])

    def test_one_workspace_mode_says_so(self):
        app = server.App(self.shop, runs.Runner(self.shop))
        self.assertEqual(app.handle("GET", "/api/mode"), (200, {"mode": "workspace"}))

    def test_refusals(self):
        self.assertEqual(self.app.handle("POST", "/api/hub/pins", {"id": "acme/api"})[0], 400)
        self.assertEqual(self.app.handle("POST", "/api/hub/pins", {"id": "../x", "pinned": True})[0], 404)
        self.assertEqual(self.app.handle("GET", "/api/state")[0], 404)


class ProjectRoutes(Root):
    """Plan 13.2: the routes of section 11 under /api/p/<company>/<project>/, an App and a Runner per project."""

    def setUp(self):
        super().setUp()
        self.app = server.HubApp(self.hub)

    def test_a_projects_routes_under_its_id(self):
        status, state = self.app.handle("GET", "/api/p/demo/shop/state")
        self.assertEqual((status, state["project"]["name"]), (200, "demo-shop"))
        self.assertEqual(self.app.handle("GET", "/api/p/acme/api/state")[1]["project"]["tracker"], "jira")
        shop = self.app.apps["demo/shop"]
        self.app.handle("GET", "/api/p/demo/shop/state")
        self.assertIs(self.app.apps["demo/shop"], shop)                       # created once, kept
        self.assertEqual(shop.runner.name, "demo/shop")
        self.assertIs(shop.runner.gate, self.hub.gate)
        self.assertEqual(shop.ws, hub.find(self.root, "demo/shop").path)
        self.assertEqual(self.app.handle("GET", "/api/p/demo/shop/runlogs/missing")[0], 404)

    def test_an_id_that_is_not_discovered_is_404(self):
        for path in ("/api/p/../demo/state", "/api/p/demo/empty/state", "/api/p/.hidden/shop/state",
                     "/api/p/demo/shop%2Fdeeper/x/state", "/api/p/demo/shop", "/api/p/%2E%2E/demo/state"):
            self.assertEqual(self.app.handle("GET", path)[0], 404, path)
        self.assertEqual(self.app.apps, {})
        self.assertEqual(self.app.handle("GET", "/api/p/demo/shop/deeper/state")[0], 404)   # no such route

    def test_a_broken_project_yaml_is_said(self):
        status, out = self.app.handle("GET", "/api/p/demo/broken/state")
        self.assertEqual(status, 500)
        self.assertIn("project.yaml", out["error"])                            # said, never a dropped connection

    def test_a_run_in_another_project_is_shown_and_refuses(self):
        self.app.handle("GET", "/api/p/acme/api/state")
        busy = runs.Run("r1", "audit", "accessibility audit", [], "r1.log")
        self.app.apps["acme/api"].runner.runs[busy.id] = busy
        state = self.app.handle("GET", "/api/p/demo/shop/state")[1]
        self.assertIsNone(state["run"])
        self.assertEqual((state["run_elsewhere"]["project"], state["run_elsewhere"]["id"]), ("acme/api", "r1"))
        self.assertIsNone(self.app.handle("GET", "/api/p/acme/api/state")[1]["run_elsewhere"])
        status, out = self.app.handle("POST", "/api/p/demo/shop/runs", {"audit": "a11y"})
        self.assertEqual(status, 409)
        self.assertIn("acme/api", out["error"])
        self.assertEqual(self.app.handle("GET", "/api/hub")[1]["run"]["project"], "acme/api")


class Http(Root):
    def setUp(self):
        super().setUp()
        self.srv, self.url = server.serve_hub(self.hub, 0)
        self.base, self.token = self.url.split("/?token=")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None, token=None):
        headers = {"X-QA-Token": token if token is not None else self.token}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_loopback_the_token_and_the_page(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
        self.assertEqual(self.call("GET", "/api/hub", token="wrong")[0], 403)
        status, state = self.call("GET", "/api/hub")
        self.assertEqual((status, len(state["projects"])), (200, 3))
        self.assertEqual(self.call("POST", "/api/hub/pins", {"id": "demo/shop", "pinned": True})[0], 200)
        with urllib.request.urlopen(self.base + "/", timeout=60) as r:
            self.assertIn('id="screen-hub"', r.read().decode("utf-8"))
        status, state = self.call("GET", "/api/p/demo/shop/state")
        self.assertEqual((status, state["project"]["name"]), (200, "demo-shop"))
        self.assertEqual(self.call("GET", "/api/p/demo/shop/state", token="wrong")[0], 403)
        self.assertEqual(self.call("GET", "/api/p/demo/../acme/api/state")[0], 404)


class CommandLine(unittest.TestCase):
    def run_(self, *args, root):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch.object(hub, "default_root", return_value=root):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_without_a_workspace_the_hub_starts_and_creates_its_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "qa-workspaces")
            with mock.patch.object(server.ThreadingHTTPServer, "serve_forever", side_effect=KeyboardInterrupt), \
                    mock.patch.object(cli.webbrowser, "open") as opened:
                code, out = self.run_("--port", "0", root=root)
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isdir(root))
            self.assertIn("does not exist: it is created empty", out)
            self.assertIn("0 project(s)", out)
            url = re.search(r"Open: (http://127\.0\.0\.1:\d+/\?token=\S{24,})", out).group(1)
            opened.assert_called_once_with(url)

    def test_a_root_that_cannot_be_created_is_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "file")
            with open(blocker, "w") as f:
                f.write("x")
            code, out = self.run_("--no-open", root=os.path.join(blocker, "qa-workspaces"))
            self.assertEqual(code, 2)
            self.assertIn("cannot be created", out)


if __name__ == "__main__":
    unittest.main()

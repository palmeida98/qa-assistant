"""scripts/remote_login.py (plan 11.3; technique-mobile.md §11.2): the login of a remote run through an issue — only
the allowed user's comment with a link of the project's hosts counts; the URL is masked, opened in-process, the
comment deleted, the issue closed; any other comment ignored and reported without its text; no link in time =
exit 3 (Blocked, login). A fake GitHub, a fake device (test_device.FakeDevice) and a fake clock: nothing real.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

import device as dv
import remote_login as rlg
from test_device import APP, HOME, FakeDevice, screen

SLUG = "demo-shop/demo-shop-qa"
LINK = "https://auth.demoshop.example/callback?token=fictional-one-time-77"
LOGIN = screen(("Demo Shop", "", "[0,0][1080,120]"), ("Send me a link", "", "[0,900][1080,1000]"))


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def pause(self, s):
        self.t += s


class FakeGitHub:
    """Issues and comments of one repository; `script` delivers comments when the clock reaches their time."""

    def __init__(self, clock, script=()):
        self.clock, self.script = clock, list(script)
        self.issues, self.comments, self.calls, self.deleted = {}, {}, [], []
        self.next_id = 100

    def __call__(self, method, path, fields=None):
        self.calls.append((method, path, dict(fields or {})))
        prefix = f"repos/{SLUG}/issues"
        assert path.startswith(prefix), path
        rest = path[len(prefix):]
        if method == "GET" and rest.startswith("?"):
            return [i for i in self.issues.values() if i["state"] == "open"]
        if method == "POST" and rest == "":
            number = len(self.issues) + 1
            self.issues[number] = {"number": number, "title": fields["title"], "body": fields["body"],
                                   "state": "open"}
            self.comments[number] = []
            return self.issues[number]
        if method == "GET" and "/comments" in rest:
            number = int(rest.split("/")[1])
            while self.script and self.script[0][0] <= self.clock.t:
                _, login, body = self.script.pop(0)
                self.next_id += 1
                self.comments[number].append({"id": self.next_id, "user": {"login": login, "type":
                                              "Bot" if login.endswith("[bot]") else "User"}, "body": body})
            return list(self.comments[number])
        if method == "POST" and rest.endswith("/comments"):
            self.comments[int(rest.split("/")[1])].append({"id": 0, "user": {"login": "github-actions[bot]",
                                                                             "type": "Bot"}, "body": fields["body"]})
            return {}
        if method == "PATCH":
            self.issues[int(rest.split("/")[1])]["state"] = fields["state"]
            return {}
        if method == "DELETE" and rest.startswith("/comments/"):
            cid = int(rest.split("/")[2])
            self.deleted.append(cid)
            for cs in self.comments.values():
                cs[:] = [c for c in cs if c["id"] != cid]
            return None
        raise AssertionError(f"unexpected {method} {path}")

    def bodies(self, number=1):
        return [c["body"] for c in self.comments[number]]


class RemoteLogin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        self.project()
        env = mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": SLUG, "GITHUB_RUN_ID": "4242"})
        env.start()
        self.addCleanup(env.stop)
        for name in ("GITHUB_ACTIONS", "QA_MOBILE_SERIAL", "QA_MOBILE_INPUT"):
            os.environ.pop(name, None)
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, login="  login:\n    mode: manual-issue\n    flow: flows/_request_link.yaml\n"
                            "    timeout_minutes: 30\n    hosts: [auth.demoshop.example]\n"
                            "    screen_text: Send me a link\n"):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("project: demo-shop\nenvironments: {test: https://shop.demo}\nmodules: [mobile]\n"
                    "mobile: {platform: android, flows: flows, app_id: com.demoshop.app}\n"
                    f"ci:\n{login}")

    def run_(self, github, *args, device=None, helper=(0, "_request_link.yaml: passed")):
        out = io.StringIO()
        with mock.patch.object(rlg, "gh_api", github), mock.patch.object(rlg, "now", self.clock.now), \
                mock.patch.object(rlg, "pause", self.clock.pause), \
                mock.patch.object(rlg.run_mobile, "run_helper", return_value=helper) as run_helper, \
                mock.patch.object(dv, "adb", device or FakeDevice([LOGIN, HOME])), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = rlg.main([self.ws, *args])
        self.helper_calls = run_helper.call_args_list
        return code, out.getvalue()

    # --- request

    def test_request_runs_the_helper_flow_then_opens_the_issue_once(self):
        github = FakeGitHub(self.clock)
        code, out = self.run_(github, "request")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.helper_calls[0].args[1], "flows/_request_link.yaml")
        issue = github.issues[1]
        self.assertEqual(issue["title"], "Login needed — 4242")
        for needle in ("@demo-shop", "as text", "Waiting 30 minutes", "auth.demoshop.example", "deletes the comment",
                       "Blocked (login)"):
            self.assertIn(needle, issue["body"])
        self.assertIn("issue #1: opened", out)
        code, out = self.run_(github, "request")
        self.assertEqual((code, len(github.issues)), (0, 1))
        self.assertIn("already open, reused", out)

    def test_the_helper_flow_failing_is_blocked_without_an_issue(self):
        github = FakeGitHub(self.clock)
        code, out = self.run_(github, "request", helper=(3, "_request_link.yaml failed (Maestro exit 1): x"))
        self.assertEqual(code, 3)
        self.assertIn("BLOCKED (login)", out)
        self.assertEqual(github.issues, {})

    # --- wait

    def requested(self, script):
        github = FakeGitHub(self.clock, script)
        self.assertEqual(self.run_(github, "request")[0], 0)
        return github

    def test_only_the_owners_link_is_used_masked_opened_deleted_and_the_issue_closed(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        github = self.requested([
            (15, "someone-else", f"here: {LINK}"),                     # not the owner: ignored, never opened
            (15, "github-actions[bot]", "a bot"),
            (30, "demo-shop", "sorry, one moment"),                     # the owner, no link
            (30, "demo-shop", "see https://evil.example/callback?token=x"),   # a host not of the project
            (45, "demo-shop", f"Here it is: [link]({LINK})."),
        ])
        device = FakeDevice([LOGIN, HOME])
        code, out = self.run_(github, "wait", device=device)
        self.assertEqual(code, 0, out)
        am = device.of("shell", "am", "start")
        self.assertEqual(len(am), 1)
        self.assertEqual(am[0][am[0].index("-d") + 1], "'" + LINK + "'")
        lines_with_link = [ln for ln in out.splitlines() if "fictional-one-time-77" in ln]
        self.assertEqual(lines_with_link, [f"::add-mask::{LINK}"])      # only the mask command
        self.assertIn("ignored: a comment by @someone-else (only @demo-shop may paste the link)", out)
        self.assertIn("without a link of", out)
        self.assertNotIn("sorry, one moment", out)                      # a comment's text is never printed
        self.assertNotIn("evil.example", out)
        self.assertEqual(len(github.deleted), 1)                        # only the comment with the link
        self.assertFalse(any("fictional-one-time-77" in b for b in github.bodies() if "someone-else" not in b
                             and "here:" not in b))
        self.assertEqual(github.issues[1]["state"], "closed")
        self.assertIn("login screen: left", out)
        self.assertFalse(any("fictional-one-time-77" in str(c) for c in github.calls))   # never sent to GitHub

    def test_outside_actions_the_link_is_never_printed(self):
        github = self.requested([(0, "demo-shop", LINK)])
        code, out = self.run_(github, "wait")
        self.assertEqual(code, 0, out)
        self.assertNotIn("fictional-one-time-77", out)

    def test_no_link_in_time_is_blocked_and_the_issue_says_so(self):
        github = self.requested([(10, "someone-else", LINK)])
        code, out = self.run_(github, "wait")
        self.assertEqual(code, 3)
        self.assertIn("BLOCKED (login): no link by @demo-shop in 30 minutes", out)
        self.assertGreaterEqual(self.clock.t, 30 * 60)
        self.assertEqual(github.issues[1]["state"], "closed")
        self.assertIn("No link received in 30 minutes", github.bodies()[-1])
        self.assertEqual(github.deleted, [])

    def test_still_on_the_login_screen_is_blocked(self):
        github = self.requested([(0, "demo-shop", LINK)])
        code, out = self.run_(github, "wait", device=FakeDevice([LOGIN]))
        self.assertEqual(code, 3)
        self.assertIn("still shows the login screen", out)
        self.assertEqual(len(github.deleted), 1)

    def test_a_link_not_opened_is_blocked_and_the_comment_still_deleted(self):
        github = self.requested([(0, "demo-shop", LINK)])
        device = FakeDevice(focus=APP, devices="")                      # no device connected
        code, out = self.run_(github, "wait", device=device)
        self.assertEqual(code, 3)
        self.assertEqual(len(github.deleted), 1)
        self.assertEqual(github.issues[1]["state"], "closed")
        self.assertNotIn("fictional-one-time-77", out)

    def test_wait_without_the_issue_and_mode_none(self):
        code, out = self.run_(FakeGitHub(self.clock), "wait")
        self.assertEqual(code, 2)
        self.assertIn("run `request` first", out)
        self.project(login="  login: {mode: none}\n")
        github = FakeGitHub(self.clock)
        self.assertEqual(self.run_(github, "request")[0], 0)
        self.assertEqual(github.calls, [])

    def test_ci_login_user_and_the_screen_check_off(self):
        self.project(login="  login: {mode: manual-issue, flow: flows/_r.yaml, user: demo-tester}\n")
        github = self.requested([(0, "demo-shop", LINK), (0, "demo-tester", "https://shop.demo/magic?t=1")])
        code, out = self.run_(github, "wait")
        self.assertEqual(code, 0, out)
        self.assertIn("ignored: a comment by @demo-shop", out)
        self.assertIn("shop.demo: opened", out)
        self.assertIn("not checked", out)

    def test_on_a_runner_the_emulator_and_adb_replace_the_phone_and_the_driver(self):
        """Plan 11.8: project.yaml names a physical phone with the companion driver; the runner's QA_MOBILE_SERIAL /
        QA_MOBILE_INPUT send the link to its emulator, through adb (the helper flow: test_run_mobile)."""
        path = os.path.join(self.ws, "project.yaml")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace("app_id: com.demoshop.app}", "app_id: com.demoshop.app, serial: R58M, input: driver}"))
        github = self.requested([(0, "demo-shop", LINK)])
        device = FakeDevice([LOGIN, HOME])                              # only emulator-5554 connected
        code, out = self.run_(github, "wait", device=device)
        self.assertEqual(code, 3, out)                                  # the phone is not there
        github = self.requested([(0, "demo-shop", LINK)])
        device = FakeDevice([LOGIN, HOME])
        with mock.patch.dict(os.environ, {"QA_MOBILE_SERIAL": "emulator-5554", "QA_MOBILE_INPUT": "adb"}):
            code, out = self.run_(github, "wait", device=device)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(device.of("shell", "am", "start")), 1)
        self.assertEqual(device.of("shell", "content", "call"), [])     # never the driver

    def test_link_in(self):
        hosts = ["auth.demoshop.example", "shop.demo"]
        for body, found in [(f"<{LINK}>", LINK), (f"{LINK}.", LINK), (f"[a]({LINK}) and more", LINK),
                            ("https://login.shop.demo/x?t=1", "https://login.shop.demo/x?t=1"),   # a subdomain
                            ("https://shop.demo.evil.example/x", None), ("https://evilshop.demo/x", None),
                            ("no link at all", None), ("ftp://shop.demo/x", None)]:
            with self.subTest(body=body):
                self.assertEqual(rlg.link_in(body, hosts), found)


if __name__ == "__main__":
    unittest.main()

"""scripts/validate_project.py (methodology 4.1): closed list of modules, section
of each active module, Excel and dashboard. The demo must pass.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import typing
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import validate_project as vp
import workspace_git as wg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_workspace_git import FakeGh, isolate_git, restore_git


def setUpModule():
    # git exports its hook variables (GIT_DIR...); committing from a linked worktree they are absolute paths, and the
    # `git -C <tmp>` of the script under test read this repository. They are dropped, and git gets a temporary home
    # and global configuration (never the user's)
    isolate_git()


def tearDownModule():
    restore_git()

BASE = {"project": "demo-shop", "company": "demo", "environments": {"test": "https://shop.demo"},
        "tracker": "azure-devops", "bug_format": "SHOP-{n}", "resolved_bugs": "remove", "evidence": "text",
        "modules": ["functional-web"]}
MOBILE = {"platform": "android", "flows": "flows", "app_id": "com.demoshop.app"}


class Validate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # history/ protected, as the onboarding leaves it (docs/workspace/settings.json)
        os.makedirs(os.path.join(self.tmp.name, ".claude"))
        shutil.copy(os.path.join(ROOT, "docs", "workspace", "settings.json"), os.path.join(self.tmp.name, ".claude"))
        self.gh = FakeGh()                                              # gh is never the real one
        patcher = mock.patch.object(wg, "gh", self.gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def with_(self, **changes):
        proj = {**BASE, **changes}
        with open(os.path.join(self.tmp.name, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f, allow_unicode=True)
        return vp.validate(self.tmp.name)

    def test_base_is_valid(self):
        self.assertEqual(self.without_tracker_warning(), ([], []))

    def without_tracker_warning(self, **changes):
        """validate() without the warning of a tracker with no adapter (the base uses azure-devops, whose back-end
        is plan 10.3: no section needed yet, no workbook)."""
        problems, warnings = self.with_(**changes)
        return problems, [w for w in warnings if "has no adapter" not in w]

    def test_a_tracker_without_an_adapter_is_a_warning(self):
        problems, warnings = self.with_(tracker="azure-devops")
        self.assertEqual(problems, [])
        self.assertTrue(any("tracker 'azure-devops' has no adapter" in w for w in warnings), warnings)

    def test_jira_has_its_adapter_and_needs_its_section(self):
        # plan 10.2: the Jira back-end is done, so the section is required and there is no "no adapter" warning
        from tracker_kit import JIRA
        problems, warnings = self.with_(tracker="jira")
        self.assertTrue(any("without the jira: section" in p for p in problems), problems)
        self.assertFalse(any("has no adapter" in w for w in warnings), warnings)
        problems, warnings = self.with_(tracker="jira", jira=JIRA)
        self.assertEqual(problems, [])
        self.assertFalse(any("has no adapter" in w for w in warnings), warnings)

    def test_the_tracker_section_is_checked(self):
        # plan 10.1: the jira: / azure_devops: section, through adapters/tracker/config.py (fictional Demo Shop)
        from tracker_kit import JIRA
        not_a_variable_name = "not a name"
        jira = {**JIRA, "site": "http://demo-shop.atlassian.net", "token_var": not_a_variable_name,
                "results": {"statuses": {"Pass": "PASSED", "Question": "TODO", "Blocked": "skip"}}}
        problems, warnings = self.with_(tracker="jira", jira=jira)
        text = " | ".join(problems)
        for part in ("jira.site", "jira.token_var", "Question"):
            self.assertIn(part, text)
        self.assertFalse(any("has no adapter" in w for w in warnings), warnings)
        self.assertTrue(any("not sent" in w and "Blocked" in w for w in warnings), warnings)
        azure = {"organization_url": "https://dev.azure.com/demo-shop", "project": "Demo Shop",
                 "pat_var": "DS_ADO_PAT", "cases": {"plan_id": 12},
                 "results": {"outcomes": {"Pass": "Passed", "Fail": "Timeout"}}}
        problems, warnings = self.with_(tracker="azure-devops", azure_devops=azure)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("Timeout", problems[0])
        self.assertTrue(any("10.3" in w for w in warnings), warnings)

    def test_a_module_outside_the_list(self):
        problems, _ = self.with_(modules=["functional-web", "smoke"])
        self.assertTrue(any("outside the closed list" in p and "smoke" in p for p in problems), problems)

    def test_an_active_module_without_its_section(self):
        problems, _ = self.with_(modules=["api", "mobile"], mobile={"platform": "web"})
        self.assertTrue(any("'api'" in p and "base_url" in p for p in problems), problems)
        self.assertTrue(any("'mobile'" in p and "flows" in p for p in problems), problems)

    def test_a_section_without_its_module_is_a_warning(self):
        problems, warnings = self.with_(api={"base_url": "https://api.shop.demo"})
        self.assertEqual(problems, [])
        self.assertTrue(any("section api:" in w for w in warnings), warnings)

    def test_base_fields(self):
        problems, _ = self.with_(project="", tracker="trello", evidence="video", environments={})
        text = " | ".join(problems)
        for fragment in ("'project' is empty", "tracker 'trello'", "evidence", "'environments' is empty"):
            self.assertIn(fragment, text)

    def test_old_values_are_problems(self):
        # a project.yaml written by hand with the values from before the translation (plan 8.11)
        problems, _ = self.with_(resolved_bugs="remover", evidence="texto", modules=["funcional-web"])
        text = " | ".join(problems)
        self.assertIn("resolved_bugs", text)
        self.assertIn("evidence", text)
        self.assertIn("funcional-web", text)

    def test_an_environment_without_a_url_or_badly_written(self):
        problems, _ = self.with_(environments={"test": "", "uat": "shop.demo"})
        text = " | ".join(problems)
        self.assertIn("environment 'test' without a URL", text)
        self.assertIn("environment 'uat'", text)

    def test_module_urls_inside_the_environments(self):
        a11y = {"pages": [{"name": "home", "url": "https://shop.demo/"},
                          {"name": "account", "url": "https://shop.demo.other.pt/account"}]}
        problems, _ = self.with_(modules=["api", "accessibility"], accessibility=a11y,
                                 api={"base_url": "https://api.shop.demo/v1"})
        text = " | ".join(problems)
        self.assertIn("api.base_url (https://api.shop.demo/v1) outside the environments", text)
        self.assertIn("accessibility.pages[account]", text)  # a similar host does not count
        self.assertNotIn("pages[home]", text)

    def test_a_production_url_is_a_problem(self):
        problems, _ = self.with_(modules=["api"], api={"base_url": "https://shop.pt/api"},
                                 environments={"test": "https://test.shop.pt", "production": "https://shop.pt"})
        self.assertTrue(any("'production'" in p for p in problems), problems)

    def test_a_url_with_the_environments_path(self):
        problems, _ = self.with_(modules=["api"], environments={"test": "https://shop.demo/app/"},
                                 api={"base_url": "https://shop.demo/app/api"})
        self.assertEqual(problems, [])
        problems, _ = self.with_(modules=["api"], environments={"test": "https://shop.demo/app/"},
                                 api={"base_url": "https://shop.demo/application"})
        self.assertTrue(problems)

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_gitignore_in_a_workspace_with_git(self):
        subprocess.run(["git", "init", "-q", self.tmp.name], check=True)
        _, warnings = self.with_(modules=["api"], api={"base_url": "https://shop.demo/api"})
        self.assertTrue(any(w.startswith(".env is not in the workspace") for w in warnings), warnings)
        self.assertTrue(any(".api-session.env" in w for w in warnings), warnings)
        with open(os.path.join(self.tmp.name, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*.env\n")
        _, warnings = self.without_tracker_warning(modules=["api"], api={"base_url": "https://shop.demo/api"})
        self.assertEqual([w for w in warnings if "has no remote" not in w], [])

    def git(self, *args):
        return subprocess.run(["git", "-C", self.tmp.name, *args], capture_output=True, text=True, check=True).stdout

    def committed_workspace(self, **changes):
        """A workspace that is a repository of its own, with the template's .gitignore and one commit."""
        self.with_(**changes)
        shutil.copy(os.path.join(ROOT, "docs", "workspace", "gitignore"), os.path.join(self.tmp.name, ".gitignore"))
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "workspace: initial commit")

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_portable_workspace_warnings(self):
        api = {"base_url": "https://shop.demo/api", "credentials": {"admin": {"user_var": "SHOP_ADMIN_USER",
                                                                             "pass_var": "SHOP_ADMIN_PASSWORD"}}}
        self.committed_workspace(modules=["api"], api=api)
        problems, warnings = self.without_tracker_warning(modules=["api"], api=api)
        self.assertEqual(problems, [])
        text = " | ".join(warnings)
        self.assertIn("has no remote", text)
        self.assertIn(".env.example (it does not exist) misses variables of project.yaml: SHOP_ADMIN_PASSWORD, "
                      "SHOP_ADMIN_USER", text)
        with open(os.path.join(self.tmp.name, ".env.example"), "w", encoding="utf-8") as f:
            f.write("SHOP_ADMIN_USER=\n")
        bare = os.path.join(self.tmp.name, "..", os.path.basename(self.tmp.name) + "-remote.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        self.addCleanup(shutil.rmtree, bare, True)
        self.git("remote", "add", "origin", bare)
        _, warnings = self.without_tracker_warning(modules=["api"], api=api)
        text = " | ".join(warnings)
        self.assertNotIn("has no remote", text)
        self.assertIn(".env.example misses variables of project.yaml: SHOP_ADMIN_PASSWORD (", text)
        self.assertIn("1 commit(s) not pushed", text)
        self.assertNotIn("visibility", text)                            # a local path: gh is not asked
        self.git("push", "-q", "-u", "origin", "HEAD")
        real = wg.file_size
        with mock.patch.object(wg, "file_size", lambda p: wg.LIMIT + 1 if p.endswith("project.yaml") else real(p)):
            _, warnings = self.without_tracker_warning(modules=["api"], api=api)
        text = " | ".join(warnings)
        self.assertNotIn("not pushed", text)
        self.assertIn("tracked files over 50 MB: project.yaml", text)

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_env_tracked_or_a_public_remote_are_problems(self):
        self.committed_workspace()
        with open(os.path.join(self.tmp.name, ".env"), "w", encoding="utf-8") as f:
            f.write("SHOP_ADMIN_USER=\n")
        self.git("add", "-f", ".env")
        problems, _ = self.with_()
        self.assertTrue(any(p.startswith(".env is tracked by git") for p in problems), problems)
        self.git("rm", "-q", "--cached", ".env")
        self.git("remote", "add", "origin", "https://github.com/demo-user/demo-shop.git")
        for visibility, problem, warning in [("PUBLIC", "is PUBLIC", None), ("PRIVATE", None, None),
                                             ("INTERNAL", None, "is INTERNAL"), (None, None, None)]:
            with self.subTest(visibility=visibility), mock.patch.object(wg, "gh", FakeGh(visibility)):
                problems, warnings = self.with_()
                self.assertEqual([p for p in problems if "PUBLIC" in p] != [], problem is not None, problems)
                self.assertEqual(any("INTERNAL" in w for w in warnings), warning is not None, warnings)
        with mock.patch.object(wg, "gh", FakeGh("PRIVATE", fail=True)):
            _, warnings = self.with_()
        self.assertTrue(any("could not be confirmed" in w for w in warnings), warnings)

    @unittest.skipUnless(shutil.which("git"), "no git")
    def test_a_folder_inside_another_repository_only_checks_env(self):
        # e.g. qa-assistant's demo: not a repository of its own, so no remote or push warnings
        self.git("init", "-q")
        sub = os.path.join(self.tmp.name, "demo")
        os.makedirs(sub)
        with open(os.path.join(sub, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({**BASE, "mobile": {"variables": ["SHOP_APP_USER"]}}, f)
        _, warnings = vp.validate(sub)
        self.assertFalse(any("remote" in w or ".env.example" in w for w in warnings), warnings)

    def test_history_without_protection_is_a_warning(self):
        settings = os.path.join(self.tmp.name, ".claude", "settings.json")
        for content in (None, "{}", '{"permissions": {"deny": ["Write(/history/**)"]}}',
                        '{"permissions": {"deny": ["Edit(/historico/**)"]}}', "not json"):
            with self.subTest(content=content):
                if content is None:
                    os.remove(settings)
                else:
                    with open(settings, "w", encoding="utf-8") as f:
                        f.write(content)
                problems, warnings = self.with_()
                self.assertEqual(problems, [])
                self.assertTrue(any("history/ is not protected" in w for w in warnings), warnings)

    def test_the_workspaces_mcp(self):
        # lessons of the pilot (25.09.2026): fixed version, --secrets .env and --isolated
        mcp = os.path.join(self.tmp.name, ".mcp.json")
        cases = {
            '["/c", "npx", "@playwright/mcp@0.0.82", "--secrets", ".env", "--isolated"]': [],
            '["/c", "npx", "@playwright/mcp@latest", "--secrets", ".env", "--isolated"]': ["without a fixed version"],
            '["/c", "npx", "@playwright/mcp@0.0.82", "--isolated"]': ["--secrets .env is missing"],
            '["/c", "npx", "@playwright/mcp@0.0.82", "--secrets", ".env"]': ["--isolated is missing"],
        }
        for args, expected in cases.items():
            with self.subTest(args=args):
                with open(mcp, "w", encoding="utf-8") as f:
                    f.write('{"mcpServers": {"playwright": {"command": "cmd", "args": ' + args + '}}}')
                warnings = vp.mcp_warnings(self.tmp.name)
                self.assertEqual(len(warnings), len(expected), warnings)
                for text in expected:
                    self.assertTrue(any(text in a for a in warnings), warnings)
        os.remove(mcp)
        self.assertEqual(vp.mcp_warnings(self.tmp.name), [])          # no .mcp.json: nothing to check

    def test_history_protected_with_other_rules(self):
        with open(os.path.join(self.tmp.name, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            f.write('{"permissions": {"deny": ["Bash(git push *)", "Edit(history/**)"]}}')
        self.assertEqual(self.without_tracker_warning(), ([], []))

    def test_the_device_keys_of_mobile(self):
        """The keys scripts/device.py reads (plan 9.3): accepted when well written, refused otherwise."""
        good = {"serial": "emulator-5554", "send_label": ["Send", "Send message"], "ignore_labels": ["New chat"],
                "evidence_batch": "20260928", "browser": "com.android.chrome"}
        self.assertEqual(self.without_tracker_warning(modules=["mobile"], mobile={**MOBILE, **good}), ([], []))
        problems, _ = self.with_(modules=["mobile"], mobile={**MOBILE, "send_label": "Send"})
        self.assertEqual(problems, [])                                  # one label as a text is also valid
        for key, value in [("serial", 5554), ("serial", "emulator 5554"), ("send_label", []),
                           ("send_label", ["Send", 3]), ("ignore_labels", "New chat"), ("ignore_labels", [""]),
                           ("evidence_batch", 20260928), ("evidence_batch", "../other"),
                           ("browser", "chrome"), ("browser", "com.android.chrome/.Main")]:
            problems, _ = self.with_(modules=["mobile"], mobile={**MOBILE, key: value})
            self.assertTrue(any(f"mobile.{key}" in p for p in problems), (key, value, problems))


REAL = {"skills_state": vp.skills_state, "playwright_browsers": vp.playwright_browsers}


class Environment(unittest.TestCase):
    """--environment (methodology 12.3; plan 9.5): every lookup is faked; nothing of this computer is read."""

    ALL: typing.ClassVar[dict] = {"git": "/bin/git", "node": "/bin/node", "maestro": "/bin/maestro",
                                  "xcrun": "/usr/bin/xcrun"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = self.tmp.name
        self.found = dict(self.ALL)
        self.android = {"adb": "/sdk/platform-tools/adb", "emulator": "/sdk/emulator/emulator"}
        self.skills = {"checkpoint": "same", "run-batch": "same"}
        for name, fake in [("which", lambda n: self.found.get(n)), ("python_version", lambda: (3, 12, 1)),
                           ("soffice", lambda: self.found.get("soffice", "/opt/soffice")),
                           ("android_tools", lambda: dict(self.android)), ("qa_audit_installed", lambda: True),
                           ("playwright_browsers", lambda: {"chromium"}), ("skills_state", lambda: self.skills)]:
            patcher = mock.patch.object(vp, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def project(self, **changes):
        proj = {**BASE, **changes}
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f)
        return proj

    def checks(self, **changes):
        return {tool: (ok, detail) for ok, tool, detail in vp.environment(self.ws, self.project(**changes))}

    def write(self, name, text):
        with open(os.path.join(self.ws, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_always_git_python_env_and_skills(self):
        c = self.checks()
        self.assertEqual(set(c), {"git", "python", ".env", "skills", "node"})   # base: functional-web
        self.assertTrue(all(ok for ok, _ in c.values()), c)
        self.found.pop("git")
        self.assertFalse(self.checks()["git"][0])
        with mock.patch.object(vp, "python_version", lambda: (3, 11, 9)):
            ok, detail = self.checks()["python"]
        self.assertFalse(ok)
        self.assertIn(">= 3.12", detail)

    def test_env_missing_a_name_is_reported_without_any_value(self):
        creds = {"admin": {"user_var": "SHOP_ADMIN_USER", "pass_var": "SHOP_ADMIN_PASSWORD"}}
        self.write(".env.example", "SHOP_ADMIN_USER=\nSHOP_ADMIN_PASSWORD=\nSHOP_EXTRA_TOKEN=\n")
        ok, detail = self.checks(api={"base_url": "https://shop.demo", "credentials": creds})[".env"]
        self.assertFalse(ok)
        self.assertIn("copy .env.example", detail)
        value = "never shown"
        self.write(".env", f"SHOP_ADMIN_USER={value}\nSHOP_EXTRA_TOKEN=\n")
        ok, detail = self.checks(api={"base_url": "https://shop.demo", "credentials": creds})[".env"]
        self.assertFalse(ok)
        self.assertIn("misses SHOP_ADMIN_PASSWORD", detail)
        self.assertIn("without a value: SHOP_EXTRA_TOKEN", detail)
        self.assertNotIn(value, detail)
        self.write(".env", "SHOP_ADMIN_USER=u1\nSHOP_ADMIN_PASSWORD=p1\nSHOP_EXTRA_TOKEN=t1\n")
        ok, detail = self.checks(api={"base_url": "https://shop.demo", "credentials": creds})[".env"]
        self.assertTrue(ok, detail)
        for value in ("u1", "p1", "t1"):
            self.assertNotIn(f"={value}", detail)

    def test_skills_out_of_sync_are_reported(self):
        self.skills = {"checkpoint": "different", "run-batch": "missing", "log-bug": "same"}
        ok, detail = self.checks()["skills"]
        self.assertFalse(ok)
        self.assertIn("checkpoint, run-batch", detail)
        self.assertIn("sync_skills.py --apply", detail)

    def test_skills_state_is_sync_skills_comparison_read_only(self):
        import sync_skills
        with tempfile.TemporaryDirectory() as target, mock.patch.object(sync_skills, "TARGET", target):
            state = REAL["skills_state"]()      # the real one, on an empty global copy
            self.assertEqual(os.listdir(target), [])
        self.assertTrue(state and set(state.values()) == {"missing"}, state)

    def test_excel_needs_libreoffice_unless_excel_com(self):
        self.assertTrue(self.checks(tracker="excel")["libreoffice"][0])
        self.found["soffice"] = None
        ok, detail = self.checks(tracker="excel")["libreoffice"]
        self.assertFalse(ok)
        self.assertIn("LibreOffice", detail)
        c = self.checks(tracker="excel", excel={"recalc": {"mechanism": "excel-com"}})
        self.assertNotIn("libreoffice", c)
        self.assertTrue(c["recalculation"][0])
        self.assertNotIn("libreoffice", self.checks(tracker="jira"))

    def test_mobile_android(self):
        mobile = {**MOBILE, "build": {"source": "the team's build share", "sha256": "a" * 64}}
        c = self.checks(modules=["mobile"], mobile=mobile)
        for tool in ("maestro", "adb", "emulator", "build"):
            self.assertTrue(c[tool][0], (tool, c))
        self.assertIn("sha256", c["build"][1])
        self.android = {"adb": None, "emulator": None}
        self.found.pop("maestro")
        c = self.checks(modules=["mobile"], mobile=MOBILE)
        for tool in ("maestro", "adb", "emulator", "build"):
            self.assertFalse(c[tool][0], (tool, c))
        self.assertIn("setup_emulator.py", c["adb"][1])
        self.assertIn("mobile.build.source", c["build"][1])
        # a real device: no emulator needed
        c = self.checks(modules=["mobile"], mobile={**MOBILE, "serial": "R58M123ABC"})
        self.assertTrue(c["emulator"][0])

    def test_mobile_web_and_ios(self):
        c = self.checks(modules=["mobile"], mobile={"platform": "web", "flows": "flows"})
        self.assertEqual({t for t in c if t in ("maestro", "adb", "emulator", "build", "xcrun")}, {"maestro"})
        self.found.pop("xcrun")
        c = self.checks(modules=["mobile"], mobile={**MOBILE, "platform": "ios"})
        self.assertFalse(c["xcrun"][0])
        self.assertNotIn("adb", c)

    def test_accessibility_needs_qa_audit_and_its_browser(self):
        a11y = {"pages": [{"name": "home", "url": "https://shop.demo/"}]}
        c = self.checks(modules=["accessibility"], accessibility=a11y)
        self.assertTrue(c["qa-audit"][0] and c["browser chromium"][0], c)
        ok, detail = self.checks(modules=["accessibility"], accessibility={**a11y, "browser": "firefox"})[
            "browser firefox"]
        self.assertFalse(ok)
        self.assertIn("playwright install firefox", detail)
        with mock.patch.object(vp, "qa_audit_installed", lambda: False):
            c = self.checks(modules=["accessibility"], accessibility=a11y)
        self.assertFalse(c["qa-audit"][0])
        self.assertIn("pip install -e .", c["qa-audit"][1])
        self.assertFalse(c["browser chromium"][0])

    def test_playwright_browsers_reads_the_doctors_folders(self):
        import importlib.util as iu
        if iu.find_spec("qa_audit") is None:
            self.skipTest("qa-audit is not installed")
        folder = os.path.join(self.ws, "pw")
        os.makedirs(os.path.join(folder, "chromium_headless_shell-1"))
        os.makedirs(os.path.join(folder, "webkit-2"))
        with mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": folder}):
            self.assertEqual(REAL["playwright_browsers"](), {"chromium", "webkit"})

    def test_api_needs_nothing_beyond_python(self):
        c = self.checks(modules=["api"], api={"base_url": "https://shop.demo"})
        self.assertEqual(set(c), {"git", "python", ".env", "skills"})

    def run_main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = vp.main([self.ws, *args])
        return rc, out.getvalue()

    def test_missing_tools_are_warnings_unless_strict(self):
        os.makedirs(os.path.join(self.ws, ".claude"))
        shutil.copy(os.path.join(ROOT, "docs", "workspace", "settings.json"), os.path.join(self.ws, ".claude"))
        self.project(modules=["mobile"], mobile={**MOBILE, "build": {"source": "share"}})
        self.found.pop("maestro")
        rc, out = self.run_main("--environment")
        self.assertEqual(rc, 0, out)
        self.assertIn("[missing] maestro: ", out)
        self.assertIn("[ok] adb: ", out)
        self.assertIn("environment: 1 missing", out)
        rc, out = self.run_main("--environment", "--strict")
        self.assertEqual(rc, 1, out)
        self.found["maestro"] = "/bin/maestro"
        self.assertEqual(self.run_main("--environment", "--strict")[0], 0)
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertNotIn("[ok]", out)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            vp.main([self.ws, "--strict"])
        self.assertEqual(e.exception.code, 2)


class Demo(unittest.TestCase):
    def test_the_demo_passes(self):
        # a copy of the demo's project.yaml and workbook (the original is only read)
        tmp = tempfile.mkdtemp()
        try:
            demo = os.path.join(ROOT, "examples", "demo")
            shutil.copy(os.path.join(demo, "project.yaml"), tmp)
            shutil.copytree(os.path.join(demo, ".claude"), os.path.join(tmp, ".claude"))
            os.makedirs(os.path.join(tmp, "workbook"))
            shutil.copy(os.path.join(demo, "workbook", "demo-plan.xlsx"), os.path.join(tmp, "workbook"))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = vp.main([tmp])
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("0 problem(s), 0 warning(s)", out.getvalue())
            # --environment reads this computer (read only) and, without --strict, never changes the exit code
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = vp.main([tmp, "--environment"])
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("environment: ", out.getvalue())
        finally:
            shutil.rmtree(tmp)

    def test_without_a_project_exits_with_2(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(vp.main([tmp]), 2)

    def test_a_workspace_from_before_the_translation_says_how_to_convert_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "projeto.yaml"), "w", encoding="utf-8") as f:
                f.write("projeto: x\n")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(vp.main([tmp]), 2)
        self.assertIn("migrate_workspace.py", err.getvalue())


if __name__ == "__main__":
    unittest.main()

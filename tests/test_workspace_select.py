"""Finding the framework and selecting a project (docs/technique-workspaces.md; plan phase 15): two fictional projects, an
old checkout without the module, a real git worktree. No test reads a real workspace, the real ~/.claude or the real
~/.qa-assistant.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import api_request
import common
import run_log
import run_mobile
import sync_skills
import workspace_select as ws
from qa_ui_kit import SECRET
from tickets_fake import ORG, FakeAdo

from adapters.tickets import cli as tickets_cli
from adapters.tracker.errors import ConfigError
from adapters.tracker.record import safe_path as tracker_safe_path

VALID = {"project": "Demo Shop", "company": "demo", "tracker": "excel", "bug_format": "BUG-{n}",
         "environments": {"test": "https://shop.example/"}, "modules": ["functional-web"]}
BOARDS = {"service": "azure-devops", "access": "api", "organization_url": ORG, "project": "Demo Shop", "pat_var": "DS_PAT"}
GIT_HOOK_VARIABLES = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR")
_env = mock.patch.dict(os.environ)


def setUpModule():
    # Inside a commit's hook git sets these: with them, a `git init` here would reach the repository being committed.
    _env.start()
    for k in GIT_HOOK_VARIABLES:
        os.environ.pop(k, None)


def tearDownModule():
    _env.stop()


def make_project(root, pid, env=None, **fields):
    path = os.path.join(root, *pid.split("/"))
    os.makedirs(path)
    with open(os.path.join(path, "project.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({**VALID, **fields}, f)
    if env:
        with open(os.path.join(path, ".env"), "w", encoding="utf-8") as f:
            f.write(env)
    return path


def make_link(target, link) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(target, link)
        return True
    return False


def tree(path) -> dict:
    """Every file under a folder, with a hash of its bytes: what proves that nothing was written."""
    out = {}
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            with open(full, "rb") as f:
                out[os.path.relpath(full, path)] = hashlib.sha256(f.read()).hexdigest()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.tmp, "any-name-for-the-root")
        os.makedirs(self.root)
        self.neutral = os.path.join(self.tmp, "elsewhere")
        os.makedirs(self.neutral)
        self.target = os.path.join(self.tmp, "global-skills")
        self.config = os.path.join(self.tmp, "home", "config.json")
        self.environ = {}

    def resolve(self, hint=None, **kw):
        kw.setdefault("cwd", self.neutral)
        kw.setdefault("root", self.root)
        return ws.resolve(hint, environ=self.environ, config_path=self.config, **kw)

    def preflight(self, skill, hint=None, **kw):
        kw.setdefault("cwd", self.neutral)
        kw.setdefault("root", self.root)
        kw.setdefault("target", self.target)
        return ws.preflight(skill, hint, environ=self.environ, config_path=self.config, **kw)


class Discovery(Base):
    def test_both_layouts_and_every_status(self):
        make_project(self.root, "demo/shop")
        make_project(self.root, "acme/api", project="Acme API")
        make_project(self.root, "solo")
        os.makedirs(os.path.join(self.root, "demo", "new"))
        os.makedirs(os.path.join(self.root, "empty"))
        broken = os.path.join(self.root, "demo", "broken")
        os.makedirs(broken)
        with open(os.path.join(broken, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("project: [unclosed\n")
        make_project(self.root, "demo/half", bug_format="")
        found = {c.id: c for c in ws.discover(self.root)}
        self.assertEqual({k: c.status for k, c in found.items()},
                         {"demo/shop": "valid", "acme/api": "valid", "solo": "valid", "demo/new": "not-configured",
                          "empty": "not-configured", "demo/broken": "incomplete", "demo/half": "incomplete"})
        self.assertEqual(found["acme/api"].name, "Acme API")
        self.assertEqual(found["demo/half"].reason, "1 problem(s) in project.yaml")
        self.assertEqual(found["demo/new"].public()["proposes"], "/project-onboarding")
        self.assertIsNone(found["demo/shop"].public()["proposes"])

    def test_a_folder_with_its_own_project_yaml_is_never_a_company(self):
        make_project(self.root, "solo")
        make_project(self.root, "solo/inner")
        self.assertEqual([c.id for c in ws.discover(self.root)], ["solo"])

    def test_what_is_not_a_project_is_ignored(self):
        make_project(self.root, "demo/shop")
        make_project(self.root, "demo/shop/deeper")
        make_project(self.root, ".hidden/shop")
        make_project(self.root, "_scratch/shop")
        with open(os.path.join(self.root, "notes.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        self.assertEqual([c.id for c in ws.discover(self.root)], ["demo/shop"])

    def test_any_folder_name_is_a_project_id_name_and_path_kept_apart(self):
        # audit finding D-01: the rule on names came only from building the id; nothing needs it
        make_project(self.root, "Acme Corp/Web Shop", project="Loja Web")
        make_project(self.root, "demo/Loja #1 é")
        make_project(self.root, "Café")
        found = {c.id: c for c in ws.discover(self.root)}
        self.assertEqual(sorted(found), ["Acme Corp/Web Shop", "Café", "demo/Loja #1 é"])
        web = found["Acme Corp/Web Shop"]
        self.assertEqual((web.status, web.configured, web.name, web.company, web.folder),
                         ("valid", True, "Loja Web", "Acme Corp", "Web Shop"))
        self.assertEqual(web.path, os.path.join(self.root, "Acme Corp", "Web Shop"))

    def test_a_folder_with_spaces_is_selected_by_id_name_path_and_directory(self):
        web = make_project(self.root, "Acme Corp/Web Shop", project="Loja Web")
        for selection in (self.resolve("Acme Corp/Web Shop"), self.resolve("Web Shop"), self.resolve("loja web"),
                          self.resolve(workspace=web), self.resolve(cwd=web)):
            self.assertEqual((selection.outcome, selection.candidate.id), ("selected", "Acme Corp/Web Shop"))
        code, report = self.preflight("analyze-feature", "Web Shop")
        self.assertEqual(code, 0, report["problems"])

    def test_a_link_is_listed_as_inaccessible_and_never_followed(self):
        outside = make_project(self.tmp, "outside/secret", env=f"X={SECRET}\n")
        os.makedirs(os.path.join(self.root, "demo"))
        if not (make_link(outside, os.path.join(self.root, "demo", "linked"))
                and make_link(outside, os.path.join(self.root, "linked"))):
            self.skipTest("links cannot be created here")
        make_project(self.root, "demo/shop")
        found = {c.id: c.status for c in ws.discover(self.root)}
        self.assertEqual(found, {"demo/linked": "inaccessible", "linked": "inaccessible", "demo/shop": "valid"})

    def test_a_folder_that_cannot_be_read_does_not_stop_the_list(self):
        make_project(self.root, "demo/shop")
        locked = make_project(self.root, "demo/locked")
        real = os.listdir

        def listing(path):
            if os.path.normcase(path) == os.path.normcase(locked):
                raise PermissionError(13, "Access is denied")
            return real(path)

        with mock.patch.object(ws.os, "listdir", listing):
            found = {c.id: c for c in ws.discover(self.root)}
        self.assertEqual(found["demo/locked"].status, "inaccessible")
        self.assertIn("Access is denied", found["demo/locked"].reason)
        self.assertEqual(found["demo/shop"].status, "valid")

    def test_listing_opens_only_project_yaml(self):
        shop = make_project(self.root, "demo/shop", env=f"DS_PAT={SECRET}\n")
        for rel, text in (("memory/tickets/t.json", SECRET), ("runs/batch_1.json", SECRET), ("evidence/e.txt", SECRET),
                          ("context.md", SECRET)):
            os.makedirs(os.path.dirname(os.path.join(shop, rel)), exist_ok=True)
            with open(os.path.join(shop, rel), "w", encoding="utf-8") as f:
                f.write(text)
        opened = []
        real = open

        def spy(path, *a, **k):
            opened.append(os.path.basename(str(path)))
            return real(path, *a, **k)

        with mock.patch("builtins.open", spy):
            found = ws.discover(self.root)
        self.assertEqual(set(opened), {"project.yaml"})
        self.assertNotIn(SECRET, json.dumps([c.public(self.root) for c in found]))


class Selection(Base):
    def test_a_path_given_is_normalised_and_may_be_any_folder(self):
        shop = make_project(self.tmp, "somewhere/shop")
        got = self.resolve(workspace=os.path.join(shop, "..", "shop"))
        self.assertEqual(got.outcome, "selected")
        self.assertEqual(got.candidate.path, shop)
        self.assertEqual(got.candidate.status, "valid")

    def test_a_path_that_does_not_exist_is_not_created(self):
        missing = os.path.join(self.tmp, "nothing")
        got = self.resolve(workspace=missing)
        self.assertEqual(got.outcome, "none")
        self.assertIn("nothing was created", got.message)
        self.assertFalse(os.path.exists(missing))

    def test_inside_the_framework_only_the_demo_is_a_workspace(self):
        self.assertEqual(self.resolve(workspace=os.path.join(ROOT, "docs")).outcome, "none")
        self.assertIn("inside the framework", self.resolve(workspace=ROOT).message)
        demo = self.resolve(workspace=os.path.join(ROOT, "examples", "demo"))
        self.assertEqual((demo.outcome, demo.candidate.status), ("selected", "valid"))

    def test_by_id_folder_name_and_part_of_a_name(self):
        make_project(self.root, "demo/shop", project="Demo Shop")
        make_project(self.root, "acme/api", project="Acme API")
        make_project(self.root, "solo", project="Solo Project")
        for hint, expected in (("demo/shop", "demo/shop"), ("DEMO\\shop", "demo/shop"), ("api", "acme/api"),
                               ("Acme API", "acme/api"), ("solo", "solo"), ("acme", "acme/api")):
            with self.subTest(hint=hint):
                got = self.resolve(hint)
                self.assertEqual((got.outcome, got.candidate.id), ("selected", expected))

    def test_the_same_name_in_two_companies_is_a_question(self):
        make_project(self.root, "demo/shop", project="Shop")
        make_project(self.root, "acme/shop", project="Shop")
        got = self.resolve("shop")
        self.assertEqual(got.outcome, "ambiguous")
        self.assertIsNone(got.candidate)
        listed = got.public()["candidates"]
        self.assertEqual([c["id"] for c in listed], ["acme/shop", "demo/shop"])
        self.assertEqual({c["path"] for c in listed}, {"acme/shop", "demo/shop"})     # the minimum: relative to the root
        self.assertEqual(self.resolve("acme/shop").candidate.id, "acme/shop")

    def test_similar_ids_never_select_the_wrong_one(self):
        make_project(self.root, "demo/shop")
        make_project(self.root, "demo/shop-2")
        self.assertEqual(self.resolve("demo/shop").candidate.id, "demo/shop")
        self.assertEqual(self.resolve("shop").candidate.id, "demo/shop")      # the exact folder wins over a part of a name
        self.assertEqual(self.resolve("shop-").candidate.id, "demo/shop-2")
        self.assertEqual(self.resolve("demo/sho").outcome, "ambiguous")

    def test_no_match_lists_what_exists(self):
        make_project(self.root, "demo/shop")
        got = self.resolve("other")
        self.assertEqual(got.outcome, "none")
        self.assertIn("no project matches", got.message)
        self.assertEqual([c.id for c in got.candidates], ["demo/shop"])

    def test_the_current_directory_comes_before_the_root(self):
        shop = make_project(self.root, "demo/shop")
        make_project(self.root, "acme/api")
        got = self.resolve(cwd=shop)
        self.assertEqual((got.outcome, got.how, got.candidate.id), ("selected", "the current directory", "demo/shop"))
        inner = os.path.join(shop, "runs", "old")
        os.makedirs(inner)
        self.assertEqual(self.resolve(cwd=inner).candidate.id, "demo/shop")

    def test_a_new_folder_in_the_root_is_selected_as_not_configured(self):
        make_project(self.root, "demo/shop")
        new = os.path.join(self.root, "demo", "new")
        os.makedirs(new)
        got = self.resolve(cwd=new)
        self.assertEqual((got.outcome, got.candidate.id, got.candidate.status), ("selected", "demo/new", "not-configured"))

    def test_inside_the_framework_the_directory_says_nothing(self):
        make_project(self.root, "demo/shop")
        make_project(self.root, "acme/api")
        self.assertEqual(self.resolve(cwd=os.path.join(ROOT, "docs")).outcome, "ambiguous")   # docs/project.yaml: a template

    def test_several_projects_are_a_question_and_nothing_is_chosen(self):
        make_project(self.root, "demo/shop")
        make_project(self.root, "acme/api")
        got = self.resolve()
        self.assertEqual((got.outcome, got.candidate), ("ambiguous", None))

    def test_the_only_valid_project_is_selected_and_said_so(self):
        make_project(self.root, "demo/shop")
        got = self.resolve()
        self.assertEqual((got.outcome, got.how), ("selected", "the only project in the root"))

    def test_one_valid_next_to_a_new_folder_is_still_a_question(self):
        make_project(self.root, "demo/shop")
        os.makedirs(os.path.join(self.root, "demo", "new"))
        self.assertEqual(self.resolve().outcome, "ambiguous")

    def test_an_empty_root_says_what_to_do(self):
        got = self.resolve()
        self.assertEqual(got.outcome, "none")
        self.assertIn("/project-onboarding", got.message)


class TheRoot(Base):
    def test_the_order_of_the_sources(self):
        os.makedirs(os.path.dirname(self.config))
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"workspaces_root": os.path.join(self.tmp, "configured")}, f)
        env = {ws.ROOT_ENV: os.path.join(self.tmp, "variable")}
        given = os.path.join(self.tmp, "given")
        self.assertEqual(ws.workspaces_root(given, env, self.config), (given, "--root"))
        self.assertEqual(ws.workspaces_root(None, env, self.config)[0], os.path.join(self.tmp, "variable"))
        self.assertEqual(ws.workspaces_root(None, {}, self.config)[0], os.path.join(self.tmp, "configured"))

    def test_a_root_that_does_not_exist_is_an_error_and_is_not_created(self):
        missing = os.path.join(self.tmp, "wrong", "qa-workspaces")
        got = self.resolve("shop", root=missing)
        self.assertEqual(got.outcome, "none")
        self.assertIn("nothing was created", got.message)
        self.assertIn(ws.ROOT_ENV, got.message)
        self.assertFalse(os.path.exists(os.path.dirname(missing)))

    def test_set_root_needs_a_folder_that_exists_outside_the_framework(self):
        self.assertEqual(ws.set_root(os.path.join(self.tmp, "nothing"), {}, self.config)[0], ws.NOTHING)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nothing")))
        self.assertEqual(ws.set_root(os.path.join(ROOT, "docs"), {}, self.config)[0], ws.NOTHING)
        self.assertEqual(ws.set_root(self.root, {}, self.config)[0], ws.OK)
        self.assertEqual(ws.workspaces_root(None, {}, self.config)[0], self.root)


@unittest.skipUnless(shutil.which("git"), "git is needed for a real worktree")
class Worktree(Base):
    def setUp(self):
        super().setUp()
        self.main = os.path.join(self.tmp, "side", "framework")
        os.makedirs(self.main)

        def git(*args, cwd=self.main):
            subprocess.run(["git", "-c", "user.name=Demo", "-c", "user.email=demo@example.com", *args], cwd=cwd,
                           check=True, capture_output=True)

        git("init", "-q")
        with open(os.path.join(self.main, "a.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        git("add", ".")
        git("commit", "-q", "-m", "first")
        self.worktree = os.path.join(self.main, ".claude", "worktrees", "feature")
        git("worktree", "add", "-q", "-b", "feature", self.worktree)

    def test_a_worktree_knows_its_main_checkout(self):
        self.assertTrue(sync_skills.same_path(common.main_checkout(self.worktree), self.main))
        self.assertTrue(sync_skills.same_path(common.main_checkout(self.main), self.main))
        self.assertEqual(common.main_checkout(self.neutral), self.neutral)

    def test_the_default_root_is_the_same_and_nothing_is_created(self):
        expected = os.path.join(os.path.dirname(self.main), "qa-workspaces")
        for framework in (self.main, self.worktree):
            root, source = ws.workspaces_root(None, {}, self.config, framework)
            self.assertTrue(sync_skills.same_path(root, expected), root)
            self.assertIn("main checkout", source)
        self.assertFalse(os.path.exists(os.path.join(self.main, ".claude", "worktrees", "qa-workspaces")))
        self.assertFalse(os.path.exists(expected))

    def test_the_state_of_a_checkout(self):
        state = common.git_state(self.worktree)
        self.assertEqual((state["branch"], state["dirty"]), ("feature", False))
        self.assertTrue(state["commit"])
        self.assertEqual(common.git_state(self.neutral)["commit"], None)

    def test_a_workspace_inside_the_main_checkout_is_refused_from_a_worktree(self):
        inside = os.path.join(self.main, "project")
        os.makedirs(inside)
        got = ws.resolve(workspace=inside, root=self.root, cwd=self.neutral, environ={}, config_path=self.config,
                         framework=self.worktree)
        self.assertIn("inside the framework", got.message)

    def test_where_on_this_checkout_says_where_its_imports_come_from(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "workspace_select.py"), "where", "--json",
                            "--root", self.root], cwd=self.neutral, capture_output=True, text=True, encoding="utf-8",
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        info = json.loads(r.stdout)
        self.assertTrue(sync_skills.same_path(info["framework"]["root"], ROOT))
        self.assertTrue(ws._inside(info["imports"]["adapters"], ROOT))
        self.assertTrue(ws._inside(info["imports"]["qa_ui"], ROOT))
        self.assertEqual(info["workspaces_root"]["source"], "--root")


class SkillAndCode(Base):
    """Skill global sem módulo correspondente: an explanation before anything runs, and nothing written."""

    def old_checkout(self) -> str:
        """A checkout that has the skills and the scripts, and not adapters/tickets (nor any adapter)."""
        old = os.path.join(self.tmp, "old-checkout")
        shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(old, "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(ROOT, ".claude", "skills"), os.path.join(old, ".claude", "skills"))
        return old

    def run_script(self, framework, *args, cwd=None):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home, exist_ok=True)
        env = {**os.environ, "USERPROFILE": home, "HOME": home, ws.CONFIG_ENV: self.config, "PYTHONIOENCODING": "utf-8"}
        env.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, os.path.join(framework, "scripts", "workspace_select.py"), *args],
                              cwd=cwd or self.neutral, env=env, capture_output=True, text=True, encoding="utf-8", check=False)

    def test_an_old_checkout_stops_with_an_instruction_and_writes_nothing(self):
        shop = make_project(self.root, "demo/shop", env=f"DS_PAT={SECRET}\n", tickets={"sources": {"boards": BOARDS}})
        before = tree(self.tmp)
        old = self.old_checkout()
        before_ws = tree(shop)
        r = self.run_script(old, "preflight", "--skill", "analyze-tickets", "--workspace", shop)
        self.assertEqual(r.returncode, ws.MISMATCH, r.stdout + r.stderr)
        self.assertIn("STOP", r.stdout)
        self.assertIn("module adapters.tickets", r.stdout)
        self.assertIn("sync_skills.py --apply", r.stdout)
        self.assertIn("Nothing was installed or changed", r.stdout)
        self.assertEqual(tree(shop), before_ws)
        self.assertTrue(before)

    def test_it_names_the_checkout_the_skills_came_from(self):
        make_project(self.root, "demo/shop")
        os.makedirs(self.target)
        sync_skills.write_record(self.target, {"root": ROOT, "branch": "main", "commit": "abc1234"})
        fake = os.path.join(self.tmp, "fake")
        os.makedirs(os.path.join(fake, ".claude", "skills", "analyze-tickets"))
        shutil.copy(os.path.join(ROOT, ".claude", "skills", "analyze-tickets", "SKILL.md"),
                    os.path.join(fake, ".claude", "skills", "analyze-tickets", "SKILL.md"))
        code, report = self.preflight("analyze-tickets", "demo/shop", framework=fake)
        self.assertEqual(code, ws.MISMATCH)
        self.assertIn(f"QA_ASSISTANT_ROOT={ROOT}", report["instruction"])
        self.assertIn("abc1234", report["instruction"])

    def test_a_record_of_a_checkout_that_is_gone_is_said(self):
        os.makedirs(self.target)
        gone = os.path.join(self.tmp, "removed-worktree")
        sync_skills.write_record(self.target, {"root": gone, "branch": "feature", "commit": "abc1234"})
        fake = os.path.join(self.tmp, "fake")
        os.makedirs(fake)
        code, report = self.preflight("analyze-tickets", framework=fake)
        self.assertEqual(code, ws.MISMATCH)
        self.assertIn("no longer exists", report["instruction"])

    def test_a_skill_this_checkout_does_not_have(self):
        code, report = self.preflight("no-such-skill")
        self.assertEqual(code, ws.MISMATCH)
        self.assertIn("does not exist in this checkout", report["instruction"])

    def test_a_global_copy_that_differs_stops_outside_the_framework_only(self):
        make_project(self.root, "demo/shop")
        os.makedirs(os.path.join(self.target, "analyze-feature"))
        with open(os.path.join(self.target, "analyze-feature", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: analyze-feature\n---\nan older copy\n")
        code, report = self.preflight("analyze-feature", "demo/shop")
        self.assertEqual(code, ws.MISMATCH)
        self.assertIn("differs from this checkout", report["instruction"])
        self.assertEqual(self.preflight("analyze-feature", "demo/shop", cwd=ROOT)[0], ws.OK)   # the project's own copy runs

    def test_the_same_copy_goes_on(self):
        make_project(self.root, "demo/shop")
        os.makedirs(os.path.join(self.target, "analyze-feature"))
        shutil.copy(os.path.join(ROOT, ".claude", "skills", "analyze-feature", "SKILL.md"),
                    os.path.join(self.target, "analyze-feature", "SKILL.md"))
        self.assertEqual(self.preflight("analyze-feature", "demo/shop")[0], ws.OK)

    def test_imports_from_another_checkout(self):
        make_project(self.root, "demo/shop", tickets={"sources": {"boards": BOARDS}})
        elsewhere = os.path.join(self.tmp, "another", "adapters", "tickets", "__init__.py")
        with mock.patch.object(ws, "import_origin", return_value=elsewhere):
            code, report = self.preflight("analyze-tickets", "demo/shop")
        self.assertEqual(code, ws.MISMATCH)
        self.assertIn("another checkout", report["instruction"])
        make_project(self.root, "demo/a11y", modules=["accessibility"], accessibility={"pages": [
            {"name": "home", "url": "https://shop.example/"}]})
        with mock.patch.object(ws, "import_origin", return_value=elsewhere), \
                mock.patch("validate_project.environment", return_value=[]):
            code, report = self.preflight("audit-accessibility", "demo/a11y")
        self.assertEqual(code, ws.OK)              # an editable qa-audit: said, not fatal
        self.assertIn("another checkout", " ".join(report["warnings"]))


class Preflight(Base):
    def test_ready_presents_the_project_task_environment_and_tools(self):
        shop = make_project(self.root, "demo/shop", env=f"DS_PAT={SECRET}\n",
                            tickets={"sources": {"boards": BOARDS}, "scopes": {}})
        code, report = self.preflight("analyze-tickets", "demo/shop")
        self.assertEqual(code, ws.OK)
        self.assertEqual(report["project"]["id"], "demo/shop")
        self.assertTrue(sync_skills.same_path(report["project"]["path"], shop))
        self.assertEqual(report["environments"], ["test"])
        self.assertEqual(report["skill"]["name"], "analyze-tickets")
        self.assertIn("Never the tracker", report["skill"]["writes"])
        self.assertEqual(report["sources"], [{"id": "boards", "service": "azure-devops", "access": "api",
                                              "credentials": {"DS_PAT": True}}])
        text = json.dumps(report)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("shop.example", text)     # the keys of the environments: their addresses need not be shown

    def test_it_writes_nothing(self):
        make_project(self.root, "demo/shop", env=f"DS_PAT={SECRET}\n", tickets={"sources": {"boards": BOARDS}})
        before = tree(self.root)
        for skill in ("analyze-tickets", "checkpoint", "run-batch", "plan-batch"):
            self.preflight(skill, "demo/shop")
        self.assertEqual(tree(self.root), before)

    def test_ambiguous_is_a_question(self):
        make_project(self.root, "demo/shop")
        make_project(self.root, "acme/api")
        code, report = self.preflight("analyze-feature")
        self.assertEqual(code, ws.AMBIGUOUS)
        self.assertEqual([c["id"] for c in report["candidates"]], ["acme/api", "demo/shop"])
        self.assertIsNone(report["project"])

    def test_a_new_folder_proposes_the_onboarding(self):
        os.makedirs(os.path.join(self.root, "demo", "new"))
        code, report = self.preflight("run-batch", "demo/new")
        self.assertEqual(code, ws.NOT_USABLE)
        self.assertEqual(report["project"]["status"], "not-configured")
        self.assertEqual(report["proposes"], "/project-onboarding")
        self.assertIn("no project.yaml", report["problems"])

    def test_the_onboarding_itself_starts_from_a_new_folder(self):
        os.makedirs(os.path.join(self.root, "demo", "new"))
        code, report = self.preflight("project-onboarding", "demo/new")
        self.assertEqual(code, ws.OK)
        self.assertEqual(report["project"]["status"], "not-configured")

    def test_an_incomplete_project_lists_its_problems(self):
        make_project(self.root, "demo/half", bug_format="", modules=[])
        code, report = self.preflight("plan-batch", "demo/half")
        self.assertEqual(code, ws.NOT_USABLE)
        self.assertIn("field 'bug_format' is empty", report["problems"])
        self.assertEqual(report["proposes"], "/project-onboarding")

    def test_the_configuration_the_skill_needs(self):
        make_project(self.root, "demo/shop")
        for skill, words in (("analyze-tickets", "no ticket source"), ("test-api", "module api is not in modules"),
                             ("test-mobile", "module mobile is not in modules")):
            with self.subTest(skill=skill):
                code, report = self.preflight(skill, "demo/shop")
                self.assertEqual(code, ws.NOT_USABLE)
                self.assertIn(words, " ".join(report["problems"]))
                self.assertEqual(report["proposes"], "/project-onboarding")

    def test_the_dashboard_is_for_excel(self):
        make_project(self.root, "demo/shop", tracker="jira", jira={
            "site": "https://demo-shop.atlassian.net", "project_key": "DS", "user_var": "U", "token_var": "T",
            "xray": {"client_id_var": "C", "client_secret_var": "S"}, "results": {"statuses": {"Pass": "PASSED"}}})
        code, report = self.preflight("dashboard", "demo/shop")
        self.assertEqual(code, ws.NOT_USABLE)
        self.assertIn("tracker: excel", " ".join(report["problems"]))

    def test_a_missing_tool_is_shown_and_does_not_stop(self):
        make_project(self.root, "demo/mob", modules=["mobile"], mobile={"platform": "android", "flows": "flows"})
        tools = [(False, "maestro", "install the Maestro CLI"), (True, "adb", "/sdk/adb"), (True, "git", "/bin/git"),
                 (False, "emulator", "install the SDK")]
        with mock.patch("validate_project.environment", return_value=tools):
            code, report = self.preflight("test-mobile", "demo/mob")
        self.assertEqual(code, ws.OK)
        self.assertEqual([(t["tool"], t["ok"]) for t in report["tools"]],
                         [("maestro", False), ("adb", True), ("emulator", False)])        # git is not this skill's

    def test_a_skill_without_tools_asks_no_tool(self):
        make_project(self.root, "demo/shop", tickets={"sources": {"boards": BOARDS}})
        with mock.patch("validate_project.environment", side_effect=AssertionError("a tool was asked")):
            self.assertEqual(self.preflight("analyze-tickets", "demo/shop")[0], ws.OK)

    def test_the_browser_mcp_is_named(self):
        shop = make_project(self.root, "demo/shop")
        with mock.patch("validate_project.environment", return_value=[]):
            self.assertFalse(self.preflight("run-batch", "demo/shop")[1]["tools"][0]["ok"])
            with open(os.path.join(shop, ".mcp.json"), "w", encoding="utf-8") as f:
                json.dump({"mcpServers": {"playwright": {}}}, f)
            line = self.preflight("run-batch", "demo/shop")[1]["tools"][0]
        self.assertEqual((line["ok"], line["tool"]), (True, "mcp"))
        self.assertIn("playwright", line["detail"])

    def test_nothing_to_select(self):
        code, report = self.preflight("plan-batch", root=os.path.join(self.tmp, "missing"))
        self.assertEqual(code, ws.NOTHING)
        self.assertIn("does not exist", report["message"])


class TicketSourcesInTheEnvironment(Base):
    def lines(self, path):
        import validate_project
        proj = common.read_project(path)
        with mock.patch.object(validate_project, "which", return_value="/bin/tool"):
            return {t: (ok, d) for ok, t, d in validate_project.environment(path, proj) if t.startswith("ticket source")}

    def test_a_token_is_named_never_valued_and_a_browser_needs_none(self):
        browser = {k: v for k, v in {**BOARDS, "access": "browser"}.items() if k != "pat_var"}
        shop = make_project(self.root, "demo/shop", env=f"DS_PAT={SECRET}\n",
                            tickets={"sources": {"a": BOARDS, "b": browser}})
        lines = self.lines(shop)
        self.assertEqual((lines["ticket source a"][0], lines["ticket source b"][0]), (True, True))
        self.assertNotIn(SECRET, json.dumps(lines))
        self.assertIn("logged-in browser", lines["ticket source b"][1])
        empty = make_project(self.root, "demo/empty", tickets={"sources": {"a": BOARDS}})
        ok, detail = self.lines(empty)["ticket source a"]
        self.assertFalse(ok)
        self.assertIn("DS_PAT", detail)

    def test_base_problems_reads_only_what_it_is_given(self):
        import validate_project
        self.assertEqual(validate_project.base_problems(dict(VALID))[0], [])
        problems, _ = validate_project.base_problems({**VALID, "tickets": {"sources": {"a": BOARDS},
                                                                           "cases": {"link_fields": "Ticket"}}})
        self.assertIn("tickets.cases.link_fields: must be a list of texts", problems)


class CommandLine(Base):
    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ws.main(list(args))
        return code, out.getvalue()

    def test_list_and_resolve_and_exit_codes(self):
        make_project(self.root, "demo/shop")
        os.makedirs(os.path.join(self.root, "acme", "new"))
        code, out = self.run_("list", "--root", self.root)
        self.assertEqual(code, 0)
        self.assertIn("valid", out)
        self.assertIn("not-configured", out)
        self.assertIn("/project-onboarding", out)
        self.assertEqual(self.run_("resolve", "shop", "--root", self.root)[0], ws.OK)
        self.assertEqual(self.run_("resolve", "acme/new", "--root", self.root)[0], ws.NOT_USABLE)
        code, out = self.run_("resolve", "nothing", "--root", self.root, "--json")
        self.assertEqual(code, ws.NOTHING)
        self.assertEqual(json.loads(out)["outcome"], "none")
        self.assertEqual(self.run_("list", "--root", os.path.join(self.tmp, "gone"))[0], ws.NOTHING)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "gone")))

    def test_the_catalogue_comes_from_the_frontmatter(self):
        code, out = self.run_("skills", "--json")
        self.assertEqual(code, 0)
        names = {s["name"] for s in json.loads(out)}
        self.assertEqual(names, {d for d in os.listdir(os.path.join(ROOT, ".claude", "skills"))})


class WritesStayInside(Base):
    def setUp(self):
        super().setUp()
        self.shop = make_project(self.root, "demo/shop")
        self.other = make_project(self.root, "demo/other")

    def refused(self, *parts):
        with self.assertRaises(common.OutsideWorkspace):
            common.workspace_path(self.shop, *parts)

    def test_inside_is_allowed_and_nothing_else(self):
        self.assertEqual(common.workspace_path(self.shop, "runs", "a.json"), os.path.join(self.shop, "runs", "a.json"))
        self.assertEqual(common.workspace_path(self.shop, "memory/../runs/a.json"), os.path.join(self.shop, "memory/../runs/a.json"))
        self.refused("..", "other", "a.json")
        self.refused("runs", "..", "..", "other", "a.json")
        self.refused(os.path.join(self.other, "a.json"))
        self.refused("history", "a.json")
        self.refused("history")
        self.refused("runs", "..", "history", "a.json")

    def test_a_link_pointing_out_is_refused(self):
        if not (make_link(self.other, os.path.join(self.shop, "runs"))
                and make_link(self.other, os.path.join(self.shop, "tracker"))):
            self.skipTest("links cannot be created here")
        self.refused("runs", "a.json")
        with self.assertRaises(run_log.UsageError):
            run_log.run_log_path(self.shop, "b1")
        with self.assertRaises(ConfigError):
            tracker_safe_path(self.shop, "sent", "a.json")

    def test_a_link_into_history_is_refused(self):
        os.makedirs(os.path.join(self.shop, "history"))
        if not make_link(os.path.join(self.shop, "history"), os.path.join(self.shop, "memory")):
            self.skipTest("links cannot be created here")
        self.refused("memory", "x.json")

    def test_the_ticket_memory_refuses_a_link_to_another_project(self):
        tickets = {"sources": {"boards": BOARDS}}
        for path in (self.shop, self.other):
            with open(os.path.join(path, "project.yaml"), "w", encoding="utf-8") as f:
                yaml.safe_dump({**VALID, "tickets": tickets}, f)
            with open(os.path.join(path, ".env"), "w", encoding="utf-8") as f:
                f.write(f"DS_PAT={SECRET}\n")
        os.makedirs(os.path.join(self.other, "memory"))
        if not make_link(os.path.join(self.other, "memory"), os.path.join(self.shop, "memory")):
            self.skipTest("links cannot be created here")
        before = tree(self.other)
        fake = FakeAdo()
        fake.add(100)
        lines = []
        code = tickets_cli.main([self.shop, "collect", "100"], transport=fake, sleep=lambda s: None, printer=lines.append)
        self.assertEqual(code, 4)
        self.assertIn("outside the workspace", "\n".join(lines))
        self.assertEqual(tree(self.other), before)

    def test_the_output_of_a_script_stays_in_the_workspace(self):
        outside = os.path.join(self.tmp, "stolen.json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(api_request, "request", side_effect=AssertionError("the request was made")):
            code = api_request.main([self.shop, "GET", "booking", "--output", outside])
        self.assertEqual(code, 2)
        self.assertIn("outside the workspace", err.getvalue())
        self.assertFalse(os.path.exists(outside))

    def test_the_runner_keeps_its_report_in_its_temporary_folder(self):
        temp = os.path.join(self.tmp, "runner-temp")
        os.makedirs(temp)
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": temp}):
            target = os.path.join(temp, "flows.json")
            self.assertEqual(run_mobile.report_path(self.shop, target), target)
            with self.assertRaises(common.OutsideWorkspace):
                run_mobile.report_path(self.shop, os.path.join(self.tmp, "elsewhere", "flows.json"))
        env = {k: v for k, v in os.environ.items() if k != "RUNNER_TEMP"}
        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(common.OutsideWorkspace):
            run_mobile.report_path(self.shop, os.path.join(temp, "flows.json"))


class TwoProjects(Base):
    """Dois workspaces com IDs semelhantes: nothing read from or written to the other."""

    def setUp(self):
        super().setUp()
        tickets = {"sources": {"boards": BOARDS}, "cases": {"link_fields": ["Ticket"]}}
        self.a = make_project(self.root, "demo/shop", env="DS_PAT=pat-of-project-a-111\n", tickets=tickets)
        self.b = make_project(self.root, "acme/shop", env="DS_PAT=pat-of-project-b-222\n", tickets=tickets)

    def collect(self, path, fake, now):
        lines = []
        code = tickets_cli.main([path, "collect", "100", "--now", now], transport=fake, sleep=lambda s: None,
                                printer=lines.append)
        return code

    def test_memory_and_credentials_never_cross(self):
        fake_a, fake_b = FakeAdo(), FakeAdo()
        fake_a.add(100, System__Title="Coupon of project A", System__Description="pat-of-project-b-222 is in this text")
        fake_b.add(100, System__Title="Coupon of project B")
        self.assertEqual(self.collect(self.a, fake_a, "2026-11-01T10:00:00"), 0)
        before_b = tree(self.b)
        self.assertEqual(self.collect(self.b, fake_b, "2026-11-01T11:00:00"), 0)
        self.assertNotIn("memory", {p.split(os.sep)[0] for p in before_b})
        expected = {fake_a: "pat-of-project-a-111", fake_b: "pat-of-project-b-222"}
        for fake, pat in expected.items():
            sent = {r.headers["Authorization"] for r in fake.requests}
            self.assertEqual(sent, {"Basic " + base64.b64encode(f":{pat}".encode()).decode()})
        a_files, b_files = tree(self.a), tree(self.b)
        self.assertTrue(any(p.startswith("memory") for p in a_files))
        self.assertTrue(any(p.startswith("memory") for p in b_files))
        for path, wanted, unwanted in ((self.a, "Coupon of project A", "Coupon of project B"),
                                       (self.b, "Coupon of project B", "Coupon of project A")):
            names = [p for p in tree(path) if p.startswith(os.path.join("memory", "tickets")) and p.endswith(".json")]
            self.assertEqual(len(names), 1)
            with open(os.path.join(path, names[0]), encoding="utf-8") as f:
                text = f.read()
            self.assertIn(wanted, text)
            self.assertNotIn(unwanted, text)

    def test_a_second_check_in_b_changes_nothing_in_a(self):
        fake_a, fake_b = FakeAdo(), FakeAdo()
        fake_a.add(100)
        fake_b.add(100)
        self.collect(self.a, fake_a, "2026-11-01T10:00:00")
        before_a = tree(self.a)
        self.collect(self.b, fake_b, "2026-11-01T11:00:00")
        fake_b.set(100, System__State="Done")
        self.collect(self.b, fake_b, "2026-11-02T11:00:00")
        self.assertEqual(tree(self.a), before_a)

    def test_the_entry_flow_from_a_neutral_folder(self):
        os.makedirs(self.target, exist_ok=True)
        self.assertEqual(self.preflight("analyze-tickets")[0], ws.AMBIGUOUS)
        self.assertEqual(self.preflight("analyze-tickets", "shop")[0], ws.AMBIGUOUS)    # the same folder in two companies
        code, report = self.preflight("analyze-tickets", "acme/shop")
        self.assertEqual(code, ws.OK)
        self.assertTrue(sync_skills.same_path(report["project"]["path"], self.b))
        self.assertEqual(report["sources"][0]["credentials"], {"DS_PAT": True})


if __name__ == "__main__":
    unittest.main()

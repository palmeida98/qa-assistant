"""scripts/workspace_git.py (plan 9.4; methodology 4, step 4, and 12.3): the workspace as a git repository of its
own, with a private remote, pushed only on approval.

python -m unittest discover -s tests   (from the root of qa-assistant)

Every repository is created in a temporary folder; the "remote" is a local bare repository, also temporary. git's
hook variables are dropped and HOME / GIT_CONFIG_GLOBAL point to a temporary file, so neither this repository nor the
user's git configuration is ever read or changed. gh is always the fake of the test: nothing reaches GitHub.
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import common
import workspace_git as wg

GIT_HOOK_VARIABLES = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR")
ISOLATED = ("HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "XDG_CONFIG_HOME")
_saved = {}
_home = None

PROJECT = {"project": "demo-shop", "company": "demo", "environments": {"test": "https://shop.demo"},
           "tracker": "jira", "bug_format": "SHOP-{n}", "modules": ["api", "mobile"],
           "api": {"base_url": "https://shop.demo/api",
                   "credentials": {"admin": {"user_var": "SHOP_ADMIN_USER", "pass_var": "SHOP_ADMIN_PASSWORD"}}},
           "mobile": {"platform": "android", "flows": "flows", "variables": ["SHOP_APP_USER", "SHOP_ADMIN_USER"]}}
ENV_VALUE = "value-that-must-not-leak"


def isolate_git() -> None:
    """Drops git's hook variables and gives git a temporary home and global configuration."""
    global _home
    _saved.update({k: os.environ.get(k) for k in GIT_HOOK_VARIABLES + ISOLATED})
    for k in GIT_HOOK_VARIABLES:
        os.environ.pop(k, None)
    _home = tempfile.mkdtemp()
    config = os.path.join(_home, "gitconfig")
    with open(config, "w", encoding="utf-8") as f:
        f.write("[user]\n\tname = Demo Tester\n\temail = tester@demo.invalid\n[commit]\n\tgpgsign = false\n"
                "[init]\n\tdefaultBranch = main\n")
    os.environ.update({"HOME": _home, "GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1",
                       "XDG_CONFIG_HOME": _home})


def restore_git() -> None:
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_home, ignore_errors=True)


def setUpModule():
    isolate_git()


def tearDownModule():
    restore_git()


def git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=True).stdout


class FakeGh:
    """gh with a fixed answer; records each call. visibility=None: gh not installed."""

    def __init__(self, visibility=None, fail=False):
        self.visibility, self.fail, self.calls = visibility, fail, []

    def __call__(self, *args):
        self.calls.append(args)
        if self.visibility is None:
            return None
        if self.fail:
            return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 401")
        return SimpleNamespace(returncode=0, stdout=f'{{"visibility": "{self.visibility}"}}', stderr="")


@unittest.skipUnless(shutil.which("git"), "no git")
class WorkspaceGit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self.tmp.name, "demo", "demo-shop")
        os.makedirs(os.path.join(self.ws, "runs"))
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(PROJECT, f)
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write(f"SHOP_ADMIN_USER=admin\nSHOP_ADMIN_PASSWORD={ENV_VALUE}\n")
        with open(os.path.join(self.ws, "runs", "batch_01.json"), "w", encoding="utf-8") as f:
            f.write('{"results": []}\n')
        self.gh = FakeGh()
        patcher = mock.patch.object(wg, "gh", self.gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wg.main([self.ws, *args])
        return code, out.getvalue() + err.getvalue()

    def read(self, name):
        with open(os.path.join(self.ws, name), encoding="utf-8") as f:
            return f.read()

    def commits(self):
        return git(self.ws, "log", "--format=%s").splitlines()

    def bare_remote(self):
        bare = os.path.join(self.tmp.name, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        return bare

    # --- init ---------------------------------------------------------------------------------------------------

    def test_init_on_a_fresh_folder(self):
        code, out = self.run_("init")
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.ws, "symbolic-ref", "--short", "HEAD").strip(), "main")
        with open(wg.TEMPLATE, encoding="utf-8") as f:
            self.assertEqual(self.read(".gitignore"), f.read())
        example = self.read(".env.example")
        names = [x for x in example.splitlines() if x and not x.startswith("#")]
        self.assertEqual(names, ["SHOP_ADMIN_PASSWORD=", "SHOP_ADMIN_USER=", "SHOP_APP_USER="])
        self.assertNotIn(ENV_VALUE, example)
        self.assertNotIn("admin\n", example)
        self.assertEqual(self.commits(), ["workspace: initial commit"])
        files = git(self.ws, "ls-files").splitlines()
        self.assertIn(".env.example", files)
        self.assertIn("project.yaml", files)
        self.assertIn("runs/batch_01.json", files)
        self.assertNotIn(".env", files)

    def test_init_is_idempotent(self):
        self.assertEqual(self.run_("init")[0], 0)
        before = (self.read(".gitignore"), self.read(".env.example"))
        code, out = self.run_("init")
        self.assertEqual(code, 0, out)
        self.assertIn(".gitignore: unchanged", out)
        self.assertIn(".env.example: unchanged", out)
        self.assertEqual((self.read(".gitignore"), self.read(".env.example")), before)
        self.assertEqual(self.commits(), ["workspace: initial commit"])

    def test_init_merges_an_existing_gitignore_and_env_example(self):
        with open(os.path.join(self.ws, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("# mine\nnode_modules/\n.env\n*.example")          # no final newline; hides .env.example
        with open(os.path.join(self.ws, ".env.example"), "w", encoding="utf-8") as f:
            f.write("# mine\nSHOP_APP_USER=\nOTHER_NAME=\n")
        self.assertEqual(self.run_("init")[0], 0)
        lines = self.read(".gitignore").splitlines()
        self.assertEqual(lines[:4], ["# mine", "node_modules/", ".env", "*.example"])
        self.assertEqual(lines.count(".env"), 1)
        for line in ("dashboard.html", "*.apk", ".qa-audit/", "~$*", "!.env.example"):
            self.assertIn(line, lines)
        self.assertGreater(lines.index("!.env.example"), lines.index("*.example"))   # after it, so it wins
        self.assertIn(".env.example", git(self.ws, "ls-files").splitlines())
        example = self.read(".env.example").splitlines()
        self.assertEqual(example, ["# mine", "SHOP_APP_USER=", "OTHER_NAME=", "SHOP_ADMIN_PASSWORD=",
                                   "SHOP_ADMIN_USER="])

    def test_init_keeps_env_example_visible(self):
        with open(os.path.join(self.ws, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("!.env.example\n*.example\n")                          # the negation comes too early
        self.assertEqual(self.run_("init")[0], 0)
        self.assertEqual(self.read(".gitignore").splitlines()[-1], "!.env.example")
        self.assertIn(".env.example", git(self.ws, "ls-files").splitlines())

    def test_init_refuses_a_gitignore_that_undoes_the_template(self):
        with open(os.path.join(self.ws, ".gitignore"), "w", encoding="utf-8") as f:
            f.write(".env\n!.env\n")
        code, out = self.run_("init")
        self.assertEqual(code, 1, out)
        self.assertIn(".env", out)
        self.assertEqual(git(self.ws, "rev-list", "--all").strip(), "")  # nothing committed

    def test_init_refuses_when_env_is_tracked(self):
        git(self.ws, "init", "-q")
        git(self.ws, "add", ".env", "project.yaml")
        git(self.ws, "commit", "-q", "-m", "by hand")
        code, out = self.run_("init")
        self.assertEqual(code, 1, out)
        self.assertIn("git rm --cached .env", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".env.example")))

    def test_init_refuses_a_workspace_inside_another_repository(self):
        git(self.tmp.name, "init", "-q")
        code, out = self.run_("init")
        self.assertEqual(code, 2, out)
        self.assertIn("inside another git repository", out)
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".git")))

    def test_init_without_project_yaml(self):
        os.remove(os.path.join(self.ws, "project.yaml"))
        self.assertEqual(self.run_("init")[0], 2)

    def test_init_refuses_to_commit_a_file_over_50_mb(self):
        big = os.path.join(self.ws, "workbook.xlsx")
        with open(big, "wb") as f:
            f.write(b"x")
        real = wg.file_size
        with mock.patch.object(wg, "file_size", lambda p: wg.LIMIT + 1 if p.endswith("workbook.xlsx") else real(p)):
            code, out = self.run_("init")
        self.assertEqual(code, 1, out)
        self.assertIn("workbook.xlsx", out)
        self.assertEqual(git(self.ws, "ls-files").strip(), "")          # the staging was undone

    def test_the_variable_names_of_project_yaml(self):
        proj = {**PROJECT, "accessibility": {"login": {"user_var": "A11Y_USER", "pass_var": "bad name"}}}
        self.assertEqual(common.variable_names(proj), ["A11Y_USER", "SHOP_ADMIN_PASSWORD", "SHOP_ADMIN_USER",
                                                       "SHOP_APP_USER"])

    # --- status -------------------------------------------------------------------------------------------------

    def test_status_counts_the_commits_not_pushed(self):
        self.run_("init")
        code, out = self.run_("status")
        self.assertEqual(code, 0, out)
        self.assertIn("branch: main", out)
        self.assertIn("remote: none", out)
        self.assertIn("not pushed: 1 commit(s) (no upstream)", out)
        self.assertEqual(self.run_("remote", "--set", self.bare_remote())[0], 0)
        self.assertEqual(self.run_("push", "--i-confirm-private")[0], 0)
        self.assertIn("not pushed: 0 commit(s) (against origin/main)", self.run_("status")[1])
        with open(os.path.join(self.ws, "context.md"), "w", encoding="utf-8") as f:
            f.write("# Session context\n")
        _, out = self.run_("status")
        self.assertIn("untracked: 1 file(s)", out)
        self.assertIn("context.md", out)
        git(self.ws, "add", "context.md")
        git(self.ws, "commit", "-q", "-m", "checkpoint")
        _, out = self.run_("status")
        self.assertIn("not pushed: 1 commit(s) (against origin/main)", out)
        self.assertIn("checkpoint", out)
        self.assertIn("visibility: unknown (not a GitHub remote)", out)
        self.assertEqual(self.gh.calls, [])                             # a local path never reaches gh

    def test_status_outside_a_repository(self):
        self.assertEqual(self.run_("status")[0], 2)

    def test_status_shows_the_visibility(self):
        self.run_("init")
        self.run_("remote", "--set", "https://github.com/demo-user/demo-shop.git")
        self.gh.visibility = "PRIVATE"
        _, out = self.run_("status")
        self.assertIn("visibility: PRIVATE", out)
        self.assertEqual(self.gh.calls, [("repo", "view", "demo-user/demo-shop", "--json", "visibility")])

    # --- remote -------------------------------------------------------------------------------------------------

    def test_remote_create_only_with_yes(self):
        self.run_("init")
        self.gh.visibility = "PRIVATE"
        code, out = self.run_("remote", "--create", "demo-user/demo-demo-shop")
        self.assertEqual(code, 0, out)
        self.assertIn(f"gh repo create demo-user/demo-demo-shop --private --source {self.ws} --remote origin", out)
        self.assertIn("not run", out)
        self.assertEqual(self.gh.calls, [])
        code, out = self.run_("remote", "--create", "demo-user/demo-demo-shop", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.gh.calls, [("repo", "create", "demo-user/demo-demo-shop", "--private", "--source",
                                          self.ws, "--remote", "origin")])

    def test_remote_create_never_without_private(self):
        for cmd in (["gh", "repo", "create", "demo-user/x", "--source", "."],
                    ["gh", "repo", "create", "demo-user/x", "--private", "--public"]):
            with self.subTest(cmd=cmd), self.assertRaises(wg.Refused):
                wg.run_create(cmd)
        self.assertEqual(self.gh.calls, [])
        self.assertIn("--private", wg.create_command(self.ws, "demo-user/x"))

    def test_remote_usage_errors(self):
        self.run_("init")
        self.assertEqual(self.run_("remote", "--create", "no-slash")[0], 2)
        self.assertEqual(self.run_("remote", "--set", self.bare_remote())[0], 0)
        self.assertEqual(self.run_("remote", "--create", "demo-user/x", "--yes")[0], 2)   # origin exists
        self.assertEqual(self.run_("remote", "--set", "https://github.com/demo-user/other.git")[0], 2)
        self.assertEqual(self.gh.calls, [])

    # --- push ---------------------------------------------------------------------------------------------------

    def github_remote_pushing_to(self, bare):
        """origin says github.com (what gh is asked about); the push goes to the local bare repository."""
        url = "https://github.com/demo-user/demo-shop.git"
        self.run_("remote", "--set", url)
        git(self.ws, "config", f"url.{bare.replace(os.sep, '/')}.pushInsteadOf", url)

    def test_push_to_a_private_remote(self):
        self.run_("init")
        bare = self.bare_remote()
        self.github_remote_pushing_to(bare)
        self.gh.visibility = "PRIVATE"
        code, out = self.run_("push")
        self.assertEqual(code, 0, out)
        self.assertIn("pushed 1 commit(s) of main", out)
        self.assertEqual(git(bare, "log", "--format=%s", "main").splitlines(), ["workspace: initial commit"])

    def test_push_refused_for_a_public_or_unknown_remote(self):
        self.run_("init")
        bare = self.bare_remote()
        self.github_remote_pushing_to(bare)
        for gh, flags, why in [(FakeGh("PUBLIC"), [], "PUBLIC"), (FakeGh("PUBLIC"), ["--i-confirm-private"], "PUBLIC"),
                               (FakeGh(None), [], "gh is not installed"), (FakeGh("PRIVATE", fail=True), [], "failed"),
                               (FakeGh("INTERNAL"), [], "INTERNAL")]:
            with self.subTest(visibility=gh.visibility, flags=flags), mock.patch.object(wg, "gh", gh):
                code, out = self.run_("push", *flags)
                self.assertEqual(code, 1, out)
                self.assertIn(why, out)
        self.assertEqual(git(bare, "rev-list", "--all").strip(), "")     # nothing arrived

    def test_push_with_the_users_confirmation_when_unknown(self):
        self.run_("init")
        bare = self.bare_remote()
        self.run_("remote", "--set", bare)
        code, out = self.run_("push", "--i-confirm-private")
        self.assertEqual(code, 0, out)
        self.assertIn("by the user's confirmation", out)

    def test_push_refused_for_a_file_over_50_mb_or_env_tracked(self):
        self.run_("init")
        bare = self.bare_remote()
        self.run_("remote", "--set", bare)
        real = wg.file_size
        with mock.patch.object(wg, "file_size", lambda p: wg.LIMIT + 1 if p.endswith("project.yaml") else real(p)):
            code, out = self.run_("push", "--i-confirm-private")
        self.assertEqual(code, 1, out)
        self.assertIn("project.yaml", out)
        git(self.ws, "add", "-f", ".env")
        git(self.ws, "commit", "-q", "-m", "oops")
        code, out = self.run_("push", "--i-confirm-private")
        self.assertEqual(code, 1, out)
        self.assertIn(".env is tracked", out)
        self.assertEqual(git(bare, "rev-list", "--all").strip(), "")

    def test_push_without_origin_and_never_force(self):
        self.run_("init")
        self.assertEqual(self.run_("push", "--i-confirm-private")[0], 2)
        self.assertNotIn("--force", " ".join(wg.PUSH))
        self.assertNotIn("-f", wg.PUSH)

    def test_repo_slug(self):
        for url, slug in [("https://github.com/demo-user/demo-shop.git", "demo-user/demo-shop"),
                          ("git@github.com:demo-user/demo-shop.git", "demo-user/demo-shop"),
                          ("https://token@github.com/demo-user/demo-shop", "demo-user/demo-shop"),
                          ("https://git.demo.invalid/team/shop.git", "git.demo.invalid/team/shop"),
                          ("/tmp/remote.git", None), ("C:\\remote.git", None)]:
            with self.subTest(url=url):
                self.assertEqual(wg.repo_slug(url), slug)


if __name__ == "__main__":
    unittest.main()

"""scripts/apk_manifest.py and scripts/workspace_build.py (plan 11.1; technique-mobile.md §11): the APK's binary
manifest read in pure Python (fixture APKs built here, never a real app), and the builds as releases of the
workspace's private repository — publish (dry run, --yes, the refusals) and fetch (the SHA-256 check).

python -m unittest discover -s tests   (from the root of qa-assistant)

gh is always a fake (nothing reaches GitHub); every repository is temporary, with git's hook variables dropped and a
temporary global configuration (test_workspace_git.isolate_git).
"""

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
from types import SimpleNamespace
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

import apk_manifest as am
import workspace_build as wb
import workspace_git as wg
from apk_kit import axml, make_apk
from test_workspace_git import isolate_git, restore_git

SLUG = "demo-shop/demo-shop-qa"
PROJECT = """project: demo-shop   # the Demo Shop
tracker: excel
modules: [mobile]

mobile:
  platform: android            # android | ios | web
  flows: flows
  app_id: com.demoshop.app     # the app under test
  build:
    source: "CI artefact"      # replaced by publish
    sha256: ""
  permissions: {grant: [android.permission.POST_NOTIFICATIONS]}

# a comment that must stay
dashboard:
  states: {}
"""


def setUpModule():
    isolate_git()


def tearDownModule():
    restore_git()


class Manifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def apk(self, name="app.apk", **kw):
        return make_apk(os.path.join(self.tmp.name, name), **kw)

    def test_utf8_and_utf16_string_pools(self):
        for utf8 in (True, False):
            with self.subTest(utf8=utf8):
                self.assertEqual(am.read_manifest(self.apk(utf8=utf8, version_name="1.0.0", version_code=16)),
                                 {"package": "com.demoshop.app", "version_name": "1.0.0", "version_code": 16,
                                  "min_sdk": 24, "target_sdk": 36})

    def test_long_and_non_ascii_strings(self):
        # lengths of two units (> 127 UTF-8 bytes, > 0x7fff UTF-16 units is not needed for a manifest)
        for utf8 in (True, False):
            for name in ("9" * 200, "versão-ç" * 30):
                with self.subTest(utf8=utf8, name=name[:8]):
                    self.assertEqual(am.read_manifest(self.apk(utf8=utf8, version_name=name))["version_name"], name)

    def test_attribute_names_stripped_are_found_by_resource_id(self):
        m = am.read_manifest(self.apk(strip_names=True, version_code=140))
        self.assertEqual((m["version_code"], m["version_name"], m["target_sdk"]), (140, "1.4.0", 36))

    def test_hex_version_code_and_missing_version_name(self):
        self.assertEqual(am.read_manifest(self.apk(code_as_hex=True, version_code=0x8C))["version_code"], 140)
        self.assertIsNone(am.read_manifest(self.apk(version_name=None))["version_name"])

    def test_not_an_apk_or_not_binary_xml(self):
        path = os.path.join(self.tmp.name, "plain.txt")
        with open(path, "w") as f:
            f.write("not a zip")
        for apk, fragment in [(path, "not an APK"),
                              (self.apk("text.apk", manifest=b"<manifest package='x'/>"), "not binary XML"),
                              (self.apk("cut.apk", manifest=axml()[:60]), "broken chunk")]:
            with self.subTest(fragment=fragment), self.assertRaises(am.ManifestError) as e:
                am.read_manifest(apk)
            self.assertIn(fragment, str(e.exception))
        import zipfile
        empty = os.path.join(self.tmp.name, "empty.apk")
        with zipfile.ZipFile(empty, "w") as z:
            z.writestr("classes.dex", b"")
        with self.assertRaises(am.ManifestError):
            am.read_manifest(empty)


class FakeGh:
    """gh: `repo view` (visibility), `release view/create/download`. Records every call."""

    def __init__(self, visibility="PRIVATE", releases=None, asset=b""):
        self.visibility, self.releases, self.asset = visibility, dict(releases or {}), asset
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ("repo", "view"):
            if self.visibility is None:
                return None
            return SimpleNamespace(returncode=0, stdout=f'{{"visibility": "{self.visibility}"}}', stderr="")
        if args[:2] == ("release", "view"):
            tag = args[2]
            if tag not in self.releases:
                return SimpleNamespace(returncode=1, stdout="", stderr="release not found")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"body": self.releases[tag]}), stderr="")
        if args[:2] == ("release", "create"):
            notes = args[args.index("--notes") + 1]
            self.releases[args[2]] = notes.replace("\n", " ")
            return ok
        if args[:2] == ("release", "download"):
            folder = args[args.index("--dir") + 1]
            with open(os.path.join(folder, "demoshop-test.apk"), "wb") as f:
                f.write(self.asset)
            return ok
        raise AssertionError(f"unexpected gh call {args}")

    def of(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=True).stdout


@unittest.skipUnless(shutil.which("git"), "no git")
class Builds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self.tmp.name, "demo-shop")
        os.makedirs(self.ws)
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(PROJECT)
        git(self.ws, "init", "-q", "--initial-branch=main")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "workspace: initial commit")
        git(self.ws, "remote", "add", "origin", f"https://github.com/{SLUG}.git")
        self.apk = make_apk(os.path.join(self.tmp.name, "demoshop-test-1.4.0.apk"))
        with open(self.apk, "rb") as f:
            self.bytes = f.read()
        self.sha = hashlib.sha256(self.bytes).hexdigest()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in ("GITHUB_REPOSITORY", "GITHUB_OUTPUT"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, gh, *args):
        out = io.StringIO()
        with mock.patch.object(wb, "gh", gh), mock.patch.object(wg, "gh", gh), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = wb.main([self.ws, *args])
        return code, out.getvalue()

    def project_text(self):
        with open(os.path.join(self.ws, "project.yaml"), encoding="utf-8") as f:
            return f.read()

    # --- publish

    def test_publish_without_yes_is_a_dry_run(self):
        gh = FakeGh()
        code, out = self.run_(gh, "publish", self.apk)
        self.assertEqual(code, 0, out)
        self.assertIn("apk: com.demoshop.app 1.4.0 (140), minSdk 24, targetSdk 36", out)
        self.assertIn(f"sha256: {self.sha}", out)
        self.assertIn(f"release: build-1.4.0-140 in {SLUG}", out)
        self.assertIn("dry run: nothing done", out)
        self.assertEqual(gh.of("release", "create"), [])
        self.assertEqual(self.project_text(), PROJECT)

    def test_publish_with_yes_creates_the_release_records_and_commits(self):
        gh = FakeGh()
        code, out = self.run_(gh, "publish", self.apk, "--yes")
        self.assertEqual(code, 0, out)
        create = gh.of("release", "create")[0]
        self.assertEqual(create[2:4], ("build-1.4.0-140", os.path.abspath(self.apk)))
        self.assertEqual(create[create.index("--repo") + 1], SLUG)
        self.assertIn(f"sha256: {self.sha}", create[create.index("--notes") + 1])
        build = yaml.safe_load(self.project_text())["mobile"]["build"]
        self.assertEqual(build, {"source": "release build-1.4.0-140", "sha256": self.sha, "version": "1.4.0 (140)"})
        text = self.project_text()
        for kept in ("# the Demo Shop", "# android | ios | web", "# a comment that must stay",
                     "  permissions: {grant: [android.permission.POST_NOTIFICATIONS]}"):
            self.assertIn(kept, text)
        self.assertNotIn("CI artefact", text)
        self.assertEqual(git(self.ws, "log", "-1", "--format=%s").strip(), "build: build-1.4.0-140")
        self.assertIn("not pushed", out)
        # again: the release exists with the same hash, nothing created, nothing to commit
        code, out = self.run_(gh, "publish", self.apk, "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(gh.of("release", "create")), 1)
        self.assertIn("already published with this SHA-256", out)

    def test_publish_inserts_the_build_when_there_is_none(self):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("project: demo-shop\nmobile:\n    platform: android\n    app_id: com.demoshop.app\n")
        self.assertEqual(self.run_(FakeGh(), "publish", self.apk, "--yes")[0], 0)
        self.assertEqual(yaml.safe_load(self.project_text())["mobile"]["build"]["sha256"], self.sha)
        self.assertIn('    build: {source: "release build-1.4.0-140"', self.project_text())

    def test_publish_refusals(self):
        other = make_apk(os.path.join(self.tmp.name, "other.apk"), package="com.example.other")
        prod = make_apk(os.path.join(self.tmp.name, "demoshop-prod.apk"))
        for gh, apk, extra, fragment in [
                (FakeGh(), other, (), "com.example.other"),
                (FakeGh(), prod, (), "production build"),
                (FakeGh(visibility="PUBLIC"), self.apk, ("--i-confirm-private",), "is PUBLIC"),
                (FakeGh(visibility="INTERNAL"), self.apk, (), "is INTERNAL"),
                (FakeGh(visibility=None), self.apk, (), "--i-confirm-private"),
                (FakeGh(releases={"build-1.4.0-140": "sha256: " + "0" * 64}), self.apk, (), "another SHA-256"),
                (FakeGh(releases={"build-1.4.0-140": "no hash here"}), self.apk, (), "without a sha256")]:
            with self.subTest(fragment=fragment):
                code, out = self.run_(gh, "publish", apk, "--yes", *extra)
                self.assertEqual(code, 1, out)
                self.assertIn(fragment, out)
                self.assertEqual(gh.of("release", "create"), [])
                self.assertEqual(self.project_text(), PROJECT)
        self.assertEqual(self.run_(FakeGh(), "publish", prod, "--allow-prod")[0], 0)
        self.assertEqual(self.run_(FakeGh(visibility=None), "publish", self.apk, "--i-confirm-private")[0], 0)

    def test_publish_usage_errors(self):
        broken = make_apk(os.path.join(self.tmp.name, "broken.apk"), manifest=b"plain")
        self.assertEqual(self.run_(FakeGh(), "publish", broken)[0], 2)
        self.assertEqual(self.run_(FakeGh(), "publish", os.path.join(self.tmp.name, "none.apk"))[0], 2)
        git(self.ws, "remote", "remove", "origin")
        code, out = self.run_(FakeGh(), "publish", self.apk)
        self.assertEqual(code, 2)
        self.assertIn("no GitHub origin", out)
        os.environ["GITHUB_REPOSITORY"] = SLUG            # on a runner
        self.assertEqual(self.run_(FakeGh(), "publish", self.apk)[0], 0)

    # --- fetch

    def published(self):
        gh = FakeGh(asset=self.bytes)
        self.assertEqual(self.run_(gh, "publish", self.apk, "--yes")[0], 0)
        return gh

    def test_fetch_checks_the_sha_and_writes_the_output(self):
        gh = self.published()
        output = os.path.join(self.tmp.name, "github_output")
        os.environ["GITHUB_OUTPUT"] = output
        code, out = self.run_(gh, "fetch")
        self.assertEqual(code, 0, out)
        target = os.path.join(self.ws, "app", "build-1.4.0-140.apk")
        with open(target, "rb") as f:
            self.assertEqual(f.read(), self.bytes)
        self.assertEqual(os.listdir(os.path.join(self.ws, "app")), ["build-1.4.0-140.apk"])   # no temp left
        with open(output, encoding="utf-8") as f:
            self.assertEqual(f.read(), f"apk={target}\ntag=build-1.4.0-140\n")
        self.assertIn("sha256 matches mobile.build.sha256", out)

    def test_fetch_refuses_another_build(self):
        gh = self.published()
        gh.asset = b"another build"
        code, out = self.run_(gh, "fetch", "--dest", "downloads")
        self.assertEqual(code, 1)
        self.assertIn("not the recorded build", out)
        self.assertEqual(os.listdir(os.path.join(self.ws, "downloads")), [])

    def test_fetch_another_tag_checks_the_release_notes(self):
        gh = self.published()
        gh.releases["build-1.3.0-130"] = f"sha256: {hashlib.sha256(b'old build').hexdigest()}"
        gh.asset = b"old build"
        self.assertEqual(self.run_(gh, "fetch", "--tag", "build-1.3.0-130")[0], 0)
        self.assertEqual(self.run_(gh, "fetch", "--tag", "build-0.0.1-1")[0], 2)          # no such release

    def test_fetch_without_a_tag_is_a_usage_error(self):
        code, out = self.run_(FakeGh(), "fetch")
        self.assertEqual(code, 2)
        self.assertIn("publish the build first", out)
        self.assertEqual(self.run_(FakeGh(), "fetch", "--tag", "../x")[0], 2)

    def test_gh_missing_or_failing_is_3(self):
        self.published()
        code, out = self.run_(lambda *a: None, "fetch")
        self.assertEqual(code, 3)
        self.assertIn("gh is not installed", out)
        code, out = self.run_(lambda *a: SimpleNamespace(returncode=1, stdout="", stderr="HTTP 502"), "fetch")
        self.assertEqual(code, 3)
        self.assertIn("HTTP 502", out)


if __name__ == "__main__":
    unittest.main()

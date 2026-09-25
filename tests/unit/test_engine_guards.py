"""Guards of the qa-audit engine: bundled axe-core, pinned dependencies, evidence never tracked, no network or
Playwright at import time (docs/qa-audit.md §7, §8, §14)."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import unittest

from qa_audit.modules.accessibility import axe

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src", "qa_audit")


class AxeBundle(unittest.TestCase):
    def test_file_matches_manifest(self):
        ok, detail = axe.verify_bundle()
        self.assertTrue(ok, detail)
        m = axe.manifest()
        with open(os.path.join(axe.VENDOR, "axe.min.js"), "rb") as f:
            data = f.read()
        self.assertEqual(hashlib.sha256(data).hexdigest(), m["sha256"])
        self.assertIn(f"axe v{m['version']}".encode(), data[:200])
        self.assertTrue(m["npm_integrity"].startswith("sha512-"))
        self.assertEqual(m["license"], "MPL-2.0")
        self.assertTrue(os.path.isfile(os.path.join(axe.VENDOR, "LICENSE-axe-core.txt")))

    def test_allowlist_marker_only_on_the_axe_checksums(self):
        # detect-secrets false positives are marked line by line, never by excluding files: only these two lines
        try:
            files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                                   text=True, check=True).stdout.splitlines()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        marker = "pragma: " + "allowlist secret"      # split so this file does not match itself
        found = []
        for rel in files:
            path = os.path.join(ROOT, rel)
            if rel.endswith((".min.js", ".xlsx", ".png")) or not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                found += [(rel, line.split(":")[0].strip()) for line in f if marker in line]
        vendor = "src/qa_audit/modules/accessibility/vendor/axe.yaml"
        self.assertEqual(sorted(found), [(vendor, "npm_integrity"), (vendor, "sha256")])

    def test_vendor_line_endings_are_never_converted(self):
        # with core.autocrlf a Windows checkout would turn axe.min.js into CRLF and break the SHA-256
        with open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8") as f:
            self.assertIn("src/qa_audit/modules/accessibility/vendor/* -text", f.read())

    def test_vendor_files_have_no_crlf(self):
        # vendor/ is -text in .gitattributes: a CRLF written by an editor or a script would be committed as is
        for name in os.listdir(axe.VENDOR):
            with self.subTest(file=name), open(os.path.join(axe.VENDOR, name), "rb") as f:
                self.assertNotIn(b"\r\n", f.read())

    def test_same_version_as_the_workspace_tool(self):
        # tools/a11y (the workspace script) and the engine must report the same axe until the script is retired
        with open(os.path.join(ROOT, "tools", "a11y", "package-lock.json"), encoding="utf-8") as f:
            lock = json.load(f)
        self.assertEqual(lock["packages"]["node_modules/axe-core"]["version"], axe.manifest()["version"])


class Packaging(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            self.py = tomllib.load(f)

    def test_dependencies_pinned(self):
        p = self.py["project"]
        deps = p["dependencies"] + [d for extra in p["optional-dependencies"].values() for d in extra]
        deps += self.py["build-system"]["requires"]
        for d in deps:
            with self.subTest(dep=d):
                self.assertRegex(d, r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$")

    def test_private_and_proprietary(self):
        self.assertIn("Private :: Do Not Upload", self.py["project"]["classifiers"])
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f:
            self.assertIn("All rights reserved", f.read())

    def test_entry_point(self):
        self.assertEqual(self.py["project"]["scripts"]["qa-audit"], "qa_audit.cli:main")

    def test_vendor_files_are_packaged(self):
        self.assertIn("modules/*/vendor/*", self.py["tool"]["setuptools"]["package-data"]["qa_audit"])


class Install(unittest.TestCase):
    def test_install_scripts(self):
        for name in ("install.sh", "install.ps1"):
            with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as f:
                text = f.read()
            with self.subTest(script=name):
                self.assertIn(".[visual]", text)
                self.assertIn("doctor", text)
        try:
            mode = subprocess.run(["git", "ls-files", "-s", "scripts/install.sh"], cwd=ROOT, capture_output=True,
                                  text=True, check=True).stdout.split(" ")[0]
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        if mode:                              # tracked: must be executable on a fresh clone
            self.assertEqual(mode, "100755")


class EvidenceNeverTracked(unittest.TestCase):
    def test_gitignore_covers_output_and_secrets(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8-sig") as f:
            lines = {x.strip() for x in f}
        for entry in ("qa-output/", ".env", "*.env", "screenshots/", "videos/", "traces/", "*.har",
                      "storage-state*.json", "qa-audit.local.yaml"):
            with self.subTest(entry=entry):
                self.assertIn(entry, lines)

    def test_no_tracked_path_under_an_output_folder(self):
        try:
            out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        bad = [p for p in out.splitlines() if re.search(r"(^|/)qa-output/|(^|/)traces/|\.har$|\.webm$", p)]
        self.assertEqual(bad, [])


class NoSideEffectsAtImport(unittest.TestCase):
    def test_core_and_reports_do_not_import_playwright(self):
        code = ("import sys, qa_audit.cli, qa_audit.core.runner, qa_audit.reports, qa_audit.core.registry;"
                "qa_audit.core.registry.discover(use_entry_points=False);"
                "print('playwright' in sys.modules)")
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
        self.assertEqual(out.stdout.strip(), "False")

    def test_no_http_client_in_the_engine(self):
        # the engine talks to the target only through the browser; no telemetry, no other HTTP client
        offenders = []
        for folder, _, files in os.walk(SRC):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(folder, name), encoding="utf-8") as f:
                        text = f.read()
                    if re.search(r"^\s*(import|from)\s+(urllib\.request|requests|http\.client|httpx|socket)\b",
                                 text, re.MULTILINE):
                        offenders.append(name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

"""Configuration of the pre-commit guards (.pre-commit-config.yaml).

python -m unittest discover -s tests   (from the root of qa-assistant)
detect-secrets 1.5.0 opens files without an encoding; on Windows (cp1252) a UTF-8
file with certain characters (e.g. "Á") was skipped silently. The hook must run
in UTF-8 mode and keep the id the CI uses.
"""

import json
import os
import re
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONBOARDING = os.path.join(ROOT, ".claude", "skills", "project-onboarding", "SKILL.md")


def hooks() -> dict:
    with open(os.path.join(ROOT, ".pre-commit-config.yaml"), encoding="utf-8-sig") as f:
        cfg = yaml.safe_load(f)
    return {h["id"]: h for repo in cfg["repos"] for h in repo["hooks"]}


class Guards(unittest.TestCase):
    def test_detect_secrets_in_utf8_mode_with_a_fixed_version(self):
        h = hooks()["detect-secrets"]
        self.assertIn("-X utf8", h.get("entry", ""))
        self.assertIn("detect-secrets==1.5.0", h.get("additional_dependencies", []))
        self.assertEqual(h.get("args"), ["--baseline", ".secrets.baseline"])

    def test_the_guards_exist(self):
        self.assertTrue({"detect-secrets", "denylist", "ruff-check", "golden", "tests"} <= set(hooks()))

    def test_tests_run_when_the_files_they_read_change(self):
        pattern = re.compile(hooks()["tests"]["files"])
        for path in ("adapters/excel/base.py", "scripts/build_dashboard.py", "tests/test_skills.py",
                     ".claude/skills/checkpoint/SKILL.md", "CLAUDE.md", "examples/demo/project.yaml",
                     "docs/project.yaml", "docs/workspace/settings.json", ".pre-commit-config.yaml",
                     "integrations/playwright/qa-audit.ts", "docs/methodology.md", "README.md",
                     "src/qa_audit/cli.py", "pyproject.toml", ".gitignore", "qa_ui/server.py",
                     "qa_ui/static/app.js", "qa_ui/prompts/batch.md"):
            with self.subTest(path=path):
                self.assertTrue(pattern.search(path))
        self.assertFalse(pattern.search("requirements.txt"))

    def test_python_dependencies_with_an_exact_version(self):
        # plan 7.5: the golden depends on openpyxl; a silent update changed it
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
            lines = [x.strip() for x in f if x.strip() and not x.startswith("#")]
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(dependency=line):
                self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$")

    def test_playwright_mcp_with_the_same_fixed_version(self):
        with open(os.path.join(ROOT, "examples", "demo", ".mcp.json"), encoding="utf-8") as f:
            mcp = f.read()
        with open(ONBOARDING, encoding="utf-8-sig") as f:
            skill = f.read()
        versions = set(re.findall(r"@playwright/mcp@([^\s\"]+)", mcp + skill))
        self.assertEqual(len(versions), 1, versions)
        self.assertRegex(versions.pop(), r"^\d+\.\d+\.\d+$")  # never @latest

    def test_denylist_also_on_the_commit_message(self):
        with open(os.path.join(ROOT, ".pre-commit-config.yaml"), encoding="utf-8-sig") as f:
            cfg = yaml.safe_load(f)
        self.assertIn("commit-msg", cfg["default_install_hook_types"])
        self.assertEqual(cfg["default_stages"], ["pre-commit"])      # the other hooks do not run twice
        h = hooks()["denylist-message"]
        self.assertEqual(h["stages"], ["commit-msg"])
        self.assertEqual(h["entry"], hooks()["denylist"]["entry"])

    def test_playwright_mcp_gets_the_env_as_secrets(self):
        # technique-browser.md section 3: the assistant writes the variable's NAME and the MCP puts the value
        with open(os.path.join(ROOT, "examples", "demo", ".mcp.json"), encoding="utf-8") as f:
            args = json.load(f)["mcpServers"]["playwright"]["args"]
        self.assertEqual(args[args.index("--secrets") + 1], ".env")
        self.assertIn("--isolated", args)          # the persistent profile lost clicks (technique-browser.md section 1)
        with open(ONBOARDING, encoding="utf-8-sig") as f:
            skill = f.read()
        self.assertEqual(len(re.findall(r"@playwright/mcp@[^\s]+ --secrets \.env --isolated", skill)), 2)   # Windows and Linux/macOS

    def test_the_encoding_bug_really_exists(self):
        # the reason for -X utf8: the UTF-8 bytes of "Á" cannot be read as cp1252
        with self.assertRaises(UnicodeDecodeError):
            "Área".encode().decode("cp1252")


if __name__ == "__main__":
    unittest.main()

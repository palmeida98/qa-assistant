"""Project skills (.claude/skills): valid and consistent frontmatter.

python -m unittest discover -s tests   (from the root of qa-assistant)
A frontmatter with invalid YAML (e.g. ': ' in the description without quotes) makes
Claude Code show the first line of the body instead of the description.
"""

import os
import re
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, ".claude", "skills")
CLAUDE_MD = os.path.join(ROOT, "CLAUDE.md")


def frontmatter(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        text = f.read().replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        raise AssertionError(f"{path}: no --- frontmatter --- block at the start")
    return yaml.safe_load(m.group(1))


class Skills(unittest.TestCase):
    def names(self):
        return sorted(d for d in os.listdir(SKILLS) if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md")))

    def test_valid_frontmatter_with_name_and_description(self):
        for name in self.names():
            with self.subTest(skill=name):
                fm = frontmatter(os.path.join(SKILLS, name, "SKILL.md"))
                self.assertIsInstance(fm, dict)
                self.assertEqual(fm.get("name"), name)
                self.assertIsInstance(fm.get("description"), str)
                self.assertGreater(len(fm["description"]), 20)

    def test_each_skill_is_in_the_commands_of_claude_md(self):
        with open(CLAUDE_MD, encoding="utf-8-sig") as f:
            commands = f.read()
        for name in self.names():
            with self.subTest(skill=name):
                self.assertIn(f"/{name}", commands)


if __name__ == "__main__":
    unittest.main()

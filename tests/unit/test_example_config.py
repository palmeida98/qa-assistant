"""The fictional example configuration stays valid (examples/qa-audit/; docs/qa-audit-guide.md §4)."""

import os
import re
import unittest

from qa_audit.core import config, registry

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXAMPLE = os.path.join(ROOT, "examples", "qa-audit", "qa-audit.yaml")


class ExampleConfig(unittest.TestCase):
    def test_loads_and_every_module_accepts_it(self):
        mods = registry.discover(use_entry_points=False)
        cfg = config.build(EXAMPLE, module_defaults={n: m.default_config() for n, m in mods.items()})
        for name, module in mods.items():
            with self.subTest(module=name):
                self.assertEqual(module.validate_config(cfg["modules"].get(name) or {}), [])
        from qa_audit.core import auth
        fake_env = {"NEW_PROJECT_QA_USER": "x", "NEW_PROJECT_QA_PASS": "y"}
        self.assertEqual(auth.validate(cfg, fake_env), [])
        self.assertFalse(cfg["safety"]["allow_submit"])
        self.assertEqual(cfg["evidence"]["trace"], "off")

    def test_fictional_and_without_secrets(self):
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(config.find_literal_secrets(config.load_file(EXAMPLE)), [])
        hosts = set(re.findall(r"https?://([^/\s\"']+)", text))
        self.assertTrue(hosts)
        self.assertTrue(all(h.endswith(".example") for h in hosts), hosts)


if __name__ == "__main__":
    unittest.main()

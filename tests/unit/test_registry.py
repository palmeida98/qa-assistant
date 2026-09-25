"""Module discovery and --type selection (docs/qa-audit.md §10)."""

import types
import unittest
from unittest import mock

from qa_audit.core import registry
from qa_audit.core.config import ConfigError
from qa_audit.core.module import AuditModule


class Extra(AuditModule):
    name = "extra-check"
    title = "Extra"


class Registry(unittest.TestCase):
    def test_builtin_accessibility_is_found(self):
        mods = registry.discover(use_entry_points=False)
        self.assertIn("accessibility", mods)
        acc = mods["accessibility"]
        self.assertEqual((acc.title, acc.id_prefix), ("Accessibility", "ACC"))
        self.assertTrue(acc.disclaimer)
        self.assertIn("tags", acc.default_config())
        sec = mods["security"]
        self.assertEqual((sec.title, sec.id_prefix), ("Security", "SEC"))
        self.assertIn("not a penetration test", sec.disclaimer)
        self.assertEqual(mods["performance"].id_prefix, "PERF")
        # readers first, the module that presses keys and resizes the viewport last
        self.assertEqual([m.name for m in registry.select("full", mods)][-1], "accessibility")
        self.assertEqual([m.name for m in registry.select("accessibility,performance", mods)],
                         ["performance", "accessibility"])

    def test_plugin_through_entry_point_needs_no_core_change(self):
        ep = types.SimpleNamespace(name="extra", load=lambda: Extra)
        with mock.patch.object(registry, "entry_points", return_value=[ep]):
            mods = registry.discover()
        self.assertIsInstance(mods["extra-check"], Extra)

    def test_invalid_plugins_are_refused(self):
        class NoName(AuditModule):
            title = "x"

        duplicate = type("Dup", (AuditModule,), {"name": "accessibility", "title": "d"})
        for obj in (object(), NoName, duplicate):
            ep = types.SimpleNamespace(name="bad", load=lambda o=obj: o)
            with (self.subTest(obj=obj), mock.patch.object(registry, "entry_points", return_value=[ep]),
                  self.assertRaises(registry.RegistryError)):
                registry.discover()

    def test_select(self):
        mods = {"accessibility": Extra(), "extra-check": Extra()}
        self.assertEqual(len(registry.select("full", mods)), 2)
        self.assertEqual(len(registry.select(None, mods)), 2)
        self.assertEqual(len(registry.select("accessibility, accessibility", mods)), 1)
        with self.assertRaisesRegex(ConfigError, "planned in task 8.7"):
            registry.select("functional", mods)
        with self.assertRaisesRegex(ConfigError, "unknown type 'nope'.*Available"):
            registry.select("nope", mods)
        with self.assertRaises(ConfigError):
            registry.select("full,accessibility", mods)


if __name__ == "__main__":
    unittest.main()

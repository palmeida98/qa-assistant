"""Configuration: precedence, validation and the no-credentials rule (docs/qa-audit.md §4)."""

import os
import tempfile
import unittest

from qa_audit.core import config
from qa_audit.core.config import ConfigError


def write(tmp, text):
    path = os.path.join(tmp, "qa-audit.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class Precedence(unittest.TestCase):
    def test_defaults_module_file_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "crawl: {max_pages: 7}\nmodules: {accessibility: {max_nodes_per_rule: 2}}\n")
            cfg = config.build(path, {"crawl": {"max_depth": 1}},
                               module_defaults={"accessibility": {"max_nodes_per_rule": 5, "tags": ["wcag2a"]}})
        self.assertEqual(cfg["crawl"]["max_pages"], 7)          # file over default
        self.assertEqual(cfg["crawl"]["max_depth"], 1)          # command line
        self.assertEqual(cfg["crawl"]["delay_ms"], 500)         # default kept
        self.assertEqual(cfg["modules"]["accessibility"], {"max_nodes_per_rule": 2, "tags": ["wcag2a"]})

    def test_defaults_are_not_mutated(self):
        config.build(None, {"crawl": {"exclude": ["x"]}})
        self.assertEqual(config.DEFAULTS["crawl"]["exclude"], [])

    def test_privacy_defaults(self):
        cfg = config.build()
        self.assertEqual(cfg["evidence"]["trace"], "off")
        self.assertFalse(cfg["evidence"]["video"])
        self.assertTrue(cfg["crawl"]["respect_robots"])
        self.assertGreater(cfg["crawl"]["delay_ms"], 0)
        self.assertFalse(cfg["crawl"]["audit_error_pages"])     # error pages are not content unless asked (§5)


class NoCredentials(unittest.TestCase):
    def test_literal_secrets_are_refused(self):
        for text in ("login: {password: hunter2}\n", "api_key: abc\n", "headers: {Authorization: Bearer x}\n",
                     "modules: {x: {token: t}}\n", "auth:\n  - {cookie: s=1}\n"):
            with (self.subTest(text=text), tempfile.TemporaryDirectory() as tmp,
                  self.assertRaisesRegex(ConfigError, "credentials are not allowed")):
                config.load_file(write(tmp, text))

    def test_environment_variable_names_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = config.load_file(write(tmp, "login: {password_env: SHOP_PASSWORD, bypass: true}\n"))
        self.assertEqual(data["login"]["password_env"], "SHOP_PASSWORD")

    def test_found_paths(self):
        self.assertEqual(config.find_literal_secrets({"a": {"secret": "s", "secret_env": "S"}}), ["a.secret"])


class Validation(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(config.validate_url(" https://shop.example/a#top "), "https://shop.example/a")
        for bad in ("shop.example", "ftp://shop.example", "file:///etc/passwd", "javascript:alert(1)", "https://"):
            with self.subTest(url=bad), self.assertRaises(ConfigError):
                config.validate_url(bad)

    def test_viewport(self):
        self.assertEqual(config.parse_viewport("390x844"), {"width": 390, "height": 844})
        with self.assertRaises(ConfigError):
            config.parse_viewport("big")

    def test_invalid_values(self):
        for over in ({"browser": {"engine": "ie"}}, {"crawl": {"max_pages": 0}}, {"crawl": {"exclude": ["("]}},
                     {"evidence": {"trace": "always"}}, {"output": {"formats": ["pdf"]}}, {"fail_on": "blocker"},
                     {"crawl": {"audit_error_pages": "yes"}}):
            with self.subTest(over=over), self.assertRaises(ConfigError):
                config.build(None, over)

    def test_missing_or_bad_file(self):
        with self.assertRaises(ConfigError):
            config.build("does-not-exist.yaml")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ConfigError):
            config.load_file(write(tmp, "- a list\n"))


if __name__ == "__main__":
    unittest.main()

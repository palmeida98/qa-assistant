"""Authentication configuration (docs/qa-audit.md §20). No browser."""

import unittest

from qa_audit.core import auth, config

ENV = {"SHOP_QA_USER": "someone", "SHOP_QA_PASS": "not-a-real-one"}
FORM = {"method": "form", "login_url": "/login", "username_env": "SHOP_QA_USER",
        "pass_env": "SHOP_QA_PASS", "success_url": "/account"}


def cfg_with(auth_section, **over):
    return config.build(None, config.deep_merge({"auth": auth_section}, over))


class Validation(unittest.TestCase):
    def test_valid_form_and_manual(self):
        self.assertEqual(auth.validate(cfg_with(FORM), ENV), [])
        manual = {"method": "manual", "login_url": "https://sso.example/login", "success_selector": "#logout"}
        self.assertEqual(auth.validate(cfg_with(manual), {}), [])      # manual needs no credentials

    def test_no_auth_is_fine(self):
        self.assertEqual(auth.validate(config.build(), {}), [])
        self.assertFalse(auth.configured(config.build()))

    def test_problems(self):
        cases = {
            "method": {**FORM, "method": "basic"},
            "storage-state is planned": {**FORM, "method": "storage-state"},
            "success check": {k: v for k, v in FORM.items() if k != "success_url"},
            "login_url": {k: v for k, v in FORM.items() if k != "login_url"},
            "username_env": {k: v for k, v in FORM.items() if k != "username_env"},
            "regular expression": {**FORM, "success_url": "("},
            "would look like a success": {**FORM, "success_url": "/"},
            "positive integer": {**FORM, "manual_timeout_s": 0},
        }
        for label, section in cases.items():
            with self.subTest(case=label):
                self.assertTrue(auth.validate(cfg_with(section), ENV), label)

    def test_missing_environment_variable_names_it_never_a_value(self):
        problems = auth.validate(cfg_with(FORM), {"SHOP_QA_USER": "someone"})
        self.assertEqual(problems, ["environment variable SHOP_QA_PASS (pass_env) is not set"])

    def test_trace_is_refused_with_auth(self):
        problems = auth.validate(cfg_with(FORM, evidence={"trace": "on"}), ENV)
        self.assertTrue(any("trace" in p for p in problems))

    def test_literal_credentials_in_the_file_are_refused_by_the_loader(self):
        self.assertEqual(config.find_literal_secrets({"auth": {"password": "x"}}), ["auth.password"])
        self.assertEqual(config.find_literal_secrets({"auth": FORM}), [])


class Helpers(unittest.TestCase):
    def test_secrets_for_the_redactor(self):
        self.assertEqual(auth.secrets(cfg_with(FORM), ENV), ENV)
        self.assertEqual(auth.secrets(config.build(), ENV), {})

    def test_expired_pattern_defaults_to_the_login_path(self):
        p = auth.expired_pattern(cfg_with(FORM), "https://shop.example/")
        self.assertTrue(p.search("https://shop.example/login?next=/account"))
        self.assertTrue(p.search("https://shop.example/login"))
        self.assertFalse(p.search("https://shop.example/login-help"))
        custom = auth.expired_pattern(cfg_with({**FORM, "expired_url": "/signin"}), "https://shop.example/")
        self.assertTrue(custom.search("https://shop.example/signin"))
        self.assertIsNone(auth.expired_pattern(config.build(), "https://shop.example/"))


class CrawlPages(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(config.build(None, {"crawl": {"pages": ["/a", "https://shop.example/b"]}})["crawl"]["pages"],
                         ["/a", "https://shop.example/b"])
        for bad in ({"crawl": {"pages": "/a"}}, {"crawl": {"pages": [""]}}, {"crawl": {"follow_links": "no"}}):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.build(None, bad)


if __name__ == "__main__":
    unittest.main()

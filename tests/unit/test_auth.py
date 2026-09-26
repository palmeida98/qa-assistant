"""Authentication configuration and saved sessions (docs/qa-audit.md §20, §20.1). No browser."""

import json
import os
import tempfile
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


SAVED = {"method": "storage-state", "login_url": "/login", "success_url": "/account"}
TARGET = "https://shop.example/"


class SavedSessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "sessions", "shop.json")

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, **auth_over):
        return cfg_with({**SAVED, "state_file": self.state, **auth_over},
                        output={"directory": os.path.join(self.tmp.name, "out")})

    def write_state(self, cookies):
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump({"cookies": cookies, "origins": []}, f)

    def test_default_path_is_per_host_outside_any_project(self):
        path = auth.state_path(cfg_with(SAVED), "https://shop.example:8443/a")
        self.assertEqual(path, os.path.join(os.path.expanduser("~"), ".qa-audit", "sessions", "shop.example_8443.json"))

    def test_audit_needs_the_saved_file_and_names_the_command_to_create_it(self):
        problems = auth.validate(self.cfg(), {}, target=TARGET)
        self.assertEqual(len(problems), 1)
        self.assertIn("qa-audit login https://shop.example/", problems[0])
        self.write_state([])
        self.assertEqual(auth.validate(self.cfg(), {}, target=TARGET), [])

    def test_login_mode(self):
        self.assertEqual(auth.validate(self.cfg(), {}, target=TARGET, mode="login"), [])     # manual by default
        form = self.cfg(login_method="form", username_env="SHOP_QA_USER", pass_env="SHOP_QA_PASS")
        self.assertEqual(auth.validate(form, ENV, target=TARGET, mode="login"), [])
        self.assertTrue(any("SHOP_QA_PASS" in p for p in auth.validate(form, {"SHOP_QA_USER": "x"}, target=TARGET,
                                                                         mode="login")))
        self.assertTrue(auth.validate(cfg_with(FORM), ENV, target=TARGET, mode="login"))     # not storage-state
        self.assertTrue(auth.validate(config.build(), {}, target=TARGET, mode="login"))      # no auth at all
        self.assertTrue(auth.validate(self.cfg(login_method="sso"), {}, target=TARGET, mode="login"))

    def test_state_file_never_in_the_output_folder_or_a_git_working_tree(self):
        inside_out = self.cfg(state_file=os.path.join(self.tmp.name, "out", "s.json"))
        self.assertTrue(any("output folder" in p for p in auth.validate(inside_out, {}, target=TARGET, mode="login")))
        os.makedirs(os.path.join(self.tmp.name, "repo", ".git"))
        in_repo = self.cfg(state_file=os.path.join(self.tmp.name, "repo", "sub", "s.json"))
        self.assertTrue(any("Git working tree" in p for p in auth.validate(in_repo, {}, target=TARGET)))

    def test_cookie_values_go_to_the_redactor(self):
        self.write_state([{"name": "sid", "value": "fictional-session-id"}, {"name": "lang", "value": "en"}])
        self.assertEqual(auth.secrets(self.cfg(), {}, target=TARGET), {"cookie:sid": "fictional-session-id"})

    def test_only_form_and_manual_log_in_during_the_audit(self):
        self.assertTrue(auth.logs_in(cfg_with(FORM)))
        self.assertFalse(auth.logs_in(self.cfg()))
        self.assertEqual(auth.login_method(SAVED), "manual")
        self.assertEqual(auth.login_method({**SAVED, "login_method": "form"}), "form")

    def test_list_and_delete(self):
        root = os.path.join(self.tmp.name, "sessions")
        self.write_state([])
        with open(os.path.join(root, "other.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertEqual([h for h, _, _ in auth.list_sessions(root)], ["other", "shop"])
        self.assertEqual(auth.delete_sessions(root, "shop"), ["shop"])
        self.assertEqual(auth.delete_sessions(root, "all"), ["other"])
        self.assertEqual(auth.list_sessions(root), [])


class CrawlPages(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(config.build(None, {"crawl": {"pages": ["/a", "https://shop.example/b"]}})["crawl"]["pages"],
                         ["/a", "https://shop.example/b"])
        for bad in ({"crawl": {"pages": "/a"}}, {"crawl": {"pages": [""]}}, {"crawl": {"follow_links": "no"}}):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                config.build(None, bad)


if __name__ == "__main__":
    unittest.main()

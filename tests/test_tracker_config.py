"""adapters/tracker (plan 10.1): the jira: / azure_devops: sections, their validation (shared with
scripts/validate_project.py), the credentials by variable name, and the batch's validation and status mapping
(technique-jira-xray.md sections 1, 2.6 and 4.1). Fictional Demo Shop data only.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import copy
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))]

from tracker_kit import AZURE, ENV, JIRA, PROJECT, RUN, Workspace, results

from adapters.excel.base import batch_hash
from adapters.tracker import config as tc
from adapters.tracker.base import prepare_batch, prepare_cases, split_evidence
from adapters.tracker.errors import ConfigError, InvalidInput


def proj_with(tracker="jira", **section_changes):
    p = copy.deepcopy(PROJECT)
    if tracker == "azure-devops":
        p.pop("jira")
        p["tracker"] = "azure-devops"
        p["azure_devops"] = copy.deepcopy(AZURE)
    name = tc.SECTIONS[tracker]
    for dotted, value in section_changes.items():
        node = p[name]
        *parents, last = dotted.split("__")
        for k in parents:
            node = node.setdefault(k, {})
        if value is DELETE:
            node.pop(last, None)
        else:
            node[last] = value
    return p


DELETE = object()


class Section(unittest.TestCase):
    def test_the_fixtures_are_valid(self):
        for tracker in ("jira", "azure-devops"):
            with self.subTest(tracker=tracker):
                problems, _ = tc.validate_section(proj_with(tracker))
                self.assertEqual(problems, [])

    def test_statuses_not_sent_are_counted_as_a_warning(self):
        _, warnings = tc.validate_section(proj_with())
        self.assertTrue(any("2 status(es) not sent" in w and "Blocked" in w and "N/A" in w for w in warnings),
                        warnings)

    def test_required_keys_of_jira(self):
        p = proj_with(site=DELETE, project_key=DELETE, token_var=DELETE, xray__client_secret_var=DELETE,
                      results__statuses=DELETE)
        text = " | ".join(tc.validate_section(p)[0])
        for key in ("jira.site", "jira.project_key", "jira.token_var", "jira.xray.client_secret_var",
                    "jira.results.statuses"):
            self.assertIn(key, text)

    def test_urls_must_be_https_and_the_site_without_a_path(self):
        for site in ("http://demo-shop.atlassian.net", "https://demo-shop.atlassian.net/jira", "demo-shop"):
            with self.subTest(site=site):
                self.assertTrue(any("jira.site" in p for p in tc.validate_section(proj_with(site=site))[0]))
        problems, _ = tc.validate_section(proj_with("azure-devops", organization_url="http://dev.azure.com/x"))
        self.assertTrue(any("organization_url" in p for p in problems), problems)

    def test_credentials_are_variable_names_never_values(self):
        problems, _ = tc.validate_section(proj_with(token_var="abc def-123", user_var="qa@demo-shop.test"))
        self.assertEqual(sum("not a .env variable name" in p for p in problems), 2, problems)

    def test_question_is_never_mapped(self):
        p = proj_with(results__statuses={**JIRA["results"]["statuses"], "Question": "TODO"})
        self.assertTrue(any("Question" in x and "8.5" in x for x in tc.validate_section(p)[0]))

    def test_id_source_needs_its_field_or_prefix(self):
        self.assertTrue(any("id_field" in p for p in tc.validate_section(proj_with(cases__id_source="field"))[0]))
        self.assertTrue(any("id_label_prefix" in p
                            for p in tc.validate_section(proj_with(cases__id_source="label"))[0]))
        self.assertTrue(any("id_source" in p for p in tc.validate_section(proj_with(cases__id_source="uuid"))[0]))
        p = proj_with("azure-devops", cases__id_source="tag")
        self.assertTrue(any("id_tag_prefix" in x for x in tc.validate_section(p)[0]))

    def test_label_prefix_mapping(self):
        problems, _ = tc.validate_section(proj_with(cases__fields={"role": "labels:", "area": "components"}))
        self.assertTrue(any("cases.fields.role" in p for p in problems), problems)

    def test_azure_devops_outcomes_from_the_closed_list(self):
        p = proj_with("azure-devops", results__outcomes={"Pass": "Passed", "Fail": "Timeout", "Not run": "skip"})
        problems, _ = tc.validate_section(p)
        self.assertTrue(any("Timeout" in x for x in problems), problems)
        self.assertEqual(len(problems), 1)

    def test_azure_devops_plan_and_suites(self):
        problems, _ = tc.validate_section(proj_with("azure-devops", cases__plan_id=DELETE,
                                                    cases__suite_ids=["13"], api_version=7.1))
        text = " | ".join(problems)
        for key in ("cases.plan_id", "cases.suite_ids", "api_version"):
            self.assertIn(key, text)

    def test_screenshots_only_with_the_text_screenshot_policy(self):
        _, warnings = tc.validate_section(proj_with(results__attach_evidence=True))
        self.assertTrue(any("attach_evidence" in w and "text+screenshot" in w for w in warnings), warnings)
        p = proj_with(results__attach_evidence=True)
        p["evidence"] = "text+screenshot"
        self.assertFalse(any("attach_evidence" in w for w in tc.validate_section(p)[1]))

    def test_a_missing_section_is_not_reported_while_the_back_end_is_a_stub(self):
        p = copy.deepcopy(PROJECT)
        p.pop("jira")
        self.assertTrue(tc.validate_section(p)[0])            # jira is built (plan 10.2): the section is required
        with mock.patch.object(tc, "IMPLEMENTED", set()):
            self.assertEqual(tc.validate_section(p), ([], []))

    def test_other_trackers_are_not_checked_here(self):
        self.assertEqual(tc.validate_section({"tracker": "excel"}), ([], []))


class Load(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def test_defaults_are_merged(self):
        c = tc.load(self.ws.path)
        self.assertEqual((c.tracker, c.section), ("jira", "jira"))
        self.assertEqual(c.cfg["cases"]["id_source"], "key")
        self.assertEqual(c.cfg["xray"]["base_url"], "https://xray.cloud.getxray.app")
        self.assertEqual(c.cfg["bugs"]["issue_type"], "Bug")
        self.assertEqual(c.status_map["Pass"], "PASSED")

    def test_azure_devops_defaults(self):
        self.ws.project(proj_with("azure-devops"))
        c = tc.load(self.ws.path)
        self.assertEqual((c.cfg["api_version"], c.cfg["cases"]["id_source"]), ("7.1", "id"))
        self.assertEqual(c.status_map_name, "azure_devops.results.outcomes")

    def test_excel_and_unknown_trackers_are_refused(self):
        for tracker in ("excel", "trello"):
            with self.subTest(tracker=tracker):
                self.ws.project({**PROJECT, "tracker": tracker})
                with self.assertRaises(ConfigError):
                    tc.load(self.ws.path)

    def test_an_invalid_section_lists_every_problem(self):
        self.ws.project(proj_with(site="http://x", project_key=DELETE))
        with self.assertRaises(ConfigError) as e:
            tc.load(self.ws.path)
        self.assertGreaterEqual(len(e.exception.problems), 2)

    def test_credentials_from_the_env_by_name(self):
        c = tc.load(self.ws.path)
        self.assertEqual(tc.credentials(c), ENV)

    def test_a_missing_credential_is_named_never_valued(self):
        self.ws.env({k: v for k, v in ENV.items() if k != "DS_XRAY_CLIENT_SECRET"})
        with self.assertRaises(ConfigError) as e:
            tc.credentials(tc.load(self.ws.path))
        self.assertIn("DS_XRAY_CLIENT_SECRET", str(e.exception))
        for value in ENV.values():
            self.assertNotIn(value, str(e.exception))


class BatchValidation(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)
        self.config = tc.load(self.ws.path)

    def problems(self, items, **kw):
        with self.assertRaises(InvalidInput) as e:
            prepare_batch(self.ws.batch("batch_x", items, **kw), self.config)
        return " | ".join(e.exception.problems)

    def test_a_valid_batch_and_what_is_not_sent(self):
        b = prepare_batch(self.ws.batch("batch_20260928_01", results(("DS-1", "Pass"), ("DS-2", "Fail"),
                                                                     ("DS-3", "Blocked"), ("DS-4", "N/A"),
                                                                     ("DS-5", "Not run"))), self.config)
        self.assertEqual(b.batch_id, "batch_20260928_01")
        self.assertEqual([(i.tc, i.target) for i in b.to_send],
                         [("DS-1", "PASSED"), ("DS-2", "FAILED"), ("DS-5", "TODO")])
        self.assertEqual([i.tc for i in b.not_sent], ["DS-3", "DS-4"])   # skip and unmapped: not an error
        self.assertEqual(b.run, RUN)

    def test_the_hash_is_the_excel_adapters(self):
        items = results(("DS-1", "Pass"))
        b = prepare_batch(self.ws.batch("b1", items), self.config)
        self.assertEqual(b.hash, batch_hash(items))
        b = prepare_batch(self.ws.batch("b2", items, translated_from="abc"), self.config)
        self.assertEqual(b.hash, "abc")

    def test_question_and_a_status_outside_the_map_are_refused(self):
        text = self.problems(results(("DS-1", "Question: which rounding?"), ("DS-2", "Passed")))
        self.assertIn("8.5", text)
        self.assertIn("'Passed' is not a key of jira.results.statuses", text)

    def test_the_run_block_is_required(self):
        self.assertIn("'run' is missing", self.problems(results(("DS-1", "Pass")), run=None))
        text = self.problems(results(("DS-1", "Pass")), run={"environment": "prod"})
        self.assertIn("'prod' is not a key of environments", text)
        self.assertIn("run.build is missing", text)

    def test_empty_duplicates_and_missing_fields(self):
        self.assertIn("empty batch", self.problems([]))
        text = self.problems(results(("DS-1", "Pass"), ("DS-1", "Fail")) + [{"status": "Pass"}, {"tc": "DS-9"}])
        self.assertIn("case repeated in the batch: ['DS-1']", text)
        self.assertIn("'tc' is missing", text)
        self.assertIn("'status' is missing", text)

    def test_new_defect_minimum_and_priority(self):
        items = results(("DS-1", "Fail"), ("DS-2", "Fail"))
        items[0]["new_defect"] = {"title": "Expired coupon accepted"}
        items[1]["new_defect"] = {"title": "t", "expected": "e", "observed": "o", "priority": "Blocker"}
        text = self.problems(items)
        self.assertIn("misses ['expected', 'observed']", text)
        self.assertIn("'Blocker' outside", text)

    def test_bug_status(self):
        items = results(("DS-1", "Pass"), ("DS-2", "Pass"))
        items[0]["bug_status"] = "Done"
        items[1].update(bug="DS-100", bug_status="Closed")
        text = self.problems(items)
        self.assertIn("bug_status without a 'bug'", text)
        self.assertIn("'Closed' outside jira.bugs.statuses", text)

    def test_evidence_only_inside_evidence(self):
        self.ws.file("evidence/20260928/DS-1.txt", "log")
        self.ws.file("notes.txt", "x")
        items = results(("DS-1", "Fail"), ("DS-2", "Fail"), ("DS-3", "Fail"))
        items[0]["evidence"] = ["evidence/20260928/DS-1.txt"]
        items[1]["evidence"] = "notes.txt"
        items[2]["evidence"] = ["evidence/20260928/missing.png"]
        text = self.problems(items)
        self.assertIn("'notes.txt' is outside", text)
        self.assertIn("missing.png' does not exist", text)
        self.assertNotIn("DS-1.txt", text)

    def test_screenshots_are_not_sent_by_default(self):
        files = ["evidence/20260928/a.png", "evidence/20260928/a.txt"]
        send, skipped = split_evidence(self.ws.path, files, self.config)
        self.assertEqual(send, [])
        self.assertEqual(len(skipped), 2)
        self.config.cfg["results"]["attach_evidence"] = True
        send, skipped = split_evidence(self.ws.path, files, self.config)
        self.assertEqual((len(send), len(skipped)), (1, 1))
        self.assertIn("text+screenshot", skipped[0])
        self.config.project["evidence"] = "text+screenshot"
        self.assertEqual(len(split_evidence(self.ws.path, files, self.config)[0]), 2)

    def test_export_cases_input(self):
        path = self.ws.file("new_cases.json", {"cases": [{"id": "NEW-1", "title": "Coupon"},
                                                         {"id": "TC-1", "title": ""}, {"id": "NEW-1",
                                                                                       "title": "x"}]})
        with self.assertRaises(InvalidInput) as e:
            prepare_cases(path, self.config)
        text = " | ".join(e.exception.problems)
        self.assertIn("provisional id NEW-<n>", text)
        self.assertIn("'title' is missing", text)
        self.assertIn("repeated", text)


if __name__ == "__main__":
    unittest.main()

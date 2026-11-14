"""scripts/spec_refs.py and its use in `pbi_state.py check` (the resume of /test-pbi; docs/technique-specs.md sections
4 and 5). A fictional shop's specification in a temporary folder; nothing real is read or written.

python -m unittest tests.test_spec_refs   (from the root of qa-assistant)
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import typing
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import pbi_state as ps
import spec_refs as sr

CART = """# Cart

1. <a id="r-00000001"></a>**Remove**
   1. <a id="r-00000002"></a>*If* the shopper confirms: the item leaves the cart.
   2. <a id="r-00000003"></a>*If* the shopper cancels: nothing changes and the item stays.

2. <a id="r-00000004"></a>**Totals.** The total is the sum of the lines.
"""
PAY = """# Payment

1. <a id="r-00000010"></a>The shopper pays once per order.
2. <a id="r-00000011"></a>**Long rule**
   1. <a id="r-00000012"></a>""" + ("A very long condition that goes on and on. " * 12) + """
"""
INDEX = "# Index\n\n| Spec | About |\n|---|---|\n| [Cart](specs/cart.md) | The cart. |\n"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def export(root, cart=CART, pay=PAY, cart_rev="aaa1111", pay_rev="bbb2222", extra=None):
    write(os.path.join(root, "_index.md"), INDEX)
    write(os.path.join(root, "specs", "cart.md"), cart)
    if pay is not None:
        write(os.path.join(root, "specs", "pay.md"), pay)
    files = [{"path": "specs/cart.md", "file_revision": cart_rev}]
    if pay is not None:
        files.append({"path": "specs/pay.md", "file_revision": pay_rev})
    for path, text in (extra or {}).items():
        write(os.path.join(root, path), text)
        files.append({"path": path, "file_revision": "ccc3333"})
    write(os.path.join(root, "MANIFEST.json"),
          json.dumps({"snapshot": {"revision": "snap-" + cart_rev}, "files": files}))


class SpecRefs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self.tmp.name, "ws")
        self.v1 = os.path.join(self.ws, "docs", "spec", "v1")
        self.v2 = os.path.join(self.tmp.name, "v2")
        os.makedirs(self.ws)
        write(os.path.join(self.ws, "project.yaml"),
              'project: "Demo Shop"\ntracker: "excel"\nspecs:\n  kind: "export"\n  location: "docs/spec/v1"\n')
        export(self.v1)
        self.source = sr.Source("export", self.v1)

    def tearDown(self):
        self.tmp.cleanup()

    # --- the rule's text

    def test_a_rule_is_its_line_and_the_lines_indented_below_it(self):
        text = sr.rule_block(CART, "r-00000001")
        self.assertTrue(text.startswith("**Remove**"))
        self.assertIn("r-00000003", text)
        self.assertNotIn("Totals", text)
        self.assertEqual(sr.rule_block(CART, "r-00000003"),
                         '*If* the shopper cancels: nothing changes and the item stays.')
        self.assertIsNone(sr.rule_block(CART, "r-0000ffff"))

    def test_cite_copies_the_text_and_the_revision_from_the_specification(self):
        ref = sr.cite(self.source, "specs/cart.md", "r-00000003")
        self.assertEqual(ref["text"], "*If* the shopper cancels: nothing changes and the item stays.")
        self.assertEqual(ref["file_revision"], "aaa1111")
        self.assertEqual(ref["text_sha256"], sr.digest(ref["text"]))
        with self.assertRaises(sr.SpecError):
            sr.cite(self.source, "specs/cart.md", "r-0000ffff")

    # --- the plan

    def plan(self, scenarios, name="plan.md", legacy=False):
        refs = {sid: [sr.cite(self.source, p, r) for p, r in rules] for sid, rules in scenarios.items()}
        doc = {"source": {"kind": "export", "revision": "snap-aaa1111"},
               "scenarios": refs if legacy else {s: {"title": f"Scenario {s}", "refs": v} for s, v in refs.items()}}
        body = "# Plan\n\n```json spec_refs\n" + json.dumps(doc, ensure_ascii=False, indent=1) + "\n```\n"
        write(os.path.join(self.ws, "analysis", name), body)
        return os.path.join(self.ws, "analysis", name)

    SCEN: typing.ClassVar[dict] = {"P1": [("specs/cart.md", "r-00000003")], "P2": [("specs/cart.md", "r-00000002")],
            "P3": [("specs/pay.md", "r-00000010")]}

    def compare(self, plan, root):
        return sr.compare(sr.plan_refs(plan), sr.Source("export", root))

    def states(self, result):
        return {s: v["state"] for s, v in result["scenarios"].items()}

    def test_same_revision_nothing_to_review(self):
        r = self.compare(self.plan(self.SCEN), self.v1)
        self.assertEqual(self.states(r), {"P1": "reusable", "P2": "reusable", "P3": "reusable"})
        self.assertEqual(sr.exit_code(r), 0)

    def test_another_revision_with_the_same_text_stays_reusable(self):
        plan = self.plan(self.SCEN)
        export(self.v2, cart_rev="aaa9999", pay_rev="bbb9999")
        r = self.compare(plan, self.v2)
        self.assertEqual(set(self.states(r).values()), {"reusable"})
        self.assertEqual({x["status"] for x in r["rules"]}, {"same-text-new-revision"})
        self.assertIn("Same text, another revision of the file (nothing to review)", "\n".join(sr.render(r)))
        self.assertEqual(sr.exit_code(r), 0)

    def test_a_changed_rule_flags_only_the_scenarios_that_cite_it(self):
        plan = self.plan(self.SCEN)
        export(self.v2, cart=CART.replace("nothing changes and the item stays", "nothing changes"), cart_rev="aaa9999")
        r = self.compare(plan, self.v2)
        self.assertEqual(self.states(r), {"P1": "review", "P2": "reusable", "P3": "reusable"})
        self.assertEqual(sr.exit_code(r), 1)
        text = "\n".join(sr.render(r))
        self.assertIn("«*If* the shopper cancels: nothing changes and the item stays.»", text)  # before, word for word
        self.assertIn("«*If* the shopper cancels: nothing changes.»", text)                      # now, word for word
        self.assertIn("Scenarios to review BEFORE the plan is reused: P1", text)
        self.assertIn("reusable as they are (every cited rule has the same text): P2, P3", text)

    def test_a_change_in_a_sub_rule_flags_the_scenario_that_cites_the_parent(self):
        plan = self.plan({"P1": [("specs/cart.md", "r-00000001")], "P2": [("specs/cart.md", "r-00000004")]})
        export(self.v2, cart=CART.replace("the item leaves the cart", "the item is removed"))
        self.assertEqual(self.states(self.compare(plan, self.v2)), {"P1": "review", "P2": "reusable"})

    def test_a_removed_rule_flags_its_scenarios(self):
        plan = self.plan(self.SCEN)
        export(self.v2, cart=CART.replace('   2. <a id="r-00000003"></a>*If* the shopper cancels: nothing changes and '
                                          'the item stays.\n', ""))
        r = self.compare(plan, self.v2)
        self.assertEqual(self.states(r)["P1"], "review")
        self.assertIn("REMOVED specs/cart.md · r-00000003", "\n".join(sr.render(r)))
        self.assertEqual(sr.exit_code(r), 1)

    def test_a_removed_file_is_removed_and_a_moved_rule_says_where(self):
        plan = self.plan(self.SCEN)
        export(self.v2, pay=None)
        r = self.compare(plan, self.v2)
        self.assertEqual(self.states(r)["P3"], "review")
        self.assertTrue(any(x["status"] == "removed" and "not in this version" in x["reason"] for x in r["rules"]))
        export(self.v2, cart=CART.replace('   2. <a id="r-00000003"></a>*If* the shopper cancels: nothing changes and '
                                          'the item stays.\n', ""),
               extra={"specs/cart2.md": '1. <a id="r-00000003"></a>*If* the shopper cancels: nothing changes and the '
                                        'item stays.\n'})
        r = self.compare(plan, self.v2)
        moved = [x for x in r["rules"] if x["status"] == "moved"]
        self.assertEqual(moved[0]["moved_to"], "specs/cart2.md")
        self.assertEqual(self.states(r)["P1"], "review")

    def test_a_source_that_cannot_be_read_leaves_everything_pending_never_unchanged(self):
        plan = self.plan(self.SCEN)
        r = self.compare(plan, os.path.join(self.tmp.name, "missing"))
        self.assertEqual(set(self.states(r).values()), {"pending"})
        self.assertEqual(sr.exit_code(r), 3)
        self.assertEqual(len(sr.render(r)), 2)  # one line for the source, not one per rule
        self.assertIn("NOT VERIFIED", "\n".join(sr.render(r)))
        other = sr.compare(sr.plan_refs(plan), sr.Source("git", None))
        self.assertEqual(set(self.states(other).values()), {"pending"})
        self.assertIn("cannot be read by this script", "\n".join(sr.render(other)))

    def test_a_file_that_cannot_be_read_is_not_verified(self):
        plan = self.plan(self.SCEN)
        export(self.v2)
        with open(os.path.join(self.v2, "specs", "pay.md"), "wb") as f:
            f.write(b"\xff\xfe\x00\xd8 not utf-8")
        r = self.compare(plan, self.v2)
        self.assertEqual(self.states(r), {"P1": "reusable", "P2": "reusable", "P3": "pending"})
        self.assertEqual(sr.exit_code(r), 3)

    def test_a_reference_without_the_previous_version_is_not_verified(self):
        plan = self.plan(self.SCEN)
        doc = sr.plan_refs(plan)
        for ref in doc["scenarios"]["P2"]["refs"]:
            ref.pop("text"), ref.pop("text_sha256")
        r = sr.compare(doc, self.source)
        self.assertEqual(self.states(r)["P2"], "pending")
        self.assertIn("did not record the text or the hash", "\n".join(sr.render(r)))

    def test_the_shape_of_the_pilot_and_a_hash_only_reference_are_compared(self):
        plan = self.plan(self.SCEN, legacy=True)
        doc = sr.plan_refs(plan)
        for sc in doc["scenarios"].values():
            for ref in sc["refs"]:
                ref.pop("text")
        export(self.v2, cart=CART.replace("nothing changes and the item stays", "nothing changes"))
        r = sr.compare(doc, sr.Source("export", self.v2))
        self.assertEqual(self.states(r), {"P1": "review", "P2": "reusable", "P3": "reusable"})
        self.assertIn("(not recorded)", "\n".join(sr.render(r)))  # the old text was not kept: said, not invented

    def test_a_long_rule_is_cited_by_reference_not_retyped(self):
        plan = self.plan({"P1": [("specs/pay.md", "r-00000011")]})
        export(self.v2, pay=PAY.replace("goes on", "continues"))
        text = "\n".join(sr.render(self.compare(plan, self.v2)))
        self.assertIn("long rule", text)
        self.assertNotIn("A very long condition", text)

    def test_a_plan_without_spec_refs_is_readable_and_says_so(self):
        write(os.path.join(self.ws, "analysis", "old.md"), "# Old plan\n\nNo references here.\n")
        self.assertIsNone(sr.plan_refs(os.path.join(self.ws, "analysis", "old.md")))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sr.main([self.ws, "compare", "analysis/old.md"])
        self.assertEqual((code, out.getvalue().strip()), (0, "no spec_refs in the plan: nothing to compare"))

    def test_a_project_without_specs_compares_nothing(self):
        write(os.path.join(self.ws, "project.yaml"), 'project: "Demo Shop"\ntracker: "excel"\n')
        self.assertIsNone(sr.source_from(self.ws))
        self.plan(self.SCEN)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sr.main([self.ws, "compare", "analysis/plan.md"])
        self.assertEqual((code, out.getvalue().strip()), (0, "the workspace has no specs: section: nothing to compare"))

    # --- the resume of /test-pbi

    def run_(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ps.main([self.ws, *args])
        return code, out.getvalue()

    def started(self, plan="analysis/plan.md"):
        self.run_("start", "1234", "--platform", "android")
        for step in ("context", "analyze", "plan"):
            self.run_("mark", "1234", step, "done", "--ref", "x")
        args = ["baseline", "1234", "--ticket", "1234=5", "--build", "android=1.0 (1)"]
        return self.run_(*args, *(["--spec-plan", plan] if plan else []))

    CHECK = ("check", "1234", "--ticket", "1234=5", "--build", "android=1.0 (1)")

    def test_resume_with_the_same_specification_changes_nothing(self):
        self.plan(self.SCEN)
        self.started()
        code, out = self.run_(*self.CHECK)
        self.assertEqual(code, 0, out)
        self.assertIn("nothing changed since the baseline", out)

    def test_resume_names_the_scenarios_to_review_before_reusing_the_plan(self):
        self.plan(self.SCEN)
        self.started()
        export(self.v2, cart=CART.replace("nothing changes and the item stays", "nothing changes"), cart_rev="zzz")
        code, out = self.run_(*self.CHECK, "--spec-root", self.v2)
        self.assertEqual(code, 1, out)
        self.assertIn("CHANGED spec rule r-00000003", out)
        self.assertIn("scenarios to review before the plan is reused: P1", out)
        self.assertIn("SPEC Scenarios reusable as they are (every cited rule has the same text): P2, P3", out)
        self.assertIn("the steps stay as they are until reviewed", out)
        # --reopen puts the plan back to pending and leaves what was not reached alone
        code, out = self.run_(*self.CHECK, "--spec-root", self.v2, "--reopen")
        self.assertIn("reopened (pending again): plan", out)
        self.assertEqual(self.record()["steps"]["plan"]["status"], "pending")

    def record(self):
        with open(os.path.join(self.ws, "memory", "pbi", "1234.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_resume_with_an_unreadable_source_is_not_concluded(self):
        self.plan(self.SCEN)
        self.started()
        code, out = self.run_(*self.CHECK, "--spec-root", os.path.join(self.tmp.name, "nowhere"))
        self.assertEqual(code, 3, out)
        self.assertIn("NOT COMPARED: spec: 3 referenced rule(s) could not be verified", out)
        self.assertEqual(out.count("NOT COMPARED"), 1)
        self.assertNotIn("nothing changed since the baseline", out)

    def test_resume_json_carries_the_spec_comparison(self):
        self.plan(self.SCEN)
        self.started()
        export(self.v2, cart=CART.replace("nothing changes and the item stays", "nothing changes"))
        code, out = self.run_(*self.CHECK, "--spec-root", self.v2, "--json")
        doc = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["spec"]["scenarios"]["P1"]["state"], "review")
        self.assertEqual(doc["spec"]["scenarios"]["P2"]["state"], "reusable")

    def test_resume_of_an_old_plan_or_a_project_without_specs_keeps_the_flow(self):
        write(os.path.join(self.ws, "analysis", "old.md"), "# Old plan\n")
        self.started(plan="analysis/old.md")
        code, out = self.run_(*self.CHECK)
        self.assertEqual(code, 0, out)
        self.assertIn("has no spec_refs: no references to compare", out)
        # a baseline without any plan, in a project with specs, says so; without specs it is silent as before
        self.run_("start", "5678", "--platform", "android")
        self.run_("baseline", "5678", "--ticket", "5678=1", "--build", "android=1.0 (1)")
        code, out = self.run_("check", "5678", "--ticket", "5678=1", "--build", "android=1.0 (1)")
        self.assertEqual(code, 0)
        self.assertIn("records no plan with spec_refs", out)
        write(os.path.join(self.ws, "project.yaml"), 'project: "Demo Shop"\ntracker: "excel"\n')
        code, out = self.run_("check", "5678", "--ticket", "5678=1", "--build", "android=1.0 (1)")
        self.assertEqual(code, 0)
        self.assertNotIn("spec", out.lower())

    def test_the_spec_plan_must_be_a_file_of_the_workspace(self):
        self.run_("start", "1234", "--platform", "android")
        code, out = self.run_("baseline", "1234", "--spec-plan", "analysis/absent.md")
        self.assertEqual(code, 2)
        self.assertIn("not a file of the workspace", out)
        code, out = self.run_("baseline", "1234", "--spec-plan", "../outside.md")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

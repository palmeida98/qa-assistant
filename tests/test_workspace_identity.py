"""Identity of a workspace, the session's workspace and PBI records bound to a workspace (technique-workspaces.md 4.1).
Every case runs in temporary folders with fictional projects that copy the two real layouts (<company>/<project>, as
LIA, and <project>, as Contas em Dia); no test reads a real workspace, ~/.claude or ~/.qa-assistant.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import pbi_state
import validate_project
import workspace_identity as wi
import workspace_select as ws
from test_workspace_select import Base, make_project

REAL = {"project": "LIA", "workspace": {"id": "lia", "kind": "real"}}
SIM = {"project": "LIA", "workspace": {"id": "lia-sim-q1", "kind": "simulation"}}


def digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree(path: str) -> dict:
    out = {}
    for base, _, files in os.walk(path):
        for name in files:
            full = os.path.join(base, name)
            out[os.path.relpath(full, path)] = digest(full)
    return out


class Identity(Base):
    def setUp(self):
        super().setUp()
        self.real = make_project(self.root, "lia/lia", **REAL)          # <company>/<project>, as the real LIA
        self.sim = make_project(self.root, "lia-sim-q1", **SIM)         # <project>, a copy with the same name

    def pbi(self, workspace, *args, cwd):
        """pbi_state.py through its command line, from the session folder `cwd`."""
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        env.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "pbi_state.py"), workspace, *args],
                              cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=False)

    # --- the schema --------------------------------------------------------------------------------------------------
    def test_the_block_is_optional_and_validated(self):
        base = {"project": "X", "tracker": "excel", "bug_format": "B", "environments": {"test": "https://x.example/"}}
        self.assertEqual(wi.problems(base), [])
        self.assertIsNone(wi.identity(base))
        self.assertEqual(wi.identity({**base, **REAL}), {"id": "lia", "kind": "real"})
        for wrong in ({"id": "LIA", "kind": "real"}, {"id": "lia", "kind": "copy"}, {"id": "lia"}, "lia",
                      {"id": "lia", "kind": "real", "name": "x"}):
            problems, _ = validate_project.base_problems({**base, "workspace": wrong})
            self.assertTrue(any(p.startswith("workspace") for p in problems), (wrong, problems))
        # `kind` keeps its own meaning under specs: (the export of the specification)
        self.assertEqual(wi.problems({**base, "specs": {"kind": "export"}}), [])

    # --- selection ---------------------------------------------------------------------------------------------------
    def test_two_workspaces_with_the_same_name_resolve_by_identity_and_by_path(self):
        self.assertEqual(self.resolve("lia").candidate.path, self.real)
        self.assertEqual(self.resolve("lia-sim-q1").candidate.path, self.sim)
        self.assertEqual(self.resolve(workspace=self.real).candidate.path, self.real)
        self.assertEqual(self.resolve(workspace=self.sim).candidate.path, self.sim)
        # the identity's id is matched first (exactly, ignoring case); the shared name alone never chooses
        self.assertEqual(self.resolve("LIA").candidate.path, self.real)
        make_project(self.root, "other", project="Loja", workspace={"id": "loja-a", "kind": "real"})
        make_project(self.root, "copy", project="Loja", workspace={"id": "loja-b", "kind": "simulation"})
        both = self.resolve("Loja")
        self.assertEqual(both.outcome, "ambiguous")
        self.assertEqual({c.kind for c in both.candidates}, {"real", "simulation"})
        self.assertEqual(self.resolve("loja-b").candidate.id, "copy")

    def test_a_session_in_the_simulation_asking_for_the_real_workspace_by_path_stops_before_any_write(self):
        before = tree(self.tmp)
        code, report = self.preflight("test-pbi", workspace=self.real, cwd=os.path.join(self.sim))
        self.assertEqual(code, ws.WRONG_SESSION, report["problems"])
        self.assertEqual(report["session"]["session"], self.sim)
        self.assertEqual(report["session"]["target"], self.real)
        self.assertIn(f"cd {self.real} && claude", report["problems"][0])
        # by name too, and whatever the names are
        code, _ = self.preflight("test-pbi", "lia", cwd=self.sim)
        self.assertEqual(code, ws.WRONG_SESSION)
        # the code behind the skill refuses as well: no record is created from the wrong session
        r = self.pbi(self.real, "start", "42434", "--platform", "ios", cwd=self.sim)
        self.assertEqual(r.returncode, 6, r.stdout + r.stderr)
        self.assertIn("session workspace", r.stderr)
        self.assertEqual(tree(self.tmp), before)

    def test_a_copy_that_kept_the_real_identity_is_still_another_folder(self):
        clone = make_project(self.root, "lia-copy", **REAL)
        code, report = self.preflight("test-pbi", workspace=self.real, cwd=clone)
        self.assertEqual(code, ws.WRONG_SESSION)
        self.assertIn("same identity", report["problems"][0])

    def test_an_identity_declared_by_two_folders_is_never_chosen_and_both_paths_are_named(self):
        clone = make_project(self.root, "lia-copy", **REAL)
        for selection in (self.resolve("lia"), self.resolve("LIA")):
            self.assertEqual(selection.outcome, "ambiguous")
            self.assertIn(self.real, selection.message)
            self.assertIn(clone, selection.message)
        code, report = self.preflight("test-pbi", "lia")
        self.assertEqual(code, ws.AMBIGUOUS)
        self.assertIn("declared by 2 folders", report["message"])
        self.assertEqual(self.resolve(workspace=clone).candidate.path, clone)      # a path still chooses

    def test_a_simulation_outside_the_root_is_the_session_and_cannot_reach_the_real_workspace(self):
        sims = os.path.join(self.tmp, "qa-simulations")
        sim = make_project(sims, "lia-42434", project="LIA", workspace={"id": "lia-sim-42434", "kind": "simulation"})
        self.assertNotIn(sim, [c.path for c in ws.discover(self.root)])
        inside = os.path.join(sim, "runs")
        os.makedirs(inside)
        self.assertEqual(self.resolve(cwd=inside).candidate.path, sim)
        before = tree(self.root)
        for kw in ({"hint": "lia"}, {"workspace": self.real}):
            hint = kw.pop("hint", None)
            code, report = self.preflight("test-pbi", hint, cwd=inside, **kw)
            self.assertEqual(code, ws.WRONG_SESSION, report["problems"])
        self.assertEqual(self.pbi(self.real, "start", "1", cwd=inside).returncode, 6)
        self.assertEqual(tree(self.root), before)

    def test_a_copy_that_kept_the_identity_resumes_only_its_own_records(self):
        """The documented limit (technique-workspaces.md 4.1): inside such a copy its own records go on; the
        original is another real path and stays out of reach."""
        self.assertEqual(self.pbi(self.real, "start", "5", cwd=self.real).returncode, 0)
        clone = os.path.join(self.root, "lia-full-copy")
        shutil.copytree(self.real, clone)
        original = digest(os.path.join(self.real, "memory", "pbi", "5.json"))
        self.assertEqual(self.pbi(clone, "saved", "5", "--ref", "context.md", cwd=clone).returncode, 0)
        self.assertEqual(self.pbi(self.real, "saved", "5", "--ref", "context.md", cwd=clone).returncode, 6)
        self.assertEqual(digest(os.path.join(self.real, "memory", "pbi", "5.json")), original)

    def test_a_session_in_a_subfolder_of_the_right_workspace_passes(self):
        deep = os.path.join(self.real, "evidence", "batch_x", "sub", "deeper", "deepest")
        os.makedirs(deep)
        for kw in ({"workspace": self.real}, {}):
            code, report = self.preflight("plan-tests", cwd=deep, **kw)
            self.assertEqual(code, ws.OK, report["problems"])
            self.assertEqual(report["project"]["path"], self.real)
        r = self.pbi(self.real, "start", "42434", "--platform", "android", cwd=deep)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_and_ci_started_outside_a_workspace_with_an_explicit_target_go_on(self):
        for cwd in (self.neutral, self.root, os.path.join(self.root, "lia"), ROOT):
            code, report = self.preflight("plan-tests", workspace=self.real, cwd=cwd)
            self.assertEqual(code, ws.OK, (cwd, report["problems"]))
            r = self.pbi(self.real, "show", "1", cwd=cwd)
            self.assertNotEqual(r.returncode, 6, (cwd, r.stderr))
        # the CI of a workspace runs in the workspace's own checkout
        code, _ = self.preflight("plan-tests", workspace=self.real, cwd=self.real)
        self.assertEqual(code, ws.OK)

    def test_the_command_line_prints_both_workspaces_and_the_command(self):
        env = {**os.environ, ws.CONFIG_ENV: self.config, "PYTHONIOENCODING": "utf-8", ws.ROOT_ENV: self.root}
        env.pop("PYTHONPATH", None)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "workspace_select.py"), "preflight", "--skill",
                            "test-pbi", "--workspace", self.real], cwd=self.sim, env=env, capture_output=True,
                           text=True, encoding="utf-8", check=False)
        self.assertEqual(r.returncode, ws.WRONG_SESSION, r.stdout + r.stderr)
        for text in ("STOP: the session is open in another workspace",
                     f"session workspace: {self.sim} (lia-sim-q1 (simulation))",
                     f"requested target:  {self.real} (lia (real))", f"cd {self.real} && claude"):
            self.assertIn(text, r.stdout)

    # --- PBI records -------------------------------------------------------------------------------------------------
    def test_a_new_record_keeps_the_identity_and_a_copied_one_is_refused_untouched(self):
        r = self.pbi(self.real, "start", "42434", "--platform", "ios", cwd=self.real)
        self.assertEqual(r.returncode, 0, r.stderr)
        record = os.path.join(self.real, "memory", "pbi", "42434.json")
        with open(record, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["workspace"], {"id": "lia", "kind": "real"})
        copied = os.path.join(self.sim, "memory", "pbi", "42434.json")
        os.makedirs(os.path.dirname(copied))
        shutil.copy2(record, copied)
        before = digest(copied)
        for args in (("start", "42434"), ("show", "42434"), ("mark", "42434", "context", "done", "--ref", "x"),
                     ("saved", "42434", "--ref", "context.md"), ("bind", "42434", "--as", "lia-sim-q1",
                                                                  "--decided-by", "QA")):
            r = self.pbi(self.sim, *args, cwd=self.sim)
            self.assertEqual(r.returncode, 6, (args, r.stdout, r.stderr))
            self.assertIn("belongs to the workspace lia (real)", r.stderr)
            self.assertIn("lia-sim-q1 (simulation)", r.stderr)
        self.assertEqual(digest(copied), before)
        # a copy without identity cannot vouch for it either
        bare = make_project(self.root, "bare")
        os.makedirs(os.path.join(bare, "memory", "pbi"))
        shutil.copy2(record, os.path.join(bare, "memory", "pbi", "42434.json"))
        r = self.pbi(bare, "show", "42434", cwd=bare)
        self.assertEqual(r.returncode, 6)
        self.assertIn("this workspace is no identity", r.stderr)

    def test_an_old_record_stays_readable_and_is_bound_only_on_the_qas_decision(self):
        old = {"pbi": "42434", "task": "42732", "platforms": ["ios"], "created": "2026-11-13T01:00:00+00:00",
               "steps": {}, "baseline": {}, "published": {}, "history": []}
        for w in (self.real, self.sim):
            os.makedirs(os.path.join(w, "memory", "pbi"))
            with open(os.path.join(w, "memory", "pbi", "42434.json"), "w", encoding="utf-8") as f:
                json.dump(old, f)
        path = os.path.join(self.sim, "memory", "pbi", "42434.json")
        before = digest(path)
        r = self.pbi(self.sim, "show", "42434", cwd=self.sim)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not bound to a workspace identity", r.stdout)
        self.assertEqual(digest(path), before)          # reading never binds
        r = self.pbi(self.sim, "start", "42434", "--platform", "android", cwd=self.sim)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(path, encoding="utf-8") as f:
            self.assertNotIn("workspace", json.load(f))     # changed as before, still unbound
        r = self.pbi(self.sim, "bind", "42434", "--as", "lia", "--decided-by", "QA", cwd=self.sim)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)      # not this workspace's id
        r = self.pbi(self.real, "bind", "42434", "--as", "lia", "--decided-by", "Pedro (QA)", cwd=self.real)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.real, "memory", "pbi", "42434.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["workspace"], {"id": "lia", "kind": "real"})
        self.assertIn("decided by Pedro (QA)", doc["history"][-1]["event"])

    def test_an_old_workspace_without_identity_in_both_layouts_works_as_before(self):
        for pid in ("acme/app", "contas"):
            path = make_project(self.root, pid)
            self.assertEqual(self.resolve(pid).candidate.identity, "")
            code, report = self.preflight("plan-tests", pid)
            self.assertEqual(code, ws.OK, report["problems"])
            r = self.pbi(path, "start", "7", "--platform", "web", cwd=path)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(path, "memory", "pbi", "7.json"), encoding="utf-8") as f:
                self.assertNotIn("workspace", json.load(f))
            self.assertEqual(self.pbi(path, "show", "7", cwd=os.path.join(path, "memory")).returncode, 0)
        self.assertEqual(pbi_state.load(path, "7")["pbi"], "7")


if __name__ == "__main__":
    unittest.main()

"""scripts/pw_cli.py: the Playwright CLI door. A fake runner stands in for the CLI: no browser, no network."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import common
import pw_cli

FAKE_LOGIN = "fake-pass-1234"     # a fictional value, never a real credential
PIN = "0.1.25"


class Fake:
    """Records the command and writes the file named by --filename, like the CLI does."""

    def __init__(self, stdout="ok", returncode=0, file_text="page text"):
        self.stdout, self.returncode, self.file_text, self.calls = stdout, returncode, file_text, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        for a in cmd:
            if a.startswith("--filename="):
                with open(a.split("=", 1)[1], "w", encoding="utf-8") as f:
                    f.write(self.file_text)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "")


class PwCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(os.path.realpath(self._tmp.name), "repo")
        os.makedirs(os.path.join(self.ws, "e2e"))
        self.write("project.yaml", f'project: "Demo"\nweb:\n  driver: cli\n  cli_version: "{PIN}"\n')
        self.write(".env", f"SHOP_USER=demo\nSHOP_PASS={FAKE_LOGIN}\n")

    def write(self, rel, text):
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def install(self, version=PIN):
        self.write("e2e/node_modules/.bin/" + pw_cli.BIN + (".cmd" if os.name == "nt" else ""), "")
        self.write("e2e/node_modules/@playwright/cli/package.json", json.dumps({"version": version}))

    def run_cli(self, args, fake=None, **kw):
        fake = fake or Fake()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pw_cli.run(self.ws, args, kw.get("batch", "b1"), kw.get("session"), kw.get("allow_eval", False),
                              runner=fake)
        return code, out.getvalue(), err.getvalue(), fake

    # --- readiness -------------------------------------------------------------------------------------------
    def test_not_installed_says_what_to_ask_and_runs_nothing(self):
        code, _, err, fake = self.run_cli(["snapshot"])
        self.assertEqual(code, 1)
        self.assertIn("not installed", err)
        self.assertIn(f"{pw_cli.PACKAGE}@{PIN}", err)
        self.assertEqual(fake.calls, [])

    def test_another_version_than_the_pin_is_not_ready(self):
        self.install("0.1.30")
        code, _, err, _ = self.run_cli(["snapshot"])
        self.assertEqual(code, 1)
        self.assertIn("pins 0.1.25", err)

    def test_no_pin_in_project_yaml_is_not_ready(self):
        self.write("project.yaml", 'project: "Demo"\n')
        self.install()
        code, _, err, _ = self.run_cli(["snapshot"])
        self.assertEqual(code, 1)
        self.assertIn("web.cli_version", err)

    def test_check_ready(self):
        self.install()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(pw_cli.check(self.ws), 0)
        self.assertIn("READY", out.getvalue())

    # --- what is refused -------------------------------------------------------------------------------------
    def test_refused_commands_and_flags(self):
        self.install()
        for args in (["install", "--skills"], ["kill-all"], ["run-code", "x"], ["eval", "1"],
                     ["open", "--persistent"], ["show", "--host=0.0.0.0"]):
            code, _, err, fake = self.run_cli(args)
            self.assertEqual(code, 2, args)
            self.assertIn("REFUSED", err)
            self.assertEqual(fake.calls, [], args)

    def test_eval_runs_only_with_the_users_approval(self):
        self.install()
        code, _, _, fake = self.run_cli(["eval", "document.title"], allow_eval=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.calls), 1)

    def test_refusal_is_reusable_by_the_guard(self):
        self.assertIsNone(pw_cli.refusal(["snapshot"]))
        self.assertIn("run-code", pw_cli.refusal(["-s=x", "run-code", "code"]))
        self.assertIsNone(pw_cli.refusal(["run-code", "code"], allow_eval=True))

    # --- the command that runs ---------------------------------------------------------------------------------
    def test_runs_from_e2e_with_the_installed_cli_in_its_own_session(self):
        self.install()
        code, _, _, fake = self.run_cli(["goto", "https://example.com"])
        cmd, kw = fake.calls[0]
        self.assertEqual(code, 0)
        self.assertEqual(os.path.basename(cmd[0]).split(".")[0], pw_cli.BIN)
        self.assertIn(os.path.join("e2e", "node_modules", ".bin"), cmd[0])
        self.assertEqual(cmd[1:], ["-s=qa-repo", "goto", "https://example.com"])
        self.assertEqual(kw["cwd"], os.path.join(self.ws, "e2e"))

    def test_a_session_given_by_the_caller_is_kept(self):
        self.install()
        _, _, _, fake = self.run_cli(["-s=other", "snapshot"], session=None)
        self.assertEqual(fake.calls[0][0].count("-s=other"), 1)
        self.assertNotIn("-s=qa-repo", fake.calls[0][0])

    def test_placeholders_become_values_that_never_reach_the_output(self):
        self.install()
        fake = Fake(stdout=f"filled {FAKE_LOGIN}")
        _, out, _, _ = self.run_cli(["fill", "e35", "{{SHOP_PASS}}"], fake=fake)
        self.assertIn(FAKE_LOGIN, fake.calls[0][0])          # the CLI receives the value
        self.assertNotIn(FAKE_LOGIN, out)                     # the assistant never reads it
        self.assertIn("SHOP_PASS", out)

    def test_an_unknown_placeholder_stops_the_command(self):
        self.install()
        code, _, err, fake = self.run_cli(["fill", "e1", "{{NOPE}}"])
        self.assertEqual(code, 2)
        self.assertIn("NOPE", err)
        self.assertEqual(fake.calls, [])

    def test_snapshot_is_named_in_evidence_and_redacted(self):
        self.install()
        fake = Fake(file_text=f"- textbox: {FAKE_LOGIN}")
        _, out, _, _ = self.run_cli(["snapshot"], fake=fake)
        rel = os.path.join("evidence", "b1", "cli", "001-snapshot.md")
        self.assertIn(f"Evidence: {rel}", out)
        with open(os.path.join(self.ws, rel), encoding="utf-8") as f:
            self.assertNotIn(FAKE_LOGIN, f.read())

    def test_evidence_numbers_continue_and_a_given_filename_is_kept(self):
        self.install()
        self.run_cli(["screenshot"])
        _, out, _, _ = self.run_cli(["screenshot", "--type=jpeg"])
        self.assertIn("002-screenshot.jpg", out)
        mine = os.path.join(self.ws, "mine.md")
        _, out, _, _ = self.run_cli(["snapshot", f"--filename={mine}"])
        self.assertNotIn("Evidence:", out)
        self.assertTrue(os.path.isfile(mine))

    def test_the_cli_failing_is_exit_1(self):
        self.install()
        code, _, _, _ = self.run_cli(["click", "e9"], fake=Fake(returncode=3))
        self.assertEqual(code, 1)

    def test_a_batch_cannot_leave_the_workspace(self):
        self.install()
        with self.assertRaises(common.OutsideWorkspace):
            self.run_cli(["snapshot"], batch="../../outside")


if __name__ == "__main__":
    unittest.main()

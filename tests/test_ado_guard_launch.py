"""scripts/ado_guard_launch.py: the generic launcher of the write guard; nothing is started, no secret is printed."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import ado_guard_launch as launch

FAKE_READ, FAKE_WRITE = "fake-read-value", "fake-write-value"


def conf_text(*lines):
    return "".join(f"{line}\n" for line in lines)


class Launch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.realpath(self._tmp.name)
        self.ws = os.path.join(self.base, "repo")
        self.tools = os.path.join(self.base, "_tools")
        os.makedirs(os.path.join(self.ws, "evidence"))
        os.makedirs(self.tools)
        self.write(os.path.join(self.ws, "project.yaml"), 'project: "Shop"\nworkspace:\n  id: "shop"\n  kind: "real"\n')
        self.write(os.path.join(self.ws, ".env"), f"SHOP_VAR={FAKE_READ}\n")
        self.write(os.path.join(self.tools, "ado_guard.conf"),
                   "ORG=example-org\nPROJECT=Demo Shop\nREAD_PAT_VAR=SHOP_VAR\n")
        self.write(os.path.join(self.tools, "ado_guard_identity"), "00000000-0000-0000-0000-000000000001\n")
        self.asked = []

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def keychain(self, service):
        self.asked.append(service)
        return FAKE_WRITE

    def test_builds_the_guard_command_and_environment(self):
        self.write(os.path.join(self.tools, "pat_validade.conf"), "ADO_GUARD_READ_PAT_EXPIRES=2027-01-31\nADO_GUARD_WRITE_PAT_EXPIRES=bad\n")
        argv, env, facts = launch.prepare(self.ws, {"ADO_GUARD_ORG": "stale", "PATH": "/bin"}, self.keychain)
        self.assertEqual(argv[argv.index("--org") + 1], "example-org")
        self.assertEqual(argv[argv.index("--project") + 1], "Demo Shop")
        self.assertEqual(argv[argv.index("--allowed-dir") + 1], os.path.join(self.ws, "evidence"))
        self.assertEqual(env["ADO_GUARD_READ_PAT"], FAKE_READ)
        self.assertEqual(env["ADO_GUARD_WRITE_PAT"], FAKE_WRITE)
        self.assertEqual(env["ADO_GUARD_READ_PAT_EXPIRES"], "2027-01-31")
        self.assertNotIn("ADO_GUARD_WRITE_PAT_EXPIRES", env)        # an invalid date is not exported
        self.assertNotIn("ADO_GUARD_ORG", env)                       # nothing inherited from the caller
        self.assertNotIn("ADO_GUARD_ALLOW_BUG_CREATE", env)          # disabled unless the project said so
        self.assertEqual(self.asked, ["shop-ado-write-pat"])
        self.assertNotIn(FAKE_READ, repr(facts))
        self.assertNotIn(FAKE_WRITE, repr(facts))

    def test_bug_creation_only_with_the_standing_decision(self):
        self.write(os.path.join(self.tools, "ado_guard.conf"),
                   conf_text("ORG=o", "PROJECT=p", "READ_PAT_VAR=SHOP_VAR", "ALLOW_BUG_CREATE=1", "KEYCHAIN_SERVICE=custom"))
        _, env, facts = launch.prepare(self.ws, {}, self.keychain)
        self.assertEqual(env["ADO_GUARD_ALLOW_BUG_CREATE"], "1")
        self.assertEqual(self.asked, ["custom"])
        self.assertTrue(facts["bug_creation"])

    def test_refusals(self):
        os.remove(os.path.join(self.tools, "ado_guard_identity"))
        with self.assertRaises(launch.Refused):
            launch.prepare(self.ws, {}, self.keychain)
        self.write(os.path.join(self.tools, "ado_guard_identity"), "x")
        self.write(os.path.join(self.tools, "ado_guard.conf"), "ORG=o\nPROJECT=p\n")
        with self.assertRaises(launch.Refused):
            launch.prepare(self.ws, {}, self.keychain)
        self.write(os.path.join(self.tools, "ado_guard.conf"), "ORG=o\nPROJECT=p\nREAD_PAT_VAR=X\nOTHER=1\n")
        with self.assertRaises(launch.Refused):
            launch.prepare(self.ws, {}, self.keychain)
        with self.assertRaises(launch.Refused):
            launch.prepare(self.base, {}, self.keychain)           # not a repo/ folder

    def test_check_prints_facts_and_no_secret(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.object(launch, "keychain_read", lambda s: FAKE_WRITE):
            code = launch.main([self.ws, "--check"])
        self.assertEqual(code, 0)
        self.assertIn("write_pat: True", out.getvalue())
        self.assertNotIn(FAKE_READ, out.getvalue())
        self.assertNotIn(FAKE_WRITE, out.getvalue())


if __name__ == "__main__":
    unittest.main()

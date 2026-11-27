"""scripts/workspace_init.py: the project folder layout, a dry run first, never an overwrite."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import workspace_init as wi
import workspace_select as ws


class WorkspaceInit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wi.main(["--root", self.root, *args])
        return code, out.getvalue()

    def test_dry_run_writes_nothing(self):
        code, out = self.run_cli("shop")
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertEqual(os.listdir(self.root), [])

    def test_creates_the_layout_and_discovery_finds_it(self):
        code, _ = self.run_cli("shop", "--yes")
        self.assertEqual(code, 0)
        for rel in ("repo/project.yaml", "repo/context.md", "repo/.claude/settings.json", "repo/e2e/specs", "repo/flows",
                    "repo/runs", "_tools", "_arquivo/INDICE.md"):
            self.assertTrue(os.path.exists(os.path.join(self.root, "shop", rel)), rel)
        found = ws.discover(self.root)
        self.assertEqual([c.id for c in found], ["shop"])
        self.assertEqual(found[0].identity, "shop")

    def test_refuses_an_existing_folder_and_a_bad_name(self):
        os.makedirs(os.path.join(self.root, "shop"))
        self.assertEqual(self.run_cli("shop", "--yes")[0], 2)
        self.assertEqual(self.run_cli("Bad Name", "--yes")[0], 2)
        self.assertEqual(os.listdir(os.path.join(self.root, "shop")), [])

    def test_a_missing_root_is_never_created(self):
        gone = os.path.join(self.root, "nope")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wi.main(["--root", gone, "shop", "--yes"]), 2)
        self.assertFalse(os.path.exists(gone))


if __name__ == "__main__":
    unittest.main()

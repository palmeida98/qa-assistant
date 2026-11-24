"""The demo's copy of the Playwright audit helper stays equal to tools/playwright/qa-audit.ts, and its spec imports the
copy, not a path inside the framework's repository (so the demo, or a copy of it, loads anywhere)."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGINAL = os.path.join(ROOT, "tools", "playwright", "qa-audit.ts")
DEMO_SPECS = os.path.join(ROOT, "examples", "demo", "e2e", "specs")


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


class DemoHelperCopy(unittest.TestCase):
    def test_the_copy_equals_the_original(self):
        self.assertEqual(read(os.path.join(DEMO_SPECS, "qa-audit.ts")), read(ORIGINAL),
                         "copy tools/playwright/qa-audit.ts to examples/demo/e2e/specs/qa-audit.ts again")

    def test_no_spec_of_the_demo_imports_a_path_that_leaves_the_workspace(self):
        for name in sorted(os.listdir(DEMO_SPECS)):
            if not name.endswith(".ts"):
                continue
            with open(os.path.join(DEMO_SPECS, name), encoding="utf-8") as f:
                imports = re.findall(r"from\s+[\"'](\.[^\"']*)[\"']", f.read())
            for target in imports:
                with self.subTest(spec=name, target=target):
                    resolved = os.path.normpath(os.path.join(DEMO_SPECS, target))
                    self.assertTrue(resolved.startswith(os.path.join(ROOT, "examples", "demo") + os.sep),
                                    f"{name} imports {target}, outside examples/demo")


if __name__ == "__main__":
    unittest.main()

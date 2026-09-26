"""Scripts' output in UTF-8, also when it is read through a pipe.

python -m unittest discover -s tests   (from the root of qa-assistant)
On Windows, a Python process with its output in a pipe (as Claude Code runs them)
wrote in cp1252: sheet, column and status names with accents arrived garbled
("M�dia"). Each entry point reconfigures stdout and stderr to UTF-8.
"""

import glob
import os
import re
import subprocess
import sys
import tempfile
import unittest

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path: str) -> str:
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def entry_points() -> list[str]:
    sources = []
    for folder in ("scripts", "adapters", "examples"):
        sources += glob.glob(os.path.join(ROOT, folder, "**", "*.py"), recursive=True)
    return sorted(f for f in sources if "node_modules" not in f and 'if __name__ == "__main__":' in read(f))


class Console(unittest.TestCase):
    def test_every_entry_point_writes_in_utf8(self):
        sources = entry_points()
        self.assertGreaterEqual(len(sources), 10)
        for f in sources:
            with self.subTest(script=os.path.relpath(f, ROOT)):
                text = read(f)
                block = text.split('if __name__ == "__main__":', 1)[1]
                self.assertRegex(block, re.compile(r'reconfigure\(encoding="utf-8"'))

    def test_accents_arrive_intact_through_a_pipe(self):
        # a project's own names can have accents in any language; the framework never translates them
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cases.xlsx")
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Cases"
            ws.append(["ID", "Title", "Status", "Observações"])
            for i in range(1, 5):
                ws.append([f"TC-X-{i:03}", f"case {i}", "Pass" if i % 2 else "Fail", None])
            wb.save(path)
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
            r = subprocess.run([sys.executable, "-m", "adapters.excel.inspect", path],
                               cwd=ROOT, capture_output=True, env=env, check=False, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", errors="replace"))
        out = r.stdout.decode("utf-8")  # strict: loose cp1252 bytes would raise UnicodeDecodeError
        self.assertIn("Observações", out)


if __name__ == "__main__":
    unittest.main()

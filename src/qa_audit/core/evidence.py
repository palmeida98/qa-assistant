"""Run folder and evidence files (docs/qa-audit.md §7).

qa-output/
├── .gitignore        "*": a run inside any Git repository never stages evidence
└── <YYYY-MM-DD_HHMMSS>/
    ├── screenshots/ videos/ traces/ logs/
    └── results.json, results.csv, report.md, report.html
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil

GITIGNORE = "# written by qa-audit: evidence of audited sites is never committed\n*\n"


def _slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:limit] or "page"


class RunFolder:
    def __init__(self, root: str, now: dt.datetime | None = None, name: str | None = None):
        self.root = os.path.abspath(root)
        stamp = name or (now or dt.datetime.now()).strftime("%Y-%m-%d_%H%M%S")   # name: one engine's sub-folder (§21)
        os.makedirs(self.root, exist_ok=True)
        self._protect_root()
        path, n = os.path.join(self.root, stamp), 1
        while os.path.exists(path):          # two runs in the same second
            n += 1
            path = os.path.join(self.root, f"{stamp}_{n}")
        self.path = path
        os.makedirs(self.path)
        self._counter = 0

    def _protect_root(self) -> None:
        gi = os.path.join(self.root, ".gitignore")
        if not os.path.isfile(gi):
            with open(gi, "w", encoding="utf-8") as f:
                f.write(GITIGNORE)

    def sub(self, name: str) -> str:
        d = os.path.join(self.path, name)
        os.makedirs(d, exist_ok=True)
        return d

    def new_file(self, sub: str, label: str, ext: str) -> tuple[str, str]:
        """(absolute path, path relative to the run folder) for a new evidence file."""
        self._counter += 1
        name = f"{self._counter:04d}_{_slug(label)}.{ext}"
        return os.path.join(self.sub(sub), name), f"{sub}/{name}"

    def rel(self, path: str) -> str:
        return os.path.relpath(path, self.path).replace(os.sep, "/")

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

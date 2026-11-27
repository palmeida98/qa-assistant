"""
What `_arquivo/` holds that nobody wrote down (technique-workspaces.md §4.2). Read only: it never deletes.

    python scripts/archive_check.py <workspace> [--days 30]

Lists every top-level item of `<project folder>/_arquivo/` that has no mention in `_arquivo/INDICE.md` (by name) and is
older than --days: the candidates to delete, with their size. An item the index mentions is kept until its own
`Delete after` date has passed (a `YYYY-MM-DD` in its row); the rest of the index is free text. Exit codes: 0 listed,
2 the workspace is not the repo/ folder of a project folder.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def size(path: str) -> int:
    if os.path.isfile(path) or os.path.islink(path):
        return os.lstat(path).st_size
    return sum(os.lstat(os.path.join(d, f)).st_size for d, _, files in os.walk(path) for f in files)


def candidates(archive: str, today: datetime.date, days: int) -> list[tuple[str, int, str]]:
    index_path = os.path.join(archive, "INDICE.md")
    rows = []
    if os.path.isfile(index_path):
        with open(index_path, encoding="utf-8") as f:
            rows = [line for line in f if line.startswith("|")]
    out = []
    for name in sorted(os.listdir(archive)):
        if name == "INDICE.md" or name.startswith("."):
            continue
        path = os.path.join(archive, name)
        mentioned = [r for r in rows if name in r]
        if mentioned:
            dates = [datetime.date.fromisoformat(d) for r in mentioned for d in DATE.findall(r)]
            if dates and max(dates) < today:
                out.append((name, size(path), f"index date {max(dates)} has passed"))
            continue
        age = (today - datetime.date.fromtimestamp(os.lstat(path).st_mtime)).days
        if age > days:
            out.append((name, size(path), f"not in INDICE.md, {age} days old"))
    return out


def main(argv: list[str] | None = None, today: datetime.date | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("workspace")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args(argv)
    ws = os.path.abspath(a.workspace)
    if os.path.basename(ws) != "repo":
        print("STOP: the workspace must be the repo/ folder of a project folder (technique-workspaces.md §4.2)")
        return 2
    archive = os.path.join(os.path.dirname(ws), "_arquivo")
    if not os.path.isdir(archive):
        print("no _arquivo/ beside the workspace: nothing to check")
        return 0
    found = candidates(archive, today or datetime.date.today(), a.days)
    for name, nbytes, why in found:
        print(f"candidate: {name}  {nbytes / 1048576:.1f} MB  ({why})")
    print(f"{len(found)} candidate(s) to delete; nothing was deleted")
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

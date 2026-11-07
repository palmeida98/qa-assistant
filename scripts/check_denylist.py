#!/usr/bin/env python3
"""Blocks commits that contain client/company names from the personal denylist.
The list lives in ~/.qa-denylist.txt (one entry per line), outside the repository;
QA_DENYLIST points to another list (used in the tests).

Checks the path of each file and its text. Office files (.xlsx, .docx, .pptx,
...) are compressed ZIPs: the text is read from inside the ZIP, otherwise a
client workbook would pass unseen.

Without a list (missing or empty) nothing is checked, and it says so: "NOT CONFIGURED: client names were NOT
checked" (exit 0, so the commits of a computer without a list still go through; with --require, exit 3, for a
validation that must not report a check that did not run). A list that checked files ends with one summary line.

    python scripts/check_denylist.py [--require] <file> ...
Exit codes: 0 clean (or not configured, without --require) - 1 a term was found - 3 not configured with --require
"""
import os
import sys
import zipfile
from pathlib import Path

DENYLIST_PATH = Path(os.environ.get("QA_DENYLIST") or Path.home() / ".qa-denylist.txt")
OFFICE_ZIP = {".xlsx", ".xlsm", ".xltx", ".docx", ".dotx", ".pptx", ".potx"}


def load_denylist():
    if not DENYLIST_PATH.exists():
        return []
    lines = DENYLIST_PATH.read_text(encoding="utf-8-sig").splitlines()
    return [l.strip().lower() for l in lines if l.strip() and not l.strip().startswith("#")]


def file_text(path: Path) -> str:
    if path.suffix.lower() in OFFICE_ZIP and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return "\n".join(z.read(n).decode("utf-8", errors="ignore") for n in z.namelist())
    return path.read_text(encoding="utf-8", errors="ignore")


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    require = "--require" in args
    files = [a for a in args if a != "--require"]
    denylist = load_denylist()
    if not denylist:
        print(f"denylist NOT CONFIGURED ({DENYLIST_PATH} missing or empty): client names were NOT checked",
              file=sys.stderr)
        return 3 if require else 0

    found = False
    checked = 0
    for filename in files:
        path = Path(filename)
        if not path.is_file():
            continue
        try:
            text = file_text(path).lower()
        except (OSError, zipfile.BadZipFile):  # an unreadable file has no text to check
            text = ""
        checked += 1
        name = filename.replace("\\", "/").lower()
        for term in denylist:
            if term in name:
                print(f"BLOCKED: '{term}' in the name/path {filename}")
                found = True
            if term in text:
                print(f"BLOCKED: '{term}' found in {filename}")
                found = True

    if found:
        print("\nCommit refused: remove these references before continuing.")
        return 1
    print(f"denylist: {len(denylist)} term(s) checked in {checked} file(s): clean")
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

#!/usr/bin/env python3
"""Blocks commits that contain client/company names from the personal denylist.
The list lives in ~/.qa-denylist.txt (one entry per line), outside the repository;
QA_DENYLIST points to another list (used in the tests).

Checks the path of each file and its text. Office files (.xlsx, .docx, .pptx,
...) are compressed ZIPs: the text is read from inside the ZIP, otherwise a
client workbook would pass unseen.
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


def main():
    denylist = load_denylist()
    if not denylist:
        return 0

    found = False
    for filename in sys.argv[1:]:
        path = Path(filename)
        if not path.is_file():
            continue
        try:
            text = file_text(path).lower()
        except (OSError, zipfile.BadZipFile):  # an unreadable file has no text to check
            text = ""
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
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

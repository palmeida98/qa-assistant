#!/usr/bin/env python3
"""Blocks commits that contain client/company names from the personal denylist.
The list lives in ~/.qa-denylist.txt (one entry per line), outside the repository;
QA_DENYLIST points to another list (used in the tests).

Checks the path of each file and its text. Office files (.xlsx, .docx, .pptx,
...) are compressed ZIPs: the text is read from inside the ZIP, otherwise a
client workbook would pass unseen.

What it never does: report as clean what it did not inspect, or print the terms of the list. A match names the
term's line in the list ("term at line 3 of the list"), so the log of a hook or of a CI step never carries them.

- No list (missing, empty, or only comments): nothing is checked and it says so ("NOT CONFIGURED: client names
  were NOT checked"). A public clone has no list, so the default is exit 0; it is exit 3 when the check is
  required: with --require, with QA_DENYLIST_REQUIRE=1, or with `git config --global qa-assistant.denylistRequired
  true` (the owner's setting, which the hooks of every checkout then honour).
- A list that cannot be read or decoded is a failure (exit 4), required or not: the owner has a list and it did
  not work.
- A file that should be inspected and cannot be (unreadable, a corrupt or encrypted Office file) is a failure
  (exit 4). A file whose format this check cannot read (PDF, legacy .doc/.xls/.ppt) has its name checked and is
  listed as "content NOT inspected" in the summary. A path that is not a regular file is listed as skipped.

    python scripts/check_denylist.py [--require] <file> ...
Exit codes: 0 clean (or not configured, not required) - 1 a term was found - 3 not configured and required -
            4 the list or a file could not be inspected
"""
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

DENYLIST_PATH = Path(os.environ.get("QA_DENYLIST") or Path.home() / ".qa-denylist.txt")
OFFICE_ZIP = {".xlsx", ".xlsm", ".xltx", ".docx", ".dotx", ".pptx", ".potx"}
NOT_READABLE_FORMATS = {".pdf", ".doc", ".xls", ".ppt"}     # the name is checked; the content is not inspected
TRUTHY = {"1", "true", "yes", "on"}


class DenylistUnreadable(Exception):
    """The list exists and could not be read or decoded."""


def load_denylist():
    """(terms, why): terms is [(line number, lower-case term)]; why says what was wrong with a list with no terms."""
    if not DENYLIST_PATH.exists():
        return [], "missing"
    try:
        lines = DENYLIST_PATH.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise DenylistUnreadable(type(exc).__name__) from exc
    terms = [(n, l.strip().lower()) for n, l in enumerate(lines, 1) if l.strip() and not l.strip().startswith("#")]
    return terms, ("" if terms else "empty")


def required(args) -> bool:
    if "--require" in args or os.environ.get("QA_DENYLIST_REQUIRE", "").strip().lower() in TRUTHY:
        return True
    try:
        out = subprocess.run(["git", "config", "--get", "qa-assistant.denylistRequired"], capture_output=True,
                             text=True, check=False, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return out.strip().lower() in TRUTHY


def file_text(path: Path) -> str:
    """The text of a file; Office files from inside their ZIP. Raises OSError / BadZipFile when it cannot be read."""
    suffix = path.suffix.lower()
    if suffix in OFFICE_ZIP:
        if not zipfile.is_zipfile(path):
            raise zipfile.BadZipFile("not a ZIP: corrupt or encrypted Office file")
        with zipfile.ZipFile(path) as z:
            return "\n".join(z.read(n).decode("utf-8", errors="ignore") for n in z.namelist())
    return path.read_text(encoding="utf-8", errors="ignore")


def masked(filename: str, denylist) -> str:
    """The path as it may be shown: a term inside a file name is replaced, so the log never carries it."""
    for lineno, term in denylist:
        filename = re.sub(re.escape(term), f"<term {lineno}>", filename, flags=re.IGNORECASE)
    return filename


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    files = [a for a in args if a != "--require"]
    must = required(args)
    try:
        denylist, why = load_denylist()
    except DenylistUnreadable as exc:
        print(f"denylist UNREADABLE ({exc} reading the list): client names were NOT checked", file=sys.stderr)
        return 4
    if not denylist:
        print(f"denylist NOT CONFIGURED (the list is {why}): client names were NOT checked"
              + (" -- required here, so this is a failure" if must else ""), file=sys.stderr)
        return 3 if must else 0

    found = False
    checked = 0
    unverified, name_only, skipped = [], [], []
    for filename in files:
        path = Path(filename)
        if not path.is_file():
            skipped.append(filename)
            continue
        name = filename.replace("\\", "/").lower()
        for lineno, term in denylist:
            if term in name:
                print(f"BLOCKED: a term (line {lineno} of the list) is in the name/path {masked(filename, denylist)}")
                found = True
        if path.suffix.lower() in NOT_READABLE_FORMATS:
            name_only.append(filename)
            continue
        try:
            text = file_text(path).lower()
        except (OSError, zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError) as exc:
            # not read, not clean: said apart from a match, and it fails the check
            unverified.append((filename, type(exc).__name__))
            continue
        checked += 1
        for lineno, term in denylist:
            if term in text:
                print(f"BLOCKED: a term (line {lineno} of the list) found in {masked(filename, denylist)}")
                found = True

    for filename, why in unverified:
        print(f"NOT VERIFIED: {masked(filename, denylist)} could not be read ({why}); its content was NOT checked", file=sys.stderr)
    for filename in name_only:
        print(f"content NOT inspected: {masked(filename, denylist)} (format this check cannot read; its name was checked)",
              file=sys.stderr)
    for filename in skipped:
        print(f"skipped: {masked(filename, denylist)} is not a regular file (deleted, a link or a folder)", file=sys.stderr)

    if found:
        print("\nCommit refused: remove these references before continuing.")
        return 1
    if unverified:
        print("\nCommit refused: a file could not be inspected, so it cannot be called clean.", file=sys.stderr)
        return 4
    extra = f" ({len(name_only)} name-only, {len(skipped)} skipped)" if name_only or skipped else ""
    print(f"denylist: {len(denylist)} term(s) checked in {checked} file(s){extra}: clean")
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

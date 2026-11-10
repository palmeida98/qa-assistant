"""Checks the files a person reads before approving: plans, comment drafts, review files (plan Q5, 10.11.2026).

    python scripts/check_approval_files.py <file> [<file> ...]

Heuristic, read only. For each file it checks that:

- it is not CUT: it ends with a new line, does not end in a truncation mark («…», «...», «[truncated]», «(cortado)»), has
  balanced code fences (``` or ~~~), balanced «» and (); a line that ends in the middle of a word at the end of file is
  reported;
- a declared COUNT is true: a line `<!-- count: <name>=<n> -->` is followed by a list (lines starting with `- `, `* ` or
  `1. `) and `n` must be the number of items of THAT list (the list ends at the first blank line after it);
- a COMMAND is whole: the fenced block that follows a heading or a line containing "Command" or "Comando" has no
  ellipsis and no placeholder like `<...>` left except `<token>`;
- a declared HASH is true: a line `text_sha256 = <64 hex>` (or `sha256: <64 hex>`) is compared with the SHA-256 of the
  sibling file with the same name and the extension `.html` (the text that would be sent), when that sibling exists.

Exit 0 when every file is clean, 1 when something is reported, 2 on a usage error. It does not judge the content; it
catches the mistakes that made reviews arrive cut or with wrong numbers.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys

TRUNCATION = ("…", "...", "[truncated]", "[cortado]", "(cortado)", "(truncated)")
COUNT = re.compile(r"<!--\s*count:\s*([A-Za-z0-9_ -]+?)\s*=\s*(\d+)\s*-->")
ITEM = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+)\S")
HASH = re.compile(r"(?:text_sha256|sha256)\s*[=:]\s*`?([0-9a-fA-F]{64})`?")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
COMMAND_HEAD = re.compile(r"\b(command|comando)\b", re.IGNORECASE)


def _fences(lines: list[str]) -> tuple[list[tuple[int, int, str]], bool]:
    """(start, end, fence) of each fenced block, and whether the last one is closed."""
    blocks, open_at, mark = [], None, None
    for i, line in enumerate(lines):
        m = FENCE.match(line)
        if not m:
            continue
        if open_at is None:
            open_at, mark = i, m.group(1)[0]
        elif m.group(1)[0] == mark:
            blocks.append((open_at, i, mark))
            open_at = None
    return blocks, open_at is None


def check_text(text: str, path: str = "") -> list[str]:
    problems: list[str] = []
    lines = text.split("\n")
    exact_payload = path.lower().endswith(".html")        # the text that is sent as it is: no new line is added to it
    if text and not text.endswith("\n") and not exact_payload:
        problems.append("the file does not end with a new line (it may be cut)")
    tail = text.rstrip()
    for mark in TRUNCATION:
        if tail.endswith(mark):
            problems.append(f"the file ends with a truncation mark ({mark!r})")
            break
    blocks, closed = _fences(lines)
    if not closed:
        problems.append("a code fence is not closed (the rest of the file would read as code)")
    prose = "\n".join(l for i, l in enumerate(lines) if not any(a <= i <= b for a, b, _m in blocks))
    for open_c, close_c in (("«", "»"), ("(", ")")):
        if prose.count(open_c) != prose.count(close_c):
            problems.append(f"unbalanced {open_c}{close_c} in the text ({prose.count(open_c)} open, {prose.count(close_c)} closed)")
    for i, line in enumerate(lines):
        m = COUNT.search(line)
        if not m:
            continue
        name, declared = m.group(1).strip(), int(m.group(2))
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        n = 0
        while j < len(lines) and ITEM.match(lines[j]):
            n += 1
            j += 1
        if n != declared:
            problems.append(f"count '{name}' declares {declared} but the list that follows has {n} item(s) (line {i + 1})")
    for a, b, _mark in blocks:
        head = " ".join(lines[max(0, a - 3):a])
        if not COMMAND_HEAD.search(head):
            continue
        body = "\n".join(lines[a + 1:b])
        if "…" in body or "..." in body:
            problems.append(f"the command at line {a + 1} has an ellipsis: it is not the whole command")
        if any(p not in ("<token>", "<expect>") for p in re.findall(r"<[A-Za-z .-]*>", body)):
            problems.append(f"the command at line {a + 1} has a placeholder left")
    if path:
        sibling = os.path.splitext(path)[0] + ".html"
        for m in HASH.finditer(text):
            if os.path.isfile(sibling) and sibling != path:
                with open(sibling, "rb") as f:
                    real = hashlib.sha256(f.read()).hexdigest()
                if real != m.group(1).lower():
                    problems.append(f"the declared hash {m.group(1)[:12]}… is not the SHA-256 of {os.path.basename(sibling)} ({real[:12]}…)")
    return problems


def check_file(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return check_text(f.read(), path)


def main(argv=None) -> int:
    files = list(sys.argv[1:] if argv is None else argv)
    if not files or files[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    bad = 0
    for path in files:
        if not os.path.isfile(path):
            print(f"{path}: not a file", file=sys.stderr)
            return 2
        problems = check_file(path)
        bad += bool(problems)
        print(f"{'FIX ' if problems else 'OK  '}{path}" + "".join(f"\n       - {p}" for p in problems))
    return 1 if bad else 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

"""
Keeps a workspace's context.md short (methodology 12.2; technique-workspaces.md section 7).

    python scripts/compact_context.py <workspace> [--keep N]            dry run: what would move, and the size
    python scripts/compact_context.py <workspace> [--keep N] --apply    move it

context.md is the session's current state, not its history. In the sections `Current state`, `Execution point` and
`Work done`, an entry that starts with a date in its first line (`**29.10.2026 ...**`, `**L43 (29.10.2026 ...)**`,
`- 29.10.2026: ...`) belongs to the past once it is not one of the newest N (default 5, newest by the date written).
Those entries move, word for word, to `archive/context/context_archive_<YYYYMMDD>.md`, and a one-line pointer stays in
their section. Everything else stays where it is: the other sections (decisions, open questions, blockers, pending
work, validation, the tickets block), every entry without a date (a how-to-resume block) and every byte outside the
moved entries. An entry that mentions a rule or a decision is listed apart, to be checked against `Decisions made`
before the move is trusted.

--apply first copies the whole file to `archive/context/context_<stamp>_before.md` (the way back), then writes under the
workspace's lock, atomically. Nothing in `history/`, the run logs or the evidence is read or written, and a path that
leaves the workspace is refused. Running it again with nothing older than the newest N changes nothing.

Exit codes: 0 done (or dry run) · 1 another process is writing the workspace · 2 usage (no context.md, a path refused)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

from common import (
    OutsideWorkspace,
    WorkspaceBusy,
    atomic_write,
    workspace_lock,
    workspace_path,
)

DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
RULE_WORDS = ("rule", "decision", "lesson", "never")      # in English: a project writes its own in context.rule_words
POINTER = "> Older entries of this section: `archive/context/` (each move is dated there)."
SECTIONS = (("Current state", r"\*\*"), ("Execution point", r"\*\*"), ("Work done", r"- "))
ARCHIVE = os.path.join("archive", "context")


class Usage(Exception):
    pass


def _blocks(lines: list[str], start: str) -> list[tuple[int, int]]:
    """(first, end) line ranges of the entries of a section: each starts with `start` at column 0 and runs to the next."""
    firsts = [i for i, ln in enumerate(lines) if re.match(start, ln)]
    if not firsts:                  # a section with no dated entry (an empty one): nothing to move
        return []
    return [(a, b) for a, b in zip(firsts, firsts[1:] + [len(lines)], strict=True)]


def _date(line: str):
    m = DATE.search(line[:140])
    return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def rule_pattern(extra=()) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in (*RULE_WORDS, *extra) if w) + ")", re.IGNORECASE)


def plan(text: str, keep: int, extra_words=()) -> dict:
    """{"sections": [{name, entries, moved: [(first_line_text, lines)]}], "new_text", "archived": {section: [lines]}}."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    bounds = list(zip(heads, heads[1:] + [len(lines)], strict=True)) if heads else []
    removed: set[int] = set()
    rule = rule_pattern(extra_words)
    report, archived, pointers = [], {}, []
    for first, end in bounds:
        name = lines[first][3:].strip()
        start = next((s for n, s in SECTIONS if name.startswith(n)), None)
        if start is None:
            continue
        body = list(range(first + 1, end))
        spans = []
        for a, b in _blocks([lines[i] for i in body], start):
            a, b = a + first + 1, b + first + 1
            for i in range(a, b):           # the pointer of an earlier move is the section's, never an entry's
                if lines[i].strip() == POINTER:
                    b = i
                    break
            spans.append((a, b))
        dated = [(sp, _date(lines[sp[0]])) for sp in spans if _date(lines[sp[0]])]
        order = sorted(range(len(dated)), key=lambda k: (tuple(-x for x in dated[k][1]), k))
        keepers = set(order[:keep])
        moved = [dated[k][0] for k in range(len(dated)) if k not in keepers]
        report.append({"section": name, "entries": len(dated), "kept": len(dated) - len(moved), "moved": [
            {"line": lines[a][:110], "lines": b - a, "may_hold_a_rule": bool(rule.search("\n".join(lines[a:b])))}
            for a, b in moved]})
        if not moved:
            continue
        archived[name] = ["\n".join(lines[a:b]).rstrip("\n") for a, b in moved]
        for a, b in moved:
            removed.update(range(a, b))
        if not any(POINTER == lines[i].strip() for i in range(first + 1, end)):
            pointers.append((first, end))
    after = {}
    for first, end in pointers:         # the pointer closes the section: after its last line that stays
        kept = [i for i in range(first + 1, end) if i not in removed and lines[i].strip()]
        after[kept[-1] if kept else first] = True
    out = []
    for i, ln in enumerate(lines):
        if i in removed:
            continue
        out.append(ln)
        if i in after:
            out += ["", POINTER]
    new = "\n".join(out)
    return {"report": report, "new_text": new.replace("\n", nl), "archived": archived,
            "moved": sum(len(r["moved"]) for r in report)}


def apply(workspace: str, keep: int, now: datetime | None = None, extra_words=()) -> dict:
    now = now or datetime.now()
    try:
        context = workspace_path(workspace, "context.md")
        folder = workspace_path(workspace, ARCHIVE)
    except OutsideWorkspace as e:
        raise Usage(str(e)) from None
    if not os.path.isfile(context):
        raise Usage(f"{context} does not exist")
    with workspace_lock(workspace):
        with open(context, encoding="utf-8", newline="") as f:
            text = f.read()
        done = plan(text, keep, extra_words)
        if not done["moved"]:
            return {**done, "files": []}
        os.makedirs(folder, exist_ok=True)
        stamp = now.strftime("%Y%m%d_%H%M%S")
        try:
            archive = workspace_path(workspace, ARCHIVE, f"context_archive_{now.strftime('%Y%m%d')}.md")
            before = _new_file(workspace, f"context_{stamp}_before.md", text)
        except OutsideWorkspace as e:
            raise Usage(str(e)) from None
        parts = [(f"\n<!-- moved from context.md on {now.isoformat(timespec='seconds')}; the whole file before is "
                  f"{os.path.basename(before)} -->\n")]
        for name, entries in done["archived"].items():
            parts.append(f"\n## {name}\n\n" + "\n\n".join(entries) + "\n")
        fd = os.open(archive, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as f:
            if f.tell() == 0:
                f.write("# context.md: entries moved here, word for word\n")
            f.write("".join(parts))
        atomic_write(context, done["new_text"])
        return {**done, "files": [before, archive]}


def _new_file(workspace: str, name: str, text: str) -> str:
    """Writes the copy of the whole file before a compaction under a name that did not exist (`_2`, `_3` … when two
    compactions fall in the same second), never through a link. Returns its path."""
    stem, ext = os.path.splitext(name)
    for n in range(1, 100):
        path = workspace_path(workspace, ARCHIVE, f"{stem}{'' if n == 1 else f'_{n}'}{ext}")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return path
    raise Usage(f"too many copies named {name} in {ARCHIVE}")


def _words(workspace: str, given: str) -> list[str]:
    """The project's own words for a rule (`context: {rule_words: [...]}` of project.yaml) and those on the command line."""
    from common import read_project
    try:
        configured = (read_project(workspace).get("context") or {}).get("rule_words") or []
    except (OSError, ValueError, AttributeError):
        configured = []
    return [str(w).strip() for w in [*configured, *given.split(",")] if str(w).strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Keeps a workspace's context.md short, moving old dated entries to archive/context/.")
    ap.add_argument("workspace")
    ap.add_argument("--keep", type=int, default=5, help="newest dated entries kept per section (default 5)")
    ap.add_argument("--apply", action="store_true", help="move them (default: a dry run)")
    ap.add_argument("--rule-words", default="", help="more words that mark an entry as holding a rule, comma separated "
                    "(added to context.rule_words of project.yaml)")
    a = ap.parse_args(argv)
    if a.keep < 1:
        print("ERROR: --keep must be at least 1", file=sys.stderr)
        return 2
    try:
        words = _words(a.workspace, a.rule_words)
        if a.apply:
            done = apply(a.workspace, a.keep, extra_words=words)
        else:
            path = workspace_path(a.workspace, "context.md")
            if not os.path.isfile(path):
                raise Usage(f"{path} does not exist")
            with open(path, encoding="utf-8", newline="") as f:
                text = f.read()
            done = {**plan(text, a.keep, words), "files": []}
            done["before"] = len(text)
    except (Usage, OutsideWorkspace) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except WorkspaceBusy as e:
        print(str(e), file=sys.stderr)
        return 1
    for r in done["report"]:
        print(f"{r['section']}: {r['entries']} dated entries, {r['kept']} kept, {len(r['moved'])} to move")
        for m in r["moved"]:
            print(f"   {'[check the rule] ' if m['may_hold_a_rule'] else ''}{m['lines']:3} lines  {m['line']}")
    if not done["moved"]:
        print("Nothing to move: context.md is already short.")
        return 0
    if a.apply:
        print(f"Moved {done['moved']} entries. Whole file before: {done['files'][0]}; entries: {done['files'][1]}")
    else:
        print(f"Dry run: {done['moved']} entries would move ({len(done['new_text'])} characters would stay). "
              "Run again with --apply; check the entries marked [check the rule] against `Decisions made`.")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

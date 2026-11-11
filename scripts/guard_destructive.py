"""Claude Code PreToolUse hook: no recursive deletion outside the session's scratchpad (incident I1, 09.11.2026).

On 09.11.2026 a helper script ran `shutil.rmtree(<parent folder>)` to remove one stray file and deleted a whole folder
of the temporary area. This hook refuses that class of command BEFORE it runs:

- Bash: `rm` with a recursive flag, `find ... -delete` / `-exec rm`, `git clean`, and `rmtree`/`removedirs` named in the
  command text (python -c, heredocs) or inside a python script file the command runs. A recursive deletion is allowed
  only when EVERY path is absolute, resolves (`..` and `~` included) to a place STRICTLY INSIDE the scratchpad of this
  session, and has no unexpanded variable, glob or substitution.
- Write / Edit / NotebookEdit: a file whose content calls `rmtree`/`removedirs`/`rm -r` with a path that is not a literal
  strictly inside the scratchpad is refused (the incident was a script written first and executed later).

The scratchpad is `/private/tmp/claude-<uid>/<project>/<session_id>/scratchpad` (also under `/tmp`), from the
`session_id` of the hook's input; `QA_SCRATCHPAD` (folders separated by `:`) adds to it for tests. Without a session
id nothing is allowed.

PLATFORM: POSIX only (macOS, Linux). It is not supported on Windows: see docs/technique-destructive-guard.md, "Platform".

LIMITS (honest): it reads the command line, python files that the command names and the text being written; it cannot
see a deletion built at run time (a path assembled from variables is refused, a path read from a file is not seen),
a script that is not a python file, or an interpreter/binary other than the ones it recognises. It is a safety net
for the mistake that happened, not a sandbox. It is NOT active until it is installed in a settings file (see
docs/technique-destructive-guard.md): installing it is a configuration change the user approves.

Exit 0 allows. Exit 2 blocks and writes the reason to stderr (Claude Code gives it to the model).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

SEPARATORS = {";", "&&", "||", "|", "&", "\n"}
WRAPPERS = {"sudo", "command", "env", "time", "nice", "nohup", "exec", "xargs"}
RM_NAMES = {"rm", "/bin/rm", "/usr/bin/rm", "unlink-r"}
RMTREE = re.compile(r"\b(?:shutil\s*\.\s*)?(rmtree|removedirs)\s*\(|\bshutil\.rmtree\b|\bos\.removedirs\b")
RM_IN_TEXT = re.compile(r"(?:^|[\s;&|(`'\"])rm\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b")
LITERAL = re.compile(r"""(?:rmtree|removedirs)\s*\(\s*(?:r|f)?(["'])(.+?)\1""")
QUOTED_RM = re.compile(r"""(?m)(?:^|[;&|("'`])[ \t]*rm[ \t]+((?:-\S+[ \t]+)+)(\S+)""")
UNSAFE_CHARS = re.compile(r"[\$`*?\[\]{}]")


def _patterns(session_id: str | None) -> list[str]:
    """Regexes of the places STRICTLY INSIDE this session's scratchpad (at least one more path component)."""
    if session_id and re.fullmatch(r"[0-9A-Za-z-]{8,64}", session_id):
        return [rf"^(?:/private)?/tmp/claude-\d+/[^/]+/{re.escape(session_id)}/scratchpad/[^/].*$"]
    return []


def strictly_inside(path: str, session_id: str | None) -> bool:
    """True only for an absolute path with no variable/glob/substitution that resolves (`..`, `~` and symbolic links
    included) STRICTLY inside a scratchpad of this session. The scratchpad folder itself is not deletable."""
    if not path or UNSAFE_CHARS.search(path):
        return False
    path = os.path.expanduser(path) if path.startswith("~") else path
    if not os.path.isabs(path):
        return False
    norm = os.path.normpath(path)
    fixed = [os.path.normpath(p) for p in os.environ.get("QA_SCRATCHPAD", "").split(":") if p.strip()]
    for real in {norm, os.path.realpath(norm) if os.path.exists(norm) else norm}:
        inside = any(re.match(p, real) for p in _patterns(session_id)) or any(real.startswith(f + "/") for f in fixed)
        if not inside:
            return False
    return True


def _tokens(command: str) -> list[list[str]]:
    """The command split into simple commands (a list of words each), on ; && || | & and new lines. Quotes honoured;
    on a quoting error the whole text is one command so that nothing slips through unparsed."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    lex.commenters = ""
    cmds, cur = [], []
    try:
        for tok in lex:
            if tok in SEPARATORS or (set(tok) <= set(";&|") and tok):
                if cur:
                    cmds.append(cur)
                cur = []
            else:
                cur.append(tok)
    except ValueError:
        return [command.split()]
    if cur:
        cmds.append(cur)
    return cmds


def _strip_wrappers(words: list[str]) -> list[str]:
    while words and (words[0] in WRAPPERS or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", words[0])):
        words = words[1:]
    return words


def _check_rm(words: list[str], session_id: str | None) -> str | None:
    recursive, paths = False, []
    end_opts = False
    for w in words[1:]:
        if not end_opts and w == "--":
            end_opts = True
        elif not end_opts and w.startswith("--"):
            recursive = recursive or w == "--recursive"
        elif not end_opts and w.startswith("-") and len(w) > 1:
            recursive = recursive or any(ch in "rR" for ch in w[1:])
        else:
            paths.append(w)
    if not recursive:
        return None
    if not paths:
        return "recursive rm without a path"
    bad = [p for p in paths if not strictly_inside(p, session_id)]
    if bad:
        return (f"recursive rm of {bad[0]!r}: only absolute paths strictly inside this session's scratchpad are allowed "
                "(never a parent folder or a prefix; list the folder first and name each path in full)")
    return None


def _check_find(words: list[str], session_id: str | None) -> str | None:
    if "-delete" not in words and not any(w in ("-exec", "-execdir", "-ok") for w in words):
        return None
    if "-delete" not in words:
        i = next(i for i, w in enumerate(words) if w in ("-exec", "-execdir", "-ok"))
        if "rm" not in [os.path.basename(w) for w in words[i + 1:i + 3]]:
            return None
    starts = []
    for w in words[1:]:
        if w.startswith("-") or w in ("(", "!"):
            break
        starts.append(w)
    bad = [p for p in (starts or ["."]) if not strictly_inside(p, session_id)]
    if bad:
        return f"find with deletion starting at {bad[0]!r}: only absolute paths strictly inside this session's scratchpad"
    return None


def _check_python_text(text: str, session_id: str | None, where: str) -> str | None:
    """Text of a python command or file: every rmtree/removedirs needs a literal path strictly inside the scratchpad."""
    if not RMTREE.search(text):
        return None
    calls = len(RMTREE.findall(text))
    literals = [m.group(2) for m in LITERAL.finditer(text)]
    if len(literals) < calls:
        return (f"{where}: rmtree/removedirs with a path that is not a literal (variable, expression): it cannot be "
                "checked; give the full literal path inside this session's scratchpad")
    bad = [p for p in literals if not strictly_inside(p, session_id)]
    if bad:
        return (f"{where}: rmtree/removedirs of {bad[0]!r}: only absolute literal paths strictly inside this session's "
                "scratchpad are allowed (the incident of 09.11.2026 removed a parent folder to delete one file: remove "
                "the file itself by its full path)")
    return None


def check_bash(command: str, session_id: str | None) -> str | None:
    problem = _check_python_text(command, session_id, "command text")
    if problem:
        return problem
    if RM_IN_TEXT.search(command):          # `rm -r` anywhere in the text (inside sh -c, heredocs, xargs lines)
        for words in _tokens(command):
            w = _strip_wrappers(words)
            if w and os.path.basename(w[0]) == "rm":
                problem = _check_rm(["rm", *w[1:]], session_id)
                if problem:
                    return problem
        for m in QUOTED_RM.finditer(command):         # inside quoted strings (sh -c '...')
            if re.search(r"[rR]", m.group(1)) and not strictly_inside(m.group(2).strip("'\""), session_id):
                return f"recursive rm of {m.group(2)!r} (in a quoted command): not strictly inside this session's scratchpad"
    for words in _tokens(command):
        w = _strip_wrappers(words)
        if not w:
            continue
        base = os.path.basename(w[0])
        if base == "find":
            problem = _check_find(w, session_id)
        elif base == "git" and "clean" in w[1:3]:
            problem = "git clean deletes untracked files and folders: not allowed from the agent"
        elif re.fullmatch(r"python[0-9.]*|python3[0-9.]*", base) or base in ("qa_python",):
            problem = None
            for arg in w[1:]:
                if arg.endswith(".py") and os.path.isfile(arg):
                    try:
                        with open(arg, encoding="utf-8", errors="replace") as f:
                            problem = _check_python_text(f.read(), session_id, f"script {arg}")
                    except OSError:
                        problem = None
                    if problem:
                        break
        else:
            problem = None
        if problem:
            return problem
    return None


def check_write(content: str, session_id: str | None, path: str = "") -> str | None:
    problem = _check_python_text(content, session_id, f"the file being written {path}".strip())
    if problem:
        return problem
    if RM_IN_TEXT.search(content):
        for m in QUOTED_RM.finditer(content):
            if re.search(r"[rR]", m.group(1)) and not strictly_inside(m.group(2).strip("'\""), session_id):
                return (f"the file being written {path} has a recursive rm of {m.group(2)!r}: not strictly inside this "
                        "session's scratchpad")
    return None


def decide(payload: dict) -> str | None:
    tool, inp = payload.get("tool_name"), payload.get("tool_input") or {}
    sid = payload.get("session_id")
    if tool == "Bash":
        return check_bash(str(inp.get("command", "")), sid)
    if tool in ("Write", "Edit", "NotebookEdit"):
        text = inp.get("content") or inp.get("new_string") or inp.get("new_source") or ""
        return check_write(str(text), sid, str(inp.get("file_path", "")))
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0                                  # not a hook call: never block on unreadable input
    reason = decide(payload if isinstance(payload, dict) else {})
    if reason:
        print(f"BLOCKED by guard_destructive: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

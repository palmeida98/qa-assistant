"""
Which Python runs the framework's scripts (technique-workspaces.md section 2; plan A-08). Standard library only: any
Python 3 can run THIS file, so the question "which interpreter has PyYAML?" never needs PyYAML to be answered.

    python3 <qa>/scripts/qa_python.py            print the interpreter to use where the docs and skills say `python`
    python3 <qa>/scripts/qa_python.py --json     the same, with where it came from and what was tried
    python3 <qa>/scripts/qa_python.py run <script-or-module args ...>
                                                 run it with that interpreter (`run scripts/qa_report.py <ws> audit`,
                                                 `run -m adapters.excel.apply <ws> <batch> --dry-run`)

<qa> is the folder this file lives in (its parent's parent), so the checkout is found without the current directory and
without a user-specific path. Candidates, in order; the first that can import the framework's dependencies on Python
>= 3.12 wins:
  1. $QA_PYTHON (an explicit choice: if it is not usable the answer is an error naming it, never a silent fallback)
  2. <qa>/.venv (bin/python, or Scripts/python.exe on Windows); in a git worktree, then the main checkout's .venv
  3. the interpreter running this file
  4. python3.13, python3.12, python3, python (and `py -3` on Windows) found on PATH
No candidate is installed, upgraded or changed. When none is usable the exit is 3 and the message gives the commands that
create an environment for this checkout (it does not run them): a global install is never suggested.

Exit codes: 0 an interpreter was found (printed) · 3 none usable (what to do is printed) · 4 usage
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ("yaml", "openpyxl")                  # what every script needs; the rest is checked by its own script
MINIMUM = (3, 12)
# What git exports to a hook (pre-commit, commit-msg ...) to say WHICH repository is being committed. In a normal checkout GIT_INDEX_FILE
# is relative (".git/index"), so a `git init` in a temporary folder is not affected; in a LINKED WORKTREE GIT_DIR and GIT_INDEX_FILE are
# absolute, and any git command a test runs in a temporary repository would read and write the index of the repository being committed
# (hundreds of staged deletions, observed 11.11.2026). The command that `run` starts gets the environment of the folder it runs in, not
# the one of the hook that started it.
REPOSITORY_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_IMPLICIT_WORK_TREE")
PROBE = ("import sys\n"
         f"assert sys.version_info >= {MINIMUM}, 'Python %d.%d is older than {MINIMUM[0]}.{MINIMUM[1]}' "
         "% sys.version_info[:2]\n"
         + "".join(f"import {m}\n" for m in REQUIRED))


def venv_python(root: str = ROOT) -> str:
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(root, ".venv", *sub)


def usable(command: list[str]) -> tuple[bool, str]:
    """Whether this interpreter can import the dependencies on a supported Python; why not otherwise. Isolated mode
    (-I): the current directory and the environment's PYTHONPATH do not decide the answer."""
    try:
        done = subprocess.run([*command, "-I", "-c", PROBE], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"cannot be started ({type(e).__name__})"
    if done.returncode == 0:
        return True, "ok"
    last = (done.stderr.strip().splitlines() or ["exit " + str(done.returncode)])[-1]
    return False, last[:200]


def main_checkout(root: str = ROOT) -> str | None:
    """The main checkout when `root` is a git worktree (its `.git` is a file pointing into the main one), else None."""
    marker = os.path.join(root, ".git")
    if not os.path.isfile(marker):
        return None
    try:
        with open(marker, encoding="utf-8") as f:
            line = f.readline().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = os.path.normpath(os.path.join(root, line.partition(":")[2].strip()))
    parent = os.path.dirname(os.path.dirname(gitdir))              # <main>/.git/worktrees/<name> -> <main>/.git
    return os.path.dirname(parent) if os.path.basename(parent) == ".git" else None


def which_all(name: str, path: str | None) -> list[str]:
    """EVERY executable called `name` on PATH, in order (shutil.which gives only the first, and the first may be a
    Python without the dependencies that shadows a good one: a pre-commit hook puts its own environment first)."""
    found = []
    exts = [e for e in os.environ.get("PATHEXT", "").split(os.pathsep) if e] if os.name == "nt" else [""]
    for folder in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep):
        for ext in exts or [""]:
            candidate = os.path.join(folder, name + ext) if folder else name + ext
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK) and candidate not in found:
                found.append(candidate)
    return found


def candidates(environ=None, root: str = ROOT) -> list[tuple[str, list[str]]]:
    environ = os.environ if environ is None else environ
    out: list[tuple[str, list[str]]] = []
    if environ.get("QA_PYTHON"):
        out.append(("QA_PYTHON", [environ["QA_PYTHON"]]))
        return out                                   # an explicit choice is not mixed with a search
    out.append((".venv of the checkout", [venv_python(root)]))
    main = main_checkout(root)
    if main and os.path.realpath(main) != os.path.realpath(root):
        out.append((".venv of the main checkout (this is a worktree)", [venv_python(main)]))
    out.append(("the interpreter running this file", [sys.executable]))
    for name in ("python3.13", "python3.12", "python3", "python"):
        for path in which_all(name, environ.get("PATH")):
            out.append((f"{name} on PATH", [path]))
    if os.name == "nt" and shutil.which("py", path=environ.get("PATH")):
        out.append(("py -3 on PATH", [shutil.which("py", path=environ.get("PATH")), "-3"]))
    return out


def find(environ=None, root: str = ROOT) -> dict:
    """{"command": [...], "source": ...} for the first usable candidate, plus "tried" [(source, command, why not)]."""
    tried = []
    seen = set()
    for source, command in candidates(environ, root):
        key = tuple(command)
        if key in seen or not (os.path.isfile(command[0]) or shutil.which(command[0])):
            if key not in seen and (source == "QA_PYTHON" or source.startswith(".venv")):
                tried.append((source, command, "not found"))
            seen.add(key)
            continue
        seen.add(key)
        ok, why = usable(command)
        if ok:
            return {"command": command, "source": source, "tried": tried}
        tried.append((source, command, why))
    return {"command": None, "source": None, "tried": tried}


def quote(path: str) -> str:
    return f'"{path}"' if (" " in path or "(" in path) else path


def instructions(root: str = ROOT, tried=()) -> str:
    base = sys.executable if sys.version_info >= MINIMUM else "python3.12"
    venv = os.path.join(root, ".venv")
    lines = [("No Python found that can run the framework "
             f"(needs Python >= {MINIMUM[0]}.{MINIMUM[1]} with {', '.join(REQUIRED)}). Tried:")]
    lines += [f"  - {s}: {' '.join(c)} -> {why}" for s, c, why in tried] or ["  - nothing on this machine"]
    lines += ["", "Create an environment for this checkout (nothing was installed or changed; run these yourself):",
              f"  {base if ' ' not in base else quote(base)} -m venv {quote(venv)}",
              f"  {quote(venv_python(root))} -m pip install -r {quote(os.path.join(root, 'requirements.txt'))}",
              ("Then run this file again. Or set QA_PYTHON to an interpreter you already prepared. Do not install "
              "into the system Python.")]
    return "\n".join(lines)


def child_env(environ, pythonpath: str) -> dict:
    """The environment of the command `run` starts: PYTHONPATH set, git's repository-location variables removed."""
    env = {k: v for k, v in environ.items() if k not in REPOSITORY_VARIABLES}
    env["PYTHONPATH"] = pythonpath
    return env


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    found = find()
    if argv[:1] == ["--json"] or not argv or argv[0] == "run":
        pass
    else:
        print(f"usage: qa_python.py [--json | run <args...>] (got {argv[0]!r})", file=sys.stderr)
        return 4
    if found["command"] is None:
        print(instructions(ROOT, found["tried"]), file=sys.stderr)
        return 3
    if argv[:1] == ["run"]:
        args = argv[1:]
        if not args:
            print("usage: qa_python.py run <script.py | -m module> [args...]", file=sys.stderr)
            return 4
        if not args[0].startswith("-") and not os.path.isabs(args[0]) and not os.path.exists(args[0]):
            args[0] = os.path.join(ROOT, args[0])
        # -m modules (adapters.*, qa_audit) are importable from the checkout, whatever the current directory
        path = [ROOT, os.path.join(ROOT, "src")] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
        env = child_env(os.environ, os.pathsep.join(path))
        return subprocess.call([*found["command"], *args], env=env)
    if argv[:1] == ["--json"]:
        print(json.dumps({"python": found["command"], "source": found["source"],
                          "tried": [{"source": s, "command": c, "why": w} for s, c, w in found["tried"]]}, indent=1))
    else:
        print(" ".join(quote(p) for p in found["command"]))
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

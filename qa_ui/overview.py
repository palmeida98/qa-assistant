"""
A project's Overview, its credentials and its notes (docs/qa-ui.md sections 14.6 and 14.7).

    overview(ws, checks)          -> project.yaml as text and its main keys, the readiness, the git state, context.md,
                                     the .env variables it refers to (set or missing; never a value)
    git_state(ws)                 -> `workspace_git.py <ws> status`, its output and exit code
    write_env(ws, proj, name, v)  -> the value written to .env, write-only: never returned, logged or kept
    notes(ws) / read_note / save_note                 -> <ws>/notes/<name>.md, text only

Nothing here reads above the workspace nor follows a link out of it: notes/ and .env that are links are refused.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime

import yaml
from common import read_env, read_project, variable_names

from . import ROOT, workspace

NOTE_NAME = re.compile(r"[A-Za-z0-9_-]{1,80}")
NOTE_MAX = 200 * 1024                  # bytes of a note (section 14.7)
CONTEXT_MAX = 200 * 1024               # what of context.md the Overview shows
SECRET_MIN = 4                         # shorter .env values (e.g. "1", "yes") are not looked for in a note
GIT_TIMEOUT = 30


class BadValue(ValueError):
    """The request's value is refused (status 400). Its text never holds the value."""


def _is_link(path: str) -> bool:
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def _read_text(path: str, limit: int) -> str | None:
    if not os.path.isfile(path) or _is_link(path):
        return None
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read(limit)


def git_state(ws: str) -> dict:
    """`scripts/workspace_git.py <ws> status` as the user would run it (a repository or not, what is not committed
    or not pushed, the remote and whether it is private)."""
    cmd = [sys.executable, os.path.join(ROOT, "scripts", "workspace_git.py"), ws, "status"]
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=GIT_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return {"code": None, "output": f"workspace_git.py status did not end in {GIT_TIMEOUT} s"}
    return {"code": r.returncode, "output": (r.stdout + r.stderr).strip()}


def env_example_names(ws: str) -> list[str]:
    return sorted(read_env(os.path.join(ws, ".env.example")))


def writable_names(ws: str, proj: dict) -> list[str]:
    """The names a value can be typed for: those project.yaml refers to and those .env.example lists."""
    return sorted(set(variable_names(proj)) | set(env_example_names(ws)))


def env_list(ws: str, proj: dict) -> list[dict]:
    """Each writable name, set or missing, and where it comes from. Never a value."""
    env = read_env(os.path.join(ws, ".env"))
    referred = set(variable_names(proj))
    return [{"name": n, "set": bool(env.get(n)), "in_project": n in referred} for n in writable_names(ws, proj)]


def overview(ws: str, checks) -> dict:
    """GET /api/p/<id>/overview. A project.yaml that cannot be read is said here, with what can still be shown
    (section 14.2): the Overview is the one screen such a project keeps."""
    out = {"yaml": _read_text(os.path.join(ws, "project.yaml"), NOTE_MAX) or "", "error": None, "project": None,
           "checks": [], "env": [], "git": git_state(ws),
           "context": _read_text(os.path.join(ws, "context.md"), CONTEXT_MAX)}
    try:
        proj = read_project(ws)
    except (OSError, ValueError, yaml.YAMLError) as e:
        text = str(e).replace(os.path.join(ws, ""), "").replace(ws, ".")
        out["error"] = f"project.yaml cannot be read: {text}"
        return out
    if not isinstance(proj, dict):
        out["error"] = "project.yaml cannot be read: it is not a mapping of keys"
        return out
    envs = proj.get("environments") or {}
    out["project"] = {
        "name": str(proj.get("project") or os.path.basename(ws)), "company": str(proj.get("company") or ""),
        "tracker": proj.get("tracker"), "modules": [str(m) for m in proj.get("modules") or []],
        "environments": [{"key": str(k), "url": str((v or {}).get("url") or "") if isinstance(v, dict) else str(v or ""),
                          "purpose": str((v or {}).get("purpose") or "") if isinstance(v, dict) else ""}
                         for k, v in (envs.items() if isinstance(envs, dict) else [])],
        "roles": [str(r) for r in proj.get("roles") or []],
        "evidence": proj.get("evidence"), "resolved_bugs": proj.get("resolved_bugs")}
    out["checks"] = [{"ok": ok, "tool": tool, "detail": detail} for ok, tool, detail in checks(proj)]
    out["env"] = env_list(ws, proj)
    return out


def _env_line_name(line: str) -> str | None:
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None
    return s.removeprefix("export ").partition("=")[0].strip()


def _quoted(value: str) -> str:
    """The value as read_env() gives it back: quoted when it has outer spaces or starts and ends with a quote."""
    if value != value.strip() or (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"):
        return f'"{value}"'
    return value


def write_env(ws: str, proj: dict, name, value) -> dict:
    """POST /api/p/<id>/env (section 14.6): the line of `name` in .env replaced, or added; the other lines kept;
    mode 600 on POSIX. Only a name project.yaml refers to or .env.example lists; never a value with a line break.
    The value is never returned, logged or written anywhere but .env."""
    if not isinstance(name, str) or name not in writable_names(ws, proj):
        raise BadValue("only a variable that project.yaml refers to, or that .env.example lists, can be written")
    if not isinstance(value, str) or not value:
        raise BadValue(f"a value is needed for {name}")
    if any(c in value for c in "\r\n\x00"):
        raise BadValue(f"the value of {name} cannot hold a line break")
    path = os.path.join(ws, ".env")
    if _is_link(path):
        raise workspace.Refused(".env is a link: it is not written through (fill it in by hand)")
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    new = f"{name}={_quoted(value)}"
    done = False
    for i, line in enumerate(lines):
        if _env_line_name(line) == name:
            if done:
                lines[i] = ""                          # a second line of the same name would win: removed
            else:
                lines[i], done = new, True
    if not done:
        lines.append(new)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(path, 0o600)
    return {"name": name, "set": True}


# ---------------------------------------------------------------------------------------------------- notes

def _notes_dir(ws: str, create: bool = False) -> str | None:
    path = os.path.join(ws, "notes")
    if _is_link(path):
        raise workspace.Refused("notes/ is a link: the interface does not follow it")
    if not os.path.isdir(path):
        if not create:
            return None
        os.makedirs(path)
    return path


def _note_path(ws: str, name, create: bool = False) -> str | None:
    if not isinstance(name, str) or not NOTE_NAME.fullmatch(name):
        raise BadValue("a note's name has letters, digits, - and _ only (at most 80)")
    folder = _notes_dir(ws, create)
    return os.path.join(folder, name + ".md") if folder else None


def notes(ws: str) -> dict:
    """GET /api/p/<id>/notes: the notes, newest first."""
    folder = _notes_dir(ws)
    out = []
    if folder:
        for entry in os.scandir(folder):
            stem, ext = os.path.splitext(entry.name)
            if ext == ".md" and NOTE_NAME.fullmatch(stem) and entry.is_file(follow_symlinks=False):
                st = entry.stat(follow_symlinks=False)
                out.append({"name": stem, "size": st.st_size, "modified": st.st_mtime})
    out.sort(key=lambda n: (-n["modified"], n["name"]))
    for n in out:
        n["modified"] = datetime.fromtimestamp(n["modified"]).isoformat(timespec="seconds")
    return {"notes": out, "max_bytes": NOTE_MAX}


def read_note(ws: str, name) -> dict:
    path = _note_path(ws, name)
    if not path or not os.path.isfile(path) or _is_link(path):
        raise workspace.NotFound(f"no note {name!r}")
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return {"name": name, "text": f.read()}


def save_note(ws: str, name, text, new: bool = False) -> dict:
    """POST /api/p/<id>/notes/<name> (section 14.7): written as text; refused over 200 KB, over an existing note
    when `new`, and when it holds the value of a .env variable (the error names the variable, never the value)."""
    if not isinstance(text, str):
        raise BadValue("`text` (the note) is needed")
    if len(text.encode("utf-8")) > NOTE_MAX:
        raise BadValue(f"a note is at most {NOTE_MAX // 1024} KB")
    for var, value in read_env(os.path.join(ws, ".env")).items():
        if len(value) >= SECRET_MIN and value in text:
            raise BadValue(f"the note holds the value of the .env variable {var}: a credential lives only in .env")
    path = _note_path(ws, name, create=True)
    if _is_link(path):
        raise workspace.Refused(f"the note {name!r} is a link: it is not written through")
    if new and os.path.exists(path):
        raise workspace.Refused(f"a note {name!r} already exists")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return {"name": name, "saved": True}

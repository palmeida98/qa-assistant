"""
Code shared by a workspace's scripts (plan 7.8): reading project.yaml and .env,
the names of the .env variables project.yaml refers to, redacting secrets in the
evidence, deciding whether a path may be written (plan 15.2), which checkout
this is, and the lock one writer at a time takes on a workspace (workspace_lock).
Nothing here writes to a workspace except that lock file, which exists only while
a writer works.

Used by api_request.py, audit_a11y.py, run_mobile.py, build_dashboard.py,
device.py, workspace_git.py and validate_project.py (from the scripts/ folder,
which is on their sys.path).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import time

import yaml

REDACTED = "«redacted»"
SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
                  "x-auth-token"}
SECRET_KEY = re.compile(r"token|password|passwd|secret|api[_-]?key", re.IGNORECASE)
SECRET_MIN = 4      # a shorter .env value ("1", "yes") is a flag, not a secret: looked for in text it would wreck it


def find_adb() -> str | None:
    """adb on the PATH, else in the Android SDK's platform-tools (ANDROID_HOME, ANDROID_SDK_ROOT, the default
    folders): a GitHub-hosted runner has the SDK but not adb on the PATH (11.8 pilot)."""
    exe = shutil.which("adb")
    if exe:
        return exe
    roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        roots.append(os.path.join(os.environ["LOCALAPPDATA"], "Android", "Sdk"))
    roots += [os.path.expanduser(os.path.join("~", "Android", "Sdk")),
              os.path.expanduser(os.path.join("~", "Library", "Android", "sdk"))]
    name = "adb.exe" if os.name == "nt" else "adb"
    return next((os.path.join(r, "platform-tools", name) for r in roots
                 if r and os.path.isfile(os.path.join(r, "platform-tools", name))), None)


class OutsideWorkspace(ValueError):
    """A path to write leaves the workspace or enters history/ (technique-workspaces.md section 7)."""


EXTENDED_PREFIX = "\\\\?\\"          # the extended-length prefix of a Windows path: backslash backslash ? backslash
EXTENDED_UNC = EXTENDED_PREFIX + "UNC\\"


def plain_path(path: str) -> str:
    """A Windows path without the extended-length prefix. realpath answers in that form for a path that is being
    created at that moment, and two forms of one path have no common drive (audit finding D-02: a false 'outside the
    workspace')."""
    if path.startswith(EXTENDED_UNC):
        return "\\\\" + path[len(EXTENDED_UNC):]
    if path.startswith(EXTENDED_PREFIX):
        return path[len(EXTENDED_PREFIX):]
    return path


def _real(path: str) -> str:
    return os.path.normcase(plain_path(os.path.realpath(path)))


def _is_link(path: str) -> bool:
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def workspace_path(workspace: str, *parts: str) -> str:
    """`parts` joined under the workspace, for writing. `..`, links and junctions are resolved before deciding: a path
    that ends outside the workspace or inside history/ raises OutsideWorkspace. An absolute part replaces the
    workspace, as in os.path.join, and is checked the same way. The folder is resolved, and the last part only when it
    is itself a link: on Windows realpath of a file that is being deleted at that moment answers a path in
    the NTFS folder of deleted files, and a write that was inside the workspace was refused (audit finding D-02)."""
    path = os.path.join(workspace, *parts)
    base = _real(workspace)
    parent, name = os.path.split(path)
    if name in ("", ".", "..") or _is_link(path):
        real = _real(path)
    else:
        real = os.path.join(_real(parent), os.path.normcase(name))
    try:
        inside = os.path.commonpath([base, real]) == base
    except ValueError:      # another drive
        inside = False
    if not inside:
        raise OutsideWorkspace(f"{path} is outside the workspace {workspace}: nothing was written")
    history = os.path.join(base, "history")
    if real == history or real.startswith(history + os.sep):
        raise OutsideWorkspace(f"{path} resolves inside history/, which is immutable (methodology 13)")
    return path


def replace_file(src: str, dst: str, attempts: int = 20, delay: float = 0.05) -> None:
    """os.replace that waits out a moment of another process holding `dst` open: on Windows an indexer or an antivirus
    does, and PermissionError is how it shows (audit finding D-02). The error stays if it lasts."""
    for n in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if n == attempts - 1:
                raise
            time.sleep(delay)


LOCK_NAME = ".qa-assistant.lock"
LOCK_STALE_SECONDS = 15 * 60        # a lock older than this has no holder worth waiting for
LOCK_WAIT_SECONDS = 30.0
LOCK_WAIT_ENV = "QA_LOCK_WAIT"      # seconds to wait for another writer before giving up


class WorkspaceBusy(RuntimeError):
    """Another process holds the workspace's lock: nothing was written."""


def pid_alive(pid: int) -> bool:
    """Whether a process with this id exists on this computer."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5             # access denied: it exists; anything else: it does not
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_holder(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        holder = doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        holder = {}
    if not isinstance(holder.get("at"), (int, float)):
        try:
            holder["at"] = os.path.getmtime(path)
        except OSError:
            holder["at"] = time.time()
    return holder


def _lock_is_stale(holder: dict) -> bool:
    """Old enough to have no holder worth waiting for, or held by a process of this computer that no longer exists."""
    if time.time() - holder["at"] > LOCK_STALE_SECONDS:
        return True
    return holder.get("host") == socket.gethostname() and isinstance(holder.get("pid"), int)         and not pid_alive(holder["pid"])


def _remove_lock(path: str) -> None:
    for _ in range(25):     # on Windows a file another process has just opened to read cannot be deleted for a moment
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.02)


@contextlib.contextmanager
def workspace_lock(workspace: str, wait: float | None = None):
    """One writer at a time on a workspace's memory and run logs (technique-workspaces.md section 9). A second writer
    waits `wait` seconds (default $QA_LOCK_WAIT or 30), then raises WorkspaceBusy and writes nothing. A lock whose
    process is gone, or that is older than 15 minutes, is taken over. Not reentrant: take it once, around the read, the
    change and the write."""
    path = workspace_path(workspace, LOCK_NAME)
    if wait is None:
        try:
            wait = float(os.environ.get(LOCK_WAIT_ENV) or LOCK_WAIT_SECONDS)
        except ValueError:
            wait = LOCK_WAIT_SECONDS
    deadline = time.monotonic() + wait
    holder: dict = {}
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):      # PermissionError: Windows, a file being deleted
            holder = _lock_holder(path)
            if os.path.exists(path) and _lock_is_stale(holder) and _lock_holder(path) == holder:
                _remove_lock(path)
                continue
            if time.monotonic() >= deadline:
                since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(holder.get("at") or time.time()))
                raise WorkspaceBusy(
                    f"Nothing was written: another process is writing this workspace (pid {holder.get('pid', '?')} on "
                    f"{holder.get('host', '?')}, since {since}). Wait for it to finish and run the same command again; "
                    f"if that process is gone, delete {path}.") from None
            time.sleep(0.05)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "host": socket.gethostname(), "at": time.time()}, f)
    try:
        yield path
    finally:
        _remove_lock(path)


def main_checkout(root: str) -> str:
    """The main checkout of a git checkout, read from .git without running git: `root` itself, unless `root` is a
    worktree, whose .git file points to <main>/.git/worktrees/<name>. Also `root` when there is no git at all."""
    dot = os.path.join(root, ".git")
    if not os.path.isfile(dot):
        return root
    try:
        with open(dot, encoding="utf-8") as f:
            m = re.match(r"gitdir:\s*(.+)", f.read().strip())
        if not m:
            return root
        gitdir = os.path.normpath(os.path.join(root, m.group(1).strip()))
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as f:
            common = os.path.normpath(os.path.join(gitdir, f.read().strip()))
    except OSError:
        return root
    return os.path.dirname(common) if os.path.basename(common) == ".git" else root


def git_state(root: str) -> dict:
    """{"commit", "branch", "dirty"} of a checkout; None values without git or outside a repository. Read only."""
    def ask(*args):
        try:
            r = subprocess.run(["git", "--no-optional-locks", "-C", root, *args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    commit = ask("rev-parse", "--short", "HEAD")
    if commit is None:
        return {"commit": None, "branch": None, "dirty": None}
    status = ask("status", "--porcelain")
    return {"commit": commit, "branch": ask("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": None if status is None else bool(status)}


def read_project(workspace: str) -> dict:
    """The workspace's project.yaml ({} if empty). FileNotFoundError if it does not exist (with a hint for a
    workspace from before the translation, plan 8.11)."""
    path = os.path.join(workspace, "project.yaml")
    if not os.path.isfile(path):
        hint = (": the workspace is from before the translation, convert it with scripts/migrate_workspace.py"
                if os.path.isfile(os.path.join(workspace, "projeto.yaml")) else "")
        raise FileNotFoundError(f"{path} does not exist{hint}")
    with open(path, encoding="utf-8-sig") as f:
        return yaml.safe_load(f) or {}


VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def variable_names(proj) -> list[str]:
    """Names of the .env variables project.yaml refers to, sorted: the values of the keys ending in `_var`
    (e.g. api.credentials.<role>.user_var, accessibility.login.pass_var) and the items of the `variables` lists
    (e.g. mobile.variables), at any depth. Only names: project.yaml never has values."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and str(k).endswith("_var") and VARIABLE_NAME.fullmatch(v):
                    found.add(v)
                elif str(k) == "variables" and isinstance(v, list):
                    found.update(x for x in v if isinstance(x, str) and VARIABLE_NAME.fullmatch(x))
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(proj)
    return sorted(found)


MOBILE_INPUTS = ("adb", "driver")
SERIAL_ENV = "QA_MOBILE_SERIAL"
INPUT_ENV = "QA_MOBILE_INPUT"


def mobile_target(m: dict, serial: str | None = None, input_: str | None = None,
                  environ=None) -> tuple[str | None, str]:
    """(serial, input) of the mobile target (technique-mobile.md §1.2 and §11; plan 11.8). Each one, in order: the
    command-line flag, the environment variable (QA_MOBILE_SERIAL / QA_MOBILE_INPUT: a runner sets them to its
    emulator and adb, over the physical phone and the driver of mobile.serial / mobile.input), project.yaml, the
    default (no serial: the only device connected; input adb). An empty variable counts as unset. ValueError for an
    input outside adb | driver, naming where it came from."""
    environ = os.environ if environ is None else environ
    m = m or {}
    serial = serial or environ.get(SERIAL_ENV, "").strip() or m.get("serial")
    for value, source in ((input_, "--input"), (environ.get(INPUT_ENV, "").strip(), INPUT_ENV),
                          (m.get("input"), "mobile.input")):
        if value:
            if value not in MOBILE_INPUTS:
                raise ValueError(f"{source} {value!r}: adb or driver (technique-mobile.md §1.2)")
            return (str(serial) if serial else None), value
    return (str(serial) if serial else None), "adb"


def read_env(path: str) -> dict[str, str]:
    """KEY=VALUE per line; ignores comments, empty lines and 'export '."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.removeprefix("export ").partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v
    return out


def read_workspace_env(workspace: str) -> dict[str, str]:
    """The workspace's .env (empty if it does not exist)."""
    return read_env(os.path.join(workspace, ".env"))


class Redactor:
    """Redacts secrets: by header name, by JSON key name and by value."""

    def __init__(self, secrets_by_value: dict[str, str]):
        # longest values first, so that a secret containing another is replaced whole
        self.values = sorted(((v, k) for k, v in secrets_by_value.items() if v and len(v) >= SECRET_MIN),
                             key=lambda x: -len(x[0]))

    def text(self, s: str) -> str:
        for value, name in self.values:
            s = s.replace(value, f"«{name}»")
        return s

    def headers(self, h: dict) -> dict:
        return {k: (REDACTED if k.lower() in SECRET_HEADERS else self.text(str(v))) for k, v in h.items()}

    def json(self, obj):
        if isinstance(obj, dict):
            return {k: (REDACTED if SECRET_KEY.search(str(k)) and v not in (None, "") else self.json(v))
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.json(x) for x in obj]
        if isinstance(obj, str):
            return self.text(obj)
        return obj

    def body(self, text: str | None, max_chars: int):
        if text in (None, ""):
            return text
        try:
            return self.json(json.loads(text))
        except ValueError:
            t = self.text(text)
            return t if len(t) <= max_chars else t[:max_chars] + f"… (+{len(t) - max_chars} characters)"

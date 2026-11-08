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
import tempfile
import threading
import time
import uuid
from datetime import datetime

import yaml

REDACTED = "«redacted»"
SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
                  "x-auth-token"}
SECRET_KEY = re.compile(r"token|password|passwd|secret|api[_-]?key", re.IGNORECASE)
SECRET_MIN = 4          # a value with no sensitive name, shorter than this, is taken for a flag
WHOLE_WORD = 8          # a shorter value is matched as a whole word or number
SENSITIVE_WORDS = {"PIN", "PASS", "PWD", "PASSCODE", "OTP", "CVV", "PAT", "AUTH", "CREDENTIAL", "CREDENTIALS", "KEY",
                   "COOKIE", "SESSION"}
COMMON_VALUES = {"true", "false", "yes", "no", "on", "off", "none", "null", "test", "prod", "dev", "local", "debug"}


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
    rel = os.path.relpath(real, base)
    # casefolded: macOS and Windows file systems ignore case, so History/ is history/ (audit 06.11.2026)
    if rel != "." and rel.split(os.sep)[0].casefold() == "history":
        raise OutsideWorkspace(f"{path} resolves inside history/, which is immutable (methodology 13)")
    return path


def atomic_write(path: str, data: bytes | str) -> None:
    """Writes `path` whole or not at all: a new temporary file next to it (created exclusively, so a link planted at a
    predictable `<file>.tmp` is never followed), flushed to the disk, then put in place. `path` is one that
    workspace_path returned. A stopped write leaves only a `.<name>.*.tmp` that the next write ignores."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())        # on the disk before it takes the place of the old one
        replace_file(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


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
LOCK_STALE_SECONDS = 15 * 60        # no heartbeat for this long: the holder on another machine is gone
LOCK_HEARTBEAT_SECONDS = 60.0
LOCK_UNNAMED_SECONDS = 10.0      # a lock file that names no holder after this long is abandoned
LOCK_WAIT_SECONDS = 30.0
LOCK_WAIT_ENV = "QA_LOCK_WAIT"      # seconds to wait for another writer before giving up
_held = threading.local()           # the locks this thread holds: {path: depth}


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
    # a zombie (ended, not yet collected by its parent) still answers kill 0: it holds nothing
    try:        # Linux
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            return f.read().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        pass
    return _ps_state(pid) not in ("Z", "")


def _ps_state(pid: int) -> str | None:
    """The first letter of a process's state from `ps` (macOS and the BSDs have no /proc): "Z" for a zombie, "" for no
    such process, None when ps cannot say (then the process counts as alive: a lock is never taken from a live one)."""
    import subprocess
    try:
        r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode not in (0, 1):
        return None
    state = r.stdout.strip()
    return state[:1] if state else ""


def process_started(pid: int) -> str | None:
    """When a process started, as an opaque text, or None when it cannot be known. A process id can be reused: the id
    and this text together name one process."""
    try:
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                times = [ctypes.c_ulonglong() for _ in range(4)]
                ok = kernel.GetProcessTimes(ctypes.c_void_p(handle), *[ctypes.byref(t) for t in times])
                return str(times[0].value) if ok else None
            finally:
                kernel.CloseHandle(ctypes.c_void_p(handle))
        if os.path.exists(f"/proc/{pid}/stat"):
            with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
                return f.read().rpartition(")")[2].split()[19]
        done = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False)
        return done.stdout.strip() or None
    except (OSError, IndexError, ValueError):
        return None


def _lock_holder(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _lock_age(path: str) -> float:
    """Seconds since the holder last said it is alive (its heartbeat touches the file)."""
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return 0.0


def _lock_is_abandoned(path: str, holder: dict) -> bool:
    """Only what can be shown: on this computer, its process is gone, or the id now belongs to another process. A live
    holder here is never abandoned, however long it takes. Elsewhere, or with a file nobody can read, only the
    heartbeat can tell."""
    pid = holder.get("pid")
    if not holder:      # empty or unreadable: a writer stopped between creating the file and naming itself
        return _lock_age(path) > LOCK_UNNAMED_SECONDS
    if holder.get("host") == socket.gethostname() and isinstance(pid, int):
        if not pid_alive(pid):
            return True
        started = process_started(pid)
        return bool(started and holder.get("started") and started != holder["started"])
    return _lock_age(path) > LOCK_STALE_SECONDS


def _remove_lock(path: str) -> None:
    for _ in range(25):     # on Windows a file another process has just opened to read cannot be deleted for a moment
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.02)


def _take_over(path: str, holder: dict) -> None:
    """Removes an abandoned lock, one waiter at a time: without this, a second waiter that read the old lock could
    remove the new one the first has just taken. The small takeover file is itself recovered by its age."""
    guard = path + ".takeover"
    try:
        os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except (FileExistsError, PermissionError):
        if _lock_age(guard) > 60:
            _remove_lock(guard)
        return
    try:
        if _lock_holder(path) == holder and _lock_is_abandoned(path, holder):
            _remove_lock(path)
    finally:
        _remove_lock(guard)


def _heartbeat(path: str, stop: threading.Event) -> None:
    while not stop.wait(LOCK_HEARTBEAT_SECONDS):
        with contextlib.suppress(OSError):
            os.utime(path)


def _create_lock(path: str, token: str) -> None:
    """Creates the lock already naming its holder: the holder is written to a file of its own, then linked to the
    lock's name, which fails if the lock exists. A lock is therefore never seen empty. Where links are not available,
    the lock is created exclusively and named right after. FileExistsError when another holder has it."""
    holder = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "started": process_started(os.getpid()),
                         "token": token, "at": time.time()})
    tmp = f"{path}.{token}.new"
    with open(tmp, "x", encoding="utf-8") as f:
        f.write(holder)
    try:
        try:
            os.link(tmp, path)
            return
        except FileExistsError:
            raise
        except (AttributeError, NotImplementedError, OSError):
            pass
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(holder)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


@contextlib.contextmanager
def workspace_lock(workspace: str, wait: float | None = None):
    """One writer at a time on a workspace's memory and run logs (technique-workspaces.md section 7). A second writer
    waits `wait` seconds (default $QA_LOCK_WAIT or 30), then raises WorkspaceBusy and writes nothing. An abandoned lock
    (_lock_is_abandoned) is taken over; a live one never is. Reentrant in one thread: an operation inside another keeps
    the same lock. The lock file names its holder, and a holder only ever removes its own."""
    path = workspace_path(workspace, LOCK_NAME)
    held = _held.__dict__.setdefault("paths", {})
    key = os.path.normcase(_real(path))     # the same workspace through a link or another case is the same lock
    if key in held:
        held[key] += 1
        try:
            yield path
        finally:
            held[key] -= 1
        return
    if wait is None:
        try:
            wait = float(os.environ.get(LOCK_WAIT_ENV) or LOCK_WAIT_SECONDS)
        except ValueError:
            wait = LOCK_WAIT_SECONDS
    deadline = time.monotonic() + wait
    token = uuid.uuid4().hex
    while True:
        try:
            _create_lock(path, token)
            break
        except (FileExistsError, PermissionError):      # PermissionError: Windows, a file being deleted
            holder = _lock_holder(path)
            if os.path.exists(path) and _lock_is_abandoned(path, holder):
                _take_over(path, holder)
                continue
            if time.monotonic() >= deadline:
                raise WorkspaceBusy(
                    f"Nothing was written: another process is writing this workspace (pid {holder.get('pid', '?')} on "
                    f"{holder.get('host', '?')}). Wait for it to finish and run the same command again; if that "
                    f"process is stuck, stop it, and the lock {path} is recovered by itself.") from None
            time.sleep(0.05)
    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(path, stop), daemon=True).start()
    held[key] = 1
    try:
        yield path
    finally:
        del held[key]
        stop.set()
        if _lock_holder(path).get("token") == token:
            _remove_lock(path)


BUILD_PARTS = re.compile(r"(\d+(?:\.\d+)*)\s*\(\s*(?:code\s*)?(\d+)\s*\)", re.IGNORECASE)


def observed_build(app_id: str, version_name: str, version_code: str) -> str:
    """A build as a run records it: `pt.demo.app 1.0.0 (34)` (technique-mobile.md section 1.4)."""
    return f"{app_id} {version_name} ({version_code})"


def same_build(a: str, b: str) -> bool:
    """Two writings of one build. When both hold a version and a code, those decide (the app id and the word `code`
    do not count); otherwise the texts, without case or repeated blanks."""
    pa, pb = BUILD_PARTS.search(a or ""), BUILD_PARTS.search(b or "")
    if pa and pb:
        return pa.groups() == pb.groups()
    norm = lambda s: " ".join(str(s or "").casefold().split())
    return bool(norm(a)) and norm(a) == norm(b)


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
MOBILE_PLATFORMS = ("android", "ios")
PLATFORM_ENV = "QA_MOBILE_PLATFORM"
WDA_URL_ENV = "QA_WDA_URL"
WDA_URL_DEFAULT = "http://localhost:8100"


def mobile_platform(m: dict, platform: str | None = None, environ=None) -> str:
    """The platform `device.py` drives (technique-mobile.md §1.3): the --platform flag, the environment variable
    QA_MOBILE_PLATFORM, mobile.platform, else android. An empty variable counts as unset. ValueError for a
    platform outside android | ios, naming where it came from."""
    environ = os.environ if environ is None else environ
    for value, source in ((platform, "--platform"), (environ.get(PLATFORM_ENV, "").strip(), PLATFORM_ENV),
                          ((m or {}).get("platform"), "mobile.platform")):
        if value:
            if value not in MOBILE_PLATFORMS:
                raise ValueError(f"{source} {value!r}: android or ios (technique-mobile.md §1.3)")
            return str(value)
    return "android"


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


def env_value(raw: str) -> str:
    """What follows `=` in a line of .env (docs/technique-workspaces.md section 7). Quoted: what is between the quotes,
    `#` included, and a comment may follow the closing quote. Unquoted: up to a `#` that follows a space; a `#` inside
    a word is part of the value. An unbalanced quote is kept as it is."""
    text = raw.strip()
    if text[:1] in ('"', "'"):
        m = re.fullmatch(rf"{text[0]}(.*){text[0]}\s*(?:#.*)?", text)
        if m:
            return m.group(1)
        return text
    m = re.search(r"\s#", raw)
    return (raw[:m.start()] if m else raw).strip()


def env_quoted(value: str) -> str:
    """A value as it must be written so that env_value() gives it back."""
    if value != value.strip() or re.search(r"\s#", value) or value[:1] in ('"', "'", "#"):
        return f'"{value}"'
    return value


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
            out[k.strip()] = env_value(v)
    return out


def env_inline_comments(path: str) -> list[str]:
    """The names of the variables whose unquoted value is cut at ` #`: a comment, or a password written without quotes
    from before this rule. Names only: the user decides."""
    names = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.removeprefix("export ").partition("=")
                    if v.strip()[:1] not in ('"', "'") and re.search(r"\s#", v):
                        names.append(k.strip())
    return names


def read_workspace_env(workspace: str) -> dict[str, str]:
    """The workspace's .env (empty if it does not exist)."""
    return read_env(os.path.join(workspace, ".env"))


def sensitive_name(name: str) -> bool:
    """A variable whose name says it holds a secret."""
    return bool(SECRET_KEY.search(str(name))) or bool(SENSITIVE_WORDS & set(re.split(r"[^A-Z0-9]+", str(name).upper())))


class Redactor:
    """Redacts secrets: by header name, by JSON key name and by value (technique-workspaces.md section 7).

    A value is sensitive when the caller says so (`sensitive`: names, or True for all of them) or its name does: it is
    always looked for, whatever its length. Any other value is left alone when it is shorter than `minimum` or a common
    word (HEADLESS=1, DEBUG=true): a flag, not a secret. A value under WHOLE_WORD characters is matched as a whole
    word or number, so a PIN 4821 is hidden and 148210 stays as it is."""

    def __init__(self, secrets_by_value: dict[str, str], sensitive=(), minimum: int = SECRET_MIN):
        kept = []
        for k, v in secrets_by_value.items():
            if not v:
                continue
            if sensitive is True or k in sensitive or sensitive_name(k) or \
                    (len(v) >= minimum and v.casefold() not in COMMON_VALUES):
                kept.append((v, k))
        # longest values first, so that a secret containing another is replaced whole
        self.values = sorted(kept, key=lambda x: -len(x[0]))
        self.patterns = [(re.compile(rf"(?<![0-9A-Za-z]){re.escape(v)}(?![0-9A-Za-z])") if len(v) < WHOLE_WORD else None,
                          v, k) for v, k in self.values]

    def text(self, s: str) -> str:
        for pattern, value, name in self.patterns:
            s = pattern.sub(lambda _m, n=name: f"«{n}»", s) if pattern else s.replace(value, f"«{name}»")
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
        if isinstance(obj, (int, float)) and not isinstance(obj, bool) and self.text(str(obj)) != str(obj):
            return self.text(str(obj))      # a PIN in a JSON number is the same PIN
        return obj

    def body(self, text: str | None, max_chars: int):
        if text in (None, ""):
            return text
        try:
            return self.json(json.loads(text))
        except ValueError:
            t = self.text(text)
            return t if len(t) <= max_chars else t[:max_chars] + f"… (+{len(t) - max_chars} characters)"


# ---------------------------------------------------------------------------------------------------------------
# Times of a run log (methodology 7.1): "ts" is the time of the execution, "recorded_at" the time it was written.
# ---------------------------------------------------------------------------------------------------------------
WHEN = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")


def parse_when(text: str, now: datetime | None = None) -> str:
    """An execution time given by the user, as the string a run log keeps (ISO 8601, to the second).

    A time needs a date AND a time (`2026-11-05T16:21`, `2026-11-05 16:21:00`): a bare date is refused, it is not
    guessed. A time without a zone stays WITHOUT one (the same convention as every `ts` already recorded: the clock of the
    machine that wrote it); a zone is neither assigned nor converted: `Z` or `+01:00` are kept as written (`Z` as
    `+00:00`). A time later than `now` is refused. A ValueError says what is wrong."""
    raw = str(text or "").strip()
    if not WHEN.fullmatch(raw):
        raise ValueError(f"{raw!r} is not a date and time (expected YYYY-MM-DDTHH:MM[:SS], optionally with Z or +HH:MM; "
                         "a date alone is not enough, and 'unknown' says the time is not known)")
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{raw!r} is not a valid date and time") from None
    now = now or datetime.now()
    limit = now.astimezone() if now.tzinfo is None and when.tzinfo is not None else now
    if when > limit:
        raise ValueError(f"{raw!r} is in the future (now is {limit.isoformat(timespec='seconds')}): an execution has "
                         "already happened when it is recorded")
    return when.isoformat(timespec="seconds")


def ts_key(value) -> str:
    """What a result is ordered by: the time as a string that compares correctly. A time without a zone is kept as it
    is (the existing convention); one with a zone is expressed in the local time of this machine so both compare in
    the same scale. A value that is not a time (an old run log) keeps its text: ordering as before. Empty stays empty
    (no time: ordered as the oldest)."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return text
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return when.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------------------------------
# The priority of a new bug (A-04): the project's schema decides whether it is required; the user decides its value.
# ---------------------------------------------------------------------------------------------------------------
def defect_priority_rule(proj: dict) -> dict:
    """What the project's schema says about the priority of a NEW bug: {"required": bool, "accepted": [...],
    "where": the project.yaml key}. Excel: required as soon as `excel.defects.priorities` lists values (the adapter
    refuses a new defect without one); Jira / Azure DevOps: optional, checked against `bugs.priorities` when given."""
    tracker = proj.get("tracker")
    if tracker == "excel":
        excel = proj.get("excel") if isinstance(proj.get("excel"), dict) else {}
        values = (excel.get("defects") or {}).get("priorities") or []
        return {"required": bool(values), "accepted": [str(v) for v in values], "where": "excel.defects.priorities"}
    section = {"jira": "jira", "azure-devops": "azure_devops"}.get(tracker)
    bugs = (proj.get(section) or {}).get("bugs") if section else None
    values = (bugs or {}).get("priorities") if isinstance(bugs, dict) else None
    values = list(values) if isinstance(values, (list, dict)) else []
    return {"required": False, "accepted": [str(v) for v in values], "where": f"{section}.bugs.priorities"}


def defect_priority_problem(proj: dict, new_defect: dict) -> str | None:
    """Why this new bug has no valid priority yet, or None. The same test as the Excel adapter's (the priority, else
    the severity, must be one of the accepted values), made before the bug is shown as ready."""
    rule = defect_priority_rule(proj)
    value = new_defect.get("priority", new_defect.get("severity")) if isinstance(new_defect, dict) else None
    accepted = rule["accepted"]
    if value in (None, ""):
        if rule["required"]:
            return (f"the project requires a priority for a new bug ({rule['where']}: {', '.join(accepted)}): ask the "
                    "user to decide it; it is never filled in by the assistant")
        return None
    if accepted and str(value) not in accepted:
        return f"priority {value!r} is not one of the project's ({rule['where']}: {', '.join(accepted)})"
    return None

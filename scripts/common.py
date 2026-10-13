"""
Code shared by a workspace's scripts (plan 7.8): reading project.yaml and .env,
the names of the .env variables project.yaml refers to, and redacting secrets in
the evidence. Read only; nothing here writes to the workspace.

Used by api_request.py, audit_a11y.py, run_mobile.py, build_dashboard.py,
device.py, workspace_git.py and validate_project.py (from the scripts/ folder,
which is on their sys.path).
"""

from __future__ import annotations

import json
import os
import re
import shutil

import yaml

REDACTED = "«redacted»"
SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
                  "x-auth-token"}
SECRET_KEY = re.compile(r"token|password|passwd|secret|api[_-]?key", re.IGNORECASE)


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
        self.values = sorted(((v, k) for k, v in secrets_by_value.items() if v), key=lambda x: -len(x[0]))

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

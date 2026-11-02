"""
Drives an Android device or emulator through adb for the conversational mode
(technique-mobile.md §2 and §4.1; plan, task 9.1).

    python scripts/device.py <workspace> [--serial S] <subcommand> ...

    check [--batch B]                       one report: device, app, driver, build, environment, session; ready or not
    dump [--capture NAME] [--batch B]       visible text, one line per node: [x1,y1][x2,y2] <text>
    tap TEXT [--index N] | tap --xy X Y     exact text first (case-insensitive), then contains
    type TEXT|--file F [--field LABEL] [--via auto|adb|maestro]
                                            ASCII through `input text`; any other text through Maestro
    send TEXT|--file F [--capture NAME] [--send-label L] [--field LABEL] [--timeout 200] [--samples N]
                                            type, tap send, wait until the screen is stable, print what is new;
                                            --samples keeps timed readings of a streamed answer (technique 4.1)
    shot NAME [--batch B]                   screenshot to <workspace>/evidence/<batch>/<NAME>.png
    link URL [--package P]                  opens a deep link or a login callback with `am start`
    swipe X1 Y1 X2 Y2 [--ms 300]            a swipe (e.g. to scroll a long answer)
    back                                    the system's back
    launch [--restart] [--timeout 60]       opens mobile.app_id, waits for it and prints its screen
    clear [--field LABEL]                   empties the focused field (or the field LABEL, tapped first)

- `check` reports everything and stops at nothing (technique-mobile.md 1.3): the device, the app, the driver (its
  setting and whether it answers are two facts), the observed build against the intended one
  (mobile.build.version), the environment, the session (mobile.session: signed_in / signed_out texts; else unknown),
  and `ready: yes` or NOT READY with what to do. It changes nothing on the device. --batch compares the observed build
  with the run log's: a build that changed during a batch stops it.
- project.yaml, mobile: section: app_id (required), serial (optional; with
  none, the only device connected), input (adb | driver; default adb). Each of
  serial and input: --serial / --input > the environment variable
  QA_MOBILE_SERIAL / QA_MOBILE_INPUT (a runner's emulator and adb, plan 11.8,
  technique-mobile.md §11) > project.yaml > the default. send_label (text or
  list), ignore_labels (the app's chrome, dropped from `send`), evidence_batch
  (default: today, YYYYMMDD) and browser (package that opens `link`).
- Privacy: dump, shot, send, tap and type act only with mobile.app_id in the
  foreground; a system permission dialog is allowed for dump and tap, never for
  shot. `type`/`send` refuse a text that contains a value of the workspace's
  .env (credentials never go on a command line). `link` never writes the URL
  (a one-time token) to stdout, a file or a log: only its host.
- --index counts from 0, among the matches of the same kind (exact or contains).
- adb and Maestro get argument lists, never a local shell; the text of `input
  text` and the URL of `am start` are quoted for the device's shell.
- Typing (plan 11.2, lessons of a real run): --file reads the text from a UTF-8
  file (on Windows an emoji passed as an argument reached the device as ????);
  `adb shell input text` goes in chunks of 300 characters (one call was cut at
  ~430); Maestro gets the text inside a UTF-8 flow file (never on its command
  line), one inputText step per 300 characters (5 000 in one step took minutes
  and stopped at ~900). "Maestro IME is not active": the field is tapped again
  (with --field) and Maestro retried once; if it fails again, ASCII text falls
  back to chunked adb (said on stderr), other text is exit 3. --via adb|maestro
  forces a route for ASCII (e.g. a field that ignores `input text`).
- Input through the companion driver (plan 11.7, technique-mobile.md §1.2):
  with `input: driver`, tap, swipe, back and the typing go through
  `adb shell content call --uri content://io.qaassistant.driver` (comma-
  separated arguments; the text as base64 of its UTF-8, set on the focused
  field in one call: any Unicode, no chunks, no Maestro), for a device that
  refuses `adb shell input` (INJECT_EVENTS). `check` pings it. Reading the
  screen is the same for both inputs. `clear` sets the empty text with the
  driver; with adb it moves to the end and deletes (as many characters as the
  field shows with --field, else CLEAR_KEYS).

Exit codes:
  0  ok
  2  invalid usage or configuration (e.g. no mobile.app_id, several devices
     without --serial, a .env value in the text, Maestro missing)
  3  no target (no adb, no device, app not installed), app not in the
     foreground, screen not readable, `send` timed out, or the driver not
     installed / its service off
  4  element not found (tap, --field, the send control; with the driver, no
     focused field to type into)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from common import (
    MOBILE_INPUTS,
    OutsideWorkspace,
    find_adb,
    mobile_target,
    observed_build,
    read_project,
    read_workspace_env,
    same_build,
    workspace_path,
)

DRIVER_SERVICE = "io.qaassistant.driver/"
PERMISSION_DIALOGS = ("com.google.android.permissioncontroller", "com.android.permissioncontroller")
SEND_LABELS = ("Send", "Send message", "Submit")
DUMP_PATH = "/sdcard/qa-device-dump.xml"
POLL_S, SETTLE_MIN_S = 5, 15
BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
FOCUS = re.compile(r"mCurrentFocus=Window\{([^}]*)\}")
# characters the device's sh would interpret in `input text` (a space goes as %s)
SHELL_SPECIAL = set("\\'\"()<>|;&*~$`!?[]{}#")
# control characters XML 1.0 forbids, raw or as a reference; uiautomator sometimes writes them and ElementTree
# refuses the whole dump
XML_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
XML_REF = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")
MIN_SECRET_LEN = 4  # a .env value shorter than this is only refused when it is the whole text
CLEAR_KEYS = 250    # deletions of `clear` through adb when the field's text is not known
CHUNK = 300         # characters per `adb shell input text` call and per Maestro inputText step
IME_NOT_ACTIVE = "IME is not active"
INPUTS = MOBILE_INPUTS
DRIVER_URI = "content://io.qaassistant.driver"
DRIVER_HELP = "build, install and turn it on: technique-mobile.md §1.2 (python scripts/build_driver.py --push)"
BUNDLE = re.compile(r"Bundle\[\{(.*)\}\]", re.DOTALL)


class UsageError(Exception):
    """Invalid usage or configuration (code 2)."""


class NoTarget(Exception):
    """No device, app not installed or not in the foreground, screen not readable, timeout (code 3)."""


class NotFound(Exception):
    """Element not found on the screen (code 4)."""


@dataclass(frozen=True)
class Node:
    x1: int
    y1: int
    x2: int
    y2: int
    text: str

    @property
    def centre(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2

    def line(self) -> str:
        return f"[{self.x1},{self.y1}][{self.x2},{self.y2}] {self.text}"


# --- the one door to the device (the tests replace it) ---------------------------------------------------------

def adb(serial: str | None, *args: str, binary: bool = False, timeout: int = 60):
    """Runs adb (with -s serial) and returns its stdout (bytes if binary). The arguments never reach an error
    message: they can carry a one-time URL (`link`)."""
    exe = find_adb()
    if not exe:
        raise NoTarget("adb not found (install the Android SDK platform-tools, set ANDROID_HOME or put them on the "
                       "PATH)")
    cmd = [exe, *(["-s", serial] if serial else []), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise NoTarget(f"adb did not answer in {timeout} s") from None
    if r.returncode != 0 and not r.stdout:
        err = r.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise NoTarget(f"adb failed: {hide_urls(err[0]) if err else f'exit {r.returncode}'}")
    return r.stdout if binary else r.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")


def run_maestro(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, **(env or {})}, timeout=600, check=False)


def hide_urls(s: str) -> str:
    return re.sub(r"\S+://\S*", "«url»", s)


# --- configuration and target -----------------------------------------------------------------------------------

def configuration(workspace: str, serial: str | None, input_: str | None = None) -> dict:
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    m = proj.get("mobile") or {}
    if not m.get("app_id"):
        raise UsageError("mobile.app_id missing in project.yaml (the app under test, e.g. com.demoshop.app)")

    try:
        serial, input_ = mobile_target(m, serial, input_)
    except ValueError as e:
        raise UsageError(str(e)) from None

    def as_list(v):
        return [str(x) for x in ([v] if isinstance(v, str) else (v or []))]
    return {"workspace": workspace, "app_id": str(m["app_id"]), "serial": serial,
            "input": input_,
            "send_labels": as_list(m.get("send_label")) or list(SEND_LABELS),
            "ignore": {x.casefold() for x in as_list(m.get("ignore_labels"))},
            "batch": str(m.get("evidence_batch") or datetime.now().strftime("%Y%m%d")),
            "browser": m.get("browser"),
            "environments": sorted(str(k) for k in (proj.get("environments") or {})
                                   if isinstance(proj.get("environments"), dict)),
            "intended_build": str((m.get("build") or {}).get("version") or "") if isinstance(m.get("build"), dict) else "",
            "signed_in": as_list((m.get("session") or {}).get("signed_in")) if isinstance(m.get("session"), dict) else [],
            "signed_out": as_list((m.get("session") or {}).get("signed_out")) if isinstance(m.get("session"), dict) else []}


def resolve_serial(wanted: str | None) -> str:
    """The serial to use: the one asked for, if connected; otherwise the only device connected."""
    rows = [ln.split() for ln in adb(None, "devices").splitlines()[1:] if ln.strip()]
    ready = [r[0] for r in rows if len(r) >= 2 and r[1] == "device"]
    if wanted:
        if wanted in ready:
            return wanted
        state = next((r[1] for r in rows if r[0] == wanted and len(r) >= 2), "not connected")
        raise NoTarget(f"device {wanted}: {state}")
    if not ready:
        raise NoTarget("no Android device/emulator connected (adb devices)")
    if len(ready) > 1:
        raise UsageError(f"several devices connected ({', '.join(ready)}): choose one with --serial, "
                         "QA_MOBILE_SERIAL or mobile.serial")
    return ready[0]


def foreground(serial: str) -> str:
    """The focused window, e.g. com.demoshop.app/com.demoshop.app.MainActivity ('' if none)."""
    m = FOCUS.search(adb(serial, "shell", "dumpsys", "window"))
    return m.group(1).split()[-1] if m and m.group(1).split() else ""


def guard(cfg: dict, serial: str, action: str, allow_dialogs: bool = False) -> None:
    """Refuses (code 3) unless the app under test (or, when allowed, a permission dialog) is in the foreground."""
    window = foreground(serial)
    pkg = window.split("/")[0]
    if pkg == cfg["app_id"] or (allow_dialogs and pkg in PERMISSION_DIALOGS):
        return
    raise NoTarget(f"{action} refused: the foreground is {window or 'nothing'!r}, not {cfg['app_id']}"
                   + (" (a permission dialog is never captured)" if pkg in PERMISSION_DIALOGS else ""))


# --- reading the screen -----------------------------------------------------------------------------------------

def parse_nodes(xml_text: str) -> list[Node]:
    """Nodes with a text (or, without one, a content-desc) and a size; entities decoded, line breaks as ' / '."""
    def legal(m):
        code = int(m.group(1)[1:], 16) if m.group(1)[0] in "xX" else int(m.group(1))
        return m.group(0) if code >= 0x20 or code in (0x9, 0xA, 0xD) else ""
    root = ET.fromstring(XML_REF.sub(legal, XML_CONTROL.sub("", xml_text)))
    nodes = []
    for n in root.iter("node"):
        label = (n.get("text") or "").strip() or (n.get("content-desc") or "").strip()
        b = BOUNDS.fullmatch(n.get("bounds") or "")
        if not label or not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        if x2 <= x1 or y2 <= y1:   # [0,0][0,0] and other nodes off the screen
            continue
        label = " / ".join(p.strip() for p in label.replace("\r", "").split("\n") if p.strip())
        nodes.append(Node(x1, y1, x2, y2, label))
    return nodes


def dump_xml(serial: str, tries: int = 3) -> str:
    for attempt in range(tries):
        try:
            adb(serial, "shell", "uiautomator", "dump", DUMP_PATH)
            out = adb(serial, "exec-out", "cat", DUMP_PATH)
        except NoTarget:
            out = ""
        if "<hierarchy" in out:
            try:
                adb(serial, "shell", "rm", "-f", DUMP_PATH)   # the screen's text does not stay on the device
            except NoTarget:
                pass
            return out[out.find("<?xml") if "<?xml" in out else out.find("<hierarchy"):]
        if attempt < tries - 1:
            time.sleep(1)
    raise NoTarget(f"uiautomator dump returned no hierarchy after {tries} tries (screen animating or locked?)")


def read_screen(cfg: dict, serial: str, allow_dialogs: bool = False) -> list[Node]:
    guard(cfg, serial, "dump", allow_dialogs)
    return parse_nodes(dump_xml(serial))


def find(nodes: list[Node], text: str, index: int = 0) -> Node:
    """Exact text first (case-insensitive), then contains; index counts among the matches of that kind."""
    want = text.casefold()
    matches = ([n for n in nodes if n.text.casefold() == want]
               or [n for n in nodes if want in n.text.casefold()])
    if index >= len(matches):
        found = f" ({len(matches)} match(es), --index {index})" if matches else ""
        raise NotFound(f"{text!r} not on the screen{found}")
    return matches[index]


def wait_stable(read, timeout: float, interval: float = POLL_S, minimum: float = SETTLE_MIN_S,
                clock=None, sleep=None):
    """Reads every `interval` s until two consecutive readings are identical after at least `minimum` s.
    Returns (last reading, stable?); not stable = timeout."""
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    start = clock()
    previous = read()
    while clock() - start < timeout:
        sleep(interval)
        current = read()
        if current == previous and clock() - start >= minimum:
            return current, True
        previous = current
    return previous, False


def after_message(lines: list[str], message: str, ignore: set[str]) -> tuple[list[str], bool]:
    """The lines after the last one containing the message (its first 40 characters), without the chrome.
    (lines, message found?); not found = every line."""
    key = " / ".join(p.strip() for p in message.split("\n") if p.strip())[:40].casefold()
    texts = [ln.split("] ", 1)[1] if "] " in ln else ln for ln in lines]
    last = max((i for i, t in enumerate(texts) if key and key in t.casefold()), default=None)
    chosen = range(len(lines)) if last is None else range(last + 1, len(lines))
    return [lines[i] for i in chosen if texts[i].casefold() not in ignore], last is not None


# --- acting -----------------------------------------------------------------------------------------------------

def driver(serial: str, method: str, arg: str | None = None) -> dict:
    """One call to the companion driver; returns its answer ({'ok': True, 'version': '1'}). Not installed, the
    service off or a refused action is NoTarget; nothing focused to type into is NotFound."""
    try:
        out = adb(serial, "shell", "content", "call", "--uri", DRIVER_URI, "--method", method,
                  *(["--arg", arg] if arg is not None else []))
    except NoTarget as e:
        raise NoTarget(f"the driver did not answer ({e}): {DRIVER_HELP}") from None
    m = BUNDLE.search(out)
    if not m:
        first = next((ln.strip() for ln in out.splitlines() if ln.strip()), "no answer")
        raise NoTarget(f"the driver did not answer ({first[:160]}): {DRIVER_HELP}")
    body = m.group(1)
    fields = dict(re.findall(r"(?:^|, )(ok|version)=([^,]*)", body))
    error = re.search(r"(?:^|, )error=(.*?)(?=, (?:ok|version)=|$)", body)
    if fields.get("ok") == "true":
        return {"ok": True, "version": fields.get("version", "?")}
    reason = error.group(1) if error else "refused"
    if reason == "no focused field":
        raise NotFound("no focused field to type into (tap the field first, or use --field)")
    if reason == "service off":
        raise NoTarget(f"the driver's accessibility service is off: {DRIVER_HELP}")
    raise NoTarget(f"driver {method}: {reason}")


def tap_xy(cfg: dict, serial: str, x: int, y: int) -> None:
    if cfg.get("input") == "driver":
        driver(serial, "tap", f"{x},{y}")
    else:
        adb(serial, "shell", "input", "tap", str(x), str(y))


def swipe_xy(cfg: dict, serial: str, x1: int, y1: int, x2: int, y2: int, ms: int) -> None:
    if cfg.get("input") == "driver":
        driver(serial, "swipe", f"{x1},{y1},{x2},{y2},{ms}")
    else:
        adb(serial, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))


def press_back(cfg: dict, serial: str) -> None:
    if cfg.get("input") == "driver":
        driver(serial, "back")
    else:
        adb(serial, "shell", "input", "keyevent", "4")


def clear_field(cfg: dict, serial: str, field: str | None) -> str:
    """Empties the focused field (after tapping the field, if given); returns the route used."""
    known = None
    if field:
        node = find(read_screen(cfg, serial), field)
        tap_xy(cfg, serial, *node.centre)
        known = len(node.text)
    else:
        guard(cfg, serial, "clear")
    if cfg.get("input") == "driver":
        driver(serial, "text")                    # no argument: the empty text
        return "driver set empty text"
    n = known if known is not None else CLEAR_KEYS
    adb(serial, "shell", "input", "keyevent", "123")                  # KEYCODE_MOVE_END
    adb(serial, "shell", "input", "keyevent", *(["67"] * n))          # KEYCODE_DEL
    return f"adb {n} deletions"


def type_driver(serial: str, text: str) -> None:
    """The whole text set on the focused field (ACTION_SET_TEXT): base64 of the UTF-8, so no quoting and any
    Unicode."""
    driver(serial, "text", base64.b64encode(text.encode("utf-8")).decode("ascii"))


def input_text_arg(text: str) -> str:
    """`input text` argument, escaped for the device's sh: spaces as %s, special characters with a backslash."""
    return "".join("%s" if ch == " " else "\\" + ch if ch in SHELL_SPECIAL else ch for ch in text)


def refuse_secrets(workspace: str, text: str) -> None:
    for name, value in read_workspace_env(workspace).items():
        if value and (text == value or (len(value) >= MIN_SECRET_LEN and value in text)):
            raise UsageError(f"the text contains the value of {name} from .env: credentials never go on a command "
                             "line (use a Maestro flow with ${MAESTRO_<VAR>}, technique-mobile.md §4)")


def chunks(text: str, size: int = CHUNK) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def type_adb(serial: str, text: str) -> int:
    """`adb shell input text` in chunks (one long call was cut at ~430 characters); returns the number of calls."""
    parts = chunks(text)
    for part in parts:
        adb(serial, "shell", "input", "text", input_text_arg(part))
    return len(parts)


def maestro_flow(app_id: str, text: str) -> tuple[str, dict]:
    """(flow, extra environment): one inputText step per chunk, the text written in the UTF-8 flow as a quoted
    scalar (an argument on Windows turned emoji into ????). A chunk with `${` (Maestro would evaluate it) goes
    through an environment variable MAESTRO_QA_TEXT_<n> instead; never through the command line."""
    steps, env = [], {}
    for i, part in enumerate(chunks(text), 1):
        if "${" in part:
            env[f"MAESTRO_QA_TEXT_{i}"] = part
            steps.append(f"- inputText: ${{MAESTRO_QA_TEXT_{i}}}\n")
        else:
            steps.append(f"- inputText: {json.dumps(part, ensure_ascii=False)}\n")
    return f"appId: {json.dumps(app_id)}\n---\n" + "".join(steps), env


def type_maestro(cfg: dict, serial: str, text: str) -> subprocess.CompletedProcess:
    maestro = shutil.which("maestro")
    if not maestro:
        raise UsageError("maestro not found: text outside ASCII is typed through Maestro's inputText "
                         "(adb input text refuses it) -- install the Maestro CLI")
    flow_text, env = maestro_flow(cfg["app_id"], text)
    with tempfile.TemporaryDirectory() as tmp:
        flow = os.path.join(tmp, "type.yaml")
        with open(flow, "w", encoding="utf-8") as f:
            f.write(flow_text)
        return run_maestro([maestro, "--device", serial, "test", flow], env)


def type_text(cfg: dict, serial: str, text: str, field: str | None, via: str = "auto") -> str:
    """Types the text (after tapping the field, if given); returns the route used."""
    refuse_secrets(cfg["workspace"], text)
    if field:
        tap_xy(cfg, serial, *find(read_screen(cfg, serial), field).centre)
    else:
        guard(cfg, serial, "type")
    if cfg.get("input") == "driver":
        if via != "auto":
            raise UsageError(f"--via {via}: with mobile.input driver the text is set by the driver")
        type_driver(serial, text)
        return "driver set text"
    ascii_only = all(" " <= ch <= "~" for ch in text)
    if via == "adb" and not ascii_only:
        raise UsageError("--via adb: `adb shell input text` types only ASCII; this text needs Maestro")
    if ascii_only and via != "maestro":
        n = type_adb(serial, text)
        return "adb input text" + (f", {n} chunks" if n > 1 else "")
    r = type_maestro(cfg, serial, text)
    if r.returncode != 0 and IME_NOT_ACTIVE in (r.stdout + r.stderr):
        # intermittent: Maestro's keyboard lost the focus; the field again, then one retry
        if field:
            tap_xy(cfg, serial, *find(read_screen(cfg, serial), field).centre)
        r = type_maestro(cfg, serial, text)
        if r.returncode != 0 and IME_NOT_ACTIVE in (r.stdout + r.stderr) and ascii_only:
            print("NOTE: Maestro's keyboard is not active (twice): the ASCII text was typed through adb instead",
                  file=sys.stderr)
            n = type_adb(serial, text)
            return "adb input text (fallback: Maestro IME not active)" + (f", {n} chunks" if n > 1 else "")
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        raise NoTarget("Maestro could not type the text: " + " | ".join(tail))
    steps = len(chunks(text))
    return "maestro inputText" + (f", {steps} steps" if steps > 1 else "")


def text_of(a) -> str:
    """The text of `type`/`send`: the argument, or the UTF-8 file of --file (one final line break dropped)."""
    if a.file and a.text is not None:
        raise UsageError("give the text or --file, not both")
    if a.file:
        try:
            with open(a.file, encoding="utf-8-sig") as f:
                text = f.read()
        except OSError as e:
            raise UsageError(f"--file {a.file}: {e.strerror}") from None
        except UnicodeDecodeError:
            raise UsageError(f"--file {a.file}: not UTF-8 text") from None
        text = text.removesuffix("\n")
        text = text.removesuffix("\r")
    elif a.text is not None:
        text = a.text
    else:
        raise UsageError("a text or --file is needed")
    if not text:
        raise UsageError("the text is empty")
    return text


def shot(cfg: dict, serial: str, name: str, batch: str | None) -> str:
    if not name or name != os.path.basename(name) or name in (".", ".."):
        raise UsageError(f"invalid capture name {name!r}: a file name, without folders")
    guard(cfg, serial, "screenshot")
    png = adb(serial, "exec-out", "screencap", "-p", binary=True)
    if not png.startswith(b"\x89PNG"):
        raise NoTarget("screencap returned no PNG")
    try:
        folder = workspace_path(cfg["workspace"], "evidence", batch or cfg["batch"])
    except OutsideWorkspace as e:
        raise UsageError(str(e)) from None
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name if name.lower().endswith(".png") else name + ".png")
    with open(path, "wb") as f:
        f.write(png)
    return path


def unwrap(url: str) -> str:
    """The target of a https://www.google.com/url?q=<target> wrapper (e-mail clients add it); others unchanged."""
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if (host == "google.com" or host.endswith(".google.com")) and p.path == "/url":
        target = parse_qs(p.query).get("q") or parse_qs(p.query).get("url")
        if target:
            return target[0]
    return url


def sh_quote(s: str) -> str:
    """Single quotes for the device's sh (adb shell joins its arguments into one command line)."""
    return "'" + s.replace("'", "'\\''") + "'"


# --- subcommands ------------------------------------------------------------------------------------------------

def driver_report(serial: str) -> dict:
    """The driver as two facts: the setting lists the service, and the service answers a ping. Reads only."""
    try:
        enabled = DRIVER_SERVICE in adb(serial, "shell", "settings", "get", "secure", "enabled_accessibility_services")
    except NoTarget:
        enabled = False
    try:
        return {"enabled": enabled, "answering": True, "version": driver(serial, "ping")["version"], "why": "", "state": "on"}
    except NoTarget as e:
        why = str(e)
        state = "off" if "service is off" in why else "missing" if "did not answer" in why else "refused"
        return {"enabled": enabled, "answering": False, "version": None, "why": why, "state": state}


def driver_advice(d: dict) -> str:
    if d["state"] == "off" and d["enabled"]:
        return ("the setting lists the QA driver but the service is not running (Android can leave a service listed "
                "without running it): in Settings, Accessibility, turn the QA driver off and on again; nothing was changed")
    if d["state"] == "off":
        return "the QA driver is installed but its service is off: turn it on in Settings, Accessibility (technique-mobile.md §1.2)"
    if d["state"] == "missing":
        return f"the driver is not installed or its provider does not answer: {DRIVER_HELP}"
    return f"the driver refused the ping ({d['why']}): technique-mobile.md §1.2"


def session_state(cfg: dict, serial: str, foreground_pkg: str) -> tuple[str, str]:
    """(confirmed | needed | unknown, why). Only from the screen text and mobile.session: never guessed."""
    if not cfg["signed_in"] and not cfg["signed_out"]:
        return "unknown", "mobile.session is not set in project.yaml (signed_in / signed_out: texts of the screen)"
    if foreground_pkg != cfg["app_id"]:
        return "unknown", "the app is not in the foreground: python scripts/device.py <workspace> launch"
    try:
        texts = [n.text.casefold() for n in read_screen(cfg, serial)]
    except NoTarget as e:
        return "unknown", f"the screen could not be read ({e})"

    def seen(words):
        return any(w.casefold() in t for w in words for t in texts)

    inn, out = seen(cfg["signed_in"]), seen(cfg["signed_out"])
    if inn and out:
        return "unknown", "both a signed-in and a signed-out text are on the screen"
    if inn:
        return "confirmed", ""
    if out:
        return "needed", "the person logs in (a magic link is theirs to ask for and hand over, technique-mobile.md §1.2)"
    return "unknown", "none of the configured texts is on the screen"


def cmd_check(cfg, serial, a, no_target: str = "") -> int:
    """One report, nothing changed on the device (technique-mobile.md 1.3). 0 ready, 3 not ready."""
    out, blockers, advice = [], [], []
    envs = f"environment: {', '.join(cfg['environments']) or 'none declared'}, app {cfg['app_id']}"
    if serial is None:
        out += [f"device: not connected ({no_target})", envs, "session: unknown (no device)"]
        blockers.append(no_target)
        advice.append("connect the device (USB debugging on) or start the emulator, then repeat")
    else:
        release = adb(serial, "shell", "getprop", "ro.build.version.release").strip()
        sdk = adb(serial, "shell", "getprop", "ro.build.version.sdk").strip()
        model = adb(serial, "shell", "getprop", "ro.product.model").strip()
        out.append(f"target: {serial} · Android {release} (SDK {sdk}) · model: {model}")
        out.append(f"device: {serial} connected")
        window = foreground(serial)
        out.append(f"foreground: {window or 'nothing'}")
        pkg = adb(serial, "shell", "dumpsys", "package", cfg["app_id"])
        installed = f"Package [{cfg['app_id']}]" in pkg
        name = re.search(r"versionName=(\S+)", pkg) if installed else None
        code = re.search(r"versionCode=(\d+)", pkg)
        if not name:
            out.append(f"app: {cfg['app_id']} not installed")
            blockers.append(f"app {cfg['app_id']} is not installed on {serial}")
            advice.append("install the test build (technique-mobile.md §1.1; mobile.build.source says where it comes from)")
        else:
            out.append(f"app: {cfg['app_id']} {name.group(1)}" + (f" (code {code.group(1)})" if code else ""))
        if cfg["input"] == "driver":
            d = driver_report(serial)
            if d["answering"]:
                out.append(f"input: driver {d['version']}, service on")
                out.append(f"driver: setting on, answering (version {d['version']})")
            else:
                out.append("input: driver, not answering")
                out.append(f"driver: setting {'on' if d['enabled'] else 'off'}, NOT answering ({d['why']})")
                blockers.append(d["why"])
                advice.append(driver_advice(d))
        else:
            out += ["input: adb", "driver: not used (input: adb)"]
        if name:
            observed = observed_build(cfg["app_id"], name.group(1), code.group(1) if code else "?")
            if cfg["intended_build"]:
                verdict = "same" if same_build(observed, cfg["intended_build"]) else "DIFFERS"
                line = f"build: observed {observed}, intended {cfg['intended_build']}: {verdict}"
                if verdict == "DIFFERS":
                    advice.append("the observed build is not the intended one (mobile.build.version): confirm it is the one "
                                  "to test; the intended build is never rewritten from the device")
            else:
                line = f"build: observed {observed}, intended not set"
            out.append(line)
            if a.batch:
                observed_in_batch = _batch_build(cfg["workspace"], a.batch)
                if observed_in_batch is None:
                    out.append(f"batch: {a.batch}: no run log yet, the first result will record this build")
                elif not same_build(observed, observed_in_batch):
                    blockers.append(f"the build changed during the batch: {a.batch} started on {observed_in_batch!r} and the "
                                    f"device now has {observed!r}")
                    advice.append("stop this batch (its results stay as they are) and plan a new batch with the right "
                                  "context (/plan-batch)")
                else:
                    out.append(f"batch: {a.batch}: same build as its run log")
        out.append(envs)
        state, why = session_state(cfg, serial, window.split("/")[0])
        out.append(f"session: {state}" + (f" ({why})" if why else ""))
        if state == "needed":
            blockers.append("the app has no session")
            advice.append(why)
        elif state == "unknown" and "foreground" in why:
            advice.append(why)
    out.append("ready: yes" if not blockers else "ready: NOT READY: " + "; ".join(blockers))
    out += [f"next: {x}" for x in advice]
    print("\n".join(out))
    if blockers:
        print(f"NO TARGET: {blockers[0]}", file=sys.stderr)
        return 3
    return 0


def _batch_build(workspace: str, batch: str) -> str | None:
    """The build a batch's run log recorded, or None when it has no run log or no build yet."""
    import run_log
    try:
        path = run_log.run_log_path(workspace, batch)
        doc = run_log.read_log(path) if os.path.isfile(path) else None
    except run_log.UsageError as e:
        raise UsageError(str(e)) from None
    build = ((doc or {}).get("run") or {}).get("build") if doc else None
    return str(build) if build else None


def cmd_dump(cfg, serial, a) -> int:
    for n in read_screen(cfg, serial, allow_dialogs=True):
        print(n.line())
    if a.capture:
        print(f"capture: {shot(cfg, serial, a.capture, a.batch)}")
    return 0


def cmd_tap(cfg, serial, a) -> int:
    if a.xy:
        guard(cfg, serial, "tap", allow_dialogs=True)
        x, y = a.xy
        label = f"({x},{y})"
    elif a.text:
        node = find(read_screen(cfg, serial, allow_dialogs=True), a.text, a.index)
        (x, y), label = node.centre, node.line()
    else:
        raise UsageError("tap needs a TEXT or --xy X Y")
    tap_xy(cfg, serial, x, y)
    print(f"tapped: {label}")
    return 0


def cmd_type(cfg, serial, a) -> int:
    print(f"typed ({type_text(cfg, serial, text_of(a), a.field, a.via)})")
    return 0


def failure_kind(error: Exception) -> str:
    """A failed reading is not a failed app: the dump cannot be read while the screen animates (capture-failed); an app
    that left the foreground is another thing (app-not-foreground)."""
    return "app-not-foreground" if "the foreground is" in str(error) else "capture-failed"


class Sampler:
    """Timed readings of a streamed answer (technique-mobile.md 4.1), limited in number and in time."""

    def __init__(self, text: str, ignore: set[str], samples: int, seconds: float, interval: float):
        self.text, self.ignore = text, ignore
        self.limits = {"samples": samples, "seconds": seconds, "interval_s": interval}
        self.samples: list[dict] = []
        self.started = time.monotonic()
        self.last_good: list[str] = []

    def _room(self) -> bool:
        return len(self.samples) < self.limits["samples"] and time.monotonic() - self.started <= self.limits["seconds"]

    def wrap(self, read):
        def sampled():
            try:
                lines = read()
            except NoTarget as e:
                if self._room():
                    self.samples.append({"at": datetime.now().isoformat(timespec="milliseconds"),
                                         "elapsed_s": round(time.monotonic() - self.started, 2),
                                         "kind": failure_kind(e), "reason": str(e)})
                return object()                 # never equal to a reading: a failed one cannot end the wait
            self.last_good = lines
            if self._room():
                new, _ = after_message(lines, self.text, self.ignore)
                answer = "\n".join(ln.split("] ", 1)[1] if "] " in ln else ln for ln in new)
                self.samples.append({"at": datetime.now().isoformat(timespec="milliseconds"),
                                     "elapsed_s": round(time.monotonic() - self.started, 2), "kind": "read",
                                     "answer": answer})
            return lines
        return sampled

    def summary(self) -> dict:
        reads = [s["answer"] for s in self.samples if s["kind"] == "read"]
        partial = any(a and b != a and b.startswith(a) for i, a in enumerate(reads) for b in reads[i + 1:])
        return {"reads": len(reads), "failed_captures": sum(s["kind"] == "capture-failed" for s in self.samples),
                "app_not_in_foreground": sum(s["kind"] == "app-not-foreground" for s in self.samples),
                "partial_text_observed": partial}

    def write(self, cfg: dict, name: str, batch: str | None) -> str:
        try:
            folder = workspace_path(cfg["workspace"], "evidence", batch or cfg["batch"])
        except OutsideWorkspace as e:
            raise UsageError(str(e)) from None
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name + "_samples.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"limits": self.limits, "summary": self.summary(), "samples": self.samples}, f,
                      ensure_ascii=False, indent=1)
            f.write("\n")
        return path


def cmd_send(cfg, serial, a) -> int:
    a.text = text_of(a)
    guard(cfg, serial, "send")
    type_text(cfg, serial, a.text, a.field)
    labels = [a.send_label] if a.send_label else cfg["send_labels"]
    nodes = read_screen(cfg, serial)
    control = next((n for lab in labels for n in nodes if n.text.casefold() == lab.casefold()), None)
    if control is None:   # contains, never the field that still shows the message
        key = a.text[:40].casefold()
        control = next((n for lab in labels for n in nodes
                        if lab.casefold() in n.text.casefold() and key not in n.text.casefold()), None)
    if control is None:
        raise NotFound(f"send control not found (tried {labels}; set mobile.send_label or --send-label)")
    tap_xy(cfg, serial, *control.centre)

    def read():
        return [n.line() for n in read_screen(cfg, serial)]
    sampler = Sampler(a.text, cfg["ignore"], a.samples, a.sample_seconds, a.sample_interval) if a.samples else None
    if sampler:
        lines, stable = wait_stable(sampler.wrap(read), a.timeout, interval=a.sample_interval)
        if not isinstance(lines, list):
            lines = sampler.last_good
    else:
        lines, stable = wait_stable(read, a.timeout)
    new, found = after_message(lines, a.text, cfg["ignore"])
    if not found:
        print("NOTE: the sent message is not on the screen (scrolled away?): every line follows", file=sys.stderr)
    for ln in new:
        print(ln)
    if a.capture:
        print(f"capture: {shot(cfg, serial, a.capture, a.batch)}")
    if sampler:
        s = sampler.summary()
        path = sampler.write(cfg, a.capture or "send", a.batch)
        print(f"samples: {s['reads']} read, {s['failed_captures']} capture-failed, {s['app_not_in_foreground']} "
              f"app-not-foreground; partial text observed: {'yes' if s['partial_text_observed'] else 'no'} ({path})")
    if not stable:
        raise NoTarget(f"the screen did not settle in {a.timeout:.0f} s (answer still streaming?): "
                       "the lines above are the last reading")
    return 0


def cmd_swipe(cfg, serial, a) -> int:
    if a.ms < 1:
        raise UsageError("--ms must be a positive number of milliseconds")
    guard(cfg, serial, "swipe", allow_dialogs=True)
    swipe_xy(cfg, serial, a.x1, a.y1, a.x2, a.y2, a.ms)
    print(f"swiped: ({a.x1},{a.y1}) -> ({a.x2},{a.y2}) in {a.ms} ms")
    return 0


def cmd_clear(cfg, serial, a) -> int:
    print(f"cleared ({clear_field(cfg, serial, a.field)})")
    return 0


def cmd_back(cfg, serial, a) -> int:
    guard(cfg, serial, "back", allow_dialogs=True)
    press_back(cfg, serial)
    print("back")
    return 0


def cmd_launch(cfg, serial, a) -> int:
    """Opens mobile.app_id (or brings it back) and waits until it is in the foreground and its screen is stable;
    prints that screen like `dump`. --restart stops it first. The agent on a runner has no other way to open the
    app (11.8 pilot: it tried `adb shell monkey` itself, outside the tools it may use)."""
    app = cfg["app_id"]
    if a.restart:
        adb(serial, "shell", "am", "force-stop", app)
    if a.restart or not foreground(serial).startswith(app + "/"):
        adb(serial, "shell", "monkey", "-p", app, "-c", "android.intent.category.LAUNCHER", "1")
    start = time.monotonic()
    while not foreground(serial).startswith(app + "/"):
        if time.monotonic() - start > a.timeout:
            raise NoTarget(f"{app} did not come to the foreground in {a.timeout:.0f} s "
                           f"(foreground: {foreground(serial) or 'nothing'})")
        time.sleep(1)
    lines, stable = wait_stable(lambda: [n.line() for n in read_screen(cfg, serial, allow_dialogs=True)],
                                max(a.timeout - (time.monotonic() - start), SETTLE_MIN_S + POLL_S))
    print(f"launched: {app}" + ("" if stable else " (the screen was still changing)"))
    for line in lines:
        print(line)
    return 0


def cmd_shot(cfg, serial, a) -> int:
    print(f"capture: {shot(cfg, serial, a.name, a.batch)}")
    return 0


def open_link(cfg: dict, serial: str, url: str, package: str | None = None) -> str:
    """Opens the URL on the device (am start); returns its host. Never writes the URL anywhere (scripts/
    remote_login.py calls this in-process, so the URL is not on any command line of this computer either)."""
    url = unwrap(url.strip())
    host = urlsplit(url).hostname or urlsplit(url).scheme
    if not host:
        raise UsageError("link needs a URL (scheme://...)")
    package = package or cfg["browser"]
    out = adb(serial, "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", sh_quote(url),
              *([str(package)] if package else []))
    error = next((ln for ln in out.splitlines() if ln.startswith("Error")), None)
    if error:
        raise NoTarget(f"{host}: not opened ({hide_urls(error)})")
    return host


def cmd_link(cfg, serial, a) -> int:
    print(f"{open_link(cfg, serial, a.url, a.package)}: opened")   # never the URL: it is a one-time credential
    return 0


COMMANDS = {"check": cmd_check, "dump": cmd_dump, "tap": cmd_tap, "type": cmd_type, "send": cmd_send,
            "shot": cmd_shot, "link": cmd_link, "swipe": cmd_swipe, "back": cmd_back,
            "clear": cmd_clear, "launch": cmd_launch}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Drives an Android device/emulator through adb "
                                             "(technique-mobile.md §4.1).")
    ap.add_argument("workspace")
    ap.add_argument("--serial",
                    help="device serial (default: $QA_MOBILE_SERIAL, mobile.serial, or the only device connected)")
    ap.add_argument("--input", choices=INPUTS,
                    help="adb, or the companion driver (default: $QA_MOBILE_INPUT, mobile.input, else adb)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--serial", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--input", choices=INPUTS, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", parents=[common], help="one report: device, app, driver, build, environment, session")
    p.add_argument("--batch", help="also compare the observed build with the run log of this batch")
    p = sub.add_parser("dump", parents=[common], help="visible text with bounds, one line per node")
    p.add_argument("--capture", metavar="NAME", help="also save a screenshot")
    p.add_argument("--batch", help="evidence folder (default: mobile.evidence_batch or today)")
    p = sub.add_parser("tap", parents=[common], help="tap by text (exact, then contains) or by coordinates")
    p.add_argument("text", nargs="?")
    p.add_argument("--index", type=int, default=0, help="which match, from 0")
    p.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))
    p = sub.add_parser("type", parents=[common], help="type a text (Unicode through Maestro)")
    p.add_argument("text", nargs="?")
    p.add_argument("--file", metavar="UTF8_FILE", help="the text from a UTF-8 file (emoji, long texts)")
    p.add_argument("--field", metavar="LABEL", help="tap this field first")
    p.add_argument("--via", choices=("auto", "adb", "maestro"), default="auto",
                   help="route for ASCII text (default auto: adb; maestro for a field that ignores `input text`)")
    p = sub.add_parser("send", parents=[common], help="type, send, wait for the screen to settle, print the new")
    p.add_argument("text", nargs="?")
    p.add_argument("--file", metavar="UTF8_FILE", help="the text from a UTF-8 file (emoji, long texts)")
    p.add_argument("--capture", metavar="NAME")
    p.add_argument("--batch")
    p.add_argument("--send-label", help="label of the send control (default: mobile.send_label)")
    p.add_argument("--field", metavar="LABEL")
    p.add_argument("--timeout", type=float, default=200, help="seconds to wait for the answer (default 200)")
    p.add_argument("--samples", type=int, default=0, metavar="N",
                   help="keep up to N timed readings of the answer in evidence/<batch>/<capture>_samples.json (default off)")
    p.add_argument("--sample-seconds", type=float, default=60, help="stop sampling after this many seconds (default 60)")
    p.add_argument("--sample-interval", type=float, default=3, help="seconds between readings while sampling (default 3)")
    p = sub.add_parser("shot", parents=[common], help="screenshot to evidence/<batch>/<NAME>.png")
    p.add_argument("name")
    p.add_argument("--batch")
    p = sub.add_parser("link", parents=[common], help="open a deep link / login callback (never printed)")
    p.add_argument("url")
    p.add_argument("--package", help="app that opens it (default: mobile.browser, else the system's choice)")
    p = sub.add_parser("swipe", parents=[common], help="a swipe from (X1,Y1) to (X2,Y2)")
    for name in ("x1", "y1", "x2", "y2"):
        p.add_argument(name, type=int)
    p.add_argument("--ms", type=int, default=300, help="duration in milliseconds (default 300)")
    sub.add_parser("back", parents=[common], help="the system's back")
    p = sub.add_parser("launch", parents=[common], help="open the app (or bring it back), wait, print its screen")
    p.add_argument("--restart", action="store_true", help="stop the app first")
    p.add_argument("--timeout", type=float, default=60)
    p = sub.add_parser("clear", parents=[common], help="empty the focused field (or --field, tapped first)")
    p.add_argument("--field", metavar="LABEL", help="tap this field first")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        cfg = configuration(a.workspace, a.serial, a.input)
        if a.command == "check":            # a report: a missing device is a line of it, not the end of it
            try:
                serial, reason = resolve_serial(cfg["serial"]), ""
            except NoTarget as e:
                serial, reason = None, str(e)
            return cmd_check(cfg, serial, a, reason)
        serial = resolve_serial(cfg["serial"])
        return COMMANDS[a.command](cfg, serial, a)
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except NoTarget as e:
        print(f"NO TARGET: {e}", file=sys.stderr)
        return 3
    except NotFound as e:
        print(f"NOT FOUND: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

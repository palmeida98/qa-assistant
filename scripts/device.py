"""
Drives an Android device or emulator through adb, or an iPhone through WebDriverAgent (--platform ios,
technique-mobile.md §1.3), for the conversational mode (technique-mobile.md §2 and §4.1; plan, task 9.1).

    python scripts/device.py <workspace> [--serial S] [--platform android|ios] <subcommand> ...

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
    hold TEXT | --xy X Y [--ms 1200]        a long press (WDA touchAndHold; driver longpress; stationary swipe)
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

- iPhone (technique-mobile.md §1.5): with --platform ios (or $QA_MOBILE_PLATFORM, or mobile.platform) the section
  mobile.ios gives app_id (the bundle id), udid and wda_url ($QA_WDA_URL; default http://localhost:8100). The
  input is always WebDriverAgent (scripts/device_ios.py); the same subcommands and the same privacy rule, the
  foreground being read from WDA. `back` is a swipe from the left edge; `link` is not available.

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
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import device_ios as wda
import ios_diagnose
from common import (
    MOBILE_INPUTS,
    SERIAL_ENV,
    WDA_URL_DEFAULT,
    WDA_URL_ENV,
    OutsideWorkspace,
    find_adb,
    mobile_platform,
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
BURST_MAX_FILES, BURST_MIN_INTERVAL, BURST_MAX_SECONDS, BURST_MAX_MB = 200, 0.5, 600, 200
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


# what happened to a text when typing or sending stopped (docs/pilot-improvements-2026-11-07.md D)
NOTHING_TYPED, PARTLY_TYPED, NOT_SENT, SENT, UNCERTAIN = (
    "nothing typed", "partly typed", "typed, not sent", "sent", "uncertain")
FOCUS_WAIT_S = 3.0              # the keyboard after tapping a field
FIELD_WAIT_S = 3.0              # a field that appears late (the screen still loading)
SENT_WAIT_S = 3.0               # the field emptied after tapping send
FOCUS_POLL_S = 0.3              # between the readings of those three waits


class Interrupted(Exception):
    """Typing or sending stopped; `outcome` says what reached the app. Never repeated automatically: a send is only
    done again by a person who looked at the app (code 3 or 4)."""

    def __init__(self, outcome: str, detail: str, code: int = 4):
        self.outcome, self.detail, self.code = outcome, detail, code
        super().__init__(f"{outcome}: {detail}")


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

def as_list(v) -> list[str]:
    return [str(x) for x in ([v] if isinstance(v, str) else (v or []))]


def configuration(workspace: str, serial: str | None, input_: str | None = None,
                  platform: str | None = None) -> dict:
    try:
        proj = read_project(workspace)
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    m = proj.get("mobile") or {}
    try:
        platform = mobile_platform(m, platform)
    except ValueError as e:
        raise UsageError(str(e)) from None
    if platform == "ios":
        return ios_configuration(workspace, m, serial, proj)
    if not m.get("app_id"):
        raise UsageError("mobile.app_id missing in project.yaml (the app under test, e.g. com.demoshop.app)")

    try:
        serial, input_ = mobile_target(m, serial, input_)
    except ValueError as e:
        raise UsageError(str(e)) from None

    return {"workspace": workspace, "platform": "android", "app_id": str(m["app_id"]), "serial": serial,
            "input": input_,
            "send_labels": as_list(m.get("send_label")) or list(SEND_LABELS),
            "field_labels": as_list(m.get("field_label")),
            "ignore": {x.casefold() for x in as_list(m.get("ignore_labels"))},
            "batch": str(m.get("evidence_batch") or datetime.now().strftime("%Y%m%d")),
            "browser": m.get("browser"),
            "environments": sorted(str(k) for k in (proj.get("environments") or {})
                                   if isinstance(proj.get("environments"), dict)),
            "intended_build": str((m.get("build") or {}).get("version") or "") if isinstance(m.get("build"), dict) else "",
            "signed_in": as_list((m.get("session") or {}).get("signed_in")) if isinstance(m.get("session"), dict) else [],
            "signed_out": as_list((m.get("session") or {}).get("signed_out")) if isinstance(m.get("session"), dict) else []}


def ios_configuration(workspace: str, m: dict, serial: str | None, proj: dict | None = None) -> dict:
    """The iPhone target (technique-mobile.md §1.5): mobile.ios, the input always WebDriverAgent."""
    section = m.get("ios") if isinstance(m.get("ios"), dict) else {}
    proj = proj or {}
    build = section.get("build") if isinstance(section.get("build"), dict) else {}
    app_id = section.get("app_id") or m.get("app_id")
    if not app_id:
        raise UsageError("mobile.ios.app_id missing in project.yaml (the bundle id of the app under test)")
    udid = serial or os.environ.get(SERIAL_ENV, "").strip() or section.get("udid") or ""
    return {"workspace": workspace, "platform": "ios", "app_id": str(app_id), "serial": str(udid),
            "input": "wda",
            "wda_url": os.environ.get(WDA_URL_ENV, "").strip() or str(section.get("wda_url") or WDA_URL_DEFAULT),
            "send_labels": as_list(m.get("send_label")) or list(SEND_LABELS),
            "field_labels": as_list(m.get("field_label")),
            "ignore": {x.casefold() for x in as_list(m.get("ignore_labels"))},
            "batch": str(m.get("evidence_batch") or datetime.now().strftime("%Y%m%d")),
            "browser": None,
            "environments": sorted(str(k) for k in (proj.get("environments") or {})
                                   if isinstance(proj.get("environments"), dict)),
            "intended_build": str(build.get("version") or ""),
            "wda_log": str(section.get("wda_log") or "")}


def ios_on(cfg: dict) -> bool:
    return cfg.get("platform") == "ios"


def on_ios(fn, cfg: dict, *args):
    """Calls device_ios; its errors become the exit codes of this script (no target: 3, no element: 4)."""
    try:
        return fn(cfg, *args)
    except wda.Unreachable as e:
        raise NoTarget(str(e)) from None
    except wda.NoElement as e:
        raise NotFound(str(e)) from None


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
    if ios_on(cfg):
        bundle = on_ios(wda.foreground, cfg)
        if bundle == cfg["app_id"] or (allow_dialogs and bundle in wda.SYSTEM_DIALOGS):
            return
        raise NoTarget(f"{action} refused: the foreground is {bundle or 'nothing'!r}, not {cfg['app_id']}"
                       + (" (SpringBoard is never read: it shows notifications and the app switcher)"
                          if bundle == "com.apple.springboard" else ""))
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
    raise NoTarget(f"uiautomator dump returned no hierarchy after {tries} tries (screen animating or locked?); "
                   "a side menu or an animation can cause it: a tap by coordinates still works (tap --xy X Y)")


def read_screen(cfg: dict, serial: str, allow_dialogs: bool = False) -> list[Node]:
    guard(cfg, serial, "dump", allow_dialogs)
    if ios_on(cfg):
        return [Node(*t) for t in on_ios(wda.source, cfg)]
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
    if ios_on(cfg):
        on_ios(wda.tap, cfg, x, y)
    elif cfg.get("input") == "driver":
        driver(serial, "tap", f"{x},{y}")
    else:
        adb(serial, "shell", "input", "tap", str(x), str(y))


def swipe_xy(cfg: dict, serial: str, x1: int, y1: int, x2: int, y2: int, ms: int) -> None:
    if ios_on(cfg):
        on_ios(wda.swipe, cfg, x1, y1, x2, y2, ms)
    elif cfg.get("input") == "driver":
        driver(serial, "swipe", f"{x1},{y1},{x2},{y2},{ms}")
    else:
        adb(serial, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))


def hold_xy(cfg: dict, serial: str, x: int, y: int, ms: int) -> None:
    """A long press: WDA on an iPhone, the companion driver's `longpress` or a stationary `input swipe` on Android."""
    if ios_on(cfg):
        on_ios(wda.hold, cfg, x, y, ms)
    elif cfg.get("input") == "driver":
        driver(serial, "longpress", f"{x},{y},{ms}")
    else:
        adb(serial, "shell", "input", "swipe", str(x), str(y), str(x), str(y), str(ms))


def press_back(cfg: dict, serial: str) -> None:
    if ios_on(cfg):
        on_ios(wda.back, cfg)
    elif cfg.get("input") == "driver":
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
    if ios_on(cfg):
        on_ios(wda.clear, cfg)
        return "wda clear"
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


def focus_ios(cfg: dict, serial: str, field: str | None, guarded: bool = False) -> str:
    """Before typing on an iPhone: the field named by --field, else the keyboard already up, else the one enabled
    text field of the screen (a late one waited for FIELD_WAIT_S), tapped from the screen just read; then the focus
    confirmed (the keyboard up within FOCUS_WAIT_S). Several candidates, none, or no focus: Interrupted, nothing
    typed. Returns which field. `guarded`: the caller has just checked the foreground."""
    if not guarded:
        guard(cfg, serial, "type")
    if field:
        node = find(read_screen(cfg, serial), field)
        tap_xy(cfg, serial, *node.centre)
        chosen = f"--field {field!r}"
    else:
        start = time.monotonic()
        while True:
            root = on_ios(wda.tree, cfg)
            if wda.has_keyboard(root):
                return "the field already focused"
            fields = wda.editable_fields(root, cfg.get("field_labels") or ())
            enabled = [f for f in fields if f["enabled"]]
            if enabled or time.monotonic() - start >= FIELD_WAIT_S:
                break
            time.sleep(FOCUS_POLL_S)
        if len(enabled) > 1:
            names = ", ".join(repr(f["label"] or f"field at {f['rect'][:2]}") for f in enabled)
            raise Interrupted(NOTHING_TYPED, f"{len(enabled)} editable fields on the screen ({names}): say which "
                              "with --field", 4)
        if not enabled:
            off = [f for f in fields if not f["enabled"]]
            extra = f" ({len(off)} disabled: {', '.join(repr(f['label']) for f in off)})" if off else ""
            hint = "" if cfg.get("field_labels") else (": an app whose input is not exposed as a text field needs "
                                                        "mobile.field_label (its label), or --field")
            raise Interrupted(NOTHING_TYPED, f"no enabled text field on the screen{extra}{hint}", 4)
        x1, y1, x2, y2 = enabled[0]["rect"]
        tap_xy(cfg, serial, (x1 + x2) // 2, (y1 + y2) // 2)
        chosen = f"the only text field {enabled[0]['label']!r}" if enabled[0]["label"] else "the only text field"
    start = time.monotonic()
    while not wda.has_keyboard(on_ios(wda.tree, cfg)):
        if time.monotonic() - start >= FOCUS_WAIT_S:
            raise Interrupted(NOTHING_TYPED, f"{chosen} did not take the focus (no keyboard after "
                              f"{FOCUS_WAIT_S:.0f} s)", 4)
        time.sleep(FOCUS_POLL_S)
    return chosen


def type_text(cfg: dict, serial: str, text: str, field: str | None, via: str = "auto", guarded: bool = False,
              timing: dict | None = None) -> str:
    """Types the text (after tapping the field, if given); returns the route used. `timing` (iOS) gets the seconds
    of the focus and of the keys (backlog 3: the time before a send)."""
    refuse_secrets(cfg["workspace"], text)
    if ios_on(cfg):
        if via != "auto":
            raise UsageError(f"--via {via}: on an iPhone the text is typed through WebDriverAgent")
        began = time.monotonic()
        chosen = focus_ios(cfg, serial, field, guarded=guarded)
        focused_at = time.monotonic()
        try:
            n = wda.type_text(cfg, text, focused=True)
        except wda.PartlyTyped as e:
            raise Interrupted(PARTLY_TYPED, f"{e}; the field holds part of the text (clear it before typing again)",
                              3) from None
        except wda.NoElement as e:
            raise Interrupted(NOTHING_TYPED, str(e), 4) from None
        except wda.Unreachable as e:
            raise Interrupted(UNCERTAIN, f"{e}; the first keys got no answer, part of the text may be in the field: "
                              "look at the app before typing again", 3) from None
        if timing is not None:
            timing.update({"focus_s": round(focused_at - began, 2), "keys_s": round(time.monotonic() - focused_at, 2)})
        return "wda keys" + (f", {n} chunks" if n > 1 else "") + f" into {chosen}"
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


def capture_png(cfg: dict, serial: str) -> bytes:
    """A screenshot of the app under test (the foreground guard first)."""
    guard(cfg, serial, "screenshot")
    if ios_on(cfg):
        png = on_ios(wda.screenshot, cfg)
    else:
        png = adb(serial, "exec-out", "screencap", "-p", binary=True)
    if not png.startswith(b"\x89PNG"):
        raise NoTarget("screencap returned no PNG")
    return png


def evidence_folder(cfg: dict, batch: str | None) -> str:
    try:
        folder = workspace_path(cfg["workspace"], "evidence", batch or cfg["batch"])
    except OutsideWorkspace as e:
        raise UsageError(str(e)) from None
    os.makedirs(folder, exist_ok=True)
    return folder


def check_name(name: str) -> None:
    if not name or name != os.path.basename(name) or name in (".", ".."):
        raise UsageError(f"invalid capture name {name!r}: a file name, without folders")


def shot(cfg: dict, serial: str, name: str, batch: str | None) -> str:
    check_name(name)
    png = capture_png(cfg, serial)
    folder = evidence_folder(cfg, batch)
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

# the probes of the iOS diagnosis (scripts/ios_diagnose.py); the tests replace it
ios_probes = ios_diagnose.Probes


def ios_diagnosis(cfg: dict, session: bool = True) -> tuple[str, str, list[str]]:
    """(key, block, lines) of the read-only diagnosis: the main block and the next action first, then the details."""
    findings = ios_diagnose.diagnose(cfg, ios_probes(cfg), session=session)
    key, block, _ = ios_diagnose.verdict(findings)
    return key, block, ios_diagnose.render(findings)


def cmd_check_ios(cfg, serial, a) -> int:
    """One report of the iPhone target, nothing changed on it (technique-mobile.md §1.3 and §1.5). 0 ready, 3 not ready.
    When WebDriverAgent does not answer or is not ready, the read-only diagnosis says why and what to do next."""
    out, blockers, advice = [], [], []
    envs = f"environment: {', '.join(cfg['environments']) or 'none declared'}, app {cfg['app_id']}"
    try:
        st = on_ios(wda.status, cfg)
    except NoTarget as e:
        out += [f"device: not reachable ({e})", envs, "session: unknown (no device)"]
        _, block, lines = ios_diagnosis(cfg)
        blockers.append(block)
        out = lines + out
        st = None
    if st is not None and not st.get("ready", True):
        blockers.append(f"WebDriverAgent is not ready ({st.get('message', 'no message')})")
        _, _, lines = ios_diagnosis(cfg)
        out = lines + out
    if st is not None and not blockers:
        out.append(f"target: {serial or 'iPhone'} · iOS {(st.get('os') or {}).get('version', '?')} · "
                   f"WebDriverAgent {(st.get('build') or {}).get('version', '?')} ready at {cfg['wda_url']}")
        out.append(f"foreground: {on_ios(wda.foreground, cfg) or 'nothing'}")
        version = wda.installed_version(serial, cfg["app_id"])
        out.append(f"app: {cfg['app_id']} {version or '(version not read: no UDID or devicectl)'}")
        out.append("input: wda")
        lines, b2, a2 = build_roles(cfg, a, f"{cfg['app_id']} {version}" if version else None)
        out += lines
        blockers += b2
        advice += a2
        out.append(envs)
        out.append("session: unknown (the iOS driver does not check the session; look at the app)")
    out.append("ready: yes" if not blockers else "ready: NOT READY: " + "; ".join(blockers))
    out += [f"next: {x}" for x in advice]
    print("\n".join(out))
    if blockers:
        print(f"NO TARGET: {blockers[0]}", file=sys.stderr)
        return 3
    return 0


def cmd_doctor(cfg, serial, a) -> int:
    """The read-only diagnosis of the iPhone target, whatever WebDriverAgent answers: 0 ready, 3 blocked."""
    if not ios_on(cfg):
        raise UsageError("doctor is for --platform ios (an Android target: `check`)")
    key, block, lines = ios_diagnosis(cfg, session=not a.no_session)
    print("\n".join(lines))
    if key != "ready":
        print(f"NO TARGET: {block}", file=sys.stderr)
        return 3
    return 0


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
            lines, b2, a2 = build_roles(cfg, a, observed)
            out += lines
            blockers += b2
            advice += a2
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


def _notice(cfg: dict, a, notice_id: str, text: str) -> bool:
    """Whether to print an advice that does not need an action now: always in the complete modes; in the short one only
    when it is new or changed for this workspace (scripts/output.py): the same advice at every check is noise."""
    try:
        import output
        from common import read_project
        proj = read_project(cfg["workspace"])
        settings = output.settings(proj, output.cli_choice(a))
        return output.Notices(cfg["workspace"], settings).show(notice_id, text)
    except Exception:       # noqa: BLE001 - never lose an advice to a failure of the notice state
        return True


def build_roles(cfg: dict, a, observed: str | None) -> tuple[list[str], list[str], list[str]]:
    """The three builds of a check, each with its source (technique-mobile.md §1.4): the line `build:` (OBSERVED, read from
    the device now, against the intended one), `build intended:` (`mobile.build.version`, a person's setting, never
    rewritten from the device) and `build of the run:` (`run.build` of the batch's run log, fixed by its first result;
    needs --batch). Returns (lines, blockers, advice). A build that changed during the batch is a blocker."""
    lines, blockers, advice = [], [], []
    intended = cfg.get("intended_build") or ""
    if observed and intended:
        verdict = "same" if same_build(observed, intended) else "DIFFERS"
        lines.append(f"build: observed {observed}, intended {intended}: {verdict}")
        if verdict == "DIFFERS":
            text = ("the observed build is not the intended one (mobile.build.version): confirm it is the one "
                    "to test; the intended build is never rewritten from the device")
            if _notice(cfg, a, f"build-differs:{observed}:{intended}", text):
                advice.append(text)
    elif observed:
        lines.append(f"build: observed {observed}, intended not set")
    else:
        lines.append("build: not read on the device: confirm it in the app (Conta / About) before recording results")
    lines.append(f"build intended: {intended or 'not set'} (mobile.build.version, a person's setting; never rewritten "
                 "from the device)")
    batch = getattr(a, "batch", None)
    if not batch:
        lines.append("build of the run: not compared (give --batch <name> to compare with the run log's build)")
        return lines, blockers, advice
    of_run = _batch_build(cfg["workspace"], batch)
    if of_run is None:
        lines.append(f"build of the run: batch {batch}: no run log yet, the first result will record the observed build")
    elif not observed:
        lines.append(f"build of the run: {of_run} (run.build of batch {batch}); the observed build was not read, "
                     "so they were not compared")
    elif not same_build(observed, of_run):
        lines.append(f"build of the run: {of_run} (run.build of batch {batch}): DIFFERS from the observed one")
        blockers.append(f"the build changed during the batch: {batch} started on {of_run!r} and the device now has "
                        f"{observed!r}")
        advice.append("stop this batch (its results stay as they are) and plan a new batch with the right "
                      "context (/plan-tests)")
    else:
        lines.append(f"build of the run: {of_run} (run.build of batch {batch}): same as the observed one")
    return lines, blockers, advice


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


# what the readings of a streamed answer could show (Sampler.summary)
HIDDEN_PARTIAL = "partial text observed, hidden in the tree"
HIDDEN_READABLE = "answer readable only hidden in the tree, no intermediate state captured"
PARTIAL_OBSERVED = "partial text observed"
READABLE_NO_INTERMEDIATE = "answer readable, no intermediate state captured"
ANSWER_NOT_READABLE = "answer text not readable"
SAMPLING_INSUFFICIENT = "sampling failed or insufficient"


class Sampler:
    """Timed readings of a streamed answer (technique-mobile.md 4.1), limited in number and in time."""

    def __init__(self, text: str, ignore: set[str], samples: int, seconds: float, interval: float,
                 baseline: set[str] | None = None, content=None, hidden=None):
        # content: a function giving the reading's content texts (iOS: no controls), instead of every line;
        # hidden: a function giving (text, shown) in the tree's order, to read the invisible answer apart (--read-hidden)
        self.content, self.hidden = content, hidden
        # baseline: what was on the screen before the send (buttons, the field's label): never the answer
        self.text, self.ignore, self.baseline = text, ignore, {x.casefold() for x in baseline or ()}
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
                new, _ = after_message(self.content() if self.content else lines, self.text, self.ignore)
                texts = [ln.split("] ", 1)[1] if "] " in ln else ln for ln in new]
                answer = "\n".join(t for t in texts if t.casefold() not in self.baseline)
                sample = {"at": datetime.now().isoformat(timespec="milliseconds"),
                          "elapsed_s": round(time.monotonic() - self.started, 2), "kind": "read", "answer": answer}
                if self.hidden:
                    items = self.hidden()
                    shown = {t: v for t, v in items}
                    after, _ = after_message([t for t, _ in items], self.text, self.ignore)
                    sample["hidden_answer"] = "\n".join(t for t in after if not shown.get(t)
                                                        and t.casefold() not in self.baseline)
                self.samples.append(sample)
            return lines
        return sampled

    def summary(self) -> dict:
        """What the readings could show (docs/pilot-improvements-2026-11-07.md E). `evidence` is about the tool's
        reading, never a verdict on the app: an answer the accessibility tree does not expose is "not readable", never
        "no streaming"."""
        reads = [s for s in self.samples if s["kind"] == "read"]
        answers = [s["answer"] for s in reads]
        readable = [s for s in reads if s["answer"].strip()]
        partial = any(a and b != a and b.startswith(a) for i, a in enumerate(answers) for b in answers[i + 1:])
        hidden = [s.get("hidden_answer") or "" for s in reads]
        hidden_partial = any(a and b != a and b.startswith(a) for i, a in enumerate(hidden) for b in hidden[i + 1:])
        if len(reads) < 2:
            evidence, observed = SAMPLING_INSUFFICIENT, None
        elif not readable and any(h.strip() for h in hidden):
            evidence, observed = (HIDDEN_PARTIAL, True) if hidden_partial else (HIDDEN_READABLE, False)
        elif not readable:
            evidence, observed = ANSWER_NOT_READABLE, None
        elif partial:
            evidence, observed = PARTIAL_OBSERVED, True
        else:
            evidence, observed = READABLE_NO_INTERMEDIATE, False
        return {"reads": len(reads), "failed_captures": sum(s["kind"] == "capture-failed" for s in self.samples),
                "app_not_in_foreground": sum(s["kind"] == "app-not-foreground" for s in self.samples),
                "readable_reads": len(readable), "evidence": evidence, "partial_text_observed": observed,
                "first_readable_s": readable[0]["elapsed_s"] if readable else None,
                "limits_hit": not self._room()}

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


class Burst:
    """Timed screenshots of the app while an answer arrives (docs/pilot-improvements-2026-11-07.md E), for when the
    accessibility tree does not expose the answer's text. Limited in files, seconds and megabytes; each file written
    whole (a temporary name, then renamed); a manifest <name>_burst.json ties them to one attempt with their times,
    the gaps and which limit stopped it. It never sends anything. Sizes or pixels never prove streaming: the content
    of the images is judged (a waiting indicator, a partial answer, the final answer)."""

    def __init__(self, cfg: dict, serial: str, name: str, batch: str | None, count: int, seconds: float,
                 interval: float, max_mb: float):
        check_name(name)
        if not 1 <= count <= BURST_MAX_FILES:
            raise UsageError(f"--burst / --count: 1 to {BURST_MAX_FILES} screenshots")
        if interval < BURST_MIN_INTERVAL or not 0 < seconds <= BURST_MAX_SECONDS or not 0 < max_mb <= BURST_MAX_MB:
            raise UsageError(f"burst limits: interval >= {BURST_MIN_INTERVAL} s, seconds <= {BURST_MAX_SECONDS}, "
                             f"megabytes <= {BURST_MAX_MB}")
        self.cfg, self.serial, self.name = cfg, serial, name.removesuffix(".png")
        self.folder = evidence_folder(cfg, batch)
        self.limits = {"files": count, "seconds": seconds, "interval_s": interval, "megabytes": max_mb}
        self.started_at = datetime.now().isoformat(timespec="milliseconds")
        self.t0 = time.monotonic()
        self.attempt = f"{self.name}@{self.started_at}"
        self.sent_s: float | None = None
        self.files: list[dict] = []
        self.failed: list[dict] = []
        self.bytes = 0
        self.stopped_by: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def elapsed(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    def mark_sent(self) -> None:
        self.sent_s = self.elapsed()

    def room(self) -> bool:
        if self.stopped_by:
            return False
        if len(self.files) + len(self.failed) >= self.limits["files"]:     # a failed capture is an attempt too
            self.stopped_by = "files"
        elif self.elapsed() > self.limits["seconds"]:
            self.stopped_by = "seconds"
        elif self.bytes >= self.limits["megabytes"] * 1024 * 1024:
            self.stopped_by = "megabytes"
        return self.stopped_by is None

    def take(self) -> None:
        if not self.room():
            return
        at, elapsed = datetime.now().isoformat(timespec="milliseconds"), self.elapsed()
        try:
            png = capture_png(self.cfg, self.serial)
        except (NoTarget, NotFound) as e:
            self.failed.append({"at": at, "elapsed_s": elapsed, "reason": str(e)[:200]})
            return
        if self.bytes + len(png) > self.limits["megabytes"] * 1024 * 1024:
            self.stopped_by = "megabytes"
            return
        name = f"{self.name}_b{len(self.files) + 1:02d}.png"
        path = os.path.join(self.folder, name)
        tmp = path + ".part"
        try:
            with open(tmp, "wb") as f:
                f.write(png)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.bytes += len(png)
        self.files.append({"file": os.path.relpath(path, self.cfg["workspace"]).replace(os.sep, "/"), "at": at,
                           "elapsed_s": elapsed, "bytes": len(png)})

    # in the background on its own clock; False: one capture per reading of the screen (the tests' fake clock)
    threaded = True

    def wrap(self, read):
        def taken():
            self.take()
            return read()
        return taken

    def run(self) -> None:
        """Captures on its own clock until a limit or stop(): each wait is the interval minus the time the capture
        took, so the cadence does not depend on the readings of the screen (backlog 1, 07.11.2026)."""
        while not self._stop.is_set() and self.room():
            began = time.monotonic()
            self.take()
            rest = self.limits["interval_s"] - (time.monotonic() - began)
            if rest > 0 and not self._stop.is_set() and self.room():
                time.sleep(rest)

    def start(self) -> None:
        """The burst in the background (send --burst): the screen is still read for the answer meanwhile."""
        self._thread = threading.Thread(target=self.run, name="burst", daemon=True)
        self._thread.start()

    def stop(self, reason: str) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        if not self.stopped_by:
            self.stopped_by = reason

    def manifest(self) -> dict:
        times = [f["elapsed_s"] for f in self.files]
        gaps = [{"after": self.files[i]["file"], "seconds": round(b - a, 2)}
                for i, (a, b) in enumerate(itertools.pairwise(times)) if b - a > 1.5 * self.limits["interval_s"]]
        return {"attempt": self.attempt, "started_at": self.started_at, "sent_after_s": self.sent_s,
                "limits": self.limits, "stopped_by": self.stopped_by or "done",
                "observed_s": [times[0], times[-1]] if times else None, "files": self.files,
                "failed": self.failed, "gaps": gaps,
                "note": "screenshots only: judge their content (waiting indicator, partial answer, final answer); "
                        "file sizes and pixel changes do not prove streaming"}

    def write(self) -> str:
        path = os.path.join(self.folder, self.name + "_burst.json")
        atomic_text(path, json.dumps(self.manifest(), ensure_ascii=False, indent=1) + "\n")
        return path

    def line(self) -> str:
        m = self.manifest()
        window = f"{m['observed_s'][0]}-{m['observed_s'][1]} s" if m["observed_s"] else "nothing"
        return (f"burst: {len(self.files)} screenshot(s), {len(self.failed)} failed, observed {window}"
                + (f" after the send at {m['sent_after_s']} s" if m["sent_after_s"] is not None else "")
                + f", stopped by {m['stopped_by']}" + (f", {len(m['gaps'])} gap(s)" if m["gaps"] else ""))


def atomic_text(path: str, text: str) -> None:
    tmp = path + ".part"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def cmd_shot_burst(cfg, serial, a) -> int:
    """Timed screenshots of the app, alone (an assisted step: the person sends, the tool watches). Start it BEFORE
    the action whose answer must be seen."""
    burst = Burst(cfg, serial, a.name, a.batch, a.count, a.seconds, a.interval, a.max_mb)
    burst.run()
    print(burst.line())
    print(f"manifest: {burst.write()}")
    return 0 if burst.files else 3


def cmd_send(cfg, serial, a) -> int:
    a.text = text_of(a)
    if a.read_hidden and not (ios_on(cfg) and a.samples):
        raise UsageError("--read-hidden goes with --samples on an iPhone")       # before anything is typed
    guard(cfg, serial, "send")
    burst = (Burst(cfg, serial, a.capture or "send", a.batch, a.burst, a.burst_seconds, a.burst_interval, a.burst_mb)
             if a.burst else None)          # the limits checked before anything is typed
    timing: dict = {}
    began = time.monotonic()
    type_text(cfg, serial, a.text, a.field, guarded=True, timing=timing)
    labels = [a.send_label] if a.send_label else cfg["send_labels"]
    typed_tree = None
    if ios_on(cfg):         # one reading after typing: the send control, the baseline and the field's label
        typed_tree = on_ios(wda.tree, cfg)
        nodes = [Node(*t) for t in wda.nodes(typed_tree)]
    else:
        try:
            nodes = read_screen(cfg, serial)
        except (NoTarget, NotFound) as e:       # typed, and the send control could not even be looked for
            raise Interrupted(NOT_SENT, f"the text was typed but the screen could not be read to find the send control "
                              f"({e}); it was not sent: look at the app", 4) from None
    control = next((n for lab in labels for n in nodes if n.text.casefold() == lab.casefold()), None)
    if control is None:   # contains, never the field that still shows the message
        key = a.text[:40].casefold()
        control = next((n for lab in labels for n in nodes
                        if lab.casefold() in n.text.casefold() and key not in n.text.casefold()), None)
    baseline = {n.text for n in nodes if a.text[:40].casefold() not in n.text.casefold()}
    held = any(a.text[:40].casefold() in n.text.casefold() for n in nodes)     # the field shows what was typed
    held_at = {(n.x1, n.y1, n.x2, n.y2) for n in nodes if a.text[:40].casefold() in n.text.casefold()}   # where
    if typed_tree is not None:          # the field's own label comes back once it is emptied: not the answer
        baseline |= {f["label"] for f in wda.editable_fields(typed_tree, cfg.get("field_labels") or ()) if f["label"]}
    if control is None:
        if ios_on(cfg):
            raise Interrupted(NOT_SENT, f"the text is in the field but the send control was not found (tried {labels}; "
                              "set mobile.send_label or --send-label)", 4)
        raise NotFound(f"send control not found (tried {labels}; set mobile.send_label or --send-label)")
    if ios_on(cfg):
        try:
            on_ios(wda.tap, cfg, *control.centre)
        except (NoTarget, NotFound) as e:
            raise Interrupted(UNCERTAIN, f"the tap on send got no answer ({e}): the message may or may not have gone; "
                              "look at the app before sending again", 3) from None
    else:
        try:
            tap_xy(cfg, serial, *control.centre)
        except (NoTarget, NotFound) as e:
            raise Interrupted(UNCERTAIN, f"the tap on send got no answer ({e}): the message may or may not have gone; "
                              "look at the app before sending again", 3) from None
    if timing:
        timing["to_send_s"] = round(time.monotonic() - began, 2)
        print(f"timing: focus {timing['focus_s']} s, keys {timing['keys_s']} s, until the tap on send "
              f"{timing['to_send_s']} s")
    if burst:                   # the first screenshot right after the tap, then on the burst's own clock
        burst.mark_sent()
        if burst.threaded:
            burst.start()
        else:
            burst.take()
    outcome = (confirm_sent_ios(cfg, a.text, held=held) if ios_on(cfg)
               else confirm_sent_android(cfg, serial, a.text, held_at))
    print(f"outcome: {outcome}")

    last: dict = {}

    def read():
        if ios_on(cfg):         # one read of the tree gives the lines and the content the sampler keeps
            guard(cfg, serial, "read")
            last["tree"] = on_ios(wda.tree, cfg)
            return [Node(*t).line() for t in wda.nodes(last["tree"])]
        return [n.line() for n in read_screen(cfg, serial)]

    def content():
        return wda.content_texts(last.get("tree"), cfg.get("field_labels") or ())
    def hidden():
        return wda.content_items(last.get("tree"), cfg.get("field_labels") or ())
    sampler = (Sampler(a.text, cfg["ignore"], a.samples, a.sample_seconds, a.sample_interval, baseline=baseline,
                       content=content if ios_on(cfg) else None, hidden=hidden if a.read_hidden else None)
               if a.samples else None)
    reader = read if not burst or burst.threaded else burst.wrap(read)
    try:
        if sampler:
            lines, stable = wait_stable(sampler.wrap(reader), a.timeout, interval=a.sample_interval)
            if not isinstance(lines, list):
                lines = sampler.last_good
        else:
            lines, stable = wait_stable(reader, a.timeout,
                                        interval=a.burst_interval if burst and not burst.threaded else POLL_S)
    except (NoTarget, NotFound) as e:           # the message went (or may have): never end with a bare NO TARGET
        raise Interrupted(SENT if outcome.startswith(SENT) else UNCERTAIN,
                          f"the answer could not be read ({e}); the message outcome was {outcome!r}: look at the app "
                          "before sending again", 3) from None
    finally:
        if burst:
            burst.stop("settled")
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
        seen = {True: "yes", False: "no", None: "not known"}[s["partial_text_observed"]]
        print(f"samples: {s['reads']} read, {s['failed_captures']} capture-failed, {s['app_not_in_foreground']} "
              f"app-not-foreground; evidence: {s['evidence']} (partial text observed: {seen}) ({path})")
        if s["evidence"] in (ANSWER_NOT_READABLE, SAMPLING_INSUFFICIENT):
            print("NOTE: the readings cannot show whether the answer was streamed (a limit of the reading, not a "
                  "result of the app): use --burst for timed screenshots and judge their content", file=sys.stderr)
    if burst:
        print(burst.line())
        print(f"manifest: {burst.write()}")
    if not stable:
        raise NoTarget(f"the screen did not settle in {a.timeout:.0f} s (answer still streaming?): "
                       "the lines above are the last reading")
    return 0


def confirm_sent_android(cfg: dict, serial: str, text: str, held_at: set) -> str:
    """After the tap on send, the Android counterpart of `confirm_sent_ios`: `sent` once the message is no longer in the
    field where it was typed (`held_at`: the bounds of the nodes that showed it) or, when no field showed it, once it is
    shown on the screen; the text still in the field after SENT_WAIT_S is Interrupted (typed, not sent); a message
    nowhere to be seen, or a screen that cannot be read, leave it `uncertain` (the answer is still read, nothing is sent
    again). A dump that fails is a result, not a bare error: the message may have gone."""
    key = text[:40].casefold()
    start = time.monotonic()
    while True:
        try:
            nodes = read_screen(cfg, serial)
        except (NoTarget, NotFound) as e:
            return f"{UNCERTAIN} (the screen could not be read after the tap: {e}; nothing is sent again)"
        showing = [n for n in nodes if key and key in n.text.casefold()]
        in_field = any((n.x1, n.y1, n.x2, n.y2) in held_at for n in showing)
        if held_at and not in_field:
            return f"{SENT} (the field no longer holds the message)"
        if not held_at and showing:
            return f"{SENT} (the message is shown on the screen)"
        if time.monotonic() - start >= SENT_WAIT_S:
            if held_at:
                raise Interrupted(NOT_SENT, f"the message is still in the field {SENT_WAIT_S:.0f} s after the tap on "
                                  "send: look at the app; it is not sent again", 4)
            return (f"{UNCERTAIN} (the message is not shown {SENT_WAIT_S:.0f} s after the tap; "
                    "nothing is sent again)")
        time.sleep(FOCUS_POLL_S)


def confirm_sent_ios(cfg: dict, text: str, held: bool = True) -> str:
    """After the tap on send: `sent` once the message is out of the field (when the field showed it: `held`) or shown
    on the screen outside a field, within SENT_WAIT_S; the text still in the field is Interrupted (typed, not sent);
    a field that never exposes its text and a message not shown leave it `uncertain`, as does a screen that cannot be
    read (the answer is still read, nothing is sent again)."""
    key = text[:40].casefold()
    start = time.monotonic()
    while True:
        try:
            root = wda.tree(cfg)
            fields = wda.editable_fields(root, cfg.get("field_labels") or ())
            shown = [t for *_, t in wda.nodes(root)]
        except (wda.Unreachable, wda.NoElement) as e:
            return f"{UNCERTAIN} (the screen could not be read after the tap: {e}; nothing is sent again)"
        in_field = any(key and key in str(f["value"]).casefold() for f in fields)
        labels = {str(f["label"]).casefold() for f in fields} | {str(f["value"]).casefold() for f in fields}
        on_screen = any(key and key in t.casefold() and t.casefold() not in labels for t in shown)
        if held and not in_field:
            return f"{SENT} (the field no longer holds the message)"
        if not held and on_screen:
            return f"{SENT} (the message is shown on the screen)"
        if not held and time.monotonic() - start >= SENT_WAIT_S:
            return (f"{UNCERTAIN} (the field does not expose its text and the message is not shown {SENT_WAIT_S:.0f} s "
                    "after the tap; nothing is sent again)")
        if time.monotonic() - start >= SENT_WAIT_S:
            raise Interrupted(NOT_SENT, f"the message is still in the field {SENT_WAIT_S:.0f} s after the tap on send: "
                              "look at the app; it is not sent again", 4)
        time.sleep(FOCUS_POLL_S)


def cmd_hold(cfg, serial, a) -> int:
    if a.ms < 1:
        raise UsageError("--ms must be a positive number of milliseconds")
    if a.xy:
        guard(cfg, serial, "hold", allow_dialogs=True)
        x, y = a.xy
        label = f"({x},{y})"
    elif a.text:
        node = find(read_screen(cfg, serial, allow_dialogs=True), a.text, a.index)
        (x, y), label = node.centre, node.line()
    else:
        raise UsageError("hold needs a TEXT or --xy X Y")
    hold_xy(cfg, serial, x, y, a.ms)
    print(f"held: {label} for {a.ms} ms")
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
    if ios_on(cfg):
        on_ios(wda.launch, cfg, app, a.restart)
        start = time.monotonic()
        while on_ios(wda.foreground, cfg) != app:
            if time.monotonic() - start > a.timeout:
                raise NoTarget(f"{app} did not come to the foreground in {a.timeout:.0f} s")
            time.sleep(1)
        lines, stable = wait_stable(lambda: [n.line() for n in read_screen(cfg, serial, allow_dialogs=True)],
                                    max(a.timeout - (time.monotonic() - start), SETTLE_MIN_S + POLL_S))
        print(f"launched: {app}" + ("" if stable else " (the screen was still changing)"))
        for line in lines:
            print(line)
        return 0
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
    if ios_on(cfg):
        raise UsageError("link is not available on an iPhone yet: the person opens the link (technique-mobile.md §1.5)")
    print(f"{open_link(cfg, serial, a.url, a.package)}: opened")   # never the URL: it is a one-time credential
    return 0


COMMANDS = {"check": cmd_check, "dump": cmd_dump, "tap": cmd_tap, "type": cmd_type, "send": cmd_send,
            "shot": cmd_shot, "link": cmd_link, "swipe": cmd_swipe, "hold": cmd_hold, "back": cmd_back,
            "clear": cmd_clear, "launch": cmd_launch, "doctor": cmd_doctor, "shot-burst": cmd_shot_burst}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Drives an Android device/emulator through adb "
                                             "(technique-mobile.md §4.1).")
    ap.add_argument("workspace")
    ap.add_argument("--serial",
                    help="device serial (default: $QA_MOBILE_SERIAL, mobile.serial, or the only device connected)")
    ap.add_argument("--input", choices=INPUTS,
                    help="adb, or the companion driver (default: $QA_MOBILE_INPUT, mobile.input, else adb)")
    ap.add_argument("--platform", choices=("android", "ios"),
                    help="android (adb) or ios (WebDriverAgent; default: $QA_MOBILE_PLATFORM, mobile.platform, "
                         "else android)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--serial", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--input", choices=INPUTS, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--platform", choices=("android", "ios"), default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", parents=[common], help="one report: device, app, driver, build, environment, session")
    p.add_argument("--batch", help="also compare the observed build with the run log of this batch")
    import output
    output.add_flags(p)
    p = sub.add_parser("doctor", parents=[common], help="iOS: why the iPhone target is not ready, read only (§1.5)")
    p.add_argument("--no-session", action="store_true",
                   help="strictly passive: do not open and close a WebDriverAgent session (the proof of UI automation)")
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
    p.add_argument("--read-hidden", action="store_true",
                   help="iOS with --samples: also read the labelled elements WDA reports as invisible after the message "
                        "(kept apart as hidden_answer; an app whose answers are not exposed as visible text)")
    p.add_argument("--burst", type=int, default=0, metavar="N",
                   help="also up to N timed screenshots from the tap on send (evidence/<batch>/<capture>_bNN.png and "
                        "<capture>_burst.json; default off)")
    p.add_argument("--burst-interval", type=float, default=1.5, help="seconds between screenshots (default 1.5)")
    p.add_argument("--burst-seconds", type=float, default=60, help="stop the burst after this many seconds (default 60)")
    p.add_argument("--burst-mb", type=float, default=25, help="stop the burst at this many megabytes (default 25)")
    p = sub.add_parser("shot-burst", parents=[common],
                       help="timed screenshots of the app with a manifest (start it before the action to watch)")
    p.add_argument("name")
    p.add_argument("--batch")
    p.add_argument("--count", type=int, default=20, help="at most this many screenshots (default 20)")
    p.add_argument("--interval", type=float, default=1.5, help="seconds between screenshots (default 1.5)")
    p.add_argument("--seconds", type=float, default=60, help="stop after this many seconds (default 60)")
    p.add_argument("--max-mb", type=float, default=25, help="stop at this many megabytes (default 25)")
    p = sub.add_parser("shot", parents=[common], help="screenshot to evidence/<batch>/<NAME>.png")
    p.add_argument("name")
    p.add_argument("--batch")
    p = sub.add_parser("link", parents=[common], help="open a deep link / login callback (never printed)")
    p.add_argument("url")
    p.add_argument("--package", help="app that opens it (default: mobile.browser, else the system's choice)")
    p = sub.add_parser("hold", parents=[common], help="a long press by text (exact, then contains) or by coordinates")
    p.add_argument("text", nargs="?")
    p.add_argument("--index", type=int, default=0, help="which match, from 0")
    p.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))
    p.add_argument("--ms", type=int, default=1200, help="duration in milliseconds (default 1200)")
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
        cfg = configuration(a.workspace, a.serial, a.input, a.platform)
        if ios_on(cfg):
            if a.command == "check":        # a report: an unreachable WebDriverAgent is a line of it
                return cmd_check_ios(cfg, cfg["serial"], a)
            if a.command == "doctor":
                return cmd_doctor(cfg, cfg["serial"], a)
            serial = cfg["serial"]
        else:
            if a.command == "check":        # a report: a missing device is a line of it, not the end of it
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
    except Interrupted as e:
        print(f"STOPPED: {e.detail}", file=sys.stderr)
        print(f"outcome: {e.outcome} (not repeated automatically)", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

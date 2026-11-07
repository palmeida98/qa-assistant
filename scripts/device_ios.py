"""
The iPhone backend of scripts/device.py (technique-mobile.md §1.5): WebDriverAgent (WDA) over HTTP.

Only the standard library. `request` is the one door to the device (the tests replace it). Errors are
Unreachable (WDA does not answer or refused the call: device.py turns it into "no target", exit 3) and NoElement
(nothing focused to type into: exit 4). An error message never carries a request body (it can hold typed text).
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import urllib.error
import urllib.request

TIMEOUT = 30
CHUNK = 300                      # characters per /wda/keys call
CLEAR_KEYS = 250                 # backspaces of `clear`
# Nothing but the app under test is ever read: SpringBoard also shows the person's notification banners and the
# app switcher, so it is not allowed (a permission alert is for the person to answer).
SYSTEM_DIALOGS: tuple[str, ...] = ()
EDGE_X = 2                       # points from the left edge where the back swipe starts
WDA_HELP = "start WebDriverAgent and `iproxy 8100 8100`: technique-mobile.md §1.5"
ELEMENT_KEYS = ("ELEMENT", "element-6066-11e4-a52e-4f735466cecf")
TEXT_FIELDS = ("TextField", "TextView", "SearchField", "SecureTextField")


def kind(el: dict) -> str:
    """The element type without the XCUIElementType prefix (the JSON source writes `Button`, the XML `XCUIElementTypeButton`)."""
    return str(el.get("type") or "").removeprefix("XCUIElementType")


class Unreachable(Exception):
    """WDA does not answer, or refused the call."""


class NoElement(Exception):
    """No element (e.g. no focused field)."""


class PartlyTyped(Exception):
    """The typing stopped after `done` of `total` chunks: part of the text is in the field."""

    def __init__(self, done: int, total: int, reason: str):
        self.done, self.total, self.reason = done, total, reason
        super().__init__(f"{done} of {total} chunks typed, then: {reason}")


# --- the one door -------------------------------------------------------------------------------------------------

def request(cfg: dict, method: str, path: str, body: dict | None = None, timeout: float = TIMEOUT) -> dict:
    """One WDA call; returns the decoded JSON ({'value': ..., 'sessionId': ...})."""
    url = cfg["wda_url"].rstrip("/") + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            value = json.loads(e.read().decode("utf-8", "replace")).get("value") or {}
        except ValueError:
            value = {}
        error = value.get("error", f"HTTP {e.code}") if isinstance(value, dict) else f"HTTP {e.code}"
        message = str(value.get("message", ""))[:160] if isinstance(value, dict) else ""
        if error == "no such element":
            raise NoElement(message or "no such element") from None
        raise Unreachable(f"WDA {path.split('?')[0]}: {error} {message}".strip()) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Unreachable(f"WebDriverAgent did not answer at {cfg['wda_url']} ({WDA_HELP})") from None
    try:
        decoded = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        decoded = None
    if not isinstance(decoded, dict):
        raise Unreachable(f"WDA {path.split('?')[0]}: the answer is not a WebDriverAgent JSON object")
    return decoded


def value(cfg: dict, method: str, path: str, body: dict | None = None):
    return request(cfg, method, path, body).get("value")


# --- state and session --------------------------------------------------------------------------------------------

def status(cfg: dict) -> dict:
    v = value(cfg, "GET", "/status")
    if not isinstance(v, dict):
        raise Unreachable(f"WDA /status: unexpected answer ({WDA_HELP})")
    return v


def foreground(cfg: dict) -> str:
    """The bundle id of the app in the foreground ('' if none)."""
    v = value(cfg, "GET", "/wda/activeAppInfo")
    return str(v.get("bundleId") or "") if isinstance(v, dict) else ""


def session(cfg: dict, bundle_id: str | None = None) -> str:
    """A WDA session id; created on first use (without a bundle id it attaches to the foreground app, with one
    it opens or activates that app). Only called after the foreground guard."""
    if bundle_id is None and cfg.get("_wda_session"):
        return cfg["_wda_session"]
    always = {"bundleId": bundle_id} if bundle_id else {}
    r = request(cfg, "POST", "/session", {"capabilities": {"alwaysMatch": always}})
    sid = r.get("sessionId") or (r.get("value") or {}).get("sessionId")
    if not sid:
        raise Unreachable("WDA did not open a session (is UI Automation on? technique-mobile.md §1.5)")
    cfg["_wda_session"] = sid
    return sid


# --- reading the screen -------------------------------------------------------------------------------------------

def label_of(el: dict) -> str:
    """value of a text field (what was typed), else label, else name; line breaks as ' / '."""
    text = ""
    if kind(el) in TEXT_FIELDS and isinstance(el.get("value"), str):
        text = el["value"].strip()
    text = text or str(el.get("label") or "").strip() or str(el.get("name") or "").strip()
    return " / ".join(p.strip() for p in text.replace("\r", "").split("\n") if p.strip())


def nodes(tree) -> list[tuple[int, int, int, int, str]]:
    """(x1, y1, x2, y2, text) per visible element with a text and a size, in points; the same rectangle and text
    (a container repeating its child's label) once."""
    out, seen = [], set()

    def walk(el):
        if not isinstance(el, dict):
            return
        r = el.get("rect") or {}
        try:
            x1, y1 = round(float(r["x"])), round(float(r["y"]))
            x2, y2 = x1 + round(float(r["width"])), y1 + round(float(r["height"]))
        except (KeyError, TypeError, ValueError):
            x1 = y1 = x2 = y2 = 0
        text = label_of(el)
        visible = str(el.get("isVisible", "1")) not in ("0", "false", "False")
        if text and visible and x2 > x1 and y2 > y1 and (x1, y1, x2, y2, text) not in seen:
            seen.add((x1, y1, x2, y2, text))
            out.append((x1, y1, x2, y2, text))
        for child in el.get("children") or []:
            walk(child)
    walk(tree)
    return out


def source(cfg: dict) -> list[tuple[int, int, int, int, str]]:
    return nodes(value(cfg, "GET", "/source?format=json"))


def tree(cfg: dict):
    """The accessibility tree as WDA gives it (JSON), for the reads that need the element types."""
    return value(cfg, "GET", "/source?format=json")


def _flag(el: dict, key: str, default: str = "1") -> bool:
    return str(el.get(key, default)) not in ("0", "false", "False")


def editable_fields(root, labels: tuple[str, ...] | list[str] = ()) -> list[dict]:
    """The visible text fields of the screen: {label, value, rect (x1, y1, x2, y2), enabled, declared}, in the tree's
    order. The label is the field's accessibility label, else its placeholder or name (never the text typed in it).
    `labels` (mobile.field_label): elements of another type whose label or name contains one of them also count, for
    apps whose text input is not exposed as a text field (a React Native composer seen as `Other`, 07.11.2026)."""
    out = []
    wanted = [x.casefold() for x in labels if x]

    def declared(el) -> bool:
        text = f"{el.get('label') or ''} {el.get('name') or ''}".casefold()
        return any(w in text for w in wanted)

    def walk(el):
        if not isinstance(el, dict):
            return
        if (kind(el) in TEXT_FIELDS or declared(el)) and _flag(el, "isVisible"):
            r = el.get("rect") or {}
            try:
                x1, y1 = round(float(r["x"])), round(float(r["y"]))
                x2, y2 = x1 + round(float(r["width"])), y1 + round(float(r["height"]))
            except (KeyError, TypeError, ValueError):
                x1 = y1 = x2 = y2 = 0
            if x2 > x1 and y2 > y1:
                label = str(el.get("label") or el.get("placeholderValue") or el.get("name") or "").strip()
                out.append({"label": label, "value": el.get("value") if isinstance(el.get("value"), str) else "",
                            "rect": (x1, y1, x2, y2), "enabled": _flag(el, "isEnabled"),
                            "declared": kind(el) not in TEXT_FIELDS})
        for child in el.get("children") or []:
            walk(child)
    walk(root)
    return out


CONTROL_KINDS = ("Button", "Keyboard", "Key", "Image", "Icon", "Switch", "Slider", "Link", "Toolbar", "NavigationBar",
                 "TabBar", "ScrollBar")


def content_texts(root, field_labels=()) -> list[str]:
    """The visible texts that can be content (a message, an answer): never a control (button, keyboard, image...),
    a text field or a declared field (mobile.field_label), nor a scroll bar's description. What the sampler reads as
    the answer (docs/pilot-improvements-2026-11-07.md E): buttons appearing after a send are not the answer."""
    return [t for t, shown in content_items(root, field_labels) if shown]


def content_items(root, field_labels=()) -> list[tuple[str, bool]]:
    """(text, shown) of every element that can be content, in the tree's order: shown = visible with a size; the
    others are labelled elements WDA reports as invisible (`isVisible=0`, no size), as the answers of a React Native
    app (07.11.2026). Only `send --read-hidden` looks at those, and reports them apart."""
    wanted = [x.casefold() for x in field_labels if x]
    out, seen = [], set()

    def walk(el):
        if not isinstance(el, dict):
            return
        k = kind(el)
        name = f"{el.get('label') or ''} {el.get('name') or ''}".casefold()
        skip = k in CONTROL_KINDS or k in TEXT_FIELDS or any(w in name for w in wanted)
        if not skip:
            text = label_of(el)
            r = el.get("rect") or {}
            try:
                sized = float(r["width"]) > 0 and float(r["height"]) > 0
            except (KeyError, TypeError, ValueError):
                sized = False
            if text and "scroll bar" not in text.casefold() and text not in seen:
                seen.add(text)
                out.append((text, sized and _flag(el, "isVisible")))
        if k not in CONTROL_KINDS:          # a button's label is never content, nor its children's
            for child in el.get("children") or []:
                walk(child)
    walk(root)
    return out


# --- acting -------------------------------------------------------------------------------------------------------

def tap(cfg: dict, x: int, y: int) -> None:
    value(cfg, "POST", f"/session/{session(cfg)}/wda/tap", {"x": x, "y": y})


def swipe(cfg: dict, x1: int, y1: int, x2: int, y2: int, ms: int) -> None:
    value(cfg, "POST", f"/session/{session(cfg)}/wda/dragfromtoforduration",
          {"fromX": x1, "fromY": y1, "toX": x2, "toY": y2, "duration": ms / 1000})


def hold(cfg: dict, x: int, y: int, ms: int) -> None:
    """A long press (touch and hold) at (x, y) for ms milliseconds."""
    value(cfg, "POST", f"/session/{session(cfg)}/wda/touchAndHold", {"x": x, "y": y, "duration": ms / 1000})


def window_size(cfg: dict) -> tuple[int, int]:
    v = value(cfg, "GET", f"/session/{session(cfg)}/window/size")
    try:
        return int(v["width"]), int(v["height"])
    except (KeyError, TypeError, ValueError):
        raise Unreachable("WDA did not give the window size") from None


def back(cfg: dict) -> None:
    """iOS has no back button: a swipe from the left edge, half way across the screen."""
    w, h = window_size(cfg)
    swipe(cfg, EDGE_X, h // 2, int(w * 0.75), h // 2, 250)


def has_keyboard(tree) -> bool:
    if isinstance(tree, dict):
        return kind(tree) == "Keyboard" or any(has_keyboard(c) for c in tree.get("children") or [])
    return False


def need_keyboard(cfg: dict) -> None:
    """A field of a React Native app is never WDA's `active element`, so "a field is focused" is read as "the
    keyboard is up". NoElement when it is not."""
    if not has_keyboard(value(cfg, "GET", "/source?format=json")):
        raise NoElement("no focused field to type into: the keyboard is not up (tap the field first, or use --field)")


def chunks(text: str, size: int = CHUNK) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def type_text(cfg: dict, text: str, focused: bool = False) -> int:
    """The text sent as keys to the focused field, in chunks; returns the number of calls. A failure after the first
    chunk is PartlyTyped (part of the text is in the field); before it, the error as it is (nothing typed). `focused`:
    the caller has just confirmed the keyboard (no second reading of the screen)."""
    if not focused:
        need_keyboard(cfg)
    parts = chunks(text)
    for i, part in enumerate(parts):
        try:
            value(cfg, "POST", f"/session/{session(cfg)}/wda/keys", {"value": list(part)})
        except (Unreachable, NoElement) as e:
            if i == 0:
                raise
            raise PartlyTyped(i, len(parts), str(e)) from None
    return len(parts)


def clear(cfg: dict) -> None:
    """Empties the focused field with backspaces (the field is not an `active element`: see need_keyboard)."""
    need_keyboard(cfg)
    value(cfg, "POST", f"/session/{session(cfg)}/wda/keys", {"value": ["\b"] * CLEAR_KEYS})


def screenshot(cfg: dict) -> bytes:
    v = value(cfg, "GET", "/screenshot")
    try:
        png = base64.b64decode(v or "")
    except (ValueError, TypeError):
        png = b""
    if not png.startswith(b"\x89PNG"):
        raise Unreachable("WDA /screenshot returned no PNG")
    return png


def launch(cfg: dict, bundle_id: str, restart: bool = False) -> None:
    if restart:
        value(cfg, "POST", f"/session/{session(cfg)}/wda/apps/terminate", {"bundleId": bundle_id})
        cfg.pop("_wda_session", None)
    session(cfg, bundle_id)


# --- the installed app --------------------------------------------------------------------------------------------

def installed_version(udid: str, bundle_id: str) -> str | None:
    """'1.0.0 (34)' from `xcrun devicectl device info apps`, or None (no UDID, no Xcode, not installed)."""
    if not udid:
        return None
    try:
        r = subprocess.run(["xcrun", "devicectl", "device", "info", "apps", "--device", udid],
                           capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in r.stdout.splitlines():
        m = re.search(re.escape(bundle_id) + r"\s+(\S+)\s+(\S+)", line)
        if m:
            return f"{m.group(1)} ({m.group(2)})"
    return None

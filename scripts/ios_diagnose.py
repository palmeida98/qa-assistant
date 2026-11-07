"""
Why an iPhone target is not ready (technique-mobile.md §1.5; docs/pilot-improvements-2026-11-07.md C): what
`device.py --platform ios check` shows when WebDriverAgent (WDA) does not answer, and `device.py --platform ios
doctor` always.

Each probe has a time limit and nothing is renewed, restarted or changed: no profile, no service, no setting. The
probes (`Probes`, replaced by fakes in the tests):

- `devicectl`: the devices Xcode sees (transport, pairing, tunnel) and the lock state of ours;
- usbmux: `idevice_id -l` (libimobiledevice), which may miss a device `devicectl` sees: never "no cable" from it alone;
- the port forward: whether something listens on the WDA port, and whether an `iproxy` process runs;
- WDA `/status`;
- when `/status` is ready, one WDA session opened and closed at once (nothing read, nothing tapped): the proof that
  the iPhone allows UI automation (`/status` answers even when it refuses it, XCTDaemonErrorDomain Code=41); a session
  that opens makes an older refusal in the WDA log history. `diagnose(session=False)` (`doctor --no-session`) skips it;
- provisioning profiles on this Mac whose app id is a WebDriverAgent runner, and their expiry;
- code-signing identities: their count and kind, never their holder's name nor anything else of the keychain;
- the accounts of Xcode > Settings > Accounts: how many, never which (they are Apple IDs);
- the end of the WDA log (`mobile.ios.wda_log`, $QA_WDA_LOG, else ~/wda_run.log): the lines of known causes.

Every finding is `confirmed` (with the source that showed it), `probable` (with the reason) or `unknown` (not
checked, or the tool is missing). `verdict()` picks the main block and the next action from them.
"""

from __future__ import annotations

import glob
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

PROBE_TIMEOUT = 15          # seconds per probe
LOG_TAIL_BYTES = 64 * 1024
LOG_ENV = "QA_WDA_LOG"
LOG_DEFAULT = "~/wda_run.log"
CONFIRMED, PROBABLE, UNKNOWN = "confirmed", "probable", "unknown"
PROFILE_DIRS = ("~/Library/MobileDevice/Provisioning Profiles", "~/Library/Developer/Xcode/UserData/Provisioning Profiles")
WDA_APP_ID = re.compile(r"(?i)(webdriveragent|xctrunner)")

# known causes in the WDA log (xcodebuild's output): (key, pattern)
LOG_RECENT_S = 15 * 60          # a cause in a log written this recently still holds, even with /status ready
LOG_CAUSES = (
    ("ui_not_authorized", re.compile(r"(?i)not authorized for performing ui testing|XCTDaemonErrorDomain Code=41")),
    ("profile_expired", re.compile(r"(?i)(provisioning )?profile .{0,120}has expired|profile has expired")),
    ("no_account", re.compile(r"(?i)no accounts?( for team)?|no account for team|sign in with your apple id")),
    ("not_trusted", re.compile(r"(?i)not (been )?trusted|untrusted developer|verify the developer app certificate|"
                               r"invalid code signature")),
    ("locked", re.compile(r"(?i)device is (passcode )?locked|unlock .{0,40}(device|iphone)|passcode protected")),
    ("not_found", re.compile(r"(?i)unable to find a destination|could not find (the )?device|device .{0,40}not "
                             r"(connected|available)")),
    ("launch_failed", re.compile(r"(?i)unable to launch|failed to launch|test runner .{0,40}(exited|crashed)")),
)


@dataclass
class Finding:
    name: str
    state: str                  # confirmed, probable, unknown
    value: str
    source: str = ""            # where it was seen (confirmed) or why it is thought (probable)
    data: dict = field(default_factory=dict)

    def line(self) -> str:
        how = {CONFIRMED: "confirmed", PROBABLE: "probable", UNKNOWN: "unknown"}[self.state]
        return f"{self.name}: {self.value} [{how}{': ' + self.source if self.source else ''}]"


# --- the probes (read only, each with a time limit) -------------------------------------------------------------

def _run(cmd: list[str], timeout: float = PROBE_TIMEOUT) -> tuple[int, str]:
    """(exit code, stdout); 127 when the tool is missing, 124 on timeout. stderr is never shown (it can be long)."""
    if not shutil.which(cmd[0]):
        return 127, ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return r.returncode, r.stdout


class Probes:
    """The real computer. Each method returns raw data or None when it could not be read."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def _devicectl_json(self, *args: str):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            code, _ = _run(["xcrun", "devicectl", *args, "--json-output", path, "--timeout", str(PROBE_TIMEOUT)])
            if code in (124, 127):
                return None
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def devicectl_devices(self):
        return self._devicectl_json("list", "devices")

    def lock_state(self, udid: str):
        return self._devicectl_json("device", "info", "lockState", "--device", udid) if udid else None

    def usbmux(self):
        code, out = _run(["idevice_id", "-l"])
        return None if code in (124, 127) else out

    def port_open(self, host: str, port: int) -> bool | None:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            return False

    def iproxy_running(self):
        code, out = _run(["pgrep", "-fl", "iproxy"])
        return None if code in (124, 127) else bool(out.strip())

    def wda_status(self):
        import device_ios as wda
        try:
            return wda.request(self.cfg, "GET", "/status", timeout=5).get("value")
        except Exception as e:          # noqa: BLE001 - any failure is "no answer", with its reason
            return {"_error": str(e)}

    def wda_session(self):
        """Whether WDA can open a session now (UI automation authorized): one session opened and closed at once,
        nothing read, nothing tapped. {"ok": True} or {"_error": the reason}."""
        import device_ios as wda
        try:
            r = wda.request(self.cfg, "POST", "/session", {"capabilities": {"alwaysMatch": {}}}, timeout=20)
            sid = r.get("sessionId") or (r.get("value") or {}).get("sessionId")
            if not sid:
                return {"_error": "no session id in the answer"}
            try:
                wda.request(self.cfg, "DELETE", f"/session/{sid}", timeout=10)
            except Exception:           # noqa: BLE001, S110 - the session was opened: that is the answer
                pass
            return {"ok": True}
        except Exception as e:          # noqa: BLE001 - the reason is the finding
            return {"_error": str(e)[:200]}

    def profiles(self):
        out = []
        for folder in PROFILE_DIRS:
            for path in glob.glob(os.path.join(os.path.expanduser(folder), "*.mobileprovision")):
                code, xml = _run(["security", "cms", "-D", "-i", path])
                if code != 0 or not xml:
                    continue
                try:
                    pl = plistlib.loads(xml.encode("utf-8"))
                except Exception:       # noqa: BLE001, S112 - an unreadable profile is skipped
                    continue
                ent = pl.get("Entitlements") or {}
                out.append({"app_id": str(ent.get("application-identifier") or ""),
                            "expires": pl.get("ExpirationDate"),
                            "team": (pl.get("TeamIdentifier") or [""])[0]})
        return out

    def identities(self):
        code, out = _run(["security", "find-identity", "-v", "-p", "codesigning"])
        return None if code in (124, 127) else out

    def xcode_accounts(self) -> int | None:
        """How many accounts Xcode > Settings > Accounts holds: the entries of its account list, counted and never
        shown (they are Apple IDs). The team cache (IDEProvisioningTeamByIdentifier) is not used: it stays empty with
        an account signed in (seen 07.11.2026)."""
        code, out = _run(["defaults", "read", "com.apple.dt.Xcode", "DVTDeveloperAccountManagerAppleIDLists"])
        if code in (124, 127):
            return None
        return len(re.findall(r"(?m)^\s*identifier\s*=", out)) if code == 0 else 0

    def wda_log(self):
        path = os.path.expanduser(os.environ.get(LOG_ENV, "").strip() or self.cfg.get("wda_log") or LOG_DEFAULT)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - LOG_TAIL_BYTES))
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return None
        return {"path": path, "text": text, "mtime": os.path.getmtime(path)}


# --- reading the probes into findings ----------------------------------------------------------------------------

def _ours(devices: list[dict], udid: str) -> dict | None:
    """Our device in devicectl's list: by UDID when it is configured, else the only iPhone."""
    phones = [d for d in devices if str((d.get("hardwareProperties") or {}).get("deviceType") or "iPhone")
              .lower().startswith("iphone")]
    if udid:
        return next((d for d in devices if (d.get("hardwareProperties") or {}).get("udid") == udid
                     or d.get("identifier") == udid), None)
    return phones[0] if len(phones) == 1 else None


def _short(udid: str) -> str:
    return f"…{udid[-6:]}" if udid else "?"


def _expires(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def diagnose(cfg: dict, probes: Probes | None = None, now: datetime | None = None,
             session: bool = True) -> list[Finding]:
    """The findings. `session=False` (doctor --no-session): strictly passive, the session probe is not tried."""
    p = probes or Probes(cfg)
    now = now or datetime.now(UTC)
    udid = str(cfg.get("serial") or "")
    out: list[Finding] = []

    # devicectl: detection, transport, pairing
    raw = p.devicectl_devices()
    devices = ((raw or {}).get("result") or {}).get("devices") if isinstance(raw, dict) else None
    ours = _ours(devices or [], udid) if devices is not None else None
    if devices is None:
        out.append(Finding("devicectl", UNKNOWN, "not read", "xcrun devicectl missing or did not answer"))
    elif ours is None:
        out.append(Finding("devicectl", CONFIRMED, f"device {_short(udid)} not listed ({len(devices)} device(s))",
                           "xcrun devicectl list devices"))
    else:
        conn = ours.get("connectionProperties") or {}
        transport = conn.get("transportType") or "unknown transport"
        pairing = conn.get("pairingState") or "unknown"
        tunnel = conn.get("tunnelState") or ""
        model = (ours.get("hardwareProperties") or {}).get("marketingName") or "iPhone"
        os_v = (ours.get("deviceProperties") or {}).get("osVersionNumber") or "?"
        if not udid:
            udid = (ours.get("hardwareProperties") or {}).get("udid") or ""
        out.append(Finding("devicectl", CONFIRMED, f"{model} iOS {os_v} seen, transport {transport}, "
                           f"pairing {pairing}" + (f", tunnel {tunnel}" if tunnel else ""),
                           "xcrun devicectl list devices",
                           {"transport": transport, "pairing": pairing, "tunnel": tunnel}))

    # lock state
    lock = p.lock_state(udid) if ours is not None else None
    res = (lock or {}).get("result") if isinstance(lock, dict) else None
    if isinstance(res, dict) and "passcodeRequired" in res:
        locked = bool(res.get("passcodeRequired"))
        out.append(Finding("lock", CONFIRMED, "locked (passcode required)" if locked else "unlocked",
                           "xcrun devicectl device info lockState", {"locked": locked}))
    else:
        out.append(Finding("lock", UNKNOWN, "not read", "the device is not seen by devicectl, or it did not answer"))

    # usbmux
    mux = p.usbmux()
    if mux is None:
        out.append(Finding("usbmux", UNKNOWN, "not read", "idevice_id missing or did not answer"))
    else:
        listed = [x.split()[0] for x in mux.splitlines() if x.strip()]
        seen = bool(udid and any(x == udid for x in listed)) or (not udid and bool(listed))
        note = "" if seen else (" (devicectl sees it: usbmux may list it late or not at all; not a cable problem by "
                                "itself)" if ours is not None else "")
        out.append(Finding("usbmux", CONFIRMED, ("lists the device" if seen else "does not list the device") + note,
                           "idevice_id -l", {"seen": seen}))

    # port forward
    url = urlparse(str(cfg.get("wda_url") or "http://localhost:8100"))
    host, port = url.hostname or "localhost", url.port or 8100
    is_open = p.port_open(host, port)
    iproxy = p.iproxy_running()
    local = host in ("localhost", "127.0.0.1", "::1")
    if is_open:
        out.append(Finding("port", CONFIRMED, f"{host}:{port} accepts connections", "TCP connect", {"open": True}))
    elif local and iproxy is False:
        out.append(Finding("port", CONFIRMED, f"nothing listens on {host}:{port} and no iproxy runs",
                           "TCP connect, pgrep iproxy", {"open": False, "iproxy": False}))
    else:
        out.append(Finding("port", CONFIRMED, f"nothing listens on {host}:{port}"
                           + (" (an iproxy process runs)" if iproxy else ""), "TCP connect",
                           {"open": False, "iproxy": iproxy}))

    # WDA
    st = p.wda_status()
    if isinstance(st, dict) and "_error" not in st:
        ready = st.get("ready", True)
        out.append(Finding("wda", CONFIRMED, "answers, ready" if ready else f"answers, NOT ready "
                           f"({st.get('message', 'no message')})", "GET /status", {"ready": bool(ready)}))
    else:
        out.append(Finding("wda", CONFIRMED, "does not answer", "GET /status", {"ready": False}))

    # UI automation: can a session open NOW (the only proof; /status answers even when it cannot)
    if not session:
        out.append(Finding("automation", UNKNOWN, "not tried (--no-session)",
                           "a passive report: /status alone does not prove a session can open"))
    elif out[-1].data.get("ready"):
        ses = p.wda_session()
        if isinstance(ses, dict) and ses.get("ok"):
            out.append(Finding("automation", CONFIRMED, "a session opens (UI automation authorized now)",
                               "POST /session, closed at once", {"ok": True}))
        else:
            reason = str((ses or {}).get("_error") or "no answer")
            refused = bool(re.search(r"(?i)not authorized for performing ui testing|Code=41", reason))
            out.append(Finding("automation", CONFIRMED, ("REFUSED: " if refused else "no session: ") + reason[:120],
                               "POST /session", {"ok": False, "refused": refused}))
    else:
        out.append(Finding("automation", UNKNOWN, "not tried", "WebDriverAgent does not answer or is not ready"))

    # provisioning profiles of a WDA runner
    profiles = p.profiles()
    wda_profiles = [x for x in (profiles or []) if WDA_APP_ID.search(x.get("app_id") or "") or
                    (x.get("app_id") or "").endswith(".*")]
    dated = [(e, x) for x in wda_profiles if (e := _expires(x.get("expires")))]
    if profiles is None:
        out.append(Finding("profile", UNKNOWN, "not read", "the profiles folder could not be read"))
    elif not dated:
        out.append(Finding("profile", PROBABLE, "no WebDriverAgent profile on this Mac",
                           f"{len(profiles)} profile(s) read, none for a WebDriverAgent runner or a wildcard"))
    else:
        latest, _ = max(dated, key=lambda t: t[0])
        state = "valid until" if latest > now else "EXPIRED on"
        out.append(Finding("profile", CONFIRMED, f"{state} {latest.astimezone().strftime('%Y-%m-%d %H:%M')}",
                           "Provisioning Profiles on this Mac (the latest WebDriverAgent one)",
                           {"expired": latest <= now, "expires": latest.isoformat()}))

    # signing identities: count and kind only
    ids = p.identities()
    if ids is None:
        out.append(Finding("certificate", UNKNOWN, "not read", "security missing or did not answer"))
    else:
        m = re.search(r"(\d+) valid identit", ids)
        count = int(m.group(1)) if m else 0
        kinds = sorted({k for k in re.findall(r'"(Apple Development|iPhone Developer|Apple Distribution)', ids)})
        out.append(Finding("certificate", CONFIRMED, f"{count} code-signing identit{'y' if count == 1 else 'ies'}"
                           + (f" ({', '.join(kinds)})" if kinds else ""), "security find-identity -p codesigning",
                           {"count": count}))

    # Xcode accounts: counted, never shown (Apple IDs)
    accounts = p.xcode_accounts()
    if accounts is None:
        out.append(Finding("account", UNKNOWN, "not read", "defaults missing or did not answer"))
    elif accounts:
        out.append(Finding("account", PROBABLE, f"Xcode has {accounts} account(s)",
                           "Xcode's account list, counted (an account may still need its password again)",
                           {"accounts": accounts}))
    else:
        out.append(Finding("account", PROBABLE, "Xcode has no account",
                           "Xcode's account list is empty: Xcode > Settings > Accounts", {"accounts": 0}))

    # the WDA log
    log = p.wda_log()
    if log is None:
        out.append(Finding("wda_log", UNKNOWN, "no log read", f"set mobile.ios.wda_log or ${LOG_ENV}"))
    else:
        hits = {}
        for line in log["text"].splitlines():
            for key, rx in LOG_CAUSES:
                if rx.search(line):
                    hits[key] = line.strip()[:200]
        recent_log = now.timestamp() - log["mtime"] <= LOG_RECENT_S
        when = datetime.fromtimestamp(log["mtime"]).strftime("%Y-%m-%d %H:%M")
        if hits:
            out.append(Finding("wda_log", CONFIRMED, "; ".join(sorted(hits)), f"{log['path']} (written {when})",
                               {"causes": hits, "mtime": log["mtime"], "recent": recent_log}))
        else:
            out.append(Finding("wda_log", CONFIRMED, "no known cause in the last lines", f"{log['path']} ({when})",
                               {"causes": {}, "mtime": log["mtime"], "recent": recent_log}))
    return out


# --- the verdict -------------------------------------------------------------------------------------------------

ACTIONS = {
    "not_detected": "check the USB connection and the cable, unlock the iPhone and answer «Trust this computer»",
    "not_paired": "unlock the iPhone and answer «Trust this computer» (pairing), then repeat",
    "locked": "unlock the iPhone (it is locked: WebDriverAgent cannot start or bring the app forward), then repeat",
    "profile_expired": "renew the WebDriverAgent signature: Xcode > Settings > Accounts with the Apple ID, then "
                       "xcodebuild build-for-testing -allowProvisioningUpdates (technique-mobile.md §1.5); the "
                       "assistant does not do this",
    "no_account": "add the Apple ID in Xcode > Settings > Accounts (a person's step), then renew the signature",
    "not_trusted": "on the iPhone: Settings > General > VPN & Device Management > trust the developer certificate",
    "no_certificate": "Xcode has no code-signing identity: sign in to the account in Xcode and let it create one",
    "no_forward": "start the port forward: iproxy 8100 8100 (or the port of mobile.ios.wda_url)",
    "wda_down": "start WebDriverAgent on the iPhone (xcodebuild test-without-building … -destination id=<udid>)",
    "wda_not_ready": "WebDriverAgent answers but is not ready: wait a few seconds, or restart it",
    "ui_not_authorized": "on the iPhone (the person's step): unlock it, check Settings > Developer > Enable UI "
                         "Automation is on and answer any prompt to allow it; then restart WebDriverAgent and repeat",
    "launch_failed": "WebDriverAgent did not launch on the iPhone: read the WDA log's last lines, then start it again",
    "ready": "nothing: the target answers",
}


def verdict(findings: list[Finding]) -> tuple[str, str, str]:
    """(key, the main block in one sentence, the next action). The most basic block first: a device not seen comes
    before a profile, a profile before the port."""
    f = {x.name: x for x in findings}

    def get(name):
        return f.get(name) or Finding(name, UNKNOWN, "")
    dev, lock, mux, port, wda, prof, cert, acct, log = (get(n) for n in (
        "devicectl", "lock", "usbmux", "port", "wda", "profile", "certificate", "account", "wda_log"))
    causes = (log.data or {}).get("causes") or {}
    auto = f.get("automation") or Finding("automation", UNKNOWN, "")
    if auto.state == CONFIRMED and auto.data.get("refused"):
        refusal = "the iPhone refuses UI automation to WebDriverAgent (POST /session now); /status still answers"
        return "ui_not_authorized", refusal, ACTIONS["ui_not_authorized"]
    if auto.state == CONFIRMED and auto.data.get("ok"):
        old = " (an earlier refusal in the WDA log is history: a session opened now)" if "ui_not_authorized" in \
            causes else ""
        return "ready", "WebDriverAgent answers and a session opens" + old, ACTIONS["ready"]
    recent = log.state == CONFIRMED and (log.data or {}).get("recent")
    if "ui_not_authorized" in causes and recent:
        block = (f"the iPhone probably refuses UI automation ({log.source}; no session could be tried now)")
        return "ui_not_authorized", block, ACTIONS["ui_not_authorized"]
    if auto.state == CONFIRMED and not auto.data.get("ok"):
        return "wda_not_ready", f"WebDriverAgent answers but no session opens ({auto.value})", ACTIONS["wda_not_ready"]
    if wda.state == CONFIRMED and wda.data.get("ready"):
        return "ready", "WebDriverAgent answers and is ready", ACTIONS["ready"]
    seen_by_devicectl = dev.state == CONFIRMED and "seen" in dev.value
    if dev.state == CONFIRMED and not seen_by_devicectl and not (mux.data or {}).get("seen"):
        return ("not_detected", "the iPhone is not detected (neither devicectl nor usbmux list it)",
                ACTIONS["not_detected"])
    if seen_by_devicectl and str(dev.data.get("pairing", "")).lower() not in ("paired", "unknown", ""):
        return "not_paired", f"the iPhone is not paired with this Mac ({dev.data.get('pairing')})", ACTIONS["not_paired"]
    if lock.data.get("locked") or "locked" in causes:
        src = lock.source if lock.data.get("locked") else log.source
        return "locked", f"the iPhone is locked (confirmed: {src})", ACTIONS["locked"]
    if prof.data.get("expired") or "profile_expired" in causes:
        src = prof.source if prof.data.get("expired") else log.source
        return "profile_expired", f"the WebDriverAgent provisioning profile has expired ({src})", ACTIONS["profile_expired"]
    if "no_account" in causes:
        return "no_account", f"Xcode has no account for the signing team ({log.source})", ACTIONS["no_account"]
    if acct.state != UNKNOWN and acct.data.get("accounts") == 0 and prof.state != CONFIRMED:
        return "no_account", "probably no account in Xcode (its account list is empty, no valid WebDriverAgent profile)", \
            ACTIONS["no_account"]
    if "not_trusted" in causes:
        return "not_trusted", f"the developer certificate is not trusted on the iPhone ({log.source})", \
            ACTIONS["not_trusted"]
    if cert.state == CONFIRMED and cert.data.get("count") == 0:
        return "no_certificate", "no code-signing identity on this Mac", ACTIONS["no_certificate"]
    if port.state == CONFIRMED and port.data.get("open") is False and port.data.get("iproxy") is False:
        return "no_forward", "no port forward to the iPhone (nothing listens, no iproxy)", ACTIONS["no_forward"]
    if "launch_failed" in causes:
        return "launch_failed", f"WebDriverAgent failed to launch ({log.source})", ACTIONS["launch_failed"]
    if wda.state == CONFIRMED and "NOT ready" in wda.value:
        return "wda_not_ready", "WebDriverAgent answers but is not ready", ACTIONS["wda_not_ready"]
    return "wda_down", "WebDriverAgent does not answer (the device is seen; no other cause found)", ACTIONS["wda_down"]


def render(findings: list[Finding], detail: bool = True) -> list[str]:
    key, block, action = verdict(findings)
    lines = [f"blocked by: {block}" if key != "ready" else f"diagnosis: {block}", f"next: {action}"]
    if detail:
        lines.append("details (confirmed = seen now; probable = inferred; unknown = not checked):")
        lines += [f"  - {x.line()}" for x in findings]
    return lines


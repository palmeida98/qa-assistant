"""
Prepares a reproducible Android emulator for the workspace's app (technique-mobile.md §1.1 and §3; plan, task 9.2).

    python scripts/setup_emulator.py <workspace> [--avd NAME] [--image system-images;android-35;google_apis;x86_64]
        [--apk PATH] [--no-boot] [--no-audio] [--cold] [--gpu MODE] [--headless] [--boot-timeout 300]
        [--allow-prod] [--dry-run]

Idempotent steps, each printed as `[ok|done|skip|fail] <step>: <detail>` (`[dry-run]` with --dry-run):
  1. sdk          emulator and adb (ANDROID_HOME, ANDROID_SDK_ROOT, the default SDK folder, then the PATH); the
                  system image must already be installed (sdkmanager is never run: the command is printed)
  2. avd          `emulator -list-avds`; `avdmanager create avd -n NAME -k IMAGE -d pixel_7` only when missing
  3. boot         starts the AVD detached (unless it is running or --no-boot) and waits for sys.boot_completed;
                  COLD (-no-snapshot-load) with --cold, or when the AVD's saved snapshot is older than the build
                  (the --apk file, or the last install this script recorded in <avd>/qa-build.txt): a stale
                  snapshot brought back an old build once (plan 11.1). --gpu (e.g. swiftshader_indirect on a runner
                  without a GPU) and --headless (-no-window) are passed to the emulator
  4. build        --apk: SHA-256 checked against mobile.build.sha256 (a mismatch is never installed);
                  `adb install -r` only when the app is missing or its versionCode differs (read from the APK's
                  binary manifest in pure Python, scripts/apk_manifest.py; aapt2 only if that fails)
  5. native-libs  ARM-only APK on an x86_64 image with native translation and uncompressed libraries
                  (extractNativeLibs=false): copies lib/arm64-v8a/*.so into the app's native library folder
                  (root image only); repeat after each reinstall. The APK is never changed.
  6. permissions  pm grant / pm revoke for mobile.permissions.grant / .revoke
  7. browser      Chrome's first run off (magic-link logins, technique §1.1); root image only

- project.yaml, mobile: section: app_id (install, permissions, libraries), avd (default: qa-<project>), image
  (default below), build: {source, sha256} (where the build comes from; the APK is never committed),
  permissions: {grant: [...], revoke: [...]}. --avd and --image override avd and image.
- Refuses a production build (technique §3: stop and ask): mobile.build.source or the APK's file name containing
  "prod" or "release-store", unless --allow-prod.
- --dry-run prints every step and the commands it would run, without running any (only local checks: the SDK
  folders, the APK's SHA-256 and its libraries).
- Every external program goes through run() and spawn() (the tests replace them); argument lists, never a shell.

Exit codes:
  0  ok
  2  configuration or usage (SDK or system image missing, mobile.app_id missing, SHA-256 mismatch, production build)
  3  the target did not boot in time, or a step failed on the device
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from apk_manifest import ManifestError, read_manifest
from common import read_project

DEFAULT_IMAGE = "system-images;android-35;google_apis;x86_64"
DEVICE_PROFILE = "pixel_7"
BOOT_TIMEOUT_S, POLL_S = 300, 5
PROD = re.compile(r"prod|release-store", re.IGNORECASE)
SHA256 = re.compile(r"[0-9a-fA-F]{64}")
LIB_NAME = re.compile(r"[A-Za-z0-9._+-]+\.so")
TMP_LIBS = "/data/local/tmp/qa-native-libs"
CHROME = "com.android.chrome"
CHROME_FLAGS = "_ --disable-fre --no-default-browser-check --no-first-run"
CHROME_FILE = "/data/local/tmp/chrome-command-line"
EXE = ".exe" if os.name == "nt" else ""
BAT = ".bat" if os.name == "nt" else ""


class ConfigError(Exception):
    """Configuration or usage (code 2)."""


class TargetError(Exception):
    """The target did not boot or a step failed on the device (code 3)."""


# --- the doors to the outside (the tests replace them) ---------------------------------------------------------

def run(cmd: list[str], timeout: int = 120, stdin: str | None = None) -> tuple[int, str]:
    """Runs a program and returns (exit code, stdout + stderr as text)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           input=stdin, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"no answer in {timeout} s"
    except OSError as e:
        return 127, str(e)
    return r.returncode, (r.stdout + r.stderr).replace("\r\n", "\n")


def spawn(cmd: list[str], log: str | None = None):
    """Starts a program detached from this one (the emulator keeps running after the script ends); its output goes
    to `log` when given (read back when it exits while starting), otherwise nowhere."""
    kw = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
          if os.name == "nt" else {"start_new_session": True})
    if not log:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **kw)
    with open(log, "wb") as out:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kw)


def log_tail(path: str, lines: int = 25) -> str:
    """The last lines of the emulator's output (it holds no secret: the emulator's own messages)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tail = [ln.rstrip() for ln in f.read().splitlines() if ln.strip()][-lines:]
    except OSError:
        return ""
    return "\n".join(tail)


def now() -> float:
    return time.monotonic()


def pause(s: float) -> None:
    time.sleep(s)


# --- local checks (no external program) ------------------------------------------------------------------------

def sdk_roots() -> list[str]:
    roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        roots.append(os.path.join(os.environ["LOCALAPPDATA"], "Android", "Sdk"))
    roots += [os.path.expanduser(os.path.join("~", "Android", "Sdk")),
              os.path.expanduser(os.path.join("~", "Library", "Android", "sdk"))]
    out = []
    for r in roots:
        if r and os.path.isdir(r) and os.path.normcase(os.path.abspath(r)) not in map(os.path.normcase, out):
            out.append(os.path.abspath(r))
    return out


def find_tools() -> dict:
    """emulator, adb, avdmanager, aapt2 (None when not found) and the SDK folder."""
    found = {"emulator": None, "adb": None, "avdmanager": None, "aapt2": None, "sdk": None}
    for root in sdk_roots():
        candidates = {
            "emulator": [os.path.join(root, "emulator", "emulator" + EXE)],
            "adb": [os.path.join(root, "platform-tools", "adb" + EXE)],
            "avdmanager": [os.path.join(root, "cmdline-tools", "latest", "bin", "avdmanager" + BAT),
                           *sorted(glob.glob(os.path.join(root, "cmdline-tools", "*", "bin", "avdmanager" + BAT))),
                           os.path.join(root, "tools", "bin", "avdmanager" + BAT)],
            "aapt2": sorted(glob.glob(os.path.join(root, "build-tools", "*", "aapt2" + EXE)), reverse=True),
        }
        for tool, paths in candidates.items():
            if not found[tool]:
                found[tool] = next((p for p in paths if os.path.isfile(p)), None)
        if found["emulator"] and found["adb"] and not found["sdk"]:
            found["sdk"] = root
    for tool in ("emulator", "adb", "avdmanager", "aapt2"):
        found[tool] = found[tool] or shutil.which(tool)
    if not found["sdk"] and found["emulator"]:
        found["sdk"] = os.path.dirname(os.path.dirname(os.path.abspath(found["emulator"])))
    return found


def avd_home() -> str:
    """Where the AVDs live: ANDROID_AVD_HOME, else ANDROID_USER_HOME/avd, else ~/.android/avd."""
    if os.environ.get("ANDROID_AVD_HOME"):
        return os.environ["ANDROID_AVD_HOME"]
    if os.environ.get("ANDROID_USER_HOME"):
        return os.path.join(os.environ["ANDROID_USER_HOME"], "avd")
    return os.path.expanduser(os.path.join("~", ".android", "avd"))


def avd_ini_dirs() -> list[str]:
    """Where an avdmanager may have written <name>.ini: avd_home() first, then the places recent cmdline-tools use
    (XDG_CONFIG_HOME, set on GitHub's runners; ANDROID_USER_HOME), which the emulator does not search."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser(os.path.join("~", ".config"))
    dirs = [avd_home(), os.path.join(xdg, ".android", "avd"), os.path.expanduser(os.path.join("~", ".android", "avd"))]
    if os.environ.get("ANDROID_USER_HOME"):
        dirs.insert(1, os.path.join(os.environ["ANDROID_USER_HOME"], "avd"))
    return list(dict.fromkeys(dirs))


def mtime(path: str) -> float | None:
    return os.path.getmtime(path) if os.path.exists(path) else None


def image_installed(sdk: str | None, image: str) -> bool:
    return bool(sdk) and os.path.isdir(os.path.join(sdk, *image.split(";")))


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def native_libs(apk: str) -> dict[str, list[zipfile.ZipInfo]]:
    """The APK's native libraries by ABI: {"arm64-v8a": [ZipInfo, ...], ...} (only lib/<abi>/<name>.so)."""
    out: dict[str, list[zipfile.ZipInfo]] = {}
    with zipfile.ZipFile(apk) as z:
        for info in z.infolist():
            parts = info.filename.split("/")
            if len(parts) == 3 and parts[0] == "lib" and parts[2].endswith(".so"):
                out.setdefault(parts[1], []).append(info)
    return out


def arm_only_reason(apk: str) -> str | None:
    """None when the APK is ARM-only with uncompressed libraries (the case of technique §1.1); else why not."""
    libs = native_libs(apk)
    if "x86_64" in libs:
        return "the APK has lib/x86_64/ (no translation needed)"
    if "arm64-v8a" not in libs:
        return "the APK has no lib/arm64-v8a/"
    compressed = [i.filename for i in libs["arm64-v8a"] if i.compress_type != zipfile.ZIP_STORED]
    if compressed:
        return f"the APK's native libraries are compressed (Android extracts them), e.g. {compressed[0]}"
    bad = [i.filename for i in libs["arm64-v8a"] if not LIB_NAME.fullmatch(i.filename.split("/")[2])]
    if bad:
        return f"unexpected library name {bad[0]!r}: not copied"
    return None


def sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# --- the setup ---------------------------------------------------------------------------------------------------

class Setup:
    def __init__(self, a, mobile: dict, project: str):
        build = mobile.get("build") or {}
        perms = mobile.get("permissions") or {}
        self.a = a
        self.dry = a.dry_run
        self.app_id = str(mobile.get("app_id") or "")
        self.avd = a.avd or str(mobile.get("avd") or "") or ("qa-" + (re.sub(r"[^A-Za-z0-9._-]+", "-", project)
                                                                     .strip("-") or "emulator"))
        self.image = a.image or str(mobile.get("image") or "") or DEFAULT_IMAGE
        self.source = str(build.get("source") or "")
        self.sha256 = str(build.get("sha256") or "").lower()
        self.grant = [str(p) for p in perms.get("grant") or []]
        self.revoke = [str(p) for p in perms.get("revoke") or []]
        self.tools: dict = {}
        self.serial: str | None = None
        self.root: bool | None = None
        self.root_reason = ""
        self.current = "configuration"

    # output and device helpers

    @staticmethod
    def say(tag: str, step: str, detail: str) -> None:
        print(f"[{tag}] {step}: {detail}")

    def plan(self, step: str, detail: str, *cmds: list[str]) -> None:
        self.say("dry-run", step, detail)
        for c in cmds:
            print("    $ " + " ".join(c))

    def adb(self, *args: str, timeout: int = 120) -> tuple[int, str]:
        return run([self.tools["adb"], *(["-s", self.serial] if self.serial else []), *args], timeout=timeout)

    def shell(self, command: str, timeout: int = 120) -> str:
        """`adb shell <command>` (one string, for the device's sh); a failure is a TargetError."""
        rc, out = self.adb("shell", command, timeout=timeout)
        if rc != 0:
            raise TargetError(f"`{command}` failed on {self.serial}: {out.strip()[:200]}")
        return out

    def prop(self, name: str) -> str:
        return self.adb("shell", "getprop", name)[1].strip()

    def package_dump(self) -> str:
        out = self.adb("shell", "dumpsys", "package", self.app_id)[1]
        return out if f"Package [{self.app_id}]" in out else ""

    # 0. production builds

    def refuse_prod(self) -> None:
        names = [("mobile.build.source", self.source)]
        if self.a.apk:
            names.append(("the APK's file name", os.path.basename(self.a.apk)))
        hits = [f"{what} ({value!r})" for what, value in names if value and PROD.search(value)]
        if hits and not self.a.allow_prod:
            raise ConfigError(f"production build? {', '.join(hits)} contains 'prod' or 'release-store': stop and ask "
                              "(technique-mobile.md §3); --allow-prod only with the user's explicit approval")

    # 1. SDK

    def step_sdk(self) -> None:
        self.tools = find_tools()
        missing = [t for t in ("emulator", "adb") if not self.tools[t]]
        default = r"%LOCALAPPDATA%\Android\Sdk" if os.name == "nt" else "~/Android/Sdk"
        if missing:
            raise ConfigError(f"{' and '.join(missing)} not found (ANDROID_HOME, ANDROID_SDK_ROOT, {default}, PATH): "
                              "install Android Studio or the command-line tools, then "
                              "`sdkmanager \"platform-tools\" \"emulator\"`, and set ANDROID_HOME")
        self.say("ok", "sdk", f"emulator {self.tools['emulator']} · adb {self.tools['adb']}"
                 + ("" if self.tools["aapt2"] else " · aapt2 not found (build-tools)"))

    # 2. AVD

    def step_avd(self) -> None:
        create = [self.tools["avdmanager"] or "avdmanager", "create", "avd", "-n", self.avd, "-k", self.image,
                  "-d", DEVICE_PROFILE]
        if self.dry:
            self.plan("avd", f"{self.avd} ({self.image}), created only if missing",
                      [self.tools["emulator"], "-list-avds"], create)
            return
        rc, out = run([self.tools["emulator"], "-list-avds"])
        avds = [ln.strip() for ln in out.splitlines() if re.fullmatch(r"[A-Za-z0-9._-]+", ln.strip())]
        if self.avd in avds:
            self.say("ok", "avd", f"{self.avd} exists")
            return
        if not image_installed(self.tools["sdk"], self.image):
            raise ConfigError(f"system image {self.image} is not installed in {self.tools['sdk']}: install it "
                              f"(sdkmanager \"{self.image}\", or Android Studio > SDK Manager) and run again")
        if not self.tools["avdmanager"]:
            raise ConfigError("avdmanager not found (the SDK's cmdline-tools): install the Android SDK "
                              "Command-line Tools, or create the AVD in Android Studio > Device Manager "
                              f"with the name {self.avd}")
        rc, out = run(create, stdin="no\n", timeout=300)
        if rc != 0:
            raise ConfigError(f"avdmanager could not create {self.avd}: {out.strip()[-300:]}")
        found = next((d for d in avd_ini_dirs() if os.path.isfile(os.path.join(d, f"{self.avd}.ini"))), None)
        if not found:
            found = self.write_avd_ini()
        if found and found != avd_home():
            # the emulator finds it only through ANDROID_AVD_HOME (11.8 pilot: "Unknown AVD name" on a runner)
            os.environ["ANDROID_AVD_HOME"] = found
        self.say("done", "avd", f"{self.avd} created ({self.image}, {DEVICE_PROFILE})"
                 + (f" in {found}" if found else ""))

    def write_avd_ini(self) -> str | None:
        """Last resort when no <name>.ini is where the emulator looks: ask avdmanager where the AVD is (its `Path:`)
        and write the .ini in avd_home() (the emulator only needs `path=`). Returns that folder, or None."""
        rc, out = run([self.tools["avdmanager"], "list", "avd"], timeout=120)
        blocks = re.split(r"-{5,}", out or "")
        path = next((m.group(1).strip() for b in blocks if re.search(rf"Name:\s*{re.escape(self.avd)}\s*$", b, re.MULTILINE)
                     for m in [re.search(r"Path:\s*(.+)", b)] if m), None)
        if rc != 0 or not path:
            return None
        os.makedirs(avd_home(), exist_ok=True)
        with open(os.path.join(avd_home(), f"{self.avd}.ini"), "w", encoding="utf-8") as f:
            f.write(f"avd.ini.encoding=UTF-8\npath={path}\n")
        return avd_home()

    # 3. boot

    def running_serial(self) -> tuple[str | None, bool]:
        """(serial of the emulator running this AVD, another emulator running?)."""
        _, out = run([self.tools["adb"], "devices"])
        emulators = [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().startswith("emulator-")]
        for s in emulators:
            _, name = run([self.tools["adb"], "-s", s, "emu", "avd", "name"], timeout=20)
            first = name.strip().splitlines()[0].strip() if name.strip() else ""
            if first != self.avd:
                _, first = run([self.tools["adb"], "-s", s, "shell", "getprop", "ro.boot.qemu.avd_name"], timeout=20)
            if first.strip() == self.avd:
                return s, True
        return None, bool(emulators)

    def booted(self) -> bool:
        return self.prop("sys.boot_completed") == "1"

    def avd_dir(self) -> str:
        return os.path.join(avd_home(), f"{self.avd}.avd")

    def cold_reason(self) -> str | None:
        """Why the boot must be cold, or None: --cold, or a saved snapshot older than the build (the --apk file or
        the last install recorded by this script)."""
        if self.a.cold:
            return "--cold"
        snapshot = mtime(os.path.join(self.avd_dir(), "snapshots", "default_boot"))
        if snapshot is None:
            return None
        builds = [t for t in (mtime(self.a.apk) if self.a.apk else None,
                              mtime(os.path.join(self.avd_dir(), "qa-build.txt"))) if t is not None]
        if builds and snapshot < max(builds):
            return "the saved snapshot is older than the build (it may bring back an old build)"
        return None

    def start_command(self, cold: bool) -> list[str]:
        return [self.tools["emulator"], "-avd", self.avd, "-no-snapshot-save", "-no-boot-anim",
                *(["-no-snapshot-load"] if cold else []), *(["-no-audio"] if self.a.no_audio else []),
                *(["-gpu", self.a.gpu] if self.a.gpu else []), *(["-no-window"] if self.a.headless else [])]

    def step_boot(self) -> None:
        cold = self.cold_reason()
        start = self.start_command(bool(cold))
        if self.dry:
            if self.a.no_boot:
                self.plan("boot", "--no-boot: only an emulator already running this AVD is used",
                          [self.tools["adb"], "devices"])
            else:
                self.plan("boot", f"started detached unless running; waits up to {self.a.boot_timeout} s for "
                          "sys.boot_completed = 1", [self.tools["adb"], "devices"], start,
                          [self.tools["adb"], "-s", "<emulator>", "shell", "getprop", "sys.boot_completed"])
            return
        self.serial, _ = self.running_serial()
        if self.serial and self.booted():
            self.say("ok", "boot", f"{self.avd} running as {self.serial}")
            return
        if self.a.no_boot:
            self.say("skip", "boot", f"--no-boot and {self.avd} is not running: the device steps are skipped")
            self.serial = None
            return
        started = False
        log = os.path.join(tempfile.gettempdir(), f"emulator-{self.avd}.log")
        if not self.serial:
            proc = spawn(start, log)
            started = True
        begin = now()
        while now() - begin < self.a.boot_timeout:
            if started and proc.poll() not in (None, 0):
                tail = log_tail(log)
                raise TargetError(f"the emulator exited (code {proc.poll()}) while starting {self.avd}"
                                  + (f"; its last output ({log}):\n{tail}" if tail else
                                     f": run `{' '.join(start)}` by hand to see why"))
            if not self.serial:
                self.serial, _ = self.running_serial()
            if self.serial and self.booted():
                self.say("done", "boot", f"{self.avd} booted as {self.serial} in {now() - begin:.0f} s"
                         + (f" (cold: {cold})" if cold and started else ""))
                return
            pause(POLL_S)
        raise TargetError(f"{self.avd} did not finish booting in {self.a.boot_timeout} s "
                          "(sys.boot_completed never 1): check the emulator window, or HAXM/WHPX/KVM acceleration")

    # 4. build

    def apk_version(self, use_aapt2: bool = True) -> tuple[str | None, str | None]:
        """(package, versionCode) from the APK's binary manifest (pure Python); aapt2 dump badging only if that
        fails; (None, None) when neither can tell."""
        try:
            m = read_manifest(self.a.apk)
            if m["version_code"] is not None:
                return m["package"], str(m["version_code"])
        except ManifestError:
            pass
        if not use_aapt2 or not self.tools.get("aapt2"):
            return None, None
        rc, out = run([self.tools["aapt2"], "dump", "badging", self.a.apk])
        m = re.search(r"package: name='([^']+)' versionCode='(\d+)'", out)
        return (m.group(1), m.group(2)) if rc == 0 and m else (None, None)

    def check_apk(self) -> None:
        """The SHA-256 and the package, before anything is installed (also in the dry run)."""
        if not self.a.apk:
            return
        if not os.path.isfile(self.a.apk):
            raise ConfigError(f"--apk {self.a.apk}: the file does not exist")
        digest = sha256_of(self.a.apk)
        if self.sha256 and digest != self.sha256:
            raise ConfigError(f"SHA-256 of {os.path.basename(self.a.apk)} is {digest}, mobile.build.sha256 is "
                              f"{self.sha256}: not the build recorded in project.yaml, nothing installed")
        self.say("ok" if self.sha256 else "skip", "sha256",
                 "matches mobile.build.sha256" if self.sha256 else
                 f"mobile.build.sha256 not set: the build is not checked (its SHA-256 is {digest}; record it)")

    def step_build(self) -> None:
        if not self.a.apk:
            self.say("skip", "build", "no --apk: nothing to install")
            return
        install = [self.tools["adb"], "-s", self.serial or "<emulator>", "install", "-r", self.a.apk]
        if self.dry:
            package, code = self.apk_version(use_aapt2=False)
            self.plan("build", f"{package or 'the app'} versionCode {code or 'unknown'}: installed only when missing "
                      "or with another versionCode", install)
            return
        if not self.serial:
            self.say("skip", "build", "no running emulator")
            return
        package, code = self.apk_version()
        if package and package != self.app_id:
            raise ConfigError(f"the APK is {package}, mobile.app_id is {self.app_id}")
        m = re.search(r"versionCode=(\d+)", self.package_dump())
        have = m.group(1) if m else None
        if have and code and have == code:
            self.say("ok", "build", f"{self.app_id} versionCode {code} already installed")
            return
        why = ("not installed" if not have else f"versionCode {have} → {code}" if code else
               "the APK's versionCode is unknown (manifest unreadable, no aapt2): reinstalled every time")
        rc, out = run(install, timeout=600)
        if rc != 0 or "Success" not in out:
            raise TargetError(f"adb install failed: {out.strip()[-300:]}")
        self.say("done", "build", f"{os.path.basename(self.a.apk)} installed ({why})")
        if os.path.isdir(self.avd_dir()):   # the cold-boot rule compares the snapshot with this record
            with open(os.path.join(self.avd_dir(), "qa-build.txt"), "w", encoding="utf-8") as f:
                f.write(f"{self.app_id} versionCode {code or 'unknown'} sha256 {sha256_of(self.a.apk)}\n")

    # root (native libraries and the browser)

    def need_root(self) -> bool:
        if self.root is not None:
            return self.root
        tag = self.image.split(";")[2] if self.image.count(";") >= 3 else ""
        if "playstore" in tag or self.prop("ro.build.type") == "user":
            self.root, self.root_reason = False, ("a Play Store image (production build) has no root: use a "
                                                  "Google APIs image (…;google_apis;x86_64)")
            return False
        self.adb("root", timeout=60)
        self.adb("wait-for-device", timeout=60)
        self.root = self.adb("shell", "id", "-u")[1].strip() == "0"
        self.root_reason = "" if self.root else "adb root was refused on this image"
        return self.root

    # 5. native libraries

    def step_native_libs(self) -> None:
        step = "native-libs"
        if not self.a.apk:
            self.say("skip", step, "no --apk: the APK's libraries cannot be inspected")
            return
        reason = arm_only_reason(self.a.apk)
        if reason:
            self.say("skip", step, reason)
            return
        if self.dry:
            self.plan(step, "ARM-only APK with uncompressed libraries: copied on an x86_64 image with native "
                      "translation and root, when the app's lib/arm64 is empty",
                      [self.tools["adb"], "root"], [self.tools["adb"], "push", "<lib>.so", TMP_LIBS + "/"],
                      [self.tools["adb"], "shell", "cp … && chown system:system … && chmod 755 … && restorecon -R …"])
            return
        if not self.serial:
            self.say("skip", step, "no running emulator")
            return
        abis = self.prop("ro.product.cpu.abilist")
        if not abis.startswith("x86_64"):
            self.say("skip", step, f"the device's ABIs are {abis or 'unknown'}: no translation involved")
            return
        bridge = self.prop("ro.dalvik.vm.native.bridge")
        if bridge in ("", "0"):
            self.say("skip", step, "the image has no native translation (ro.dalvik.vm.native.bridge): an ARM-only "
                     "app cannot run on it; use a Google APIs x86_64 image from Android 11 on")
            return
        m = re.search(r"legacyNativeLibraryDir=(\S+)", self.package_dump())
        if not m:
            self.say("skip", step, f"{self.app_id} is not installed (or has no native library folder)")
            return
        if not self.need_root():
            self.say("skip", step, f"{self.root_reason}: the app may fail at start (technique-mobile.md §1.1)")
            return
        folder = m.group(1).rstrip("/") + "/arm64"
        names = sorted(i.filename.split("/")[2] for i in native_libs(self.a.apk)["arm64-v8a"])
        have = set(self.adb("shell", "ls", sh_quote(folder))[1].split())
        if all(n in have for n in names):
            self.say("ok", step, f"{len(names)} arm64 libraries already in {folder}")
            return
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(self.a.apk) as z:
            self.shell(f"rm -rf {TMP_LIBS} && mkdir -p {TMP_LIBS}")
            for name in names:
                local = os.path.join(tmp, name)
                with open(local, "wb") as f:
                    f.write(z.read(f"lib/arm64-v8a/{name}"))
                rc, out = self.adb("push", local, f"{TMP_LIBS}/{name}", timeout=300)
                if rc != 0:
                    raise TargetError(f"adb push {name} failed: {out.strip()[-200:]}")
        q = sh_quote(folder)
        self.shell(f"mkdir -p {q} && cp {TMP_LIBS}/*.so {q}/ && chown -R system:system {q} && chmod 755 {q} "
                   f"{q}/*.so && restorecon -R {q}; rm -rf {TMP_LIBS}")
        have = set(self.adb("shell", "ls", sh_quote(folder))[1].split())
        missing = [n for n in names if n not in have]
        if missing:
            raise TargetError(f"{len(missing)} libraries still missing in {folder} (e.g. {missing[0]})")
        self.say("done", step, f"{len(names)} arm64 libraries copied into {folder} (the APK is unchanged); "
                 "repeat after each reinstall (run this script again)")

    # 6. permissions

    def step_permissions(self) -> None:
        step = "permissions"
        wanted = [("grant", p) for p in self.grant] + [("revoke", p) for p in self.revoke]
        if not wanted:
            self.say("skip", step, "mobile.permissions has none")
            return
        if self.dry:
            self.plan(step, f"{len(self.grant)} to grant, {len(self.revoke)} to revoke",
                      *[[self.tools["adb"], "shell", "pm", verb, self.app_id, p] for verb, p in wanted])
            return
        if not self.serial:
            self.say("skip", step, "no running emulator")
            return
        dump = self.package_dump()
        if not dump:
            self.say("skip", step, f"{self.app_id} is not installed")
            return
        changed, same = [], []
        for verb, p in wanted:
            state = re.search(re.escape(p) + r": granted=(true|false)", dump)
            if state and (state.group(1) == "true") == (verb == "grant"):
                same.append(p)
                continue
            rc, out = self.adb("shell", "pm", verb, self.app_id, p)
            if rc != 0 or re.search(r"Exception|Error|not a changeable|Unknown permission", out):
                raise TargetError(f"pm {verb} {p}: {out.strip()[:200]}")
            changed.append(f"{verb} {p}")
        if changed:
            self.say("done", step, ", ".join(changed) + (f" ({len(same)} already as wanted)" if same else ""))
        else:
            self.say("ok", step, f"{len(same)} already as wanted")

    # 7. the browser's first run

    def step_browser(self) -> None:
        step = "browser"
        cmds = [[self.tools["adb"], "shell", f"echo '{CHROME_FLAGS}' > {CHROME_FILE}"],
                [self.tools["adb"], "shell", "am", "set-debug-app", "--persistent", CHROME]]
        if self.dry:
            self.plan(step, "Chrome's first run off (root image; skipped without Chrome)", *cmds)
            return
        if not self.serial:
            self.say("skip", step, "no running emulator")
            return
        if f"package:{CHROME}" not in self.adb("shell", "pm", "list", "packages", CHROME)[1].split():
            self.say("skip", step, "Chrome is not installed")
            return
        if not self.need_root():
            self.say("skip", step, f"{self.root_reason}: accept Chrome's first-run screens by hand before a "
                     "magic-link login")
            return
        current = self.adb("shell", "cat", CHROME_FILE)[1].strip()
        debug_app = self.adb("shell", "settings", "get", "global", "debug_app")[1].strip()
        if current == CHROME_FLAGS and debug_app == CHROME:
            self.say("ok", step, "Chrome's first run already off")
            return
        self.shell(f"echo '{CHROME_FLAGS}' > {CHROME_FILE} && chmod 644 {CHROME_FILE}")
        self.shell(f"am set-debug-app --persistent {CHROME}")
        self.say("done", step, "Chrome's first run off (restart Chrome if it was open)")

    def check_config(self) -> None:
        self.refuse_prod()
        if (self.a.apk or self.grant or self.revoke) and not self.app_id:
            raise ConfigError("mobile.app_id missing in project.yaml (the app under test, e.g. com.demoshop.app)")

    def execute(self) -> int:
        steps = [("configuration", self.check_config), ("sdk", self.step_sdk), ("sha256", self.check_apk),
                 ("avd", self.step_avd), ("boot", self.step_boot), ("build", self.step_build),
                 ("native-libs", self.step_native_libs), ("permissions", self.step_permissions),
                 ("browser", self.step_browser)]
        for self.current, step in steps:
            step()
        print("dry run: nothing was run" if self.dry else
              f"target ready: {self.avd}" + (f" as {self.serial}" if self.serial else " (not running)"))
        return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Prepares a reproducible Android emulator (technique-mobile.md §1.1).")
    ap.add_argument("workspace")
    ap.add_argument("--avd", help="AVD name (default: mobile.avd, else qa-<project>)")
    ap.add_argument("--image", help=f"system image (default: mobile.image, else {DEFAULT_IMAGE})")
    ap.add_argument("--apk", help="the build to install (checked against mobile.build.sha256)")
    ap.add_argument("--no-boot", action="store_true", help="do not start the emulator")
    ap.add_argument("--no-audio", action="store_true", help="start the emulator without audio")
    ap.add_argument("--cold", action="store_true", help="boot without loading a saved snapshot (-no-snapshot-load)")
    ap.add_argument("--gpu", metavar="MODE", help="the emulator's GPU mode, e.g. swiftshader_indirect (a runner)")
    ap.add_argument("--headless", action="store_true", help="no emulator window (-no-window; a runner)")
    ap.add_argument("--boot-timeout", type=int, default=BOOT_TIMEOUT_S, help="seconds (default 300)")
    ap.add_argument("--allow-prod", action="store_true",
                    help="accept a build that looks like production (only with the user's explicit approval)")
    ap.add_argument("--dry-run", action="store_true", help="print the steps and commands, run nothing")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        try:
            proj = read_project(a.workspace)
        except FileNotFoundError as e:
            raise ConfigError(str(e)) from None
        mobile = proj.get("mobile") or {}
        if not isinstance(mobile, dict):
            raise ConfigError("mobile: in project.yaml must be a map")
        if a.image and a.image.count(";") != 3:
            raise ConfigError(f"--image {a.image!r}: expected system-images;android-<n>;<tag>;<abi>")
    except ConfigError as e:
        print(f"[fail] configuration: {e}", file=sys.stderr)
        return 2
    setup = Setup(a, mobile, str(proj.get("project") or ""))
    # the emulator searches only ANDROID_AVD_HOME (then ~/.android/avd): set it for the whole run, and the avd step
    # moves it to wherever avdmanager really wrote the AVD (11.8 pilot: XDG_CONFIG_HOME on GitHub's runners)
    saved = os.environ.get("ANDROID_AVD_HOME")
    os.environ["ANDROID_AVD_HOME"] = avd_home()
    try:
        return setup.execute()
    except ConfigError as e:
        print(f"[fail] {setup.current}: {e}", file=sys.stderr)
        return 2
    except TargetError as e:
        print(f"[fail] {setup.current}: {e}", file=sys.stderr)
        return 3
    finally:
        if saved is None:
            os.environ.pop("ANDROID_AVD_HOME", None)
        else:
            os.environ["ANDROID_AVD_HOME"] = saved


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

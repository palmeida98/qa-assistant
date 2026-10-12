"""
Builds the companion driver (tools/android-driver/) for a real Android device that blocks input injection
(technique-mobile.md §1.2; plan, task 11.7). No Gradle: aapt2, javac, d8, zipalign and apksigner.

    python scripts/build_driver.py [--out DIR] [--push [--serial S]]

Steps, each printed as `[ok|done|fail] <step>: <detail>`:
  1. tools     the Android SDK (ANDROID_HOME, ANDROID_SDK_ROOT, the default folder), the newest build-tools and
               platform installed, javac and keytool (JAVA_HOME, then the PATH); the command to install what is
               missing is printed
  2. version   android:versionCode of AndroidManifest.xml equals BuildConfig.VERSION (the driver reports it to
               device.py)
  3. key       the signing key ~/.qa-assistant/android-driver.jks, created on the first build with a random
               password kept next to it (android-driver.pass); both outside every repository, never committed.
               The password reaches keytool and apksigner as a file, never on a command line
  4. build     resources, classes, dex, zip, align, sign -> <out>/qa-driver.apk (default
               tools/android-driver/build/, ignored by git); prints its SHA-256
  5. push      (--push) `adb install -r`; when the device refuses it (e.g. INSTALL_FAILED_USER_RESTRICTED), copies
               the APK to /sdcard/Download/qa-driver.apk for the person to install from the file manager

Exit codes:
  0  ok (with --push: installed, or copied for the person to install)
  2  a tool missing, the versions differ
  3  a build step failed, or --push could neither install nor copy
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import setup_emulator as se

DRIVER = os.path.join(ROOT, "tools", "android-driver")
KEY_DIR = os.path.join(os.path.expanduser("~"), ".qa-assistant")
KEY_ALIAS = "qa-driver"
DEVICE_PATH = "/sdcard/Download/qa-driver.apk"
MIN_API = "26"
TARGET_API = "33"
EXE = ".exe" if os.name == "nt" else ""
BAT = ".bat" if os.name == "nt" else ""


class ConfigError(Exception):
    """A tool missing or the versions differ (code 2)."""


class StepError(Exception):
    """A build step failed (code 3)."""


def run(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> tuple[int, str]:
    """Runs a program and returns (exit code, stdout + stderr). The tests replace it."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"no answer in {timeout} s"
    except OSError as e:
        return 127, str(e)
    return r.returncode, (r.stdout + r.stderr).replace("\r\n", "\n")


def say(state: str, step: str, detail: str) -> None:
    print(f"[{state}] {step}: {detail}")


def newest(pattern: str) -> str | None:
    """The newest folder matching pattern, by its numeric version (35.0.0 > 9.0.0; android-35 > android-9)."""
    def key(p):
        return [int(n) for n in re.findall(r"\d+", os.path.basename(p))]
    found = sorted((p for p in glob.glob(pattern) if os.path.isdir(p)), key=key, reverse=True)
    return found[0] if found else None


def java_tool(name: str) -> str | None:
    home = os.environ.get("JAVA_HOME")
    if home and os.path.isfile(os.path.join(home, "bin", name + EXE)):
        return os.path.join(home, "bin", name + EXE)
    return shutil.which(name)


def find_tools() -> dict:
    for sdk in se.sdk_roots():
        bt = newest(os.path.join(sdk, "build-tools", "*"))
        platform = newest(os.path.join(sdk, "platforms", "android-*"))
        if bt and platform and os.path.isfile(os.path.join(platform, "android.jar")):
            tools = {"sdk": sdk, "android.jar": os.path.join(platform, "android.jar"),
                     "aapt2": os.path.join(bt, "aapt2" + EXE), "d8": os.path.join(bt, "d8" + BAT),
                     "zipalign": os.path.join(bt, "zipalign" + EXE), "apksigner": os.path.join(bt, "apksigner" + BAT),
                     "adb": os.path.join(sdk, "platform-tools", "adb" + EXE)}
            break
    else:
        raise ConfigError("Android build-tools and a platform not found (ANDROID_HOME, ANDROID_SDK_ROOT, the default "
                          "folder): `sdkmanager \"build-tools;35.0.0\" \"platforms;android-35\"`")
    for name in ("aapt2", "d8", "zipalign", "apksigner"):
        if not os.path.isfile(tools[name]):
            raise ConfigError(f"{name} missing in {os.path.dirname(tools['aapt2'])}: reinstall the build-tools")
    for name in ("javac", "keytool", "jar"):
        tools[name] = java_tool(name)
        if not tools[name]:
            raise ConfigError(f"{name} not found (JAVA_HOME, PATH): install a JDK 17 or newer")
    if not os.path.isfile(tools["adb"]):
        tools["adb"] = shutil.which("adb")
    return tools


def versions() -> tuple[int, int]:
    """(android:versionCode of the manifest, BuildConfig.VERSION)."""
    with open(os.path.join(DRIVER, "AndroidManifest.xml"), encoding="utf-8") as f:
        m = re.search(r'android:versionCode="(\d+)"', f.read())
    with open(os.path.join(DRIVER, "src", "io", "qaassistant", "driver", "BuildConfig.java"), encoding="utf-8") as f:
        b = re.search(r"VERSION\s*=\s*(\d+)", f.read())
    if not (m and b):
        raise ConfigError("versionCode or BuildConfig.VERSION not found")
    return int(m.group(1)), int(b.group(1))


def ensure_key(tools: dict, key_dir: str = KEY_DIR) -> tuple[str, str, bool]:
    """(keystore, password file, created now). The password is random and only ever lives in its file."""
    ks, pw = os.path.join(key_dir, "android-driver.jks"), os.path.join(key_dir, "android-driver.pass")
    if os.path.isfile(ks) and os.path.isfile(pw):
        return ks, pw, False
    if os.path.isfile(ks) != os.path.isfile(pw):
        raise ConfigError(f"{ks} and {pw} must exist together: move the one left aside and build again (a new key "
                          "means uninstalling the driver from the device before installing the new APK)")
    os.makedirs(key_dir, exist_ok=True)
    with open(pw, "w", encoding="ascii", newline="\n") as f:
        f.write(secrets.token_urlsafe(24) + "\n")
    code, out = run([tools["keytool"], "-genkeypair", "-keystore", ks, "-storetype", "PKCS12",
                     "-storepass:file", pw, "-alias", KEY_ALIAS, "-keyalg", "RSA", "-keysize", "2048",
                     "-validity", "10000", "-dname", "CN=qa-assistant android driver"])
    if code != 0:
        for p in (ks, pw):
            if os.path.isfile(p):
                os.remove(p)
        raise StepError(f"keytool: {out.strip()[-300:]}")
    return ks, pw, True


def sources() -> list[str]:
    return sorted(glob.glob(os.path.join(DRIVER, "src", "**", "*.java"), recursive=True))


def build(tools: dict, out_dir: str, ks: str, pw: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    apk = os.path.join(out_dir, "qa-driver.apk")
    with tempfile.TemporaryDirectory() as tmp:
        res, base = os.path.join(tmp, "res.zip"), os.path.join(tmp, "base.apk")
        classes, dex = os.path.join(tmp, "classes"), os.path.join(tmp, "dex")
        os.makedirs(classes)
        os.makedirs(dex)
        aligned = os.path.join(tmp, "aligned.apk")
        steps = [
            ("resources", [tools["aapt2"], "compile", "--dir", os.path.join(DRIVER, "res"), "-o", res]),
            ("manifest", [tools["aapt2"], "link", "-I", tools["android.jar"], "--manifest",
                          os.path.join(DRIVER, "AndroidManifest.xml"), "--min-sdk-version", MIN_API,
                          "--target-sdk-version", TARGET_API, "-o", base, res]),
            ("javac", [tools["javac"], "-nowarn", "-Xlint:-options", "-encoding", "UTF-8", "-source", "8",
                       "-target", "8", "-bootclasspath", tools["android.jar"], "-d", classes, *sources()]),
        ]
        for name, cmd in steps:
            code, out = run(cmd)
            if code != 0:
                raise StepError(f"{name}: {out.strip()[-500:]}")
        class_files = sorted(glob.glob(os.path.join(classes, "**", "*.class"), recursive=True))
        code, out = run([tools["d8"], "--lib", tools["android.jar"], "--min-api", MIN_API, "--output", dex,
                         *class_files])
        if code != 0:
            raise StepError(f"d8: {out.strip()[-500:]}")
        with zipfile.ZipFile(base, "a", zipfile.ZIP_DEFLATED) as z:
            z.write(os.path.join(dex, "classes.dex"), "classes.dex")
        for name, cmd in [("zipalign", [tools["zipalign"], "-f", "4", base, aligned]),
                          ("apksigner", [tools["apksigner"], "sign", "--ks", ks, "--ks-pass", f"file:{pw}",
                                         "--ks-key-alias", KEY_ALIAS, "--out", apk, aligned])]:
            code, out = run(cmd)
            if code != 0:
                raise StepError(f"{name}: {out.strip()[-500:]}")
    return apk


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def push(tools: dict, apk: str, serial: str | None) -> str:
    """'installed' or 'copied' (the person installs it from Download/)."""
    if not tools.get("adb"):
        raise StepError("adb not found (the SDK's platform-tools, PATH)")
    target = ["-s", serial] if serial else []
    code, out = run([tools["adb"], *target, "install", "-r", apk])
    if code == 0 and "Success" in out:
        return "installed"
    say("skip", "install", (out.strip().splitlines() or ["refused"])[-1][:200])
    code, out = run([tools["adb"], *target, "push", apk, DEVICE_PATH])
    if code != 0:
        raise StepError(f"adb push: {out.strip()[-300:]}")
    return "copied"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Builds the companion driver APK (technique-mobile.md §1.2).")
    ap.add_argument("--out", default=os.path.join(DRIVER, "build"), help="output folder (default: tools/android-driver/build)")
    ap.add_argument("--push", action="store_true", help="install it on the device, or copy it to Download/")
    ap.add_argument("--serial", help="the device (default: the only one connected)")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        tools = find_tools()
        say("ok", "tools", f"{os.path.dirname(tools['aapt2'])}, {os.path.basename(os.path.dirname(tools['android.jar']))}")
        manifest, config = versions()
        if manifest != config:
            raise ConfigError(f"android:versionCode {manifest} differs from BuildConfig.VERSION {config}")
        say("ok", "version", str(manifest))
        ks, pw, created = ensure_key(tools)
        say("done" if created else "ok", "key", ks + (" (new: a device with an older build of the driver needs it "
                                                     "uninstalled first)" if created else ""))
        apk = build(tools, a.out, ks, pw)
        say("done", "build", f"{apk} sha256 {sha256_of(apk)}")
        if a.push:
            how = push(tools, apk, a.serial)
            if how == "installed":
                say("done", "push", "installed; turn the service on (technique-mobile.md §1.2, steps 3-4) if it is new")
            else:
                say("done", "push", f"copied to {DEVICE_PATH}: install it on the device from the file manager, then "
                                    "turn the service on (technique-mobile.md §1.2, steps 2-4)")
    except ConfigError as e:
        say("fail", "config", str(e))
        return 2
    except StepError as e:
        say("fail", "build", str(e))
        return 3
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

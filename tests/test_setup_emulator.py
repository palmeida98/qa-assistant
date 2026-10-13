"""scripts/setup_emulator.py (technique-mobile.md §1.1; plan 9.2): each step skipped when already done, the SHA-256
check before any install, the ARM-only detection from fixture APKs built here, the root refusal on a Play Store
image, the dry run running nothing, production builds refused, the boot timeout, and the new mobile: keys in
scripts/validate_project.py. No SDK, emulator nor device: run(), spawn() and the clock are replaced by a fake SDK.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import hashlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import setup_emulator as se
import validate_project as vp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apk_kit import make_apk

SERIAL = "emulator-5554"
LIB_DIR = "/data/app/~~Qa1==/com.demoshop.app-Zx9==/lib"
IMAGE = "system-images;android-35;google_apis;x86_64"


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def pause(self, s):
        self.t += s


class Proc:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


class FakeSDK:
    """Canned answers of emulator, avdmanager, aapt2 and adb; the state changes as the script acts."""

    def __init__(self, avds=("qa-demo",), running=True, boot_after=0, installed_code="140", apk_code="140",
                 abis="x86_64,arm64-v8a", bridge="libndk_translation.so", build_type="userdebug", root_ok=True,
                 libs_on_device=(), chrome=True, chrome_done=False, perms=None):
        self.avds, self.running, self.boot_after = list(avds), running, boot_after
        self.installed_code, self.apk_code = installed_code, apk_code
        self.props = {"ro.product.cpu.abilist": abis, "ro.dalvik.vm.native.bridge": bridge,
                      "ro.build.type": build_type, "ro.boot.qemu.avd_name": ""}
        self.root_ok, self.rooted = root_ok, False
        self.libs = set(libs_on_device)
        self.chrome, self.chrome_done = chrome, chrome_done
        self.perms = dict(perms or {})
        self.calls, self.spawned, self.boot_polls = [], [], 0
        self.crash = None
        self.avd_path = "/home/runner/.config/.android/avd"

    def spawn(self, cmd, log=None):
        self.spawned.append(cmd)
        self.log = log
        self.spawn_avd_home = os.environ.get("ANDROID_AVD_HOME")
        if self.crash is not None:          # the emulator writes its error and exits while starting
            with open(log, "w", encoding="utf-8") as f:
                f.write(self.crash)
            return Proc(code=1)
        self.running = True
        return Proc()

    def of(self, *words):
        return [c for c in self.calls if all(w in c for w in words)]

    def __call__(self, cmd, timeout=120, stdin=None):
        self.calls.append(cmd)
        tool, args = cmd[0], cmd[1:]
        if tool == "emulator" and args == ["-list-avds"]:
            return 0, "".join(a + "\n" for a in self.avds)
        if tool == "avdmanager" and args[:2] == ["list", "avd"]:
            return 0, "".join(f"    Name: {n}\n    Path: {self.avd_path}/{n}.avd\n---------\n" for n in self.avds)
        if tool == "avdmanager":
            self.avds.append(args[args.index("-n") + 1])
            return 0, ""
        if tool == "aapt2":
            return 0, f"package: name='com.demoshop.app' versionCode='{self.apk_code}' versionName='1.4.0'\n"
        assert tool == "adb", cmd
        if args == ["devices"]:
            return 0, "List of devices attached\n" + (f"{SERIAL}\tdevice\n" if self.running else "") + "\n"
        assert args[:2] == ["-s", SERIAL], cmd
        args = args[2:]
        if args == ["emu", "avd", "name"]:
            return 0, "qa-demo\nOK\n"
        if args[:2] == ["shell", "getprop"]:
            if args[2] == "sys.boot_completed":
                self.boot_polls += 1
                return 0, "1\n" if self.running and self.boot_polls > self.boot_after else "\n"
            return 0, self.props.get(args[2], "") + "\n"
        if args[:3] == ["shell", "dumpsys", "package"]:
            if not self.installed_code:
                return 0, ""
            perms = "".join(f"      {p}: granted={str(v).lower()}\n" for p, v in self.perms.items())
            return 0, (f"Packages:\n  Package [com.demoshop.app] (1a2b):\n    versionCode={self.installed_code} "
                       f"minSdk=26\n    legacyNativeLibraryDir={LIB_DIR}\n    runtime permissions:\n{perms}")
        if args[:2] == ["install", "-r"]:
            self.installed_code, self.libs = self.apk_code, set()
            return 0, "Performing Streamed Install\nSuccess\n"
        if args == ["root"]:
            self.rooted = self.root_ok
            return 0, "restarting adbd as root\n" if self.root_ok else "adbd cannot run as root in production builds\n"
        if args == ["wait-for-device"]:
            return 0, ""
        if args == ["shell", "id", "-u"]:
            return 0, "0\n" if self.rooted else "2000\n"
        if args[:2] == ["shell", "ls"]:
            return 0, "\n".join(sorted(self.libs)) + "\n" if self.libs else f"ls: {args[2]}: No such file\n"
        if args[0] == "push":
            return 0, "1 file pushed\n"
        if args[:2] == ["shell", "pm"] and args[2] in ("grant", "revoke"):
            self.perms[args[4]] = args[2] == "grant"
            return 0, ""
        if args[:4] == ["shell", "pm", "list", "packages"]:
            return 0, "package:com.android.chrome\n" if self.chrome else ""
        if args == ["shell", "cat", se.CHROME_FILE]:
            return 0, (se.CHROME_FLAGS if self.chrome_done else "cat: No such file") + "\n"
        if args == ["shell", "settings", "get", "global", "debug_app"]:
            return 0, ("com.android.chrome" if self.chrome_done else "null") + "\n"
        if args[0] == "shell" and len(args) == 2:   # one command string for the device's sh
            if " cp " in f" {args[1]}":
                self.libs |= {"libfoo.so", "libbar.so"}
            if "set-debug-app" in args[1]:
                self.chrome_done = True
            return 0, ""
        raise AssertionError(f"unexpected call {cmd}")


class Base(unittest.TestCase):
    MOBILE = ("mobile:\n  platform: android\n  app_id: com.demoshop.app\n  avd: qa-demo\n"
              f"  image: \"{IMAGE}\"\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self.tmp.name, "ws")
        self.sdk = os.path.join(self.tmp.name, "sdk")
        os.makedirs(self.ws)
        os.makedirs(os.path.join(self.sdk, *IMAGE.split(";")))
        self.apk = make_apk(os.path.join(self.tmp.name, "demoshop-test-1.4.0.apk"))
        with open(self.apk, "rb") as f:
            self.sha = hashlib.sha256(f.read()).hexdigest()
        self.project(f"  build: {{source: \"CI artefact, test flavour\", sha256: \"{self.sha}\"}}\n"
                     "  permissions: {grant: [android.permission.POST_NOTIFICATIONS], "
                     "revoke: [android.permission.CAMERA]}\n")
        self.tools = {"emulator": "emulator", "adb": "adb", "avdmanager": "avdmanager", "aapt2": "aapt2",
                      "sdk": self.sdk}

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, extra=""):
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("project: demo-shop\n" + self.MOBILE + extra)

    def go(self, fake, *args, clock=None, tools=None):
        out, err, clock = io.StringIO(), io.StringIO(), clock or Clock()
        with mock.patch.object(se, "run", fake), mock.patch.object(se, "spawn", fake.spawn), \
                mock.patch.object(se, "now", clock.now), mock.patch.object(se, "pause", clock.pause), \
                mock.patch.object(se, "find_tools", lambda: dict(tools or self.tools)), \
                mock.patch.object(se, "avd_home", lambda: os.path.join(self.tmp.name, "avd")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    def line(self, out, step):
        return next((ln for ln in out.splitlines() if f"] {step}:" in ln), "")


class AlreadyDone(Base):
    def test_everything_already_in_place_changes_nothing(self):
        fake = FakeSDK(libs_on_device={"libfoo.so", "libbar.so"}, chrome_done=True,
                       perms={"android.permission.POST_NOTIFICATIONS": True, "android.permission.CAMERA": False})
        rc, out, err = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 0, err)
        for step in ("sdk", "sha256", "avd", "boot", "build", "native-libs", "permissions", "browser"):
            self.assertTrue(self.line(out, step).startswith("[ok]"), (step, out))
        self.assertEqual(fake.spawned, [])
        self.assertEqual(fake.of("install"), [])
        self.assertEqual(fake.of("push"), [])
        self.assertEqual([c for c in fake.calls if c[0] == "avdmanager"], [])
        self.assertFalse([c for c in fake.calls if len(c) > 4 and c[4] in ("grant", "revoke")])

    def test_avdmanager_and_the_emulator_share_the_avd_home(self):
        # the 11.8 pilot: avdmanager followed XDG_CONFIG_HOME, the emulator did not ("Unknown AVD name")
        fake = FakeSDK(avds=(), running=False, boot_after=2, installed_code="")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/home/runner/.config"}):
            os.environ.pop("ANDROID_AVD_HOME", None)
            rc, _, err = self.go(fake, "--apk", self.apk)
            self.assertNotIn("ANDROID_AVD_HOME", os.environ)          # restored after the run
        self.assertEqual(rc, 0, err)
        self.assertEqual(fake.spawn_avd_home, os.path.join(self.tmp.name, "avd"))

    def test_an_avd_written_under_xdg_config_home_is_found(self):
        # the 11.8 pilot: avdmanager ignored ANDROID_AVD_HOME and wrote the .ini under XDG_CONFIG_HOME
        xdg = os.path.join(self.tmp.name, "xdg")
        os.makedirs(os.path.join(xdg, ".android", "avd"))
        with open(os.path.join(xdg, ".android", "avd", "qa-demo.ini"), "w", encoding="utf-8") as f:
            f.write("path=somewhere\n")
        fake = FakeSDK(avds=(), running=False, boot_after=2, installed_code="")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": xdg}):
            os.environ.pop("ANDROID_AVD_HOME", None)
            rc, _, err = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 0, err)
        self.assertEqual(fake.spawn_avd_home, os.path.join(xdg, ".android", "avd"))

    def test_with_no_ini_where_expected_one_is_written_from_avdmanager_list(self):
        fake = FakeSDK(avds=(), running=False, boot_after=2, installed_code="")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(self.tmp.name, "none")}):
            os.environ.pop("ANDROID_AVD_HOME", None)
            rc, _, err = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.tmp.name, "avd", "qa-demo.ini"), encoding="utf-8") as f:
            self.assertIn("path=/home/runner/.config/.android/avd/qa-demo.avd", f.read())
        self.assertEqual(fake.spawn_avd_home, os.path.join(self.tmp.name, "avd"))

    def test_an_emulator_that_exits_while_starting_shows_its_last_output(self):
        # the 11.8 pilot: the runner's emulator exited with code 1 and the reason was thrown away
        fake = FakeSDK(avds=(), running=False)
        fake.crash = "INFO | starting\nERROR | x86_64 emulation currently requires hardware acceleration!\n"
        rc, out, err = self.go(fake, "--apk", self.apk)
        self.assertNotEqual(rc, 0)
        self.assertIn("exited (code 1)", out + err)
        self.assertIn("requires hardware acceleration", out + err)
        os.remove(fake.log)

    def test_missing_pieces_are_done(self):
        fake = FakeSDK(avds=(), running=False, boot_after=2, installed_code="")
        rc, out, err = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.line(out, "avd").startswith("[done]"), out)
        self.assertIn(["avdmanager", "create", "avd", "-n", "qa-demo", "-k", IMAGE, "-d", "pixel_7"], fake.calls)
        self.assertEqual(fake.spawned, [["emulator", "-avd", "qa-demo", "-no-snapshot-save", "-no-boot-anim"]])
        self.assertTrue(self.line(out, "boot").startswith("[done]"), out)
        self.assertIn("not installed", self.line(out, "build"))
        self.assertTrue(self.line(out, "native-libs").startswith("[done]"), out)
        self.assertIn("repeat after each reinstall", self.line(out, "native-libs"))
        self.assertIn("grant android.permission.POST_NOTIFICATIONS", self.line(out, "permissions"))
        self.assertTrue(self.line(out, "browser").startswith("[done]"), out)
        self.assertEqual(len(fake.of("push")), 2)

    def test_no_audio_flag(self):
        fake = FakeSDK(running=False)
        self.go(fake, "--no-audio")
        self.assertIn("-no-audio", fake.spawned[0])

    def test_other_version_code_is_reinstalled(self):
        fake = FakeSDK(installed_code="139", apk_code="140")
        rc, out, _ = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 0)
        self.assertIn("versionCode 139 → 140", self.line(out, "build"))
        self.assertEqual(len(fake.of("install")), 1)

    def test_without_aapt2_the_manifest_gives_the_version_and_nothing_is_reinstalled(self):
        # plan 11.1: before, without aapt2 the build was reinstalled on every run (and the app's login was lost)
        fake = FakeSDK()
        rc, out, _ = self.go(fake, "--apk", self.apk, tools={**self.tools, "aapt2": None})
        self.assertEqual(rc, 0)
        self.assertIn("versionCode 140 already installed", self.line(out, "build"))
        self.assertEqual(fake.of("install"), [])
        self.assertEqual([c for c in fake.calls if c[0] == "aapt2"], [])     # never needed

    def test_an_unreadable_manifest_falls_back_to_aapt2_then_reinstalls(self):
        apk = make_apk(os.path.join(self.tmp.name, "odd.apk"), manifest=b"not binary xml")
        self.project()
        fake = FakeSDK(installed_code="140", apk_code="140")
        rc, out, _ = self.go(fake, "--apk", apk)
        self.assertEqual(rc, 0)
        self.assertIn("already installed", self.line(out, "build"))           # aapt2 told
        fake = FakeSDK()
        rc, out, _ = self.go(fake, "--apk", apk, tools={**self.tools, "aapt2": None})
        self.assertEqual(rc, 0)
        self.assertIn("versionCode is unknown", self.line(out, "build"))
        self.assertEqual(len(fake.of("install")), 1)

    def test_an_install_is_recorded_for_the_cold_boot_rule(self):
        os.makedirs(os.path.join(self.tmp.name, "avd", "qa-demo.avd"))
        fake = FakeSDK(installed_code="139")
        self.go(fake, "--apk", self.apk)
        with open(os.path.join(self.tmp.name, "avd", "qa-demo.avd", "qa-build.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), f"com.demoshop.app versionCode 140 sha256 {self.sha}\n")

    def test_no_boot_with_the_emulator_stopped_skips_the_device_steps(self):
        fake = FakeSDK(running=False)
        rc, out, _ = self.go(fake, "--apk", self.apk, "--no-boot")
        self.assertEqual(rc, 0)
        self.assertEqual(fake.spawned, [])
        for step in ("boot", "build", "native-libs", "permissions", "browser"):
            self.assertTrue(self.line(out, step).startswith("[skip]"), (step, out))


class Build(Base):
    def test_sha_mismatch_refuses_the_install(self):
        self.project("  build: {source: \"CI artefact\", sha256: \"" + "0" * 64 + "\"}\n")
        fake = FakeSDK(installed_code="")
        rc, _, err = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 2)
        self.assertIn("SHA-256", err)
        self.assertEqual(fake.of("install"), [])
        self.assertEqual(fake.calls, [])   # refused before touching the SDK

    def test_sha_missing_is_said_with_the_value_to_record(self):
        self.project()
        rc, out, _ = self.go(FakeSDK(), "--apk", self.apk)
        self.assertEqual(rc, 0)
        self.assertIn(self.sha, self.line(out, "sha256"))

    def test_other_package_is_refused(self):
        other = make_apk(os.path.join(self.tmp.name, "other.apk"), package="com.example.other")
        self.project()
        fake = FakeSDK()
        rc, _, err = self.go(fake, "--apk", other)
        self.assertEqual(rc, 2)
        self.assertIn("com.example.other", err)
        self.assertEqual(fake.of("install"), [])

    def test_production_builds_are_refused(self):
        prod_apk = make_apk(os.path.join(self.tmp.name, "demoshop-prod-1.4.0.apk"))
        fake = FakeSDK()
        rc, _, err = self.go(fake, "--apk", prod_apk)
        self.assertEqual(rc, 2)
        self.assertIn("technique-mobile.md §3", err)
        self.assertEqual(fake.calls, [])
        self.project("  build: {source: \"store: release-store track\"}\n")
        self.assertEqual(self.go(FakeSDK())[0], 2)
        self.assertEqual(self.go(FakeSDK(), "--allow-prod")[0], 0)


class NativeLibs(Base):
    def test_detection_from_fixture_apks(self):
        self.assertIsNone(se.arm_only_reason(self.apk))
        deflated = make_apk(os.path.join(self.tmp.name, "deflated.apk"), stored=False)
        self.assertIn("compressed", se.arm_only_reason(deflated))
        both = make_apk(os.path.join(self.tmp.name, "both.apk"),
                        libs=(("arm64-v8a", "libfoo.so"), ("x86_64", "libfoo.so")))
        self.assertIn("x86_64", se.arm_only_reason(both))
        none = make_apk(os.path.join(self.tmp.name, "none.apk"), libs=())
        self.assertIn("no lib/arm64-v8a", se.arm_only_reason(none))
        odd = make_apk(os.path.join(self.tmp.name, "odd.apk"), libs=(("arm64-v8a", "lib;rm -rf.so"),))
        self.assertIn("unexpected library name", se.arm_only_reason(odd))

    def test_deflated_apk_is_skipped(self):
        apk = make_apk(os.path.join(self.tmp.name, "deflated.apk"), stored=False)
        self.project()
        fake = FakeSDK()
        rc, out, _ = self.go(fake, "--apk", apk)
        self.assertEqual(rc, 0)
        self.assertIn("[skip] native-libs: the APK's native libraries are compressed", out)
        self.assertEqual(fake.of("push"), [])

    def test_image_without_translation_or_arm_device_is_skipped(self):
        _, out, _ = self.go(FakeSDK(bridge="0"), "--apk", self.apk)
        self.assertIn("no native translation", self.line(out, "native-libs"))
        _, out, _ = self.go(FakeSDK(abis="arm64-v8a"), "--apk", self.apk)
        self.assertIn("no translation involved", self.line(out, "native-libs"))

    def test_play_store_image_has_no_root(self):
        self.MOBILE = self.MOBILE.replace("google_apis;", "google_apis_playstore;")
        self.project()
        fake = FakeSDK(build_type="user", root_ok=False)
        rc, out, _ = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 0)
        self.assertIn("[skip] native-libs: a Play Store image", out)
        self.assertIn("[skip] browser: a Play Store image", out)
        self.assertEqual(fake.of("root"), [])
        self.assertEqual(fake.of("push"), [])

    def test_adb_root_refused_is_a_skip_with_the_reason(self):
        fake = FakeSDK(root_ok=False)
        rc, out, _ = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 0)
        self.assertIn("adb root was refused", self.line(out, "native-libs"))
        self.assertEqual(fake.of("push"), [])

    def test_the_copy_uses_the_package_folder_and_cleans_up(self):
        fake = FakeSDK()
        self.go(fake, "--apk", self.apk)
        script = next(c[-1] for c in fake.calls if " cp " in f" {c[-1]}")
        folder = f"'{LIB_DIR}/arm64'"
        for part in (f"cp {se.TMP_LIBS}/*.so {folder}/", f"chown -R system:system {folder}", "chmod 755",
                     f"restorecon -R {folder}", f"rm -rf {se.TMP_LIBS}"):
            self.assertIn(part, script)
        self.assertEqual(sorted(c[-1] for c in fake.of("push")),
                         [f"{se.TMP_LIBS}/libbar.so", f"{se.TMP_LIBS}/libfoo.so"])


class DryRunAndBoot(Base):
    def test_dry_run_runs_nothing(self):
        fake = FakeSDK(avds=(), running=False, installed_code="")
        rc, out, err = self.go(fake, "--apk", self.apk, "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertEqual(fake.calls, [])
        self.assertEqual(fake.spawned, [])
        for step in ("avd", "boot", "build", "native-libs", "permissions", "browser"):
            self.assertTrue(self.line(out, step).startswith("[dry-run]"), (step, out))
        self.assertIn("$ avdmanager create avd -n qa-demo", out)
        self.assertIn("dry run: nothing was run", out)

    def test_boot_timeout_is_3(self):
        clock = Clock()
        fake = FakeSDK(running=False, boot_after=10**6)
        rc, _, err = self.go(fake, clock=clock)
        self.assertEqual(rc, 3)
        self.assertIn("[fail] boot:", err)
        self.assertGreaterEqual(clock.t, 300)

    def test_missing_sdk_is_2_with_how_to_install(self):
        rc, _, err = self.go(FakeSDK(), tools={**self.tools, "emulator": None, "adb": None})
        self.assertEqual(rc, 2)
        self.assertIn("sdkmanager", err)

    def test_missing_system_image_prints_the_command_and_never_runs_sdkmanager(self):
        fake = FakeSDK(avds=())
        rc, _, err = self.go(fake, "--image", "system-images;android-34;google_apis;x86_64")
        self.assertEqual(rc, 2)
        self.assertIn('sdkmanager "system-images;android-34;google_apis;x86_64"', err)
        self.assertFalse([c for c in fake.calls if "sdkmanager" in c[0] or c[0] == "avdmanager"])

    def test_app_id_is_needed_for_the_build(self):
        self.MOBILE = "mobile:\n  platform: android\n"
        self.project()
        self.assertEqual(self.go(FakeSDK(), "--apk", self.apk)[0], 2)


class ColdBoot(Base):
    def snapshot(self, age_s):
        folder = os.path.join(self.tmp.name, "avd", "qa-demo.avd", "snapshots", "default_boot")
        os.makedirs(folder, exist_ok=True)
        t = os.path.getmtime(self.apk) + age_s
        os.utime(folder, (t, t))

    def test_a_snapshot_older_than_the_build_is_never_loaded(self):
        self.snapshot(-3600)                                   # saved an hour before the APK arrived
        fake = FakeSDK(running=False)
        rc, out, _ = self.go(fake, "--apk", self.apk)
        self.assertEqual(rc, 0)
        self.assertIn("-no-snapshot-load", fake.spawned[0])
        self.assertIn("cold: the saved snapshot is older than the build", self.line(out, "boot"))

    def test_a_snapshot_older_than_the_last_install_is_never_loaded(self):
        self.snapshot(-3600)
        record = os.path.join(self.tmp.name, "avd", "qa-demo.avd", "qa-build.txt")
        with open(record, "w", encoding="utf-8") as f:
            f.write("com.demoshop.app versionCode 140\n")
        fake = FakeSDK(running=False)
        self.go(fake)                                          # no --apk: the record decides
        self.assertIn("-no-snapshot-load", fake.spawned[0])

    def test_a_newer_snapshot_is_loaded_and_cold_forces(self):
        self.snapshot(+3600)
        fake = FakeSDK(running=False)
        self.go(fake, "--apk", self.apk)
        self.assertNotIn("-no-snapshot-load", fake.spawned[0])
        fake = FakeSDK(running=False)
        self.go(fake, "--apk", self.apk, "--cold", "--gpu", "swiftshader_indirect", "--headless", "--no-audio")
        self.assertEqual(fake.spawned[0], ["emulator", "-avd", "qa-demo", "-no-snapshot-save", "-no-boot-anim",
                                           "-no-snapshot-load", "-no-audio", "-gpu", "swiftshader_indirect",
                                           "-no-window"])

    def test_a_running_emulator_is_not_restarted(self):
        self.snapshot(-3600)
        fake = FakeSDK(running=True)
        self.go(fake, "--apk", self.apk)
        self.assertEqual(fake.spawned, [])


class FindTools(unittest.TestCase):
    def test_sdk_from_android_home(self):
        with tempfile.TemporaryDirectory() as sdk:
            exe = se.EXE
            for rel in (("emulator", "emulator" + exe), ("platform-tools", "adb" + exe),
                        ("build-tools", "35.0.0", "aapt2" + exe)):
                os.makedirs(os.path.join(sdk, *rel[:-1]), exist_ok=True)
                open(os.path.join(sdk, *rel), "w").close()
            env = {"ANDROID_HOME": sdk, "ANDROID_SDK_ROOT": "", "LOCALAPPDATA": os.path.join(sdk, "none")}
            with mock.patch.dict(os.environ, env), mock.patch.object(se.shutil, "which", lambda _: None), \
                    mock.patch.object(se.os.path, "expanduser", lambda p: os.path.join(sdk, "no-home")):
                tools = se.find_tools()
        self.assertEqual(tools["sdk"], os.path.abspath(sdk))
        self.assertTrue(tools["adb"].endswith("adb" + exe))
        self.assertTrue(tools["aapt2"].endswith("aapt2" + exe))
        self.assertIsNone(tools["avdmanager"])


class ValidateKeys(unittest.TestCase):
    def check(self, **mobile):
        return vp.mobile_target({"project": "demo-shop",
                                 "mobile": {"platform": "android", "app_id": "com.demoshop.app", **mobile}})

    def test_valid_keys(self):
        self.assertEqual(self.check(avd="qa-demo", image=IMAGE, build={"source": "CI artefact", "sha256": "a" * 64},
                                    permissions={"grant": ["android.permission.CAMERA"], "revoke": []}), ([], []))

    def test_invalid_keys(self):
        for mobile, fragment in [({"avd": "qa demo"}, "mobile.avd"),
                                 ({"image": "android-35"}, "mobile.image"),
                                 ({"build": "somewhere"}, "mobile.build must be a map"),
                                 ({"build": {"source": "CI", "sha256": "abc"}}, "64 hexadecimal"),
                                 ({"permissions": ["android.permission.CAMERA"]}, "mobile.permissions"),
                                 ({"permissions": {"grant": "android.permission.CAMERA"}}, "list of permission"),
                                 ({"permissions": {"grant": ["X"], "revoke": ["X"]}}, "both granted and revoked")]:
            problems, _ = self.check(**mobile)
            self.assertTrue(any(fragment in p for p in problems), (mobile, problems))

    def test_missing_sha_is_a_warning(self):
        problems, warnings = self.check(build={"source": "CI artefact"})
        self.assertEqual(problems, [])
        self.assertTrue(any("sha256 is missing" in w for w in warnings), warnings)

    def test_validate_reports_them(self):
        with tempfile.TemporaryDirectory() as ws:
            with open(os.path.join(ws, "project.yaml"), "w", encoding="utf-8") as f:
                f.write("project: demo-shop\ntracker: jira\nbug_format: SHOP-{n}\nenvironments: {test: "
                        "https://shop.demo}\nmodules: [mobile]\nmobile: {platform: android, flows: flows, "
                        "build: {sha256: nothex}}\n")
            with mock.patch.object(vp, "gitignore_warnings", lambda *a: []):
                problems, _ = vp.validate(ws)
        self.assertTrue(any("64 hexadecimal" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()

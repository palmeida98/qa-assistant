"""scripts/build_driver.py (technique-mobile.md §1.2; plan 11.7): the steps in order, the signing key created once
outside the repository with its password only in a file (never on a command line), the versions kept equal, a failed
step and a missing toolchain, and --push falling back to the device's Download/ when `adb install` is refused. No SDK
nor JDK: run() and the tool lookup are replaced by a fake toolchain that writes the files each tool would.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import build_driver as bd

TOOLS = {"sdk": "/sdk", "android.jar": "/sdk/platforms/android-35/android.jar", "aapt2": "aapt2", "d8": "d8",
         "zipalign": "zipalign", "apksigner": "apksigner", "javac": "javac", "keytool": "keytool", "jar": "jar",
         "adb": "adb"}


class Toolchain:
    """Plays each tool: records the command line and writes the file the real tool would write."""

    def __init__(self, fail=None, install="Success\n"):
        self.calls, self.fail, self.install = [], fail, install

    def arg(self, cmd, flag):
        return cmd[cmd.index(flag) + 1]

    def __call__(self, cmd, cwd=None, timeout=300):
        self.calls.append(cmd)
        tool = cmd[0]
        name = cmd[1] if tool in ("aapt2", "apksigner") else tool
        if self.fail in (tool, name):
            return 1, f"{tool} failed: fictional error"
        if tool == "keytool":
            with open(self.arg(cmd, "-keystore"), "wb") as f:
                f.write(b"fictional keystore")
        elif tool == "aapt2" and name == "compile":
            open(self.arg(cmd, "-o"), "wb").close()
        elif tool == "aapt2" and name == "link":
            with zipfile.ZipFile(self.arg(cmd, "-o"), "w") as z:
                z.writestr("AndroidManifest.xml", b"binary manifest")
        elif tool == "d8":
            with open(os.path.join(self.arg(cmd, "--output"), "classes.dex"), "wb") as f:
                f.write(b"dex\n035")
        elif tool == "zipalign":
            shutil.copyfile(cmd[-2], cmd[-1])
        elif tool == "apksigner":
            shutil.copyfile(cmd[-1], self.arg(cmd, "--out"))
        elif tool == "adb" and "install" in cmd:
            return (0 if "Success" in self.install else 1), self.install
        return 0, ""

    def tools(self):
        return [c[0] if c[0] not in ("aapt2", "apksigner") else f"{c[0]} {c[1]}" for c in self.calls]


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.keys = os.path.join(self.tmp.name, "home", ".qa-assistant")
        self.out = os.path.join(self.tmp.name, "out")
        for name, value in [("find_tools", lambda: dict(TOOLS)), ("KEY_DIR", self.keys)]:
            patcher = mock.patch.object(bd, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # ensure_key's default was bound at definition: call it through the patched folder
        real = bd.ensure_key
        patcher = mock.patch.object(bd, "ensure_key", lambda tools: real(tools, self.keys))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_it(self, chain, *args):
        out = io.StringIO()
        with mock.patch.object(bd, "run", chain), contextlib.redirect_stdout(out):
            rc = bd.main(["--out", self.out, *args])
        return rc, out.getvalue()

    def test_the_steps_in_order_and_the_apk(self):
        chain = Toolchain()
        rc, out = self.run_it(chain)
        self.assertEqual(rc, 0, out)
        self.assertEqual(chain.tools(), ["keytool", "aapt2 compile", "aapt2 link", "javac", "d8", "zipalign",
                                         "apksigner sign"])
        apk = os.path.join(self.out, "qa-driver.apk")
        with zipfile.ZipFile(apk) as z:
            self.assertEqual(sorted(z.namelist()), ["AndroidManifest.xml", "classes.dex"])
        self.assertIn(f"[done] build: {apk} sha256 ", out)
        javac = next(c for c in chain.calls if c[0] == "javac")
        self.assertTrue(any(p.endswith("DriverProvider.java") for p in javac))   # the real sources
        link = next(c for c in chain.calls if c[:2] == ["aapt2", "link"])
        self.assertEqual(link[link.index("--min-sdk-version") + 1], "26")

    def test_the_key_is_made_once_outside_the_repository_and_its_password_only_in_a_file(self):
        chain = Toolchain()
        self.assertEqual(self.run_it(chain)[0], 0)
        ks, pw = os.path.join(self.keys, "android-driver.jks"), os.path.join(self.keys, "android-driver.pass")
        self.assertTrue(os.path.isfile(ks) and os.path.isfile(pw))
        self.assertFalse(os.path.abspath(ks).startswith(os.path.abspath(ROOT) + os.sep))
        with open(pw, encoding="ascii") as f:
            password = f.read().strip()
        self.assertGreaterEqual(len(password), 24)
        keytool = chain.calls[0]
        self.assertEqual(keytool[keytool.index("-storepass:file") + 1], pw)
        sign = next(c for c in chain.calls if c[:2] == ["apksigner", "sign"])
        self.assertEqual(sign[sign.index("--ks-pass") + 1], f"file:{pw}")
        self.assertFalse(any(password in part for c in chain.calls for part in c))   # never on a command line
        chain = Toolchain()
        rc, out = self.run_it(chain)
        self.assertEqual(rc, 0)
        self.assertNotIn("keytool", chain.tools())                                     # reused, not replaced
        self.assertIn("[ok] key:", out)

    def test_a_half_key_is_refused(self):
        os.makedirs(self.keys)
        with open(os.path.join(self.keys, "android-driver.jks"), "wb") as f:
            f.write(b"orphan")
        rc, out = self.run_it(Toolchain())
        self.assertEqual(rc, 2)
        self.assertIn("must exist together", out)

    def test_a_failed_keytool_leaves_no_key(self):
        rc, _ = self.run_it(Toolchain(fail="keytool"))
        self.assertEqual(rc, 3)
        self.assertFalse(os.path.exists(os.path.join(self.keys, "android-driver.pass")))

    def test_a_failed_step_is_3(self):
        for step in ("compile", "javac", "d8", "sign"):
            rc, out = self.run_it(Toolchain(fail=step))
            self.assertEqual(rc, 3, step)
            self.assertIn("fictional error", out)

    def test_versions_must_match(self):
        self.assertEqual(len(set(bd.versions())), 1)                     # the sources in the repository agree
        with mock.patch.object(bd, "versions", return_value=(2, 1)):
            rc, out = self.run_it(Toolchain())
        self.assertEqual(rc, 2)
        self.assertIn("BuildConfig.VERSION 1", out)

    def test_push_installs_or_copies_to_download(self):
        chain = Toolchain()
        rc, out = self.run_it(chain, "--push", "--serial", "R58M")
        self.assertEqual(rc, 0)
        self.assertIn("[done] push: installed", out)
        self.assertEqual([c[:4] for c in chain.calls if c[0] == "adb"], [["adb", "-s", "R58M", "install"]])
        chain = Toolchain(install="Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user]\n")
        rc, out = self.run_it(chain, "--push")
        self.assertEqual(rc, 0)
        self.assertIn("INSTALL_FAILED_USER_RESTRICTED", out)
        self.assertIn(f"copied to {bd.DEVICE_PATH}", out)
        push = [c for c in chain.calls if c[:2] == ["adb", "push"]]
        self.assertEqual(push[0][-1], "/sdcard/Download/qa-driver.apk")


class ToolchainLookup(unittest.TestCase):
    def test_no_sdk_says_what_to_install(self):
        with mock.patch.object(bd.se, "sdk_roots", return_value=[]), self.assertRaises(bd.ConfigError) as e:
            bd.find_tools()
        self.assertIn("build-tools", str(e.exception))

    def test_the_newest_build_tools_and_platform(self):
        with tempfile.TemporaryDirectory() as sdk:
            for d in ("build-tools/9.0.0", "build-tools/35.0.0", "platforms/android-9", "platforms/android-35"):
                os.makedirs(os.path.join(sdk, d))
            for d in ("build-tools/35.0.0", "build-tools/9.0.0"):
                for tool in ("aapt2" + bd.EXE, "d8" + bd.BAT, "zipalign" + bd.EXE, "apksigner" + bd.BAT):
                    open(os.path.join(sdk, d, tool), "w").close()
            for d in ("platforms/android-35", "platforms/android-9"):
                open(os.path.join(sdk, d, "android.jar"), "w").close()
            with mock.patch.object(bd.se, "sdk_roots", return_value=[sdk]), \
                    mock.patch.object(bd, "java_tool", side_effect=lambda n: f"/jdk/bin/{n}"):
                tools = bd.find_tools()
        self.assertIn(os.path.join("build-tools", "35.0.0"), tools["aapt2"])
        self.assertIn("android-35", tools["android.jar"])
        self.assertEqual(tools["javac"], "/jdk/bin/javac")


if __name__ == "__main__":
    unittest.main()

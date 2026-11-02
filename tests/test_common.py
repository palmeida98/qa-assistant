"""scripts/common.py (plan 7.8): reading project.yaml and .env, shared by a
workspace's scripts. Redaction (Redactor) is covered by the tests of
api_request and run_mobile.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import typing
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import api_request
import audit_a11y
import common
import run_mobile


class Common(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text, encoding="utf-8"):
        with open(os.path.join(self.ws, name), "w", encoding=encoding) as f:
            f.write(text)

    def test_env_with_bom_comments_export_and_quotes(self):
        self.write(".env", "# comment\n\nexport A=1\nB = 'two'\nC=\"three = 3\"\nNO_EQUALS\nD=\n",
                   encoding="utf-8-sig")
        self.assertEqual(common.read_workspace_env(self.ws), {"A": "1", "B": "two", "C": "three = 3", "D": ""})

    def test_missing_env_is_empty(self):
        self.assertEqual(common.read_workspace_env(self.ws), {})

    def test_empty_project_is_a_dict_and_missing_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            common.read_project(self.ws)
        self.write("project.yaml", "")
        self.assertEqual(common.read_project(self.ws), {})
        self.write("project.yaml", "project: shop\napi:\n  base_url: https://shop.demo/\n", encoding="utf-8-sig")
        self.assertEqual(common.read_project(self.ws)["api"]["base_url"], "https://shop.demo/")

    def test_a_workspace_from_before_the_translation_says_how_to_convert_it(self):
        self.write("projeto.yaml", "projeto: shop\n")
        with self.assertRaises(FileNotFoundError) as e:
            common.read_project(self.ws)
        self.assertIn("migrate_workspace.py", str(e.exception))

    def test_the_scripts_use_the_common_code(self):
        self.assertIs(api_request.Redactor, common.Redactor)
        self.assertIs(api_request.read_env, common.read_env)
        self.assertEqual(api_request.REDACTED, common.REDACTED)
        self.assertIs(run_mobile.Redactor, common.Redactor)
        self.assertFalse(hasattr(audit_a11y, "api_request"))

    def test_missing_project_keeps_the_scripts_messages(self):
        with self.assertRaises(audit_a11y.ConfigError) as a:
            audit_a11y.configuration(self.ws)
        with self.assertRaises(run_mobile.UsageError) as m:
            run_mobile.configuration(self.ws)
        for e in (a.exception, m.exception):
            self.assertIn("does not exist", str(e))
            self.assertIn("project.yaml", str(e))
        self.assertIsNone(api_request.base_url(self.ws))


class FindAdb(unittest.TestCase):
    """The 11.8 pilot: a GitHub-hosted runner has the SDK (ANDROID_HOME) but not adb on the PATH."""

    def test_on_the_path_first_then_in_the_sdk(self):
        with tempfile.TemporaryDirectory() as sdk:
            os.makedirs(os.path.join(sdk, "platform-tools"))
            adb = os.path.join(sdk, "platform-tools", "adb.exe" if os.name == "nt" else "adb")
            open(adb, "w").close()
            with mock.patch.object(common.shutil, "which", return_value=None),                     mock.patch.dict(os.environ, {"ANDROID_HOME": sdk}):
                self.assertEqual(common.find_adb(), adb)
            with mock.patch.object(common.shutil, "which", return_value="/usr/bin/adb"):
                self.assertEqual(common.find_adb(), "/usr/bin/adb")

    def test_nowhere_is_none(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(common.shutil, "which", return_value=None),                 mock.patch.object(common.os.path, "expanduser", lambda p: p.replace("~", home)),                 mock.patch.dict(os.environ, {"ANDROID_HOME": "", "ANDROID_SDK_ROOT": "", "LOCALAPPDATA": home}):
            self.assertIsNone(common.find_adb())


class Builds(unittest.TestCase):
    """The build a run records and the one a device reports are compared as a name and a code, not as text."""

    def test_the_observed_build_is_named_the_way_a_run_records_it(self):
        self.assertEqual(common.observed_build("pt.demo.app", "1.0.0", "34"), "pt.demo.app 1.0.0 (34)")

    def test_the_same_build_written_in_different_ways(self):
        same = common.same_build
        self.assertTrue(same("pt.demo.app 1.0.0 (34)", "1.0.0 (34)"))
        self.assertTrue(same("1.4.0 (140)", "pt.demo.app 1.4.0 (code 140)"))
        self.assertTrue(same("release build-7", " Release  Build-7 "))
        self.assertFalse(same("1.0.0 (34)", "1.0.0 (33)"))
        self.assertFalse(same("1.0.0 (34)", "1.0.1 (34)"))
        self.assertFalse(same("1.0.0 (34)", "release build-7"))
        self.assertFalse(same("", "1.0.0 (34)"))


class Redaction(unittest.TestCase):
    """Audit findings D-08 and D-09: a flag must not wreck the evidence, and a short secret must not reach it."""

    ENV: typing.ClassVar[dict] = {"HEADLESS": "1", "DEBUG": "true", "PORT": "80", "APP_PIN": "4821", "API_PWD": "ab1",
                                  "TOKEN": "abcd1234efgh"}

    def test_numbers_and_common_words_are_kept(self):
        red = common.Redactor(self.ENV)
        for text in ("total 10 of 148210, debug true, port 8080", "version 1.4.8210", "abcdef"):
            self.assertEqual(red.text(text.replace("1.4.8210", "1.4")), text.replace("1.4.8210", "1.4"))
        self.assertEqual(red.json({"total": 10, "big": 148210, "ok": True}), {"total": 10, "big": 148210, "ok": True})

    def test_a_short_sensitive_value_is_never_shown(self):
        red = common.Redactor(self.ENV)
        self.assertEqual(red.text("pin=4821 pwd: ab1 url?p=4821&x=1"), "pin=«APP_PIN» pwd: «API_PWD» url?p=«APP_PIN»&x=1")
        self.assertEqual(red.json({"code": 4821, "note": "ab1"}), {"code": "«APP_PIN»", "note": "«API_PWD»"})
        self.assertEqual(red.text("148210 and 4821 in one line"), "148210 and «APP_PIN» in one line")

    def test_what_the_caller_names_is_sensitive_whatever_its_name_says(self):
        self.assertEqual(common.Redactor({"QA_USER": "bob"}).text("bob logged in"), "bob logged in")
        self.assertEqual(common.Redactor({"QA_USER": "bob"}, sensitive=["QA_USER"]).text("bob logged in"), "«QA_USER» logged in")
        self.assertEqual(common.Redactor({"X": "1"}, sensitive=True).text("x 1 y"), "x «X» y")

    def test_a_long_value_is_found_inside_other_text(self):
        self.assertEqual(common.Redactor(self.ENV).text("Bearerabcd1234efghZ"), "Bearer«TOKEN»Z")

    def test_a_name_that_only_contains_the_letters_is_not_sensitive(self):
        for name in ("SHIPPING", "PINNED", "COMPASS", "MONKEY"):
            self.assertFalse(common.sensitive_name(name), name)
        for name in ("APP_PIN", "DB_PASS", "api-key", "MY_PASSWORD", "AUTH_TOKEN", "PAT"):
            self.assertTrue(common.sensitive_name(name), name)

    def test_the_interface_and_the_tickets_follow_the_same_rule(self):
        from adapters.tickets.model import Sanitizer
        from qa_ui import runs
        with tempfile.TemporaryDirectory() as ws:
            with open(os.path.join(ws, ".env"), "w", encoding="utf-8") as f:
                f.write("HEADLESS=1" + chr(10) + "APP_PIN=4821" + chr(10) + "NAMED=bob" + chr(10))
            with open(os.path.join(ws, "project.yaml"), "w", encoding="utf-8") as f:
                f.write("project: x" + chr(10) + "api:" + chr(10) + "  user_var: NAMED" + chr(10))
            self.assertEqual(runs.Runner(ws).redactor().text("1 of 10: 4821 bob"), "1 of 10: «APP_PIN» «NAMED»")
        sanitizer = Sanitizer({"HEADLESS": "1", "APP_PIN": "4821", "LABEL": "short"})
        self.assertEqual(sanitizer.text("1 of 10: 4821 short"), "1 of 10: «APP_PIN» short")


class EnvFile(unittest.TestCase):
    """Comments in a .env line (technique-workspaces.md section 7; audit finding D-10)."""

    def read(self, text):
        with tempfile.TemporaryDirectory() as ws:
            path = os.path.join(ws, ".env")
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            return common.read_env(path), common.env_inline_comments(path)

    def test_the_formats(self):
        got, comments = self.read(chr(10).join([
            "# a comment", "PLAIN=abc", "HASH=pa#ss", "START=#abc", "COMMENT=abc # note", "EMPTY= # note", "NOTHING=",
            'DQ="a # b"', "SQ='x#y'", 'DQC="v" # note', "SQC='v' # note", 'OPEN="abc', 'IN=a"b', "export EXP=1 ",
            "CRLF=v\r".replace("\\r", chr(13)), "EQ=a=b"]))
        self.assertEqual(got, {"PLAIN": "abc", "HASH": "pa#ss", "START": "#abc", "COMMENT": "abc", "EMPTY": "", "NOTHING": "",
                               "DQ": "a # b", "SQ": "x#y", "DQC": "v", "SQC": "v", "OPEN": '"abc', "IN": 'a"b', "EXP": "1",
                               "CRLF": "v", "EQ": "a=b"})
        self.assertEqual(comments, ["COMMENT", "EMPTY"])

    def test_what_is_written_is_read_back(self):
        for value in ("plain", "pa#ss", "a # b", " spaced ", '"quoted"', "'q'", "#start", 'a"b', "x=y"):
            got, _ = self.read("V=" + common.env_quoted(value) + chr(10))
            self.assertEqual(got["V"], value)


class WorkspaceLock(unittest.TestCase):
    """Audit finding D-02: two sessions on one workspace read, changed and wrote the same files with nothing between them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        self.path = os.path.join(self.ws, common.LOCK_NAME)

    def tearDown(self):
        self.tmp.cleanup()

    def hold(self, **fields):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "at": time.time(), **fields}, f)

    def test_one_at_a_time_and_released_afterwards(self):
        errors = []

        def second():
            try:
                with common.workspace_lock(self.ws, wait=0.2):
                    pass
            except common.WorkspaceBusy as e:
                errors.append(str(e))

        with common.workspace_lock(self.ws):
            self.assertTrue(os.path.isfile(self.path))
            t = threading.Thread(target=second)
            t.start()
            t.join()
        self.assertEqual(len(errors), 1)
        self.assertIn(str(os.getpid()), errors[0])
        self.assertFalse(os.path.exists(self.path))
        with common.workspace_lock(self.ws, wait=0.2):
            pass

    def test_released_when_the_work_fails(self):
        with self.assertRaises(KeyError), common.workspace_lock(self.ws):
            raise KeyError("x")
        self.assertFalse(os.path.exists(self.path))

    def test_nothing_is_lost_when_several_writers_update_one_file(self):
        counter = os.path.join(self.ws, "counter.txt")
        with open(counter, "w", encoding="utf-8") as f:
            f.write("0")

        def bump():
            with common.workspace_lock(self.ws, wait=30):
                with open(counter, encoding="utf-8") as f:
                    n = int(f.read())
                time.sleep(0.005)
                with open(counter, "w", encoding="utf-8") as f:
                    f.write(str(n + 1))

        threads = [threading.Thread(target=bump) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(counter, encoding="utf-8") as f:
            self.assertEqual(f.read(), "8")

    def test_the_lock_of_a_finished_process_is_taken_over(self):
        done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=False)
        self.hold(pid=int(done.stdout))
        self.assertFalse(common.pid_alive(int(done.stdout)))
        self.assertTrue(common.pid_alive(os.getpid()))
        with common.workspace_lock(self.ws, wait=0.5), open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["pid"], os.getpid())

    def test_another_machines_lock_is_respected_until_its_heartbeat_stops(self):
        self.hold(host="some-other-machine", pid=1)
        with self.assertRaises(common.WorkspaceBusy), common.workspace_lock(self.ws, wait=0.2):
            pass
        old = time.time() - 2 * common.LOCK_STALE_SECONDS
        os.utime(self.path, (old, old))
        with common.workspace_lock(self.ws, wait=0.5):
            pass

    def test_a_lock_nobody_can_read_is_respected_until_its_file_is_old(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("not json")
        with self.assertRaises(common.WorkspaceBusy), common.workspace_lock(self.ws, wait=0.2):
            pass
        old = time.time() - 2 * common.LOCK_STALE_SECONDS
        os.utime(self.path, (old, old))
        with common.workspace_lock(self.ws, wait=0.5):
            pass

    @unittest.skipUnless(os.path.isdir("/proc"), "zombies are read from /proc (Linux)")
    def test_a_zombie_is_not_alive(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:          # ended but not collected: a zombie
            with open(f"/proc/{proc.pid}/stat", encoding="utf-8") as f:
                if f.read().rpartition(")")[2].split()[0] == "Z":
                    break
            time.sleep(0.05)
        self.assertFalse(common.pid_alive(proc.pid))
        proc.wait()

    def test_a_reused_process_id_does_not_hold_the_lock(self):
        self.hold(started="the start of another process")
        with common.workspace_lock(self.ws, wait=0.5), open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["started"], common.process_started(os.getpid()))

    def test_a_live_holder_is_never_taken_over_however_old(self):
        self.hold(started=common.process_started(os.getpid()))
        old = time.time() - 100 * common.LOCK_STALE_SECONDS
        os.utime(self.path, (old, old))
        with self.assertRaises(common.WorkspaceBusy), common.workspace_lock(self.ws, wait=0.2):
            pass
        self.assertTrue(os.path.isfile(self.path))

    def test_a_nested_operation_keeps_the_lock(self):
        with common.workspace_lock(self.ws, wait=0.1):
            with common.workspace_lock(self.ws, wait=0.1):
                self.assertTrue(os.path.isfile(self.path))
            self.assertTrue(os.path.isfile(self.path))      # the inner one did not release it
        self.assertFalse(os.path.exists(self.path))

    def test_nesting_is_per_thread(self):
        errors = []

        def other():
            try:
                with common.workspace_lock(self.ws, wait=0.1):
                    errors.append("entered")
            except common.WorkspaceBusy:
                pass

        with common.workspace_lock(self.ws):
            t = threading.Thread(target=other)
            t.start()
            t.join()
        self.assertEqual(errors, [])

    def test_a_holder_never_removes_a_lock_that_is_not_its_own(self):
        with common.workspace_lock(self.ws):
            self.hold(pid=4, token="someone else")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["token"], "someone else")
        os.remove(self.path)

    def test_many_waiters_on_an_abandoned_lock_exclude_each_other(self):
        done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=False)
        self.hold(pid=int(done.stdout))
        counter = os.path.join(self.ws, "counter.txt")
        with open(counter, "w", encoding="utf-8") as f:
            f.write("0")

        def bump():
            with common.workspace_lock(self.ws, wait=30):
                with open(counter, encoding="utf-8") as f:
                    n = int(f.read())
                time.sleep(0.01)
                with open(counter, "w", encoding="utf-8") as f:
                    f.write(str(n + 1))

        threads = [threading.Thread(target=bump) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(counter, encoding="utf-8") as f:
            self.assertEqual(f.read(), "6")

    def holder_process(self) -> int:
        """A real process holding the lock; its own id, since a virtual environment's python.exe is a launcher."""
        code = ("import os, sys, time; sys.path.insert(0, sys.argv[1]); import common\n"
                "with common.workspace_lock(sys.argv[2]):\n    print(os.getpid(), flush=True); time.sleep(120)\n")
        proc = subprocess.Popen([sys.executable, "-c", code, os.path.join(ROOT, "scripts"), self.ws], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait)
        pid = int(proc.stdout.readline())
        self.addCleanup(lambda: common.pid_alive(pid) and os.kill(pid, signal.SIGTERM))
        return pid

    def test_two_real_processes_and_the_death_of_the_holder(self):
        pid = self.holder_process()
        with self.assertRaises(common.WorkspaceBusy) as busy, common.workspace_lock(self.ws, wait=0.3):
            pass
        self.assertIn(str(pid), str(busy.exception))
        self.assertTrue(os.path.isfile(self.path))          # a live holder's lock stays
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 30            # never a hang: CI found one on Linux, where the dead child was a zombie
        while common.pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(common.pid_alive(pid), "the holder should be dead, or a zombie that holds nothing")
        self.assertTrue(os.path.isfile(self.path))          # it died without releasing it
        with common.workspace_lock(self.ws, wait=5):
            pass
        self.assertFalse(os.path.exists(self.path))

    def test_a_lock_in_a_workspace_that_does_not_exist_is_an_error_not_a_hang(self):
        with self.assertRaises(FileNotFoundError), common.workspace_lock(os.path.join(self.ws, "missing"), wait=0.2):
            pass


class ExtendedLengthPaths(unittest.TestCase):
    """Audit finding D-02: realpath answered in the extended-length form for a path being created at that moment, and
    the guard read it as another drive: 'outside the workspace', for a path that was inside."""

    def test_the_extended_form_is_the_same_path(self):
        bs = chr(92)
        self.assertEqual(common.plain_path(bs * 2 + "?" + bs + "C:" + bs + "Users" + bs + "ws"), "C:" + bs + "Users" + bs + "ws")
        self.assertEqual(common.plain_path(bs * 2 + "?" + bs + "UNC" + bs + "server" + bs + "share"),
                         bs * 2 + "server" + bs + "share")
        self.assertEqual(common.plain_path("/a/b"), "/a/b")
        self.assertEqual(common.plain_path("C:" + bs + "a"), "C:" + bs + "a")

    def test_a_file_being_deleted_is_not_mistaken_for_a_path_outside(self):
        # audit finding D-02: on Windows realpath answers `\?\C:\$Extend\$Deleted\<id>` for a file that is being
        # deleted at that moment, and the guard refused a path that was inside the workspace
        with tempfile.TemporaryDirectory() as ws:
            real = os.path.realpath
            elsewhere = os.path.join(os.path.dirname(ws), "$Extend", "$Deleted", "00AF")

            def answers(p, *a, **k):
                return elsewhere if os.path.basename(p) == "b.json" else real(p, *a, **k)

            with mock.patch.object(common.os.path, "realpath", answers):
                self.assertEqual(common.workspace_path(ws, "runs", "b.json"), os.path.join(ws, "runs", "b.json"))
                self.assertEqual(common.workspace_path(ws, ".qa-assistant.lock"), os.path.join(ws, ".qa-assistant.lock"))

    def test_a_link_as_the_last_part_is_still_followed_and_refused(self):
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as outside:
            link = os.path.join(ws, "runs")
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                if os.name != "nt":
                    self.skipTest("links cannot be created here")
                import _winapi
                _winapi.CreateJunction(outside, link)       # no privilege needed on Windows
            with self.assertRaises(common.OutsideWorkspace):
                common.workspace_path(ws, "runs")
            with self.assertRaises(common.OutsideWorkspace):
                common.workspace_path(ws, "runs", "b.json")

    @unittest.skipUnless(os.name == "nt", "the extended form is a Windows one")
    def test_a_path_answered_in_extended_form_is_still_inside_the_workspace(self):
        with tempfile.TemporaryDirectory() as ws:
            real = os.path.realpath

            def extended(p, *a, **k):
                answer = real(p, *a, **k)
                return common.EXTENDED_PREFIX + answer if os.path.basename(p) == "b.json" else answer

            with mock.patch.object(common.os.path, "realpath", extended):
                self.assertEqual(common.workspace_path(ws, "runs", "b.json"), os.path.join(ws, "runs", "b.json"))


class ReplaceFile(unittest.TestCase):
    """Audit finding D-02: on Windows an indexer or an antivirus holds a new file open for a moment, and os.replace
    answers PermissionError; a write that took no other lock failed for it."""

    def test_a_moment_of_another_process_holding_the_file_is_waited_out(self):
        with tempfile.TemporaryDirectory() as ws:
            src, dst = os.path.join(ws, "a.tmp"), os.path.join(ws, "a")
            with open(src, "w", encoding="utf-8") as f:
                f.write("new")
            real, calls = os.replace, []

            def flaky(a, b):
                calls.append(1)
                if len(calls) < 3:
                    raise PermissionError(13, "Access is denied")
                return real(a, b)

            with mock.patch.object(common.os, "replace", flaky), mock.patch.object(common.time, "sleep", lambda s: None):
                common.replace_file(src, dst)
            self.assertEqual(len(calls), 3)
            with open(dst, encoding="utf-8") as f:
                self.assertEqual(f.read(), "new")

    def test_a_file_that_stays_held_is_an_error_in_the_end(self):
        with mock.patch.object(common.os, "replace", side_effect=PermissionError(13, "Access is denied")),                 mock.patch.object(common.time, "sleep", lambda s: None), self.assertRaises(PermissionError):
            common.replace_file("a", "b", attempts=3)


if __name__ == "__main__":
    unittest.main()

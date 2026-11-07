"""Write integrity found in the audit of 2026-11-06: history/ in any case, temporary files that are never followed
through a link, a lock that never stays empty, reentrancy through another spelling of the workspace, and the
compaction's copies that never write through a link. Fictional workspaces only.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

import common
import compact_context as cc
from common import (
    OutsideWorkspace,
    WorkspaceBusy,
    atomic_write,
    workspace_lock,
    workspace_path,
)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.ws = os.path.join(self.root, "ws")
        os.makedirs(os.path.join(self.ws, "history"))
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write("project: demo-shop\n")
        self.outside = os.path.join(self.root, "outside.txt")
        with open(self.outside, "w", encoding="utf-8") as f:
            f.write("untouched")

    def tearDown(self):
        self.tmp.cleanup()

    def outside_text(self):
        with open(self.outside, encoding="utf-8") as f:
            return f.read()


class History(Base):
    def test_history_is_refused_whatever_its_case(self):
        for parts in (("History", "x.md"), ("HISTORY", "x.md"), ("evidence", "..", "History", "x.md")):
            with self.subTest(parts=parts), self.assertRaises(OutsideWorkspace):
                workspace_path(self.ws, *parts)

    def test_a_folder_that_only_starts_like_history_is_allowed(self):
        self.assertTrue(workspace_path(self.ws, "history_notes", "x.md").endswith("x.md"))


class AtomicWrite(Base):
    def test_a_link_planted_at_the_temporary_name_is_never_followed(self):
        target = workspace_path(self.ws, "runs", "batch_1.json")
        os.makedirs(os.path.dirname(target))
        os.symlink(self.outside, target + ".tmp")
        atomic_write(target, json.dumps({"results": []}))
        self.assertEqual(self.outside_text(), "untouched")
        with open(target, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"results": []})
        self.assertFalse(os.path.islink(target))

    def test_a_stopped_write_leaves_the_old_file_and_no_temporary_file(self):
        target = os.path.join(self.ws, "context.md")
        with open(target, "w", encoding="utf-8") as f:
            f.write("old")
        with mock.patch.object(common.os, "replace", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            atomic_write(target, "new")
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "old")
        self.assertEqual([n for n in os.listdir(self.ws) if n.endswith(".tmp")], [])


class Lock(Base):
    def test_a_lock_that_names_nobody_is_recovered_after_a_short_time(self):
        path = os.path.join(self.ws, common.LOCK_NAME)
        open(path, "w").close()                       # a writer stopped between creating and naming
        old = time.time() - common.LOCK_UNNAMED_SECONDS - 5
        os.utime(path, (old, old))
        with workspace_lock(self.ws, wait=1):
            pass
        self.assertFalse(os.path.exists(path))

    def test_a_fresh_unnamed_lock_is_still_respected(self):
        open(os.path.join(self.ws, common.LOCK_NAME), "w").close()
        with self.assertRaises(WorkspaceBusy), workspace_lock(self.ws, wait=0.2):
            pass

    def test_the_lock_is_never_seen_without_its_holder(self):
        with workspace_lock(self.ws):
            with open(os.path.join(self.ws, common.LOCK_NAME), encoding="utf-8") as f:
                holder = json.load(f)
            self.assertEqual(holder["pid"], os.getpid())

    def test_the_same_workspace_through_a_link_is_the_same_lock_in_one_thread(self):
        link = os.path.join(self.root, "link")
        os.symlink(self.ws, link)
        done = []

        def nested():
            with workspace_lock(self.ws), workspace_lock(link, wait=0.5):
                done.append(True)
        t = threading.Thread(target=nested)
        t.start()
        t.join(5)
        self.assertEqual(done, [True])


class Compaction(Base):
    def context(self):
        lines = ["# Session context", "", "## Work done", ""]
        lines += [f"- {d:02d}.10.2026: entry {d}" for d in range(1, 21)]
        with open(os.path.join(self.ws, "context.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def test_an_archive_that_is_a_link_is_refused_and_nothing_is_written_outside(self):
        self.context()
        folder = os.path.join(self.ws, "archive", "context")
        os.makedirs(folder)
        now = datetime(2026, 11, 6, 10, 0, 0)
        os.symlink(self.outside, os.path.join(folder, f"context_archive_{now.strftime('%Y%m%d')}.md"))
        with self.assertRaises(cc.Usage):
            cc.apply(self.ws, keep=3, now=now)
        self.assertEqual(self.outside_text(), "untouched")

    def test_two_compactions_in_the_same_second_keep_both_copies(self):
        now = datetime(2026, 11, 6, 10, 0, 0)
        self.context()
        first = cc.apply(self.ws, keep=10, now=now)
        self.context()
        second = cc.apply(self.ws, keep=3, now=now)
        befores = sorted(f for f in first["files"] + second["files"] if "_before" in os.path.basename(f))
        self.assertEqual(len(set(befores)), 2)


if __name__ == "__main__":
    unittest.main()

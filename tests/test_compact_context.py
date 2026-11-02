"""scripts/compact_context.py (methodology 12.2; technique-workspaces.md section 7): context.md kept short by moving the
older dated entries, word for word, to archive/context/; everything else stays; a recoverable copy first. Fictional
workspace, no network.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import compact_context as cc
import qa_ui_kit as kit

CONTEXT = """# Session context — Demo Shop
Updated: 2026-11-02 10:20 (L46)

## Current state
**02.11.2026, 10:20 — L46 applied (build 34).** Three cases Passou.
- Redmi: signed in.

**29.10.2026, 19:45 — nothing new.** Waiting for the team.

**29.10.2026, 19:25 — L43 applied.** One rule: never reuse a magic link.
- Detail line of L43.

**26.10.2026, 15:10 — L31 tested.** Not applied.

**20.10.2026 — L26 applied.** Old.

## Execution point (last batch, last case)
**L46 (02.11.2026, build 34): last batch applied** — TC-SHOP-001, 018, 020.

**L43 (29.10.2026, build 33)** — TC-SHOP-045.

**How to resume (any computer)** — `docs/resume.md`:
- `.env` from `.env.example`.
- Build comes from the release.

**L30 (26.10.2026, build 28)** — TC-SHOP-100.

## Work done
- 02.11.2026: L46 on the Redmi.
- 29.10.2026: L43, nested:
  - detail one
  - detail two
- 20.10.2026: L26.

## Decisions made (and who made them)
- 29.10.2026 (user): never change tickets' states.

## Pending work
- (02.11.2026) Decide the retest of TC-SHOP-055.
"""


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = self._tmp.name
        self.write(CONTEXT)

    def write(self, text, newline="\n"):
        with open(os.path.join(self.ws, "context.md"), "w", encoding="utf-8", newline="") as f:
            f.write(text.replace("\n", newline))

    def read(self, *parts):
        with open(os.path.join(self.ws, *parts), encoding="utf-8", newline="") as f:
            return f.read()

    def files(self):
        out = []
        for root, _d, names in os.walk(self.ws):
            out += [os.path.relpath(os.path.join(root, n), self.ws).replace(os.sep, "/") for n in names]
        return sorted(out)


class Dry(Base):
    def test_a_dry_run_says_what_would_move_and_changes_nothing(self):
        before = self.read("context.md")
        done = cc.plan(before, 2)
        names = {r["section"]: r for r in done["report"]}
        self.assertEqual((names["Current state"]["entries"], names["Current state"]["kept"], len(names["Current state"]["moved"])),
                         (5, 2, 3))
        self.assertEqual(len(names["Execution point (last batch, last case)"]["moved"]), 1)       # 4 blocks, 3 dated, keep 2
        self.assertEqual(len(names["Work done"]["moved"]), 1)
        self.assertEqual(self.files(), ["context.md"])
        self.assertEqual(self.read("context.md"), before)

    def test_a_project_can_name_its_own_words_for_a_rule(self):
        text = CONTEXT.replace("One rule: never reuse a magic link.", "A guideline: a link works only once.")
        flagged = lambda words: [m["line"][:19] for r in cc.plan(text, 2, words)["report"] for m in r["moved"]
                                 if m["may_hold_a_rule"]]
        self.assertNotIn("**29.10.2026, 19:25", flagged(()))
        self.assertIn("**29.10.2026, 19:25", flagged(("guideline",)))

    def test_the_rule_in_an_entry_is_flagged_before_the_move(self):
        moved = {m["line"][:19]: m for r in cc.plan(CONTEXT, 2)["report"] for m in r["moved"]}
        self.assertTrue(moved["**29.10.2026, 19:25"]["may_hold_a_rule"])
        self.assertFalse(moved["**26.10.2026, 15:10"]["may_hold_a_rule"])


class Apply(Base):
    def run_apply(self, keep=2, now=datetime(2026, 11, 2, 11, 0, 0)):
        return cc.apply(self.ws, keep, now)

    def test_the_old_entries_move_word_for_word_and_the_rest_stays_byte_for_byte(self):
        original = self.read("context.md")
        done = self.run_apply()
        new = self.read("context.md")
        archive = self.read("archive", "context", "context_archive_20261102.md")
        for entry in ("**29.10.2026, 19:25 — L43 applied.** One rule: never reuse a magic link.\n- Detail line of L43.",
                      "**26.10.2026, 15:10 — L31 tested.** Not applied.", "**20.10.2026 — L26 applied.** Old.",
                      "**L30 (26.10.2026, build 28)** — TC-SHOP-100.", "- 20.10.2026: L26."):
            self.assertIn(entry, archive)
            self.assertNotIn(entry, new)
        self.assertEqual(done["moved"], 5)
        for kept in ("**02.11.2026, 10:20 — L46 applied (build 34).**", "- Redmi: signed in.", "**29.10.2026, 19:45 — nothing new.**",
                     "**L46 (02.11.2026, build 34)", "**L43 (29.10.2026, build 33)** — TC-SHOP-045.",
                     "- 02.11.2026: L46 on the Redmi.", "- 29.10.2026: L43, nested:\n  - detail one\n  - detail two"):
            self.assertIn(kept, new)
        # an entry with no date is a how-to-resume block: it stays, with its lines
        self.assertIn("**How to resume (any computer)** — `docs/resume.md`:\n- `.env` from `.env.example`.\n- Build comes from the release.", new)
        # the other sections and the head are the very same text
        for part in ("# Session context — Demo Shop\nUpdated: 2026-11-02 10:20 (L46)\n", "## Decisions made (and who made them)\n- 29.10.2026 (user): never change tickets' states.\n",
                     "## Pending work\n- (02.11.2026) Decide the retest of TC-SHOP-055.\n"):
            self.assertIn(part, new)
        self.assertLess(len(new), len(original))

    def test_a_recoverable_copy_is_made_first_and_is_the_whole_file(self):
        original = self.read("context.md")
        done = self.run_apply()
        before = [f for f in self.files() if f.endswith("_before.md")]
        self.assertEqual(before, ["archive/context/context_20261102_110000_before.md"])
        self.assertEqual(self.read(*before[0].split("/")), original)
        self.assertEqual(sorted(os.path.relpath(f, self.ws).replace(os.sep, "/") for f in done["files"]),
                         sorted(f for f in self.files() if f.startswith("archive/")))
        self.assertEqual(sorted(f for f in self.files() if not f.startswith("archive/")), ["context.md"])

    def test_a_pointer_closes_each_section_once_and_a_second_run_changes_nothing(self):
        self.run_apply()
        new = self.read("context.md")
        self.assertEqual(new.count(cc.POINTER), 3)                       # the three sections that moved something
        files = self.files()
        again = self.run_apply(now=datetime(2026, 11, 3, 9, 0, 0))
        self.assertEqual(again["moved"], 0)
        self.assertEqual(self.read("context.md"), new)
        self.assertEqual(self.files(), files)                            # no copy, no archive for a run that did nothing

    def test_a_later_run_adds_to_the_archive_and_keeps_one_pointer(self):
        self.run_apply()
        self.write(self.read("context.md").replace("## Current state\n", "## Current state\n**03.11.2026 — new.** Later.\n\n"))
        done = self.run_apply(now=datetime(2026, 11, 3, 9, 0, 0))
        self.assertEqual(done["moved"], 1)
        self.assertEqual(self.read("context.md").count(cc.POINTER), 3)
        self.assertIn("**29.10.2026, 19:45 — nothing new.**", self.read("archive", "context", "context_archive_20261103.md"))

    def test_line_endings_are_kept(self):
        self.write(CONTEXT, "\r\n")
        self.run_apply()
        new = self.read("context.md")
        self.assertNotIn("\n", new.replace("\r\n", ""))
        self.assertIn("\r\n", new)

    def test_nothing_older_than_the_newest_n_means_nothing_to_do(self):
        before = self.read("context.md")
        self.assertEqual(self.run_apply(keep=50)["moved"], 0)
        self.assertEqual((self.read("context.md"), self.files()), (before, ["context.md"]))

    def test_the_tickets_block_and_the_generated_markers_are_untouched(self):
        block = "<!-- analyze-tickets:start (generated) -->\n## Tickets (/analyze-tickets)\n**29.10.2026 — a line inside**\n<!-- analyze-tickets:end -->\n"
        self.write(CONTEXT + "\n" + block)
        self.run_apply()
        self.assertIn(block, self.read("context.md"))


class Command(Base):
    def run_main(self, *args):
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out, \
                mock.patch("sys.stderr", new_callable=__import__("io").StringIO) as err:
            code = cc.main([self.ws, *args])
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_by_default_apply_on_request(self):
        code, out, _ = self.run_main("--keep", "2")
        self.assertEqual(code, 0)
        self.assertIn("Dry run: 5 entries would move", out)
        self.assertIn("[check the rule]", out)
        self.assertEqual(self.files(), ["context.md"])
        code, out, _ = self.run_main("--keep", "2", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("Whole file before:", out)
        self.assertEqual(len(self.files()), 3)
        self.assertIn("already short", self.run_main("--keep", "2", "--apply")[1])

    def test_no_context_and_a_bad_keep_are_usage_errors(self):
        os.remove(os.path.join(self.ws, "context.md"))
        self.assertEqual(self.run_main()[0], 2)
        self.write(CONTEXT)
        self.assertEqual(self.run_main("--keep", "0")[0], 2)

    def test_a_workspace_held_by_another_writer_is_exit_1_and_nothing_is_written(self):
        before = self.read("context.md")
        with mock.patch.dict(os.environ, {"QA_LOCK_WAIT": "0.2"}), kit.held_by_another_writer(self.ws):
            code, _, err = self.run_main("--keep", "2", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("another process", err)
        self.assertEqual((self.read("context.md"), self.files()), (before, ["context.md"]))

    def test_a_link_pointing_out_of_the_workspace_is_refused(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        os.makedirs(os.path.join(self.ws, "archive"))
        try:
            os.symlink(outside, os.path.join(self.ws, "archive", "context"), target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != "nt":
                self.skipTest("links cannot be created here")
            import _winapi
            _winapi.CreateJunction(outside, os.path.join(self.ws, "archive", "context"))
        before = self.read("context.md")
        self.assertEqual(self.run_main("--keep", "2", "--apply")[0], 2)
        self.assertEqual((self.read("context.md"), os.listdir(outside)), (before, []))


if __name__ == "__main__":
    unittest.main()

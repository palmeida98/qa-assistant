#!/usr/bin/env python3
"""Summarises a `python -m unittest -v` log for a CI run and fails when the run proved less than it announces.

    python scripts/ci_test_summary.py unit.log --title "unit tests (tests/)" \
        --allow-skip "needs LibreOffice" --allow-skip "Windows" [--max-skips N] [--summary-file PATH]

What it reports (Markdown, to stdout and appended to --summary-file, or to $GITHUB_STEP_SUMMARY when that is set):
the counts (run, failed, errors, skipped), the skips grouped by reason, and the failing test ids.

Exit code:
  0  every test that ran passed, at least one test ran, and every skip is allowed
  1  a test failed or errored, or NO test ran, or a skip has no allowed reason (--allow-skip is a regular expression,
     searched in the reason; with no --allow-skip any skip is accepted unless --max-skips is given), or there are
     more skips than --max-skips
  2  usage error or unreadable log

A skipped test proved nothing: a suite that is green because its tests were skipped (a missing browser, a missing
LibreOffice) must show up as a failed check, not as an "OK" with a footnote.
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys

RAN = re.compile(r"^Ran (\d+) tests? in ")
RESULT = re.compile(r"^(OK|FAILED)(?: \((.*)\))?\s*$")
SKIP = re.compile(r"\.\.\. skipped (['\"])(.*)\1\s*$")
BAD = re.compile(r"^(FAIL|ERROR): \S+ \(([^)\s]+)\)")


def parse(text: str) -> dict:
    ran = None
    status = None
    counts: dict[str, int] = {}
    reasons: collections.Counter[str] = collections.Counter()
    bad: list[str] = []
    bad_ids: list[str] = []
    for line in text.splitlines():
        m = RAN.match(line)
        if m:
            ran = int(m.group(1))
            continue
        m = RESULT.match(line)
        if m:
            status = m.group(1)
            for part in (m.group(2) or "").split(","):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    if v.isdigit():
                        counts[k] = int(v)
            continue
        m = SKIP.search(line)
        if m:
            reasons[m.group(2)] += 1
            continue
        m = BAD.match(line)
        if m and m.group(2) not in bad_ids:  # a failing sub-test or a teardown error repeats the id
            bad_ids.append(m.group(2))
            bad.append(f"{m.group(1)} {m.group(2)}")
    return {"ran": ran, "status": status, "counts": counts, "reasons": reasons, "bad": bad, "bad_ids": bad_ids}


def read_known(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]


def split_known(info: dict, known: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(known failures still failing, new failures, known ones that no longer fail)."""
    failing = info["bad_ids"]
    return ([i for i in failing if i in known], [i for i in failing if i not in known], [k for k in known if k not in failing])


def evaluate(info: dict, allow: list[str], max_skips: int | None) -> tuple[int, list[str]]:
    problems: list[str] = []
    if info["ran"] is None:
        problems.append("no 'Ran N tests' line: the run did not finish or the log is not a unittest log")
    elif info["ran"] == 0:
        problems.append("0 tests ran: nothing was validated")
    if info["status"] != "OK":
        problems.append("the run did not end with OK" + (f" ({info['status']})" if info["status"] else ""))
    skipped = info["counts"].get("skipped", 0)
    from_lines = sum(info["reasons"].values())
    total_skips = max(skipped, from_lines)
    if allow:
        regexes = [re.compile(a) for a in allow]
        for reason, n in sorted(info["reasons"].items()):
            if not any(r.search(reason) for r in regexes):
                problems.append(f"{n} skip(s) with a reason that is not allowed: {reason}")
        if skipped > from_lines:
            problems.append(f"{skipped - from_lines} skip(s) without a readable reason (the count says {skipped})")
    if max_skips is not None and total_skips > max_skips:
        problems.append(f"{total_skips} skip(s), more than the {max_skips} allowed")
    return (1 if problems else 0), problems


def render(title: str, info: dict, problems: list[str], banner: str | None = None, known: list[str] | None = None) -> str:
    c = info["counts"]
    failed = c.get("failures", 0) + c.get("errors", 0)
    out = [f"### {title}", ""]
    if banner:
        out += [f"**{banner}**", ""]
    out.append(f"- Ran: {info['ran'] if info['ran'] is not None else 'unknown'}; failed or errored: {failed}; "
               f"skipped: {max(c.get('skipped', 0), sum(info['reasons'].values()))}")
    if info["reasons"]:
        out += ["", "Skipped, by reason (a skipped test proved nothing):"]
        out += [f"- {n} × {reason}" for reason, n in sorted(info["reasons"].items())]
    if known is not None:
        still, new, fixed = split_known(info, known)
        out += ["", f"Failing tests: {len(info['bad_ids'])} distinct (the count above also counts repeated errors)",
                f"- known (listed in the baseline): {len(still)}", f"- **new, not in the baseline: {len(new)}**"]
        out += [f"  - NEW {i}" for i in new]
        out += [f"- in the baseline but no longer failing: {len(fixed)}" + (" (remove them from the file)" if fixed else "")]
        out += [f"  - {i}" for i in fixed]
        out += ["", "Known failures:"] + [f"- {i}" for i in still]
    elif info["bad"]:
        out += ["", "Failing tests:"] + [f"- {b}" for b in info["bad"][:50]]
    if problems:
        out += ["", "**This check failed because:**"] + [f"- {p}" for p in problems]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log")
    ap.add_argument("--title", default="unit tests")
    ap.add_argument("--allow-skip", action="append", default=[], metavar="REGEX")
    ap.add_argument("--max-skips", type=int, default=None)
    ap.add_argument("--summary-file", default=None)
    ap.add_argument("--known-failures", default=None, metavar="FILE",
                    help="test ids (one per line) already known to fail: they are listed apart from the new failures; "
                         "the exit code stays 1 while anything fails, so the check never turns green with failures")
    ap.add_argument("--banner", default=None, help="a line shown first in the summary")
    try:
        args = ap.parse_args(argv)
        with open(args.log, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for a in args.allow_skip:
            re.compile(a)
        known = read_known(args.known_failures) if args.known_failures else None
    except (OSError, re.error, SystemExit) as e:
        if isinstance(e, SystemExit) and e.code in (0, None):
            return 0
        print(f"ci_test_summary: {e}" if not isinstance(e, SystemExit) else "ci_test_summary: usage error", file=sys.stderr)
        return 2
    info = parse(text)
    code, problems = evaluate(info, args.allow_skip, args.max_skips)
    md = render(args.title, info, problems, args.banner, known)
    print(md)
    out_file = os.environ.get("GITHUB_OUTPUT")
    if known is not None and out_file:
        still, new, fixed = split_known(info, known)
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(f"known_failures={len(still)}\nnew_failures={len(new)}\nfixed={len(fixed)}\n")
    target = args.summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    for p in problems:
        print(f"::error title={args.title}::{p}" if os.environ.get("GITHUB_ACTIONS") else f"ERROR: {p}", file=sys.stderr)
    return code


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

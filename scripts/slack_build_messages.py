"""Turns the text a Slack connector returns for channel reads into the message list that `build_candidates.py` reads
(phase B, 10.11.2026). READ ONLY and OFFLINE: this script never talks to Slack and has no credentials. The agent reads the
channel with the connector's read tools (within the approved channel and date window), saves the text it got to a file in the
scratchpad, and this script structures it.

Input: the "detailed" output of the connector (`=== Message from NAME <email> (ID) at DATE ===` followed by `Message TS: ...`).
A message WITHOUT an exact `Message TS` is skipped and reported: an approximate or inferred timestamp is never used as an
identifier. The "concise" output has no TS and therefore cannot be used here.

What is kept (the minimum for traceability): channel id, exact TS, whether the author is a bot or a member (never a name, an
e-mail or a user id), the text with mentions reduced to `@member` and e-mail addresses removed, and counts of thread replies
and files. A permalink is built only when `--workspace-domain` is given (the domain is NOT exposed by the connector).

Usage:
    python scripts/slack_build_messages.py raw_read.txt --channel C0XXXXXXX [--workspace-domain acme] [-o messages.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys

HEADER = re.compile(r"^=== Message from (?P<who>.+?) \((?P<uid>[A-Z0-9]+)\) at (?P<when>.+?) ===\s*$")
TS = re.compile(r"^Message TS:\s*(\d{10}\.\d{6})\s*$")
META = re.compile(r"^(Reactions|Thread|Files):\s*(.*)$")
MENTION = re.compile(r"<@[A-Z0-9]+(?:\|[^>]*)?>")
MAILTO = re.compile(r"<mailto:[^>]*>")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def clean(text: str) -> str:
    text = MENTION.sub("@member", text)
    text = MAILTO.sub("[email]", text)
    return EMAIL.sub("[email]", text).replace("&amp;", "&").strip()


def parse(raw: str, channel: str, workspace_domain: str | None = None) -> tuple[list[dict], list[dict]]:
    """Returns (messages, skipped). `skipped` lists the blocks that had no exact TS."""
    blocks, cur = [], None
    for line in raw.splitlines():
        h = HEADER.match(line)
        if h:
            cur = {"uid": h.group("uid"), "when": h.group("when"), "ts": None, "body": [], "replies": 0, "files": 0}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        t = TS.match(line)
        if t and cur["ts"] is None:
            cur["ts"] = t.group(1)
            continue
        m = META.match(line)
        if m:
            if m.group(1) == "Thread":
                n = re.match(r"(\d+) repl", m.group(2))
                cur["replies"] = int(n.group(1)) if n else 0
            elif m.group(1) == "Files":
                cur["files"] += 1
            continue
        cur["body"].append(line)
    messages, skipped = [], []
    for b in blocks:
        if not b["ts"]:
            skipped.append({"when": b["when"], "reason": "no exact Message TS: not usable as an identifier"})
            continue
        link = (f"https://{workspace_domain}.slack.com/archives/{channel}/p{b['ts'].replace('.', '')}"
                if workspace_domain else None)
        messages.append({"text": clean("\n".join(b["body"])), "channel": channel, "ts": b["ts"],
                         "author": "bot" if b["uid"].startswith("B") else "member", "link": link,
                         "sent_at": b["when"], "thread_replies": b["replies"], "files": b["files"]})
    return messages, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Structure connector read output for build_candidates.py (offline, read only)")
    ap.add_argument("raw", help="text file with the connector's detailed output")
    ap.add_argument("--channel", required=True, help="channel id the text was read from")
    ap.add_argument("--workspace-domain", help="Slack workspace domain, only to build permalinks (not exposed by the connector)")
    ap.add_argument("-o", "--output", help="write the JSON here (default: stdout)")
    a = ap.parse_args(argv)
    with open(a.raw, encoding="utf-8-sig") as f:
        messages, skipped = parse(f.read(), a.channel, a.workspace_domain)
    out = json.dumps(messages, ensure_ascii=False, indent=1)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(out + "\n")
    else:
        print(out)
    for s in skipped:
        print(f"SKIPPED ({s['when']}): {s['reason']}", file=sys.stderr)
    print(f"{len(messages)} messages, {len(skipped)} skipped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

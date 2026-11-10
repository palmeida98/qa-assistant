"""Build candidates from announcements, compared with the build OBSERVED on the device (plan Q9, 10.11.2026).

Reads messages (a JSON list of {"text", "channel", "ts", "author", "link"} that the USER pasted or exported; there is NO
access to Slack here) and returns, per platform mentioned, a candidate with THREE things kept apart:

- the **announced release** (the version the message names, for example 1.0.0);
- the **distributed build** (the build number the message states explicitly: «build 38», «(38)», «versionCode 38»);
- the **observed build** (what `device.py check` reads on the device: `--observed "1.0.0 (38)"`).

A release is never taken for a build: a message that names a release and no build number has `build: null`, and the
candidate is "to confirm". It is also "to confirm" when the platform or the environment is missing, or the environment is
production (not authorised by default), or the platform is not the observed one. It "matches" only when platform,
environment, release AND build number are all stated and equal to the observed ones; otherwise it "differs". Tickets or PRs
named in the message are listed as `mentions` and are never proof that a change is in the build.

Nothing is installed, updated or downgraded, and the device is not touched. Usage:
    python scripts/build_candidates.py messages.json --observed "1.0.0 (38)" --platform android [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys

PLATFORMS = (("android", r"\b(android|apk|play\s*store|google\s*play|firebase)\b"), ("ios", r"\b(ios|iphone|testflight)\b"))
ENVIRONMENTS = (("production", r"\b(prod|production|produ[cç][aã]o)\b"),
                ("staging", r"\b(staging|stg|pre-?prod)\b"),
                ("test", r"\b(test|teste|qa)\b"),
                ("dev", r"\b(dev|desenvolvimento|develop)\b"))
CHANNELS = (("testflight", r"\btestflight\b"), ("apk", r"\bapk\b"), ("play store", r"\b(play\s*store|google\s*play)\b"),
            ("firebase", r"\bfirebase\b"))
RELEASE = re.compile(r"(?<![\w.])(\d+\.\d+(?:\.\d+)?)(?![\w.]*\d)")
BUILD_AFTER_RELEASE = re.compile(r"(\d+\.\d+(?:\.\d+)?)\s*\(\s*(\d+)\s*\)")
BUILD_WORD = re.compile(r"\b(?:build|versionCode|version\s+code|c[oó]digo(?:\s+de\s+build)?|code)\s*[:=#]?\s*(\d+)\b", re.IGNORECASE)
MENTION = re.compile(r"\b(?:AB#\d+|PR\s*#?\d+|#\d{3,})\b")


def _first(patterns, text):
    for name, pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return name
    return None


def parse_observed(text: str) -> dict:
    """`1.0.0 (38)` or `release 1.0.0 build 38` -> {"release": "1.0.0", "build": "38"} (either may be None)."""
    m = BUILD_AFTER_RELEASE.search(text or "")
    if m:
        return {"release": m.group(1), "build": m.group(2)}
    r, b = RELEASE.search(text or ""), BUILD_WORD.search(text or "")
    return {"release": r.group(1) if r else None, "build": b.group(1) if b else None}


def _segments(text: str) -> list[tuple[str | None, str]]:
    """One (platform, text) per platform mentioned, from each mention to the next one; a message that names no platform is
    one segment with platform None."""
    marks = []
    for plat, pat in PLATFORMS:
        marks += [(m.start(), plat) for m in re.finditer(pat, text, re.IGNORECASE)]
    marks.sort()
    if not marks:
        return [(None, text)]
    merged = []                 # consecutive mentions of the same platform are one
    for pos, plat in marks:
        if not merged or merged[-1][1] != plat:
            merged.append((pos, plat))
    out = []
    for i, (pos, plat) in enumerate(merged):
        end = merged[i + 1][0] if i + 1 < len(merged) else len(text)
        start = 0 if i == 0 else pos
        out.append((plat, text[start:end]))
    return out


DEPLOY_STAGE = re.compile(r"Deployment on stage\s+<[^|>]*\|([^>]+)>\s+(succeeded|failed|partially succeeded|canceled)", re.IGNORECASE)
DEPLOY_RELEASE = re.compile(r"Release:\s*<[^|>]*\|\s*(Release-\d+)\s*>")
DEPLOY_PIPELINE = re.compile(r"Release pipeline:\s*<[^|>]*\|([^>]+)>")


def deployment(msg: dict) -> dict | None:
    """A release-pipeline notification («Deployment on stage Development succeeded», «Release-33»). It tells that the pipeline
    deployed something to an environment; it is NOT an announcement of an app build, and the release id of the pipeline is
    never an app build number. Returns None when the message is not such a notification."""
    text = str(msg.get("text", "")).replace("&amp;", "&")
    stage, rel = DEPLOY_STAGE.search(text), DEPLOY_RELEASE.search(text)
    if not (stage and rel):
        return None
    pipe = DEPLOY_PIPELINE.search(text)
    name = stage.group(1).strip()
    low = name.lower()
    env = ("dev" if low.startswith("dev") else "production" if low.startswith("prod") else
           "staging" if low.startswith(("stag", "pre")) else "test" if low in ("test", "qa", "uat") else None)
    return {"kind": "deployment", "stage": name, "environment": env, "status": stage.group(2).lower(),
            "pipeline_release_id": rel.group(1), "pipeline": pipe.group(1).strip() if pipe else None,
            "platform": None, "app_build": None,
            "note": "pipeline release id, not an app build number; the platform and the app build are not stated",
            "source": {k: msg.get(k) for k in ("channel", "ts", "author", "link")}}


def deployments(messages: list[dict]) -> list[dict]:
    return [d for d in (deployment(m) for m in messages) if d]


ANCHOR_DISTANCE = 25      # characters between the platform word and the release/build token


def _anchored(seg: str, plat: str, m, release, build) -> bool:
    """True only when the platform word sits right next to the release/build token («Android 1.0.0 (38)», «1.0.0 (38) iOS»).
    A platform named elsewhere in the message (a crash, a PR, a ticket) may describe a fix, not the platform of the build."""
    if m:
        start, end = m.start(), m.end()
    else:
        spans = [x.span() for x in (RELEASE.search(seg), BUILD_WORD.search(seg)) if x]
        start, end = min(a for a, _ in spans), max(b for _, b in spans)
    pat = dict(PLATFORMS)[plat]
    for hit in re.finditer(pat, seg, re.IGNORECASE):
        gap = start - hit.end() if hit.end() <= start else hit.start() - end if hit.start() >= end else 0
        if gap <= ANCHOR_DISTANCE:
            return True
    return False


def candidates(messages: list[dict]) -> list[dict]:
    out = []
    for msg in messages:
        if deployment(msg):          # pipeline notifications are listed by `deployments`, never as build candidates
            continue
        text = str(msg.get("text", ""))
        env_all = _first(ENVIRONMENTS, text)
        for plat, seg in _segments(text):
            m = BUILD_AFTER_RELEASE.search(seg)
            if m:
                release, build = m.group(1), m.group(2)
            else:
                r, b = RELEASE.search(seg), BUILD_WORD.search(seg)
                release, build = (r.group(1) if r else None), (b.group(1) if b else None)
            if release is None and build is None:
                continue
            anchored = plat is not None and _anchored(seg, plat, m, release, build)
            hint = None if (plat is None or anchored) else plat
            plat = plat if anchored else None
            out.append({"platform": plat, "platform_hint": hint, "environment": _first(ENVIRONMENTS, seg) or env_all,
                        "channel": _first(CHANNELS, seg), "release": release, "build": build,
                        "mentions": sorted(set(MENTION.findall(text))),
                        "source": {k: msg.get(k) for k in ("channel", "ts", "author", "link")}})
    return out


def compare(candidate: dict, observed: dict, observed_platform: str | None) -> dict:
    """{"result": "matches" | "differs" | "to confirm", "reasons": [...]}. Never assumes release == build."""
    why = []
    if candidate["platform"] is None:
        why.append("platform not stated" if not candidate.get("platform_hint") else
                   f"platform not stated for the build: the text names {candidate['platform_hint']}, which may describe a fix "
                   f"or a ticket and does not prove the platform of the build")
    elif observed_platform and candidate["platform"] != observed_platform:
        return {"result": "to confirm", "reasons": [f"other platform ({candidate['platform']}); observed is {observed_platform}"]}
    if candidate["environment"] is None:
        why.append("environment not stated")
    elif candidate["environment"] == "production":
        why.append("production is not authorised by default")
    if candidate["release"] is None:
        why.append("release not stated")
    if candidate["build"] is None:
        why.append("build number not stated: the release is not the build")
    if observed.get("build") is None:
        why.append("observed build number unknown")
    if why:
        return {"result": "to confirm", "reasons": why}
    same = candidate["release"] == observed.get("release") and candidate["build"] == observed.get("build")
    return {"result": "matches" if same else "differs",
            "reasons": [] if same else [(f"announced {candidate['release']} ({candidate['build']}) vs observed "
                                         f"{observed.get('release')} ({observed.get('build')})")]}


def report(messages: list[dict], observed_text: str, observed_platform: str | None) -> list[dict]:
    observed = parse_observed(observed_text)
    rows = []
    for c in candidates(messages):
        rows.append({**c, "observed": {**observed, "platform": observed_platform}, **compare(c, observed, observed_platform),
                     "note": "mentions are listed in the message: they do not prove that a change is in the build; "
                             "nothing was installed, updated or downgraded"})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare announced builds with the build observed on the device (read only)")
    ap.add_argument("messages", help="JSON list of {text, channel, ts, author, link}")
    ap.add_argument("--observed", required=True, help='as device.py check prints it, e.g. "1.0.0 (38)"')
    ap.add_argument("--platform", choices=("android", "ios"), help="platform of the observed build")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deployments", action="store_true",
                    help="also list the release-pipeline notifications (kept apart from the build candidates)")
    a = ap.parse_args(argv)
    with open(a.messages, encoding="utf-8-sig") as f:
        messages = json.load(f)
    rows = report(messages, a.observed, a.platform)
    deps = deployments(messages) if a.deployments else []
    if a.json:
        print(json.dumps({"candidates": rows, "deployments": deps} if a.deployments else rows, ensure_ascii=False, indent=1))
        return 0
    for d in deps:
        print(f"DEPLOYMENT  {d['pipeline_release_id']} {d['stage']} {d['status']} ({d['pipeline'] or 'pipeline ?'}) · "
              f"{d['source'].get('link') or d['source'].get('ts') or ''} · not an app build number")
    for r in rows:
        print(f"{r['result'].upper():11} {r['platform'] or '?':8} {r['environment'] or '?':10} release {r['release'] or '?'} "
              f"build {r['build'] or '?'} ({r['channel'] or 'channel ?'}) · {'; '.join(r['reasons']) or 'same release and build as observed'}"
              f" · {r['source'].get('link') or r['source'].get('ts') or ''}")
    if not rows:
        print("No candidate found in the messages.")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

"""
Resolving what was asked, the bounded expansion and the scopes (docs/technique-tickets.md sections 2, 3.1 and 6).
"""

from __future__ import annotations

import re
import urllib.parse
from collections import deque

from adapters.tracker.errors import Conflict, InvalidInput

JIRA_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*")
SOURCE_PREFIX = re.compile(r"([A-Za-z0-9][A-Za-z0-9_-]*):(.+)")
ADO_PATH = re.compile(r"/(?:_workitems/edit|_apis/wit/workitems)/(\d+)", re.IGNORECASE)
JIRA_PATH = re.compile(r"/browse/([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)")
LINEAR_PATH = re.compile(r"/issue/([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)(?:/|$)")
KEY_SERVICES = ("jira", "linear")      # services whose tickets are keys like ABC-123
JIRA_PARAM = re.compile(r"[?&](?:selectedIssue|issueKey)=([A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*)")


def _resolve_url(config, text: str) -> tuple[str, str]:
    """A link only ever resolves to a configured source: a token is never sent to another host."""
    u = urllib.parse.urlsplit(text)
    matches = []
    for source in config.sources.values():
        base = urllib.parse.urlsplit(source.base_url)
        same = u.netloc.lower() == base.netloc.lower() and u.path.lower().startswith(base.path.lower().rstrip("/") + "/")
        if not same:
            continue
        if source.service == "azure-devops":
            match = ADO_PATH.search(u.path)
        elif source.service == "linear":
            match = LINEAR_PATH.search(u.path)
        else:
            match = JIRA_PATH.search(u.path) or JIRA_PARAM.search(text)
        if match:
            matches.append((source, match.group(1)))
    # two sources can share an organisation: the project in the link decides
    exact = [m for m in matches if f"/{urllib.parse.quote(str(m[0].cfg.get('project')))}/".lower() in u.path.lower()]
    if exact or matches:
        source, tid = (exact or matches)[0]
        return source.id, tid.upper() if source.service == "linear" else tid
    raise InvalidInput(f"{text}: not a ticket link of a configured source (tickets.sources); a link to another "
                       "host is never opened with a credential")


def _key_candidates(config, prefix: str) -> list[str]:
    """The sources a key ABC-123 may belong to. Within one service, the old rule: the sources that declare the prefix,
    else all of that service. Across services (Jira and Linear), a source of another service that declares no prefix
    could hold the key too, so it stays a candidate: the ambiguity is asked, never resolved by guessing."""
    keyed = [s for s in config.sources.values() if s.service in KEY_SERVICES]
    exact = [s for s in keyed if prefix in s.key_prefixes()]
    if not exact:
        return [s.id for s in keyed]
    services = {s.service for s in exact}
    open_elsewhere = [s for s in keyed if s.service not in services and not s.key_prefixes()]
    return [s.id for s in exact + open_elsewhere]


def resolve_ref(config, text: str) -> tuple[str, str]:
    """(source id, ticket id) of a link, an id, a key or `<source>:<id>`. Ambiguity stops: it is never guessed."""
    text = str(text).strip()
    if re.match(r"https?://", text, re.IGNORECASE):
        return _resolve_url(config, text)
    prefixed = SOURCE_PREFIX.fullmatch(text)
    if prefixed and prefixed.group(1) in config.sources:
        return prefixed.group(1), prefixed.group(2)
    if text.isdigit():
        candidates = [s.id for s in config.sources.values() if s.service == "azure-devops"]
    elif JIRA_KEY.fullmatch(text):
        text = text.upper()
        candidates = _key_candidates(config, text.rsplit("-", 1)[0])
    else:
        raise InvalidInput(f"{text!r} is not a link, an id, a key or <source>:<id>")
    if not candidates:
        raise InvalidInput(f"{text}: no configured source of that service (tickets.sources)")
    if len(candidates) > 1:
        raise Conflict(f"{text}: ambiguous between the sources {candidates}: write <source>:{text}")
    return candidates[0], text


def expand(source, roots: list[str], limits: dict, follow: bool = True) -> tuple[list[dict], list[dict]]:
    """Reads the roots and their context. Returns (entries {snapshot, role, depth, via}, omitted {id, from, reason}).
    `source.fetch` returns a bundle, or None when a browser bundle lacks that ticket."""
    queue = deque((str(r), 0, "root", None) for r in roots)
    seen: dict[str, dict] = {}
    omitted: dict[str, dict] = {}

    def omit(target, origin, reason):
        omitted.setdefault(str(target), {"id": str(target), "from": origin, "reason": reason})

    while queue:
        tid, depth, role, via = queue.popleft()
        if tid in seen:
            continue
        if len(seen) >= limits["max_items"]:
            omit(tid, via, "max-items")
            continue
        bundle = source.fetch(tid)
        if bundle is None:
            omit(tid, via, "not-provided")
            continue
        snap = source.normalize(bundle)
        seen[tid] = {"snapshot": snap, "role": role, "depth": depth, "via": via}
        if not follow:
            # listed, not read: a change in a child (a task Done, a comment) does not change this ticket's revision,
            # so what was not expanded is said (audit 06.11.2026: it was left out without a word)
            for rel in snap["components"]["relations"]:
                if not rel.get("external") and str(rel["target"]) not in seen:
                    omit(rel["target"], tid, "not-expanded")
            continue
        for rel in snap["components"]["relations"]:
            target = str(rel["target"])
            if target in seen:          # a cycle, or a ticket already read
                continue
            if rel.get("external"):
                omit(target, tid, "external")
                continue
            kind = rel["kind"]
            if kind == "parent":
                wanted, next_role = True, "parent"
            elif kind == "child":
                wanted, next_role = role in ("root", "parent"), "child" if role == "root" else "sibling"
            else:
                wanted, next_role = role == "root", "related"
            if not wanted:
                omit(target, tid, "not-followed")
            elif depth >= limits["max_depth"]:
                omit(target, tid, "max-depth")
            else:
                queue.append((target, depth + 1, next_role, tid))
    return list(seen.values()), [o for o in omitted.values() if o["id"] not in seen]


class BundleSource:
    """The browser route: `fetch` answers from a bundle, so the expansion and the normaliser are the same."""

    def __init__(self, tickets: dict[str, dict], normalize):
        self.tickets = tickets
        self.normalize_fn = normalize

    def fetch(self, tid: str):
        return self.tickets.get(str(tid))

    def normalize(self, bundle: dict) -> dict:
        return self.normalize_fn(bundle)

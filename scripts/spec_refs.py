"""
The specification references (`spec_refs`) of a plan: cite a rule, and compare a plan with the specification available
on a resume (docs/technique-specs.md sections 4 and 5; /test-pbi step 3).

    python scripts/spec_refs.py <workspace> cite <path>#<rule id> [<path>#<rule id> ...] [--spec-root DIR]
    python scripts/spec_refs.py <workspace> compare <plan> [--spec-root DIR] [--json]

The plan (a Markdown file of the workspace, or a .json) holds its references in one fenced block:

    ```json spec_refs
    {"source": {"kind": "export", "revision": "<snapshot revision>"},
     "scenarios": {"S1": {"title": "...", "refs": [{"path": "specs/a.md", "rule_id": "r-1a2b3c4d",
                                                       "file_revision": "...", "text": "...", "text_sha256": "..."}]}}}
    ```

`cite` prints such references with the rule's text copied from the specification, so a plan never retypes it.
A scenario may also be a plain list of references (the shape of the first pilot); a reference with only a
`text_sha256` is compared by that hash.

`compare` reads the same rules in the specification now available and says, per scenario:

- reusable: every rule it cites has the same text, even when the file's revision changed;
- to review: a rule it cites changed, was removed from its file or moved to another one;
- pending review (not verified): something could not be checked (the source cannot be read, a file or a rule could
  not be read, the plan did not record the text or the hash of the version it used). Never reported as unchanged.

A rule is the text of its anchored line plus the lines indented below it (its sub-rules), compared as written
(line ends and trailing spaces aside). The text of a rule is shown word for word when it is short; a long one is
named by file, rule id and revision (rules.md section 17). Only `specs.kind: export` is read by this script: another
kind is "not verified" and its scenarios wait for a review. A workspace without `specs:` (and no --spec-root) has
nothing to compare and the flow stays as it was; a plan without references says so.

Exit codes: 0 nothing to review (or nothing to compare) · 1 a rule changed or was removed · 3 not concluded (something
could not be verified) · 2 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

DEFAULT_RULE_ID = r"r-[0-9a-f]{8}"
LONG_RULE = 400  # characters; a longer rule is cited by reference, never retyped in a report
BLOCK = re.compile(r"```json[ \t]+spec_refs[ \t]*\n(.*?)\n```", re.DOTALL)
LEGACY_BLOCK = re.compile(r"```json[ \t]*\n(\{.*?\"scenarios\".*?\})\n```", re.DOTALL)
NUMBERING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[-*+])\s+")


class SpecError(Exception):
    pass


def normalise(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def digest(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def rule_block(markdown: str, rule_id: str) -> str | None:
    """The text of one rule: its anchored line (numbering and anchor removed) and the lines indented below it."""
    anchor = re.compile(r'<a id="' + re.escape(rule_id) + r'"></a>')
    lines = markdown.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        m = anchor.search(line)
        if not m:
            continue
        head = anchor.sub("", NUMBERING.sub("", line, count=1) if NUMBERING.match(line) else line, count=1).strip()
        base, body = _indent(line), [head]
        for nxt in lines[i + 1:]:
            if nxt.strip() and _indent(nxt) <= base:
                break
            body.append(nxt.rstrip())
        return "\n".join(body).rstrip()
    return None


class Source:
    """The specification now available. `available` False says why in `reason`; nothing is guessed."""

    def __init__(self, kind: str | None, root: str | None, rule_id: str = DEFAULT_RULE_ID):
        self.kind, self.root, self.rule_id = kind, root, rule_id
        self.available, self.reason = True, ""
        self.revisions: dict[str, str] = {}
        self.snapshot: str | None = None
        if kind != "export":
            self.available, self.reason = False, (
                f"specs.kind {kind!r} cannot be read by this script (only a local export): read the specification "
                "as technique-specs.md says")
        elif not root or not os.path.isdir(root):
            self.available, self.reason = False, f"the export folder {root!r} is not accessible"
        else:
            self._manifest()

    def _manifest(self) -> None:
        path = os.path.join(self.root, "MANIFEST.json")
        try:
            with open(path, encoding="utf-8") as f:
                m = json.load(f)
            self.revisions = {x["path"]: x.get("file_revision") for x in m.get("files", []) if x.get("path")}
            self.snapshot = (m.get("snapshot") or {}).get("revision")
        except (OSError, ValueError, TypeError, KeyError):
            pass  # no manifest: the text is still compared; the revision is just unknown

    def revision_of(self, path: str) -> str | None:
        return self.revisions.get(path)

    def read(self, path: str) -> str:
        """The file's Markdown; FileNotFoundError when it is not in this version; SpecError when it cannot be read."""
        full = os.path.normpath(os.path.join(self.root, path))
        if os.path.commonpath([os.path.realpath(self.root), os.path.realpath(full)]) != os.path.realpath(self.root):
            raise SpecError(f"{path}: outside the specification folder")
        try:
            with open(full, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            raise
        except (OSError, UnicodeDecodeError) as e:
            raise SpecError(f"{path}: cannot be read ({type(e).__name__})") from None

    def find(self, rule_id: str, skip: str) -> str | None:
        """The file that holds the rule when it is no longer in the file the plan cited."""
        for base, _, files in os.walk(self.root):
            for name in sorted(files):
                rel = os.path.relpath(os.path.join(base, name), self.root).replace(os.sep, "/")
                if not name.endswith(".md") or rel == skip:
                    continue
                try:
                    if rule_block(self.read(rel), rule_id) is not None:
                        return rel
                except (SpecError, FileNotFoundError):
                    continue
        return None


def source_from(workspace: str, root_override: str | None = None) -> Source | None:
    """The source of the workspace's `specs:` (None when it has none and no folder was given)."""
    from common import read_project
    try:
        specs = (read_project(workspace) or {}).get("specs")
    except Exception:  # noqa: BLE001 - an unreadable project.yaml is the caller's error, not a spec source
        specs = None
    if not isinstance(specs, dict) and not root_override:
        return None
    specs = specs if isinstance(specs, dict) else {}
    rule_id = str(specs.get("rule_id") or DEFAULT_RULE_ID)
    kind = "export" if root_override else specs.get("kind")
    location = root_override or specs.get("location")
    root = None
    if location:
        root = location if os.path.isabs(location) else os.path.join(workspace, location)
    return Source(kind, root, rule_id)


def plan_refs(path: str) -> dict | None:
    """The `spec_refs` document of a plan, normalised to {"source": {...}, "scenarios": {id: {"title", "refs"}}}.
    None when the plan has no references. SpecError when the plan or the block cannot be read."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise SpecError(f"the plan {path} cannot be read ({type(e).__name__})") from None
    raw = None
    if path.endswith(".json"):
        try:
            raw = json.loads(text)
        except ValueError as e:
            raise SpecError(f"{path}: not valid JSON ({e})") from None
    else:
        m = BLOCK.search(text) or LEGACY_BLOCK.search(text)
        if m:
            try:
                raw = json.loads(m.group(1))
            except ValueError as e:
                raise SpecError(f"{path}: the spec_refs block is not valid JSON ({e})") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("scenarios"), dict) or not raw["scenarios"]:
        return None
    scenarios = {}
    for sid, val in raw["scenarios"].items():
        refs = val.get("refs") if isinstance(val, dict) else val
        title = val.get("title", "") if isinstance(val, dict) else ""
        if not isinstance(refs, list):
            raise SpecError(f"{path}: scenario {sid} has no list of references")
        scenarios[str(sid)] = {"title": title, "refs": [r for r in refs if isinstance(r, dict)]}
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return {"source": source, "scenarios": scenarios, "snapshot_revision": raw.get("snapshot_revision")}


def cite(source: Source, path: str, rule_id: str) -> dict:
    """One reference, with the text copied from the specification (never retyped)."""
    if not source.available:
        raise SpecError(source.reason)
    try:
        text = rule_block(source.read(path), rule_id)
    except FileNotFoundError:
        raise SpecError(f"{path}: not in the specification") from None
    if text is None:
        raise SpecError(f"{path}: rule {rule_id} not found")
    ref = {"path": path, "rule_id": rule_id, "text": text, "text_sha256": digest(text)}
    rev = source.revision_of(path)
    if rev:
        ref["file_revision"] = rev
    return ref


def judge(source: Source, ref: dict) -> dict:
    """The state of one referenced rule now: same | same-text-new-revision | changed | removed | moved | unverified."""
    path, rid = ref.get("path"), ref.get("rule_id")
    out = {"path": path, "rule_id": rid, "old_revision": ref.get("file_revision"), "new_revision": None,
           "old_text": ref.get("text"), "new_text": None}
    if not path or not rid:
        return {**out, "status": "unverified", "reason": "the reference has no path or rule id"}
    if not source.available:
        return {**out, "status": "unverified", "reason": source.reason}
    old_hash = ref.get("text_sha256") or (digest(ref["text"]) if ref.get("text") else None)
    if not old_hash:
        return {**out, "status": "unverified",
                "reason": "the plan did not record the text or the hash of the version it used"}
    out["new_revision"] = source.revision_of(path)
    try:
        now = rule_block(source.read(path), rid)
    except FileNotFoundError:
        now, missing_file = None, True
    except SpecError as e:
        return {**out, "status": "unverified", "reason": str(e)}
    else:
        missing_file = False
    if now is None:
        elsewhere = source.find(rid, path)
        if elsewhere:
            return {**out, "status": "moved", "moved_to": elsewhere,
                    "reason": f"the rule is no longer in {path}; it is in {elsewhere}"}
        return {**out, "status": "removed",
                "reason": f"{path} is not in this version" if missing_file else f"rule {rid} is not in {path}"}
    out["new_text"] = now
    if digest(now) != old_hash:
        return {**out, "status": "changed", "reason": "the text of the rule changed"}
    same_rev = (out["old_revision"] or out["new_revision"]) is None or out["old_revision"] == out["new_revision"]
    return {**out, "status": "same" if same_rev else "same-text-new-revision",
            "reason": "same text" + ("" if same_rev else "; only the revision of the file changed")}


def compare(plan: dict, source: Source) -> dict:
    """{"rules": [...], "scenarios": {id: {"state": reusable|review|pending, "rules": [...]}}, "source": {...}}."""
    rules, scenarios = {}, {}
    for sid, sc in plan["scenarios"].items():
        states = []
        for ref in sc["refs"]:
            j = judge(source, ref)
            key = (j["path"], j["rule_id"])
            entry = rules.setdefault(key, {**j, "scenarios": []})
            entry["scenarios"].append(sid)
            states.append(j["status"])
        if any(s in ("changed", "removed", "moved") for s in states):
            state = "review"
        elif any(s == "unverified" for s in states) or not states:
            state = "pending"
        else:
            state = "reusable"
        scenarios[sid] = {"title": sc["title"], "state": state,
                          "rules": [r["rule_id"] for r in sc["refs"] if r.get("rule_id")]}
    return {"rules": list(rules.values()), "scenarios": scenarios,
            "source": {"kind": source.kind, "available": source.available, "reason": source.reason,
                       "snapshot": source.snapshot, "plan_snapshot": plan.get("snapshot_revision")
                       or plan["source"].get("revision")}}


def _quote(text: str | None) -> str:
    if text is None:
        return "(not recorded)"
    if len(text) > LONG_RULE:
        return f"(long rule, {len(text.splitlines())} lines: see the specification by file, rule id and revision)"
    return "«" + text + "»"


def render(result: dict) -> list[str]:
    lines = []
    sc = result["scenarios"]
    src = result["source"]
    if not src["available"]:
        return [f"NOT VERIFIED: {src['reason']}; no rule of the plan was compared",
                "Scenarios pending review (could not be verified): " + ", ".join(sc)]
    review = [s for s, v in sc.items() if v["state"] == "review"]
    pending = [s for s, v in sc.items() if v["state"] == "pending"]
    reuse = [s for s, v in sc.items() if v["state"] == "reusable"]
    renamed = [r["rule_id"] for r in result["rules"] if r["status"] == "same-text-new-revision"]
    if renamed:
        lines.append("Same text, another revision of the file (nothing to review): " + ", ".join(renamed))
    for r in result["rules"]:
        if r["status"] in ("same", "same-text-new-revision"):
            continue
        ref = f"{r['path']} · {r['rule_id']} · {r['old_revision'] or '?'} -> {r['new_revision'] or '?'}"
        who = ", ".join(r["scenarios"])
        if r["status"] == "unverified":
            lines.append(f"NOT VERIFIED {ref}: {r['reason']}; scenarios {who}")
            continue
        lines.append(f"{r['status'].upper()} {ref}: {r['reason']}; scenarios {who}")
        if r["status"] == "changed":
            lines.append(f"  before {_quote(r['old_text'])}")
            lines.append(f"  now    {_quote(r['new_text'])}")
    if review:
        lines.append("Scenarios to review BEFORE the plan is reused: " + ", ".join(review))
    if pending:
        lines.append("Scenarios pending review (could not be verified): " + ", ".join(pending))
    if reuse:
        lines.append("Scenarios reusable as they are (every cited rule has the same text): " + ", ".join(reuse))
    return lines


def exit_code(result: dict) -> int:
    states = {v["state"] for v in result["scenarios"].values()}
    return 1 if "review" in states else (3 if "pending" in states else 0)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python scripts/spec_refs.py", description="spec_refs of a plan.")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("cite", help="references with the rule text copied from the specification")
    p.add_argument("rules", nargs="+", help="<path>#<rule id>")
    p.add_argument("--spec-root")
    p = sub.add_parser("compare", help="a plan's references against the specification now available")
    p.add_argument("plan")
    p.add_argument("--spec-root")
    p.add_argument("--json", action="store_true")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    ws = os.path.abspath(a.workspace)
    try:
        source = source_from(ws, a.spec_root)
        if a.command == "cite":
            if source is None:
                raise SpecError("the workspace has no specs: section (and no --spec-root)")
            refs = []
            for item in a.rules:
                path, _, rid = item.partition("#")
                if not path or not rid:
                    raise SpecError(f"{item!r}: expected <path>#<rule id>")
                refs.append(cite(source, path, rid))
            print(json.dumps(refs, ensure_ascii=False, indent=1))
            return 0
        plan_path = a.plan if os.path.isabs(a.plan) else os.path.join(ws, a.plan)
        plan = plan_refs(plan_path)
        if plan is None:
            print("no spec_refs in the plan: nothing to compare")
            return 0
        if source is None:
            print("the workspace has no specs: section: nothing to compare")
            return 0
        result = compare(plan, source)
        if a.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print("\n".join(render(result)) or "nothing to review")
        return exit_code(result)
    except SpecError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

"""
Syncs the repository's skills (.claude/skills/) with the global copy
(~/.claude/skills/), which is the one Claude Code loads in the workspaces: a
workspace lives outside this repository and does not see its project skills.

    python scripts/sync_skills.py           (only checks; never writes)
    python scripts/sync_skills.py --apply   (copies from the repository to the global copy)

The source of truth is always the repository: the global copy never comes back
here. Differences only in line endings (CRLF/LF) do not count. Global skills
that do not exist in the repository (from other sources) are ignored, except the
ones this framework used to install under their old names (plan 8.11), which
--apply removes.

--apply also writes .qa-assistant-source.json next to the copies: the checkout
they came from (root, branch, commit), so a skill finds the code that matches it
(technique-workspaces.md section 9). It refuses a skill whose requires-modules or
requires-scripts are not in this checkout, and only SKILL.md is ever copied. It
never merges anything: syncing from a worktree or another branch is said aloud.

Exit codes: 0 in sync (or copied with --apply) · 1 differences to apply ·
2 no skills in the repository, or a skill whose code is not in this checkout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, ".claude", "skills")
TARGET = os.path.join(os.path.expanduser("~"), ".claude", "skills")
RECORD = ".qa-assistant-source.json"
DEFAULT_BRANCHES = ("main", "master")
# the framework's skills before the translation (plan 8.11): removed from the global copy
RETIRED = ("onboarding-projeto", "analisar-funcionalidade", "gerar-casos", "planear-lote", "executar-lote",
           "registar-bug", "fim-sessao", "painel", "testar-api", "auditar-acessibilidade", "testar-mobile")


def _text(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


def compare(source: str = SOURCE, target: str = TARGET) -> dict[str, str]:
    """{skill: "same" | "different" | "missing" | "retired"} for each repository skill, plus the retired ones
    still in the global copy."""
    state = {}
    for name in sorted(os.listdir(source)):
        src = os.path.join(source, name, "SKILL.md")
        if not os.path.isfile(src):
            continue
        copy = _text(os.path.join(target, name, "SKILL.md"))
        state[name] = "missing" if copy is None else ("same" if copy == _text(src) else "different")
    for name in RETIRED:
        if name not in state and os.path.isdir(os.path.join(target, name)):
            state[name] = "retired"
    return state


def apply(state: dict[str, str], source: str = SOURCE, target: str = TARGET) -> list[str]:
    done = []
    for name, st in state.items():
        if st == "retired":
            shutil.rmtree(os.path.join(target, name))
            done.append(f"{name} (removed)")
        elif st != "same":
            os.makedirs(os.path.join(target, name), exist_ok=True)
            shutil.copyfile(os.path.join(source, name, "SKILL.md"), os.path.join(target, name, "SKILL.md"))
            done.append(name)
    return done


# --- a skill's contract: its frontmatter, the single source (technique-workspaces.md section 8) -----------------------

def skill_doc(path: str) -> dict:
    """The frontmatter of a SKILL.md ({} when it has none)."""
    import yaml
    text = _text(path)
    m = re.match(r"^---\n(.*?)\n---\n", text or "", re.DOTALL)
    doc = yaml.safe_load(m.group(1)) if m else None
    return doc if isinstance(doc, dict) else {}


def listed(doc: dict, key: str) -> list[str]:
    """A comma-separated value of the skill's metadata, as a list."""
    value = (doc.get("metadata") or {}).get(key)
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def module_exists(root: str, dotted: str) -> bool:
    """A module of the framework in this checkout, looked at as files: nothing is imported."""
    rel = os.path.join(*dotted.split("."))
    return any(os.path.isfile(os.path.join(root, *base, rel, "__init__.py")) or
               os.path.isfile(os.path.join(root, *base, rel + ".py")) for base in ((), ("src",)))


def missing_code(root: str, doc: dict) -> list[str]:
    """What a skill says it needs and this checkout does not have."""
    return ([f"module {m}" for m in listed(doc, "requires-modules") if not module_exists(root, m)]
            + [f"script {s}" for s in listed(doc, "requires-scripts") if not os.path.isfile(os.path.join(root, s))])


def skill_hash(path: str) -> str:
    return hashlib.sha256((_text(path) or "").encode("utf-8")).hexdigest()[:16]


# --- the record of where the copies came from ----------------------------------------------------------------------

def read_record(target: str = TARGET) -> dict | None:
    try:
        with open(os.path.join(target, RECORD), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def same_path(a, b) -> bool:
    return bool(a and b) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def source_record(root: str = ROOT, source: str = SOURCE) -> dict:
    from common import git_state, main_checkout
    main = main_checkout(root)
    return {"root": os.path.realpath(root), "main_checkout": os.path.realpath(main), "worktree": not same_path(root, main),
            **git_state(root), "synced_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "skills": {name: skill_hash(os.path.join(source, name, "SKILL.md")) for name in sorted(os.listdir(source))
                       if os.path.isfile(os.path.join(source, name, "SKILL.md"))}}


def write_record(target: str, record: dict) -> None:
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, RECORD)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Syncs .claude/skills/ with ~/.claude/skills/.")
    ap.add_argument("--apply", action="store_true", help="copy the different or missing skills, remove retired ones, "
                                                         "and record this checkout as their source")
    ap.add_argument("--target", default=TARGET, help="folder of the global skills (default ~/.claude/skills)")
    a = ap.parse_args(argv)
    if not os.path.isdir(SOURCE):
        print(f"ERROR: {SOURCE} does not exist", file=sys.stderr)
        return 2
    state = compare(SOURCE, a.target)
    if not any(st != "retired" for st in state.values()):
        print(f"ERROR: no skill in {SOURCE}", file=sys.stderr)
        return 2
    for name, st in state.items():
        print(f"{st:9}  {name}")
    pending = [n for n, st in state.items() if st != "same"]
    if not a.apply:
        record = read_record(a.target)
        if record and not same_path(record.get("root"), ROOT):
            print(f"note: the global copy was last synced from {record.get('root')} "
                  f"(branch {record.get('branch')}, commit {record.get('commit')}), not from this checkout")
        if not pending:
            print(f"{len(state)} skill(s) in sync with {a.target}")
            return 0
        print(f"{len(pending)} skill(s) to sync: run with --apply")
        return 1
    return _apply(state, pending, a.target)


def _apply(state: dict[str, str], pending: list[str], target: str) -> int:
    try:
        broken = {n: missing_code(ROOT, skill_doc(os.path.join(SOURCE, n, "SKILL.md"))) for n, st in state.items()
                  if st != "retired"}
    except ImportError as e:
        print(f"ERROR: {e}: run `pip install -r requirements.txt` first (nothing was copied)", file=sys.stderr)
        return 2
    broken = {n: m for n, m in broken.items() if m}
    if broken:
        for name, missing in broken.items():
            print(f"ERROR: {name} needs {', '.join(missing)}, not in this checkout ({ROOT})", file=sys.stderr)
        print("Nothing was copied: an installed skill must find its code.", file=sys.stderr)
        return 2
    done = apply(state, SOURCE, target) if pending else []
    record = source_record()
    write_record(target, record)
    print(f"applied to {target}: {', '.join(done)}" if done else f"{len(state)} skill(s) in sync with {target}")
    print(f"recorded as their source: {record['root']} (branch {record['branch']}, commit {record['commit']}"
          f"{', uncommitted changes' if record['dirty'] else ''})")
    if record["worktree"] or (record["branch"] and record["branch"] not in DEFAULT_BRANCHES):
        print("warning: this is a worktree or another branch: the workspaces run this checkout's code until the next "
              "sync; nothing was merged")
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    sys.exit(main())

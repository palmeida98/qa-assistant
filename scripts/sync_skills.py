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

Exit codes: 0 in sync (or copied with --apply) · 1 differences to apply ·
2 no skills in the repository
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, ".claude", "skills")
TARGET = os.path.join(os.path.expanduser("~"), ".claude", "skills")
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Syncs .claude/skills/ with ~/.claude/skills/.")
    ap.add_argument("--apply", action="store_true", help="copy the different or missing skills, remove retired ones")
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
    if not pending:
        print(f"{len(state)} skill(s) in sync with {a.target}")
        return 0
    if not a.apply:
        print(f"{len(pending)} skill(s) to sync: run with --apply")
        return 1
    done = apply(state, SOURCE, a.target)
    print(f"applied to {a.target}: {', '.join(done)}")
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

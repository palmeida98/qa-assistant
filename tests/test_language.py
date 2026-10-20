"""Everything in this repository is in English (plan 8.11): a common Portuguese word in a framework file fails.

python -m unittest discover -s tests   (from the root of qa-assistant)
The old names and keys that scripts/migrate_workspace.py converts (and the adapter still reads in old workbooks)
are the only Portuguese that may appear, and only as those exact tokens: they are removed before the check.
A project's own names (sheets, columns, statuses) never live in this repository, so they do not need an exception.
"""

import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import migrate_workspace as mw

WORDS = re.compile(
    r"\b(não|nao|também|tambem|ficheiros?|utilizador(?:es)?|através|está|estão|são|então|porque|secção|seccao|"
    r"metodologia|técnica|tecnica|lotes?|defeitos?|página|pagina|obrigatório|registo|contexto|projeto|painel|"
    r"esperado|obtido|casos?|uma|para|pelo|pela|resultado|decisão|utilizar|correr|verificar)\b",
    re.IGNORECASE)
TEXT = (".md", ".py", ".yaml", ".yml", ".json", ".ts", ".toml", ".txt", ".ps1", ".sh", ".cfg", ".html", ".xml")
SKIP = ("src/qa_audit/modules/accessibility/vendor/", "examples/demo/e2e/package-lock.json", ".secrets.baseline")
# files whose job is the old vocabulary: the conversion itself, its test, and the list of retired skill names; and
# the headers of test cases recognised in other languages (a workbook's columns stay in its own language)
LEGACY_FILES = ("scripts/migrate_workspace.py", "tests/test_migrate_workspace.py", "scripts/sync_skills.py",
                "tests/test_sync_skills.py", "tests/test_language.py", "adapters/case_headers.py")


def legacy_tokens() -> list[str]:
    """Every old name or key the migration knows, longest first (a token can contain a shorter one)."""
    tokens = set()
    for mapping in (mw.TOP_KEYS, mw.RESOLVED_BUGS, mw.EVIDENCE, mw.MODULES, mw.ENVIRONMENTS, mw.API_KEYS, mw.AUTH,
                    mw.CREDENTIAL_KEYS, mw.A11Y_KEYS, mw.A11Y_LOGIN_KEYS, mw.PAGE_KEYS, mw.MOBILE_KEYS,
                    mw.DASHBOARD_KEYS, mw.DASHBOARD_TYPES, mw.EXCEL_KEYS, mw.CASES_KEYS, mw.CASE_COLUMNS,
                    mw.DEFECTS_KEYS, mw.DEFECT_COLUMNS, mw.SUMMARY_KEYS, mw.VIEW_KEYS, mw.VIEW_SUMMARY_KEYS,
                    mw.NOTES_KEYS, mw.RECALC_KEYS, mw.BATCH_ITEM_KEYS, mw.NEW_DEFECT_KEYS, mw.GOLDEN_KEYS):
        tokens.update(mapping)
    tokens.update(old for old, _ in mw.TEXT_NAMES + mw.RENAMES)
    tokens.update(old for old, _ in mw.PLACEHOLDERS)
    tokens.update(mw.OLD_NOTES.values())
    tokens.update({mw.OLD_CONTROL_SHEET, mw.OLD_REPORTED_BY, "resultados", "lote_", "lote_*.json", "#segredo",
                   "antes.xlsx", "depois.xlsx", "_qa_lotes", "CT-"})
    return sorted(tokens, key=len, reverse=True)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout.split("\n")
    return sorted({f for f in out if f and (f.endswith(TEXT) or "." not in os.path.basename(f))
                   and not f.startswith(SKIP) and os.path.isfile(os.path.join(ROOT, f))})


class Language(unittest.TestCase):
    def test_no_portuguese_in_the_framework_files(self):
        tokens = legacy_tokens()
        found = []
        for rel in tracked_files():
            if rel in LEGACY_FILES:
                continue
            with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    for t in tokens:
                        line = line.replace(t, " ")
                    m = WORDS.search(line)
                    if m:
                        found.append(f"{rel}:{n}: {m.group(0)!r} in {line.strip()[:120]!r}")
        self.assertEqual(found, [], f"{len(found)} line(s) with Portuguese:\n" + "\n".join(found[:80]))

    def test_the_check_catches_portuguese(self):
        self.assertTrue(WORDS.search("o ficheiro não existe"))
        self.assertTrue(WORDS.search("Resultado esperado do caso"))
        self.assertFalse(WORDS.search("the file does not exist at example.com"))


if __name__ == "__main__":
    unittest.main()

"""Configuracao dos guards do pre-commit (.pre-commit-config.yaml).

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
O detect-secrets 1.5.0 abre os ficheiros sem encoding; no Windows (cp1252) um
ficheiro UTF-8 com certos caracteres (ex.: "Á") era saltado sem aviso. O hook
tem de correr em modo UTF-8 e manter o id que o CI usa.
"""

import os
import re
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hooks() -> dict:
    with open(os.path.join(ROOT, ".pre-commit-config.yaml"), encoding="utf-8-sig") as f:
        cfg = yaml.safe_load(f)
    return {h["id"]: h for repo in cfg["repos"] for h in repo["hooks"]}


class Guards(unittest.TestCase):
    def test_detect_secrets_em_modo_utf8_com_versao_fixada(self):
        h = hooks()["detect-secrets"]
        self.assertIn("-X utf8", h.get("entry", ""))
        self.assertIn("detect-secrets==1.5.0", h.get("additional_dependencies", []))
        self.assertEqual(h.get("args"), ["--baseline", ".secrets.baseline"])

    def test_os_guards_existem(self):
        self.assertTrue({"detect-secrets", "denylist", "ruff-check", "golden", "testes"} <= set(hooks()))

    def test_testes_correm_quando_mudam_os_ficheiros_que_leem(self):
        padrao = re.compile(hooks()["testes"]["files"])
        for caminho in ("adapters/excel/base.py", "scripts/gerar_painel.py", "tests/test_skills.py",
                        ".claude/skills/checkpoint/SKILL.md", "CLAUDE.md", "examples/demo/projeto.yaml",
                        "docs/projeto.yaml", ".pre-commit-config.yaml", "tools/a11y/axe_scan.mjs"):
            with self.subTest(caminho=caminho):
                self.assertTrue(padrao.search(caminho))
        self.assertFalse(padrao.search("docs/metodologia-core.md"))

    def test_o_bug_de_codificacao_existe_mesmo(self):
        # a razao do -X utf8: bytes de "Á" em UTF-8 nao se leem em cp1252
        with self.assertRaises(UnicodeDecodeError):
            "Área".encode().decode("cp1252")


if __name__ == "__main__":
    unittest.main()

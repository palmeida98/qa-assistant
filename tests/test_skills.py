"""Skills do projeto (.claude/skills): frontmatter valido e coerente.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
Um frontmatter com YAML invalido (ex.: ': ' na descricao sem aspas) faz o
Claude Code mostrar a primeira linha do corpo em vez da descricao.
"""

import os
import re
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, ".claude", "skills")
CLAUDE_MD = os.path.join(ROOT, "CLAUDE.md")


def frontmatter(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        texto = f.read().replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", texto, re.DOTALL)
    if not m:
        raise AssertionError(f"{path}: sem bloco --- frontmatter --- no inicio")
    return yaml.safe_load(m.group(1))


class Skills(unittest.TestCase):
    def nomes(self):
        return sorted(d for d in os.listdir(SKILLS) if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md")))

    def test_frontmatter_valido_com_nome_e_descricao(self):
        for nome in self.nomes():
            with self.subTest(skill=nome):
                fm = frontmatter(os.path.join(SKILLS, nome, "SKILL.md"))
                self.assertIsInstance(fm, dict)
                self.assertEqual(fm.get("name"), nome)
                self.assertIsInstance(fm.get("description"), str)
                self.assertGreater(len(fm["description"]), 20)

    def test_cada_skill_esta_nos_comandos_do_claude_md(self):
        with open(CLAUDE_MD, encoding="utf-8-sig") as f:
            comandos = f.read()
        for nome in self.nomes():
            with self.subTest(skill=nome):
                self.assertIn(f"/{nome}", comandos)


if __name__ == "__main__":
    unittest.main()

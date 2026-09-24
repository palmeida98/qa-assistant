"""scripts/sincronizar_skills.py: a copia global das skills (~/.claude/skills),
que e a que corre nos workspaces, tem de ser igual a do repositorio.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
Usa uma pasta temporaria como destino: nunca toca na ~/.claude/skills real.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import sincronizar_skills as ss


class Sincronizar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.destino = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def correr(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = ss.main(["--destino", self.destino, *args])
        return rc, out.getvalue()

    def test_destino_vazio_fica_por_sincronizar_sem_escrever(self):
        rc, out = self.correr()
        self.assertEqual(rc, 1)
        self.assertIn("em falta", out)
        self.assertEqual(os.listdir(self.destino), [])

    def test_aplicar_copia_e_depois_fica_igual(self):
        self.assertEqual(self.correr("--aplicar")[0], 0)
        estado = ss.comparar(ss.ORIGEM, self.destino)
        self.assertTrue(estado)
        self.assertEqual(set(estado.values()), {"igual"})
        self.assertEqual(self.correr()[0], 0)

    def test_so_fins_de_linha_diferentes_conta_como_igual(self):
        self.correr("--aplicar")
        nome = min(ss.comparar(ss.ORIGEM, self.destino))
        path = os.path.join(self.destino, nome, "SKILL.md")
        with open(path, encoding="utf-8") as f:
            texto = f.read().replace("\r\n", "\n")
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(texto)
        self.assertEqual(ss.comparar(ss.ORIGEM, self.destino)[nome], "igual")

    def test_copia_editada_e_detetada_e_reposta(self):
        self.correr("--aplicar")
        nome = min(ss.comparar(ss.ORIGEM, self.destino))
        path = os.path.join(self.destino, nome, "SKILL.md")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nlinha a mais\n")
        rc, out = self.correr()
        self.assertEqual(rc, 1)
        self.assertIn(f"diferente  {nome}", out)
        self.assertEqual(self.correr("--aplicar")[0], 0)
        self.assertEqual(ss.comparar(ss.ORIGEM, self.destino)[nome], "igual")

    def test_skills_globais_de_outras_origens_sao_ignoradas(self):
        os.makedirs(os.path.join(self.destino, "outra-coisa"))
        with open(os.path.join(self.destino, "outra-coisa", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: outra-coisa\n---\n")
        self.correr("--aplicar")
        self.assertNotIn("outra-coisa", ss.comparar(ss.ORIGEM, self.destino))
        self.assertTrue(os.path.isfile(os.path.join(self.destino, "outra-coisa", "SKILL.md")))


if __name__ == "__main__":
    unittest.main()

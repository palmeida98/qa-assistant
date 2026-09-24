"""Guard de privacidade scripts/check_denylist.py: texto, nomes de ficheiros e
ficheiros Office (ZIP) -- um workbook de cliente nao pode passar sem ser visto.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
Usa uma denylist ficticia via QA_DENYLIST; nunca le a lista pessoal.
"""

import os
import subprocess
import sys
import tempfile
import unittest

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_denylist.py")
TERMO = "empresaficticiaxyz"


class Denylist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lista = os.path.join(self.tmp.name, "denylist.txt")
        with open(self.lista, "w", encoding="utf-8") as f:
            f.write(f"# comentario\n{TERMO.upper()}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def correr(self, *ficheiros):
        env = {**os.environ, "QA_DENYLIST": self.lista}
        return subprocess.run([sys.executable, SCRIPT, *ficheiros], env=env,
                              capture_output=True, text=True, check=False)

    def ficheiro(self, nome, texto=""):
        path = os.path.join(self.tmp.name, nome)
        with open(path, "w", encoding="utf-8") as f:
            f.write(texto)
        return path

    def xlsx(self, texto):
        path = os.path.join(self.tmp.name, "plano.xlsx")
        wb = openpyxl.Workbook()
        wb.active["B7"] = texto
        wb.save(path)
        return path

    def test_texto_limpo_passa(self):
        self.assertEqual(self.correr(self.ficheiro("a.md", "nada aqui")).returncode, 0)

    def test_termo_no_texto_bloqueia(self):
        r = self.correr(self.ficheiro("a.md", f"cliente {TERMO} x"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("BLOQUEADO", r.stdout)

    def test_termo_no_nome_do_ficheiro_bloqueia(self):
        self.assertEqual(self.correr(self.ficheiro(f"plano-{TERMO}.md", "limpo")).returncode, 1)

    def test_termo_dentro_de_um_xlsx_bloqueia(self):
        self.assertEqual(self.correr(self.xlsx(f"Portal {TERMO} - login")).returncode, 1)

    def test_xlsx_limpo_passa(self):
        self.assertEqual(self.correr(self.xlsx("Loja Demo")).returncode, 0)


if __name__ == "__main__":
    unittest.main()

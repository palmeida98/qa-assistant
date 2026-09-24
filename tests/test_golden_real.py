"""Golden real (scripts/golden_check.py --dir; metodologia secao 15; plano 7.3).

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
Com dados ficticios (fixture e lote do golden ficticio), num workspace
temporario: o mecanismo tem de passar quando o depois.xlsx bate certo, falhar a
apontar a celula quando nao bate, e recusar pastas incompletas. Precisa do
LibreOffice (o aplicar recalcula): no CI corre no job golden.
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import openpyxl
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests", "golden")]

import golden_check
from gerar_fixture import gerar

from adapters.excel.aplicar import aplicar
from adapters.excel.recalc import find_soffice

with open(os.path.join(ROOT, "adapters", "excel", "exemplo.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
LOTE = os.path.join(ROOT, "tests", "golden", "lote.json")
AGORA = "2026-01-02T03:04:05"
NOME_LOTE = "lote_20260102_01.json"


def tem_libreoffice() -> bool:
    try:
        find_soffice()
        return True
    except FileNotFoundError:
        return False


def workspace(pasta: str) -> None:
    with open(os.path.join(pasta, "projeto.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"projeto": "loja-demo", "excel": CFG}, f, allow_unicode=True)


@unittest.skipUnless(tem_libreoffice(), "precisa do LibreOffice (corre no job golden do CI)")
class GoldenReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # o "depois" guardado: o workbook depois do checkpoint original, com o mesmo instante
        cls.origem = tempfile.mkdtemp()
        workspace(cls.origem)
        cls.antes = os.path.join(cls.origem, "antes.xlsx")
        gerar(cls.antes)
        shutil.copy(cls.antes, os.path.join(cls.origem, CFG["ficheiro"]))
        shutil.copy(LOTE, os.path.join(cls.origem, NOME_LOTE))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = aplicar(cls.origem, os.path.join(cls.origem, NOME_LOTE), agora=golden_check.datetime.fromisoformat(AGORA))
        assert rc == 0, rc
        cls.depois = os.path.join(cls.origem, CFG["ficheiro"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.origem)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        workspace(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def golden(self, nome="lote01", agora=AGORA) -> str:
        pasta = os.path.join(self.ws, "golden", nome)
        os.makedirs(pasta)
        shutil.copy(self.antes, os.path.join(pasta, "antes.xlsx"))
        shutil.copy(self.depois, os.path.join(pasta, "depois.xlsx"))
        shutil.copy(LOTE, os.path.join(pasta, NOME_LOTE))
        with open(os.path.join(pasta, "golden.yaml"), "w", encoding="utf-8") as f:
            f.write(f"agora: '{agora}'\n" if agora else "etiqueta: x\n")
        return pasta

    def correr(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = golden_check.main(list(args))
        return rc, out.getvalue()

    def test_depois_igual_passa(self):
        pasta = self.golden()
        rc, out = self.correr("--dir", pasta)
        self.assertEqual(rc, 0, out)
        self.assertIn("GOLDEN REAL OK", out)

    def test_celula_diferente_falha_e_aponta_a_celula(self):
        pasta = self.golden()
        wb = openpyxl.load_workbook(os.path.join(pasta, "depois.xlsx"))
        ws = wb[CFG["defeitos"]["sheet"]]
        ws["A2"] = "BUG-999"
        wb.save(os.path.join(pasta, "depois.xlsx"))
        rc, out = self.correr("--dir", pasta)
        self.assertEqual(rc, 1)
        self.assertIn(f"{CFG['defeitos']['sheet']}!A2: esperado ('BUG-999'", out)

    def test_outro_instante_muda_as_datas_escritas(self):
        pasta = self.golden(agora="2026-03-04T05:06:07")
        rc, out = self.correr("--dir", pasta)
        self.assertEqual(rc, 1)
        self.assertIn("2026-03-04", out)

    def test_pasta_com_varios_goldens_e_variavel_de_ambiente(self):
        self.golden("a")
        self.golden("b")
        with mock.patch.dict(os.environ, {"QA_GOLDEN_DIR": os.path.join(self.ws, "golden")}):
            rc, out = self.correr()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("GOLDEN REAL OK"), 2)

    def test_pasta_incompleta_ou_fora_de_um_workspace_sai_com_2(self):
        pasta = self.golden(agora=None)
        rc, out = self.correr("--dir", pasta)
        self.assertEqual(rc, 2)
        self.assertIn("falta 'agora'", out)
        os.remove(os.path.join(self.ws, "projeto.yaml"))
        with open(os.path.join(pasta, "golden.yaml"), "w", encoding="utf-8") as f:
            f.write(f"agora: '{AGORA}'\n")
        rc, out = self.correr("--dir", pasta)
        self.assertEqual(rc, 2)
        self.assertIn("nenhum projeto.yaml", out)
        rc, out = self.correr("--dir", self.ws)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

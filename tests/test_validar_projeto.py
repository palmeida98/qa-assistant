"""scripts/validar_projeto.py (metodologia 4.1): lista fechada de modulos,
seccao de cada modulo ativo, Excel e painel. O demo tem de passar.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import validar_projeto as vp

BASE = {"projeto": "loja-demo", "empresa": "demo", "ambientes": {"teste": "https://loja.demo"},
        "tracker": "jira", "formato_bug": "LOJA-{n}", "bugs_resolvidos": "remover", "evidencia": "texto",
        "modulos": ["funcional-web"]}


class Validar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def com(self, **alteracoes):
        proj = {**BASE, **alteracoes}
        with open(os.path.join(self.tmp.name, "projeto.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f, allow_unicode=True)
        return vp.validar(self.tmp.name)

    def test_base_valida(self):
        self.assertEqual(self.com(), ([], []))

    def test_modulo_fora_da_lista(self):
        problemas, _ = self.com(modulos=["funcional-web", "smoke"])
        self.assertTrue(any("fora da lista fechada" in p and "smoke" in p for p in problemas), problemas)

    def test_modulo_ativo_sem_seccao(self):
        problemas, _ = self.com(modulos=["api", "mobile"], mobile={"plataforma": "web"})
        self.assertTrue(any("'api'" in p and "base_url" in p for p in problemas), problemas)
        self.assertTrue(any("'mobile'" in p and "flows" in p for p in problemas), problemas)

    def test_seccao_sem_modulo_e_aviso(self):
        problemas, avisos = self.com(api={"base_url": "https://api.loja.demo"})
        self.assertEqual(problemas, [])
        self.assertTrue(any("seccao api:" in w for w in avisos), avisos)

    def test_campos_base(self):
        problemas, _ = self.com(projeto="", tracker="trello", evidencia="video", ambientes={})
        texto = " | ".join(problemas)
        for trecho in ("'projeto' vazio", "tracker 'trello'", "evidencia", "'ambientes' vazio"):
            self.assertIn(trecho, texto)


class Demo(unittest.TestCase):
    def test_o_demo_passa(self):
        # copia do projeto.yaml e do workbook do demo (so leitura do original)
        tmp = tempfile.mkdtemp()
        try:
            demo = os.path.join(ROOT, "examples", "demo")
            shutil.copy(os.path.join(demo, "projeto.yaml"), tmp)
            os.makedirs(os.path.join(tmp, "workbook"))
            shutil.copy(os.path.join(demo, "workbook", "plano-demo.xlsx"), os.path.join(tmp, "workbook"))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = vp.main([tmp])
            self.assertEqual(rc, 0, out.getvalue())
        finally:
            shutil.rmtree(tmp)

    def test_sem_projeto_sai_com_2(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(vp.main([tmp]), 2)


if __name__ == "__main__":
    unittest.main()

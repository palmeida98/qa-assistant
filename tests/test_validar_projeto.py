"""scripts/validar_projeto.py (metodologia 4.1): lista fechada de modulos,
seccao de cada modulo ativo, Excel e painel. O demo tem de passar.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
"""

import contextlib
import io
import os
import shutil
import subprocess
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
        # historico/ protegido, como o onboarding deixa (docs/workspace/settings.json)
        os.makedirs(os.path.join(self.tmp.name, ".claude"))
        shutil.copy(os.path.join(ROOT, "docs", "workspace", "settings.json"), os.path.join(self.tmp.name, ".claude"))

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


    def test_ambiente_sem_url_ou_mal_escrito(self):
        problemas, _ = self.com(ambientes={"teste": "", "uat": "loja.demo"})
        texto = " | ".join(problemas)
        self.assertIn("ambiente 'teste' sem URL", texto)
        self.assertIn("ambiente 'uat'", texto)

    def test_urls_dos_modulos_dentro_dos_ambientes(self):
        a11y = {"paginas": [{"nome": "home", "url": "https://loja.demo/"},
                            {"nome": "conta", "url": "https://loja.demo.outro.pt/conta"}]}
        problemas, _ = self.com(modulos=["api", "acessibilidade"], acessibilidade=a11y,
                                api={"base_url": "https://api.loja.demo/v1"})
        texto = " | ".join(problemas)
        self.assertIn("api.base_url (https://api.loja.demo/v1) fora dos ambientes", texto)
        self.assertIn("acessibilidade.paginas[conta]", texto)  # anfitriao parecido nao conta
        self.assertNotIn("paginas[home]", texto)

    def test_url_de_producao_e_problema(self):
        problemas, _ = self.com(modulos=["api"], api={"base_url": "https://loja.pt/api"},
                                ambientes={"teste": "https://teste.loja.pt", "producao": "https://loja.pt"})
        self.assertTrue(any("'producao'" in p for p in problemas), problemas)

    def test_url_com_caminho_do_ambiente(self):
        problemas, _ = self.com(modulos=["api"], ambientes={"teste": "https://loja.demo/app/"},
                                api={"base_url": "https://loja.demo/app/api"})
        self.assertEqual(problemas, [])
        problemas, _ = self.com(modulos=["api"], ambientes={"teste": "https://loja.demo/app/"},
                                api={"base_url": "https://loja.demo/application"})
        self.assertTrue(problemas)

    @unittest.skipUnless(shutil.which("git"), "sem git")
    def test_gitignore_num_workspace_com_git(self):
        subprocess.run(["git", "init", "-q", self.tmp.name], check=True)
        _, avisos = self.com(modulos=["api"], api={"base_url": "https://loja.demo/api"})
        self.assertTrue(any(".env nao esta no .gitignore" in w for w in avisos), avisos)
        self.assertTrue(any(".api-sessao.env" in w for w in avisos), avisos)
        with open(os.path.join(self.tmp.name, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*.env\n")
        _, avisos = self.com(modulos=["api"], api={"base_url": "https://loja.demo/api"})
        self.assertEqual(avisos, [])


    def test_historico_sem_protecao_e_aviso(self):
        settings = os.path.join(self.tmp.name, ".claude", "settings.json")
        for conteudo in (None, "{}", '{"permissions": {"deny": ["Write(/historico/**)"]}}', "nao e json"):
            with self.subTest(conteudo=conteudo):
                if conteudo is None:
                    os.remove(settings)
                else:
                    with open(settings, "w", encoding="utf-8") as f:
                        f.write(conteudo)
                problemas, avisos = self.com()
                self.assertEqual(problemas, [])
                self.assertTrue(any("historico/ sem protecao" in w for w in avisos), avisos)

    def test_historico_protegido_com_outras_regras(self):
        with open(os.path.join(self.tmp.name, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            f.write('{"permissions": {"deny": ["Bash(git push *)", "Edit(historico/**)"]}}')
        self.assertEqual(self.com(), ([], []))


class Demo(unittest.TestCase):
    def test_o_demo_passa(self):
        # copia do projeto.yaml e do workbook do demo (so leitura do original)
        tmp = tempfile.mkdtemp()
        try:
            demo = os.path.join(ROOT, "examples", "demo")
            shutil.copy(os.path.join(demo, "projeto.yaml"), tmp)
            shutil.copytree(os.path.join(demo, ".claude"), os.path.join(tmp, ".claude"))
            os.makedirs(os.path.join(tmp, "workbook"))
            shutil.copy(os.path.join(demo, "workbook", "plano-demo.xlsx"), os.path.join(tmp, "workbook"))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = vp.main([tmp])
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("0 problema(s), 0 aviso(s)", out.getvalue())
        finally:
            shutil.rmtree(tmp)

    def test_sem_projeto_sai_com_2(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(vp.main([tmp]), 2)


if __name__ == "__main__":
    unittest.main()

"""scripts/auditar_a11y.py (tecnica-acessibilidade.md): mapeamento das tags do
axe para criterios WCAG e nivel, separacao violacoes / boas praticas /
incompletos, pagina redirecionada (falso Pass) e validacao da configuracao.
Sem browser nem rede: usa um resultado bruto do axe ficticio.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import auditar_a11y as a11y


def violacao(regra, tags, n=1):
    return {"id": regra, "impacto": "serious", "tags": tags, "ajuda": "…", "ajuda_url": "https://x",
            "elementos": n, "exemplos": [{"alvo": "#x", "html": "<b>", "resumo": "…"}]}


BRUTO = {"browser": "chrome 1", "paginas": [
    {"nome": "inicio", "url": "https://loja.demo/", "url_final": "https://loja.demo", "titulo": "Loja Demo",
     "violacoes": [violacao("color-contrast", ["cat.color", "wcag2aa", "wcag143"], 3),
                   violacao("image-alt", ["wcag2a", "wcag111", "section508"]),
                   violacao("region", ["cat.keyboard", "best-practice"])],
     "incompletos": [violacao("color-contrast", ["wcag2aa", "wcag143"], 2)], "passaram": 30},
    {"nome": "conta", "url": "https://loja.demo/conta", "url_final": "https://loja.demo/", "titulo": "Entrar",
     "violacoes": [violacao("image-alt", ["wcag2a", "wcag111"])], "incompletos": [], "passaram": 20},
    {"nome": "falhou", "url": "https://loja.demo/x", "erro": "net::ERR_NAME_NOT_RESOLVED"},
]}


class Mapeamento(unittest.TestCase):
    def test_criterios_e_nivel(self):
        self.assertEqual(a11y.criterios(["wcag2aa", "wcag143", "wcag1410", "cat.color"]), ["1.4.3", "1.4.10"])
        self.assertEqual(a11y.nivel(["wcag2aa", "wcag143"]), "AA")
        self.assertEqual(a11y.nivel(["wcag2a", "wcag21aa"]), "A")
        self.assertIsNone(a11y.nivel(["best-practice"]))


class Classificar(unittest.TestCase):
    def setUp(self):
        self.rel = a11y.classificar(BRUTO, "WCAG 2.1 AA")
        self.p = {p["nome"]: p for p in self.rel["paginas"]}

    def test_violacoes_wcag_e_boas_praticas_separadas(self):
        v = self.p["inicio"]["violacoes_wcag"]
        self.assertEqual([(x["regra"], x["criterios"], x["nivel"], x["elementos"]) for x in v],
                         [("color-contrast", ["1.4.3"], "AA", 3), ("image-alt", ["1.1.1"], "A", 1)])
        self.assertEqual([x["regra"] for x in self.p["inicio"]["boas_praticas"]], ["region"])

    def test_incompletos_ficam_para_revisao_manual(self):
        self.assertEqual(self.p["inicio"]["incompletos"], [{"regra": "color-contrast", "criterios": ["1.4.3"],
                                                            "elementos": 2}])

    def test_pagina_redirecionada_nao_da_falso_pass(self):
        conta = self.p["conta"]
        self.assertIn("redirecionada", conta["erro"])
        self.assertEqual(conta["violacoes_wcag"], [])

    def test_barra_final_nao_conta_como_redirecionamento(self):
        self.assertIsNone(self.p["inicio"]["erro"])

    def test_resumo_diz_as_paginas_nao_auditadas(self):
        linhas = a11y.resumo_texto(self.rel)
        self.assertTrue(any(ln.startswith("falhou: NAO AUDITADA") for ln in linhas), linhas)
        self.assertTrue(any("color-contrast [1.4.3 AA] x3" in ln for ln in linhas), linhas)


class Configuracao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def escrever(self, yaml_txt, env=""):
        with open(os.path.join(self.tmp.name, "projeto.yaml"), "w", encoding="utf-8") as f:
            f.write(yaml_txt)
        with open(os.path.join(self.tmp.name, ".env"), "w", encoding="utf-8") as f:
            f.write(env)

    LOGIN = ('  login: {url: "https://loja.demo/", campo_utilizador: "#u", campo_palavra_passe: "#p",'
             ' botao: "#b", var_utilizador: LOJA_U, var_palavra_passe: LOJA_P}\n')

    def test_sem_paginas(self):
        self.escrever("acessibilidade: {}\n")
        with self.assertRaises(a11y.Erro):
            a11y.configuracao(self.tmp.name)

    def test_norma_nao_suportada(self):
        self.escrever('acessibilidade:\n  norma: "WCAG 9"\n  paginas: [{nome: a, url: "https://x"}]\n')
        with self.assertRaises(a11y.Erro):
            a11y.configuracao(self.tmp.name)

    def test_login_sem_valor_no_env(self):
        self.escrever("acessibilidade:\n" + self.LOGIN + '  paginas: [{nome: a, url: "https://x", login: true}]\n',
                      "LOJA_U=ana\n")
        with self.assertRaises(a11y.Erro) as cm:
            a11y.configuracao(self.tmp.name)
        self.assertIn("LOJA_P", str(cm.exception))

    def test_credenciais_so_no_ambiente_nunca_na_configuracao(self):
        self.escrever("acessibilidade:\n" + self.LOGIN + '  paginas: [{nome: a, url: "https://x", login: true}]\n',
                      "LOJA_U=ana\nLOJA_P=segredo-ficticio\n")
        conf, env = a11y.configuracao(self.tmp.name)
        self.assertEqual(env, {"QA_A11Y_UTILIZADOR": "ana", "QA_A11Y_PALAVRA_PASSE": "segredo-ficticio"})
        self.assertNotIn("segredo-ficticio", repr(conf))
        self.assertIn("best-practice", conf["cfg"]["tags"])


if __name__ == "__main__":
    unittest.main()

"""scripts/auditar_a11y.py (tecnica-acessibilidade.md secao 2): traducao do projeto.yaml para a configuracao
do motor qa-audit, e do resultado do motor (referencias WCAG dos achados) para criterios, nivel, violacoes /
boas praticas / incompletos / outras verificacoes; pagina redirecionada ou nao auditada (falso Pass); validacao
da configuracao. Sem browser nem rede: usa um results.json do motor ficticio.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
"""

import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts"), os.path.join(ROOT, "src")]

import auditar_a11y as a11y


def achado(url, regra, refs, status="problem", n=1, severidade="medium"):
    return {"module": "accessibility", "check": f"axe/{regra}", "title": f"ajuda {regra}", "status": status,
            "severity": severidade, "url": url, "actual": f"{n} element(s) fail this rule",
            "references": refs + [f"https://dequeuniversity.com/rules/axe/4.13/{regra}"],
            "evidence": [{"kind": "html", "value": "<b>", "label": "#x"}], "element": {"selector": "#x"}}


def pagina(url, final=None, audited=True, erro="", titulo="Loja Demo"):
    return {"url": url, "final_url": final or url, "title": titulo, "error": erro, "audited": audited}


INICIO, CONTA, NOVA = "https://loja.demo/", "https://loja.demo/conta", "https://loja.demo/novidades"
RESULTADO = {
    "browser": {"engine": "chromium", "version": "153"},
    "notes": ["auth: logged in (form) at /conta; one attempt"],
    "crawl": {"skipped": [{"url": "https://loja.demo/bloqueada", "reason": "disallowed by robots.txt"}]},
    "pages": [pagina(INICIO), pagina(CONTA, final="https://loja.demo/entrar", titulo="Entrar"),
              pagina(NOVA, erro="HTTP 404", audited=False)],
    "findings": [
        achado(INICIO, "color-contrast", ["WCAG 1.4.3", "WCAG 2.0 AA"], n=3),
        achado(INICIO, "image-alt", ["WCAG 1.1.1", "WCAG 2.0 A"], severidade="high"),
        achado(INICIO, "region", []),                                   # best-practice: sem criterio
        achado(INICIO, "target-size", ["WCAG 2.5.8", "WCAG 2.2 AA"]),   # fora da WCAG 2.1: boa pratica aqui
        achado(INICIO, "color-contrast", ["WCAG 1.4.3", "WCAG 2.0 AA"], status="needs-human", n=2),
        {"module": "accessibility", "check": "reflow", "title": "Content scrolls horizontally at 320 CSS px",
         "status": "warning", "severity": "medium", "url": INICIO, "actual": "The page is 900 px wide",
         "references": ["WCAG 1.4.10"], "element": {}},
        {"module": "security", "check": "missing-csp", "title": "x", "status": "problem", "severity": "low",
         "url": INICIO},
        achado("https://loja.demo/entrar", "image-alt", ["WCAG 1.1.1", "WCAG 2.0 A"]),
    ],
}
GRUPO = {"login": True, "alvo": INICIO, "paginas": [
    {"nome": "inicio", "url": INICIO, "login": True}, {"nome": "conta", "url": CONTA, "login": True},
    {"nome": "novidades", "url": NOVA, "login": True},
    {"nome": "bloqueada", "url": "https://loja.demo/bloqueada", "login": True},
    {"nome": "nunca", "url": "https://loja.demo/nunca", "login": True}]}


class Referencias(unittest.TestCase):
    def test_criterios_e_nivel_das_referencias_do_motor(self):
        self.assertEqual(a11y.criterios(["WCAG 1.4.3", "WCAG 2.0 AA", "WCAG 1.4.10", "https://x"]),
                         ["1.4.3", "1.4.10"])
        self.assertEqual(a11y.tag_da_referencia("WCAG 2.0 AA"), "wcag2aa")
        self.assertEqual(a11y.tag_da_referencia("WCAG 2.1 A"), "wcag21a")
        self.assertEqual(a11y.nivel(["WCAG 1.4.3", "WCAG 2.0 AA"], "WCAG 2.1 AA"), "AA")
        self.assertEqual(a11y.nivel(["WCAG 2.0 A", "WCAG 2.1 AA"], "WCAG 2.1 AA"), "A")
        self.assertIsNone(a11y.nivel([], "WCAG 2.1 AA"))

    def test_nivel_so_dentro_da_norma(self):
        self.assertIsNone(a11y.nivel(["WCAG 2.5.8", "WCAG 2.2 AA"], "WCAG 2.1 AA"))
        self.assertEqual(a11y.nivel(["WCAG 2.5.8", "WCAG 2.2 AA"], "WCAG 2.2 AA"), "AA")


class Classificar(unittest.TestCase):
    def setUp(self):
        self.rel = a11y.classificar([{"grupo": GRUPO, "codigo": 0, "resultado": RESULTADO, "erro": ""}],
                                    "WCAG 2.1 AA")
        self.p = {p["nome"]: p for p in self.rel["paginas"]}

    def test_violacoes_wcag_e_boas_praticas_separadas(self):
        v = self.p["inicio"]["violacoes_wcag"]
        self.assertEqual([(x["regra"], x["criterios"], x["nivel"], x["elementos"]) for x in v],
                         [("color-contrast", ["1.4.3"], "AA", 3), ("image-alt", ["1.1.1"], "A", 1)])
        self.assertEqual(v[1]["severidade_sugerida"], "high")
        self.assertTrue(v[0]["ajuda_url"].startswith("https://dequeuniversity.com/"))
        self.assertEqual([x["regra"] for x in self.p["inicio"]["boas_praticas"]], ["region", "target-size"])

    def test_incompletos_ficam_para_revisao_manual(self):
        self.assertEqual(self.p["inicio"]["incompletos"], [{"regra": "color-contrast", "criterios": ["1.4.3"],
                                                            "elementos": 2}])

    def test_outras_verificacoes_do_motor_sao_indicios_e_so_de_acessibilidade(self):
        self.assertEqual([(o["verificacao"], o["criterios"], o["estado"]) for o in self.p["inicio"]["outras_verificacoes"]],
                         [("reflow", ["1.4.10"], "warning")])

    def test_pagina_redirecionada_nao_da_falso_pass(self):
        conta = self.p["conta"]
        self.assertIn("redirecionada", conta["erro"])
        self.assertEqual(conta["violacoes_wcag"], [])

    def test_paginas_de_erro_saltadas_ou_nao_visitadas_ficam_nao_auditadas(self):
        self.assertEqual(self.p["novidades"]["erro"], "HTTP 404")
        self.assertIn("robots", self.p["bloqueada"]["erro"])
        self.assertIn("nao visitada", self.p["nunca"]["erro"])
        self.assertIsNone(self.p["inicio"]["erro"])

    def test_login_falhado_deixa_o_grupo_todo_por_auditar(self):
        rel = a11y.classificar([{"grupo": GRUPO, "codigo": 3, "resultado": None,
                                 "erro": "qa-audit: login not confirmed"}], "WCAG 2.1 AA")
        self.assertTrue(all("login falhado" in p["erro"] for p in rel["paginas"]))

    def test_notas_e_browser_do_motor(self):
        self.assertEqual(self.rel["browser"], "chromium 153")
        self.assertIn("auth: logged in (form) at /conta; one attempt", self.rel["notas"])

    def test_resumo_diz_as_paginas_nao_auditadas(self):
        linhas = a11y.resumo_texto(self.rel)
        self.assertTrue(any(ln.startswith("novidades: NAO AUDITADA") for ln in linhas), linhas)
        self.assertTrue(any("color-contrast [1.4.3 AA] x3" in ln for ln in linhas), linhas)
        self.assertTrue(any("reflow [1.4.10] a confirmar" in ln for ln in linhas), linhas)


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
    PAGINAS = ('  paginas:\n    - {nome: entrada, url: "https://loja.demo/", login: false}\n'
               '    - {nome: conta, url: "https://loja.demo/conta", login: true}\n'
               '    - {nome: carrinho, url: "https://loja.demo/carrinho", login: true}\n'
               '    - {nome: ajuda, url: "https://ajuda.loja.demo/", login: false}\n')

    def test_sem_paginas(self):
        self.escrever("acessibilidade: {}\n")
        with self.assertRaises(a11y.Erro):
            a11y.configuracao(self.tmp.name)

    def test_norma_e_browser_nao_suportados(self):
        for yml in ('acessibilidade:\n  norma: "WCAG 9"\n  paginas: [{nome: a, url: "https://x"}]\n',
                    'acessibilidade:\n  browser: edge\n  paginas: [{nome: a, url: "https://x"}]\n',
                    'acessibilidade:\n  paginas: [{nome: a, url: "ftp://x"}]\n'):
            with self.subTest(yml=yml):
                self.escrever(yml)
                with self.assertRaises(a11y.Erro):
                    a11y.configuracao(self.tmp.name)

    def test_chrome_passa_a_chromium_com_aviso(self):
        self.escrever('acessibilidade:\n  browser: chrome\n  paginas: [{nome: a, url: "https://x"}]\n')
        conf, _ = a11y.configuracao(self.tmp.name)
        self.assertEqual(conf["browser"], "chromium")
        self.assertEqual(conf["grupos"][0]["motor"]["browser"]["engine"], "chromium")
        self.assertTrue(conf["avisos"])

    def test_login_sem_valor_no_env(self):
        self.escrever("acessibilidade:\n" + self.LOGIN + '  paginas: [{nome: a, url: "https://x", login: true}]\n',
                      "LOJA_U=ana\n")
        with self.assertRaises(a11y.Erro) as cm:
            a11y.configuracao(self.tmp.name)
        self.assertIn("LOJA_P", str(cm.exception))

    def test_um_grupo_por_login_e_origem_so_nas_paginas_declaradas(self):
        self.escrever("acessibilidade:\n" + self.LOGIN + self.PAGINAS, "LOJA_U=ana\nLOJA_P=segredo-ficticio\n")
        conf, _ = a11y.configuracao(self.tmp.name)
        g = {(x["login"], x["alvo"]): x for x in conf["grupos"]}
        self.assertEqual(set(g), {(False, "https://loja.demo/"), (True, "https://loja.demo/conta"),
                                  (False, "https://ajuda.loja.demo/")})
        com = g[(True, "https://loja.demo/conta")]["motor"]
        self.assertEqual(com["crawl"]["pages"], ["https://loja.demo/carrinho"])
        self.assertFalse(com["crawl"]["follow_links"])
        self.assertEqual(com["crawl"]["max_pages"], 2)
        self.assertFalse(com["crawl"]["audit_error_pages"])
        self.assertEqual(com["modules"]["accessibility"]["tags"],
                         ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
        self.assertEqual(com["auth"]["username_selector"], "#u")
        self.assertNotIn("auth", g[(False, "https://loja.demo/")]["motor"])

    def test_credenciais_so_no_ambiente_nunca_na_configuracao(self):
        self.escrever("acessibilidade:\n" + self.LOGIN + self.PAGINAS, "LOJA_U=ana\nLOJA_P=segredo-ficticio\n")
        conf, env = a11y.configuracao(self.tmp.name)
        self.assertEqual(env, {"QA_A11Y_UTILIZADOR": "ana", "QA_A11Y_PALAVRA_PASSE": "segredo-ficticio"})
        self.assertNotIn("segredo-ficticio", repr(conf))
        self.assertNotIn("ana", repr([g["motor"] for g in conf["grupos"]]))

    def test_sucesso_do_login_por_omissao_e_sair_da_pagina_de_login(self):
        self.escrever("acessibilidade:\n" + self.LOGIN + self.PAGINAS, "LOJA_U=ana\nLOJA_P=segredo-ficticio\n")
        conf, _ = a11y.configuracao(self.tmp.name)
        auth = next(g for g in conf["grupos"] if g["login"])["motor"]["auth"]
        sucesso = re.compile(auth["success_url"])
        for fica in ("https://loja.demo/", "https://loja.demo", "https://loja.demo/?erro=1"):
            self.assertIsNone(sucesso.search(fica), fica)
        self.assertTrue(sucesso.search("https://loja.demo/conta"))

    def test_configuracao_gerada_e_aceite_pelo_motor(self):
        from qa_audit.core import auth, config
        self.escrever("acessibilidade:\n" + self.LOGIN + self.PAGINAS + "  auditar_paginas_com_erro_http: true\n",
                      "LOJA_U=ana\nLOJA_P=segredo-ficticio\n")
        conf, env = a11y.configuracao(self.tmp.name)
        for g in conf["grupos"]:
            with self.subTest(alvo=g["alvo"]):
                cfg = config.build(None, g["motor"])
                self.assertTrue(cfg["crawl"]["audit_error_pages"])
                self.assertEqual(auth.validate(cfg, environ=env), [])


if __name__ == "__main__":
    unittest.main()

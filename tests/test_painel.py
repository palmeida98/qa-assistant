"""Painel (scripts/gerar_painel.py, plano 5.1): os numeros batem com o
esperado do golden (escrito a mao) e o HTML e um ficheiro unico, offline.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
Nao precisa de LibreOffice: conta os estados diretamente, sem recalcular.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden"), os.path.join(ROOT, "scripts")]

import gerar_painel
from gerar_fixture import gerar

from adapters.excel import ExcelAdapter, load_lote

GOLDEN = os.path.join(ROOT, "tests", "golden")
ESTADOS = {"To Do": "pendente", "Pass": "ok", "Fail": "falha", "Blocked": "bloqueado", "N/A": "na"}
CONTEXTO = """# Contexto
Atualizado: 2026-09-24 10:00

## Ponto de execução
- lote 01, `CT-LOJA-004`
  - sub-item **forte**

## Trabalho pendente
- 2.3 <script>x</script>
"""


class _Tags(HTMLParser):
    """Regista tags por fechar ou mal encaixadas."""

    VAZIAS = frozenset({"meta", "br", "img", "link", "input", "hr"})

    def __init__(self):
        super().__init__()
        self.pilha, self.erros = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VAZIAS:
            self.pilha.append(tag)

    def handle_endtag(self, tag):
        if self.pilha and self.pilha[-1] == tag:
            self.pilha.pop()
        else:
            self.erros.append(tag)


def bem_formado(html_txt: str) -> bool:
    p = _Tags()
    p.feed(html_txt)
    return not p.erros and not p.pilha


class PainelGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = tempfile.mkdtemp()
        with open(os.path.join(ROOT, "adapters", "excel", "exemplo.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        gerar(os.path.join(cls.ws, cfg["ficheiro"]))
        ad = ExcelAdapter.from_dict(cfg, base_dir=cls.ws)
        ad.load()
        ad.apply_lote(load_lote(os.path.join(GOLDEN, "lote.json")), etiqueta="[golden]")
        ad.regenerate_active_defects()
        assert ad.save_if_clean()["gravado_original"]
        proj = {"projeto": "golden", "tracker": "excel", "excel": cfg, "painel": {"estados": ESTADOS}}
        with open(os.path.join(cls.ws, "projeto.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(proj, f, allow_unicode=True, sort_keys=False)
        with open(os.path.join(cls.ws, "contexto.md"), "w", encoding="utf-8") as f:
            f.write(CONTEXTO)
        with contextlib.redirect_stdout(io.StringIO()):
            assert gerar_painel.main([cls.ws]) == 0
        with open(os.path.join(cls.ws, "painel.html"), encoding="utf-8") as f:
            cls.html = f.read()
        cls.num = json.loads(re.search(r'id="numeros">(.*?)</script>', cls.html, re.DOTALL).group(1))
        with open(os.path.join(GOLDEN, "esperado.json"), encoding="utf-8") as f:
            cls.esp = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.ws)

    def test_totais_por_area_batem_com_a_summary_esperada(self):
        ts = self.esp["totais_summary"]
        for a in self.num["casos"]["por_area"]:
            s = ts[a["area"]]
            self.assertEqual(a["total"], s["Total"], a["area"])
            self.assertEqual(a["executados"], s["Total"] - s["To Do"], a["area"])
            for estado in ESTADOS:
                self.assertEqual(a["por_estado"].get(estado, 0), s[estado], f"{a['area']}/{estado}")
        self.assertEqual(self.num["casos"]["total"], ts["Total"]["Total"])
        self.assertEqual(self.num["casos"]["executados"], ts["Total"]["Total"] - ts["Total"]["To Do"])

    def test_areas_pela_ordem_da_configuracao(self):
        self.assertEqual([a["area"] for a in self.num["casos"]["por_area"]], ["Loja", "Conta", "Admin"])

    def test_defeitos_batem_com_a_vista_esperada(self):
        # esperado: "3 open (2 High, 0 Medium, 1 Low) - 1 closed"
        d = self.num["defeitos"]
        self.assertEqual((d["abertos"], d["fechados"]), (3, 1))
        self.assertEqual(d["por_severidade"], {"High": 2, "Low": 1})
        ordem = re.findall(r"<tr><td>(BUG-\d+)</td>", self.html)
        self.assertEqual(ordem, [linha[0] for linha in self.esp["vista"]["linhas"]])

    def test_ficheiro_unico_offline(self):
        self.assertIsNone(re.search(r'(src|href)\s*=\s*"?(https?:)?//', self.html))
        self.assertNotIn("<link", self.html)
        self.assertNotIn("@import", self.html)

    def test_html_bem_formado_e_contexto_escapado(self):
        self.assertTrue(bem_formado(self.html))
        self.assertIn("Último checkpoint: 2026-09-24 10:00", self.html)
        self.assertIn("<code>CT-LOJA-004</code>", self.html)
        self.assertNotIn("<script>x</script>", self.html)


class TiposDeEstado(unittest.TestCase):
    def test_sem_secao_painel_o_primeiro_estado_e_pendente(self):
        self.assertEqual(gerar_painel.tipos_de_estado(["A", "B"], None), {"A": "pendente", "B": "outro"})

    def test_secao_invalida_e_recusada(self):
        with self.assertRaises(ValueError):
            gerar_painel.tipos_de_estado(["A"], {"estados": {"Z": "ok"}})
        with self.assertRaises(ValueError):
            gerar_painel.tipos_de_estado(["A"], {"estados": {"A": "verde"}})


class MarkdownMinimo(unittest.TestCase):
    def test_listas_encadeadas_bem_formadas(self):
        for md in ["- a\n  - b\n    - c\n- d\n", "  - comeca indentado\n- volta\n", "texto\n\n- x\ncontinua\n"]:
            self.assertTrue(bem_formado("<div>" + gerar_painel.markdown_minimo(md) + "</div>"), md)


if __name__ == "__main__":
    unittest.main()

"""Resumo do workbook vs contagem direta (tecnica-excel.md secao 5).

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
A classe ComLibreOffice recalcula formulas reais e so corre onde houver
LibreOffice (localmente e no job golden do CI); as outras nao precisam dele:
escrevem numeros no resumo em vez de formulas.
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden")]

from gerar_fixture import gerar

from adapters.excel import ConfigError, ExcelAdapter, load_lote
from adapters.excel import aplicar as aplicar_mod
from adapters.excel.recalc import find_soffice

with open(os.path.join(ROOT, "adapters", "excel", "exemplo.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
with open(os.path.join(ROOT, "tests", "golden", "esperado.json"), encoding="utf-8") as _f:
    TOTAIS = json.load(_f)["totais_summary"]  # escritos a mao, depois do lote do golden
LOTE = os.path.join(ROOT, "tests", "golden", "lote.json")


def tem_libreoffice() -> bool:
    try:
        find_soffice()
        return True
    except Exception:  # noqa: BLE001 -- qualquer falha = sem LibreOffice
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        gerar(os.path.join(self.tmp.name, CFG["ficheiro"]))
        self.ad = ExcelAdapter.from_dict(copy.deepcopy(CFG), base_dir=self.tmp.name)
        self.ad.load()
        self.ad.apply_lote(load_lote(LOTE), etiqueta="[t]")
        self.ad.save()

    def tearDown(self):
        self.tmp.cleanup()

    def resumo_com_numeros(self, alterar=None):
        """Substitui as formulas do resumo pelos totais esperados (numeros).
        `alterar` = {(area, coluna): valor} para introduzir erros."""
        self.ad.load()
        ws = self.ad.wb["Summary"]
        cab = [c.value for c in ws[1]]
        for row in ws.iter_rows(min_row=2):
            area = row[0].value
            if area in TOTAIS:
                for i, col in enumerate(cab[1:], start=1):
                    row[i].value = (alterar or {}).get((area, col), TOTAIS[area][col])
        self.ad.save()


class SemLibreOffice(Base):
    def test_sem_valores_calculados_nao_compara(self):
        vr = self.ad.verificar_resumo()
        self.assertFalse(vr["verificado"])
        self.assertIn("recalcular", vr["motivo"])

    def test_resumo_certo(self):
        self.resumo_com_numeros()
        self.assertEqual(self.ad.verificar_resumo(), {"verificado": True, "problemas": [], "motivo": None})

    def test_valor_errado_aponta_a_celula(self):
        self.resumo_com_numeros({("Loja", "Pass"): 0})
        problemas = self.ad.verificar_resumo()["problemas"]
        self.assertEqual(len(problemas), 1)
        self.assertIn("Summary!D2 (area 'Loja', Pass)", problemas[0])

    def test_total_errado(self):
        self.resumo_com_numeros({("Total", "Total"): 11})
        self.assertIn("linha de total, total", self.ad.verificar_resumo()["problemas"][0])

    def test_area_em_falta_no_resumo(self):
        self.resumo_com_numeros()
        self.ad.load()
        self.ad.wb["Summary"].delete_rows(4)  # linha da area Admin
        self.ad.save()
        problemas = self.ad.verificar_resumo()["problemas"]
        self.assertTrue(any("'Admin' nao aparece" in p for p in problemas), problemas)

    def test_sem_aba_resumo_nao_verifica(self):
        cfg = copy.deepcopy(CFG)
        del cfg["aba_resumo"]
        ad = ExcelAdapter.from_dict(cfg, base_dir=self.tmp.name)
        self.assertFalse(ad.verificar_resumo()["verificado"])

    def test_configuracao_invalida(self):
        cfg = copy.deepcopy(CFG)
        cfg["aba_resumo"]["coluna_area"] = "NaoExiste"
        with self.assertRaises(ConfigError):
            ExcelAdapter.from_dict(cfg, base_dir=self.tmp.name).load()
        cfg = copy.deepcopy(CFG)
        cfg["aba_resumo"]["colunas_estado"] = {"Inventado": "X"}
        with self.assertRaises(ConfigError):
            ExcelAdapter.from_dict(cfg, base_dir=self.tmp.name)


@unittest.skipUnless(tem_libreoffice(), "precisa do LibreOffice (corre no job golden do CI)")
class ComLibreOffice(Base):
    def test_formulas_reais_batem_depois_do_recalculo(self):
        self.assertTrue(self.ad.recalc()["ok"])
        self.assertEqual(self.ad.verificar_resumo()["problemas"], [])

    def test_formula_a_contar_a_coluna_errada(self):
        # o caso do piloto: a formula conta outra coluna e da um numero plausivel mas errado
        self.ad.wb["Summary"]["D2"] = "=COUNTIF('Loja'!A2:A100,\"Pass\")"
        self.ad.save()
        self.assertTrue(self.ad.recalc()["ok"])
        problemas = self.ad.verificar_resumo()["problemas"]
        self.assertTrue(any("Summary!D2" in p for p in problemas), problemas)

    def test_caso_fora_do_intervalo_fixo(self):
        ws = self.ad.wb["Loja"]
        ws.cell(101, 1).value = "CT-LOJA-099"  # as formulas da fixture param na linha 100
        ws.cell(101, 3).value = "To Do"
        self.ad.save()
        self.assertTrue(self.ad.recalc()["ok"])
        problemas = self.ad.verificar_resumo()["problemas"]
        self.assertTrue(any("area 'Loja', total" in p for p in problemas), problemas)

    def test_aplicar_sai_com_5(self):
        ws_dir = self.tmp.name
        self.ad.wb["Summary"]["D2"] = "=COUNTIF('Loja'!A2:A100,\"Pass\")"
        self.ad.save()
        with open(os.path.join(ws_dir, "projeto.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"tracker": "excel", "excel": CFG}, f, allow_unicode=True)
        lote = os.path.join(ws_dir, "lote_extra.json")
        with open(lote, "w", encoding="utf-8") as f:
            json.dump({"resultados": [{"tc": "CT-LOJA-003", "estado": "Pass", "resultado_obtido": "ok"}]}, f)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = aplicar_mod.main([ws_dir, lote])
        self.assertEqual(rc, 5, out.getvalue())
        self.assertIn("corrigir as FORMULAS", out.getvalue())


if __name__ == "__main__":
    unittest.main()

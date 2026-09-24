"""Validacao de lotes no adaptador Excel (tecnica-excel.md secao 4).

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
Nao precisa de LibreOffice: nunca recalcula.
"""

import copy
import json
import os
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden")]

from gerar_fixture import gerar

from adapters.excel import ExcelAdapter, LoteInvalido, load_lote

with open(os.path.join(ROOT, "adapters", "excel", "exemplo.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)


def item(tc="CT-LOJA-001", estado="Pass", **extra):
    return {"tc": tc, "estado": estado, "resultado_obtido": "obs", "defeito_novo": None, **extra}


class ValidarLote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        gerar(os.path.join(self.tmp.name, CFG["ficheiro"]))
        self.ad = ExcelAdapter.from_dict(copy.deepcopy(CFG), base_dir=self.tmp.name)
        self.ad.load()

    def tearDown(self):
        self.tmp.cleanup()

    def recusa(self, lote, trecho):
        """O lote e recusado, com `trecho` na mensagem, e o workbook fica igual."""
        antes = self.ad.progress_summary()
        with self.assertRaises(LoteInvalido) as cm:
            self.ad.apply_lote(lote)
        self.assertIn(trecho, str(cm.exception))
        self.assertEqual(self.ad.progress_summary(), antes)
        self.assertEqual(self.ad.lotes_aplicados(), [])

    def test_lote_valido(self):
        self.assertEqual(self.ad.validar_lote([item(), item("CT-CONTA-001", "Fail")]), [])

    def test_vazio(self):
        self.recusa([], "lote vazio")

    def test_estado_fora_do_vocabulario(self):
        self.recusa([item(estado="Passed")], "estado 'Passed'")

    def test_duvida_tem_de_ser_resolvida(self):
        self.recusa([item(estado="Dúvida")], "resolver a Duvida")

    def test_sem_estado(self):
        self.recusa([{"tc": "CT-LOJA-001"}], "falta 'estado'")

    def test_caso_inexistente(self):
        self.recusa([item("CT-NADA-999")], "caso nao existe")

    def test_caso_repetido(self):
        self.recusa([item(), item(estado="Fail")], "caso repetido")

    def test_bug_inexistente(self):
        self.recusa([item(estado="Fail", bug="BUG-999")], "bug 'BUG-999'")

    def test_prioridade_fora_da_lista(self):
        self.recusa([item(estado="Fail", defeito_novo={"titulo": "t", "prioridade": "P9"})], "prioridade")

    def test_estado_invalido_nao_escreve_os_itens_validos(self):
        self.recusa([item("CT-LOJA-003", "Pass"), item(estado="xx")], "estado 'xx'")

    def test_bug_existente_acrescenta_nota(self):
        r = self.ad.apply_lote([item("CT-LOJA-004", "Fail", bug="BUG-1")], etiqueta="[t]")
        self.assertEqual(r["novos_defeitos"], [])
        linha = self.ad.casos_por_estado("Fail")["Loja"]
        obs = next(x for x in linha if x["ID"] == "CT-LOJA-004")["Notes"]
        self.assertTrue(str(obs).endswith("[t] Defeito associado: BUG-1."), obs)

    def test_chaves_extra_sao_ignoradas(self):
        self.assertEqual(self.ad.validar_lote([item(ts="2026-09-24T10:00:00", nota="x")]), [])


class LerLote(unittest.TestCase):
    def escrever(self, conteudo, encoding="utf-8"):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "w", encoding=encoding) as f:
            f.write(conteudo)
        return path

    def test_aceita_bom(self):
        path = self.escrever(json.dumps({"resultados": [item()]}), encoding="utf-8-sig")
        self.assertEqual(load_lote(path)[0]["tc"], "CT-LOJA-001")

    def test_formato_errado_tem_mensagem_clara(self):
        path = self.escrever('{"caso": "X", "resultado": "Fail"}')
        with self.assertRaises(LoteInvalido) as cm:
            load_lote(path)
        self.assertIn('"resultados"', str(cm.exception))


if __name__ == "__main__":
    unittest.main()

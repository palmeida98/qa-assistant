"""Workbook reproduzivel (plano 7.6; tecnica-excel.md secao 1, "Data da aplicacao").

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
- Agora: com o instante fixo (aplicar --agora), aplicar o mesmo lote em duas
  copias da as mesmas celulas, e as datas escritas sao as desse instante. Sem
  LibreOffice (--sem-recalc).
- Demo: reconstruir o demo numa pasta temporaria (gerador + lotes de runs/)
  da exatamente as celulas do workbook versionado. Precisa do LibreOffice: no
  CI corre no job golden; no job testes fica skipped.
"""

import contextlib
import copy
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import openpyxl
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "examples", "demo")
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "golden")]

from gerar_fixture import gerar

from adapters.excel.aplicar import main as aplicar
from adapters.excel.recalc import find_soffice

with open(os.path.join(ROOT, "adapters", "excel", "exemplo.yaml"), encoding="utf-8") as _f:
    CFG = yaml.safe_load(_f)
LOTE = os.path.join(ROOT, "tests", "golden", "lote.json")
AGORA = "2026-01-02T03:04:05"


def tem_libreoffice() -> bool:
    try:
        find_soffice()
        return True
    except FileNotFoundError:
        return False


def celulas(path: str) -> dict:
    """{sheet: {coordenada: valor}} de todas as sheets (formulas como texto)."""
    wb = openpyxl.load_workbook(path, data_only=False)
    return {ws.title: {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}
            for ws in wb.worksheets}


class Agora(unittest.TestCase):
    def workspace(self, tmp: str) -> str:
        cfg = copy.deepcopy(CFG)
        with open(os.path.join(tmp, "projeto.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"excel": cfg}, f, allow_unicode=True)
        gerar(os.path.join(tmp, cfg["ficheiro"]))
        return os.path.join(tmp, cfg["ficheiro"])

    def aplicar(self, tmp: str) -> dict:
        path = self.workspace(tmp)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = aplicar([tmp, LOTE, "--sem-recalc", "--agora", AGORA])
        self.assertEqual(rc, 0)
        return celulas(path)

    def test_mesmo_instante_mesmas_celulas(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(self.aplicar(a), self.aplicar(b))

    def test_datas_escritas_sao_as_do_instante(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.aplicar(tmp)
        controlo = c[CFG.get("controlo_lotes", {}).get("sheet") or "_qa_lotes"]
        self.assertIn(AGORA, controlo.values())                      # aplicado_em
        defeitos = c[CFG["defeitos"]["sheet"]]
        self.assertIn(AGORA[:10], defeitos.values())                 # data de report dos defeitos novos
        todas = [v for s in c.values() for v in s.values() if isinstance(v, str)]
        self.assertTrue(any(f"[{AGORA[:10]}, lote]" in v for v in todas))  # etiqueta por omissao


@unittest.skipUnless(tem_libreoffice(), "precisa do LibreOffice (corre no job golden do CI)")
class Demo(unittest.TestCase):
    def test_reconstrucao_da_as_celulas_do_workbook_versionado(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(os.path.join(DEMO, "projeto.yaml"), tmp)
            r = subprocess.run([sys.executable, os.path.join(DEMO, "reconstruir_workbook.py"), tmp],
                               capture_output=True, text=True, encoding="utf-8", cwd=ROOT, check=False,
                               timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            novo = celulas(os.path.join(tmp, "workbook", "plano-demo.xlsx"))
        versionado = celulas(os.path.join(DEMO, "workbook", "plano-demo.xlsx"))
        self.assertEqual(sorted(novo), sorted(versionado))
        for sheet in versionado:
            with self.subTest(sheet=sheet):
                diferentes = {k: (versionado[sheet].get(k), novo[sheet].get(k))
                              for k in set(versionado[sheet]) | set(novo[sheet])
                              if versionado[sheet].get(k) != novo[sheet].get(k)}
                self.assertEqual(diferentes, {}, "reconstruir o demo e voltar a versionar o workbook "
                                                 "(python examples/demo/reconstruir_workbook.py)")


if __name__ == "__main__":
    unittest.main()

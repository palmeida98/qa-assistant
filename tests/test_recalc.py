"""Recalculo seguro (adapters/excel/recalc.py; tecnica-excel.md secao 2): o
timeout tem de ser efetivo mesmo quando o processo lanca filhos (soffice ->
soffice.bin), e uma falha deixa o original intacto.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
Nao precisa de LibreOffice.
"""

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from adapters.excel import recalc

# um pai que lanca um neto com os mesmos pipes e fica os dois pendurados
PAI_COM_NETO = ("import subprocess, sys, time; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)")


class Recalc(unittest.TestCase):
    def test_timeout_mata_a_arvore_de_processos(self):
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            recalc._run_with_timeout([sys.executable, "-c", PAI_COM_NETO], timeout=2)
        # sem matar o neto, o communicate() ficava a espera dele (60 s)
        self.assertLess(time.monotonic() - t0, 30)

    def test_mecanismo_desconhecido_nao_toca_no_ficheiro(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.xlsx")
            with open(path, "wb") as f:
                f.write(b"original")
            r = recalc.recalc_safe(path, mecanismo="calc-magico")
            self.assertFalse(r["ok"])
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"original")

    def test_falha_do_mecanismo_deixa_o_original_intacto(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.xlsx")
            with open(path, "wb") as f:
                f.write(b"original")
            original = recalc._recalc_libreoffice

            def partido(p, timeout):
                with open(p, "wb") as f:
                    f.write(b"meio escrito")
                raise RuntimeError("LibreOffice falhou")

            recalc._recalc_libreoffice = partido
            try:
                r = recalc.recalc_safe(path)
            finally:
                recalc._recalc_libreoffice = original
            self.assertFalse(r["ok"])
            self.assertIn("falhou", r["erro"])
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"original")


if __name__ == "__main__":
    unittest.main()

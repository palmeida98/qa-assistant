"""Saida dos scripts em UTF-8, tambem quando e lida por um pipe.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
No Windows, um processo Python com a saida num pipe (como o Claude Code os
corre) escrevia em cp1252: nomes de sheets, colunas e estados com acentos
chegavam trocados ("M�dia"). Cada ponto de entrada reconfigura stdout e
stderr para UTF-8.
"""

import glob
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_XLSX = os.path.join(ROOT, "examples", "demo", "workbook", "plano-demo.xlsx")


def ler(path: str) -> str:
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def pontos_de_entrada() -> list[str]:
    fontes = []
    for pasta in ("scripts", "adapters", "examples"):
        fontes += glob.glob(os.path.join(ROOT, pasta, "**", "*.py"), recursive=True)
    return sorted(f for f in fontes if "node_modules" not in f and 'if __name__ == "__main__":' in ler(f))


class Consola(unittest.TestCase):
    def test_todos_os_pontos_de_entrada_escrevem_em_utf8(self):
        fontes = pontos_de_entrada()
        self.assertGreaterEqual(len(fontes), 10)
        for f in fontes:
            with self.subTest(script=os.path.relpath(f, ROOT)):
                texto = ler(f)
                bloco = texto.split('if __name__ == "__main__":', 1)[1]
                self.assertRegex(bloco, re.compile(r'reconfigure\(encoding="utf-8"'))

    def test_acentos_chegam_intactos_por_um_pipe(self):
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        r = subprocess.run([sys.executable, "-m", "adapters.excel.inspecionar", DEMO_XLSX],
                           cwd=ROOT, capture_output=True, env=env, check=False, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", errors="replace"))
        saida = r.stdout.decode("utf-8")  # estrito: bytes cp1252 soltos dariam UnicodeDecodeError
        self.assertIn("Observações", saida)


if __name__ == "__main__":
    unittest.main()

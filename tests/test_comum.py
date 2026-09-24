"""scripts/comum.py (plano 7.8): leitura do projeto.yaml e do .env partilhada
pelos scripts de um workspace. A redacao (Redator) esta coberta pelos testes do
api_pedido e do testar_mobile.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import api_pedido
import auditar_a11y
import comum
import testar_mobile


class Comum(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def escrever(self, nome, texto, encoding="utf-8"):
        with open(os.path.join(self.ws, nome), "w", encoding=encoding) as f:
            f.write(texto)

    def test_env_com_bom_comentarios_export_e_aspas(self):
        self.escrever(".env", "# comentario\n\nexport A=1\nB = 'dois'\nC=\"tres = 3\"\nSEM_IGUAL\nD=\n",
                      encoding="utf-8-sig")
        self.assertEqual(comum.ler_env_workspace(self.ws), {"A": "1", "B": "dois", "C": "tres = 3", "D": ""})

    def test_env_em_falta_e_vazio(self):
        self.assertEqual(comum.ler_env_workspace(self.ws), {})

    def test_projeto_vazio_e_dicionario_e_em_falta_e_erro(self):
        with self.assertRaises(FileNotFoundError):
            comum.ler_projeto(self.ws)
        self.escrever("projeto.yaml", "")
        self.assertEqual(comum.ler_projeto(self.ws), {})
        self.escrever("projeto.yaml", "projeto: loja\napi:\n  base_url: https://loja.demo/\n", encoding="utf-8-sig")
        self.assertEqual(comum.ler_projeto(self.ws)["api"]["base_url"], "https://loja.demo/")

    def test_os_scripts_usam_o_codigo_comum(self):
        # uma so implementacao: os nomes antigos do api_pedido continuam a funcionar
        self.assertIs(api_pedido.Redator, comum.Redator)
        self.assertIs(api_pedido.ler_env, comum.ler_env)
        self.assertEqual(api_pedido.REDIGIDO, comum.REDIGIDO)
        self.assertIs(testar_mobile.Redator, comum.Redator)
        self.assertFalse(hasattr(auditar_a11y, "api_pedido"))

    def test_projeto_em_falta_mantem_as_mensagens_dos_scripts(self):
        with self.assertRaises(auditar_a11y.Erro) as a:
            auditar_a11y.configuracao(self.ws)
        with self.assertRaises(testar_mobile.Erro) as m:
            testar_mobile.configuracao(self.ws)
        for e in (a.exception, m.exception):
            self.assertIn("nao existe", str(e))
            self.assertIn("projeto.yaml", str(e))
        self.assertIsNone(api_pedido.base_url(self.ws))


if __name__ == "__main__":
    unittest.main()

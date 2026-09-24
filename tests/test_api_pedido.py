"""scripts/api_pedido.py (tecnica-api.md): substituicao de variaveis, redacao de
segredos, sessao entre pedidos e codigos de saida. Usa um servidor HTTP local:
nao depende da rede.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import api_pedido

SENHA = "senha-ficticia-123"
TOKEN = "tok-ficticio-abcdef"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silencio nos testes
        pass

    def _responder(self, codigo, corpo, cab=None):
        dados = json.dumps(corpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        for k, v in (cab or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/auth":
            if corpo.get("password") == SENHA:
                self._responder(200, {"token": TOKEN}, {"Set-Cookie": f"token={TOKEN}"})
            else:
                self._responder(200, {"reason": "Bad credentials"})
        elif self.path == "/itens":
            self._responder(200, {"id": 42, "item": corpo})
        else:
            self._responder(500, {"erro": "interno"})

    def do_GET(self):
        # devolve o que recebeu, para se ver se o segredo chegou ao servidor
        self._responder(200, {"cookie_recebido": self.headers.get("Cookie"), "caminho": self.path})


class ApiPedido(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write(f"# teste\nAPI_USER=utilizador\nAPI_PASSWORD={SENHA}\n")
        with open(os.path.join(self.ws, "projeto.yaml"), "w", encoding="utf-8") as f:
            f.write(f"api:\n  base_url: {self.base}/\n")

    def tearDown(self):
        self.tmp.cleanup()

    def correr(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = api_pedido.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    def auth(self, *extra):
        return self.correr("POST", "/auth", "--json", '{"username": "{{API_USER}}", "password": "{{API_PASSWORD}}"}',
                           *extra)

    def test_nenhum_segredo_na_saida(self):
        rc, out, _ = self.auth("--esperado", "200")
        self.assertEqual(rc, 0)
        self.assertNotIn(SENHA, out)
        self.assertNotIn(TOKEN, out)  # no corpo e no Set-Cookie
        evid = json.loads(out)
        self.assertEqual(evid["pedido"]["corpo"]["password"], api_pedido.REDIGIDO)
        self.assertEqual(evid["resposta"]["corpo"]["token"], api_pedido.REDIGIDO)
        self.assertEqual(evid["resposta"]["cabecalhos"]["Set-Cookie"], api_pedido.REDIGIDO)
        self.assertEqual(evid["pedido"]["corpo"]["username"], "«API_USER»")

    def test_segredo_guardado_e_usado_sem_aparecer(self):
        rc, out, _ = self.auth("--guardar-segredo", "token=TOKEN")
        self.assertEqual(rc, 0)
        self.assertIn("caracteres", json.loads(out)["guardado"]["TOKEN"])
        rc, out, _ = self.correr("GET", "/eco", "-H", "Cookie: token={{TOKEN}}")
        self.assertEqual(rc, 0)
        self.assertNotIn(TOKEN, out)
        self.assertEqual(json.loads(out)["resposta"]["corpo"]["cookie_recebido"], "token=«TOKEN»")
        with open(os.path.join(self.ws, api_pedido.SESSAO), encoding="utf-8") as f:
            self.assertIn("#segredo TOKEN", f.read())

    def test_valor_guardado_nao_secreto_aparece(self):
        rc, out, _ = self.correr("POST", "/itens", "--json", '{"nome": "QA-AUTO-x"}', "--guardar", "id=ITEM_ID")
        self.assertEqual((rc, json.loads(out)["guardado"]), (0, {"ITEM_ID": "42"}))
        rc, out, _ = self.correr("GET", "/itens/{{ITEM_ID}}")
        self.assertEqual(json.loads(out)["resposta"]["corpo"]["caminho"], "/itens/42")

    def test_codigo_diferente_do_esperado_sai_com_1(self):
        rc, out, _ = self.correr("POST", "/rebenta", "--json", "{}", "--esperado", "400")
        self.assertEqual(rc, 1)
        self.assertEqual((json.loads(out)["resposta"]["codigo"], json.loads(out)["confere"]), (500, False))

    def test_variavel_inexistente_sai_com_2_sem_pedido(self):
        rc, out, err = self.correr("GET", "/x/{{NAO_EXISTE}}")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("NAO_EXISTE", err)

    def test_sem_resposta_sai_com_3(self):
        rc, out, _ = self.correr("GET", "http://127.0.0.1:9/nada", "--timeout", "2")
        self.assertEqual(rc, 3)
        self.assertIn("sem resposta", json.loads(out)["erro"])

    def test_caminho_sem_barra_usa_a_base_url(self):
        rc, out, _ = self.correr("GET", "itens/7")
        self.assertEqual((rc, json.loads(out)["resposta"]["corpo"]["caminho"]), (0, "/itens/7"))

    def test_caminho_convertido_pelo_git_bash_explica(self):
        rc, _, err = self.correr("GET", "C:/Program Files/Git/booking")
        self.assertEqual(rc, 2)
        self.assertIn("sem a barra inicial", err)

    def test_saida_relativa_fica_no_workspace(self):
        rc, _, _ = self.auth("--saida", "runs/evidencia/CT-API-001.json")
        self.assertEqual(rc, 0)
        destino = os.path.join(self.ws, "runs", "evidencia", "CT-API-001.json")
        with open(destino, encoding="utf-8") as f:
            texto = f.read()
        self.assertNotIn(SENHA, texto)
        self.assertEqual(json.loads(texto)["resposta"]["codigo"], 200)

    def test_json_invalido_sai_com_2(self):
        self.assertEqual(self.correr("POST", "/itens", "--json", "{nao e json")[0], 2)


if __name__ == "__main__":
    unittest.main()

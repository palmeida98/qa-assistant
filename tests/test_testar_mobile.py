"""scripts/testar_mobile.py (tecnica-mobile.md): caso pelo nome do flow, leitura
do JUnit do Maestro com redacao, credenciais so por MAESTRO_<VAR>, sem alvo =
Blocked. Sem Maestro nem dispositivo: o Maestro e o adb sao simulados.

python -m unittest discover -s tests   (a partir da raiz do qa-assistant)
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import testar_mobile as tm
from comum import Redator

# formato real do `maestro test --format junit` (2.10), com um segredo ficticio na mensagem
JUNIT = """<?xml version='1.0' encoding='UTF-8'?>
<testsuites>
  <testsuite name="Test Suite" tests="2" failures="1" time="19.9">
    <testcase id="CT-MOB-002_bloqueado" name="CT-MOB-002_bloqueado" file="CT-MOB-002_bloqueado.yaml" time="19.5"
      status="ERROR">
      <failure>Assertion is false: "senha-ficticia-9" is visible</failure>
    </testcase>
    <testcase id="CT-MOB-001_login" name="CT-MOB-001_login" file="CT-MOB-001_login.yaml" time="13.3"
      status="SUCCESS"/>
  </testsuite>
</testsuites>"""


class Unidades(unittest.TestCase):
    def test_caso_pelo_nome_do_flow(self):
        self.assertEqual(tm.caso_do_flow("flows/CT-MOB-003_carrinho.yaml"), "CT-MOB-003")
        self.assertEqual(tm.caso_do_flow("CT-LOGIN-001.yaml"), "CT-LOGIN-001")
        self.assertIsNone(tm.caso_do_flow("login.yaml"))

    def test_junit_com_redacao(self):
        res = tm.ler_junit(JUNIT, Redator({"SENHA": "senha-ficticia-9"}))
        self.assertEqual([(r["caso"], r["estado_sugerido"]) for r in res],
                         [("CT-MOB-002", "Fail"), ("CT-MOB-001", "Pass")])
        self.assertNotIn("senha-ficticia-9", json.dumps(res))
        self.assertIn("«SENHA»", res[0]["mensagem"])


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        os.makedirs(os.path.join(self.ws, "flows"))
        for nome in ("CT-MOB-001_login.yaml", "CT-MOB-002_bloqueado.yaml"):
            with open(os.path.join(self.ws, "flows", nome), "w", encoding="utf-8") as f:
                f.write("appId: loja.demo\n---\n- launchApp\n")
        self.projeto("android")
        with open(os.path.join(self.ws, ".env"), "w", encoding="utf-8") as f:
            f.write("LOJA_U=ana\nLOJA_P=senha-ficticia-9\n")

    def tearDown(self):
        self.tmp.cleanup()

    def projeto(self, plataforma, variaveis="[LOJA_U, LOJA_P]"):
        with open(os.path.join(self.ws, "projeto.yaml"), "w", encoding="utf-8") as f:
            f.write(f"mobile:\n  plataforma: {plataforma}\n  flows: flows\n  variaveis: {variaveis}\n")

    def correr(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = tm.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    def test_credenciais_so_em_variaveis_maestro(self):
        env, segredos = tm.ambiente_maestro(self.ws, ["LOJA_U", "LOJA_P"])
        self.assertEqual(env, {"MAESTRO_LOJA_U": "ana", "MAESTRO_LOJA_P": "senha-ficticia-9"})
        self.assertEqual(set(segredos), {"LOJA_U", "LOJA_P"})

    def test_variavel_sem_valor_sai_com_2(self):
        self.projeto("web", "[NAO_EXISTE]")
        with mock.patch.object(tm.shutil, "which", return_value="maestro"):
            rc, _, err = self.correr()
        self.assertEqual(rc, 2)
        self.assertIn("NAO_EXISTE", err)

    def test_plataforma_invalida_sai_com_2(self):
        self.projeto("windows-phone")
        self.assertEqual(self.correr()[0], 2)

    def test_flow_sem_id_de_caso_sai_com_2(self):
        with open(os.path.join(self.ws, "flows", "sem_id.yaml"), "w", encoding="utf-8") as f:
            f.write("---\n")
        rc, _, err = self.correr(os.path.join(self.ws, "flows", "sem_id.yaml"))
        self.assertEqual(rc, 2)
        self.assertIn("sem ID de caso", err)

    def test_sem_dispositivo_android_da_blocked_e_nao_corre_o_maestro(self):
        def which(nome):
            return None if nome == "adb" else "maestro"
        with mock.patch.object(tm.shutil, "which", side_effect=which), \
                mock.patch.object(tm.subprocess, "run") as run:
            rc, out, _ = self.correr("--saida", "runs/m.json")
        self.assertEqual(rc, 3)
        run.assert_not_called()
        with open(os.path.join(self.ws, "runs", "m.json"), encoding="utf-8") as f:
            rel = json.load(f)
        self.assertEqual({r["estado_sugerido"] for r in rel["resultados"]}, {"Blocked"})
        self.assertIn("adb nao encontrado", out)

    def test_web_corre_o_maestro_com_as_variaveis_no_ambiente(self):
        self.projeto("web")

        def correr_maestro(cmd, **kw):
            destino = cmd[cmd.index("--output") + 1]
            with open(destino, "w", encoding="utf-8") as f:
                f.write(JUNIT)
            self.assertEqual(kw["env"]["MAESTRO_LOJA_P"], "senha-ficticia-9")
            self.assertNotIn("senha-ficticia-9", " ".join(cmd))  # nunca na linha de comandos
            return mock.Mock(stdout="", stderr="")

        with mock.patch.object(tm.shutil, "which", return_value="maestro"), \
                mock.patch.object(tm.subprocess, "run", side_effect=correr_maestro):
            rc, out, _ = self.correr()
        self.assertEqual(rc, 0)
        self.assertIn("CT-MOB-001: Pass", out)
        self.assertIn("CT-MOB-002: Fail", out)
        self.assertNotIn("senha-ficticia-9", out)


if __name__ == "__main__":
    unittest.main()

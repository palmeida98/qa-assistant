"""scripts/api_request.py (technique-api.md): variable substitution, redaction of
secrets, session between requests and exit codes. Uses a local HTTP server:
does not depend on the network.

python -m unittest discover -s tests   (from the root of qa-assistant)
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

import api_request

API_PASS = "fictional-value-123"
TOKEN = "fictional-tok-abcdef"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence in the tests
        pass

    def _answer(self, status, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/auth":
            if body.get("password") == API_PASS:
                self._answer(200, {"token": TOKEN}, {"Set-Cookie": f"token={TOKEN}"})
            else:
                self._answer(200, {"reason": "Bad credentials"})
        elif self.path == "/items":
            self._answer(200, {"id": 42, "item": body})
        else:
            self._answer(500, {"error": "internal"})

    def do_GET(self):
        # returns what it received, to see whether the secret reached the server
        self._answer(200, {"cookie_received": self.headers.get("Cookie"), "path": self.path})


class ApiRequest(unittest.TestCase):
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
            f.write(f"# test\nAPI_USER=user\nAPI_PASSWORD={API_PASS}\n")
        with open(os.path.join(self.ws, "project.yaml"), "w", encoding="utf-8") as f:
            f.write(f"api:\n  base_url: {self.base}/\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = api_request.main([self.ws, *args])
        return rc, out.getvalue(), err.getvalue()

    def auth(self, *extra):
        return self.run_it("POST", "/auth", "--json", '{"username": "{{API_USER}}", "password": "{{API_PASSWORD}}"}',
                           *extra)

    def test_no_secret_in_the_output(self):
        rc, out, _ = self.auth("--expected", "200")
        self.assertEqual(rc, 0)
        self.assertNotIn(API_PASS, out)
        self.assertNotIn(TOKEN, out)  # in the body and in Set-Cookie
        evid = json.loads(out)
        self.assertEqual(evid["request"]["body"]["password"], api_request.REDACTED)
        self.assertEqual(evid["response"]["body"]["token"], api_request.REDACTED)
        self.assertEqual(evid["response"]["headers"]["Set-Cookie"], api_request.REDACTED)
        self.assertEqual(evid["request"]["body"]["username"], "«API_USER»")

    def test_a_secret_saved_and_used_without_showing(self):
        rc, out, _ = self.auth("--save-secret", "token=TOKEN")
        self.assertEqual(rc, 0)
        self.assertIn("characters", json.loads(out)["saved"]["TOKEN"])
        rc, out, _ = self.run_it("GET", "/echo", "-H", "Cookie: token={{TOKEN}}")
        self.assertEqual(rc, 0)
        self.assertNotIn(TOKEN, out)
        self.assertEqual(json.loads(out)["response"]["body"]["cookie_received"], "token=«TOKEN»")
        with open(os.path.join(self.ws, api_request.SESSION), encoding="utf-8") as f:
            self.assertIn("#secret TOKEN", f.read())

    def test_a_saved_value_that_is_not_secret_is_shown(self):
        rc, out, _ = self.run_it("POST", "/items", "--json", '{"name": "QA-AUTO-x"}', "--save", "id=ITEM_ID")
        self.assertEqual((rc, json.loads(out)["saved"]), (0, {"ITEM_ID": "42"}))
        rc, out, _ = self.run_it("GET", "/items/{{ITEM_ID}}")
        self.assertEqual(json.loads(out)["response"]["body"]["path"], "/items/42")

    def test_a_status_other_than_expected_exits_with_1(self):
        rc, out, _ = self.run_it("POST", "/breaks", "--json", "{}", "--expected", "400")
        self.assertEqual(rc, 1)
        self.assertEqual((json.loads(out)["response"]["status"], json.loads(out)["matches"]), (500, False))

    def test_a_missing_variable_exits_with_2_without_a_request(self):
        rc, out, err = self.run_it("GET", "/x/{{DOES_NOT_EXIST}}")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("DOES_NOT_EXIST", err)

    def test_no_response_exits_with_3(self):
        rc, out, _ = self.run_it("GET", "http://127.0.0.1:9/nothing", "--timeout", "2")
        self.assertEqual(rc, 3)
        self.assertIn("no response", json.loads(out)["error"])

    def test_a_path_without_a_slash_uses_the_base_url(self):
        rc, out, _ = self.run_it("GET", "items/7")
        self.assertEqual((rc, json.loads(out)["response"]["body"]["path"]), (0, "/items/7"))

    def test_a_path_converted_by_git_bash_is_explained(self):
        rc, _, err = self.run_it("GET", "C:/Program Files/Git/booking")
        self.assertEqual(rc, 2)
        self.assertIn("without the leading slash", err)

    def test_a_relative_output_stays_in_the_workspace(self):
        rc, _, _ = self.auth("--output", "runs/evidence/TC-API-001.json")
        self.assertEqual(rc, 0)
        target = os.path.join(self.ws, "runs", "evidence", "TC-API-001.json")
        with open(target, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(API_PASS, text)
        self.assertEqual(json.loads(text)["response"]["status"], 200)

    def test_invalid_json_exits_with_2(self):
        self.assertEqual(self.run_it("POST", "/items", "--json", "{not json")[0], 2)


if __name__ == "__main__":
    unittest.main()

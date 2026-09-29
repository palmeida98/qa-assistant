"""adapters/tracker/http.py (technique-jira-xray.md section 2.7; plan 10.1): retries only for 429/502/503/504 on
reads and idempotent writes, Retry-After, backoff with jitter, one re-authentication on 401, a create never retried
blindly, pagination with a guard, redaction and the --record files. The fake transport of adapters/tracker/testing.py,
plus a local http.server on 127.0.0.1 for the real transport's timeout and statuses. No network.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import sys
import tempfile
import threading
import time
import typing
import unittest
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

from adapters.tracker.errors import (
    CONFLICT,
    INVALID,
    UNAVAILABLE,
    AuthFailed,
    HttpError,
    InvalidInput,
    RateLimited,
    Unavailable,
    UncertainWrite,
)
from adapters.tracker.http import (
    HttpClient,
    Recorder,
    SecretRedactor,
    TransportTimeout,
    paginate_offset,
    paginate_token,
    urllib_transport,
)
from adapters.tracker.testing import FakeTransport

TOKEN = "fictional-value-abc"
BASE = "https://demo-shop.atlassian.net"


def ok(json_body=None, **kw):
    return {"status": 200, "json": json_body if json_body is not None else {}, **kw}


class Client(unittest.TestCase):
    def make(self, responses, **kw):
        self.fake = FakeTransport(responses, fail_at=kw.pop("fail_at", None))
        self.sleeps = []
        self.redactor = SecretRedactor({"DS_JIRA_TOKEN": TOKEN})
        return HttpClient(self.fake, self.redactor, BASE, auth=lambda: {"Authorization": f"Basic {TOKEN}"},
                          sleep=self.sleeps.append, rng=lambda: 1.0, **kw)

    def test_a_read_is_retried_on_503_then_answers(self):
        c = self.make([{"status": 503}, {"status": 502}, ok({"k": 1})])
        self.assertEqual(c.get("/rest/api/3/myself").json(), {"k": 1})
        self.assertEqual(len(self.fake.requests), 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])      # exponential backoff (jitter at its maximum)

    def test_jitter_is_between_half_and_all_of_the_step(self):
        c = self.make([])
        c.rng = lambda: 0.0
        self.assertEqual([c.retry_delay(None, n) for n in (1, 2, 3)], [0.5, 1.0, 2.0])
        self.assertEqual(c.retry_delay(None, 30), 30.0)   # capped step (60) at its minimum jitter

    def test_retry_after_seconds_and_its_cap(self):
        c = self.make([{"status": 429, "headers": {"Retry-After": "7"}},
                       {"status": 429, "headers": {"Retry-After": "999"}}, ok()])
        c.get("/x")
        self.assertEqual(self.sleeps, [7.0, 120.0])

    def test_retry_after_as_an_http_date(self):
        c = self.make([])
        c.clock = lambda: datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        from adapters.tracker.http import Response
        r = Response(429, {"Retry-After": "Mon, 28 Sep 2026 10:00:05 GMT"})
        self.assertEqual(c.retry_delay(r, 1), 5.0)

    def test_at_most_five_attempts_then_exit_3(self):
        c = self.make([{"status": 429}] * 5)
        with self.assertRaises(RateLimited) as e:
            c.get("/x")
        self.assertEqual((len(self.fake.requests), e.exception.code), (5, UNAVAILABLE))

    def test_a_create_is_never_retried_blindly(self):
        for status, error in ((503, UncertainWrite), (504, UncertainWrite), (429, RateLimited)):
            with self.subTest(status=status):
                c = self.make([{"status": status}])
                with self.assertRaises(error):
                    c.post("/rest/api/3/issue", json_body={"fields": {}})
                self.assertEqual(len(self.fake.requests), 1)
        c = self.make([{"raise": "timeout"}])
        with self.assertRaises(UncertainWrite) as e:
            c.post("/rest/api/3/issue", json_body={})
        self.assertIn("marker", str(e.exception))

    def test_a_read_sent_with_post_is_retried(self):
        c = self.make([{"status": 503}, {"raise": "timeout"}, ok({"issues": []})])
        c.post("/rest/api/3/search/jql", json_body={"jql": "project = DS"}, read=True)
        self.assertEqual(len(self.fake.requests), 3)
        self.assertEqual(self.fake.writes(), [])

    def test_a_put_is_idempotent_and_a_patch_only_when_said(self):
        c = self.make([{"status": 503}, ok()])
        c.request("PUT", "/x", json_body={})
        self.assertEqual(len(self.fake.requests), 2)
        c = self.make([{"status": 503}])
        with self.assertRaises(UncertainWrite):
            c.request("PATCH", "/x", json_body=[])
        c = self.make([{"status": 503}, ok()])
        c.request("PATCH", "/x", json_body=[], idempotent=True)
        self.assertEqual(len(self.fake.requests), 2)

    def test_no_answer_on_a_read_is_retried_then_exit_3(self):
        c = self.make([{"raise": "timeout"}] * 5)
        with self.assertRaises(Unavailable) as e:
            c.get("/x")
        self.assertNotIsInstance(e.exception, UncertainWrite)
        self.assertEqual(len(self.fake.requests), 5)

    def test_one_reauthentication_on_401(self):
        renewed = []
        c = self.make([{"status": 401}, ok({"ok": True})])
        c.reauth = lambda: renewed.append(1)
        self.assertEqual(c.get("/x").json(), {"ok": True})
        self.assertEqual(renewed, [1])
        c = self.make([{"status": 401}, {"status": 401}])
        c.reauth = lambda: None
        with self.assertRaises(AuthFailed):
            c.get("/x")
        self.assertEqual(len(self.fake.requests), 2)

    def test_401_without_reauthentication_and_403_are_exit_3_without_the_token(self):
        for status in (401, 403):
            with self.subTest(status=status):
                c = self.make([{"status": status, "body": f"bad credentials {TOKEN}"}])
                with self.assertRaises(AuthFailed) as e:
                    c.get(f"/x?token={TOKEN}")
                self.assertEqual(e.exception.code, UNAVAILABLE)
                self.assertNotIn(TOKEN, str(e.exception))
                self.assertIn("«DS_JIRA_TOKEN»", str(e.exception))

    def test_other_statuses(self):
        for status, code in ((404, CONFLICT), (409, CONFLICT), (412, CONFLICT), (400, INVALID)):
            with self.subTest(status=status):
                c = self.make([{"status": status, "json": {"errorMessages": ["no"]}}])
                with self.assertRaises(HttpError) as e:
                    c.get("/x")
                self.assertEqual((e.exception.status, e.exception.code), (status, code))
        c = self.make([{"status": 500}])
        with self.assertRaises(Unavailable):
            c.get("/x")
        self.assertEqual(len(self.fake.requests), 1)   # 500 is not retried

    def test_url_params_and_json_body(self):
        c = self.make([ok()])
        c.post("/rest/api/3/search/jql", json_body={"jql": "project = DS"}, params={"fields": "summary"}, read=True)
        r = self.fake.requests[0]
        self.assertEqual(r.url, f"{BASE}/rest/api/3/search/jql?fields=summary")
        self.assertEqual((r.json(), r.headers["Content-Type"]), ({"jql": "project = DS"}, "application/json"))
        self.assertTrue(r.read_only)

    def test_a_token_learnt_at_run_time_is_redacted(self):
        c = self.make([{"status": 403, "body": "xray-token-zzz is not allowed"}])
        c.redactor.add("xray token", "xray-token-zzz")
        with self.assertRaises(AuthFailed) as e:
            c.get("/x")
        self.assertNotIn("xray-token-zzz", str(e.exception))

    def test_record_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.make([ok({"token": TOKEN, "key": "DS-1"}, headers={"Set-Cookie": "s=1"})])
            c.recorder = Recorder(os.path.join(tmp, "rec"), self.redactor)
            c.post("/api/v2/authenticate", json_body={"client_id": "id", "client_secret": "s3"}, read=True,
                   params={"sig": TOKEN})
            files = os.listdir(os.path.join(tmp, "rec"))
            self.assertEqual(files, ["0001.json"])
            with open(os.path.join(tmp, "rec", files[0]), encoding="utf-8") as f:
                text = f.read()
            doc = json.loads(text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual(doc["request"]["headers"]["Authorization"], "«redacted»")
        self.assertEqual(doc["request"]["body"]["client_secret"], "«redacted»")
        self.assertEqual(doc["response"]["body"], {"token": "«redacted»", "key": "DS-1"})
        self.assertEqual(doc["response"]["headers"]["set-cookie"], "«redacted»")


class Pagination(unittest.TestCase):
    def test_token_pagination_to_the_end(self):
        pages = {None: ([1, 2], "t1"), "t1": ([3], "t2"), "t2": ([4], None)}
        self.assertEqual(paginate_token(lambda t: pages[t]), [1, 2, 3, 4])

    def test_the_guard_asks_for_a_narrower_query(self):
        with self.assertRaises(InvalidInput) as e:
            paginate_token(lambda t: (list(range(100)), "next" + str(t)), max_items=250)
        self.assertIn("narrow", str(e.exception))

    def test_a_repeated_token_stops(self):
        with self.assertRaises(Unavailable):
            paginate_token(lambda t: ([1], "same"))

    def test_offset_pagination(self):
        data = list(range(250))
        calls = []

        def fetch(start, limit):
            calls.append(start)
            return data[start:start + limit], len(data)
        self.assertEqual(paginate_offset(fetch, limit=100), data)
        self.assertEqual(calls, [0, 100, 200])
        with self.assertRaises(InvalidInput):
            paginate_offset(lambda s, n: ([0] * n, 6000))


class _Handler(BaseHTTPRequestHandler):
    hits: typing.ClassVar[list] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        _Handler.hits.append(self.path)
        if self.path == "/slow":
            time.sleep(1.5)
        status = {"/busy": 503, "/missing": 404}.get(self.path, 200)
        if self.path == "/busy" and _Handler.hits.count("/busy") > 1:
            status = 200
        body = json.dumps({"path": self.path}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RealTransport(unittest.TestCase):
    """urllib_transport against a local server on 127.0.0.1 only."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def client(self, **kw):
        return HttpClient(urllib_transport, SecretRedactor(), self.url, sleep=lambda s: None, **kw)

    def test_statuses_come_back_as_responses_and_a_503_is_retried(self):
        self.assertEqual(self.client().get("/busy").json(), {"path": "/busy"})
        self.assertEqual(_Handler.hits.count("/busy"), 2)
        with self.assertRaises(HttpError) as e:
            self.client().get("/missing")
        self.assertEqual(e.exception.status, 404)

    def test_the_timeout(self):
        from adapters.tracker.http import Request
        with self.assertRaises(TransportTimeout):
            urllib_transport(Request("GET", self.url + "/slow", timeout=0.3))
        with self.assertRaises(Unavailable):
            self.client(timeout=0.3, max_attempts=2).get("/slow")


if __name__ == "__main__":
    unittest.main()

"""Local test site and recording proxy for the qa-audit integration tests (docs/qa-audit.md §15).

FixtureSite serves tests/fixtures/site on 127.0.0.1 and records every request (method, path).
RecordingProxy is an HTTP forward proxy that records every host the browser contacts; it forwards only to the
fixture site and refuses everything else (HTTPS CONNECT included), so nothing leaves the machine.
"""

from __future__ import annotations

import http.client
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")


class _SiteHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _record(self):
        self.server.requests.append((self.command, self.path))

    def do_GET(self):
        self._record()
        super().do_GET()

    def do_HEAD(self):
        self._record()
        super().do_HEAD()

    def do_POST(self):
        self._record()
        self.send_error(405, "fixture site: nothing may be posted")


class FixtureSite:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_SiteHandler, directory=SITE))
        self.httpd.requests = []
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def requests(self) -> list[tuple[str, str]]:
        return self.httpd.requests

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class _ProxyHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self.server.hosts.append(self.path)
        self.send_error(403, "recording proxy: external traffic refused")

    def _forward(self):
        parts = urlsplit(self.path)
        host = parts.netloc or self.headers.get("Host", "")
        self.server.hosts.append(host)
        if host != self.server.allowed:
            self.send_error(403, "recording proxy: external traffic refused")
            return
        conn = http.client.HTTPConnection(host, timeout=30)
        body = None
        if self.headers.get("Content-Length"):
            body = self.rfile.read(int(self.headers["Content-Length"]))
        path = parts.path + ("?" + parts.query if parts.query else "")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("proxy-connection", "connection")}
        conn.request(self.command, path or "/", body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        conn.close()

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_OPTIONS = _forward


class RecordingProxy:
    def __init__(self, allowed_host: str):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        self.httpd.hosts = []
        self.httpd.allowed = allowed_host
        self.server = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def hosts(self) -> list[str]:
        return self.httpd.hosts

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

"""Local test site and recording proxy for the qa-audit integration tests (docs/qa-audit.md §15).

FixtureSite serves tests/fixtures/site on 127.0.0.1 and records every request (method, path); with tls=True it
serves the same site over HTTPS. RecordingProxy is an HTTP forward proxy that records every host the browser
contacts; it forwards only to the fixture site and refuses everything else (HTTPS CONNECT included), so nothing
leaves the machine.
"""

from __future__ import annotations

import http.client
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")

# planted security defects (docs/qa-audit.md §16): headers per path. Every other page sends no security header,
# which the security module reports once per check for the whole site.
EXTRA_HEADERS = {
    "/sec.html": [("Server", "Apache/2.4.1 (Unix)"), ("X-Powered-By", "PHP/5.6.40"),
                  ("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-eval'"),
                  ("Set-Cookie", "sessionid=fixture-not-a-secret; Path=/"),
                  ("Set-Cookie", "theme=dark; Path=/; HttpOnly")],
    "/secure.html": [("Content-Security-Policy", "default-src 'self'; frame-ancestors 'self'"),
                     ("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"),
                     ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")],
}
# planted performance defect (docs/qa-audit.md §17): the server waits before answering (TTFB "poor" > 1800 ms)
SLOW_PATHS = {"/slow.html": 2.0}
# planted functional defects (docs/qa-audit.md §18): a server error and a chain of three redirects
ERROR_PATHS = {"/error500.html": 500}
REDIRECTS = {"/r1": "/r2", "/r2": "/r3", "/r3": "/about.html"}
# planted visual change (docs/qa-audit.md §19): with FixtureSite.variant on, /visual.html serves visual-v2.html
VARIANTS = {"/visual.html": "visual-v2.html"}
GIT_OPENSSL = (r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files\Git\mingw64\bin\openssl.exe")


def find_openssl() -> str | None:
    """openssl for the HTTPS fixture: PATH, or the copy that ships with Git for Windows."""
    return shutil.which("openssl") or next((p for p in GIT_OPENSSL if os.path.isfile(p)), None)


class _SiteHandler(SimpleHTTPRequestHandler):
    server_version = "FixtureServer"      # no version number, unlike sec.html
    sys_version = ""

    def log_message(self, *args):
        pass

    def _extra(self):
        return EXTRA_HEADERS.get(urlsplit(self.path).path, [])

    def _record(self):
        self.server.requests.append((self.command, self.path))

    def translate_path(self, path):
        alt = VARIANTS.get(urlsplit(path).path) if getattr(self.server, "variant", False) else None
        return os.path.join(SITE, alt) if alt else super().translate_path(path)

    def version_string(self):
        return next((v for n, v in self._extra() if n == "Server"), self.server_version)

    def end_headers(self):
        for name, value in self._extra():
            if name != "Server":
                self.send_header(name, value)
        super().end_headers()

    def _special(self) -> bool:
        path = urlsplit(self.path).path
        if path in ERROR_PATHS:
            self.send_error(ERROR_PATHS[path], "fixture: planted server error")
            return True
        if path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        return False

    def do_GET(self):
        self._record()
        delay = SLOW_PATHS.get(urlsplit(self.path).path)
        if delay:
            time.sleep(delay)
        if not self._special():
            super().do_GET()

    def do_HEAD(self):
        self._record()
        if not self._special():
            super().do_HEAD()

    def do_POST(self):
        self._record()
        self.send_error(405, "fixture site: nothing may be posted")


class FixtureSite:
    """tls=True: HTTPS with a self-signed certificate valid for two days (planted: expiring certificate)."""

    def __init__(self, tls: bool = False):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_SiteHandler, directory=SITE))
        self.httpd.requests = []
        self.httpd.variant = False
        self.port = self.httpd.server_address[1]
        self._tmp = None
        if tls:
            self._tmp = tempfile.TemporaryDirectory()
            cert, key = os.path.join(self._tmp.name, "cert.pem"), os.path.join(self._tmp.name, "key.pem")
            subprocess.run([find_openssl() or "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-days", "2", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                            "-keyout", key, "-out", cert], check=True, capture_output=True)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.url = f"{'https' if tls else 'http'}://127.0.0.1:{self.port}/"
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def requests(self) -> list[tuple[str, str]]:
        return self.httpd.requests

    @property
    def variant(self) -> bool:
        return self.httpd.variant

    @variant.setter
    def variant(self, on: bool) -> None:
        self.httpd.variant = on

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._tmp:
            self._tmp.cleanup()


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

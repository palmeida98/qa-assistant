"""
The local server of the interface (docs/qa-ui.md sections 2 and 11).

Loopback only. Every /api/ request needs the token of this start in the X-QA-Token header, and a request whose Host
is not this server's is refused, so a page of another site cannot reach the interface through the browser. JSON in
and out. The page and its script are files of qa_ui/static/, with no external address; they hold no data, so they
are served without the token.

    App(workspace, runner, token).handle(method, path, body) -> (status, payload)     the routes, testable alone
    serve(workspace, runner, port)                                 -> (server, url)   the HTTP server around them
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import yaml

from . import intake, plan, runs, suggest, workspace

HOST = "127.0.0.1"
MAX_BODY = 12 * 1024 * 1024         # a 5 MB file in base64, with room
CHECKS_TTL = 30                     # seconds the readiness checks are kept (they look for tools on this computer)
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/static/app.css": ("app.css", "text/css; charset=utf-8"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
CASE_KEYS = (*intake.FIELDS, "source", "last_status", "in_library", "flags", "why")


class BadRequest(ValueError):
    """The request's body is not what the route takes (status 400)."""


def clean_cases(value) -> list[dict]:
    """The cases a request carries, reduced to the fields of a case (the page sends back what the intake gave)."""
    if not isinstance(value, list):
        raise BadRequest("`cases` must be a list")
    out = []
    for c in value:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"].strip():
            raise BadRequest("every case needs an `id`")
        case = {k: ("" if c.get(k) is None else str(c.get(k))) for k in (*intake.FIELDS, "source", "last_status",
                                                                         "why")}
        case["id"] = case["id"].strip()
        case["in_library"] = c.get("in_library") if isinstance(c.get("in_library"), bool) else None
        case["flags"] = [str(f) for f in c.get("flags") or [] if isinstance(f, str)]
        out.append(case)
    return out


class App:
    def __init__(self, ws: str, runner: runs.Runner, token: str | None = None):
        self.ws = os.path.abspath(ws)
        self.runner = runner
        self.token = token or secrets.token_urlsafe(24)
        self._checks: tuple[float, list] | None = None

    # ------------------------------------------------------------------ helpers

    def checks(self, proj: dict, refresh: bool = False) -> list:
        now = time.monotonic()
        if refresh or self._checks is None or now - self._checks[0] > CHECKS_TTL:
            self._checks = (now, workspace.readiness(self.ws, proj))
        return self._checks[1]

    def state(self, proj: dict, refresh: bool = False) -> dict:
        st = workspace.state(self.ws, proj)
        current = self.runner.current()
        key, reason = suggest.find_test_environment(proj)
        st.update(checks=[{"ok": ok, "tool": tool, "detail": detail} for ok, tool, detail in
                          self.checks(proj, refresh)],
                  claude=bool(self.runner.claude()), run=dict(current.public(), tail=[]) if current else None,
                  runs=self.runner.listing(), test_environment=key, test_environment_reason=reason,
                  limits={"max_turns": self.runner.max_turns, "timeout_minutes": self.runner.timeout_minutes})
        return st

    def annotated(self, proj: dict, cases: list[dict]) -> list[dict]:
        """The cases marked against the library as it is now (a case may have been added since the intake)."""
        lib = intake.load_library(self.ws, proj)
        for c in cases:
            c["flags"] = [f for f in c["flags"] if f != "not-in-library"]
        intake.annotate(cases, lib)
        return cases

    # ------------------------------------------------------------------ routes

    def handle(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        """One /api/ request, already authorised. Returns (status, JSON payload)."""
        body = body if isinstance(body, dict) else {}
        url = urlsplit(path)
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        try:
            proj = workspace.project(self.ws)
            return 200, self.route(method, parts, url.query, body, proj)
        except (BadRequest, intake.IntakeError) as e:
            return 400, {"error": str(e)}
        except (workspace.NotFound, runs.NotFound) as e:
            return 404, {"error": str(e)}
        except (workspace.Refused, runs.Refused, plan.PlanError) as e:
            return 409, {"error": str(e)}
        except FileNotFoundError as e:
            return 409, {"error": str(e)}
        except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as e:
            # e.g. a project.yaml that is not valid YAML: said to the user, never a dropped connection
            return 500, {"error": f"the request failed ({type(e).__name__}): {e}"}

    def route(self, method: str, parts: list[str], query: str, body: dict, proj: dict) -> dict:
        if parts[:1] != ["api"]:
            raise workspace.NotFound("no such route")
        rest = parts[1:]
        if method == "GET" and rest == ["state"]:
            return self.state(proj, refresh="refresh=1" in query)
        if method == "POST" and rest == ["intake"]:
            file = body.get("file")
            if file is not None and not (isinstance(file, dict) and isinstance(file.get("name"), str)
                                         and isinstance(file.get("data"), str)):
                raise BadRequest("`file` must be {name, data} with the data in base64")
            text = body.get("text")
            if text is not None and not isinstance(text, str):
                raise BadRequest("`text` must be text")
            if not (text or file or body.get("library")):
                raise BadRequest("nothing to read: paste text, choose a file or load the library")
            return intake.intake(self.ws, proj, text=text, file=file, library=bool(body.get("library")))
        if method == "POST" and rest == ["suggest"]:
            moved = body.get("moved") if isinstance(body.get("moved"), dict) else {}
            return suggest.suggest(clean_cases(body.get("cases")), proj, self.checks(proj), self.runner.claude(),
                                   moved)
        if method == "POST" and rest == ["ask-claude"]:
            notes = [str(n) for n in body.get("notes") or [] if isinstance(n, str)]
            return self.runner.ask_claude(clean_cases(body.get("cases")), notes, bool(body.get("proposals")))
        if method == "POST" and rest == ["plan"]:
            cases = self.annotated(proj, clean_cases(body.get("cases")))
            assignments = body.get("assignments")
            cards = body.get("cards")
            if not isinstance(assignments, dict) or not isinstance(cards, list):
                raise BadRequest("`assignments` must be an object and `cards` a list")
            assignments = {str(k): {"module": v.get("module")} for k, v in assignments.items() if isinstance(v, dict)}
            out = plan.build(self.ws, cases, assignments, [str(c) for c in cards])
            if body.get("save"):
                if not out["batches"]:
                    raise plan.PlanError("there is no batch to save")
                plan.save(self.ws, out)
                out["saved"] = [b["name"] for b in out["batches"]]
            return out
        if method == "POST" and rest == ["cases"]:
            if body.get("confirm") is not None:
                return self.runner.new_cases(proj, confirm=str(body["confirm"]))
            return self.runner.new_cases(proj, clean_cases(body.get("cases")))
        if rest[:1] == ["runs"]:
            if method == "POST" and len(rest) == 1:
                if body.get("audit") is not None:
                    return self.runner.start_audit(proj, str(body["audit"])).public()
                if not isinstance(body.get("batch"), str):
                    raise BadRequest("`batch` (a planned batch's name) or `audit` (a11y or site) is needed")
                return self.runner.start_batch(proj, body["batch"], body.get("environment"),
                                               body.get("build")).public()
            if method == "GET" and len(rest) == 2:
                out = self.runner.get(rest[1])
                if out["batch"]:
                    try:
                        out["run_log"] = workspace.read_run_log(self.ws, proj, out["batch"], with_applied=False)
                    except (workspace.NotFound, workspace.Refused) as e:
                        out["run_log"] = {"error": str(e)}
                return out
            if method == "POST" and len(rest) == 3 and rest[2] == "stop":
                return self.runner.stop(rest[1])
        if rest[:1] == ["runlogs"] and len(rest) >= 2:
            name = rest[1]
            if method == "GET" and len(rest) == 2:
                out = workspace.read_run_log(self.ws, proj, name)
                out["check"] = workspace.check_apply(self.ws, proj, name)
                planned = next((b for b in workspace.batches(self.ws) if "batch_" + b["name"] == out["name"]), None)
                out["planned"] = planned["cases"] if planned else []
                return out
            if method == "POST" and len(rest) == 4 and rest[2] == "items":
                action = body.get("action")
                if not isinstance(action, str):
                    raise BadRequest("`action` is needed: approve, edit, link, drop or answer")
                return workspace.edit_item(self.ws, proj, name, rest[3], action, body)
            if method == "POST" and len(rest) == 3 and rest[2] == "apply":
                return self.runner.apply(proj, name, confirm=body.get("confirm") is True)
        raise workspace.NotFound("no such route")


def make_handler(app: App, port_of):
    static = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    class Handler(BaseHTTPRequestHandler):
        server_version = "qa-ui"
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass                                  # a request line may hold the token

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            port = port_of()
            return self.headers.get("Host", "") in (f"{HOST}:{port}", f"localhost:{port}")

        def _authorised(self) -> bool:
            return hmac.compare_digest(self.headers.get("X-QA-Token", ""), app.token)

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "this address is not the interface's"})
            path = urlsplit(self.path).path
            if path in STATIC:
                name, content_type = STATIC[path]
                with open(os.path.join(static, name), "rb") as f:
                    return self._send(200, f.read(), content_type,
                                      {"Content-Security-Policy": CSP} if name == "index.html" else None)
            if not path.startswith("/api/"):
                return self._json(404, {"error": "no such page"})
            if not self._authorised():
                return self._json(403, {"error": "the token is missing or wrong: open the address the command "
                                                 "printed"})
            self._json(*app.handle("GET", self.path))

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": "this address is not the interface's"})
            if not urlsplit(self.path).path.startswith("/api/"):
                return self._json(404, {"error": "no such page"})
            if not self._authorised():
                return self._json(403, {"error": "the token is missing or wrong: open the address the command "
                                                 "printed"})
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self._json(400, {"error": "the body must be JSON (Content-Type: application/json)"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                return self._json(400, {"error": "the body is missing or too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"error": "the body is not valid JSON"})
            self._json(*app.handle("POST", self.path, body))

    return Handler


class _Server(ThreadingHTTPServer):
    """On Windows, SO_REUSEADDR (which HTTPServer sets) lets a second server bind a port another one is using, so a
    taken port gave no error and two interfaces shared it: there the port is asked for exclusively instead."""
    if os.name == "nt":
        allow_reuse_address = False

        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()


def serve(ws: str, runner: runs.Runner, port: int = 8765, token: str | None = None) -> tuple[ThreadingHTTPServer, str]:
    """The server bound to the loopback address (port 0: a free one) and the address to open. OSError when the
    port is taken."""
    app = App(ws, runner, token)
    box = {}
    server = _Server((HOST, port), make_handler(app, lambda: box["port"]))
    server.daemon_threads = True
    box["port"] = server.server_address[1]
    server.app = app
    return server, f"http://{HOST}:{box['port']}/?token={app.token}"

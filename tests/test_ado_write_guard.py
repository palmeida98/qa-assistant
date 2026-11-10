"""The guarded Azure DevOps write server (scripts/ado_write_guard.py).

Everything is fictional: an in-memory fake of the Azure DevOps REST calls, temporary files, no network, no real ticket.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import ado_write_guard as g

ME = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"
FAKE_PAT = "FICTIONAL-PAT-VALUE-0123456789"      # a made-up string, not a credential: used only to prove it is never printed


def wi(wid, assigned=ME, project="EXAMPLE", rev=4, name="Ana Example"):
    a = None if assigned is None else ({"displayName": name, "id": assigned} if assigned != "noid" else {"displayName": name})
    f = {"System.Id": wid, "System.TeamProject": project, "System.Rev": rev, "System.WorkItemType": "Task",
         "System.State": "In Progress", "System.Title": "t"}
    if a:
        f["System.AssignedTo"] = a
    return {"id": wid, "rev": rev, "fields": f}


class FakeHttp:
    """Answers the few calls the guard makes and records every one of them."""

    def __init__(self, items, comments=None):
        self.items, self.calls, self.comments = dict(items), [], comments or []
        self.fail_get = False
        self.after_first_get = None  # a callable run after the first GET of a work item (concurrent change)
        self._gets = 0
        self.patch_status = 200

    def request(self, method, url, body=None, content_type=None, use_write=False, read=False):
        self.calls.append((method, url, body, content_type))
        if "connectionData" in url:
            return getattr(self, "probe_status", 200), {"authenticatedUser": {"id": getattr(self, "probe_id", ME)}}
        if "workitems?ids=" in url:
            return getattr(self, "probe_wi_status", 200), {}
        if method == "GET" and "/comments" in url:
            return 200, {"comments": self.comments}
        if method == "GET" and "/workitems/" in url:
            if self.fail_get:
                return 500, {"error": "boom"}
            wid = int(url.split("/workitems/")[1].split("?")[0])
            self._gets += 1
            out = json.loads(json.dumps(self.items.get(wid))) if wid in self.items else None
            if self._gets == 1 and self.after_first_get:
                self.after_first_get()
            return (200, out) if out else (404, {})
        if method == "POST" and "/comments" in url:
            return 200, {"id": 77}
        if method == "POST" and "/attachments" in url:
            return 201, {"id": "att", "url": "https://dev.azure.com/example-org/_apis/wit/attachments/att"}
        if method == "PATCH":
            return self.patch_status, {}
        return 400, {}

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PATCH")]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ev = os.path.join(self.tmp.name, "evidence")
        os.makedirs(self.ev)
        self.png = os.path.join(self.ev, "a.png")
        with open(self.png, "wb") as f:
            f.write(b"\x89PNG fake")
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self, items, **kw):
        self.http = FakeHttp(items, kw.pop("comments", None))
        return g.Guard("example-org", "EXAMPLE", ME, self.ev, self.http, self.logs.append, **kw)

    def call(self, guard, name, args):
        return g.call_tool(guard, name, args, self.logs.append)


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


class Assignment(Base):
    def test_assigned_to_me_prepares_a_write(self):
        gd = self.guard({1: wi(1)})
        r = self.call(gd, "ado_guard_check", {"work_item_id": 1})
        self.assertNotIn("isError", r)
        self.assertEqual(json.loads(r["content"][0]["text"])["assigned_to_verified"], True)

    def test_other_person_blocked(self):
        gd = self.guard({1: wi(1, assigned=OTHER)})
        r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "x", "text_sha256": sha("x")})
        self.assertTrue(r["isError"])
        self.assertEqual(self.http.writes(), [])

    def test_same_display_name_other_id_blocked(self):
        gd = self.guard({1: wi(1, assigned=OTHER, name="Ana Example")})
        self.assertTrue(self.call(gd, "ado_guard_check", {"work_item_id": 1})["isError"])

    def test_unassigned_blocked(self):
        gd = self.guard({1: wi(1, assigned=None)})
        self.assertIn("no assignee", self.call(gd, "ado_guard_check", {"work_item_id": 1})["content"][0]["text"])

    def test_ambiguous_identity_without_id_blocked(self):
        gd = self.guard({1: wi(1, assigned="noid")})
        self.assertIn("cannot be confirmed", self.call(gd, "ado_guard_check", {"work_item_id": 1})["content"][0]["text"])

    def test_other_project_blocked(self):
        gd = self.guard({1: wi(1, project="Other")})
        self.assertTrue(self.call(gd, "ado_guard_check", {"work_item_id": 1})["isError"])

    def test_read_error_is_safe(self):
        gd = self.guard({1: wi(1)})
        self.http.fail_get = True
        r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "x", "text_sha256": sha("x")})
        self.assertTrue(r["isError"])
        self.assertEqual(self.http.writes(), [])

    def test_assignee_changes_between_check_and_write(self):
        gd = self.guard({1: wi(1)})
        self.http.after_first_get = lambda: self.http.items.__setitem__(1, wi(1, assigned=OTHER, rev=5))
        r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "x", "text_sha256": sha("x")})
        self.assertTrue(r["isError"])
        self.assertEqual(self.http.writes(), [])

    def test_identity_must_be_a_guid_not_a_name(self):
        with self.assertRaises(ValueError):
            g.Guard("example-org", "EXAMPLE", "Ana Example", self.ev, FakeHttp({}))


class Comments(Base):
    def test_comment_is_posted_once_with_exact_text(self):
        gd = self.guard({1: wi(1)})
        r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "olá", "text_sha256": sha("olá")})
        self.assertNotIn("isError", r)
        posts = [c for c in self.http.writes() if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(json.loads(posts[0][2])["text"], "olá")

    def test_hash_mismatch_blocked(self):
        gd = self.guard({1: wi(1)})
        self.assertTrue(self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "a", "text_sha256": sha("b")})["isError"])
        self.assertEqual(self.http.writes(), [])

    def test_duplicate_comment_blocked(self):
        gd = self.guard({1: wi(1)}, comments=[{"text": "mesmo texto"}])
        r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "mesmo texto", "text_sha256": sha("mesmo texto")})
        self.assertIn("identical comment", r["content"][0]["text"])
        self.assertEqual(self.http.writes(), [])

    def test_disabled_without_credential(self):
        gd = self.guard({1: wi(1)}, write_enabled=False)
        r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "x", "text_sha256": sha("x")})
        self.assertIn("disabled", r["content"][0]["text"])
        self.assertEqual(self.http.calls, [])


class Attachments(Base):
    def test_upload_and_link_in_one_operation_with_revision_test(self):
        gd = self.guard({1: wi(1)})
        r = self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})
        self.assertNotIn("isError", r)
        kinds = [c[0] for c in self.http.writes()]
        self.assertEqual(kinds, ["POST", "PATCH"])
        patch = json.loads(self.http.writes()[1][2])
        self.assertEqual(patch[0], {"op": "test", "path": "/rev", "value": 4})

    def test_other_person_nothing_uploaded(self):
        gd = self.guard({1: wi(1, assigned=OTHER)})
        self.assertTrue(self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})["isError"])
        self.assertEqual(self.http.writes(), [])  # no isolated upload

    def test_link_refused_is_reported_and_nothing_else_changes(self):
        gd = self.guard({1: wi(1)})
        self.http.patch_status = 412
        r = self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})
        self.assertIn("NOT linked", r["content"][0]["text"])

    def test_path_outside_folder_and_symlink_blocked(self):
        gd = self.guard({1: wi(1)})
        outside = os.path.join(self.tmp.name, "x.png")
        with open(outside, "wb") as f:
            f.write(b"x")
        self.assertTrue(self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": outside})["isError"])
        link = os.path.join(self.ev, "l.png")
        os.symlink(outside, link)
        self.assertTrue(self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": link})["isError"])
        self.assertEqual(self.http.writes(), [])

    def test_disallowed_type_blocked(self):
        gd = self.guard({1: wi(1)})
        p = os.path.join(self.ev, "s.sh")
        with open(p, "w") as f:
            f.write("echo")
        self.assertTrue(self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": p})["isError"])


class NoBypass(Base):
    def test_unknown_and_dangerous_tools_blocked(self):
        gd = self.guard({1: wi(1)})
        for name in ("wit_create_work_item", "ado_guard_create_bug", "ado_guard_link", "ado_guard_update_fields",
                     "ado_guard_set_assigned_to", "wit_update_work_items_batch"):
            r = self.call(gd, name, {"work_item_id": 1})
            self.assertTrue(r["isError"], name)
        self.assertEqual(self.http.writes(), [])

    def test_batches_and_extra_arguments_blocked(self):
        gd = self.guard({1: wi(1), 2: wi(2)})
        self.assertTrue(self.call(gd, "ado_guard_check", {"work_item_id": [1, 2]})["isError"])
        self.assertTrue(self.call(gd, "ado_guard_check", {"work_item_id": 1, "fields": {"System.AssignedTo": "x"}})["isError"])
        self.assertTrue(self.call(gd, "ado_guard_add_comment", {"work_item_id": True, "text": "x", "text_sha256": sha("x")})["isError"])
        self.assertEqual(self.http.writes(), [])

    def test_tool_list_is_closed(self):
        # plan C (10.11.2026): the bug creation is the only addition (a preview that only reads, and a creation that is off
        # unless the launcher enables it); nothing that deletes, changes a state, a field or an assignee
        self.assertEqual(sorted(t["name"] for t in g.TOOLS),
                         ["ado_guard_add_comment", "ado_guard_attach_file", "ado_guard_check", "ado_guard_create_bug",
                          "ado_guard_preview_bug", "ado_guard_preview_comment", "ado_guard_probe",
                          "ado_guard_render_markdown", "ado_guard_status"])


class Probe(Base):
    def run_probe(self, **kw):
        gd = self.guard({1: wi(1)}, **{k: v for k, v in kw.items() if k == "write_enabled"})
        for k in ("probe_status", "probe_id", "probe_wi_status"):
            if k in kw:
                setattr(self.http, k, kw[k])
        return self.call(gd, "ado_guard_probe", {}), self.http

    def test_accepted_writes_nothing(self):
        r, http = self.run_probe()
        self.assertEqual(json.loads(r["content"][0]["text"])["write_credential"], "accepted")
        self.assertEqual(http.writes(), [])

    def test_rejected_401(self):
        r, _ = self.run_probe(probe_status=401)
        self.assertEqual(json.loads(r["content"][0]["text"])["write_credential"], "rejected")

    def test_other_identity_is_a_problem(self):
        r, _ = self.run_probe(probe_id=OTHER)
        self.assertEqual(json.loads(r["content"][0]["text"])["write_credential"], "problem")

    def test_no_write_credential_blocked(self):
        r, _ = self.run_probe(write_enabled=False)
        self.assertTrue(r["isError"])


class RealStatus(Base):
    """The probe reports the status really received; a classification never replaces it (mocked transport)."""

    def http(self, behaviour):
        old = g.open_url
        g.open_url = behaviour
        self.addCleanup(setattr, g, "open_url", old)
        return g.Http("W", "R")

    @staticmethod
    def response(status, body=b"{}", ctype="application/json"):
        class R:
            def __init__(s):
                s.status = status
                s.headers = {"Content-Type": ctype}

            def read(s):
                return body

            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

        return R()

    def probe(self, behaviour):
        gd = g.Guard("example-org", "EXAMPLE", ME, self.ev, self.http(behaviour), self.logs.append)
        return gd.probe_write_credential()

    def test_login_page_with_http_200_is_reported_as_200_and_not_authenticated(self):
        out = self.probe(lambda req: self.response(200, b"<html>sign in</html>", "text/html"))
        self.assertEqual(out["http_status"], 200)
        self.assertEqual(out["write_credential"], "rejected")
        self.assertIn("not JSON", out["error"])

    def test_203_is_reported_as_203(self):
        out = self.probe(lambda req: self.response(203, b"<html/>", "text/html"))
        self.assertEqual(out["http_status"], 203)
        self.assertEqual(out["write_credential"], "rejected")

    def test_redirect_is_not_followed_and_shows_a_destination_without_query(self):
        def redirect(req):
            raise g.urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "https://login.example/path?code=SECRET"}, None)

        out = self.probe(redirect)
        self.assertEqual(out["http_status"], 302)
        self.assertEqual(out["redirect_to"], "https://login.example/path")
        self.assertNotIn("SECRET", json.dumps(out))

    def test_real_401_and_403_keep_their_status(self):
        for code in (401, 403):
            def refuse(req, code=code):
                raise g.urllib.error.HTTPError(req.full_url, code, "x", {}, None)

            out = self.probe(refuse)
            self.assertEqual((out["http_status"], out["write_credential"]), (code, "rejected"))

    def test_unknown_status_is_unknown_not_rejected(self):
        def teapot(req):
            raise g.urllib.error.HTTPError(req.full_url, 418, "x", {}, None)

        out = self.probe(teapot)
        self.assertEqual((out["http_status"], out["write_credential"]), (418, "unknown"))

    def test_no_response_is_status_zero_and_unknown(self):
        def down(req):
            raise OSError("boom")

        out = self.probe(down)
        self.assertEqual((out["http_status"], out["write_credential"]), (0, "unknown"))

    def test_metadata_has_no_secret_material(self):
        meta = g.Http("W" * 10 + " \n", "W" * 10 + " \n").credential_metadata()
        text = json.dumps(meta)
        self.assertNotIn("WWWW", text)
        self.assertEqual(meta["write"]["length"], 12)
        self.assertTrue(meta["write"]["has_whitespace"] and meta["write"]["has_newline"])
        self.assertTrue(meta["write_equals_read"])
        self.assertFalse(meta["write"]["looks_hex_encoded_text"])
        self.assertTrue(meta["write"]["whitespace_only_at_the_ends"])
        self.assertEqual(g.Http("ab cde", None).credential_metadata()["write"]["pieces_separated_by_whitespace_lengths"], [2, 3])
        self.assertFalse(g.Http("ab cde", None).credential_metadata()["write"]["whitespace_only_at_the_ends"])
        self.assertTrue(g.Http(" abc ", None).credential_metadata()["write"]["whitespace_only_at_the_ends"])
        enc = g.Http(b"hello world token".hex(), None).credential_metadata()
        self.assertTrue(enc["write"]["looks_hex_encoded_text"])


class Credentials(Base):
    def test_reads_use_the_read_credential_and_writes_only_the_write_one(self):
        seen = []
        real = g.open_url

        class R:
            status = 200

            def __init__(self):
                self.headers = {"Content-Type": "application/json"}

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req):
            seen.append((req.get_method(), req.headers.get("Authorization")))
            return R()

        g.open_url = fake
        try:
            h = g.Http("WRITE", "READ")
            h.request("GET", "https://x")
            h.request("POST", "https://x", b"{}", "application/json")
            g.Http(None, "READ").request("POST", "https://x", b"{}", "application/json")
        finally:
            g.open_url = real
        import base64

        def enc(s):
            return "Basic " + base64.b64encode(f":{s}".encode()).decode()

        self.assertEqual(seen[0], ("GET", enc("READ")))
        self.assertEqual(seen[1], ("POST", enc("WRITE")))
        self.assertIsNone(seen[2][1])  # a write never falls back to the read credential


class Protocol(Base):
    def test_rpc_roundtrip_and_no_secret_in_logs(self):
        out, err = io.StringIO(), io.StringIO()
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ado_guard_status", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 4, "method": "nope"}]
        env = {"ADO_GUARD_WRITE_PAT": FAKE_PAT}
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n")
        old = dict(os.environ)
        os.environ.update(env)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                g.main(["--org", "example-org", "--project", "EXAMPLE", "--identity-id", ME, "--allowed-dir", self.ev])
        finally:
            sys.stdin = old_stdin
            os.environ.clear()
            os.environ.update(old)
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        self.assertIn("error", replies[3])
        self.assertNotIn(FAKE_PAT, out.getvalue() + err.getvalue())

    def test_redact_removes_the_credential(self):
        self.assertNotIn(FAKE_PAT, g.redact(f"error with {FAKE_PAT} inside", [FAKE_PAT]))


class StartupProbe(Base):
    """The opt-in probe at startup (plan Q6c): one read with the write credential, reported in the status, never fatal."""

    def test_the_status_says_it_was_not_run_until_it_is_asked_for(self):
        gd = self.guard({})
        self.assertIn("not run", str(gd.status()["startup_probe"]))
        self.assertEqual([c for c in self.http.calls if "connectionData" in c[1]], [])      # no network at startup by default

    def test_an_authenticated_write_credential_is_reported_compactly_without_credential_facts(self):
        gd = self.guard({})
        gd.startup_probe = g.startup_probe(gd)
        st = gd.status()["startup_probe"]
        self.assertEqual((st["ran"], st["http_status"], st["classification"], st["identity_matches"]), (True, 200, "authenticated", True))
        self.assertNotIn("credential_metadata", json.dumps(st))
        self.assertEqual(len([c for c in self.http.calls if "connectionData" in c[1]]), 1)

    def test_another_identity_or_a_refusal_is_a_result_not_an_exception(self):
        gd = self.guard({})
        self.http.probe_id = OTHER
        self.assertEqual(g.startup_probe(gd)["identity_matches"], False)
        self.http.probe_status = 401
        self.assertEqual(g.startup_probe(gd)["http_status"], 401)

    def test_without_a_write_credential_nothing_is_called(self):
        gd = self.guard({}, write_enabled=False)
        self.assertEqual(g.startup_probe(gd), {"ran": False, "reason": "no usable write credential"})
        self.assertEqual(self.http.calls, [])

    def test_a_transport_failure_never_stops_the_server(self):
        gd = self.guard({})

        def boom(*a, **k):
            raise OSError("network down")
        self.http.request = boom
        out = g.startup_probe(gd)
        self.assertEqual((out["ran"], out["reason"]), (False, "OSError"))


class OrganisationConfig(Base):
    """The organisation is configuration, never a constant of the code: no default, and without it nothing starts."""

    STATUS = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "ado_guard_status", "arguments": {}}}) + "\n"

    def _run(self, args, env=None, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        old_stdin, old = sys.stdin, dict(os.environ)
        sys.stdin = io.StringIO(stdin)
        os.environ.pop("ADO_GUARD_ORG", None)
        os.environ.pop("ADO_GUARD_WRITE_PAT", None)
        os.environ.update(env or {})
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    g.main(args)
                except SystemExit as e:
                    code = e.code
        finally:
            sys.stdin = old_stdin
            os.environ.clear()
            os.environ.update(old)
        return code, out.getvalue(), err.getvalue()

    def test_without_an_organisation_the_server_does_not_start(self):
        code, out, err = self._run(["--project", "P", "--identity-id", ME, "--allowed-dir", self.ev], stdin=self.STATUS)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")                       # no reply, so nothing was read or written
        self.assertIn("organisation missing", err)

    def test_the_organisation_can_come_from_the_environment_and_the_flag_wins(self):
        base = ["--project", "P", "--identity-id", ME, "--allowed-dir", self.ev]
        _, out, _ = self._run(base, env={"ADO_GUARD_ORG": "org-from-env"}, stdin=self.STATUS)
        self.assertIn("org-from-env", out)
        _, out, _ = self._run(["--org", "org-from-flag"] + base, env={"ADO_GUARD_ORG": "org-from-env"}, stdin=self.STATUS)
        self.assertIn("org-from-flag", out)
        self.assertNotIn("org-from-env", out)

    def test_an_organisation_must_be_a_name_never_a_url_or_a_path(self):
        for bad in ("", "https://dev.azure.com/x", "a/b", "a?b=1", "a b", "../x", "x" * 80):
            with self.assertRaises(ValueError, msg=bad):
                g.Guard(bad, "P", ME, self.ev, FakeHttp({}))


# ---- hardening: credential hygiene, comment preview, Markdown helper, publication reports, expiry -------------------------
FIXED_NOW = datetime(2026, 11, 10, 23, 30, 0, tzinfo=UTC)
PART_A, PART_B = "FICTIONAL-PART-ONE", "FICTIONAL-PART-TWO"   # made-up pieces: a value with a space between them is "damaged"


def run_main(args, env, stdin, patch_http=False):
    """Run main() with a controlled environment and stdin; returns (stdout lines as JSON, stderr, Http constructor calls)."""
    out, err, made = io.StringIO(), io.StringIO(), []
    old_stdin, old = sys.stdin, dict(os.environ)
    for k in [k for k in os.environ if k.startswith("ADO_GUARD_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.stdin = io.StringIO(stdin)
    real_http = g.Http

    def capture(write_value, read_value=None):
        made.append((write_value, read_value))
        return real_http(write_value, read_value)

    try:
        with redirect_stdout(out), redirect_stderr(err), mock.patch.object(g, "Http", side_effect=capture):
            g.main(args)
    finally:
        sys.stdin = old_stdin
        os.environ.clear()
        os.environ.update(old)
    return [json.loads(x) for x in out.getvalue().splitlines()], err.getvalue(), made


def rpc(name, arguments=None, mid=1):
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments or {}}}) + "\n"


class CredentialHygiene(Base):
    def base_args(self):
        return ["--org", "example-org", "--project", "EXAMPLE", "--identity-id", ME, "--allowed-dir", self.ev]

    def status(self, env):
        replies, err, made = run_main(self.base_args(), env, rpc("ado_guard_status"))
        return json.loads(replies[0]["result"]["content"][0]["text"]), err, made

    def test_screen_credential_states(self):
        self.assertEqual(g.screen_credential(None), (None, "absent"))
        self.assertEqual(g.screen_credential("  \n "), (None, "absent"))
        self.assertEqual(g.screen_credential("  abc\n"), ("abc", "present"))       # the ends are stripped, as before
        self.assertEqual(g.screen_credential("ab cd")[1], "rejected: contains whitespace inside")
        self.assertEqual(g.screen_credential("ab\tcd")[1], "rejected: contains whitespace inside")
        self.assertEqual(g.screen_credential("ab\ncd")[1], "rejected: contains whitespace inside")
        self.assertEqual(g.screen_credential("ab\u00a0cd")[1], "rejected: contains whitespace inside")
        self.assertEqual(g.screen_credential("ab\x01cd")[1], "rejected: contains control characters")
        self.assertEqual(g.screen_credential("abcd\x00")[1], "rejected: contains control characters")

    def test_a_write_credential_with_whitespace_inside_starts_with_writes_disabled(self):
        st, err, made = self.status({"ADO_GUARD_WRITE_PAT": f"{PART_A} {PART_B}"})
        self.assertEqual(st["write_credential"], "rejected: contains whitespace inside")
        self.assertFalse(st["writes_enabled"])
        self.assertEqual(made[0][0], None)                               # the damaged value never reaches the HTTP layer
        blob = json.dumps(st) + err
        self.assertNotIn(PART_A, blob)
        self.assertNotIn(PART_B, blob)
        self.assertNotIn("length", blob)
        self.assertNotIn(str(len(f"{PART_A} {PART_B}")), json.dumps(st["write_credential"]))

    def test_every_kind_of_inner_whitespace_is_refused(self):
        for sep in (" ", "\t", "\n", "\r\n", "  "):
            st, _, _ = self.status({"ADO_GUARD_WRITE_PAT": f"{PART_A}{sep}{PART_B}"})
            self.assertFalse(st["writes_enabled"], repr(sep))
            self.assertTrue(st["write_credential"].startswith("rejected"), repr(sep))

    def test_whitespace_at_the_ends_is_still_stripped_and_accepted(self):
        st, _, made = self.status({"ADO_GUARD_WRITE_PAT": f"  {PART_A}\n"})
        self.assertEqual((st["write_credential"], st["writes_enabled"]), ("present", True))
        self.assertEqual(made[0][0], PART_A)

    def test_a_write_tool_answers_disabled_and_nothing_is_sent(self):
        sent = []
        stdin = rpc("ado_guard_add_comment", {"work_item_id": 1, "text": "x", "text_sha256": sha("x")})
        with mock.patch.object(g, "open_url", side_effect=lambda req: sent.append(req)):
            replies, _, _ = run_main(self.base_args(), {"ADO_GUARD_WRITE_PAT": f"{PART_A} {PART_B}"}, stdin)
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("disabled", replies[0]["result"]["content"][0]["text"])
        self.assertEqual(sent, [])

    def test_a_read_credential_with_whitespace_inside_is_refused_too(self):
        st, err, made = self.status({"ADO_GUARD_WRITE_PAT": PART_A, "ADO_GUARD_READ_PAT": f"{PART_B} {PART_A}"})
        self.assertEqual(st["read_credential"], "rejected: contains whitespace inside")
        self.assertEqual(st["write_credential"], "present")
        self.assertTrue(st["writes_enabled"])                            # the write credential is fine
        self.assertEqual(made[0], (PART_A, None))                        # the damaged read value is not used
        self.assertNotIn(PART_B, json.dumps(st) + err)

    def test_both_absent_and_both_present_are_reported(self):
        st, _, _ = self.status({})
        self.assertEqual((st["write_credential"], st["read_credential"], st["writes_enabled"]), ("absent", "absent", False))
        st, _, _ = self.status({"ADO_GUARD_WRITE_PAT": PART_A, "ADO_GUARD_READ_PAT": PART_B})
        self.assertEqual((st["write_credential"], st["read_credential"]), ("present", "present"))

    def test_the_startup_log_names_the_problem_but_never_the_value(self):
        _, err, _ = self.status({"ADO_GUARD_WRITE_PAT": f"{PART_A} {PART_B}"})
        self.assertIn("write credential rejected: contains whitespace inside", err)
        self.assertNotIn(PART_A, err)


class FlexHttp(FakeHttp):
    """FakeHttp that stores the posted comment, can serve it back (changed or not) and can fail the comments read."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.comments_status, self.stored, self.readback = 200, {}, "same"   # same | changed | fail

    def request(self, method, url, body=None, content_type=None, use_write=False, read=False):
        if method == "GET" and "/comments" in url and self.comments_status != 200:
            self.calls.append((method, url, body, content_type))
            return self.comments_status, {}
        if method == "POST" and "/comments" in url:
            self.stored[77] = json.loads(body)["text"]
        if method == "GET" and re.search(r"/comments/\d+\?", url):
            self.calls.append((method, url, body, content_type))
            if self.readback == "fail":
                return 404, {}
            return 200, {"id": 77, "text": self.stored[77] + ("!" if self.readback == "changed" else "")}
        return super().request(method, url, body, content_type, use_write, read)


class PreviewComment(Base):
    def guard(self, items, **kw):
        status = kw.pop("comments_status", 200)
        self.http = FlexHttp(items, kw.pop("comments", None))
        self.http.comments_status = status
        return g.Guard("example-org", "EXAMPLE", ME, self.ev, self.http, self.logs.append, **kw)

    def preview(self, gd, wid, text):
        r = self.call(gd, "ado_guard_preview_comment", {"work_item_id": wid, "text": text})
        self.assertNotIn("isError", r)
        return json.loads(r["content"][0]["text"])

    def test_a_clean_item_would_be_written_and_the_hash_is_of_the_exact_text(self):
        gd = self.guard({1: wi(1)})
        out = self.preview(gd, 1, "  olá  \n")
        self.assertTrue(out["would_write"])
        self.assertEqual((out["reasons"], out["duplicate"]), ([], False))
        self.assertEqual(out["text_sha256"], sha("  olá  \n"))             # the exact text, not a trimmed one
        self.assertEqual(out["item"]["state"], "In Progress")
        self.assertEqual(self.http.writes(), [])

    def test_it_never_writes_in_any_case(self):
        for kw, items, text in (({}, {1: wi(1)}, "x"), ({}, {1: wi(1, assigned=OTHER)}, "x"), ({"write_enabled": False}, {1: wi(1)}, "x"),
                                ({"comments": [{"text": "x"}]}, {1: wi(1)}, "x")):
            gd = self.guard(items, **kw)
            self.preview(gd, 1, text)
            self.assertEqual(self.http.writes(), [])
            self.assertEqual([c for c in self.http.calls if c[0] != "GET"], [])

    def test_a_duplicate_is_reported(self):
        gd = self.guard({1: wi(1)}, comments=[{"text": " mesmo texto "}])
        out = self.preview(gd, 1, "mesmo texto")
        self.assertTrue(out["duplicate"])
        self.assertFalse(out["would_write"])
        self.assertIn("identical comment already exists", out["reasons"][0])

    def test_no_write_credential_is_a_reason_but_the_checks_still_run(self):
        gd = self.guard({1: wi(1)}, write_enabled=False, comments=[{"text": "dup"}])
        out = self.preview(gd, 1, "dup")
        self.assertFalse(out["would_write"])
        self.assertTrue(out["duplicate"])
        self.assertEqual(len(out["reasons"]), 2)
        self.assertIn("disabled", out["reasons"][0])

    def test_invalid_input_is_a_reason_not_a_crash(self):
        gd = self.guard({1: wi(1)})
        for wid, text in ((1, ""), (1, "   "), (1, "x" * (g.MAX_COMMENT + 1)), (True, "x"), (-3, "x"), ("1", "x"), (1, 5)):
            r = self.call(gd, "ado_guard_preview_comment", {"work_item_id": wid, "text": text})
            self.assertNotIn("isError", r, (wid, text if not isinstance(text, str) else text[:5]))
            out = json.loads(r["content"][0]["text"])
            self.assertFalse(out["would_write"])
            self.assertTrue(out["reasons"])
        self.assertEqual(self.http.writes(), [])

    def test_the_schema_is_closed(self):
        gd = self.guard({1: wi(1)})
        self.assertTrue(self.call(gd, "ado_guard_preview_comment", {"work_item_id": 1, "text": "x", "text_sha256": sha("x")})["isError"])
        missing = self.call(gd, "ado_guard_preview_comment", {"work_item_id": 1})          # a missing text is a reason, not a write
        self.assertFalse(json.loads(missing["content"][0]["text"])["would_write"])
        self.assertTrue(self.call(gd, "ado_guard_preview_comment", {"work_item_id": 1, "text": "x", "fields": {}})["isError"])

    def test_preview_and_add_comment_agree_on_the_same_fixtures(self):
        long = "x" * (g.MAX_COMMENT + 1)
        fixtures = [
            ("clean", {}, {1: wi(1)}, "texto", True),
            ("other assignee", {}, {1: wi(1, assigned=OTHER)}, "texto", False),
            ("same name, other id", {}, {1: wi(1, assigned=OTHER, name="Ana Example")}, "texto", False),
            ("unassigned", {}, {1: wi(1, assigned=None)}, "texto", False),
            ("no stable id", {}, {1: wi(1, assigned="noid")}, "texto", False),
            ("other project", {}, {1: wi(1, project="Other")}, "texto", False),
            ("missing item", {}, {}, "texto", False),
            ("duplicate", {"comments": [{"text": "texto"}]}, {1: wi(1)}, "texto", False),
            ("duplicate with spaces", {"comments": [{"text": "  texto "}]}, {1: wi(1)}, "texto", False),
            ("other comment", {"comments": [{"text": "outro"}]}, {1: wi(1)}, "texto", True),
            ("comments unreadable", {"comments_status": 500}, {1: wi(1)}, "texto", False),
            ("empty", {}, {1: wi(1)}, "  ", False),
            ("too long", {}, {1: wi(1)}, long, False),
            ("no write credential", {"write_enabled": False}, {1: wi(1)}, "texto", False),
        ]
        for label, kw, items, text, expected in fixtures:
            pv = self.preview(self.guard(items, **kw), 1, text)
            gd = self.guard(items, **kw)
            r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": text, "text_sha256": sha(text)})
            wrote = "isError" not in r
            self.assertEqual(pv["would_write"], wrote, label)
            self.assertEqual(wrote, expected, label)
            if not wrote:
                msg = r["content"][0]["text"].removeprefix("BLOCKED: ")
                if label != "no write credential":
                    self.assertIn(msg, pv["reasons"], label)             # the very same reason, from the same code path
                else:
                    self.assertTrue(pv["reasons"], label)


class MarkdownHelper(Base):
    md = staticmethod(g.markdown_to_html)

    def test_paragraphs_become_divs_and_a_line_break_inside_is_br(self):
        self.assertEqual(self.md("one\n\ntwo\nthree"), "<div>one</div>\n<div>two<br>three</div>")
        self.assertEqual(self.md("\n\n  one  \n\n\n\ntwo\n"), "<div>one</div>\n<div>two</div>")

    def test_bold_and_code(self):
        self.assertEqual(self.md("a **b** c `d` e"), "<div>a <b>b</b> c <code>d</code> e</div>")
        self.assertEqual(self.md("**bold with `code` inside**"), "<div><b>bold with <code>code</code> inside</b></div>")
        self.assertEqual(self.md("`**not bold**`"), "<div><code>**not bold**</code></div>")

    def test_unbalanced_or_empty_markers_stay_as_text(self):
        self.assertEqual(self.md("a **b"), "<div>a **b</div>")
        self.assertEqual(self.md("a ** b **"), "<div>a ** b **</div>")
        self.assertEqual(self.md("a `b"), "<div>a `b</div>")
        self.assertEqual(self.md("a * b * c"), "<div>a * b * c</div>")

    def test_html_is_escaped_everywhere(self):
        out = self.md("<script>alert(1)</script> & \"q\" 'a'\n\n- <b>x</b>\n\n**<i>y</i>** `<u>z</u>`")
        self.assertNotIn("<script>", out)
        self.assertNotIn("<i>", out)
        self.assertNotIn("<u>", out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot; &#x27;a&#x27;", out)
        self.assertIn("<li>&lt;b&gt;x&lt;/b&gt;</li>", out)
        self.assertIn("<b>&lt;i&gt;y&lt;/i&gt;</b> <code>&lt;u&gt;z&lt;/u&gt;</code>", out)

    def test_bullet_lists(self):
        self.assertEqual(self.md("- a\n* b\n- **c**"), "<ul><li>a</li><li>b</li><li><b>c</b></li></ul>")

    def test_numbered_lists(self):
        self.assertEqual(self.md("1. a\n2) b\n10. c"), "<ol><li>a</li><li>b</li><li>c</li></ol>")

    def test_blocks_change_without_a_blank_line(self):
        self.assertEqual(self.md("intro\n- a\n- b\n1. x\nend"),
                         "<div>intro</div>\n<ul><li>a</li><li>b</li></ul>\n<ol><li>x</li></ol>\n<div>end</div>")

    def test_a_line_starting_with_bold_is_not_a_bullet(self):
        self.assertEqual(self.md("**Title** text"), "<div><b>Title</b> text</div>")
        self.assertEqual(self.md("-not a bullet"), "<div>-not a bullet</div>")

    def test_crlf_and_empty_input(self):
        self.assertEqual(self.md("a\r\n\r\n- b\r\n"), "<div>a</div>\n<ul><li>b</li></ul>")
        self.assertEqual(self.md(""), "")
        self.assertEqual(self.md(" \n\n "), "")

    def test_it_is_deterministic(self):
        text = "Título\n\n- um **dois** `três`\n- <x>\n\n1. a\n2. b\n\nfim & fim"
        outs = {self.md(text) for _ in range(50)}
        self.assertEqual(len(outs), 1)
        self.assertEqual(self.md(self.md(text)), self.md(self.md(text)))

    def test_only_a_string_is_accepted(self):
        with self.assertRaises(TypeError):
            self.md(None)
        with self.assertRaises(TypeError):
            self.md(b"x")

    def test_the_tool_returns_html_and_its_hash_and_touches_nothing(self):
        gd = self.guard({1: wi(1)})
        text = "a **b**\n\n- <c>"
        r = self.call(gd, "ado_guard_render_markdown", {"markdown": text})
        self.assertNotIn("isError", r)
        out = json.loads(r["content"][0]["text"])
        self.assertEqual(out["html"], g.markdown_to_html(text))
        self.assertEqual(out["html_sha256"], sha(out["html"]))
        self.assertTrue(out["within_comment_limit"])
        self.assertEqual(self.http.calls, [])                               # no network, no work item read

    def test_the_tool_flags_html_over_the_comment_limit(self):
        gd = self.guard({1: wi(1)})
        out = json.loads(self.call(gd, "ado_guard_render_markdown", {"markdown": "\n".join(["- x"] * 4000)})["content"][0]["text"])
        self.assertFalse(out["within_comment_limit"])

    def test_the_tool_rejects_bad_arguments(self):
        gd = self.guard({1: wi(1)})
        self.assertTrue(self.call(gd, "ado_guard_render_markdown", {"markdown": 5})["isError"])
        self.assertTrue(self.call(gd, "ado_guard_render_markdown", {})["isError"])
        self.assertTrue(self.call(gd, "ado_guard_render_markdown", {"markdown": "x", "work_item_id": 1})["isError"])

    def test_add_comment_never_converts_what_is_approved(self):
        gd = self.guard({1: wi(1)})
        text = "**not converted** `raw`"
        self.assertNotIn("isError", self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": text, "text_sha256": sha(text)}))
        post = next(c for c in self.http.writes() if c[0] == "POST")
        self.assertEqual(json.loads(post[2])["text"], text)


class PublicationReports(Base):
    def setUp(self):
        super().setUp()
        self.reports = os.path.join(self.tmp.name, "reports")      # does not exist yet: the guard creates it

    def guard(self, items, **kw):
        self.http = FlexHttp(items, kw.pop("comments", None))
        kw.setdefault("report_dir", self.reports)
        kw.setdefault("clock", lambda: FIXED_NOW)
        return g.Guard("example-org", "EXAMPLE", ME, self.ev, self.http, self.logs.append, **kw)

    def files(self):
        return sorted(os.listdir(self.reports)) if os.path.isdir(self.reports) else []

    def load(self, name):
        with open(os.path.join(self.reports, name), encoding="utf-8") as fh:
            raw = fh.read()
        return raw, json.loads(raw)

    def test_a_comment_writes_one_report_with_the_hashes_and_no_text(self):
        text = "UNIQUE-DRAFT-TEXT-0042 with details"
        gd = self.guard({1: wi(1)})
        r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": text, "text_sha256": sha(text)})
        self.assertNotIn("isError", r)
        self.assertNotIn("report_error", json.loads(r["content"][0]["text"]))
        names = self.files()
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^20261110T233000000000Z_1_comment\.json$")
        raw, rep = self.load(names[0])
        self.assertEqual((rep["kind"], rep["work_item_id"], rep["comment_id"]), ("comment", 1, 77))
        self.assertEqual(rep["text_sha256"], sha(text))
        self.assertIs(rep["readback_equal"], True)
        self.assertEqual((rep["revision_before"], rep["revision_after"]), (4, 4))
        self.assertEqual(rep["utc_time"], "2026-11-10T23:30:00Z")
        self.assertNotIn("UNIQUE-DRAFT-TEXT-0042", raw)                    # never the text
        self.assertFalse({"text", "comment_text", "body"} & set(rep))

    def test_a_readback_that_differs_or_fails_is_recorded(self):
        for mode, expected in (("changed", False), ("fail", None)):
            shutil_dir = os.path.join(self.tmp.name, f"r_{mode}")
            gd = self.guard({1: wi(1)}, report_dir=shutil_dir)
            self.http.readback = mode
            self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "abc", "text_sha256": sha("abc")})
            (name,) = os.listdir(shutil_dir)
            with open(os.path.join(shutil_dir, name), encoding="utf-8") as fh:
                rep = json.load(fh)
            self.assertIs(rep["readback_equal"], expected, mode)
            self.assertEqual(rep["readback_sha256"], sha("abc!") if mode == "changed" else None)

    def test_an_attachment_writes_one_report_with_the_file_hash_not_the_content(self):
        gd = self.guard({1: wi(1)})
        r = self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})
        self.assertNotIn("isError", r)
        (name,) = self.files()
        self.assertRegex(name, r"_1_attachment\.json$")
        raw, rep = self.load(name)
        self.assertEqual((rep["kind"], rep["file_name"]), ("attachment", "a.png"))
        self.assertEqual(rep["file_sha256"], hashlib.sha256(b"\x89PNG fake").hexdigest())
        self.assertEqual((rep["revision_before"], rep["revision_after"]), (4, 4))
        self.assertNotIn("PNG fake", raw)
        self.assertNotIn(self.ev, raw)                                      # a name, not the path

    def test_reports_are_off_by_default_and_cost_no_extra_reads(self):
        gd = self.guard({1: wi(1)}, report_dir=None)
        self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "abc", "text_sha256": sha("abc")})
        self.assertEqual(self.files(), [])
        self.assertFalse([c for c in self.http.calls if re.search(r"/comments/\d+\?", c[1])])
        self.assertEqual(json.loads(self.call(gd, "ado_guard_status", {})["content"][0]["text"])["publication_reports"], "off")

    def test_a_blocked_write_leaves_no_report(self):
        gd = self.guard({1: wi(1, assigned=OTHER)})
        self.assertTrue(self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "abc", "text_sha256": sha("abc")})["isError"])
        self.assertTrue(self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})["isError"])
        gd = self.guard({1: wi(1)}, comments=[{"text": "abc"}])
        self.assertTrue(self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "abc", "text_sha256": sha("abc")})["isError"])
        self.http.patch_status = 412
        self.assertTrue(self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})["isError"])
        self.assertEqual(self.files(), [])

    def test_a_report_that_cannot_be_written_does_not_undo_or_hide_the_success(self):
        blocker = os.path.join(self.tmp.name, "a_file")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        gd = self.guard({1: wi(1)}, report_dir=os.path.join(blocker, "sub"))       # cannot be created under a file
        r = self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "abc", "text_sha256": sha("abc")})
        self.assertNotIn("isError", r)
        out = json.loads(r["content"][0]["text"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["comment_id"], 77)
        self.assertIn("report was not written", out["report_error"])
        self.assertEqual(len([c for c in self.http.writes() if c[0] == "POST"]), 1)
        r = self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})
        out = json.loads(r["content"][0]["text"])
        self.assertTrue(out["ok"])
        self.assertIn("report_error", out)

    def test_a_second_report_in_the_same_instant_is_not_overwritten(self):
        gd = self.guard({1: wi(1)})
        self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})
        r = self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})
        self.assertIn("report_error", json.loads(r["content"][0]["text"]))      # exclusive create: the first one is kept
        self.assertEqual(len(self.files()), 1)

    def test_the_option_and_the_environment_reach_the_guard(self):
        args = ["--org", "example-org", "--project", "EXAMPLE", "--identity-id", ME, "--allowed-dir", self.ev]
        replies, _, _ = run_main(args + ["--report-dir", self.reports], {}, rpc("ado_guard_status"))
        self.assertEqual(json.loads(replies[0]["result"]["content"][0]["text"])["publication_reports"], self.reports)
        other = os.path.join(self.tmp.name, "from_env")
        replies, _, _ = run_main(args, {"ADO_GUARD_REPORT_DIR": other}, rpc("ado_guard_status"))
        self.assertEqual(json.loads(replies[0]["result"]["content"][0]["text"])["publication_reports"], other)
        replies, _, _ = run_main(args + ["--report-dir", self.reports], {"ADO_GUARD_REPORT_DIR": other}, rpc("ado_guard_status"))
        self.assertEqual(json.loads(replies[0]["result"]["content"][0]["text"])["publication_reports"], self.reports)
        replies, _, _ = run_main(args, {}, rpc("ado_guard_status"))
        self.assertEqual(json.loads(replies[0]["result"]["content"][0]["text"])["publication_reports"], "off")

    def test_no_credential_is_in_a_report(self):
        gd = self.guard({1: wi(1)})
        self.call(gd, "ado_guard_add_comment", {"work_item_id": 1, "text": "abc", "text_sha256": sha("abc")})
        self.call(gd, "ado_guard_attach_file", {"work_item_id": 1, "file_path": self.png})
        for name in self.files():
            raw, _ = self.load(name)
            self.assertNotIn(FAKE_PAT, raw)
            self.assertNotRegex(raw.lower(), r"authorization|password|basic ")


class CredentialExpiry(Base):
    def status(self, **expiries):
        gd = self.guard({1: wi(1)}, expiries=expiries, clock=lambda: FIXED_NOW)
        return json.loads(self.call(gd, "ado_guard_status", {})["content"][0]["text"])

    def test_days_left_and_the_warning_threshold(self):
        for date, left, warn in (("2026-11-20", 10, False), ("2026-11-14", 4, False), ("2026-11-13", 3, True),
                                 ("2026-11-10", 0, True), ("2026-11-09", -1, True), ("2026-01-01", -313, True)):
            e = self.status(write=date)["credential_expiry"]["write"]
            self.assertEqual((e["days_left"], e["warning"]), (left, warn), date)
            self.assertEqual(e["expiry"], date)

    def test_an_expired_date_adds_a_note_but_disables_nothing(self):
        st = self.status(write="2026-11-08", read="2026-12-31")
        self.assertIn("EXPIRED 2 day(s) ago", st["credential_expiry"]["write"]["note"])
        self.assertTrue(st["writes_enabled"])
        self.assertEqual(st["write_credential"], "present")
        self.assertNotIn("note", st["credential_expiry"]["read"])
        self.assertFalse(st["credential_expiry"]["read"]["warning"])

    def test_soon_adds_a_renewal_note(self):
        e = self.status(read="2026-11-12")["credential_expiry"]["read"]
        self.assertEqual((e["days_left"], e["warning"]), (2, True))
        self.assertIn("renew", e["note"])

    def test_invalid_dates(self):
        for bad in ("2026-13-45", "tomorrow", "20261110", "2026-11-1", "10/11/2026", "2026-11-10T00:00:00", "2026-02-30", "0", "x" * 40):
            self.assertEqual(self.status(write=bad)["credential_expiry"]["write"], {"expiry": "invalid date"}, bad)

    def test_not_configured(self):
        st = self.status()
        self.assertEqual(st["credential_expiry"], {"write": {"expiry": "not configured"}, "read": {"expiry": "not configured"}})
        self.assertEqual(self.status(write="  ")["credential_expiry"]["write"], {"expiry": "not configured"})

    def test_the_date_is_judged_in_utc_whatever_the_clock_zone(self):
        from datetime import timedelta
        late_in_lisbon_plus = datetime(2026, 11, 11, 1, 0, tzinfo=timezone(timedelta(hours=2)))   # 2026-11-10 23:00 UTC
        gd = self.guard({1: wi(1)}, expiries={"write": "2026-11-10"}, clock=lambda: late_in_lisbon_plus)
        e = json.loads(self.call(gd, "ado_guard_status", {})["content"][0]["text"])["credential_expiry"]["write"]
        self.assertEqual(e["days_left"], 0)

    def test_the_environment_variables_reach_the_status(self):
        args = ["--org", "example-org", "--project", "EXAMPLE", "--identity-id", ME, "--allowed-dir", self.ev]
        env = {"ADO_GUARD_WRITE_PAT_EXPIRES": "2000-01-01", "ADO_GUARD_READ_PAT_EXPIRES": "2999-01-01"}
        replies, _, _ = run_main(args, env, rpc("ado_guard_status"))
        e = json.loads(replies[0]["result"]["content"][0]["text"])["credential_expiry"]
        self.assertTrue(e["write"]["warning"] and e["write"]["days_left"] < 0)
        self.assertFalse(e["read"]["warning"])
        replies, _, _ = run_main(args, {"ADO_GUARD_READ_PAT_EXPIRES": "soon"}, rpc("ado_guard_status"))
        self.assertEqual(json.loads(replies[0]["result"]["content"][0]["text"])["credential_expiry"]["read"], {"expiry": "invalid date"})

    def test_the_status_never_shows_a_credential(self):
        st = self.status(write="2026-11-12")
        self.assertEqual(set(st["credential_expiry"]), {"write", "read"})
        self.assertEqual(set(st["credential_expiry"]["write"]), {"expiry", "days_left", "warning", "note"})


if __name__ == "__main__":
    unittest.main()

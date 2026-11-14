#!/usr/bin/env python3
"""Guarded Azure DevOps writes as a small MCP server (stdio), for QA workspaces.

Why: the official @azure-devops/mcp server can write, and Azure DevOps cannot say "only work items assigned to me". This
server is the ONE write path given to the agent. Every write goes through the same checks, and anything not listed here
does not exist (unknown tools, batches, links, field updates, bug creation, Assigned To changes are refused by default).

Tools (all fail closed):
  ado_guard_status      configuration and whether a write credential is present (never shows it)
  ado_guard_check       read the work item and say if a write would be allowed, and why not
  ado_guard_add_comment one comment on one work item
  ado_guard_preview_comment read-only: every check of add_comment (same code path), duplicate, text hash, would_write and why
  ado_guard_render_markdown read-only: deterministic Markdown -> HTML of a draft, with the SHA-256 of the HTML (nothing is sent)
  ado_guard_attach_file one file from the allowed folder, uploaded AND linked to the same work item in one operation
  ado_guard_preview_bug read-only: every check of a bug creation and the exact JSON Patch + payload hash to approve
  ado_guard_create_bug  ONE bug, DISABLED unless ADO_GUARD_ALLOW_BUG_CREATE=1 (see "Bug creation" below)

Rule before every write (a stable identity id, never the display name):
  organisation and project fixed -> read the work item -> System.TeamProject is the project -> System.AssignedTo.id equals the
  verified identity id -> the content hash matches what was approved -> read it again right before writing; any difference
  (assignee, revision) or any error blocks. Attachments link with a JSON Patch `test` on /rev, so a concurrent change refuses
  the write. A comment POST has no revision precondition: the re-read narrows that window, it does not remove it.

Bug creation (plan C, 10.11.2026; disabled by default; first used against the real project on 13.11.2026): a bug is created
only under a parent that is a PBI or a QA task of the project ASSIGNED to the verified identity (checked by stable id), is born
UNASSIGNED (13.11.2026: System.AssignedTo is not sent; the Bug type of the project has no default and does not require it, and
the read-back refuses to call a bug with any assignee "verified") with the parent's area and iteration, in ONE request that
carries the fields and the Parent link, with only the listed fields, a payload hash the user approved and a duplicate search
first.
The steps that follow a creation (attachments and the creation comment) need a write on the new, unassigned bug. Only that
case is opened, and narrowly: the bug must be in the guard's ledger of bugs it created (the --created-bugs-file, or this
process's memory), still be a Bug, still have NO assignee, have been created by the verified identity and keep, as its Parent,
the parent recorded at creation, which must itself pass the normal check (assigned to the verified identity). Every other
unassigned item stays refused, and once anyone assigns the bug the normal rule applies. Whether Azure DevOps commits the fields and the link together is NOT PROVEN (the
documentation shows the request, not a guarantee): the result is always read back and a bug created without the parent, or
different from what was approved, is reported with its id (ATOMICITY_PROVEN = False); nothing is repeated, corrected or
deleted. After a timeout or any uncertain answer the guard looks for the bug first and never sends the request again.

Limits that remain (also in docs/technique-ado-write-guard.md): this is a local control, not an Azure DevOps permission. The
credential it holds can write elsewhere if used outside this server; the agent must not be able to read it.

Run (the project's launcher sets the environment; nothing secret is passed on the command line):
  python scripts/ado_write_guard.py --org <organisation> --project <project> --identity-id <guid> --allowed-dir <evidence folder>
  (the organisation may also come from ADO_GUARD_ORG; with neither, the server does not start)
Environment: ADO_GUARD_WRITE_PAT (optional: without it the write tools answer "disabled"); ADO_GUARD_READ_PAT (optional,
read-only credential used for the checks, so the read path can be validated before any write credential exists). A credential
with whitespace or control characters INSIDE is refused at startup (writes disabled, status says so; never the value).
ADO_GUARD_WRITE_PAT_EXPIRES / ADO_GUARD_READ_PAT_EXPIRES (optional ISO dates, not secret): the status reports the days left.
--report-dir / ADO_GUARD_REPORT_DIR (optional): one small JSON report per successful comment or attachment (no text, no content).
--created-bugs-file / ADO_GUARD_CREATED_BUGS_FILE (optional): the ledger of bugs this guard created (ids, parent, time, payload
hash; no content). Without it the ledger lives only in the process, so the steps after a creation must happen before a restart.
Video evidence (13.11.2026): .mp4/.mov/.webm up to 60 MB, only with a review record next to it (<file>.review.json with
"reviewed": true and "personal_data": "none" or "masked"): a video is checked for personal data and credentials before it goes.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVER_NAME = "ado-write-guard"
SERVER_VERSION = "1.2.0"   # 1.2.0 (13.11.2026): final preview of the fields, project mapping, patch hash; 1.1.0: unassigned bugs, ledger, video
MAX_COMMENT = 20000
MAX_FILE = 10 * 1024 * 1024
MAX_VIDEO = 60 * 1024 * 1024        # Azure DevOps accepts up to 130 MB in one upload; a demonstration clip should be short
VIDEO_EXT = {".mp4", ".mov", ".webm"}
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".pdf", ".txt", ".md"} | VIDEO_EXT
VIDEO_REVIEW_SUFFIX = ".review.json"
VIDEO_PERSONAL_DATA_OK = ("none", "masked")
API_COMMENTS = "7.1-preview.4"
API = "7.1"
BUG_TYPE = "Bug"
PARENT_TYPES = ("Product Backlog Item", "Task")
BUG_FIELDS = {"title": "System.Title", "repro_steps": "Microsoft.VSTS.TCM.ReproSteps",
              "system_info": "Microsoft.VSTS.TCM.SystemInfo", "acceptance_criteria": "Microsoft.VSTS.Common.AcceptanceCriteria"}
MAX_TITLE, MAX_BODY = 255, 30000
BUG_LABELS = {"title": "Title", "repro_steps": "Repro Steps", "system_info": "System Info",
              "acceptance_criteria": "Acceptance Criteria"}
EXTRA_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
EXTRA_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
EXTRA_REF_DENIED = {"Microsoft.VSTS.Common.Priority", "Microsoft.VSTS.Common.Severity", "Microsoft.VSTS.Common.ValueArea",
                    "Microsoft.VSTS.Build.FoundIn", "Microsoft.VSTS.Build.IntegrationBuild", "Microsoft.VSTS.Common.Activity",
                    "Microsoft.VSTS.Scheduling.Effort", "Microsoft.VSTS.Common.StateCode", *BUG_FIELDS.values()}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
CREDENTIAL = re.compile(r"\b(password|passwd|pwd|senha|palavra-passe|token|pat)\s*[:=]\s*\S", re.IGNORECASE)
BUG_CONFIG_KEYS = {"labels", "fields", "checks", "notices", "require_patch_hash"}
ATOMICITY_PROVEN = False   # fields and Parent link in one request: the API accepts it; a guarantee that it is all-or-nothing is not documented
PARENT_REL = "System.LinkTypes.Hierarchy-Reverse"
EXPIRY_WARNING_DAYS = 3
DUPLICATE_MSG = "an identical comment already exists on this work item: not published twice"
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WS_BEFORE_CLOSE = re.compile(r"\s+(</)")
ORG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")      # a name only: never a URL, a path or a query
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class Blocked(Exception):
    """A write that must not happen; the message is the reason shown to the user."""


class ConfigError(ValueError):
    """The project's write_guard.bugs section cannot be used: the guard does not start."""


def _rules(section, what: str, keys) -> dict:
    out = {}
    for key, rules in (section or {}).items():
        if key not in keys:
            raise ConfigError(f"write_guard.bugs.{what}: {key!r} is not a field of the bug ({', '.join(sorted(keys))})")
        if not isinstance(rules, list):
            raise ConfigError(f"write_guard.bugs.{what}.{key} must be a list of {{name, pattern}}")
        out[key] = []
        for r in rules:
            if not isinstance(r, dict) or not str(r.get("name") or "").strip() or not isinstance(r.get("pattern"), str):
                raise ConfigError(f"write_guard.bugs.{what}.{key}: each rule needs a name and a pattern")
            try:
                rx = re.compile(r["pattern"], re.IGNORECASE | re.MULTILINE)
            except re.error as e:
                raise ConfigError(f"write_guard.bugs.{what}.{key}: pattern of {r['name']!r} is not a valid regex ({e})") from None
            out[key].append((str(r["name"]).strip(), rx))
    return out


def load_bug_config(section) -> dict:
    """The project's `write_guard.bugs` (project.yaml), validated: labels, extra fields, checks, notices. None or {} gives
    the defaults (the four standard fields, as before)."""
    section = section or {}
    if not isinstance(section, dict):
        raise ConfigError("write_guard.bugs must be a mapping")
    unknown = set(section) - BUG_CONFIG_KEYS
    if unknown:
        raise ConfigError(f"write_guard.bugs: unknown key(s) {sorted(unknown)} (known: {sorted(BUG_CONFIG_KEYS)})")
    fields = {}
    for key, spec in (section.get("fields") or {}).items():
        spec = {"reference": spec} if isinstance(spec, str) else spec
        if not EXTRA_KEY.match(str(key)) or key in BUG_FIELDS:
            raise ConfigError(f"write_guard.bugs.fields: {key!r} is not a valid extra key (lower case; not one of "
                              f"{', '.join(BUG_FIELDS)})")
        ref = str((spec or {}).get("reference") or "")
        if not EXTRA_REF.match(ref) or ref.startswith("System.") or ref in EXTRA_REF_DENIED:
            raise ConfigError(f"write_guard.bugs.fields.{key}: {ref!r} cannot be written by the guard (a confirmed "
                              "reference name of a content field; never System.*, management fields or the four standard ones)")
        if ref in {f["reference"] for f in fields.values()}:
            raise ConfigError(f"write_guard.bugs.fields.{key}: {ref} is mapped twice")
        fields[key] = {"reference": ref, "label": str(spec.get("label") or ref), "required": bool(spec.get("required"))}
    labels = dict(BUG_LABELS)
    for key, label in (section.get("labels") or {}).items():
        if key not in BUG_FIELDS or not str(label or "").strip():
            raise ConfigError(f"write_guard.bugs.labels: {key!r} is not a standard field (extra fields carry their own label)")
        labels[key] = str(label).strip()
    keys = set(BUG_FIELDS) | set(fields)
    return {"labels": labels, "fields": fields, "checks": _rules(section.get("checks"), "checks", keys),
            "notices": _rules(section.get("notices"), "notices", keys),
            "require_patch_hash": bool(section.get("require_patch_hash"))}


def load_project_bug_config(path: str) -> dict:
    """`write_guard.bugs` of a workspace's project.yaml (PyYAML, as in the rest of the framework)."""
    try:
        import yaml
    except ImportError:
        raise ConfigError("--project-yaml needs PyYAML (run the guard with the framework's interpreter)") from None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"{path} cannot be read ({type(e).__name__})") from None
    return load_bug_config(((doc.get("write_guard") or {}) if isinstance(doc, dict) else {}).get("bugs"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def html_equivalent(stored, approved) -> bool:
    """Azure DevOps stores rich-text fields with a space added before closing tags (`</div>`, `</li>`, `</ol>`, `</ul>`; seen on
    13.11.2026 on two real bugs). Equal when the only difference is whitespace right before a closing tag; nothing else."""
    if not isinstance(stored, str) or not isinstance(approved, str):
        return stored == approved
    return _WS_BEFORE_CLOSE.sub(r"\1", stored).strip() == _WS_BEFORE_CLOSE.sub(r"\1", approved).strip()


def utc_now() -> datetime.datetime:
    """The clock (replaced in the tests): always timezone-aware UTC."""
    return datetime.datetime.now(datetime.UTC)


def screen_credential(raw):
    """(value, state) for a credential read from the environment. The ends are stripped (a paste often adds whitespace); any
    whitespace or control character INSIDE means the value was damaged or is two values: it is refused, never repaired.
    The state never contains the value or its length."""
    v = (raw or "").strip()
    if not v:
        return None, "absent"
    if any(c.isspace() for c in v):
        return v, "rejected: contains whitespace inside"
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        return v, "rejected: contains control characters"
    return v, "present"


def expiry_facts(raw, today: datetime.date) -> dict:
    """Days left of a credential whose ISO expiry date is configured (a date is not secret). Reports only; never disables."""
    raw = (raw or "").strip()
    if not raw:
        return {"expiry": "not configured"}
    try:
        if not ISO_DATE.match(raw):
            raise ValueError(raw)
        d = datetime.date.fromisoformat(raw)
    except ValueError:
        return {"expiry": "invalid date"}
    left = (d - today).days
    out = {"expiry": d.isoformat(), "days_left": left, "warning": left <= EXPIRY_WARNING_DAYS}
    if left < 0:
        out["note"] = (f"EXPIRED {-left} day(s) ago: Azure DevOps will most likely refuse this credential; "
                       "nothing is disabled by this date, renew it")
    elif left <= EXPIRY_WARNING_DAYS:
        out["note"] = f"expires in {left} day(s): renew it"
    return out


# ---- deterministic Markdown -> HTML (a helper for drafts; never applied automatically) ----------------------------------
_INLINE = re.compile(r"`([^`\n]+)`|\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BULLET = re.compile(r"^[-*] +(.*)$")
_NUMBERED = re.compile(r"^\d{1,9}[.)] +(.*)$")


def _inline(escaped: str) -> str:
    def sub(m):
        return f"<code>{m.group(1)}</code>" if m.group(1) is not None else f"<b>{_inline(m.group(2))}</b>"
    return _INLINE.sub(sub, escaped)


def markdown_to_html(text: str) -> str:
    """A small, pure, deterministic subset: paragraphs (blank-line separated; a line break inside one is <br>) -> <div>,
    **bold** -> <b>, `code` -> <code>, "- " or "* " items -> <ul><li>, "1. " or "1) " items -> <ol><li>. Everything else is
    text, and ALL text is HTML-escaped first, so no markup can come through. Not supported (left as text): headings, links,
    images, tables, nested lists, emphasis with single * or _. List numbers are not kept (the browser numbers the items)."""
    if not isinstance(text, str):
        raise TypeError("markdown must be a string")
    blocks, cur_kind, cur = [], None, []

    def flush():
        nonlocal cur_kind, cur
        if cur:
            if cur_kind == "p":
                blocks.append("<div>" + "<br>".join(cur) + "</div>")
            else:
                tag = "ul" if cur_kind == "ul" else "ol"
                blocks.append(f"<{tag}>" + "".join(f"<li>{i}</li>" for i in cur) + f"</{tag}>")
        cur_kind, cur = None, []

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.rstrip()
        if not line.strip():
            flush()
            continue
        m_b, m_n = _BULLET.match(line.lstrip()), _NUMBERED.match(line.lstrip())
        kind, body = ("ul", m_b.group(1)) if m_b else ("ol", m_n.group(1)) if m_n else ("p", line.strip())
        if kind != cur_kind:
            flush()
            cur_kind = kind
        cur.append(_inline(html.escape(body, quote=True)))
    flush()
    return "\n".join(blocks)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the status the server really sent is the status we report."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def open_url(req):
    """The single network call (replaced in the tests)."""
    return _OPENER.open(req, timeout=30)


def _safe_destination(location: str | None) -> str | None:
    if not location:
        return None
    u = urllib.parse.urlparse(location)
    return f"{u.scheme}://{u.netloc}{u.path}"  # never the query: it may carry state or tokens


class Http:
    """The only place that talks to Azure DevOps (replaced by a fake in the tests).

    Returns (status, data): `status` is the HTTP status really received (0 only when there was no HTTP response at all),
    `data` the parsed JSON, or {"error": ...} describing what came back instead. Nothing is translated into another status."""

    def __init__(self, write_pat: str | None, read_pat: str | None = None):
        self.write_pat, self.read_pat = write_pat, read_pat

    def credential_metadata(self) -> dict:
        """Facts about the credentials that are NOT secret: presence, length, odd characters, and whether the two are the
        same value. Never the value, a prefix, a suffix or a hash."""
        def facts(v):
            if not v:
                return {"present": False}
            hexlike = len(v) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in v)
            decodes_to_text = False
            if hexlike:  # the macOS `security -w` prints some stored values hex-encoded
                try:
                    decodes_to_text = all(32 <= b < 127 or b in (9, 10, 13) for b in bytes.fromhex(v))
                except ValueError:
                    decodes_to_text = False
            return {"present": True, "length": len(v), "has_whitespace": any(c.isspace() for c in v),
                    "has_newline": ("\n" in v or "\r" in v), "non_ascii": any(ord(c) > 127 for c in v),
                    "whitespace_only_at_the_ends": any(c.isspace() for c in v) and not any(c.isspace() for c in v.strip()),
                    "pieces_separated_by_whitespace_lengths": [len(x) for x in v.split()],
                    "looks_hex_encoded_text": hexlike and decodes_to_text}
        return {"write": facts(self.write_pat), "read": facts(self.read_pat),
                "write_equals_read": bool(self.write_pat and self.write_pat == self.read_pat)}

    def request(self, method: str, url: str, body: bytes | None = None, content_type: str | None = None,
                use_write: bool = False, read: bool = False):
        headers = {"Accept": "application/json"}
        # reads may use the separate read-only credential; writes only ever use the write credential; only the probe
        # (a read that proves the WRITE credential works) asks for it explicitly; `read` marks a POST that only reads
        # (a WIQL query), which then uses the read credential
        pat = self.write_pat if ((method != "GET" and not read) or use_write) else (self.read_pat or self.write_pat)
        if pat:
            headers["Authorization"] = "Basic " + base64.b64encode(f":{pat}".encode()).decode()
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with open_url(req) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "") if getattr(r, "headers", None) else ""
                try:
                    return r.status, (json.loads(raw) if raw else {})
                except ValueError:
                    return r.status, {"error": "response was not JSON", "content_type": ctype.split(";")[0]}
        except urllib.error.HTTPError as e:
            data = {"error": f"HTTP {e.code}"}
            dest = _safe_destination(e.headers.get("Location") if e.headers else None)
            if dest:
                data["redirect_to"] = dest
            return e.code, data
        except Exception as e:  # noqa: BLE001 - network, timeout: no HTTP response at all
            return 0, {"error": f"no HTTP response ({type(e).__name__})"}


class Guard:
    def __init__(self, org: str, project: str, identity_id: str, allowed_dir: str, http, log=None, write_enabled=True,
                 allow_bug_create: bool = False, credentials: dict | None = None, expiries: dict | None = None,
                 report_dir: str | None = None, clock=None, created_bugs_file: str | None = None,
                 bug_config: dict | None = None):
        if not UUID.match(identity_id or ""):
            raise ValueError("identity id must be the stable Azure DevOps identity id (a GUID), not a name")
        if not ORG.match(org or ""):
            raise ValueError("organisation missing or not a valid Azure DevOps organisation name")
        self.org, self.project, self.identity_id = org, project, identity_id.lower()
        self.allowed_dir = os.path.realpath(allowed_dir) if allowed_dir else None
        self.http, self.log, self.write_enabled = http, log or (lambda *_: None), write_enabled
        self.allow_bug_create = bool(allow_bug_create)
        self.startup_probe = None          # the compact result of the opt-in probe at startup (--probe-on-start)
        # state of each credential as screened at startup ("present", "absent" or "rejected: ..."), never a value
        self.credentials = dict(credentials) if credentials else {
            "write": "present" if write_enabled else "absent",
            "read": "present" if getattr(http, "read_pat", None) else "absent"}
        self.expiries = dict(expiries or {})                 # ISO dates as text (not secret), per "write" / "read"
        self.report_dir = report_dir or None
        self.clock = clock or utc_now
        self.base = f"https://dev.azure.com/{urllib.parse.quote(org)}"
        self.created_bugs_file = created_bugs_file or None
        self._created_bugs: dict = {}       # bug id -> parent id, for the bugs this process created
        self.bug_config = bug_config if bug_config is not None else load_bug_config(None)

    # ---- the ledger of bugs this guard created ----------------------------------------------------------------------
    def _ledger(self) -> dict:
        """bug id -> parent id: this process's memory plus the ledger file (when configured and readable)."""
        out = dict(self._created_bugs)
        if self.created_bugs_file and os.path.isfile(self.created_bugs_file):
            try:
                with open(self.created_bugs_file, encoding="utf-8") as fh:
                    rows = json.load(fh)
                for r in rows if isinstance(rows, list) else []:
                    b, p = r.get("bug_id") if isinstance(r, dict) else None, r.get("parent_id") if isinstance(r, dict) else None
                    if isinstance(b, int) and isinstance(p, int) and not isinstance(b, bool):
                        out.setdefault(b, p)
            except (OSError, ValueError):
                pass                       # an unreadable ledger opens nothing: only the memory counts
        return out

    def _record_created(self, bug_id: int, parent_id: int, payload_sha256: str):
        """Remember a bug this guard created; None when recorded, else why the FILE could not be written (the memory is)."""
        self._created_bugs[bug_id] = parent_id
        if not self.created_bugs_file:
            return None
        try:
            rows = []
            if os.path.isfile(self.created_bugs_file):
                with open(self.created_bugs_file, encoding="utf-8") as fh:
                    rows = json.load(fh)
                if not isinstance(rows, list):
                    raise ValueError("the ledger is not a list")
            rows.append({"bug_id": bug_id, "parent_id": parent_id, "payload_sha256": payload_sha256,
                         "created_utc": self.clock().astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
            d = os.path.dirname(os.path.abspath(self.created_bugs_file))
            os.makedirs(d, exist_ok=True)
            tmp = self.created_bugs_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, self.created_bugs_file)
            return None
        except Exception as e:  # noqa: BLE001 - the bug exists; only the ledger file failed
            return f"{type(e).__name__}: the ledger file was not updated (this process still remembers the bug)"

    # ---- reads --------------------------------------------------------------------------------------------------
    def _wi(self, wid: int) -> dict:
        url = (f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workitems/{wid}"
               f"?fields=System.Id,System.Title,System.TeamProject,System.AssignedTo,System.WorkItemType,System.State,System.Rev,"
               f"System.CreatedBy,System.Parent"
               f"&api-version={API}")
        status, data = self.http.request("GET", url)
        if status != 200 or not isinstance(data, dict) or "fields" not in data:
            raise Blocked(f"work item {wid} could not be read (status {status}): write blocked")
        return data

    def check(self, wid: int, allow_guard_bug: bool = True) -> dict:
        """Raise Blocked unless the work item is in the project and assigned to the verified identity (or, when allowed, is an
        unassigned bug this guard created: see _check_unassigned_guard_bug)."""
        if not isinstance(wid, int) or isinstance(wid, bool) or wid <= 0:
            raise Blocked("work_item_id must be a positive integer (no batches, no lists)")
        d = self._wi(wid)
        f = d["fields"]
        if f.get("System.Id") != wid or d.get("id") != wid:
            raise Blocked("the work item returned is not the one requested")
        if f.get("System.TeamProject") != self.project:
            raise Blocked(f"work item is in project {f.get('System.TeamProject')!r}, not {self.project!r}")
        a = f.get("System.AssignedTo")
        if not a:
            if not allow_guard_bug:
                raise Blocked("work item has no assignee: write blocked")
            return self._check_unassigned_guard_bug(wid, d)
        if not isinstance(a, dict) or not a.get("id"):
            raise Blocked("assignee identity cannot be confirmed (no stable id returned): write blocked")
        if str(a["id"]).lower() != self.identity_id:
            raise Blocked("work item is assigned to another identity: write blocked")
        rev = d.get("rev", f.get("System.Rev"))
        if not isinstance(rev, int):
            raise Blocked("revision could not be read: write blocked")
        return {"id": wid, "rev": rev, "title": f.get("System.Title"), "state": f.get("System.State"),
                "type": f.get("System.WorkItemType"), "project": self.project, "organisation": self.org,
                "assigned_to_verified": True}

    def _check_unassigned_guard_bug(self, wid: int, d: dict) -> dict:
        """The ONE exception to "assigned to the verified identity": a bug this guard created, still unassigned, so that its
        attachments and creation comment can follow. Anything else without an assignee stays refused."""
        f = d["fields"]
        parent_id = self._ledger().get(wid)
        if parent_id is None:
            raise Blocked("work item has no assignee: write blocked")
        if f.get("System.WorkItemType") != BUG_TYPE:
            raise Blocked("work item has no assignee and is not a Bug: write blocked")
        creator = f.get("System.CreatedBy")
        if not isinstance(creator, dict) or str(creator.get("id", "")).lower() != self.identity_id:
            raise Blocked("unassigned bug not created by the verified identity: write blocked")
        if f.get("System.Parent") != parent_id:
            raise Blocked("unassigned bug no longer has the parent it was created under: write blocked")
        try:
            self.check(parent_id, allow_guard_bug=False)  # the parent itself: assigned by stable id (never another exception)
        except Blocked as e:
            raise Blocked(f"unassigned bug: its parent {parent_id} no longer passes the check ({e}): write blocked") from None
        rev = d.get("rev", f.get("System.Rev"))
        if not isinstance(rev, int):
            raise Blocked("revision could not be read: write blocked")
        return {"id": wid, "rev": rev, "title": f.get("System.Title"), "state": f.get("System.State"),
                "type": f.get("System.WorkItemType"), "project": self.project, "organisation": self.org,
                "assigned_to_verified": False,
                "allowed_as": f"unassigned bug created by this guard under {parent_id} (attachments and comments only)"}

    def status(self) -> dict:
        today = self.clock().astimezone(datetime.UTC).date()
        return {"server_version": SERVER_VERSION, "organisation": self.org, "project": self.project, "verified_identity_id": self.identity_id,
                "allowed_attachment_folder": self.allowed_dir, "writes_enabled": self.write_enabled,
                "write_credential": self.credentials.get("write"), "read_credential": self.credentials.get("read"),
                "credential_expiry": {k: expiry_facts(self.expiries.get(k), today) for k in ("write", "read")},
                "publication_reports": self.report_dir or "off",
                "startup_probe": self.startup_probe or "not run (opt-in: --probe-on-start / ADO_GUARD_PROBE_ON_START=1)",
                "tools": sorted(KNOWN), "bug_creation": "enabled" if (self.allow_bug_create and self.write_enabled) else "disabled",
                "bug_creation_atomicity_proven": ATOMICITY_PROVEN, "new_bug_assignee": "unassigned",
                "created_bugs_ledger": self.created_bugs_file or "process memory only",
                "bug_fields": {"standard": {k: self.bug_config["labels"][k] for k in BUG_FIELDS},
                               "extra": {k: f"{v['label']} ({v['reference']})" for k, v in self.bug_config["fields"].items()},
                               "require_patch_hash": self.bug_config["require_patch_hash"]},
                "video_attachments": {"extensions": sorted(VIDEO_EXT), "max_mb": MAX_VIDEO // (1024 * 1024),
                                      "review_record": f"<file>{VIDEO_REVIEW_SUFFIX}"},
                "blocked_by_design": ["links other than the new bug's Parent", "field updates", "Assigned To changes", "state changes",
                                      "deleting", "batches", "unknown tools"]}

    def probe_write_credential(self) -> dict:
        """A READ with the write credential: does it authenticate, as the verified identity, and may it read work items?
        Writes nothing; never used as a fallback for the read path. `http_status` is what was really received;
        `classification` is this guard's own conclusion and is kept apart from it."""
        if not self.write_enabled:
            raise Blocked("no write credential is configured for this server")
        url = f"{self.base}/_apis/connectionData?api-version=7.1-preview"
        status, data = self.http.request("GET", url, use_write=True)
        out = {"http_status": status}
        meta = getattr(self.http, "credential_metadata", None)
        if meta:
            out["credential_metadata"] = meta()
        if isinstance(data, dict):
            for k in ("error", "redirect_to", "content_type"):
                if data.get(k):
                    out[k] = data[k]
        uid = str(((data or {}).get("authenticatedUser") or {}).get("id", "")).lower() if isinstance(data, dict) else ""
        if status == 200 and uid:
            out["classification"] = "authenticated"
            out["identity_matches"] = uid == self.identity_id
            wi_url = (f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workitems?ids=1&fields=System.Id"
                      f"&errorPolicy=omit&api-version={API}")
            s2, _ = self.http.request("GET", wi_url, use_write=True)
            out["work_items_read_http_status"] = s2
            out["write_credential"] = "accepted" if (uid == self.identity_id and s2 == 200) else "problem"
            out["note"] = "read-only probe; the write scope itself can only be proven by a write"
        elif status in (401, 403):
            out["classification"] = "refused by the server"
            out["write_credential"] = "rejected"
        elif status == 203 or 300 <= status < 400 or (status == 200 and not uid):
            out["classification"] = "not authenticated: the server answered with something other than the API (sign-in page or redirect)"
            out["write_credential"] = "rejected"
        elif status == 0:
            out["classification"] = "no HTTP response (network or local error): the credential was not judged"
            out["write_credential"] = "unknown"
        else:
            out["classification"] = f"unexpected HTTP status {status}"
            out["write_credential"] = "unknown"
        return out

    def _require_write(self):
        if not self.write_enabled:
            raise Blocked("writes are disabled: no write credential is configured for this server")

    def _recheck(self, wid: int, first: dict) -> dict:
        again = self.check(wid)
        if again["rev"] != first["rev"]:
            raise Blocked("the work item changed between the check and the write (revision differs): write blocked")
        return again

    # ---- writes -------------------------------------------------------------------------------------------------
    def _comment_gate(self, wid, text) -> dict:
        """Every check of a comment except the write credential and the approved hash: the text, the work item (project and
        assignee by stable id) and the existing comments. The ONE code path of ado_guard_add_comment and of
        ado_guard_preview_comment, so the preview cannot say something the write would not. Raises Blocked at the first
        problem; returns the item as read, the comments URL and whether an identical comment exists."""
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_COMMENT:
            raise Blocked(f"comment text must be 1..{MAX_COMMENT} characters")
        first = self.check(wid)
        url_c = f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workItems/{wid}/comments?api-version={API_COMMENTS}"
        status, data = self.http.request("GET", url_c + "&$top=200")
        if status != 200 or not isinstance(data, dict):
            raise Blocked("existing comments could not be read to rule out a duplicate: write blocked")
        want = sha256_text(text.strip())
        duplicate = any(sha256_text(str(c.get("text", "")).strip()) == want for c in data.get("comments", []))
        return {"first": first, "url": url_c, "duplicate": duplicate}

    def preview_comment(self, wid, text) -> dict:
        """Read only: what ado_guard_add_comment would do with this exact text, and why not. Never writes."""
        reasons, gate = [], None
        if not self.write_enabled:
            reasons.append("writes are disabled: no usable write credential is configured for this server")
        try:
            gate = self._comment_gate(wid, text)
            if gate["duplicate"]:
                reasons.append(DUPLICATE_MSG)
        except Blocked as e:
            reasons.append(str(e))
        item = (gate or {}).get("first") or {}
        return {"ok": True, "read_only": True, "work_item_id": wid if isinstance(wid, int) and not isinstance(wid, bool) else None,
                "text_sha256": sha256_text(text) if isinstance(text, str) else None,
                "length": len(text) if isinstance(text, str) else None,
                "duplicate": None if gate is None else gate["duplicate"],
                "item": {k: item.get(k) for k in ("type", "state", "rev")} if item else None,
                "would_write": not reasons, "reasons": reasons}

    # ---- publication reports (optional) ----------------------------------------------------------------------------------
    def _revision_now(self, wid):
        try:
            return self._wi(wid).get("rev")
        except Blocked:
            return None

    def _write_report(self, kind: str, wid, record: dict):
        """One small JSON file per successful write; None when it was written (or reports are off), else the reason it was not.
        The write has already happened: a failure here is reported next to the success, never instead of it."""
        if not self.report_dir:
            return None
        try:
            now = self.clock().astimezone(datetime.UTC)
            body = {"kind": kind, "work_item_id": wid, **record, "utc_time": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            os.makedirs(self.report_dir, exist_ok=True)
            name = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}_{wid}_{kind}.json"
            with open(os.path.join(self.report_dir, name), "x", encoding="utf-8") as fh:
                json.dump(body, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
            return None
        except Exception as e:  # noqa: BLE001 - the write already happened
            return f"{type(e).__name__}: the report was not written"

    def _read_back_comment(self, wid, comment_id):
        """The text Azure DevOps stored for a comment, or None when it could not be read."""
        if not isinstance(comment_id, int):
            return None
        url = (f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workItems/{wid}/comments/{comment_id}"
               f"?api-version={API_COMMENTS}")
        status, data = self.http.request("GET", url)
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("text"), str):
            return None
        return data["text"]

    # ---- writes -------------------------------------------------------------------------------------------------
    def add_comment(self, wid, text, text_sha256) -> dict:
        self._require_write()
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_COMMENT:
            raise Blocked(f"comment text must be 1..{MAX_COMMENT} characters")
        if sha256_text(text) != str(text_sha256 or "").lower():
            raise Blocked("text_sha256 does not match the text: the approved content and the content sent differ")
        gate = self._comment_gate(wid, text)
        if gate["duplicate"]:
            raise Blocked(DUPLICATE_MSG)
        first, url_c = gate["first"], gate["url"]
        self._recheck(wid, first)
        status, data = self.http.request("POST", url_c, json.dumps({"text": text}).encode(), "application/json")
        if status not in (200, 201):
            raise Blocked(f"comment was not created (status {status})")
        out = {"ok": True, "work_item_id": wid, "comment_id": data.get("id"), "text_sha256": text_sha256,
               "url": f"{self.base}/{urllib.parse.quote(self.project)}/_workitems/edit/{wid}"}
        if self.report_dir:
            back = self._read_back_comment(wid, data.get("id"))
            err = self._write_report("comment", wid, {
                "comment_id": data.get("id"), "text_sha256": sha256_text(text), "readback_equal": None if back is None else back == text,
                "readback_sha256": None if back is None else sha256_text(back),
                "revision_before": first["rev"], "revision_after": self._revision_now(wid)})
            if err:
                out["report_error"] = err
        return out

    def _file(self, file_path: str) -> tuple[str, bytes]:
        if not self.allowed_dir:
            raise Blocked("no allowed folder is configured for attachments")
        real = os.path.realpath(file_path)
        if os.path.commonpath([real, self.allowed_dir]) != self.allowed_dir:
            raise Blocked("file is outside the allowed evidence folder")
        if not os.path.isfile(real):
            raise Blocked("file does not exist")
        ext = os.path.splitext(real)[1].lower()
        if ext not in ALLOWED_EXT:
            raise Blocked(f"file type {ext!r} is not allowed")
        size = os.path.getsize(real)
        if size == 0 or size > (MAX_VIDEO if ext in VIDEO_EXT else MAX_FILE):
            raise Blocked("file is empty or larger than the limit")
        if ext in VIDEO_EXT:
            self._check_video_review(real)
        with open(real, "rb") as fh:
            return os.path.basename(real), fh.read()

    @staticmethod
    def _check_video_review(real: str) -> None:
        """A video goes only after someone watched it for personal data and credentials: <file>.review.json next to it."""
        review = real + VIDEO_REVIEW_SUFFIX
        if not os.path.isfile(review):
            raise Blocked(f"a video needs its review record ({os.path.basename(review)}) next to it: watch it for personal "
                          "data and credentials first")
        try:
            with open(review, encoding="utf-8") as fh:
                r = json.load(fh)
        except (OSError, ValueError):
            raise Blocked("the video's review record could not be read as JSON") from None
        if not isinstance(r, dict) or r.get("reviewed") is not True or r.get("personal_data") not in VIDEO_PERSONAL_DATA_OK:
            raise Blocked('the video\'s review record must say "reviewed": true and "personal_data": "none" or "masked"')

    def attach_file(self, wid, file_path, comment="") -> dict:
        self._require_write()
        name, content = self._file(str(file_path))
        first = self.check(wid)
        self._recheck(wid, first)
        up = (f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/attachments"
              f"?fileName={urllib.parse.quote(name)}&api-version={API}")
        status, data = self.http.request("POST", up, content, "application/octet-stream")
        if status not in (200, 201) or not data.get("url"):
            raise Blocked(f"upload failed (status {status}); nothing was linked")
        patch = [{"op": "test", "path": "/rev", "value": first["rev"]},
                 {"op": "add", "path": "/relations/-",
                  "value": {"rel": "AttachedFile", "url": data["url"], "attributes": {"comment": str(comment)[:200]}}}]
        link = f"{self.base}/_apis/wit/workitems/{wid}?api-version={API}"
        status, _ = self.http.request("PATCH", link, json.dumps(patch).encode(), "application/json-patch+json")
        if status not in (200, 201):
            raise Blocked(f"the file was uploaded but NOT linked (status {status}, a concurrent change or a refusal): "
                          "it stays unattached and invisible; nothing else was changed")
        out = {"ok": True, "work_item_id": wid, "file": name, "attachment_url": data["url"],
               "file_sha256": hashlib.sha256(content).hexdigest()}
        if self.report_dir:
            err = self._write_report("attachment", wid, {
                "file_name": name, "file_sha256": out["file_sha256"], "revision_before": first["rev"],
                "revision_after": self._revision_now(wid)})
            if err:
                out["report_error"] = err
        return out


    # ---- bug creation (plan C) -----------------------------------------------------------------------------------
    @staticmethod
    def _norm_title(t: str) -> str:
        return " ".join(str(t).split()).casefold()

    @staticmethod
    def _clean_args(args: dict, config: dict | None = None) -> dict:
        """The fields of a bug, validated. Anything else is refused by the tool's own schema before this. The extra
        fields are those of the project's mapping (`write_guard.bugs.fields`), carried with their reference names."""
        config = config if config is not None else load_bug_config(None)
        parent = args.get("parent_id")
        if not isinstance(parent, int) or isinstance(parent, bool) or parent <= 0:
            raise Blocked("parent_id must be a positive integer (one parent, no lists)")
        out = {"parent_id": parent}
        for key, limit, required in (("title", MAX_TITLE, True), ("repro_steps", MAX_BODY, True),
                                     ("system_info", MAX_BODY, True), ("acceptance_criteria", MAX_BODY, False)):
            v = args.get(key)
            if (v is None or v == "") and not required:
                out[key] = ""
                continue
            if not isinstance(v, str) or not v.strip() or len(v) > limit:
                raise Blocked(f"{key} must be a text of 1..{limit} characters")
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", v) or (key == "title" and ("\n" in v or "\r" in v)):
                raise Blocked(f"{key} has characters that are not allowed (control characters; a title is one line)")
            out[key] = v
        extra = args.get("extra_fields")
        if extra is None:
            extra = {}
        if not isinstance(extra, dict):
            raise Blocked("extra_fields must be an object of field key -> text")
        mapped = config["fields"]
        unknown = sorted(set(extra) - set(mapped))
        if unknown:
            raise Blocked(f"extra_fields {unknown} are not mapped in this project's write_guard.bugs.fields "
                          f"({', '.join(sorted(mapped)) or 'none'}): nothing is sent to an unconfirmed field")
        out["extra_fields"] = {}
        for key, spec in mapped.items():
            v = extra.get(key)
            if v is None or (isinstance(v, str) and not v.strip()):
                if spec["required"]:
                    raise Blocked(f"{key} ({spec['label']}) is required by this project's mapping")
                continue
            if not isinstance(v, str) or len(v) > MAX_BODY:
                raise Blocked(f"{key} must be a text of 1..{MAX_BODY} characters")
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", v):
                raise Blocked(f"{key} has characters that are not allowed (control characters)")
            out["extra_fields"][key] = {"reference": spec["reference"], "value": v}
        texts = [(k, out[k]) for k in BUG_FIELDS] + [(k, e["value"]) for k, e in out["extra_fields"].items()]
        for key, text in texts:
            if EMAIL.search(text or ""):
                raise Blocked(f"{key} has an e-mail address: personal addresses never go in a bug (name the QA account "
                              "by its alias or role, or say it is to confirm)")
            if CREDENTIAL.search(text or ""):
                raise Blocked(f"{key} looks like it carries a credential (password/token/pat followed by : or =): "
                              "credentials never go in a bug")
        return out

    @staticmethod
    def payload_hash(clean: dict) -> str:
        """SHA-256 of the canonical JSON of what is sent: the parent, the four content fields and, only when there are
        any, the extra fields with their reference names (so a project without them keeps the hash it had)."""
        body = {k: clean[k] for k in ("parent_id", "title", "repro_steps", "system_info", "acceptance_criteria")}
        if clean.get("extra_fields"):
            body["extra_fields"] = clean["extra_fields"]
        canon = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256_text(canon)

    @staticmethod
    def patch_hash(patch: list) -> str:
        """SHA-256 of the exact JSON Patch the creation sends (fields, area and iteration of the parent, Parent link)."""
        return sha256_text(json.dumps(patch, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    def _wi_full(self, wid: int) -> dict:
        url = f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workitems/{wid}?$expand=relations&api-version={API}"
        status, data = self.http.request("GET", url)
        if status != 200 or not isinstance(data, dict) or "fields" not in data:
            raise Blocked(f"work item {wid} could not be read (status {status}): bug creation blocked")
        return data

    def _check_parent(self, parent_id: int) -> dict:
        """The parent, fully read, only if it is a PBI or a QA task of the project assigned to the verified identity."""
        info = self.check(parent_id, allow_guard_bug=False)  # project, stable assignee id, revision
        full = self._wi_full(parent_id)
        f = full["fields"]
        # With $expand=relations the real API returns the id at the top level and NOT inside `fields` (verified against the
        # real service on 10.11.2026): the top-level id must match; System.Id in `fields`, when present, must match too.
        if full.get("id") != parent_id or f.get("System.Id", parent_id) != parent_id:
            raise Blocked("the work item returned is not the parent requested")
        kind = f.get("System.WorkItemType")
        if kind not in PARENT_TYPES:
            raise Blocked(f"the parent is a {kind!r}: only {' or '.join(PARENT_TYPES)} can be a bug's parent")
        if kind == "Task" and not str(f.get("System.Title", "")).lstrip().casefold().startswith("qa"):
            raise Blocked("the parent task is not a QA task (its title does not start with «QA»)")
        if f.get("System.State") == "Removed":
            raise Blocked("the parent is Removed")
        for key in ("System.AreaPath", "System.IterationPath"):
            if not f.get(key):
                raise Blocked(f"the parent has no {key}: creation blocked")
        return {"info": info, "fields": f, "rev": full.get("rev", f.get("System.Rev")), "relations": full.get("relations") or []}

    def _check_bug_type(self) -> None:
        """The process's Bug type has the fields this tool writes, and requires nothing else without a default."""
        url = f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workitemtypes/{BUG_TYPE}?api-version={API}"
        status, data = self.http.request("GET", url)
        rows = (data.get("fields") or data.get("fieldInstances")) if isinstance(data, dict) else None
        if status != 200 or not isinstance(rows, list):
            raise Blocked(f"the {BUG_TYPE} type could not be read (status {status}): bug creation blocked")
        have = {r.get("referenceName"): r for r in rows if isinstance(r, dict)}
        writes = {*BUG_FIELDS.values(), "System.AreaPath", "System.IterationPath"}
        writes |= {f["reference"] for f in self.bug_config["fields"].values()}
        missing = sorted(f for f in writes if f not in have and f != "Microsoft.VSTS.Common.AcceptanceCriteria")
        if missing:
            raise Blocked(f"the {BUG_TYPE} type of this process has no field {', '.join(missing)}: bug creation blocked")
        required = sorted(r["referenceName"] for r in have.values()
                          if r.get("alwaysRequired") and r.get("defaultValue") in (None, "") and r["referenceName"] not in writes
                          and not str(r["referenceName"]).startswith("System.Id"))
        required = [r for r in required if r not in ("System.TeamProject", "System.WorkItemType", "System.State", "System.Reason",
                                                     "System.AreaId", "System.IterationId",     # set through the paths below
                                                     "System.Rev", "System.ChangedDate", "System.CreatedDate", "System.CreatedBy",
                                                     "System.ChangedBy", "System.AuthorizedDate", "System.RevisedDate")]
        assignee = have.get("System.AssignedTo") or {}
        if assignee.get("alwaysRequired") or assignee.get("defaultValue") not in (None, ""):
            raise Blocked(f"the {BUG_TYPE} type requires or defaults Assigned To: a bug would not be born unassigned: "
                          "bug creation blocked")
        if required:
            raise Blocked(f"the {BUG_TYPE} type requires fields this tool does not set and that have no default: "
                          f"{', '.join(required)}: bug creation blocked")

    def _find_duplicates(self, title: str, parent_children: list) -> list[str]:
        """Bugs with the same normalised title: under the same parent and anywhere in the project (read only)."""
        want = self._norm_title(title)
        found: dict = {}
        if parent_children:
            ids = ",".join(str(i) for i in parent_children[:200])
            url = (f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workitems?ids={ids}"
                   f"&fields=System.Title,System.WorkItemType&api-version={API}")
            status, data = self.http.request("GET", url)
            if status != 200 or not isinstance(data, dict):
                raise Blocked("the parent's children could not be read to rule out a duplicate: bug creation blocked")
            for it in data.get("value", []):
                f = it.get("fields", {}) if isinstance(it, dict) else {}
                if f.get("System.WorkItemType") == BUG_TYPE and self._norm_title(f.get("System.Title", "")) == want:
                    found[it.get("id")] = "under the same parent"
        for wid in self._wiql_same_title(title):
            found.setdefault(wid, "in the project")
        return [f"{wid} ({where})" for wid, where in sorted(found.items(), key=lambda kv: str(kv[0]))]

    def _wiql_same_title(self, title: str) -> list:
        escaped = title.replace("'", "''")
        query = (f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.WorkItemType] = '{BUG_TYPE}' "
                 f"AND [System.Title] = '{escaped}'")
        url = f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/wiql?api-version={API}"
        status, data = self.http.request("POST", url, json.dumps({"query": query}).encode(), "application/json", read=True)
        if status != 200 or not isinstance(data, dict):
            raise Blocked(f"the duplicate search could not run (status {status}): bug creation blocked")
        return [w.get("id") for w in data.get("workItems", []) if isinstance(w, dict)]

    def _bug_patch(self, clean: dict, parent: dict) -> list:
        f = parent["fields"]
        ops = [{"op": "add", "path": f"/fields/{BUG_FIELDS['title']}", "value": clean["title"]},
               {"op": "add", "path": f"/fields/{BUG_FIELDS['repro_steps']}", "value": clean["repro_steps"]},
               {"op": "add", "path": f"/fields/{BUG_FIELDS['system_info']}", "value": clean["system_info"]},
               {"op": "add", "path": "/fields/System.AreaPath", "value": f["System.AreaPath"]},
               {"op": "add", "path": "/fields/System.IterationPath", "value": f["System.IterationPath"]}]
        if clean["acceptance_criteria"]:
            ops.append({"op": "add", "path": f"/fields/{BUG_FIELDS['acceptance_criteria']}", "value": clean["acceptance_criteria"]})
        for e in (clean.get("extra_fields") or {}).values():
            ops.append({"op": "add", "path": f"/fields/{e['reference']}", "value": e["value"]})
        ops.append({"op": "add", "path": "/relations/-", "value": {
            "rel": PARENT_REL, "url": f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workItems/{clean['parent_id']}",
            "attributes": {"comment": "Bug created by the QA guard"}}})
        return ops

    def _content_checks(self, clean: dict) -> tuple[list, list, list]:
        """The project's checks and notices on the exact values sent: (checks, missing, notices)."""
        values = {k: clean.get(k) or "" for k in BUG_FIELDS}
        values.update({k: e["value"] for k, e in (clean.get("extra_fields") or {}).items()})
        checks, missing, notices = [], [], []
        optional = {"acceptance_criteria"} | {k for k, f in self.bug_config["fields"].items() if not f["required"]}
        for key, rules in self.bug_config["checks"].items():
            if key in optional and not values.get(key, "").strip():
                checks += [{"field": self._label(key), "check": name, "result": "not applicable (field left empty)"}
                           for name, _rx in rules]
                continue
            for name, rx in rules:
                ok = bool(rx.search(values.get(key, "")))
                checks.append({"field": self._label(key), "check": name, "result": "ok" if ok else "missing"})
                if not ok:
                    missing.append(f"{self._label(key)}: {name}")
        for key, rules in self.bug_config["notices"].items():
            notices += [f"{self._label(key)}: {name}" for name, rx in rules if rx.search(values.get(key, ""))]
        return checks, missing, notices

    def _label(self, key: str) -> str:
        return self.bug_config["labels"].get(key) or (self.bug_config["fields"].get(key) or {}).get("label") or key

    def _fields_preview(self, patch: list) -> list:
        """One row per field of the patch the creation sends, in its order: label shown in the Board, reference, value."""
        by_ref = {v: self.bug_config["labels"][k] for k, v in BUG_FIELDS.items()}
        by_ref.update({f["reference"]: f["label"] for f in self.bug_config["fields"].values()})
        by_ref.update({"System.AreaPath": "Area", "System.IterationPath": "Iteration"})
        rows = []
        for op in patch:
            if op["path"].startswith("/fields/"):
                ref = op["path"][len("/fields/"):]
                rows.append({"label": by_ref.get(ref, ref), "reference": ref, "value": op["value"]})
        return rows

    def _associated(self, args: dict) -> dict:
        """The attachments and the creation comment, listed for the review only: never sent by the creation."""
        files, problems = [], []
        paths = args.get("attachments") or []
        if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
            raise Blocked("attachments must be a list of file paths")
        for path in paths:
            row = {"file": os.path.basename(path)}
            try:
                name, content = self._file(path)
                row.update({"file": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                            "accepted": True})
                if os.path.splitext(name)[1].lower() in VIDEO_EXT:
                    with open(os.path.realpath(path) + VIDEO_REVIEW_SUFFIX, encoding="utf-8") as fh:
                        r = json.load(fh)
                    row["video_review"] = {"reviewed": r.get("reviewed"), "personal_data": r.get("personal_data")}
            except Blocked as e:
                row.update({"accepted": False, "reason": str(e)})
                problems.append(f"{row['file']}: {e}")
            files.append(row)
        comment = args.get("creation_comment")
        out = {"attachments": files, "attachment_problems": problems,
               "note": "not part of the bug's hashes and not sent by the creation: after it, each attachment goes through "
                       "ado_guard_attach_file and the comment through ado_guard_add_comment, each with its own approval"}
        if comment is not None:
            if not isinstance(comment, str) or not comment.strip() or len(comment) > MAX_COMMENT:
                raise Blocked(f"creation_comment must be a text of 1..{MAX_COMMENT} characters")
            if EMAIL.search(comment) or CREDENTIAL.search(comment):
                raise Blocked("creation_comment has an e-mail address or a credential: never in a comment")
            out["creation_comment"] = {"text": comment, "text_sha256": sha256_text(comment), "length": len(comment)}
        return out

    @staticmethod
    def _preview_text(rows: list, parent: dict, clean: dict, assoc: dict, checks: list, notices: list,
                      payload: str, patch_sha: str) -> str:
        lines = ["PREVIEW OF THE BUG (what the guard sends; nothing is created by this preview)", ""]
        for r in rows:
            lines += [f"== {r['label']} [{r['reference']}]", r["value"], ""]
        f = parent["fields"]
        lines += [(f"== Parent: {clean['parent_id']} ({f.get('System.WorkItemType')}: {f.get('System.Title')}, rev "
                   f"{parent['rev']}) · relation {PARENT_REL}"), "== Assigned To: empty (the bug is born unassigned)", ""]
        if checks:
            lines.append("== Checks: " + "; ".join(f"{c['field']} · {c['check']}: {c['result']}" for c in checks))
        if notices:
            lines.append("== Notices: " + "; ".join(notices))
        lines.append("== Associated operations (separate approvals):")
        for a in assoc["attachments"]:
            extra = ""
            if a.get("video_review"):
                extra = f" · video reviewed={a['video_review']['reviewed']} personal_data={a['video_review']['personal_data']}"
            lines.append(f"   attachment {a['file']}: " + (f"{a['bytes']} bytes, sha256 {a['sha256'][:16]}…{extra}"
                                                          if a.get("accepted") else f"REFUSED ({a['reason']})"))
        if assoc.get("creation_comment"):
            lines.append(f"   creation comment: {assoc['creation_comment']['length']} characters, text_sha256 "
                         f"{assoc['creation_comment']['text_sha256']}")
        if not assoc["attachments"] and not assoc.get("creation_comment"):
            lines.append("   none listed")
        lines += ["", f"payload_sha256 {payload}", f"patch_sha256   {patch_sha}"]
        return "\n".join(lines)

    def preview_bug(self, args: dict) -> dict:
        """Read only: every check of a bug creation, each field as it will be sent (label, reference, value), the exact
        JSON Patch and the hashes to approve, and the associated operations listed apart."""
        clean = self._clean_args(args, self.bug_config)
        assoc = self._associated(args)
        parent = self._check_parent(clean["parent_id"])
        self._check_bug_type()
        children = [int(r["url"].rsplit("/", 1)[1]) for r in parent["relations"]
                    if r.get("rel") == "System.LinkTypes.Hierarchy-Forward" and str(r.get("url", "")).rsplit("/", 1)[-1].isdigit()]
        duplicates = self._find_duplicates(clean["title"], children)
        if duplicates:
            raise Blocked("possible duplicate bug(s): " + ", ".join(duplicates) + ": nothing is created; the user decides")
        patch = self._bug_patch(clean, parent)
        checks, missing, notices = self._content_checks(clean)
        rows = self._fields_preview(patch)
        payload, patch_sha = self.payload_hash(clean), self.patch_hash(patch)
        return {"ok": True, "creation_enabled": self.allow_bug_create and self.write_enabled, "ready": not missing,
                "missing": missing, "checks": checks, "notices": notices,
                "payload_sha256": payload, "patch_sha256": patch_sha,
                "hash_covers": {"payload_sha256": "parent id, title, repro steps, system info, acceptance criteria and the "
                                                  "extra fields with their reference names",
                                "patch_sha256": "the exact JSON Patch: every field, the parent's area and iteration, the "
                                                "Parent relation",
                                "not_covered": "attachments and the creation comment (separate operations, own approvals)"},
                "require_patch_hash": self.bug_config["require_patch_hash"],
                "parent": {"id": clean["parent_id"], "type": parent["fields"].get("System.WorkItemType"),
                           "title": parent["fields"].get("System.Title"), "rev": parent["rev"],
                           "assigned_to_verified": True, "relation": PARENT_REL},
                "assigned_to": "nobody (the bug is born unassigned)",
                "area_path": parent["fields"]["System.AreaPath"], "iteration_path": parent["fields"]["System.IterationPath"],
                "fields_preview": rows, "associated_operations": assoc,
                "preview_text": self._preview_text(rows, parent, clean, assoc, checks, notices, payload, patch_sha),
                "atomicity_proven": ATOMICITY_PROVEN, "patch": patch}

    def _read_back(self, bug_id, clean: dict, parent: dict) -> dict:
        problems = []
        status, data = self.http.request(
            "GET", f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workitems/{bug_id}?$expand=relations&api-version={API}")
        if status != 200 or not isinstance(data, dict) or "fields" not in data:
            return {"read_back": False, "problems": [f"the bug could not be read back (status {status})"], "rev": None,
                    "state": None, "assignee_now": None}
        f = data["fields"]
        who = f.get("System.AssignedTo")
        if not who:
            assignee_now = "unassigned"
        elif isinstance(who, dict) and str(who.get("id", "")).lower() == self.identity_id:
            assignee_now = "the verified identity"
        else:
            assignee_now = "another identity" + (f" ({who.get('displayName')})" if isinstance(who, dict) and who.get("displayName") else "")
        if f.get("System.WorkItemType") != BUG_TYPE:
            problems.append(f"the type is {f.get('System.WorkItemType')!r}")
        if f.get("System.TeamProject") != self.project:
            problems.append("the project is not the expected one")
        if f.get("System.AssignedTo"):
            problems.append("the bug was born WITH an assignee (expected unassigned): nothing was changed")
        normalised = []
        for key, ref in BUG_FIELDS.items():
            want = clean.get(key)
            if want and f.get(ref) != want:
                if key != "title" and html_equivalent(f.get(ref), want):
                    normalised.append(ref)
                else:
                    problems.append(f"{ref} is not what was approved")
        for e in (clean.get("extra_fields") or {}).values():
            if f.get(e["reference"]) != e["value"]:
                if html_equivalent(f.get(e["reference"]), e["value"]):
                    normalised.append(e["reference"])
                else:
                    problems.append(f"{e['reference']} is not what was approved")
        for ref in ("System.AreaPath", "System.IterationPath"):
            if f.get(ref) != parent["fields"].get(ref):
                problems.append(f"{ref} is not the parent's")
        parents = [r for r in data.get("relations") or [] if r.get("rel") == PARENT_REL]
        if len(parents) != 1 or str(parents[0].get("url", "")).rsplit("/", 1)[-1] != str(clean["parent_id"]):
            problems.append("the bug is NOT linked to the parent (or has another parent): the link and the fields were not "
                            "committed together")
        return {"read_back": True, "problems": problems, "rev": data.get("rev", f.get("System.Rev")),
                "html_normalised": normalised, "state": f.get("System.State"), "assignee_now": assignee_now}

    def _locate_after_uncertain(self, clean: dict) -> list:
        """After an answer that does not say whether the bug exists: look for it, never send the request again."""
        try:
            return self._wiql_same_title(clean["title"])
        except Blocked:
            return None

    def create_bug(self, args: dict) -> dict:
        self._require_write()
        if not self.allow_bug_create:
            raise Blocked("bug creation is DISABLED (ADO_GUARD_ALLOW_BUG_CREATE is not set in the launcher): nothing was written")
        clean = self._clean_args(args, self.bug_config)
        if self.payload_hash(clean) != str(args.get("payload_sha256") or "").lower():
            raise Blocked("payload_sha256 does not match the bug: the approved content and the content sent differ "
                          "(a field, the mapping of a field or the parent changed): preview again and ask for approval")
        _checks, missing, _notices = self._content_checks(clean)
        if missing:
            raise Blocked("the project's checks are missing: " + "; ".join(missing) + ": not created")
        want_patch = str(args.get("patch_sha256") or "").lower()
        if self.bug_config["require_patch_hash"] and not want_patch:
            raise Blocked("this project requires patch_sha256 (from the preview): not created")
        parent = self._check_parent(clean["parent_id"])
        self._check_bug_type()
        children = [int(r["url"].rsplit("/", 1)[1]) for r in parent["relations"]
                    if r.get("rel") == "System.LinkTypes.Hierarchy-Forward" and str(r.get("url", "")).rsplit("/", 1)[-1].isdigit()]
        duplicates = self._find_duplicates(clean["title"], children)
        if duplicates:
            raise Blocked("possible duplicate bug(s): " + ", ".join(duplicates) + ": not created; the user decides")
        again = self._check_parent(clean["parent_id"])          # right before writing: the parent has not changed
        if again["rev"] != parent["rev"]:
            raise Blocked("the parent changed between the check and the creation (revision differs): not created")
        if want_patch and self.patch_hash(self._bug_patch(clean, parent)) != want_patch:
            raise Blocked("patch_sha256 does not match the request that would be sent (the parent's area or iteration, or "
                          "a field, changed since the preview): preview again and ask for approval; not created")
        url = f"{self.base}/{urllib.parse.quote(self.project)}/_apis/wit/workitems/${BUG_TYPE}?api-version={API}"
        status, data = self.http.request("POST", url, json.dumps(self._bug_patch(clean, parent)).encode(),
                                         "application/json-patch+json")
        bug_id = data.get("id") if isinstance(data, dict) else None
        if status in (200, 201) and isinstance(bug_id, int):
            ledger_error = self._record_created(bug_id, clean["parent_id"], self.payload_hash(clean))
            back = self._read_back(bug_id, clean, parent)
            out = {"ok": not back["problems"], "created": True, "bug_id": bug_id, "rev": back["rev"],
                   "url": f"{self.base}/{urllib.parse.quote(self.project)}/_workitems/edit/{bug_id}",
                   "payload_sha256": self.payload_hash(clean), "read_back": back["read_back"], "problems": back["problems"],
                   "state": back.get("state"), "assigned_to": back.get("assignee_now"), "atomicity_proven": ATOMICITY_PROVEN,
                   "note": ("created and verified" if not back["problems"] else
                            "CREATED BUT NOT AS APPROVED: the bug EXISTS (see bug_id, state, assigned_to): do not create it "
                            "again; nothing was corrected, repeated or deleted; the user decides")}
            if back.get("html_normalised"):
                out["html_normalised_by_azure_devops"] = back["html_normalised"]
            if ledger_error:
                out["ledger_error"] = ledger_error
            return out
        if 400 <= status < 500:                                   # a definite refusal: nothing was created
            msg = str(data.get("message", ""))[:200] if isinstance(data, dict) else ""
            raise Blocked(f"the bug was not created (HTTP {status}{': ' + msg if msg else ''}); nothing was repeated")
        # no HTTP response, a 5xx or an answer without an id: the outcome is UNCERTAIN. Look for the bug; never resend.
        found = self._locate_after_uncertain(clean)
        if found:
            out = {"ok": False, "created": "found after an uncertain answer", "candidates": found, "http_status": status,
                   "note": "the request was NOT sent again; check these work items before anything else"}
            if len(found) == 1:
                self._record_created(found[0], clean["parent_id"], self.payload_hash(clean))
                back = self._read_back(found[0], clean, parent)
                out.update({"bug_id": found[0], "read_back": back["read_back"], "problems": back["problems"],
                            "ok": not back["problems"], "rev": back["rev"], "state": back.get("state"),
                            "assigned_to": back.get("assignee_now"),
                            "url": f"{self.base}/{urllib.parse.quote(self.project)}/_workitems/edit/{found[0]}"})
            return out
        return {"ok": False, "created": "unknown" if found is None else "not found", "http_status": status,
                "note": ("the answer did not say whether the bug exists and the search found none (or could not run): the request "
                         "was NOT sent again; look for it in the board before any new attempt")}


# ---- MCP plumbing ------------------------------------------------------------------------------------------------
TOOLS = [
    {"name": "ado_guard_status", "description": "Show the guard's organisation, project, verified identity id, allowed folder and whether writes are enabled. Never shows a credential.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "ado_guard_probe", "description": "Read-only check that the WRITE credential authenticates as the verified identity. Writes nothing and is never used as a read fallback.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "ado_guard_check", "description": "Read one work item and say whether a write would be allowed (project and stable assignee id). Writes nothing.",
     "inputSchema": {"type": "object", "properties": {"work_item_id": {"type": "integer", "minimum": 1}}, "required": ["work_item_id"], "additionalProperties": False}},
    {"name": "ado_guard_add_comment", "description": "Add ONE comment to ONE work item assigned to the verified identity (or to an unassigned bug this guard created). Needs the user's explicit approval of the exact text; text_sha256 is the SHA-256 (hex) of the exact text approved.",
     "inputSchema": {"type": "object", "properties": {"work_item_id": {"type": "integer", "minimum": 1}, "text": {"type": "string"}, "text_sha256": {"type": "string"}}, "required": ["work_item_id", "text", "text_sha256"], "additionalProperties": False}},
    {"name": "ado_guard_preview_comment", "description": "READ ONLY. Run every check of ado_guard_add_comment on this exact text (same code path): project, assignee by stable id, text length, duplicate. Returns text_sha256, duplicate, would_write and the reasons. Writes nothing.",
     "inputSchema": {"type": "object", "properties": {"work_item_id": {"type": "integer", "minimum": 1}, "text": {"type": "string"}}, "required": ["work_item_id", "text"], "additionalProperties": False}},
    {"name": "ado_guard_render_markdown", "description": "READ ONLY, local, no network. Deterministic Markdown to HTML of a draft (paragraphs, **bold**, `code`, bullet and numbered lists; HTML is escaped) with the SHA-256 of the HTML, to review before approving. Never applied automatically: the exact text that is approved is the one sent.",
     "inputSchema": {"type": "object", "properties": {"markdown": {"type": "string"}}, "required": ["markdown"], "additionalProperties": False}},
    {"name": "ado_guard_preview_bug", "description": "READ ONLY. Check a bug creation (parent is a PBI or QA task assigned to the verified identity, the Bug type, duplicates, the project's content checks) and return each field as it will be sent (label in the Board, reference name, exact value), the exact JSON Patch, payload_sha256 and patch_sha256 to approve, and the attachments and creation comment listed as separate operations. Writes nothing.",
     "inputSchema": {"type": "object", "properties": {"parent_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "maxLength": 255}, "repro_steps": {"type": "string"}, "system_info": {"type": "string"}, "acceptance_criteria": {"type": "string"}, "extra_fields": {"type": "object", "additionalProperties": {"type": "string"}}, "attachments": {"type": "array", "items": {"type": "string"}}, "creation_comment": {"type": "string"}}, "required": ["parent_id", "title", "repro_steps", "system_info"], "additionalProperties": False}},
    {"name": "ado_guard_create_bug", "description": "Create ONE bug under a parent assigned to the verified identity, born UNASSIGNED, with the Parent link in the same request. DISABLED unless the launcher enables it. Needs the user's explicit approval of the exact content; payload_sha256 comes from the preview. Never repeats a request and reads the result back.",
     "inputSchema": {"type": "object", "properties": {"parent_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "maxLength": 255}, "repro_steps": {"type": "string"}, "system_info": {"type": "string"}, "acceptance_criteria": {"type": "string"}, "extra_fields": {"type": "object", "additionalProperties": {"type": "string"}}, "payload_sha256": {"type": "string"}, "patch_sha256": {"type": "string"}}, "required": ["parent_id", "title", "repro_steps", "system_info", "payload_sha256"], "additionalProperties": False}},
    {"name": "ado_guard_attach_file", "description": "Upload ONE file from the allowed evidence folder and link it to ONE work item assigned to the verified identity (or to an unassigned bug this guard created), in a single operation. A video (.mp4/.mov/.webm, up to 60 MB) needs <file>.review.json next to it (reviewed for personal data and credentials). Needs the user's explicit approval of that file.",
     "inputSchema": {"type": "object", "properties": {"work_item_id": {"type": "integer", "minimum": 1}, "file_path": {"type": "string"}, "comment": {"type": "string", "maxLength": 200}}, "required": ["work_item_id", "file_path"], "additionalProperties": False}},
]
KNOWN = {t["name"]: set(t["inputSchema"]["properties"]) for t in TOOLS}


def redact(text: str, secrets) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return text


def call_tool(guard: Guard, name: str, args: dict, log) -> dict:
    """Returns an MCP tool result; every refusal is an isError result with the reason, never an exception."""
    try:
        if name not in KNOWN:
            raise Blocked(f"unknown tool {name!r}: this server only exposes {sorted(KNOWN)}")
        if not isinstance(args, dict) or set(args) - KNOWN[name]:
            raise Blocked(f"unexpected arguments for {name}: only {sorted(KNOWN[name])} are accepted")
        if name == "ado_guard_status":
            out = guard.status()
        elif name == "ado_guard_render_markdown":
            md = args.get("markdown")
            if not isinstance(md, str):
                raise Blocked("markdown must be a text")
            rendered = markdown_to_html(md)
            out = {"ok": True, "read_only": True, "html": rendered, "html_sha256": sha256_text(rendered), "length": len(rendered),
                   "within_comment_limit": len(rendered) <= MAX_COMMENT,
                   "note": "nothing was sent; to publish this HTML, approve exactly this text and use its hash"}
        elif name == "ado_guard_preview_comment":
            out = guard.preview_comment(args.get("work_item_id"), args.get("text"))
        elif name == "ado_guard_probe":
            out = guard.probe_write_credential()
        elif name == "ado_guard_check":
            out = guard.check(args.get("work_item_id"))
        elif name == "ado_guard_preview_bug":
            out = guard.preview_bug(args)
        elif name == "ado_guard_create_bug":
            out = guard.create_bug(args)
        elif name == "ado_guard_add_comment":
            out = guard.add_comment(args.get("work_item_id"), args.get("text"), args.get("text_sha256"))
        else:
            out = guard.attach_file(args.get("work_item_id"), args.get("file_path"), args.get("comment", ""))
        log({"tool": name, "work_item_id": args.get("work_item_id", args.get("parent_id")), "decision": "allowed"})
        return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}
    except Blocked as e:
        log({"tool": name, "work_item_id": ((args or {}).get("work_item_id") or (args or {}).get("parent_id")) if isinstance(args, dict) else None,
             "decision": "blocked", "reason": str(e)})
        return {"isError": True, "content": [{"type": "text", "text": f"BLOCKED: {e}"}]}
    except Exception as e:  # noqa: BLE001 - unexpected: fail closed
        log({"tool": name, "decision": "blocked", "reason": f"internal error {type(e).__name__}"})
        return {"isError": True, "content": [{"type": "text", "text": f"BLOCKED: internal error ({type(e).__name__}); nothing was written"}]}


def handle(guard: Guard, msg: dict, log):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        pv = (msg.get("params") or {}).get("protocolVersion", "2025-03-26")
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": pv, "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params") or {}
        return {"jsonrpc": "2.0", "id": mid, "result": call_tool(guard, p.get("name"), p.get("arguments") or {}, log)}
    if mid is None:  # notification
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def startup_probe(guard: Guard) -> dict:
    """The opt-in probe at startup (plan Q6c): ONE read with the write credential, to say early whether it authenticates as the
    verified identity. Compact and without credential facts; a failure is a result, never an exception, and never stops the
    server (the writes stay as they were: this only reports)."""
    if not guard.write_enabled:
        return {"ran": False, "reason": "no usable write credential"}
    try:
        r = guard.probe_write_credential()
    except Blocked as e:
        return {"ran": False, "reason": str(e)}
    except Exception as e:  # noqa: BLE001 - a probe that cannot run must not stop the server
        return {"ran": False, "reason": f"{type(e).__name__}"}
    return {"ran": True, "http_status": r.get("http_status"), "classification": r.get("classification"),
            "identity_matches": r.get("identity_matches"), "at": guard.clock().astimezone(datetime.UTC).isoformat(timespec="seconds")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--org", default=(os.environ.get("ADO_GUARD_ORG") or "").strip() or None,
                    help="Azure DevOps organisation (or ADO_GUARD_ORG); no default: without it the server does not start")
    ap.add_argument("--project", required=True)
    ap.add_argument("--identity-id", required=True, help="stable Azure DevOps identity id (GUID) of the person allowed")
    ap.add_argument("--allowed-dir", required=True, help="the only folder attachments may come from")
    ap.add_argument("--report-dir", default=(os.environ.get("ADO_GUARD_REPORT_DIR") or "").strip() or None,
                    help="optional folder (or ADO_GUARD_REPORT_DIR): one small JSON report per successful comment or attachment")
    ap.add_argument("--created-bugs-file", default=(os.environ.get("ADO_GUARD_CREATED_BUGS_FILE") or "").strip() or None,
                    help="optional ledger (or ADO_GUARD_CREATED_BUGS_FILE) of the bugs this guard created, so that their "
                         "attachments and creation comment may follow while they are unassigned, across restarts")
    ap.add_argument("--project-yaml", default=(os.environ.get("ADO_GUARD_PROJECT_YAML") or "").strip() or None,
                    help="optional (or ADO_GUARD_PROJECT_YAML): the workspace's project.yaml, whose write_guard.bugs maps the "
                         "bug's extra fields, labels and content checks; a section that cannot be used stops the start")
    ap.add_argument("--probe-on-start", action="store_true",
                    default=(os.environ.get("ADO_GUARD_PROBE_ON_START") or "").strip() == "1",
                    help="opt-in (or ADO_GUARD_PROBE_ON_START=1): ONE read with the write credential at startup, reported in "
                         "`ado_guard_status` as startup_probe; it never stops the server")
    a = ap.parse_args(argv)
    if not a.org:
        ap.error("organisation missing: pass --org or set ADO_GUARD_ORG (nothing was started, nothing was read or written)")
    try:
        bug_config = load_project_bug_config(a.project_yaml) if a.project_yaml else load_bug_config(None)
    except ConfigError as e:
        ap.error(f"write_guard.bugs: {e} (nothing was started, nothing was read or written)")
    # whitespace at the ends is never part of a token (a paste often adds it); whitespace or control characters INSIDE mean a
    # damaged value: it is refused (writes disabled / read credential unused), never repaired, and only the state is reported
    pat, write_state = screen_credential(os.environ.get("ADO_GUARD_WRITE_PAT"))
    read_pat, read_state = screen_credential(os.environ.get("ADO_GUARD_READ_PAT"))
    secrets = [pat, read_pat]                                   # for redaction, rejected values included
    if write_state != "present":
        pat = None
    if read_state != "present":
        read_pat = None

    def log(rec):
        sys.stderr.write(redact(json.dumps(rec, ensure_ascii=False), secrets) + "\n")
        sys.stderr.flush()

    allow_bug = os.environ.get("ADO_GUARD_ALLOW_BUG_CREATE", "").strip() == "1"          # off unless the launcher sets it
    for who, state in (("write", write_state), ("read", read_state)):
        if state.startswith("rejected"):
            log({"startup": f"{who} credential {state}; it was not used"})
    expiries = {"write": os.environ.get("ADO_GUARD_WRITE_PAT_EXPIRES"), "read": os.environ.get("ADO_GUARD_READ_PAT_EXPIRES")}
    guard = Guard(a.org, a.project, a.identity_id, a.allowed_dir, Http(pat, read_pat), log, write_enabled=bool(pat),
                  allow_bug_create=allow_bug, credentials={"write": write_state, "read": read_state}, expiries=expiries,
                  report_dir=a.report_dir, created_bugs_file=a.created_bugs_file, bug_config=bug_config)
    if a.probe_on_start:
        guard.startup_probe = startup_probe(guard)
        log({"event": "startup_probe", **guard.startup_probe})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            out = handle(guard, json.loads(line), log)
        except Exception as e:  # noqa: BLE001 - a bad line never stops the server nor writes
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": f"internal error {type(e).__name__}"}}
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    for _s in (sys.stdin, sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

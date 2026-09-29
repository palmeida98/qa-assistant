"""
Jira Cloud + Xray Cloud back-end of the tracker adapter (docs/technique-jira-xray.md; plan 10.2): check, import-cases
(section 6), export-cases (section 7), apply (sections 4 and 5.2, with the read back) and bug-search (section 5.1).

The command line (adapters/tracker/cli.py) validates the input, reads the record and prints the plan; this back-end
resolves everything against Jira and Xray with reads only (`plan_*`), writes with --yes (`send_*`, recording each
confirmed write), and reads back (`read_back`).
"""

from __future__ import annotations

import base64
import mimetypes
import os
from datetime import datetime

from adapters.tracker.base import (
    Plan,
    TrackerAdapter,
    batch_marker,
    case_marker,
    fill_template,
    significant_words,
    split_evidence,
)
from adapters.tracker.config import is_question
from adapters.tracker.errors import ConfigError, Conflict, UncertainWrite
from adapters.tracker.record import IdCache, write_cases

from . import fields as F
from .adf import to_adf
from .api import (
    IMPORT,
    IMPORT_MULTIPART,
    JiraApi,
    chunks,
    jql_phrase,
    jql_string,
    key_number,
)

CHUNK = 100               # results per import (technique 4.2)
JQL_CHUNK = 50            # IDs per resolution query
EXECUTION_TYPE = "Test Execution"
PLAN_TYPE = "Test Plan"
CASE_KEYS = ("area", "feature", "preconditions", "priority", "role", "platform", "technique", "origin")


def _iso(ts) -> str | None:
    """A run log instant -> ISO 8601 with an offset (Xray's date-time): a naive instant gets this computer's
    offset."""
    if ts in (None, ""):
        return None
    try:
        d = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d.isoformat(timespec="seconds")


class JiraAdapter(TrackerAdapter):
    tracker = "jira"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.api = JiraApi(ctx)
        self.pk = self.cfg["project_key"]
        self.cases_cfg = self.cfg["cases"]
        self.bugs_cfg = self.cfg["bugs"]
        self.results_cfg = self.cfg["results"]
        self._rich: dict[str, dict] = {}   # export-cases: case -> rich text fields, set after createTest

    # =============================================================================================================
    # check
    # =============================================================================================================

    def check(self) -> list[str]:
        api, problems = self.api, []
        me = api.jira.get("/rest/api/3/myself").json() or {}
        self.ctx.out(f"Jira: authenticated as {me.get('displayName') or me.get('accountId') or '?'}")
        project = api.project()
        self.ctx.out(f"Project {self.pk}: {project.get('name', '?')}")
        types = [self.cases_cfg["issue_type"], self.bugs_cfg["issue_type"], EXECUTION_TYPE]
        if self.results_cfg.get("test_plan_key"):
            types.append(PLAN_TYPE)
        for t in types:
            if api.issue_type(t) is None:
                problems.append(f"issue type {t!r} is not in project {self.pk} (is Xray enabled in the project?)")
        known = {f.get("id") for f in api.jira.get("/rest/api/3/field").json() or []}
        mapped = [("cases", self.cases_cfg["issue_type"], self.cases_cfg.get("fields") or {}),
                  ("bugs", self.bugs_cfg["issue_type"], self.bugs_cfg.get("fields") or {})]
        if self.cases_cfg.get("id_source") == "field":
            mapped[0] = ("cases", mapped[0][1], {**mapped[0][2], "id (id_field)": self.cases_cfg["id_field"]})
        for part, type_name, mapping in mapped:
            if api.issue_type(type_name) is None:
                continue
            meta = api.create_meta(type_name)
            for model, jira_field in mapping.items():
                fid = "labels" if F.is_label(jira_field) else jira_field
                if fid not in known and fid != "labels":
                    problems.append(f"jira.{part}.fields.{model}: field {fid!r} does not exist in this Jira")
                elif fid not in meta:
                    problems.append(f"jira.{part}.fields.{model}: field {fid!r} is not on the create screen of "
                                    f"{type_name!r}")
        wanted = self.bugs_cfg.get("statuses") or []
        if wanted and api.issue_type(self.bugs_cfg["issue_type"]) is not None:
            data = api.jira.get(f"/rest/api/3/project/{self.pk}/statuses").json() or []
            names = {s.get("name") for t in data if str(t.get("name", "")).lower()
                     == self.bugs_cfg["issue_type"].lower() for s in t.get("statuses") or []}
            problems += [f"jira.bugs.statuses: {s!r} is not a status of {self.bugs_cfg['issue_type']!r}"
                         for s in wanted if s not in names]
        if self.bugs_cfg.get("link_type") and self.bugs_cfg["link_type"] not in api.link_types():
            problems.append(f"jira.bugs.link_type {self.bugs_cfg['link_type']!r}: no such issue link type")
        if self.results_cfg.get("test_plan_key"):
            try:
                self._test_plan()
            except Conflict as e:
                problems.append(str(e))
        statuses = set(api.statuses(project.get("id")))
        self.ctx.out(f"Xray: authenticated; test run statuses {sorted(statuses)}")
        for status, target in self.config.status_map.items():
            if target not in (None, "") and str(target).lower() != "skip" and target not in statuses:
                problems.append(f"jira.results.statuses.{status}: {target!r} is not a test run status in Xray "
                                "(create it in the Xray settings, or map the status to skip)")
        return problems

    # =============================================================================================================
    # shared resolution
    # =============================================================================================================

    def _test_plan(self) -> dict | None:
        key = self.results_cfg.get("test_plan_key")
        if not key:
            return None
        issues = self.api.search_or_conflict(f"key = {jql_string(key)}", ["issuetype", "summary"], "test plan")
        if not issues:
            raise Conflict(f"jira.results.test_plan_key {key}: not found")
        issue = issues[0]
        if str(issue["fields"]["issuetype"]["name"]).lower() != PLAN_TYPE.lower():
            raise Conflict(f"jira.results.test_plan_key {key} is a {issue['fields']['issuetype']['name']}, not a "
                           f"{PLAN_TYPE}")
        return {"key": issue["key"], "id": str(issue["id"])}

    def _internal_id(self, issue: dict) -> tuple[str | None, str | None]:
        """(the case's internal ID, a problem) per cases.id_source (technique section 3)."""
        source = self.cases_cfg.get("id_source") or "key"
        f = issue.get("fields") or {}
        if source == "key":
            return issue["key"], None
        if source == "field":
            value, _ = F.read_value(f.get(self.cases_cfg["id_field"]))
            return (value.strip() if value else None), None
        prefix = self.cases_cfg["id_label_prefix"]
        ids = [lab[len(prefix):] for lab in f.get("labels") or [] if lab.startswith(prefix)]
        if len(ids) > 1:
            return ids[0], f"{issue['key']} has several ID labels {ids}"
        return (ids[0] if ids else None), None

    def _id_query(self, ids: list[str]) -> str:
        source = self.cases_cfg.get("id_source") or "key"
        if source == "key":
            return f"key in ({', '.join(jql_string(i) for i in ids)})"
        base = f"project = {jql_string(self.pk)} AND issuetype = {jql_string(self.cases_cfg['issue_type'])}"
        if source == "field":
            field = F.jql_field(self.cases_cfg["id_field"])
            return f"{base} AND ({' OR '.join(f'{field} ~ {jql_phrase(i)}' for i in ids)})"
        prefix = self.cases_cfg["id_label_prefix"]
        return f"{base} AND labels in ({', '.join(jql_string(prefix + i) for i in ids)})"

    def _id_fields(self) -> list[str]:
        out = ["summary", "issuetype", "labels"]
        if self.cases_cfg.get("id_source") == "field":
            out.append(self.cases_cfg["id_field"])
        return out

    def resolve_cases(self, ids: list[str], ids_cache: IdCache) -> tuple[dict, list[str]]:
        """internal ID -> {key, id} confirmed in Jira (section 3); (the map, notes). Conflict (exit 2) for a case not
        found, a case mapped to two tests, an ID the cache has pointing elsewhere, or an issue that is not a Test."""
        found: dict[str, list[dict]] = {}
        problems, notes = [], []
        by_cached_id = {str(v.get("id")): k for k, v in ids_cache.cases.items() if v.get("id")}
        for chunk in chunks(ids, JQL_CHUNK):
            issues = self.api.search_or_conflict(self._id_query(chunk), self._id_fields(), "cases of the batch")
            for issue in issues:
                cid, problem = self._internal_id(issue)
                if problem:
                    problems.append(problem)
                if cid not in chunk and by_cached_id.get(str(issue["id"])) in chunk:
                    cid = by_cached_id[str(issue["id"])]    # moved: the key changed, the issue id did not
                if cid in chunk:
                    found.setdefault(cid, []).append(issue)
        out = {}
        for cid in ids:
            issues = found.get(cid) or []
            if not issues:
                problems.append(f"{cid}: no {self.cases_cfg['issue_type']} with this ID in Jira")
                continue
            if len(issues) > 1:
                problems.append(f"{cid}: mapped to {len(issues)} issues {[i['key'] for i in issues]}")
                continue
            issue = issues[0]
            itype = issue["fields"].get("issuetype", {}).get("name")
            if str(itype).lower() != self.cases_cfg["issue_type"].lower():
                problems.append(f"{cid}: {issue['key']} is a {itype}, not a {self.cases_cfg['issue_type']}")
                continue
            cached = ids_cache.get(cid)
            if cached and cached.get("id") and str(cached["id"]) != str(issue["id"]):
                problems.append(f"{cid}: tracker/ids.yaml has issue id {cached['id']} ({cached.get('key')}), Jira "
                                f"has {issue['id']} ({issue['key']}) — the ID now points elsewhere")
                continue
            if cached and cached.get("key") and cached["key"] != issue["key"]:
                notes.append(f"{cid}: moved {cached['key']} -> {issue['key']} (followed through the issue id)")
            out[cid] = {"key": issue["key"], "id": str(issue["id"])}
        if problems:
            raise Conflict(f"{len(problems)} case(s) do not match Jira: nothing sent", problems)
        return out, notes

    def _marker_issues(self, marker: str, fields=("summary", "issuetype", "labels")) -> list:
        return self.api.search(f"project = {jql_string(self.pk)} AND labels = {jql_string(marker)}", list(fields))

    # -- field formatting ------------------------------------------------------------------------------------------

    def _put(self, out: dict, labels: list, meta: dict, mapping: str, value, what: str, problems: list,
             notes: list, type_name: str) -> None:
        if value in (None, "", []):
            return
        if F.is_label(mapping):
            for v in (value if isinstance(value, list) else [value]):
                label, changed = F.label_value(F.label_prefix(mapping), v)
                if changed:
                    notes.append(f"{what}: {v!r} written as the label {label!r} (labels cannot have spaces)")
                labels.append(label)
            return
        if mapping not in meta:
            problems.append(f"{what}: field {mapping!r} is not on the create screen of {type_name!r}")
            return
        out[mapping] = F.format_value(mapping, meta[mapping].get("schema"), value)

    def _required(self, out: dict, meta: dict, type_name: str, what: str) -> list[str]:
        skip = {"project", "issuetype", "reporter", "labels"}
        return [f"{what}: the required field {m.get('name', fid)!r} ({fid}) of {type_name!r} has no value (map it in "
                "project.yaml or add it to the defaults)" for fid, m in meta.items()
                if m.get("required") and not m.get("hasDefaultValue") and fid not in skip and fid not in out]

    def _redact(self, text, what: str, notes: list) -> str:
        text = "" if text is None else str(text)
        red = self.ctx.redactor.text(text)
        if red != text:
            notes.append(f"{what}: redacted (a credential's value was replaced by its name)")
        return red

    # =============================================================================================================
    # apply (sections 4 and 5.2)
    # =============================================================================================================

    def _bug_description(self, batch, item) -> str:
        nd = item.data["new_defect"]
        lines = [f"Environment: {batch.run['environment']} · Build: {batch.run['build']}"]
        for key, label in (("role", "Role"), ("platform", "Platform"), ("preconditions", "Preconditions")):
            if nd.get(key) not in (None, ""):
                lines.append(f"{label}: {nd[key]}")
        lines += [f"Expected: {nd['expected']}", f"Observed: {nd['observed']}", f"Linked cases: {item.tc}"]
        blocks = [str(nd["description"]).strip()] if nd.get("description") not in (None, "") else []
        blocks.append("\n".join(lines))
        steps = nd.get("steps")
        if isinstance(steps, list) and steps:
            blocks.append("Steps:")
            blocks.append("\n".join(f"- {s}" for s in steps))
        elif steps not in (None, "", []):
            blocks.append(f"Steps:\n{steps}")
        return "\n\n".join(blocks)

    def _bug_fields(self, batch, item, meta, problems, notes) -> dict:
        nd = item.data["new_defect"]
        mapping = dict(self.bugs_cfg.get("fields") or {})
        defaults = dict(self.bugs_cfg.get("defaults") or {})
        type_name = self.bugs_cfg["issue_type"]
        what = f"{item.tc} (new bug)"
        out = {"project": {"key": self.pk}, "issuetype": {"name": type_name}}
        labels = [batch_marker(batch.batch_id), case_marker(item.tc)]
        title_field = mapping.pop("title", "summary")
        out[title_field] = self._redact(nd["title"], f"{what} title", notes)
        desc_field = mapping.pop("description", "description")
        description = self._redact(self._bug_description(batch, item), f"{what} description", notes)
        self._put(out, labels, meta, desc_field, description, what, problems, notes, type_name)
        for model, jira_field in mapping.items():
            value = nd.get(model)
            if value in (None, "") and model == "environment":
                value = batch.run["environment"]
            if value in (None, ""):
                value = defaults.get(model)
            if isinstance(value, str):
                value = self._redact(value, f"{what} {model}", notes)
            self._put(out, labels, meta, jira_field, value, what, problems, notes, type_name)
        for fid, value in defaults.items():   # a default whose key is not a model field is a Jira field id
            if fid not in self.bugs_cfg.get("fields", {}) and fid not in out:
                self._put(out, labels, meta, fid, value, what, problems, notes, type_name)
        out["labels"] = labels
        problems += self._required(out, meta, type_name, what)
        return out

    def _test_entry(self, item, key: str, notes: list, not_sent: list) -> dict:
        """One test of the Xray JSON import (the new bug's key is added to `defects` at the send)."""
        entry = {"testKey": key, "status": item.target}
        comment = self._redact(item.data.get("actual_result"), f"{item.tc} comment", notes)
        if comment:
            entry["comment"] = comment
        ts = _iso(item.data.get("ts"))
        if ts:
            entry["start"] = entry["finish"] = ts
        if item.data.get("bug") not in (None, ""):
            entry["defects"] = [str(item.data["bug"]).strip()]
        files = item.data.get("evidence")
        files = [files] if isinstance(files, str) else list(files or [])
        send, skipped = split_evidence(self.ctx.workspace, files, self.config)
        not_sent += [f"{item.tc} (evidence) {s}" for s in skipped]
        evidence = []
        for path in send:
            with open(path, "rb") as f:
                data = f.read()
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or path.lower().endswith((".json", ".log", ".txt", ".md")):
                data = self.ctx.redactor.text(data.decode("utf-8", errors="replace")).encode("utf-8")
            evidence.append({"data": base64.b64encode(data).decode("ascii"), "filename": os.path.basename(path),
                             "contentType": ctype})
        if evidence:
            entry["evidence"] = evidence
        return entry

    def plan_apply(self, batch, record):
        api, plan = self.api, Plan()
        ids = IdCache(self.ctx.workspace, "jira")
        tcs = [i.tc for i in batch.to_send]
        cases, notes = self.resolve_cases(tcs, ids) if tcs else ({}, [])
        plan.notes += notes
        # existing bugs of the batch (linked, commented, transitioned)
        bug_keys = sorted({str(i.data["bug"]).strip() for i in batch.items if i.data.get("bug") not in (None, "")},
                          key=key_number)
        bugs: dict[str, dict] = {}
        for chunk in chunks(bug_keys, JQL_CHUNK):
            for issue in api.search_or_conflict(f"key in ({', '.join(jql_string(k) for k in chunk)})",
                                                ["summary", "status", "issuetype"], "bugs of the batch"):
                bugs[issue["key"]] = {"id": str(issue["id"]), "status": issue["fields"]["status"]["name"],
                                      "summary": issue["fields"].get("summary")}
        missing = [k for k in bug_keys if k not in bugs]
        if missing:
            raise Conflict(f"bug(s) of the batch not found in Jira: {missing}: nothing sent")
        test_plan = self._test_plan()
        # objects this batch already created (a create that timed out after Jira did it; a record lost)
        marker = batch_marker(batch.batch_id)
        marked = self._marker_issues(marker)
        exec_found = [i for i in marked if i["fields"]["issuetype"]["name"].lower() == EXECUTION_TYPE.lower()]
        if len(exec_found) > 1:
            raise Conflict(f"{len(exec_found)} Test Executions carry {marker}: {[i['key'] for i in exec_found]}")
        api.authenticate()   # Xray's credentials are checked before the approval

        recorded_bugs = {w["case"]: w for w in record.find("bug")}
        new_items = [i for i in batch.to_send if i.data.get("new_defect") not in (None, {})]
        plan.not_sent += [f"{i.tc} ({i.status}): its new_defect is not created either (the item is not sent)"
                          for i in batch.not_sent if i.data.get("new_defect") not in (None, {})]
        new_bugs, reused = {}, {}
        problems: list[str] = []
        for item in new_items:
            if item.tc in recorded_bugs:
                continue
            found = [i for i in marked if case_marker(item.tc) in (i["fields"].get("labels") or [])
                     and i["fields"]["issuetype"]["name"].lower() == self.bugs_cfg["issue_type"].lower()]
            if found:
                reused[item.tc] = {"key": found[0]["key"], "id": str(found[0]["id"])}
                plan.notes.append(f"{item.tc}: bug {found[0]['key']} found by its markers (an earlier run created "
                                  "it): reused, not created again")
                continue
            meta = api.create_meta(self.bugs_cfg["issue_type"])   # read once, only when a bug is created
            new_bugs[item.tc] = self._bug_fields(batch, item, meta, problems, plan.notes)
            plan.add("new bugs", "POST", "/rest/api/3/issue",
                     f"{item.tc}: {self.bugs_cfg['issue_type']} \"{item.data['new_defect']['title']}\" "
                     f"(labels {', '.join(new_bugs[item.tc]['labels'])})", item.tc)
        links = []
        if self.bugs_cfg.get("link_type") and new_items:
            if self.bugs_cfg["link_type"] not in api.link_types():
                problems.append(f"jira.bugs.link_type {self.bugs_cfg['link_type']!r}: no such issue link type")
            links = [i.tc for i in new_items if not record.find("link", i.tc)]
        if problems:
            raise ConfigError(f"{len(problems)} problem(s) with the new bugs' fields: nothing sent", problems)

        # results: one Test Execution per batch, in chunks of 100
        execution = None
        for w in record.find("execution"):
            execution = {"key": w["key"], "id": str(w["id"])}
        if execution is None and exec_found:
            execution = {"key": exec_found[0]["key"], "id": str(exec_found[0]["id"]), "reused": True}
            plan.notes.append(f"Test Execution {execution['key']} found by its marker {marker} (an earlier run "
                              "created it with the first chunk): reused")
        summary = fill_template(self.results_cfg.get("summary"), batch_id=batch.batch_id,
                               environment=batch.run["environment"], build=batch.run["build"])
        done_chunks = {w.get("chunk") for w in record.find("results")} | ({1} if execution else set())
        groups = chunks(batch.to_send, CHUNK)
        plan_chunks = {}
        for n, group in enumerate(groups, 1):
            if n in done_chunks:
                continue
            plan_chunks[n] = [(i.tc, self._test_entry(i, cases[i.tc]["key"], plan.notes, plan.not_sent))
                              for i in group]
            desc = ", ".join(f"{cases[i.tc]['key']} {i.target}"
                             + (f" [defect {i.data['bug']}]" if i.data.get("bug") else "")
                             + (" [defect: the new bug]" if i.tc in new_bugs or i.tc in reused
                                or i.tc in recorded_bugs else "") for i in group)
            if n == 1:
                plan.add("results", "POST", IMPORT_MULTIPART,
                         f"chunk 1/{len(groups)}: new {EXECUTION_TYPE} \"{summary}\" (label {marker}; environment "
                         f"{batch.run['environment']}"
                         + (f"; test plan {test_plan['key']}" if test_plan else "") + f"): {desc}")
            else:
                plan.add("results", "POST", IMPORT, f"chunk {n}/{len(groups)} into the execution: {desc}")
        # existing bugs: a comment on each, and the bug_status transitions
        comments, transitions = [], {}
        for item in batch.to_send:
            key = item.data.get("bug")
            if key in (None, "") or record.find("comment", item.tc):
                continue
            tag = f"{marker} · {item.tc}"
            if record.exists and any(tag in (F.read_value(c.get("body"))[0] or "") for c in api.comments(key)):
                plan.notes.append(f"{item.tc}: the comment on {key} is already there (an earlier run): not repeated")
                continue
            comments.append(item.tc)
            plan.add("updated bugs", "POST", f"/rest/api/3/issue/{key}/comment",
                     f"{key}: comment \"{tag}: {item.status}\" with the actual result", item.tc)
        for item in batch.items:
            want = item.data.get("bug_status")
            if want in (None, "") or record.find("transition", item.tc):
                continue
            key = item.data["bug"]
            if bugs[key]["status"].lower() == str(want).lower():
                plan.notes.append(f"{item.tc}: {key} is already {want}: no transition")
                continue
            options = api.transitions(key)
            match = [t for t in options if str((t.get("to") or {}).get("name", "")).lower() == str(want).lower()]
            if not match:
                raise Conflict(f"{item.tc}: {key} ({bugs[key]['status']}) has no transition to {want!r}; available: "
                               f"{[(t.get('to') or {}).get('name') for t in options]}: nothing sent")
            transitions[item.tc] = str(match[0]["id"])
            plan.add("updated bugs", "POST", f"/rest/api/3/issue/{key}/transitions",
                     f"{key}: {bugs[key]['status']} -> {want}", item.tc)
        for tc in links:
            plan.add("links", "POST", "/rest/api/3/issueLink",
                     f"the new bug of {tc} {self.bugs_cfg['link_type']} {cases[tc]['key']}", tc)
        plan.data = {"cases": cases, "bugs": bugs, "new_bugs": new_bugs, "reused": reused, "execution": execution,
                     "chunks": plan_chunks, "groups": len(groups), "summary": summary, "test_plan": test_plan,
                     "comments": comments, "transitions": transitions, "links": links, "moved": notes}
        return plan

    def send_apply(self, batch, plan, record):
        d, api = plan.data, self.api
        ids = IdCache(self.ctx.workspace, "jira")
        for cid, ref in d["cases"].items():
            ids.set(cid, **ref)
        if d["cases"]:
            ids.save()
        # 1. new bugs (their keys go into the test runs)
        for tc, ref in d["reused"].items():
            record.add("bug", tc, key=ref["key"], id=ref["id"], reused=True)
        for tc, fields in d["new_bugs"].items():
            created = api.create_issue(fields)
            record.add("bug", tc, key=created.get("key"), id=str(created.get("id")))
        new_bug = {w["case"]: w for w in record.find("bug")}
        # 2. the results
        execution = d["execution"]
        if execution and execution.get("reused"):
            record.add("execution", key=execution["key"], id=execution["id"], chunk=1, reused=True)
        for n, entries in d["chunks"].items():
            tests = []
            for tc, entry in entries:
                if tc in new_bug:
                    entry = {**entry, "defects": list(entry.get("defects") or []) + [new_bug[tc]["key"]]}
                tests.append(entry)
            if n == 1:
                info = {"fields": {"project": {"key": self.pk}, "summary": d["summary"],
                                   "issuetype": {"name": EXECUTION_TYPE}, "labels": [batch_marker(batch.batch_id)]},
                        "xrayFields": {"environments": [batch.run["environment"]],
                                       **({"testPlanKey": d["test_plan"]["key"]} if d["test_plan"] else {})}}
                created = api.import_new({"tests": tests}, info)
                execution = {"key": created.get("key"), "id": str(created.get("id"))}
                record.add("execution", key=execution["key"], id=execution["id"], chunk=1,
                           tests=[t["testKey"] for t in tests])
            else:
                api.import_into({"testExecutionKey": execution["key"], "tests": tests})
                record.add("results", chunk=n, execution=execution["key"], tests=[t["testKey"] for t in tests])
        # 3. existing bugs: comments and transitions; links of the new bugs
        marker = batch_marker(batch.batch_id)
        items = {i.tc: i for i in batch.items}
        for tc in d["comments"]:
            item = items[tc]
            key = item.data["bug"]
            text = (f"{marker} · {tc}: {item.status} · environment {batch.run['environment']} · build "
                    f"{batch.run['build']}\n{self._redact(item.data.get('actual_result'), tc, [])}").strip()
            created = api.add_comment(key, to_adf(text))
            record.add("comment", tc, bug=key, id=str((created or {}).get("id")))
        for tc, transition_id in d["transitions"].items():
            key = items[tc].data["bug"]
            api.transition(key, transition_id)
            record.add("transition", tc, bug=key, status=items[tc].data["bug_status"])
        for tc in d["links"]:
            bug = new_bug[tc]["key"]
            api.link(self.bugs_cfg["link_type"], bug, d["cases"][tc]["key"])
            record.add("link", tc, bug=bug, test=d["cases"][tc]["key"])

    def read_back(self, batch, plan, record):
        """The execution's test runs (status and defects) against what was sent (section 4.2 step 6)."""
        executions = record.find("execution")
        if not executions:
            return []
        runs = self.api.test_runs(executions[-1]["id"])
        by_test = {str((r.get("test") or {}).get("issueId")): r for r in runs}
        new_bug = {w["case"]: w for w in record.find("bug")}
        bugs = plan.data.get("bugs") or {}
        differences = []
        for item in batch.to_send:
            ref = plan.data["cases"][item.tc]
            run = by_test.get(ref["id"])
            if run is None:
                differences.append(f"{item.tc} ({ref['key']}): no test run in {executions[-1]['key']}")
                continue
            got = (run.get("status") or {}).get("name")
            if str(got).upper() != str(item.target).upper():
                differences.append(f"{item.tc} ({ref['key']}): {item.target} sent, {got} read back")
            defects = {str(x) for x in run.get("defects") or []}
            wanted = []
            if item.data.get("bug"):
                wanted.append((item.data["bug"], bugs.get(item.data["bug"], {}).get("id")))
            if item.tc in new_bug:
                wanted.append((new_bug[item.tc]["key"], new_bug[item.tc].get("id")))
            for key, iid in wanted:
                if key not in defects and str(iid) not in defects:
                    differences.append(f"{item.tc} ({ref['key']}): defect {key} not linked to the test run")
        return differences

    # =============================================================================================================
    # bug-search (section 5.1)
    # =============================================================================================================

    def bug_search(self, draft: dict, include_closed: bool) -> list[dict]:
        mapping = self.bugs_cfg.get("fields") or {}
        base = (f"project = {jql_string(self.pk)} AND issuetype = {jql_string(self.bugs_cfg['issue_type'])}"
                + ("" if include_closed or not self.bugs_cfg.get("open_jql") else f" AND ({self.bugs_cfg['open_jql']})"))
        criteria = []   # (name, JQL clause, check on a candidate)
        for model in ("area", "feature", "sub_area", "platform", "role"):
            value, target = draft.get(model), mapping.get(model)
            if value in (None, "") or not target:
                continue
            if F.is_label(target):
                label, _ = F.label_value(F.label_prefix(target), value)
                criteria.append((model, f"labels = {jql_string(label)}", ("labels", label)))
            else:
                criteria.append((model, f"{F.jql_field(target)} = {jql_string(value)}", (target, str(value))))
        words = significant_words(draft.get("observed"), draft.get("title"))
        if words:
            criteria.append(("text", f"text ~ {jql_string(' '.join(chr(34) + w + chr(34) for w in words))}",
                             ("text", words)))
        fetch = sorted({"summary", "status", "labels", *(c[2][0] for c in criteria if c[2][0] not in ("labels",
                                                                                                       "text"))})
        passes = [criteria]
        for drop in ("role", "platform", "sub_area", "text"):
            nxt = [c for c in passes[-1] if c[0] != drop]
            if len(nxt) != len(passes[-1]):
                passes.append(nxt)
        for n, crit in enumerate(passes, 1):
            jql = " AND ".join([base] + [c[1] for c in crit])
            issues = self.api.search(jql, fetch, max_results=50, first_page_only=True)
            if issues:
                out = []
                for issue in issues:
                    f = issue.get("fields") or {}
                    matched = [c[0] for c in crit] + [c[0] for c in criteria if c not in crit
                                                      and self._matches(f, c[2])]
                    out.append({"key": issue["key"], "summary": f.get("summary"),
                                "status": (f.get("status") or {}).get("name"),
                                "matched": [c[0] for c in criteria if c[0] in matched], "pass": n})
                out.sort(key=lambda c: (-len(c["matched"]), key_number(c["key"])))
                return out
        return []

    @staticmethod
    def _matches(fields: dict, check) -> bool:
        name, want = check
        if name == "labels":
            return want in (fields.get("labels") or [])
        if name == "text":
            text = str(fields.get("summary") or "").lower()
            return all(w in text for w in want)
        value, _ = F.read_value(fields.get(name))
        return bool(value) and want.lower() in [v.strip().lower() for v in value.split(",")]

    # =============================================================================================================
    # import-cases (section 6)
    # =============================================================================================================

    def import_cases(self, query):
        mapping = self.cases_cfg.get("fields") or {}
        type_name = self.cases_cfg["issue_type"]
        jql = query or self.cases_cfg.get("jql") or (f"project = {jql_string(self.pk)} AND issuetype = "
                                                     f"{jql_string(type_name)}")
        wanted = self._id_fields() + sorted({v for v in mapping.values() if not F.is_label(v)})
        issues = self.api.search(jql, sorted(set(wanted)))
        test_plan = self._test_plan()
        xray = self.api.tests([str(i["id"]) for i in issues], test_plan["id"] if test_plan else None)
        back: dict[str, str] = {}
        for status, target in self.config.status_map.items():
            if target not in (None, "") and str(target).lower() != "skip" and not is_question(status):
                if str(target).upper() in back:
                    back[str(target).upper() + "#dup"] = status
                else:
                    back[str(target).upper()] = status
        cases, flags, seen = [], [], {}
        for issue in sorted(issues, key=lambda i: key_number(i["key"])):
            f = issue.get("fields") or {}
            key = issue["key"]
            test = xray.get(str(issue["id"]))
            if test is None:
                flags.append({"case": key, "flag": "not an Xray test: not imported"})
                continue
            cid, problem = self._internal_id(issue)
            if problem:
                flags.append({"case": key, "flag": problem})
            if not cid:
                flags.append({"case": key, "flag": f"no internal ID ({self.cases_cfg.get('id_source')}): not imported"})
                continue
            seen.setdefault(cid, []).append(key)
            case = {"id": cid, "key": key, "title": f.get("summary")}
            simplified = False
            for model in CASE_KEYS + tuple(k for k in mapping if k not in CASE_KEYS and k not in ("title",)):
                target = mapping.get(model)
                if not target:
                    case[model] = None
                elif F.is_label(target):
                    case[model] = F.read_labels(f.get("labels"), F.label_prefix(target))
                else:
                    case[model], s = F.read_value(f.get(target))
                    simplified = simplified or s
            pre = ((test.get("preconditions") or {}).get("results")) or []
            if pre:
                texts = [p.get("definition") for p in pre if p.get("definition")]
                case["preconditions"] = "\n\n".join([t for t in [case.get("preconditions")] + texts if t])
                case["preconditions_from"] = [(p.get("jira") or {}).get("key") or p.get("issueId") for p in pre]
            steps = [{"action": s.get("action") or "", "data": s.get("data") or "", "expected": s.get("result") or ""}
                     for s in test.get("steps") or []]
            case["steps"] = steps
            case["expected"] = steps[-1]["expected"] if steps else None
            case["test_type"] = (test.get("testType") or {}).get("name")
            status = ((test.get("status") or {}).get("name")) if test_plan else None
            case["last_status"] = back.get(str(status).upper()) if status else None
            if status and str(status).upper() + "#dup" in back:
                flags.append({"case": cid, "flag": f"Xray status {status} is mapped from several project statuses: "
                                                   f"imported as {case['last_status']}"})
            if status and case["last_status"] is None:
                flags.append({"case": cid, "flag": f"Xray status {status} is not in jira.results.statuses"})
            if not steps:
                flags.append({"case": cid, "flag": "no steps"})
            if case["test_type"] and case["test_type"] != "Manual":
                flags.append({"case": cid, "flag": f"test type {case['test_type']}: read only, never exported"})
            if len([s for s in steps if s["expected"].strip()]) > 1:
                flags.append({"case": cid, "flag": "several expected results (methodology 5.3)"})
            if simplified:
                flags.append({"case": cid, "flag": "rich_text_simplified"})
            cases.append(case)
        dup = {cid: keys for cid, keys in seen.items() if len(keys) > 1}
        if dup:
            raise Conflict("two tests with the same internal ID: nothing written", [f"{c}: {k}" for c, k in dup.items()])
        ids = IdCache(self.ctx.workspace, "jira")
        issue_ids = {i["key"]: str(i["id"]) for i in issues}
        for case in cases:
            ids.set(case["id"], key=case["key"], id=issue_ids[case["key"]])
        ids.save()
        write_cases(self.ctx.workspace, "jira", cases, flags, self.ctx.now)
        return {"cases": len(cases), "flags": [f"{x['case']}: {x['flag']}" for x in flags]}

    # =============================================================================================================
    # export-cases (section 7)
    # =============================================================================================================

    def plan_export(self, doc, record):
        api, plan = self.api, Plan()
        source = self.cases_cfg.get("id_source") or "key"
        type_name = self.cases_cfg["issue_type"]
        name = record.batch_id
        marker = batch_marker(name)
        recorded = {w["case"]: w for w in record.find("case")}
        marked = [i for i in self._marker_issues(marker) if i["fields"]["issuetype"]["name"].lower()
                  == type_name.lower()]
        marked_ids = {str(i["id"]) for i in marked}
        todo, reused = [], {}
        for case in doc["cases"]:
            cid = str(case["id"]).strip()
            if cid in recorded:
                continue
            hit = [i for i in marked if str(i["fields"].get("summary", "")).strip() == str(case["title"]).strip()]
            if hit:
                reused[cid] = {"key": hit[0]["key"], "id": str(hit[0]["id"])}
                plan.notes.append(f"{cid}: {hit[0]['key']} found by its marker {marker} (an earlier run created it): "
                                  "reused, not created again")
            else:
                todo.append(case)
        conflicts = []
        if source != "key" and todo:
            ids = [str(c["id"]).strip() for c in todo]
            for chunk in chunks(ids, JQL_CHUNK):
                for issue in api.search_or_conflict(self._id_query(chunk), self._id_fields(), "IDs of the cases"):
                    cid, _ = self._internal_id(issue)
                    if cid in chunk and str(issue["id"]) not in marked_ids:
                        conflicts.append(f"{cid}: already exists as {issue['key']}")
        titles = [str(c["title"]).strip() for c in todo]
        for chunk in chunks(titles, 20):
            jql = (f"project = {jql_string(self.pk)} AND issuetype = {jql_string(type_name)} AND ("
                   + " OR ".join(f"summary ~ {jql_phrase(t)}" for t in chunk) + ")")
            for issue in api.search(jql, ["summary", "labels"]):
                s = str(issue["fields"].get("summary", "")).strip()
                if s.casefold() in {t.casefold() for t in chunk} and str(issue["id"]) not in marked_ids:
                    conflicts.append(f"\"{s}\": a {type_name} with the same summary exists ({issue['key']}) — a "
                                     "candidate duplicate (methodology 10): remove the case from the file, or change "
                                     "its title, and run again")
        if conflicts:
            raise Conflict(f"{len(conflicts)} case(s) already in Jira: nothing created", conflicts)
        test_plan = self._test_plan()
        meta = api.create_meta(type_name) if todo else {}
        problems, creates = [], {}
        for case in todo:
            cid = str(case["id"]).strip()
            creates[cid] = self._test_variables(case, name, meta, problems, plan.notes)
            n = len(creates[cid]["steps"])
            plan.add("cases", "POST", "/api/v2/graphql",
                     f"createTest {cid}: \"{case['title']}\" (Manual, {n} step(s)"
                     + (f", folder {self.cases_cfg['folder']}" if self.cases_cfg.get("folder") else "")
                     + f", label {marker})", cid)
        if problems:
            raise ConfigError(f"{len(problems)} problem(s) with the cases' fields: nothing created", problems)
        if test_plan and (todo or reused):
            plan.add("cases", "POST", "/api/v2/graphql",
                     f"addTestsToTestPlan {test_plan['key']}: the {len(todo) + len(reused)} new test(s)")
        if todo:
            api.authenticate()
        plan.data = {"creates": creates, "reused": reused, "test_plan": test_plan, "rich": self._rich}
        return plan

    def _test_variables(self, case, name, meta, problems, notes) -> dict:
        mapping = self.cases_cfg.get("fields") or {}
        type_name = self.cases_cfg["issue_type"]
        cid = str(case["id"]).strip()
        what = f"case {cid}"
        out = {"project": {"key": self.pk}, "summary": self._redact(case["title"], f"{what} title", notes)}
        labels = [batch_marker(name)]
        source = self.cases_cfg.get("id_source") or "key"
        if source == "field":
            self._put(out, labels, meta, self.cases_cfg["id_field"], cid, what, problems, notes, type_name)
        elif source == "label":
            labels.append(self.cases_cfg["id_label_prefix"] + cid)
        for model, target in mapping.items():
            value = case.get(model)
            if isinstance(value, str):
                value = self._redact(value, f"{what} {model}", notes)
            self._put(out, labels, meta, target, value, what, problems, notes, type_name)
        out["labels"] = labels
        problems += self._required(out, meta, type_name, what)
        # rich text fields go through Jira's REST v3 right after the create (ADF), not through createTest's `jira`
        rich = {k: v for k, v in out.items() if F.is_adf(v)}
        for k in rich:
            out.pop(k)
        if rich:
            self._rich[cid] = rich
        steps = [{"action": self._redact(s.get("action"), what, notes), "data": self._redact(s.get("data"), what, notes),
                  "result": self._redact(s.get("expected"), what, notes)} for s in case.get("steps") or []]
        variables = {"testType": {"name": "Manual"}, "steps": steps, "jira": {"fields": out}}
        if self.cases_cfg.get("folder"):
            variables["folderPath"] = self.cases_cfg["folder"]
        return variables

    def send_export(self, doc, plan, record):
        d, api = plan.data, self.api
        ids = IdCache(self.ctx.workspace, "jira")
        source = self.cases_cfg.get("id_source") or "key"
        created_ids = []
        for cid, ref in d["reused"].items():
            record.add("case", cid, key=ref["key"], id=ref["id"], reused=True)
        for cid, variables in d["creates"].items():
            result = api.create_test(variables)
            test = result.get("test") or {}
            key = (test.get("jira") or {}).get("key")
            if not key or not test.get("issueId"):
                raise UncertainWrite(f"createTest {cid}: no key in the answer {result.get('warnings') or ''}; the next "
                                     "run searches the marker first")
            record.add("case", cid, key=key, id=str(test["issueId"]),
                       **({"warnings": result["warnings"]} if result.get("warnings") else {}))
        for w in record.find("case"):
            final = w["key"] if source == "key" else w["case"]
            ids.set(final, key=w["key"], id=str(w["id"]))
            created_ids.append(str(w["id"]))
            rich = (d.get("rich") or {}).get(w["case"])
            if rich and not record.find("case_fields", w["case"]):
                api.update_issue(w["key"], rich)
                record.add("case_fields", w["case"], key=w["key"], fields=sorted(rich))
        ids.save()
        if d["test_plan"] and created_ids:
            api.add_to_plan(d["test_plan"]["id"], created_ids)
            record.add("test_plan", key=d["test_plan"]["key"], tests=[w["key"] for w in record.find("case")])
        for w in record.find("case"):
            final = w["key"] if source == "key" else w["case"]
            self.ctx.out(f"  {w['case']} -> {w['key']}" + (f" (ID {final})" if final != w["key"] else ""))

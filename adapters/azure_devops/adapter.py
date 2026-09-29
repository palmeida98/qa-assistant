"""
Azure DevOps Services (Test Plans + Boards) back-end of the tracker adapter (docs/technique-azure-devops.md; plan
10.3): check, import-cases (section 6), export-cases (section 7), apply (sections 4 and 5.2, with the read back) and
bug-search (section 5.1).

The command line (adapters/tracker/cli.py) validates the input, reads the record and prints the plan; this back-end
resolves everything against Azure DevOps with reads only (`plan_*`), writes with --yes (`send_*`, recording each
confirmed write), and reads back (`read_back`). The choices follow the Jira back-end (adapters/jira/adapter.py) unless
Azure DevOps makes them wrong; the plan's note of 10.3 lists where and why.
"""

from __future__ import annotations

import base64
import os

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
from adapters.tracker.errors import ConfigError, Conflict, HttpError, UncertainWrite
from adapters.tracker.record import IdCache, read_cases, write_cases

from . import fields as F
from . import steps as S
from .api import AdoApi, chunks, wiql_string
from .html import plain, to_html

ZERO = "00000000-0000-0000-0000-000000000000"
ID_CHUNK = 50             # IDs per resolution WIQL (field/tag)
RESULTS_CHUNK = 100       # results per PATCH
COMMENT_MAX = 1000        # a test result's comment (the documented maximum)
CASE_KEYS = ("area", "feature", "preconditions", "priority", "role", "platform", "technique", "origin")
STEPS = "Microsoft.VSTS.TCM.Steps"
PARAMETERS = "Microsoft.VSTS.TCM.Parameters"
TESTED_BY = "Microsoft.VSTS.Common.TestedBy-Forward"
BASIC = ("the project uses the Basic process, which has no Bug work item type: the adapter needs Agile or Scrum "
         "(or a process inherited from one of them)")


def _int(value) -> int | None:
    text = str(value).strip()
    return int(text) if text.isdigit() else None


class AzureDevOpsAdapter(TrackerAdapter):
    tracker = "azure-devops"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.api = AdoApi(ctx)
        self.cases_cfg = self.cfg["cases"]
        self.bugs_cfg = self.cfg["bugs"]
        self.results_cfg = self.cfg["results"]
        self.plan_id = int(self.cases_cfg["plan_id"])
        self.project = self.cfg["project"]

    # =============================================================================================================
    # check
    # =============================================================================================================

    def process_base(self, project: dict) -> tuple[str, str]:
        """(the project's process, the system process it is or inherits from)."""
        tmpl = (project.get("capabilities") or {}).get("processTemplate") or {}
        name = str(tmpl.get("templateName") or "?")
        type_id = tmpl.get("templateTypeId")
        if not type_id:
            return name, name
        proc = self.api.process(type_id)
        base = proc.get("name") or name
        parent = proc.get("parentProcessTypeId")
        if parent and parent != ZERO:
            base = self.api.process(parent).get("name") or base
        return name, base

    def check(self) -> list[str]:
        api, problems = self.api, []
        project = api.project()
        self.ctx.out(f"Azure DevOps: project {project.get('name', self.project)} ({project.get('id', '?')})")
        name, base = self.process_base(project)
        self.ctx.out(f"Process: {name}" + (f" (inherited from {base})" if base != name else ""))
        if base.lower() == "basic":
            problems.append(BASIC)
        elif base.lower() not in ("agile", "scrum"):
            problems.append(f"the project uses the {base} process, not covered in version 1: the adapter needs Agile "
                            "or Scrum (or a process inherited from one of them)")
        types = api.work_item_types()
        case_type, bug_type = self.cases_cfg["work_item_type"], self.bugs_cfg["work_item_type"]
        for t in (case_type, bug_type):
            if t not in types:
                problems.append(f"work item type {t!r} is not in project {self.project!r}")
        known = api.fields()
        cases_map = dict(self.cases_cfg.get("fields") or {})
        if self.cases_cfg.get("id_source") == "field":
            cases_map["id (id_field)"] = self.cases_cfg["id_field"]
        for part, type_name, mapping in (("cases", case_type, cases_map),
                                         ("bugs", bug_type, self.bugs_cfg.get("fields") or {})):
            if type_name not in types:
                continue
            on_type = api.type_fields(type_name)
            for model, ref in mapping.items():
                if F.is_tag(ref):
                    continue
                if ref not in known:
                    problems.append(f"azure_devops.{part}.fields.{model}: field {ref!r} does not exist in this "
                                    "organisation")
                elif ref not in on_type:
                    problems.append(f"azure_devops.{part}.fields.{model}: field {ref!r} is not a field of "
                                    f"{type_name!r}")
        if bug_type in types:
            states = [str(s) for s in (api.type_fields(bug_type).get("System.State") or {}).get("allowedValues") or []]
            if states:
                self.ctx.out(f"{bug_type} states: {states}")
                for key in ("states", "closed_states"):
                    problems += [f"azure_devops.bugs.{key}: {s!r} is not a state of {bug_type!r} ({states})"
                                 for s in self.bugs_cfg.get(key) or [] if s not in states]
        try:
            plan = api.plan(self.plan_id)
            self.ctx.out(f"Test plan {self.plan_id}: {plan.get('name', '?')}")
            suites = {int(s["id"]): s for s in api.suites(self.plan_id)}
            for sid in self.cases_cfg.get("suite_ids") or []:
                if int(sid) not in suites:
                    problems.append(f"azure_devops.cases.suite_ids: suite {sid} is not in test plan {self.plan_id}")
            new_suite = self.cases_cfg.get("new_cases_suite_id")
            if new_suite:
                s = suites.get(int(new_suite))
                if s is None:
                    problems.append(f"azure_devops.cases.new_cases_suite_id: suite {new_suite} is not in test plan "
                                    f"{self.plan_id}")
                elif str(s.get("suiteType", "")).lower() != "statictestsuite":
                    problems.append(f"azure_devops.cases.new_cases_suite_id: suite {new_suite} is a "
                                    f"{s.get('suiteType')}, not a static suite (cases cannot be added to it)")
        except HttpError as e:
            if e.status != 404:
                raise
            problems.append(f"azure_devops.cases.plan_id {self.plan_id}: test plan not found")
        wanted = self.results_cfg.get("configurations") or {}
        if wanted:
            names = [c.get("name") for c in api.configurations()]
            problems += [f"azure_devops.results.configurations.{platform}: {conf!r} is not a Test Configuration of "
                         f"the project ({names})" for platform, conf in wanted.items() if conf not in names]
        return problems

    # =============================================================================================================
    # shared resolution
    # =============================================================================================================

    def _wiql(self, where: str) -> list[int]:
        return self.api.wiql(f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND {where} "
                             "ORDER BY [System.Id]")

    def _type_clause(self, type_name: str) -> str:
        return f"[System.WorkItemType] = {wiql_string(type_name)}"

    def _internal_id(self, wi: dict) -> tuple[str | None, str | None]:
        """(the case's internal ID, a problem) per cases.id_source (technique section 3)."""
        source = self.cases_cfg.get("id_source") or "id"
        f = wi.get("fields") or {}
        if source == "id":
            return str(wi["id"]), None
        if source == "field":
            value = f.get(self.cases_cfg["id_field"])
            return (str(value).strip() or None) if value not in (None, "") else None, None
        prefix = self.cases_cfg["id_tag_prefix"]
        ids = [t[len(prefix):] for t in F.split_tags(f.get(F.TAGS)) if t.startswith(prefix)]
        if len(ids) > 1:
            return ids[0], f"work item {wi['id']} has several ID tags {ids}"
        return (ids[0] if ids else None), None

    def _id_fields(self, *extra) -> list[str]:
        out = ["System.Id", "System.WorkItemType", "System.Title", F.TAGS, *extra]
        if self.cases_cfg.get("id_source") == "field":
            out.append(self.cases_cfg["id_field"])
        return list(dict.fromkeys(out))

    def _find_by_ids(self, ids: list[str]) -> list[dict]:
        """The Test Case work items whose internal ID is one of `ids` (field/tag): WIQL, then their fields."""
        source = self.cases_cfg.get("id_source")
        type_clause = self._type_clause(self.cases_cfg["work_item_type"])
        found: list[int] = []
        for chunk in chunks(ids, ID_CHUNK):
            if source == "field":
                ref = self.cases_cfg["id_field"]
                where = f"[{ref}] IN ({', '.join(wiql_string(i) for i in chunk)})"
            else:
                prefix = self.cases_cfg["id_tag_prefix"]
                where = "(" + " OR ".join(f"[System.Tags] CONTAINS {wiql_string(prefix + i)}" for i in chunk) + ")"
            found += self._wiql(f"{type_clause} AND {where}")
        return list(self.api.batch(sorted(set(found)), self._id_fields("System.AreaPath")).values())

    def resolve_cases(self, ids: list[str], cache: IdCache) -> tuple[dict, list[str]]:
        """internal ID -> {"id", "area"} confirmed in Azure DevOps (section 3); (the map, notes). Conflict (exit 2)
        for a case not found, a case mapped to two work items, an ID the cache has pointing elsewhere, or a work item
        that is not a Test Case."""
        type_name = self.cases_cfg["work_item_type"]
        problems, found = [], {}
        if (self.cases_cfg.get("id_source") or "id") == "id":
            bad = [i for i in ids if _int(i) is None]
            problems += [f"{i}: not a work item id (cases.id_source is id)" for i in bad]
            wis = self.api.batch([_int(i) for i in ids if _int(i) is not None], self._id_fields("System.AreaPath"))
            for i in ids:
                if _int(i) in wis:
                    found[i] = [wis[_int(i)]]
        else:
            for wi in self._find_by_ids(ids):
                cid, problem = self._internal_id(wi)
                if problem:
                    problems.append(problem)
                if cid in ids:
                    found.setdefault(cid, []).append(wi)
        out = {}
        for cid in ids:
            wis = found.get(cid) or []
            if not wis:
                if _int(cid) is not None or (self.cases_cfg.get("id_source") or "id") != "id":
                    problems.append(f"{cid}: no {type_name} with this ID in Azure DevOps")
                continue
            if len(wis) > 1:
                problems.append(f"{cid}: mapped to {len(wis)} work items {[w['id'] for w in wis]}")
                continue
            wi = wis[0]
            wtype = (wi.get("fields") or {}).get("System.WorkItemType")
            if str(wtype).lower() != type_name.lower():
                problems.append(f"{cid}: work item {wi['id']} is a {wtype}, not a {type_name}")
                continue
            cached = cache.get(cid)
            if cached and cached.get("id") and int(cached["id"]) != int(wi["id"]):
                problems.append(f"{cid}: tracker/ids.yaml has work item {cached['id']}, Azure DevOps has {wi['id']} — "
                                "the ID now points elsewhere")
                continue
            out[cid] = {"id": int(wi["id"]), "area": (wi.get("fields") or {}).get("System.AreaPath")}
        if problems:
            raise Conflict(f"{len(problems)} case(s) do not match Azure DevOps: nothing sent", problems)
        return out, []

    def _suite_ids(self) -> list[int]:
        return [int(s) for s in self.cases_cfg.get("suite_ids") or []] or \
            [int(s["id"]) for s in self.api.suites(self.plan_id)]

    def case_points(self, wids: list[int]) -> dict[int, list[dict]]:
        """work item id -> its test points in the plan's suites: [{point, suite, configuration, outcome}]."""
        out: dict[int, list[dict]] = {}
        for sid in self._suite_ids():
            for chunk in chunks(sorted(set(wids)), 100):
                for p in self.api.points(self.plan_id, sid, chunk):
                    wid = int((p.get("testCaseReference") or {}).get("id") or 0)
                    if wid in chunk:
                        out.setdefault(wid, []).append({
                            "point": int(p["id"]), "suite": sid,
                            "configuration": (p.get("configuration") or {}).get("name"),
                            "outcome": (p.get("results") or {}).get("outcome")})
        return out

    def _platform(self, item, imported: dict) -> str | None:
        value = item.data.get("platform")
        if value in (None, ""):
            value = (imported.get(item.tc) or {}).get("platform")
        return None if value in (None, "") else str(value)

    def resolve_points(self, items, cases: dict) -> dict:
        """tc -> {"point", "suite", "configuration"}: the test point of each item (section 3). Conflict (exit 2) for
        a case not in the plan, several points without a platform mapped to one configuration, or the same
        configuration in two suites."""
        by_case = self.case_points([c["id"] for c in cases.values()])
        imported = {str(c.get("id")): c for c in read_cases(self.ctx.workspace).get("cases") or []}
        table = self.results_cfg.get("configurations") or {}
        out, problems = {}, []
        for item in items:
            wid = cases[item.tc]["id"]
            points = by_case.get(wid) or []
            if not points:
                problems.append(f"{item.tc}: work item {wid} is not in test plan {self.plan_id} (no test point)")
                continue
            platform = self._platform(item, imported)
            if platform is not None and platform in table:
                chosen = [p for p in points if p["configuration"] == table[platform]]
                if not chosen:
                    problems.append(f"{item.tc}: no test point with the configuration {table[platform]!r} (platform "
                                    f"{platform}); points: {sorted({p['configuration'] for p in points})}")
                    continue
            elif len(points) == 1:
                chosen = points
            else:
                problems.append(f"{item.tc}: {len(points)} test points ({sorted({p['configuration'] for p in points})})"
                                + (f" and platform {platform!r} is not in azure_devops.results.configurations"
                                   if platform else ": give the item's platform, mapped in "
                                   "azure_devops.results.configurations"))
                continue
            if len(chosen) > 1:
                problems.append(f"{item.tc}: the configuration {chosen[0]['configuration']!r} is in {len(chosen)} suites "
                                f"{[p['suite'] for p in chosen]}: which one is ambiguous")
                continue
            out[item.tc] = chosen[0]
        if problems:
            raise Conflict(f"{len(problems)} case(s) without a single test point: nothing sent", problems)
        return out

    def _marker_items(self, marker: str, type_name: str, fields: list[str]) -> list[dict]:
        ids = self._wiql(f"{self._type_clause(type_name)} AND [System.Tags] CONTAINS {wiql_string(marker)}")
        items = self.api.batch(ids, fields) if ids else {}
        return [wi for wi in items.values() if marker in F.split_tags((wi.get("fields") or {}).get(F.TAGS))]

    def _redact(self, text, what: str, notes: list) -> str:
        text = "" if text is None else str(text)
        red = self.ctx.redactor.text(text)
        if red != text:
            notes.append(f"{what}: redacted (a credential's value was replaced by its name)")
        return red

    # -- field formatting (JSON Patch operations) ------------------------------------------------------------------

    def _area_ok(self, value) -> bool:
        text = str(value)
        return text == self.project or text.startswith(self.project + "\\")

    def _put(self, ops: list, tags: list, meta: dict, on_type: dict, ref: str, value, what: str,
             problems: list, notes: list, type_name: str) -> None:
        if value in (None, "", []):
            return
        if F.is_tag(ref):
            for v in (value if isinstance(value, list) else [value]):
                tag, changed = F.tag_value(F.tag_prefix(ref), v)
                if changed:
                    notes.append(f"{what}: {v!r} written as the tag {tag!r} (\";\" separates tags)")
                tags.append(tag)
            return
        if ref not in on_type:
            problems.append(f"{what}: field {ref!r} is not a field of {type_name!r}")
            return
        if ref in ("System.AreaPath", "System.IterationPath") and not self._area_ok(value):
            problems.append(f"{what}: {ref} {value!r} is not a path of project {self.project!r} (e.g. "
                            f"{self.project}\\Checkout)")
            return
        if any(op["path"] == f"/fields/{ref}" for op in ops):
            return
        formatted, problem = F.format_value(ref, meta.get(ref), value, (on_type[ref] or {}).get("allowedValues"))
        if problem:
            problems.append(f"{what}: {problem}")
            return
        ops.append({"op": "add", "path": f"/fields/{ref}", "value": formatted})

    def _required(self, ops: list, on_type: dict, type_name: str, what: str) -> list[str]:
        given = {op["path"][len("/fields/"):] for op in ops if op["path"].startswith("/fields/")}
        return [f"{what}: the required field {m.get('name', ref)!r} ({ref}) of {type_name!r} has no value (map it in "
                "project.yaml or add it to the defaults)" for ref, m in on_type.items()
                if m.get("alwaysRequired") and m.get("defaultValue") in (None, "") and ref not in given
                and ref not in F.AUTOMATIC]

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

    def _bug_ops(self, batch, item, case: dict, meta: dict, on_type: dict, problems: list, notes: list) -> list:
        nd = item.data["new_defect"]
        mapping = dict(self.bugs_cfg.get("fields") or {})
        defaults = dict(self.bugs_cfg.get("defaults") or {})
        type_name = self.bugs_cfg["work_item_type"]
        what = f"{item.tc} (new bug)"
        ops: list[dict] = []
        tags = [batch_marker(batch.batch_id), case_marker(item.tc)]
        put = (lambda ref, value: self._put(ops, tags, meta, on_type, ref, value, what, problems, notes, type_name))
        put(mapping.pop("title", "System.Title"), self._redact(nd["title"], f"{what} title", notes))
        put(mapping.pop("description", "Microsoft.VSTS.TCM.ReproSteps"),
            self._redact(self._bug_description(batch, item), f"{what} description", notes))
        for model, ref in mapping.items():
            value = nd.get(model)
            if value in (None, "") and model == "environment":
                value = batch.run["environment"]
            if value in (None, ""):
                value = defaults.get(model)
            if isinstance(value, str):
                value = self._redact(value, f"{what} {model}", notes)
            put(ref, value)
        for ref, value in defaults.items():   # a default whose key is not a model field is a field reference name
            if ref not in (self.bugs_cfg.get("fields") or {}):
                put(ref, value)
        put("System.AreaPath", self.bugs_cfg.get("area_path") or case.get("area"))
        put("System.IterationPath", self.bugs_cfg.get("iteration_path"))
        ops.append({"op": "add", "path": f"/fields/{F.TAGS}", "value": F.join_tags(tags)})
        ops.append({"op": "add", "path": "/relations/-",
                    "value": {"rel": TESTED_BY, "url": self.api.work_item_url(case["id"]),
                              "attributes": {"comment": f"{batch_marker(batch.batch_id)} · {item.tc}"}}})
        problems += self._required(ops, on_type, type_name, what)
        return ops

    def _evidence(self, item, plan: Plan) -> list[str]:
        files = item.data.get("evidence")
        files = [files] if isinstance(files, str) else list(files or [])
        send, skipped = split_evidence(self.ctx.workspace, files, self.config)
        plan.not_sent += [f"{item.tc} (evidence) {s}" for s in skipped]
        return send

    def plan_apply(self, batch, record):
        api, plan = self.api, Plan()
        cache = IdCache(self.ctx.workspace, "azure-devops")
        tcs = [i.tc for i in batch.to_send]
        cases, notes = self.resolve_cases(tcs, cache) if tcs else ({}, [])
        plan.notes += notes
        points = self.resolve_points(batch.to_send, cases) if tcs else {}
        # existing bugs of the batch (linked to the results, commented, their state changed)
        refs = sorted({str(i.data["bug"]).strip() for i in batch.items if i.data.get("bug") not in (None, "")})
        bad = [b for b in refs if _int(b) is None]
        if bad:
            raise Conflict(f"bug(s) of the batch that are not work item ids: {bad}: nothing sent")
        found = api.batch([int(b) for b in refs], ["System.Id", "System.WorkItemType", "System.State",
                                                   "System.Title"]) if refs else {}
        bugs = {str(k): {"id": k, "rev": v.get("rev"), "state": (v.get("fields") or {}).get("System.State"),
                         "title": (v.get("fields") or {}).get("System.Title")} for k, v in found.items()}
        missing = [b for b in refs if b not in bugs]
        if missing:
            raise Conflict(f"bug(s) of the batch not found in Azure DevOps: {missing}: nothing sent")
        marker = batch_marker(batch.batch_id)
        bug_type = self.bugs_cfg["work_item_type"]
        # new bugs: the marker search first (a create that timed out after Azure DevOps did it; a record lost)
        recorded_bugs = {w["case"]: w for w in record.find("bug")}
        new_items = [i for i in batch.to_send if i.data.get("new_defect") not in (None, {})]
        plan.not_sent += [f"{i.tc} ({i.status}): its new_defect is not created either (the item is not sent)"
                          for i in batch.not_sent if i.data.get("new_defect") not in (None, {})]
        pending = [i for i in new_items if i.tc not in recorded_bugs]
        marked = self._marker_items(marker, bug_type, ["System.Id", F.TAGS, "System.Title"]) if pending else []
        new_bugs, reused, problems = {}, {}, []
        for item in pending:
            hits = [wi for wi in marked if case_marker(item.tc) in F.split_tags(wi["fields"].get(F.TAGS))]
            if hits:
                reused[item.tc] = int(hits[0]["id"])
                plan.notes.append(f"{item.tc}: bug {hits[0]['id']} found by its markers (an earlier run created it): "
                                  "reused, not created again")
                continue
            meta, on_type = api.fields(), api.type_fields(bug_type)
            new_bugs[item.tc] = self._bug_ops(batch, item, cases[item.tc], meta, on_type, problems, plan.notes)
            plan.add("new bugs", "POST", f"/_apis/wit/workitems/${bug_type}",
                     f"{item.tc}: {bug_type} \"{item.data['new_defect']['title']}\" (tags {marker}, "
                     f"{case_marker(item.tc)}; Tested By {cases[item.tc]['id']})", item.tc)
        if problems:
            raise ConfigError(f"{len(problems)} problem(s) with the new bugs' fields: nothing sent", problems)
        # bug_status: the state change goes right after the new bugs, with the `test` on /rev read above (linking a
        # bug to a test result and commenting add revisions to it)
        states = {}
        allowed = None
        for item in batch.items:
            want = item.data.get("bug_status")
            if want in (None, "") or record.find("state", item.tc):
                continue
            bug = bugs[str(item.data["bug"]).strip()]
            if str(bug["state"]).lower() == str(want).lower():
                plan.notes.append(f"{item.tc}: {bug['id']} is already {want}: no state change")
                continue
            if allowed is None:
                allowed = [str(s) for s in (api.type_fields(bug_type).get("System.State") or {})
                           .get("allowedValues") or []]
            if allowed and want not in allowed:
                raise Conflict(f"{item.tc}: {want!r} is not a state of {bug_type!r} ({allowed}): nothing sent")
            states[item.tc] = {"bug": bug["id"], "rev": bug["rev"], "state": want}
            plan.add("updated bugs", "PATCH", f"/_apis/wit/workitems/{bug['id']}",
                     f"{bug['id']}: {bug['state']} -> {want} (test /rev {bug['rev']})", item.tc)
        # results: one test run per batch
        run = None
        for w in record.find("run"):
            run = {"id": int(w["id"])}
        name = fill_template(self.results_cfg.get("run_name"), batch_id=batch.batch_id,
                             environment=batch.run["environment"], build=batch.run["build"])
        comment = f"{marker} · environment {batch.run['environment']} · build {batch.run['build']}"
        if run is None and batch.to_send and record.exists:
            hits = [r for r in api.runs(self.plan_id) if str(r.get("comment") or "").startswith(marker)]
            if len(hits) > 1:
                raise Conflict(f"{len(hits)} test runs of plan {self.plan_id} carry {marker}: "
                               f"{[r.get('id') for r in hits]}")
            if hits:
                run = {"id": int(hits[0]["id"]), "reused": True}
                plan.notes.append(f"test run {run['id']} found by its marker {marker} (an earlier run created it): "
                                  "reused")
        done_chunks = {w.get("chunk") for w in record.find("results")}
        groups = chunks(batch.to_send, RESULTS_CHUNK)
        evidence = {}
        if batch.to_send:
            if run is None:
                plan.add("results", "POST", "/_apis/test/runs",
                         f"new test run \"{name}\" (plan {self.plan_id}, {len(batch.to_send)} point(s), comment "
                         f"\"{comment}\")")
            for n, group in enumerate(groups, 1):
                if n in done_chunks:
                    continue
                desc = ", ".join(f"{i.tc} (point {points[i.tc]['point']}) {i.target}"
                                 + (f" [bug {i.data['bug']}]" if i.data.get("bug") else "")
                                 + (" [bug: the new one]" if i.tc in new_bugs or i.tc in reused
                                    or i.tc in recorded_bugs else "") for i in group)
                plan.add("results", "PATCH", "/_apis/test/Runs/<run>/results",
                         f"chunk {n}/{len(groups)}: {desc}")
            for item in batch.to_send:
                files = [f for f in self._evidence(item, plan)
                         if not any(w.get("file") == os.path.basename(f) for w in record.find("attachment", item.tc))]
                if files:
                    evidence[item.tc] = files
                    for f in files:
                        plan.add("results", "POST", "/_apis/test/Runs/<run>/Results/<result>/attachments",
                                 f"{item.tc}: {os.path.basename(f)}", item.tc)
            if not record.find("run_completed"):
                plan.add("results", "PATCH", "/_apis/test/runs/<run>", "state Completed")
        # existing bugs: a comment on each
        comments = []
        for item in batch.to_send:
            ref = item.data.get("bug")
            if ref in (None, "") or record.find("comment", item.tc):
                continue
            tag = f"{marker} · {item.tc}"
            if record.exists and any(tag in plain(c.get("text")) for c in api.comments(int(ref))):
                plan.notes.append(f"{item.tc}: the comment on {ref} is already there (an earlier run): not repeated")
                continue
            comments.append(item.tc)
            plan.add("updated bugs", "POST", f"/_apis/wit/workItems/{ref}/comments",
                     f"{ref}: comment \"{tag}: {item.status}\" with the actual result", item.tc)
        plan.data = {"cases": cases, "points": points, "bugs": bugs, "new_bugs": new_bugs, "reused": reused,
                     "states": states, "run": run, "name": name, "comment": comment, "groups": len(groups),
                     "evidence": evidence, "comments": comments}
        return plan

    def send_apply(self, batch, plan, record):
        d, api = plan.data, self.api
        cache = IdCache(self.ctx.workspace, "azure-devops")
        for tc, ref in d["cases"].items():
            prev = dict((cache.get(tc) or {}).get("points") or {})
            point = d["points"][tc]
            prev[point["configuration"]] = point["point"]
            cache.set(tc, id=ref["id"], points=prev)
        if d["cases"]:
            cache.save()
        # 1. new bugs (their ids go into the results' associated bugs)
        for tc, wid in d["reused"].items():
            record.add("bug", tc, id=wid, reused=True)
        for tc, ops in d["new_bugs"].items():
            created = api.create_work_item(self.bugs_cfg["work_item_type"], ops)
            if not created.get("id"):
                raise UncertainWrite(f"{tc}: the new bug's answer has no id; the next run searches the marker first")
            record.add("bug", tc, id=int(created["id"]))
        new_bug = {w["case"]: int(w["id"]) for w in record.find("bug")}
        # 2. bug_status (the rev read at the resolution: a concurrent edit is a 412, exit 2)
        for tc, st in d["states"].items():
            api.update_work_item(st["bug"], [{"op": "test", "path": "/rev", "value": st["rev"]},
                                             {"op": "add", "path": "/fields/System.State", "value": st["state"]}])
            record.add("state", tc, bug=st["bug"], state=st["state"])
        if not batch.to_send:
            self._comments(batch, d, record)
            return
        # 3. the test run, on the points of the items sent
        run = d["run"]
        if run is None:
            body = {"name": d["name"], "plan": {"id": str(self.plan_id)},
                    "pointIds": [d["points"][i.tc]["point"] for i in batch.to_send], "automated": False,
                    "comment": d["comment"]}
            created = api.create_run(body)
            if not created.get("id"):
                raise UncertainWrite("the new test run's answer has no id; the next run searches the marker first")
            run = {"id": int(created["id"])}
            record.add("run", id=run["id"], name=d["name"])
        elif run.get("reused"):
            record.add("run", id=run["id"], reused=True)
        run_id = run["id"]
        # 4. the results: outcome, comment (the actual result, redacted), the bugs
        result_ids = {}
        for w in record.find("results"):
            result_ids.update(w.get("results") or {})
        done = {w.get("chunk") for w in record.find("results")}
        if len(done) < d["groups"] or d["evidence"]:
            missing = [i.tc for i in batch.to_send if i.tc not in result_ids]
            if missing:
                by_point = {int((r.get("testPoint") or {}).get("id") or 0): int(r["id"]) for r in api.results(run_id)}
                for tc in missing:
                    rid = by_point.get(d["points"][tc]["point"])
                    if rid is None:
                        raise Conflict(f"{tc}: test run {run_id} has no result for point {d['points'][tc]['point']}")
                    result_ids[tc] = rid
        for n, group in enumerate(chunks(batch.to_send, RESULTS_CHUNK), 1):
            if n in done:
                continue
            payload = []
            for item in group:
                entry = {"id": result_ids[item.tc], "outcome": item.target, "state": "Completed"}
                text = self._redact(item.data.get("actual_result"), item.tc, [])
                if text:
                    entry["comment"] = text[:COMMENT_MAX]
                linked = [str(item.data["bug"]).strip()] if item.data.get("bug") not in (None, "") else []
                if item.tc in new_bug:
                    linked.append(str(new_bug[item.tc]))
                if linked:
                    entry["associatedBugs"] = [{"id": b} for b in linked]
                payload.append(entry)
            api.update_results(run_id, payload)
            record.add("results", chunk=n, run=run_id, results={i.tc: result_ids[i.tc] for i in group})
        # 5. evidence (attach_evidence with the project's policy): on the test result, not on the bug
        for tc, files in d["evidence"].items():
            rid = result_ids[tc]
            # a run from an earlier send may have them already (an upload without an answer): looked up first
            existing = {a.get("fileName") or a.get("name") for a in api.attachments(run_id, rid)} \
                if d["run"] is not None else set()
            for path in files:
                fname = os.path.basename(path)
                if fname in existing:
                    record.add("attachment", tc, result=rid, file=fname, reused=True)
                    continue
                with open(path, "rb") as f:
                    data = f.read()
                if fname.lower().endswith((".txt", ".log", ".json", ".md", ".csv", ".xml", ".html")):
                    data = self.ctx.redactor.text(data.decode("utf-8", errors="replace")).encode("utf-8")
                created = api.add_attachment(run_id, rid, {"stream": base64.b64encode(data).decode("ascii"),
                                                           "fileName": fname, "comment": batch_marker(batch.batch_id),
                                                           "attachmentType": "GeneralAttachment"})
                record.add("attachment", tc, result=rid, file=fname, id=created.get("id"))
        # 6. the run completed
        if not record.find("run_completed"):
            api.update_run(run_id, {"state": "Completed"})
            record.add("run_completed", run=run_id)
        # 7. comments on the existing bugs
        self._comments(batch, d, record)

    def _comments(self, batch, d, record):
        marker = batch_marker(batch.batch_id)
        items = {i.tc: i for i in batch.items}
        for tc in d["comments"]:
            item = items[tc]
            ref = int(item.data["bug"])
            text = (f"{marker} · {tc}: {item.status} · environment {batch.run['environment']} · build "
                    f"{batch.run['build']}\n{self._redact(item.data.get('actual_result'), tc, [])}").strip()
            created = self.api.add_comment(ref, to_html(text))
            record.add("comment", tc, bug=ref, id=created.get("id"))

    def read_back(self, batch, plan, record):
        """The run's results (outcome and bugs) and the points' latest outcome against what was sent (section 4.2
        step 5)."""
        runs = record.find("run")
        if not runs or not batch.to_send:
            return []
        run_id = int(runs[-1]["id"])
        result_ids = {}
        for w in record.find("results"):
            result_ids.update(w.get("results") or {})
        results = {int(r["id"]): r for r in self.api.results(run_id, work_items=True)}
        new_bug = {w["case"]: int(w["id"]) for w in record.find("bug")}
        differences = []
        for item in batch.to_send:
            rid = result_ids.get(item.tc)
            r = results.get(int(rid)) if rid is not None else None
            if r is None:
                differences.append(f"{item.tc}: no result in test run {run_id}")
                continue
            got = r.get("outcome")
            if str(got).lower() != str(item.target).lower():
                differences.append(f"{item.tc} (result {rid}): {item.target} sent, {got} read back")
            linked = {str(b.get("id")) for b in r.get("associatedBugs") or []}
            wanted = [str(item.data["bug"]).strip()] if item.data.get("bug") not in (None, "") else []
            if item.tc in new_bug:
                wanted.append(str(new_bug[item.tc]))
            differences += [f"{item.tc} (result {rid}): bug {b} not linked to the result" for b in wanted
                            if b not in linked]
        points = plan.data["points"]
        by_suite: dict[int, list[int]] = {}
        for item in batch.to_send:
            by_suite.setdefault(points[item.tc]["suite"], []).append(plan.data["cases"][item.tc]["id"])
        latest = {}
        for sid, wids in sorted(by_suite.items()):
            for p in self.api.points(self.plan_id, sid, sorted(set(wids))):
                latest[int(p["id"])] = (p.get("results") or {}).get("outcome")
        for item in batch.to_send:
            got = latest.get(points[item.tc]["point"])
            if str(got).lower() != str(item.target).lower():
                differences.append(f"{item.tc} (point {points[item.tc]['point']}): latest outcome {got}, "
                                   f"{item.target} sent")
        return differences

    # =============================================================================================================
    # bug-search (section 5.1)
    # =============================================================================================================

    def bug_search(self, draft: dict, include_closed: bool) -> list[dict]:
        mapping = self.bugs_cfg.get("fields") or {}
        base = self._type_clause(self.bugs_cfg["work_item_type"])
        closed = self.bugs_cfg.get("closed_states") or []
        if closed and not include_closed:
            base += f" AND [System.State] NOT IN ({', '.join(wiql_string(s) for s in closed)})"
        criteria = []   # (name, WIQL clause, check on a candidate)
        for model in ("area", "feature", "sub_area", "platform", "role"):
            value, ref = draft.get(model), mapping.get(model)
            if value in (None, "") or not ref:
                continue
            if F.is_tag(ref):
                tag, _ = F.tag_value(F.tag_prefix(ref), value)
                criteria.append((model, f"[System.Tags] CONTAINS {wiql_string(tag)}", ("tag", tag)))
            elif ref in ("System.AreaPath", "System.IterationPath"):
                criteria.append((model, f"[{ref}] UNDER {wiql_string(value)}", ("under", ref, str(value))))
            else:
                criteria.append((model, f"[{ref}] = {wiql_string(value)}", ("eq", ref, str(value))))
        words = significant_words(draft.get("observed"), draft.get("title"))
        text_ref = mapping.get("description") or "Microsoft.VSTS.TCM.ReproSteps"
        if words:
            phrase = wiql_string(" ".join(words))
            clause = f"([System.Title] CONTAINS WORDS {phrase} OR [{text_ref}] CONTAINS WORDS {phrase})"
            criteria.append(("text", clause, ("text", words)))
        wanted = ["System.Id", "System.Title", "System.State", F.TAGS] + sorted(
            {c[2][1] for c in criteria if c[2][0] in ("eq", "under")})
        passes = [criteria]
        for drop in ("role", "platform", "sub_area", "text"):
            nxt = [c for c in passes[-1] if c[0] != drop]
            if len(nxt) != len(passes[-1]):
                passes.append(nxt)
        for n, crit in enumerate(passes, 1):
            ids = self._wiql(" AND ".join([base] + [c[1] for c in crit]))[:50]
            if not ids:
                continue
            items = self.api.batch(ids, list(dict.fromkeys(wanted)))
            out = []
            for wid in ids:
                f = (items.get(wid) or {}).get("fields") or {}
                matched = [c[0] for c in crit] + [c[0] for c in criteria if c not in crit and self._matches(f, c[2])]
                out.append({"key": str(wid), "summary": f.get("System.Title"), "status": f.get("System.State"),
                            "matched": [c[0] for c in criteria if c[0] in matched], "pass": n})
            out.sort(key=lambda c: (-len(c["matched"]), int(c["key"])))
            return out
        return []

    @staticmethod
    def _matches(fields: dict, check) -> bool:
        kind = check[0]
        if kind == "tag":
            return check[1].lower() in [t.lower() for t in F.split_tags(fields.get(F.TAGS))]
        if kind == "text":
            text = str(fields.get("System.Title") or "").lower()
            return all(w in text for w in check[1])
        value = str(fields.get(check[1]) or "")
        if kind == "under":
            return value.lower() == check[2].lower() or value.lower().startswith(check[2].lower() + "\\")
        return value.lower() == check[2].lower()

    # =============================================================================================================
    # import-cases (section 6)
    # =============================================================================================================

    def import_cases(self, query):
        api = self.api
        mapping = self.cases_cfg.get("fields") or {}
        type_name = self.cases_cfg["work_item_type"]
        in_plan: dict[int, list[dict]] = {}
        if query:
            ids = api.wiql(query)
        else:
            ids = []
            for sid in self._suite_ids():
                for tc in api.suite_cases(self.plan_id, sid):
                    wid = int((tc.get("workItem") or {}).get("id") or 0)
                    if wid and wid not in ids:
                        ids.append(wid)
        wanted = self._id_fields(STEPS, PARAMETERS, *sorted({v for v in mapping.values() if not F.is_tag(v)}))
        items = api.batch(ids, wanted)
        meta = api.fields()
        # shared steps: expanded to text from their own Steps
        refs, parsed = set(), {}
        for wid, wi in items.items():
            steps, simplified = S.parse((wi.get("fields") or {}).get(STEPS))
            parsed[wid] = (steps, simplified)
            refs |= {s["shared"] for s in steps if "shared" in s}
        shared = {k: S.parse((v.get("fields") or {}).get(STEPS))[0]
                  for k, v in (api.batch(sorted(refs), ["System.Id", STEPS]) if refs else {}).items()}
        if ids:
            in_plan = self.case_points(list(items))
        back: dict[str, str] = {}
        for status, target in self.config.status_map.items():
            if target not in (None, "") and str(target).lower() != "skip" and not is_question(status):
                key = str(target).lower()
                back[key + "#dup" if key in back else key] = status
        table = self.results_cfg.get("configurations") or {}
        cases, flags, seen, points_of = [], [], {}, {}
        for wid in sorted(items):
            wi = items[wid]
            f = wi.get("fields") or {}
            if str(f.get("System.WorkItemType", type_name)).lower() != type_name.lower():
                flags.append({"case": str(wid), "flag": f"a {f.get('System.WorkItemType')}, not a {type_name}: not "
                                                        "imported"})
                continue
            cid, problem = self._internal_id(wi)
            if problem:
                flags.append({"case": str(wid), "flag": problem})
            if not cid:
                flags.append({"case": str(wid), "flag": f"no internal ID ({self.cases_cfg.get('id_source')}): not "
                                                        "imported"})
                continue
            seen.setdefault(cid, []).append(wid)
            case = {"id": cid, "key": str(wid), "title": f.get("System.Title")}
            simplified = False
            for model in CASE_KEYS + tuple(k for k in mapping if k not in CASE_KEYS and k != "title"):
                ref = mapping.get(model)
                if not ref:
                    case[model] = None
                elif F.is_tag(ref):
                    case[model] = F.read_tags(f.get(F.TAGS), F.tag_prefix(ref))
                else:
                    case[model], s = F.read_value(ref, f.get(ref), meta.get(ref))
                    simplified = simplified or s
            steps, s = parsed[wid]
            simplified = simplified or s
            expanded = []
            for step in steps:
                if "shared" in step:
                    expanded += shared.get(step["shared"]) or []
                    flags.append({"case": cid, "flag": f"shared_steps: work item {step['shared']} expanded to text "
                                                       "(never written back)"})
                else:
                    expanded.append(step)
            case["steps"] = expanded
            case["expected"] = expanded[-1]["expected"] if expanded else None
            if "<param" in str(f.get(PARAMETERS) or ""):
                flags.append({"case": cid, "flag": "parameters: the steps use parameters (not expanded)"})
            pts = in_plan.get(wid) or []
            points_of[cid] = {}
            for p in pts:
                if p["configuration"] in points_of[cid]:
                    flags.append({"case": cid, "flag": f"configuration {p['configuration']!r} in several suites of the "
                                                       "plan"})
                else:
                    points_of[cid][p["configuration"]] = p["point"]
            chosen = pts
            if len(pts) > 1 and case.get("platform") in table:
                chosen = [p for p in pts if p["configuration"] == table[case["platform"]]] or pts
            outcomes = {str(p.get("outcome") or "").lower() for p in chosen}
            outcomes -= {"", "none", "unspecified"}
            case["last_status"] = None
            if len(outcomes) > 1:
                flags.append({"case": cid, "flag": f"points with different outcomes {sorted(outcomes)}: last_status "
                                                   "left empty"})
            elif outcomes:
                outcome = outcomes.pop()
                case["last_status"] = back.get(outcome)
                if outcome + "#dup" in back:
                    flags.append({"case": cid, "flag": f"outcome {outcome} is mapped from several project statuses: "
                                                       f"imported as {case['last_status']}"})
                if case["last_status"] is None:
                    flags.append({"case": cid, "flag": f"outcome {outcome} is not in azure_devops.results.outcomes"})
            if query and not pts:
                flags.append({"case": cid, "flag": f"not in test plan {self.plan_id}: no test point"})
            if not expanded:
                flags.append({"case": cid, "flag": "no steps"})
            if len([s for s in expanded if str(s["expected"]).strip()]) > 1:
                flags.append({"case": cid, "flag": "several expected results (methodology 5.3)"})
            if simplified:
                flags.append({"case": cid, "flag": "rich_text_simplified"})
            cases.append(case)
        dup = {cid: wids for cid, wids in seen.items() if len(wids) > 1}
        if dup:
            raise Conflict("two work items with the same internal ID: nothing written",
                           [f"{c}: {w}" for c, w in dup.items()])
        cache = IdCache(self.ctx.workspace, "azure-devops")
        for case in cases:
            cache.set(case["id"], id=int(case["key"]), points=points_of.get(case["id"]) or None)
        cache.save()
        write_cases(self.ctx.workspace, "azure-devops", cases, flags, self.ctx.now)
        return {"cases": len(cases), "flags": [f"{x['case']}: {x['flag']}" for x in flags]}

    # =============================================================================================================
    # export-cases (section 7)
    # =============================================================================================================

    def plan_export(self, doc, record):
        api, plan = self.api, Plan()
        suite = self.cases_cfg.get("new_cases_suite_id")
        if not suite:
            raise ConfigError("azure_devops.cases.new_cases_suite_id is missing: the static suite where new cases go "
                              "(export-cases needs it)")
        source = self.cases_cfg.get("id_source") or "id"
        type_name = self.cases_cfg["work_item_type"]
        marker = batch_marker(record.batch_id)
        recorded = {w["case"]: w for w in record.find("case")}
        marked = self._marker_items(marker, type_name, ["System.Id", "System.Title", F.TAGS])
        marked_ids = {int(w["id"]) for w in marked}
        todo, reused = [], {}
        for case in doc["cases"]:
            cid = str(case["id"]).strip()
            if cid in recorded:
                continue
            hit = [w for w in marked if str(w["fields"].get("System.Title", "")).strip() == str(case["title"]).strip()]
            if hit:
                reused[cid] = int(hit[0]["id"])
                plan.notes.append(f"{cid}: work item {hit[0]['id']} found by its marker {marker} (an earlier run "
                                  "created it): reused, not created again")
            else:
                todo.append(case)
        conflicts = []
        if source != "id" and todo:
            ids = [str(c["id"]).strip() for c in todo]
            for wi in self._find_by_ids(ids):
                cid, _ = self._internal_id(wi)
                if cid in ids and int(wi["id"]) not in marked_ids:
                    conflicts.append(f"{cid}: already exists as work item {wi['id']}")
        type_clause = self._type_clause(type_name)
        for chunk in chunks([str(c["title"]).strip() for c in todo], 20):
            found = self._wiql(f"{type_clause} AND ("
                               + " OR ".join(f"[System.Title] = {wiql_string(t)}" for t in chunk) + ")")
            titles = {t.casefold() for t in chunk}
            for wid, wi in (api.batch(found, ["System.Id", "System.Title"]) if found else {}).items():
                title = str((wi.get("fields") or {}).get("System.Title", "")).strip()
                if title.casefold() in titles and wid not in marked_ids:
                    conflicts.append(f"\"{title}\": a {type_name} with the same title exists (work item {wid}) — a "
                                     "candidate duplicate (methodology 10): remove the case from the file, or change "
                                     "its title, and run again")
        if conflicts:
            raise Conflict(f"{len(conflicts)} case(s) already in Azure DevOps: nothing created", conflicts)
        creates, problems = {}, []
        if todo:
            meta, on_type = api.fields(), api.type_fields(type_name)
            for case in todo:
                cid = str(case["id"]).strip()
                creates[cid] = self._case_ops(case, record.batch_id, meta, on_type, problems, plan.notes)
                n = len([s for s in case.get("steps") or [] if isinstance(s, dict)])
                plan.add("cases", "POST", f"/_apis/wit/workitems/${type_name}",
                         f"{cid}: \"{case['title']}\" ({n} step(s), tag {marker})", cid)
        if problems:
            raise ConfigError(f"{len(problems)} problem(s) with the cases' fields: nothing created", problems)
        # added to the suite: the cases created, and those an earlier run created but did not add
        in_suite = set()
        waiting = [int(w["id"]) for w in recorded.values()] + list(reused.values())
        if waiting and not record.find("suite"):
            in_suite = {int((tc.get("workItem") or {}).get("id") or 0)
                        for tc in api.suite_cases(self.plan_id, int(suite), waiting)}
        pending_suite = [w for w in waiting if w not in in_suite]
        if todo or pending_suite:
            plan.add("cases", "POST", f"/_apis/testplan/Plans/{self.plan_id}/Suites/{suite}/TestCase",
                     f"add the {len(todo) + len(pending_suite)} new case(s) to suite {suite} (its configurations make "
                     "the test points)")
        plan.data = {"creates": creates, "reused": reused, "suite": int(suite), "in_suite": sorted(in_suite)}
        return plan

    def _case_ops(self, case, name, meta, on_type, problems, notes) -> list:
        mapping = self.cases_cfg.get("fields") or {}
        type_name = self.cases_cfg["work_item_type"]
        cid = str(case["id"]).strip()
        what = f"case {cid}"
        ops: list[dict] = []
        tags = [batch_marker(name)]
        put = (lambda ref, value: self._put(ops, tags, meta, on_type, ref, value, what, problems, notes, type_name))
        put("System.Title", self._redact(case["title"], f"{what} title", notes))
        source = self.cases_cfg.get("id_source") or "id"
        if source == "field":
            put(self.cases_cfg["id_field"], cid)
        elif source == "tag":
            tags.append(self.cases_cfg["id_tag_prefix"] + cid)
        for model, ref in mapping.items():
            value = case.get(model)
            if isinstance(value, str):
                value = self._redact(value, f"{what} {model}", notes)
            put(ref, value)
        put("System.AreaPath", self.cases_cfg.get("area_path"))
        steps = [{"action": self._redact(s.get("action"), what, notes), "data": self._redact(s.get("data"), what, notes),
                  "expected": self._redact(s.get("expected"), what, notes)}
                 for s in case.get("steps") or [] if isinstance(s, dict)]
        xml = S.to_xml(steps)
        if xml:
            ops.append({"op": "add", "path": f"/fields/{STEPS}", "value": xml})
        ops.append({"op": "add", "path": f"/fields/{F.TAGS}", "value": F.join_tags(tags)})
        problems += self._required(ops, on_type, type_name, what)
        return ops

    def send_export(self, doc, plan, record):
        d, api = plan.data, self.api
        source = self.cases_cfg.get("id_source") or "id"
        type_name = self.cases_cfg["work_item_type"]
        for cid, wid in d["reused"].items():
            record.add("case", cid, id=wid, reused=True)
        for cid, ops in d["creates"].items():
            created = api.create_work_item(type_name, ops)
            if not created.get("id"):
                raise UncertainWrite(f"{cid}: the new case's answer has no id; the next run searches the marker first")
            record.add("case", cid, id=int(created["id"]))
        cases = record.find("case")
        points: dict[int, dict] = {}
        if not record.find("suite"):
            add = [int(w["id"]) for w in cases if int(w["id"]) not in set(d["in_suite"])]
            if add:
                for tc in api.add_to_suite(self.plan_id, d["suite"], add):
                    wid = int((tc.get("workItem") or {}).get("id") or 0)
                    points[wid] = {a.get("configurationName"): a.get("id") for a in tc.get("pointAssignments") or []}
            record.add("suite", suite=d["suite"], cases=[int(w["id"]) for w in cases])
        cache = IdCache(self.ctx.workspace, "azure-devops")
        for w in cases:
            final = str(w["id"]) if source == "id" else w["case"]
            prev = (cache.get(final) or {}).get("points")
            cache.set(final, id=int(w["id"]), points=points.get(int(w["id"])) or prev or None)
        cache.save()
        for w in cases:
            final = str(w["id"]) if source == "id" else w["case"]
            self.ctx.out(f"  {w['case']} -> {w['id']}" + (f" (ID {final})" if final != str(w["id"]) else ""))


"""
Universal Excel adapter (technique-excel.md section 1).

Applies a batch of results (run log, methodology section 7.1) to an Excel
workbook used as the source of truth for test cases and defects:

    load (keeping formulas) -> apply batch -> regenerate the active defects
    view -> cross-check -> save only if clean (else .REVIEWED.xlsx)
    -> recalculate safely

No sheet, column or status name lives in this code: the whole layout comes
from the configuration (see example.yaml next to this file; fields documented
in from_dict()).

Typical use:

    from adapters.excel import ExcelAdapter, load_batch

    ad = ExcelAdapter.from_yaml("project.yaml", section="excel")
    ad.load()
    res = ad.apply_batch(load_batch("runs/batch_x.json"), label="[2026-09-23]")
    ad.regenerate_active_defects()
    out = ad.save_if_clean()
    if out["saved_original"]:
        ad.recalc()

Batch format: {"results": [item, ...]}, each item
    {"tc": <case id>, "status": <status>, "actual_result": <text>,
     "new_defect": null | {<key>: <value>, ...}}
The keys of "new_defect" are mapped to columns by the configuration
(defects.batch_fields); "related_tc" (by default the case itself),
"severity" and "priority" (by default the severity) have their own role.
"bug" (optional) links an existing defect to the case (a note only).
Other keys (ts, note, ...) are ignored. An invalid batch is refused whole
before anything is written (validate_batch, technique-excel.md section 4).
"""
from __future__ import annotations

import contextlib
import copy
import fnmatch
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime

import openpyxl
from openpyxl.formula.translate import Translator

from .recalc import recalc_safe

CONTROL_SHEET_DEFAULT = "_qa_batches"
WORKBOOK_ENV = "QA_WORKBOOK"   # replaces excel.file for one command (one workbook per platform)


def workbook_patterns(excel: dict) -> list[tuple[str, str, list[str]]]:
    """(name, file, batch patterns) of each extra workbook of `excel.workbooks` (technique-excel.md section 1.1)."""
    out = []
    for key, v in ((excel or {}).get("workbooks") or {}).items():
        if isinstance(v, dict) and v.get("file"):
            pats = v.get("batches") or []
            out.append((str(key), str(v["file"]), [pats] if isinstance(pats, str) else [str(p) for p in pats]))
    return out


def configured_workbook(excel: dict, batch_id: str | None) -> tuple[str, str]:
    """(file, where it comes from) of the workbook a batch belongs to by project.yaml alone: the first entry of
    `excel.workbooks` whose patterns match the batch's name, else `excel.file`."""
    for key, file, pats in workbook_patterns(excel):
        if batch_id and any(fnmatch.fnmatch(batch_id, p) for p in pats):
            return file, f"excel.workbooks.{key}"
    return str((excel or {}).get("file") or ""), "excel.file"


def workbook_for(excel: dict, batch_id: str | None, environ=None) -> tuple[str, str]:
    """(file, where it comes from) of the workbook to use for a batch: $QA_WORKBOOK when set (one command), else
    configured_workbook. The one rule for apply, run_log and the reports (audit 06.11.2026)."""
    environ = os.environ if environ is None else environ
    forced = str(environ.get(WORKBOOK_ENV, "")).strip()
    if forced:
        return forced, f"${WORKBOOK_ENV}"
    return configured_workbook(excel, batch_id)


class Route(tuple):
    """The workbook of a batch: (file, source, platform, problem). `problem` is None when the route is certain; any
    text means the batch must not be applied (nor its application read from one workbook only)."""
    __slots__ = ()

    def __new__(cls, file: str, source: str, platform: str, problem: str | None):
        return tuple.__new__(cls, (file, source, platform, problem))

    file = property(lambda s: s[0])
    source = property(lambda s: s[1])
    platform = property(lambda s: s[2])
    problem = property(lambda s: s[3])


def _name_platforms(proj: dict, batch_id: str) -> list[str]:
    """The platforms `report.platforms` gives to a batch name (all that match, in order, without repeats)."""
    report = proj.get("report") if isinstance(proj.get("report"), dict) else {}
    out = []
    for platform, patterns in ((report or {}).get("platforms") or {}).items():
        for pattern in [patterns] if isinstance(patterns, str) else patterns or []:
            if fnmatch.fnmatch(batch_id, str(pattern)) and str(platform).lower() not in out:
                out.append(str(platform).lower())
    return out


def route_workbook(proj: dict, batch_id: str, run: dict | None = None, environ=None, workspace: str = ".") -> Route:
    """The one rule that chooses the workbook of a batch (technique-excel.md section 1.1), with every reason to stop.

    Each `excel.workbooks` entry is the workbook of one platform: its `platform`, else its key. `excel.file` is the
    workbook of the platforms without an entry of their own. The batch's platform comes from three places that must
    agree: the run block's `platform`, the `report.platforms` pattern its name matches, and the `excel.workbooks`
    entry its name matches. The route stops (problem) when:
      - the name matches the patterns of more than one workbook, or of more than one platform;
      - two of the three places name different platforms (e.g. run.platform ios, name of an Android batch);
      - the batch is of a platform with its own workbook but the name does not match that workbook's patterns;
      - the project declares several workbooks and nothing says the batch's platform (the default workbook is never
        chosen by elimination).
    $QA_WORKBOOK replaces the file for one command only when it names the same workbook or the project declares one."""
    environ = os.environ if environ is None else environ
    excel = proj.get("excel") if isinstance(proj.get("excel"), dict) else {}
    entries = [(key, file, pats, str(((excel.get("workbooks") or {}).get(key) or {}).get("platform") or key).lower())
               for key, file, pats in workbook_patterns(excel)]
    default = str(excel.get("file") or "")
    declared = str((run or {}).get("platform") or "").strip().lower()
    by_name = _name_platforms(proj, batch_id)
    matched = [e for e in entries if any(fnmatch.fnmatch(batch_id, p) for p in e[2])]
    problem = None
    if len(matched) > 1:
        problem = (f"{batch_id} matches the patterns of {len(matched)} workbooks ("
                   + ", ".join(f"excel.workbooks.{k}: {', '.join(p)}" for k, _, p, _ in matched)
                   + "): fix the patterns in project.yaml or rename the batch")
    elif len(by_name) > 1:
        problem = (f"{batch_id} matches the report.platforms patterns of {' and '.join(by_name)}: fix the patterns "
                   "in project.yaml or rename the batch")
    said = {"run.platform": declared, "report.platforms": by_name[0] if by_name else ""}
    if matched:
        said[f"excel.workbooks.{matched[0][0]}"] = matched[0][3]
    said = {k: v for k, v in said.items() if v}
    platform = next(iter(said.values()), "")
    if not problem and len(set(said.values())) > 1:
        problem = (f"{batch_id}: the platforms do not agree ("
                   + ", ".join(f"{k}: {v}" for k, v in said.items())
                   + "): correct the run block's platform or rename the batch; nothing is chosen for you")
    if matched:
        file, source = matched[0][1], f"excel.workbooks.{matched[0][0]}"
    else:
        own = next((e for e in entries if e[3] == platform), None) if platform else None
        file, source = default, "excel.file"
        if own and not problem:
            problem = (f"{batch_id} is of platform {platform} (" + ", ".join(f"{k}" for k in said)
                       + f"), whose workbook is {own[1]} (excel.workbooks.{own[0]}), but its name matches none of "
                       f"{', '.join(own[2])}: rename the batch so the name and the platform say the same")
        elif entries and not platform and not problem:
            problem = (f"{batch_id}: no platform recognised (no run.platform, and the name matches no pattern of "
                       "excel.workbooks or report.platforms) while the project has several workbooks: declare "
                       "\"platform\" in the run block or rename the batch; the default workbook is not chosen by "
                       "elimination")
    forced = str(environ.get(WORKBOOK_ENV, "")).strip()
    if forced:
        def full(x):
            return os.path.normcase(os.path.abspath(os.path.join(workspace, x)))
        if entries and not problem and full(forced) != full(file):
            problem = (f"${WORKBOOK_ENV}={forced} but {batch_id} belongs to {file} ({source}): unset "
                       f"{WORKBOOK_ENV} (the workbook is chosen from project.yaml) or rename the batch")
        file, source = forced, f"${WORKBOOK_ENV}"
    return Route(file, source, platform or "", problem)


def workbook_files(proj: dict) -> list[str]:
    """Every workbook the project declares: excel.file, then each excel.workbooks entry (no repeats)."""
    excel = proj.get("excel") if isinstance(proj.get("excel"), dict) else {}
    out = [str(excel.get("file") or "")] + [file for _, file, _ in workbook_patterns(excel)]
    return [f for i, f in enumerate(out) if f and f not in out[:i]]


def workbook_conflict(excel: dict, batch_id: str, workspace: str, environ=None) -> str | None:
    """Why $QA_WORKBOOK cannot be used for this batch, or None: when project.yaml declares several workbooks and the
    batch belongs to another one, the override would write the results in the wrong workbook."""
    file, source = workbook_for(excel, batch_id, environ)
    if not source.startswith("$") or not workbook_patterns(excel):
        return None
    own, own_source = configured_workbook(excel, batch_id)
    same = os.path.normcase(os.path.abspath(os.path.join(workspace, file))) == \
        os.path.normcase(os.path.abspath(os.path.join(workspace, own)))
    return None if same else (f"${WORKBOOK_ENV}={file} but {batch_id} belongs to {own} ({own_source}): unset "
                              f"{WORKBOOK_ENV} (the workbook is chosen from project.yaml) or rename the batch")


_CONTROL_HEADERS = ["hash", "applied_at", "label", "items", "new_defects", "batch_id", "result_keys"]
# control sheets written before the translation (plan 8.11) keep their headers: they are workbook data
_LEGACY_CONTROL_HEADERS = {"aplicado_em": "applied_at", "etiqueta": "label", "itens": "items",
                           "defeitos_novos": "new_defects", "lote_id": "batch_id"}
REPORTED_BY_DEFAULT = "Automated QA"
# fields of the case model (methodology section 2) that add-cases can write (technique-excel.md section 4.1)
CASE_MODEL_FIELDS = ("title", "area", "feature", "preconditions", "steps", "expected", "role", "platform",
                     "priority", "technique", "origin", "pbi_id", "qa_task")


class ConfigError(ValueError):
    pass


class InvalidBatch(ValueError):
    """Batch refused before anything is written; .problems holds the list."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("invalid batch:\n - " + "\n - ".join(problems))


def read_batch(path: str) -> dict:
    """The whole batch file: {"results": [...], "translated_from": <hash>?, ...}."""
    with open(path, encoding="utf-8-sig") as f:  # -sig: accepts files saved with a BOM
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        hint = (" (a batch from before the translation: convert the workspace with scripts/migrate_workspace.py)"
                if isinstance(data, dict) and "resultados" in data else "")
        raise InvalidBatch([f"{path}: expected {{\"results\": [...]}} (technique-excel.md section 4){hint}"])
    return data


def load_batch(path: str) -> list:
    return read_batch(path)["results"]


def batch_hash(batch: list) -> str:
    """Fingerprint of the batch's content (independent of key order)."""
    canon = json.dumps(batch, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def result_key(item: dict) -> str:
    """The identity of ONE result (plan Q1, 10.11.2026): its case, status, observed text and time of execution. Evidence,
    bug links and the time it was recorded are not part of it (they can be added later), so a result copied unchanged into
    a partial batch has the same key as in the original, and the same result can be applied only once, from whichever
    batch it comes."""
    canon = json.dumps({"tc": str(item.get("tc", "")).strip(), "status": item.get("status"),
                        "actual_result": item.get("actual_result"),
                        "ts": item.get("ts") or ("unknown" if item.get("ts_unknown") else None)},
                       sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def execution_key(item: dict) -> str | None:
    """The identity of the EXECUTION a result records: the case and the time it was executed (`ts`). Correcting the status
    or the observed text of an execution that was already applied does not make it another execution: it keeps this key,
    and the apply asks for a revision link (`revision_of`) instead of taking it for a new run. None when the time is
    unknown (such a result is judged by `result_key` only)."""
    if not item.get("ts"):
        return None
    canon = json.dumps({"tc": str(item.get("tc", "")).strip(), "ts": str(item["ts"])}, sort_keys=True,
                       ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def result_entry(item: dict) -> str:
    """What the control sheet keeps of a result: `<result key>:<execution key or ->:<case>`. The case is kept so that the apply
    can tell, for a case, what it already applied (including an execution whose time was never known)."""
    return f"{result_key(item)}:{execution_key(item) or '-'}:{str(item.get('tc', '')).strip()}"


def _req(d: dict, key: str, where: str):
    if key not in d or d[key] in (None, "", [], {}):
        raise ConfigError(f"configuration: '{where}{key}' is missing")
    return d[key]


class ExcelAdapter:
    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def __init__(self, config: dict, base_dir: str | None = None):
        self.cfg = self._validate(config)
        self.base_dir = base_dir or os.getcwd()
        self.wb: openpyxl.Workbook | None = None
        # instant used in the dates written (label, report date, view summary, applied_at);
        # None = the clock. Fixed, the same sequence of batches always gives the same cells
        # (technique-excel.md section 1, "Date of the application")
        self.now: datetime | None = None

    def _now(self) -> datetime:
        return self.now or datetime.now()

    @classmethod
    def from_dict(cls, config: dict, base_dir: str | None = None) -> ExcelAdapter:
        """Fields (required unless stated):

        file: path of the workbook (relative to base_dir)
        cases:
          sheets: {id_prefix: sheet_name}
          header_row: int (optional, 1)
          columns: {id, status, actual_result (opt.), notes (opt.)}
                   (the optional ones are used only in the sheets that have them)
          statuses: [valid case statuses]
          fields: {model_field: column} (opt.; add-cases, title required there)
          new_status: status of a case added by add-cases (opt., the 1st status)
        defects:
          sheet, header_row (opt., 1)
          id_format: e.g. "BUG-{n}"
          columns: {id, status, case (opt.), severity (opt.), priority (opt.),
                    reported_by (opt.), report_date (opt.)}
          batch_fields: {defect_key_in_the_batch: column} (opt.)
          defaults: {defect_key_in_the_batch: value} (opt.)
          reported_by: value of the reporter column (opt., "Automated QA")
          statuses, closed_statuses, initial_status
          priorities: [order, highest first] (opt.)
        active_view (opt.):
          sheet, header_row (opt., 1)
          columns: {view_header: defects_column} (unmapped headers use the
                   column with the same name)
          sort_by: defects column used as the 2nd criterion (opt.)
          summary: {cell, template} (opt.; template with {open}, {closed},
                   {severity[<value>]}, {date:%d.%m.%Y})
        notes (opt.):
          result: template with {result} (used when there is no actual_result column)
          new_defect: template with {id}
          existing_defect: template with {id} (item with "bug")
        batch_control (opt.): {sheet} -- hidden sheet with the batches applied
        recalc (opt.): {mechanism: libreoffice | excel-com, timeout: int}
        """
        return cls(config, base_dir)

    @classmethod
    def from_yaml(cls, path: str, section: str | None = None, file: str | None = None) -> ExcelAdapter:
        import yaml

        with open(path, encoding="utf-8-sig") as f:
            data = yaml.safe_load(f) or {}
        if section:
            if section not in data:
                raise ConfigError(f"{path}: section '{section}' is missing")
            data = data[section]
        if file:
            data = {**data, "file": file}
        return cls(data, base_dir=os.path.dirname(os.path.abspath(path)))

    @classmethod
    def from_project(cls, workspace: str = ".", file: str | None = None, batch: str | None = None) -> ExcelAdapter:
        """Reads the `excel:` section of the workspace's project.yaml (plan
        task 1.3). The workbook path is relative to the workspace. `file`, else
        workbook_for (QA_WORKBOOK, then the `excel.workbooks` entry whose
        patterns match `batch`, then excel.file)."""
        path = os.path.join(workspace, "project.yaml")
        if not os.path.exists(path):
            old = os.path.join(workspace, "projeto.yaml")
            hint = (" (a workspace from before the translation: convert it with scripts/migrate_workspace.py)"
                    if os.path.exists(old) else "")
            raise ConfigError(f"{path} does not exist{hint}")
        if not file:
            import yaml

            with open(path, encoding="utf-8-sig") as f:
                excel = (yaml.safe_load(f) or {}).get("excel") or {}
            file = workbook_for(excel, batch)[0] or None
        return cls.from_yaml(path, section="excel", file=file)

    @staticmethod
    def _validate(cfg: dict) -> dict:
        cfg = copy.deepcopy(cfg)
        _req(cfg, "file", "")
        cases = _req(cfg, "cases", "")
        _req(cases, "sheets", "cases.")
        ccol = _req(cases, "columns", "cases.")
        _req(ccol, "id", "cases.columns.")
        _req(ccol, "status", "cases.columns.")
        _req(cases, "statuses", "cases.")
        cases.setdefault("header_row", 1)
        cases["fields"] = cases.get("fields") or {}
        unknown = [k for k in cases["fields"] if k not in CASE_MODEL_FIELDS]
        if unknown:
            raise ConfigError(f"configuration: cases.fields outside the case model {list(CASE_MODEL_FIELDS)}: {unknown}")
        cases["new_status"] = cases.get("new_status") or cases["statuses"][0]
        if cases["new_status"] not in cases["statuses"]:
            raise ConfigError(f"configuration: cases.new_status {cases['new_status']!r} outside cases.statuses")

        d = _req(cfg, "defects", "")
        _req(d, "sheet", "defects.")
        fmt = _req(d, "id_format", "defects.")
        if fmt.count("{n}") != 1:
            raise ConfigError("configuration: defects.id_format must contain '{n}' once")
        dcol = _req(d, "columns", "defects.")
        _req(dcol, "id", "defects.columns.")
        _req(dcol, "status", "defects.columns.")
        _req(d, "statuses", "defects.")
        _req(d, "initial_status", "defects.")
        d.setdefault("header_row", 1)
        for key in ("batch_fields", "defaults"):
            d[key] = d.get(key) or {}
        d["priorities"] = d.get("priorities") or []
        d["closed_statuses"] = d.get("closed_statuses") or []
        d["reported_by"] = d.get("reported_by") or REPORTED_BY_DEFAULT
        bad = [e for e in d["closed_statuses"] if e not in d["statuses"]]
        if bad:
            raise ConfigError(f"configuration: defects.closed_statuses outside defects.statuses: {bad}")
        if d["initial_status"] not in d["statuses"]:
            raise ConfigError("configuration: defects.initial_status outside defects.statuses")
        pre, _, post = fmt.partition("{n}")
        d["_id_re"] = re.compile("^" + re.escape(pre) + r"(\d+)" + re.escape(post) + "$")

        # optional blocks left blank (as in the template) count as absent
        v = cfg.get("active_view")
        if v and v.get("sheet"):
            v.setdefault("header_row", 1)
            v["columns"] = v.get("columns") or {}
            if not (v.get("summary") or {}).get("cell"):
                v["summary"] = None
        else:
            cfg["active_view"] = None

        r = cfg.get("summary")
        if r and r.get("sheet"):
            _req(r, "area_column", "summary.")
            r.setdefault("header_row", 1)
            r["status_columns"] = r.get("status_columns") or {}
            outside = [e for e in r["status_columns"] if e not in cases["statuses"]]
            if outside:
                raise ConfigError(f"configuration: summary.status_columns with statuses outside cases.statuses: "
                                  f"{outside}")
            if r.get("percent_column"):
                pend = _req(r, "not_run_status", "summary.")
                if pend not in cases["statuses"]:
                    raise ConfigError(f"configuration: summary.not_run_status {pend!r} outside cases.statuses")
                tol = r.get("percent_tolerance")
                r["percent_tolerance"] = 0.001 if tol in (None, "") else float(tol)
        else:
            cfg["summary"] = None

        defaults = {
            "notes": {"result": "Result: {result}", "new_defect": "New defect: {id}.",
                      "previous_result": "Previous result ({status}): {result}",
                      "existing_defect": "Linked defect: {id}."},
            "batch_control": {"sheet": CONTROL_SHEET_DEFAULT},
            "recalc": {"mechanism": "libreoffice", "timeout": 60},
        }
        for block, vals in defaults.items():
            cfg[block] = cfg.get(block) or {}
            for k, val in vals.items():
                if cfg[block].get(k) in (None, ""):
                    cfg[block][k] = val
        return cfg

    @property
    def path(self) -> str:
        return os.path.join(self.base_dir, self.cfg["file"])

    # ------------------------------------------------------------------
    # Loading / saving
    # ------------------------------------------------------------------

    def load(self, path: str | None = None) -> openpyxl.Workbook:
        """Loads keeping formulas (data_only=False) -- what is wanted for
        EDITING. Calculated values are read in a separate copy."""
        self.wb = openpyxl.load_workbook(path or self.path, data_only=False)
        self._check_layout()
        return self.wb

    def _need_wb(self):
        if self.wb is None:
            raise RuntimeError("workbook not loaded -- call load() first")
        return self.wb

    @staticmethod
    def _headers(ws, row: int) -> dict:
        return {c.value: c.column for c in ws[row] if c.value not in (None, "")}

    def _check_layout(self) -> None:
        """Fails early if the configuration does not match the real workbook."""
        wb = self.wb
        problems = []
        cases, d = self.cfg["cases"], self.cfg["defects"]
        field_cols = set(cases["fields"].values())
        for sheet in set(cases["sheets"].values()):
            if sheet not in wb.sheetnames:
                problems.append(f"case sheet missing: '{sheet}'")
                continue
            h = self._headers(wb[sheet], cases["header_row"])
            field_cols -= set(h)
            # actual_result / notes are optional sheet by sheet
            for role in ("id", "status"):
                col = cases["columns"][role]
                if col not in h:
                    problems.append(f"'{sheet}': column '{col}' ({role}) missing")
        # a field's column may be missing from some sheets, never from all (a typo)
        if field_cols and all(sh in wb.sheetnames for sh in cases["sheets"].values()):
            problems += [f"cases.fields: column '{c}' is in no case sheet" for c in sorted(field_cols)]
        if d["sheet"] not in wb.sheetnames:
            problems.append(f"defects sheet missing: '{d['sheet']}'")
        else:
            h = self._headers(wb[d["sheet"]], d["header_row"])
            cols = [(k, c) for k, c in d["columns"].items()] + [(k, c) for k, c in d["batch_fields"].items()]
            for role, col in cols:
                if col and col not in h:
                    problems.append(f"'{d['sheet']}': column '{col}' ({role}) missing")
        v = self.cfg.get("active_view")
        if v and v["sheet"] not in wb.sheetnames:
            problems.append(f"active defects view sheet missing: '{v['sheet']}'")
        r = self.cfg.get("summary")
        if r:
            if r["sheet"] not in wb.sheetnames:
                problems.append(f"summary sheet missing: '{r['sheet']}'")
            else:
                h = self._headers(wb[r["sheet"]], r["header_row"])
                for role in ("area_column", "total_column", "percent_column"):
                    if r.get(role) and r[role] not in h:
                        problems.append(f"'{r['sheet']}': column '{r[role]}' ({role}) missing")
                if not any(r["status_columns"].get(e, e) in h for e in cases["statuses"]):
                    problems.append(f"'{r['sheet']}': no column named after a status (use status_columns)")
        if problems:
            raise ConfigError("configuration does not match the workbook:\n - " + "\n - ".join(problems))

    def save(self, path: str | None = None) -> str:
        """Saves to a temporary file next to the target, checks that it opens, then puts it in place: a stop in the
        middle never leaves a broken workbook (audit 06.11.2026)."""
        out = path or self.path
        folder = os.path.dirname(os.path.abspath(out))
        fd, tmp = tempfile.mkstemp(prefix=".~qa-", suffix=os.path.splitext(out)[1] or ".xlsx", dir=folder)
        os.close(fd)
        try:
            self._need_wb().save(tmp)
            openpyxl.load_workbook(tmp, read_only=True).close()
            for n in range(20):         # Windows: a reader holding the file for a moment
                try:
                    os.replace(tmp, out)
                    break
                except PermissionError:
                    if n == 19:
                        raise
                    import time
                    time.sleep(0.05)
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        return out

    def reviewed_path(self) -> str:
        root, ext = os.path.splitext(self.path)
        return f"{root}.REVIEWED{ext}"

    def save_if_clean(self) -> dict:
        """Saves over the original only if the cross-check returns zero
        problems; otherwise saves <name>.REVIEWED.xlsx and leaves the
        original untouched (technique-excel.md section 1, step 5)."""
        problems = self.cross_check()
        if problems:
            alt = self.save(self.reviewed_path())
            return {"saved_original": False, "file": alt, "problems": problems}
        return {"saved_original": True, "file": self.save(), "problems": []}

    def recalc(self, path: str | None = None) -> dict:
        r = self.cfg["recalc"]
        return recalc_safe(path or self.path, mechanism=r["mechanism"], timeout=r["timeout"])

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def _case_sheets(self):
        """(name, ws, headers) of each case sheet, in the configuration's order."""
        wb, cases = self._need_wb(), self.cfg["cases"]
        seen = []
        for sheet in cases["sheets"].values():
            if sheet not in seen:
                seen.append(sheet)
                ws = wb[sheet]
                yield sheet, ws, self._headers(ws, cases["header_row"])

    def _case_rows(self, ws, h):
        cases = self.cfg["cases"]
        idc = h[cases["columns"]["id"]]
        for r in range(cases["header_row"] + 1, ws.max_row + 1):
            v = ws.cell(r, idc).value
            if v not in (None, ""):
                yield r, v

    def sheet_for_case(self, case_id: str) -> str:
        for pref, sheet in self.cfg["cases"]["sheets"].items():
            if str(case_id).startswith(pref):
                return sheet
        raise KeyError(f"case ID without a configured prefix: {case_id}")

    def progress_summary(self) -> dict:
        """{sheet: {status: n}} -- direct count, not depending on formulas."""
        st = self.cfg["cases"]["columns"]["status"]
        out = {}
        for sheet, ws, h in self._case_sheets():
            cnt = Counter(ws.cell(r, h[st]).value for r, _ in self._case_rows(ws, h))
            out[sheet] = dict(cnt)
        return out

    def case_rows(self) -> dict:
        """{sheet: [row as dict by header, with '_row']} of every case, in the order of the sheets and of the
        rows (read only: the workspace's library)."""
        out = {}
        for sheet, ws, h in self._case_sheets():
            out[sheet] = [{**{name: ws.cell(r, c).value for name, c in h.items()}, "_row": r}
                          for r, _ in self._case_rows(ws, h)]
        return out

    def cases_with_status(self, status: str) -> dict:
        """{sheet: [row as dict, with '_row']} of the cases in a given status."""
        st = self.cfg["cases"]["columns"]["status"]
        out = {}
        for sheet, ws, h in self._case_sheets():
            rows = []
            for r, _ in self._case_rows(ws, h):
                if ws.cell(r, h[st]).value == status:
                    row = {name: ws.cell(r, c).value for name, c in h.items()}
                    row["_row"] = r
                    rows.append(row)
            out[sheet] = rows
        return out

    def _defect_rows(self):
        d = self.cfg["defects"]
        ws = self._need_wb()[d["sheet"]]
        h = self._headers(ws, d["header_row"])
        idc = h[d["columns"]["id"]]
        rows = [(r, ws.cell(r, idc).value) for r in range(d["header_row"] + 1, ws.max_row + 1)]
        return ws, h, [(r, v) for r, v in rows if v not in (None, "")]

    def _defect_number(self, defect_id) -> int | None:
        m = self.cfg["defects"]["_id_re"].match(str(defect_id).strip())
        return int(m.group(1)) if m else None

    def next_defect_number(self) -> int:
        _, _, rows = self._defect_rows()
        nums = [n for n in (self._defect_number(v) for _, v in rows) if n is not None]
        return max(nums, default=0) + 1

    # ------------------------------------------------------------------
    # Control of the batches applied
    # ------------------------------------------------------------------

    @staticmethod
    def _control_names(ws) -> list:
        """Header row of the control sheet, with the headers from before the translation read as the new ones."""
        return [_LEGACY_CONTROL_HEADERS.get(c.value, c.value) for c in ws[1]]

    def _control_ws(self, create: bool):
        wb, name = self._need_wb(), self.cfg["batch_control"]["sheet"]
        if name in wb.sheetnames:
            ws = wb[name]
            if create:  # sheets from earlier versions get the new columns (e.g. batch_id)
                existing = self._control_names(ws)
                for col in _CONTROL_HEADERS:
                    if col not in existing:
                        ws.cell(1, len(existing) + 1).value = col
                        existing.append(col)
            return ws
        if not create:
            return None
        ws = wb.create_sheet(name)
        ws.sheet_state = "hidden"
        ws.append(_CONTROL_HEADERS)
        return ws

    def applied_batches(self) -> list[dict]:
        ws = self._control_ws(create=False)
        if ws is None:
            return []
        head = self._control_names(ws)  # by the header row: old sheets have no batch_id
        return [dict(zip(head, row)) for row in ws.iter_rows(min_row=2, values_only=True) if row[0]]

    def already_applied(self, batch: list, identity: str | None = None) -> dict | None:
        h = identity or batch_hash(batch)
        return next((x for x in self.applied_batches() if x["hash"] == h), None)

    def _no_results_row(self, row: dict) -> bool:
        """True when a control row without keys is an add-cases or an annotate record (it applied no results): the file it
        came from is found by its name and its content hash equals the row's. Read only."""
        batch_id = row.get("batch_id")
        if not batch_id:
            return False
        for folder in ("cases", "analysis", ""):
            path = os.path.join(self.base_dir, folder, f"{batch_id}.json")
            try:
                with open(path, encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            for kind in ("cases", "annotations"):
                key = "add_cases" if kind == "cases" else "annotate"
                if isinstance(data.get(kind), list) and batch_hash([{key: data[kind]}]) == row.get("hash"):
                    return True
        return False

    def _run_log_origin(self, row: dict):
        """(entries, proven) of the run log named like the control row: the entries of its results and whether its content is
        what was applied (the hash of its results equals the row's hash, or its `translated_from`). (None, False) when
        there is no such file. Read only."""
        batch_id = row.get("batch_id")
        if not batch_id:
            return None, False
        try:
            with open(os.path.join(self.base_dir, "runs", f"{batch_id}.json"), encoding="utf-8-sig") as f:
                doc = json.load(f)
            results = [r for r in doc.get("results", []) if isinstance(r, dict)]
            same = batch_hash(doc.get("results", [])) == row.get("hash") or doc.get("translated_from") == row.get("hash")
        except (OSError, ValueError, AttributeError):
            return None, False
        return [result_entry(r) for r in results], bool(same)

    def row_origin(self, row: dict) -> dict:
        """Where the results of a control row can be known from, and whether that origin is PROVEN (plan Q1, review of
        10.11.2026): {"entries": [...] | None, "state": ..., "detail": ...}. `stored`: the column `result_keys` written by the
        apply (authoritative; a run log that no longer matches what was applied is reported in `detail`); `rebuilt`: no column,
        the run log of the same name and its hash equals the row's (proven); `no-results`: a row of cases or notes recognised by
        its file and hash; `divergent`: a run log exists but is NOT what was applied (edited after, or another file): unproven;
        `missing`: nothing to rebuild from. `divergent` and `missing` make the row UNVERIFIABLE. Read only."""
        raw, stored = row.get("result_keys"), None
        if raw not in (None, ""):
            try:
                stored = [str(e) for e in json.loads(raw)]
            except (TypeError, ValueError):
                stored = None
        entries, proven = self._run_log_origin(row)
        batch_id = row.get("batch_id") or "<batch id>"
        if stored is not None:
            detail = None
            if entries is not None and not proven:
                detail = (f"runs/{batch_id}.json is not what was applied (its hash is not the one recorded): the stored keys "
                          "are used, the file changed after the apply")
            return {"entries": stored, "state": "stored", "detail": detail}
        if entries is not None and proven:
            return {"entries": entries, "state": "rebuilt", "detail": None}
        if entries is not None:
            return {"entries": None, "state": "divergent",
                    "detail": f"runs/{batch_id}.json is not what was applied (its hash is not the one recorded in the row)"}
        if self._no_results_row(row):
            return {"entries": [], "state": "no-results", "detail": None}
        return {"entries": None, "state": "missing", "detail": f"no runs/{batch_id}.json to rebuild them"}

    def applied_results_index(self) -> dict:
        """What this workbook already applied (plan Q1): {"results": {result key: batch}, "executions": {execution key:
        (batch, result key)}, "by_case": {case: [{"batch", "ek", "applied_at"}]}, "unverifiable": [text], "warnings": [text]}.
        An application whose origin cannot be proven (`row_origin`) is listed in `unverifiable` with its batch id, time and items:
        duplicates cannot be ruled out against it. Read only."""
        results: dict = {}
        executions: dict = {}
        by_case: dict = {}
        unverifiable: list = []
        warnings: list = []
        for row in self.applied_batches():
            origin = self.row_origin(row)
            batch_id = row.get("batch_id")
            name = batch_id or row.get("label") or "?"
            if origin["entries"] is None:
                unverifiable.append(f"{batch_id or '(no batch id)'} applied at {row.get('applied_at')} "
                                    f"({row.get('items')} item(s)): no result_keys in the control sheet and "
                                    f"{origin['detail']}")
                continue
            if origin["detail"]:
                warnings.append(f"{batch_id}: {origin['detail']}")
            for entry in origin["entries"]:
                rk, _, rest = entry.partition(":")
                ek, _, tc = rest.partition(":")
                ek = None if ek in ("", "-") else ek
                results.setdefault(rk, name)
                if ek:
                    executions.setdefault(ek, (name, rk))
                if tc:
                    by_case.setdefault(tc, []).append({"batch": name, "ek": ek, "applied_at": str(row.get("applied_at") or "")})
        return {"results": results, "executions": executions, "by_case": by_case, "unverifiable": unverifiable,
                "warnings": warnings}

    def duplicate_results(self, batch: list, batch_id: str | None = None) -> list[str]:
        """Why the results of `batch` cannot be applied (empty = they can; plan Q1):
        - a result already applied from ANOTHER batch (a partial batch and its original, or two partial batches);
        - the correction of an execution already applied (same case and time, another status or text) with no `revision_of`
          link to the batch that applied it;
        - a result WITHOUT a time of execution for a case that already has an applied result: a new execution cannot be told
          from a correction, and no time is ever invented: record the time (`--executed-at` with its source) or a revision;
        - a result WITH a time, for a case whose applied execution has no time, when it is not later than the moment that
          application was written: it could be the correction of that execution (a later one is certainly a new execution);
        - an application of this workbook that cannot be checked (its origin is named).
        The batch's own earlier application is not a duplicate here (its hash and its id are handled before)."""
        index = self.applied_results_index()
        out = []
        for item in batch:
            if not isinstance(item, dict):
                continue
            tc, rk, ek = str(item.get("tc", "?")).strip(), result_key(item), execution_key(item)
            prior = [p for p in index["by_case"].get(tc, []) if p["batch"] != batch_id]
            link = item.get("revision_of")
            linked = isinstance(link, dict) and link.get("batch") in {p["batch"] for p in prior}
            if rk in index["results"] and index["results"][rk] != batch_id:
                out.append(f"{tc} (already applied from {index['results'][rk]})")
            elif ek and ek in index["executions"] and index["executions"][ek][0] != batch_id:
                origin = index["executions"][ek][0]
                if not (isinstance(link, dict) and link.get("batch") == origin):
                    out.append(f"{tc} (an execution already applied from {origin} at the same time, with another status or "
                               "text: a correction is recorded as a revision (`revision_of`, run_log.py resolve), not as "
                               "a new execution)")
            elif ek is None and prior and not linked:
                out.append(f"{tc} (no time of execution and the case already has a result applied from "
                           f"{', '.join(sorted({p['batch'] for p in prior}))}: a new execution cannot be told from a correction; "
                           "record the time of execution with its source (run_log.py add --executed-at --executed-source) or "
                           "a revision (`revision_of`))")
            elif ek and not linked:
                untimed = [p for p in prior if p["ek"] is None]
                if untimed and str(item.get("ts")) <= max(p["applied_at"] for p in untimed):
                    out.append(f"{tc} (the case has a result applied from {untimed[-1]['batch']} with no time of execution, written "
                               f"at {untimed[-1]['applied_at']}, and this result is not later than that: it may be its correction; "
                               "record a revision (`revision_of`) or a later time of execution)")
        if index["unverifiable"]:
            out.append("duplicates cannot be ruled out against: " + "; ".join(index["unverifiable"])
                       + " (restore the run log or reconcile the row, `python -m adapters.excel.reconcile`, before applying)")
        return out

    # ------------------------------------------------------------------
    # Applying a batch
    # ------------------------------------------------------------------

    def validate_batch(self, batch: list) -> list[str]:
        """Read only. Problems that prevent applying the batch (empty = it can
        be applied): technique-excel.md section 4."""
        cases, d = self.cfg["cases"], self.cfg["defects"]
        if not isinstance(batch, list) or not batch:
            return ["empty batch"]
        existing = {str(v).strip() for _, ws, h in self._case_sheets() for _, v in self._case_rows(ws, h)}
        defects = {str(v).strip() for _, v in self._defect_rows()[2]}
        statuses, prios = cases["statuses"], d["priorities"]
        problems, seen = [], Counter()
        for i, item in enumerate(batch, 1):
            if not isinstance(item, dict):
                problems.append(f"item {i}: not an object")
                continue
            tc = str(item.get("tc") or "").strip()
            where = f"item {i} ({tc or 'no tc'})"
            if not tc:
                problems.append(f"{where}: 'tc' is missing")
            elif tc not in existing:
                problems.append(f"{where}: the case does not exist in the workbook"
                                + (" (its result is marked case_pending_add: add the case first with "
                                   "`python -m adapters.excel.add_cases`, then apply)" if item.get("case_pending_add") else ""))
            seen[tc] += 1
            if item.get("status") in (None, ""):
                problems.append(f"{where}: 'status' is missing")
            elif item["status"] not in statuses:
                problems.append(f"{where}: status {item['status']!r} outside {statuses}"
                                + (" -- resolve the Question before applying (methodology 8.5)"
                                   if str(item["status"]).lower().startswith("question") else ""))
            bug = item.get("bug")
            if bug not in (None, "") and str(bug).strip() not in defects:
                problems.append(f"{where}: bug {bug!r} does not exist in '{d['sheet']}'")
            new = item.get("new_defect")
            if new not in (None, {}):
                if not isinstance(new, dict):
                    problems.append(f"{where}: 'new_defect' must be an object or null")
                    continue
                if new.get("proposed", False) is not False:
                    problems.append(f"{where}: the new_defect is still a proposal (\"proposed\") -- the user approves "
                                    "it by removing \"proposed\" (or drops it) before the batch is applied "
                                    "(methodology 9.4)")
                if prios:
                    pr = new.get("priority", new.get("severity"))
                    if pr not in prios:
                        problems.append(f"{where}: priority of the new defect {pr!r} outside {prios}")
                known = set(d["batch_fields"]) | {"related_tc", "severity", "priority", "proposed"}
                lost = sorted(k for k in new if k not in known)
                if lost:        # a key with no column was dropped without a word (audit 06.11.2026)
                    problems.append(f"{where}: new_defect key(s) {lost} have no column in excel.defects.batch_fields "
                                    f"(known: {sorted(known)}): map them or rename them, nothing would keep them")
        dup = sorted(tc for tc, n in seen.items() if tc and n > 1)
        if dup:
            problems.append(f"case repeated in the batch: {dup}")
        return problems

    def apply_batch(self, batch: list, label: str | None = None, reported_by: str | None = None,
                    batch_id: str | None = None, identity: str | None = None, run: dict | None = None,
                    keep_previous: bool = False) -> dict:
        """Applies the batch: status, actual result and notes per case, and
        creates the new defects. A batch already applied to this workbook (same
        content) is not applied again: returns {"applied": False, ...} without
        changing anything (technique-excel.md section 5). With `batch_id` (apply
        uses the file name), a batch with that id already applied but with a
        different content -- a batch edited after being applied -- is refused.
        `identity` replaces the content's hash as the batch's identity: a batch
        converted by scripts/migrate_workspace.py carries the hash of its
        original content (`translated_from`), which the workbook recorded.

        `keep_previous`: a result the case already had (and its status) is kept in
        the case's notes when the batch replaces it, so nothing is lost in the
        workbook; it needs the notes column.

        The whole batch is validated before anything is written
        (validate_batch); if it has problems, raises InvalidBatch and nothing
        changes."""
        self._need_wb()
        h_batch = identity or batch_hash(batch)
        previous = self.already_applied(batch, h_batch)
        if previous:
            return {"applied": False, "hash": h_batch, "new_defects": [],
                    "reason": f"batch already applied at {previous['applied_at']}"}
        if batch_id:
            same_id = next((x for x in self.applied_batches() if x.get("batch_id") == batch_id), None)
            if same_id:
                raise InvalidBatch([(
                    f"the batch '{batch_id}' was already applied at {same_id['applied_at']} with a DIFFERENT "
                    "content (it was edited after being applied). Applying it again would repeat notes and "
                    "defects. Put the correction in a new batch, with another name, with only the cases to "
                    "correct (technique-excel.md section 5)."
                )])

        label = label or f"[{self._now().date().isoformat()}]"
        cases, d, notes = self.cfg["cases"], self.cfg["defects"], self.cfg["notes"]
        reported_by = reported_by or d["reported_by"]
        ccol = cases["columns"]

        problems = self.validate_batch(batch)
        if keep_previous:
            missing = [sheet for sheet, _ws, h in self._case_sheets() if ccol.get("notes") not in h]
            if missing:
                problems.append("--keep-previous needs the notes column (excel.cases.columns.notes) in every case "
                                f"sheet; missing in {missing}")
        duplicates = self.duplicate_results(batch, batch_id)
        if duplicates:
            problems.append("results that cannot be applied (the same result is never applied twice; apply only the cases "
                            "not yet applied, e.g. with `run_log.py subset`): " + "; ".join(duplicates))
        if problems:
            raise InvalidBatch(problems)

        # case -> (ws, headers, row)
        rowmap = {}
        for _sheet, ws, h in self._case_sheets():
            for r, v in self._case_rows(ws, h):
                rowmap.setdefault(str(v).strip(), (ws, h, r))

        ws_def, h_def, def_rows = self._defect_rows()
        last_def_row = max([r for r, _ in def_rows] + [d["header_row"]])
        next_num = self.next_defect_number()
        dcol = d["columns"]
        new_ids = []
        replaced: list[str] = []         # the cases whose earlier result text this batch replaces

        for item in batch:
            tc = str(item["tc"]).strip()
            ws, h, r = rowmap[tc]
            old_status = ws.cell(r, h[ccol["status"]]).value
            ws.cell(r, h[ccol["status"]]).value = item["status"]
            has_res = ccol.get("actual_result") in h
            old_result = ws.cell(r, h[ccol["actual_result"]]).value if has_res else None
            if old_result not in (None, "") and str(old_result) != str(item.get("actual_result")):
                replaced.append(tc)
            if has_res:
                ws.cell(r, h[ccol["actual_result"]]).value = item.get("actual_result")

            new_defect = item.get("new_defect")
            def_id = None
            if new_defect:
                def_id = d["id_format"].replace("{n}", str(next_num))
                next_num += 1
                new_ids.append(def_id)

            if ccol.get("notes") in h:
                parts = []
                if not has_res:
                    parts.append(notes["result"].format(result=item.get("actual_result")))
                if keep_previous and old_result not in (None, "") and str(old_result) != str(item.get("actual_result")):
                    parts.append(notes["previous_result"].format(status=old_status, result=old_result))
                if def_id:
                    parts.append(notes["new_defect"].format(id=def_id))
                if item.get("bug") not in (None, ""):
                    parts.append(notes["existing_defect"].format(id=str(item["bug"]).strip()))
                if parts:
                    note = f"{label} " + " ".join(parts)
                    c = ws.cell(r, h[ccol["notes"]])
                    c.value = (str(c.value).rstrip() + "\n\n" + note).strip() if c.value else note

            if new_defect:
                last_def_row += 1
                vals = dict(d["defaults"])
                if (run or {}).get("build") and "build" in d["batch_fields"]:
                    vals["build"] = run["build"]        # the build of the run, before a project-wide default
                vals.update(new_defect)

                def put(col, value, row=last_def_row):
                    if col:
                        ws_def.cell(row, h_def[col]).value = value

                put(dcol["id"], def_id)
                put(dcol.get("case"), vals.get("related_tc", tc))
                for key, col in d["batch_fields"].items():
                    put(col, vals.get(key))
                put(dcol.get("severity"), vals.get("severity"))
                put(dcol.get("priority"), vals.get("priority", vals.get("severity")))
                put(dcol["status"], d["initial_status"])
                put(dcol.get("reported_by"), reported_by)
                put(dcol.get("report_date"), self._now().date().isoformat())

        ctl = self._control_ws(create=True)
        record = {"hash": h_batch, "applied_at": self._now().isoformat(timespec="seconds"),
                  "label": label, "items": len(batch), "new_defects": ", ".join(new_ids),
                  "batch_id": batch_id,
                  "result_keys": json.dumps([result_entry(r) for r in batch if isinstance(r, dict)])}
        ctl.append([record.get(name) for name in self._control_names(ctl)])
        return {"applied": True, "hash": h_batch, "new_defects": new_ids, "replaced_results": replaced}

    # ------------------------------------------------------------------
    # Adding approved cases (technique-excel.md section 4.1)
    # ------------------------------------------------------------------

    @staticmethod
    def _title_key(value) -> str:
        return " ".join(str(value or "").split()).casefold()

    @staticmethod
    def case_cell_text(field: str, value):
        """The text a model field gets in its cell: steps one per line, lists joined."""
        if value in (None, "", []):
            return None
        if field == "steps" and isinstance(value, list):
            lines = []
            for n, step in enumerate(value, 1):
                line = f"{n}. {str(step.get('action') or '').strip()}"
                if step.get("data") not in (None, ""):
                    line += f" [{step['data']}]"
                if step.get("expected") not in (None, ""):
                    line += f" -> {step['expected']}"
                lines.append(line)
            return "\n".join(lines)
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return value

    def validate_cases(self, cases: list) -> list[str]:
        """Read only. Problems that prevent adding the cases (empty = they can
        be added): technique-excel.md section 4.1."""
        ccfg = self.cfg["cases"]
        if "title" not in ccfg["fields"]:
            return [("cases.fields has no 'title' column: add-cases needs it to find duplicates "
                     "(technique-excel.md section 4.1)")]
        if not isinstance(cases, list) or not cases:
            return ["no cases"]
        existing = {str(v).strip() for _, ws, h in self._case_sheets() for _, v in self._case_rows(ws, h)}
        titles: dict[str, dict[str, str]] = {}  # sheet -> {title key: case id}
        for sheet, ws, h in self._case_sheets():
            tcol = h.get(ccfg["fields"]["title"])
            titles[sheet] = {} if tcol is None else {
                self._title_key(ws.cell(r, tcol).value): str(v).strip()
                for r, v in self._case_rows(ws, h) if ws.cell(r, tcol).value not in (None, "")}
        problems, seen = [], Counter()
        for i, case in enumerate(cases, 1):
            if not isinstance(case, dict):
                problems.append(f"case {i}: not an object")
                continue
            cid = str(case.get("id") or "").strip()
            where = f"case {i} ({cid or 'no id'})"
            sheet = None
            if not cid:
                problems.append(f"{where}: 'id' is missing")
            else:
                seen[cid] += 1
                try:
                    sheet = self.sheet_for_case(cid)
                except KeyError:
                    problems.append(f"{where}: the id has no prefix of cases.sheets {list(ccfg['sheets'])}")
                if cid in existing:
                    problems.append(f"{where}: the case already exists in the workbook")
            title = self._title_key(case.get("title"))
            if not title:
                problems.append(f"{where}: 'title' is missing")
            elif sheet:
                other = titles[sheet].get(title)
                if other and other != cid:
                    problems.append(f"{where}: candidate duplicate of {other} (same title in '{sheet}'): remove the "
                                    "case from the file if it is the same case (methodology 10)")
                else:
                    titles[sheet][title] = cid
            steps = case.get("steps")
            if steps is not None and not (isinstance(steps, list) and all(isinstance(x, dict) for x in steps)):
                problems.append(f"{where}: 'steps' must be a list of {{action, data, expected}}")
        dup = sorted(c for c, n in seen.items() if n > 1)
        if dup:
            problems.append(f"case id repeated in the file: {dup}")
        return problems

    def plan_cases(self, cases: list) -> list[dict]:
        """Read only, for valid cases: per case {"id", "sheet", "row", "cells":
        {column: value}, "not_written": [fields]}, in the file's order."""
        ccfg = self.cfg["cases"]
        sheets = {sheet: (ws, h) for sheet, ws, h in self._case_sheets()}
        last = {sheet: max([r for r, _ in self._case_rows(ws, h)] + [ccfg["header_row"]])
                for sheet, (ws, h) in sheets.items()}
        out = []
        for case in cases:
            cid = str(case["id"]).strip()
            sheet = self.sheet_for_case(cid)
            _, h = sheets[sheet]
            last[sheet] += 1
            cells = {ccfg["columns"]["id"]: cid, ccfg["columns"]["status"]: ccfg["new_status"]}
            not_written = []
            for field in CASE_MODEL_FIELDS:
                value = self.case_cell_text(field, case.get(field))
                if value is None:
                    continue
                col = ccfg["fields"].get(field)
                if col and col in h:
                    cells[col] = value
                else:
                    not_written.append(field)
            out.append({"id": cid, "sheet": sheet, "row": last[sheet], "cells": cells, "not_written": not_written})
        return out

    @staticmethod
    def _copy_row_format(ws, h: dict, src: int, dst: int, written: set) -> None:
        """Style of row `src` on row `dst`; formulas of the columns not written translated; data
        validations that covered `src` extended to `dst`. The row height is never set (section 3)."""
        for col in h.values():
            a, b = ws.cell(src, col), ws.cell(dst, col)
            if a.has_style:
                b.font, b.fill, b.border = copy.copy(a.font), copy.copy(a.fill), copy.copy(a.border)
                b.alignment, b.protection = copy.copy(a.alignment), copy.copy(a.protection)
                b.number_format = a.number_format
            if col not in written and isinstance(a.value, str) and a.value.startswith("="):
                b.value = Translator(a.value, origin=a.coordinate).translate_formula(b.coordinate)
        for dv in ws.data_validations.dataValidation:
            for col in h.values():
                a, b = ws.cell(src, col).coordinate, ws.cell(dst, col).coordinate
                if a in dv.sqref and b not in dv.sqref:
                    dv.sqref.add(b)

    def add_cases(self, cases: list, label: str | None = None, batch_id: str | None = None) -> dict:
        """Adds approved cases (technique-excel.md section 4.1): each in the row
        after the last case of its sheet, with the previous row's style, in the
        status cases.new_status. Recorded in the control sheet like a batch: the
        same cases again change nothing ({"added": False}); the same batch_id
        with another content is refused. Everything is validated before
        writing (InvalidBatch)."""
        self._need_wb()
        h_cases = batch_hash([{"add_cases": cases}])
        previous = next((x for x in self.applied_batches() if x["hash"] == h_cases), None)
        if previous:
            return {"added": False, "hash": h_cases, "cases": [],
                    "reason": f"cases already added at {previous['applied_at']}"}
        if batch_id:
            same_id = next((x for x in self.applied_batches() if x.get("batch_id") == batch_id), None)
            if same_id:
                raise InvalidBatch([(
                    f"the cases file '{batch_id}' was already added at {same_id['applied_at']} with a DIFFERENT "
                    "content (it was edited after being added). New cases go in a new file, with another name "
                    "(technique-excel.md section 4.1)."
                )])
        problems = self.validate_cases(cases)
        if problems:
            raise InvalidBatch(problems)

        plan = self.plan_cases(cases)
        sheets = {sheet: (ws, h) for sheet, ws, h in self._case_sheets()}
        for p in plan:
            ws, h = sheets[p["sheet"]]
            written = {h[c] for c in p["cells"]}
            if p["row"] - 1 > self.cfg["cases"]["header_row"]:
                self._copy_row_format(ws, h, p["row"] - 1, p["row"], written)
            for col, value in p["cells"].items():
                ws.cell(p["row"], h[col]).value = value

        label = label or f"[{self._now().date().isoformat()}]"
        ctl = self._control_ws(create=True)
        record = {"hash": h_cases, "applied_at": self._now().isoformat(timespec="seconds"),
                  "label": label, "items": len(cases), "new_defects": "", "batch_id": batch_id,
                  "result_keys": "[]"}            # a row of cases: it applies no results
        ctl.append([record.get(name) for name in self._control_names(ctl)])
        return {"added": True, "hash": h_cases, "cases": plan}

    # ------------------------------------------------------------------
    # Active defects view
    # ------------------------------------------------------------------

    def open_defects(self, sort_by: str | None = None) -> dict:
        """Read only. {"open": [row as dict], "closed": n}: the defects whose
        status is not in closed_statuses, sorted by priority, by `sort_by`
        (defects column, optional) and by the defect's number."""
        d = self.cfg["defects"]
        ws_def, h_def, def_rows = self._defect_rows()
        st_col = h_def[d["columns"]["status"]]
        closed_set = set(d["closed_statuses"])

        rows, closed = [], 0
        for r, _ in def_rows:
            if ws_def.cell(r, st_col).value in closed_set:
                closed += 1
                continue
            rows.append({name: ws_def.cell(r, c).value for name, c in h_def.items()})

        prio_col, prios = d["columns"].get("priority"), d["priorities"]

        def key(row):
            p = row.get(prio_col) if prio_col else None
            pk = prios.index(p) if p in prios else len(prios)
            n = self._defect_number(row.get(d["columns"]["id"]))
            sec = str(row.get(sort_by) or "") if sort_by else ""
            return (pk, sec, n if n is not None else float("inf"))

        rows.sort(key=key)
        return {"open": rows, "closed": closed}

    def regenerate_active_defects(self) -> dict:
        """Rebuilds the data rows of the active defects view from the defects
        sheet (without the closed statuses), sorted by priority, by the
        configured secondary criterion and by the defect's number. Keeps what
        is above the first data row and reapplies the style of the original
        first data row."""
        v = self.cfg.get("active_view")
        if not v:
            return {"total_open": None, "reason": "active_view not configured"}
        d = self.cfg["defects"]
        od = self.open_defects(v.get("sort_by"))
        rows, closed = od["open"], od["closed"]

        ws = self._need_wb()[v["sheet"]]
        hv = self._headers(ws, v["header_row"])
        first = v["header_row"] + 1
        styles = None
        if ws.cell(first, 1).value not in (None, ""):
            styles = {}
            for col in hv.values():
                c = ws.cell(first, col)
                styles[col] = (copy.copy(c.font), copy.copy(c.fill), copy.copy(c.border),
                               copy.copy(c.alignment), c.number_format)
        if ws.max_row >= first:
            ws.delete_rows(first, ws.max_row - first + 1)

        for i, row in enumerate(rows):
            target = first + i
            for head, col in hv.items():
                c = ws.cell(target, col)
                c.value = row.get(v["columns"].get(head, head))
                if styles:
                    c.font, c.fill, c.border, c.alignment, c.number_format = styles[col]

        sev_col = d["columns"].get("severity")
        sev = Counter(row.get(sev_col) for row in rows) if sev_col else Counter()
        summary = v.get("summary")
        if summary:
            ws[summary["cell"]].value = summary["template"].format_map(
                {"open": len(rows), "closed": closed, "severity": sev, "date": self._now().date()}
            )
        return {"total_open": len(rows), "by_severity": dict(sev), "closed": closed}

    # ------------------------------------------------------------------
    # Cross-check (methodology section 11)
    # ------------------------------------------------------------------

    def cross_check(self) -> list[str]:
        """Returns a list of problems as text (empty = 0 problems): case and
        defect statuses outside the valid list, duplicated case and defect
        IDs, and defects linked to a case that does not exist."""
        cases, d = self.cfg["cases"], self.cfg["defects"]
        case_statuses, def_statuses = set(cases["statuses"]), set(d["statuses"])
        problems = []
        case_ids = Counter()
        for sheet, ws, h in self._case_sheets():
            sc = h[cases["columns"]["status"]]
            for r, v in self._case_rows(ws, h):
                case_ids[str(v).strip()] += 1
                st = ws.cell(r, sc).value
                if st not in case_statuses:
                    problems.append(f"{sheet} row {r} ({v}): invalid case status {st!r}")
        dup = sorted(k for k, n in case_ids.items() if n > 1)
        if dup:
            problems.append(f"duplicated case ID: {dup}")

        ws_def, h_def, def_rows = self._defect_rows()
        sc = h_def[d["columns"]["status"]]
        case_col = d["columns"].get("case")
        seen = Counter()
        for r, v in def_rows:
            seen[str(v).strip()] += 1
            st = ws_def.cell(r, sc).value
            if st not in def_statuses:
                problems.append(f"{d['sheet']} row {r} ({v}): invalid defect status {st!r}")
            if case_col:
                ref = ws_def.cell(r, h_def[case_col]).value
                for tc in self._case_refs(ref):
                    if tc not in case_ids:
                        problems.append(f"{d['sheet']} row {r} ({v}): related case does not exist {tc!r}")
        dup = sorted(k for k, n in seen.items() if n > 1)
        if dup:
            problems.append(f"duplicated defect ID: {dup}")
        return problems

    def check_summary(self, path: str | None = None) -> dict:
        """Read only. Compares the CALCULATED values of the summary sheet
        (excel.summary) with the direct count of the statuses, both read from
        the file saved at `path` (by default the workbook): per area, per
        status, in the area's total and in the total row (technique-excel.md
        section 5). Returns {"checked", "problems", "reason"}; checked is
        False without a summary or without calculated values (recalculate
        first)."""
        r = self.cfg.get("summary")
        if not r:
            return {"checked": False, "problems": [], "reason": "summary not configured"}
        wb = openpyxl.load_workbook(path or self.path, data_only=True)
        reader = copy.copy(self)
        reader.wb = wb
        direct = {area: Counter(c) for area, c in reader.progress_summary().items()}
        total = sum(direct.values(), Counter())

        ws = wb[r["sheet"]]
        h = self._headers(ws, r["header_row"])
        col_area = h[r["area_column"]]
        col_st = {e: h[r["status_columns"].get(e, e)] for e in self.cfg["cases"]["statuses"]
                  if r["status_columns"].get(e, e) in h}
        col_tot = h.get(r["total_column"]) if r.get("total_column") else None
        # % run (methodology 16.3): (total - not run) / total; N/A and Blocked count
        col_pct = h.get(r["percent_column"]) if r.get("percent_column") else None
        pend, tol_pct = r.get("not_run_status"), r.get("percent_tolerance")

        problems, seen, cells, empty = [], set(), 0, 0
        total_row_seen = False
        for row in range(r["header_row"] + 1, ws.max_row + 1):
            area = ws.cell(row, col_area).value
            area = "" if area is None else str(area).strip()
            if r.get("total_row") and area == r["total_row"]:
                target, name, total_row_seen = total, "total row", True
            elif area in direct:
                target, name = direct[area], f"area '{area}'"
                seen.add(area)
            else:
                continue  # titles, notes and empty rows are not areas
            compare = [(e, c, target.get(e, 0)) for e, c in col_st.items()]
            if col_tot:
                compare.append(("total", col_tot, sum(target.values())))
            if col_pct:
                n = sum(target.values())
                compare.append(("% run", col_pct, (n - target.get(pend, 0)) / n if n else 0.0))
            for caption, col, expected in compare:
                cell = ws.cell(row, col)
                cells += 1
                if cell.value is None:
                    empty += 1
                    continue
                try:
                    v = float(cell.value)
                    if col == col_pct:  # fraction (0.2857) or percentage points (28.57)
                        equal = min(abs(v - expected * 100), abs(v - expected) * 100) <= tol_pct
                    else:
                        equal = v == expected
                except (TypeError, ValueError):
                    equal = False
                if not equal:
                    rule = (f"(total - {pend}) / total gives {expected:.4%}" if col == col_pct
                            else f"the direct count gives {expected}")
                    problems.append(f"{r['sheet']}!{cell.coordinate} ({name}, {caption}): the summary says "
                                    f"{cell.value!r}, {rule}")
        if cells and empty == cells:
            return {"checked": False, "problems": [],
                    "reason": "the summary has no calculated values -- recalculate first"}
        if empty:
            problems.append(f"{r['sheet']}: {empty} cell(s) without a calculated value")
        problems += [f"{r['sheet']}: the area '{a}' is not in the summary" for a in direct if a not in seen]
        if r.get("total_row") and not total_row_seen:
            problems.append(f"{r['sheet']}: total row '{r['total_row']}' not found")
        return {"checked": True, "problems": problems, "reason": None}

    def _case_refs(self, value) -> list[str]:
        """Case IDs named in a cell (there may be several, separated by comma,
        semicolon, space or line break). Only what starts with a configured
        case prefix counts as a reference."""
        if value in (None, ""):
            return []
        prefixes = tuple(self.cfg["cases"]["sheets"].keys())
        return [t for t in re.split(r"[\s,;/]+", str(value)) if t.startswith(prefixes)]

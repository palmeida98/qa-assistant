"""Findings, evidence, severities and statuses (docs/qa-audit.md §9).

The evidence rule: a `problem` or a `warning` without evidence is refused. The engine must be able to show
what it saw; otherwise the result is `needs-human` or nothing at all.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SEVERITIES = ("critical", "high", "medium", "low", "info")
STATUSES = ("problem", "warning", "needs-human")
CONFIDENCES = ("high", "medium", "low")
EVIDENCE_KINDS = ("screenshot", "html", "header", "metric", "log", "text")


def severity_rank(severity: str) -> int:
    """0 for critical … 4 for info. ValueError for an unknown severity."""
    return SEVERITIES.index(severity)


@dataclass
class Evidence:
    kind: str
    value: str = ""        # the observed text (snippet, header line, metric) — never a secret (see privacy.py)
    path: str = ""         # file inside the run folder, relative to it
    label: str = ""

    def __post_init__(self):
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence kind {self.kind!r}; expected one of {EVIDENCE_KINDS}")
        if not (self.value or self.path):
            raise ValueError("evidence needs a value or a path")


@dataclass
class Finding:
    module: str
    check: str
    title: str
    status: str
    severity: str
    url: str
    description: str = ""
    expected: str = ""
    actual: str = ""
    recommendation: str = ""
    confidence: str = "high"
    element: dict = field(default_factory=dict)      # {"selector": ..., "snippet": ...}
    steps: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    id: str = ""                                     # assigned by the runner (e.g. ACC-001)

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}; expected one of {STATUSES}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}; expected one of {SEVERITIES}")
        if self.confidence not in CONFIDENCES:
            raise ValueError(f"unknown confidence {self.confidence!r}; expected one of {CONFIDENCES}")
        if not self.module or not self.check or not self.title or not self.url:
            raise ValueError("a finding needs module, check, title and url")
        if self.status in ("problem", "warning") and not self.evidence:
            raise ValueError(f"{self.module}/{self.check}: a {self.status} without evidence is not reported "
                             "(docs/qa-audit.md §9)")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckTally:
    """Checks that ran without a finding, per module (the 'passed' count)."""

    passed: dict[str, int] = field(default_factory=dict)

    def add(self, module: str, n: int = 1) -> None:
        self.passed[module] = self.passed.get(module, 0) + n

    @property
    def total(self) -> int:
        return sum(self.passed.values())


def summarize(findings: list[Finding], tally: CheckTally) -> dict:
    by_status = {s: 0 for s in STATUSES}
    by_severity = {s: 0 for s in SEVERITIES}
    for f in findings:
        by_status[f.status] += 1
        if f.status != "needs-human":
            by_severity[f.severity] += 1
    return {"passed": tally.total, "by_status": by_status, "by_severity": by_severity}


def assign_ids(findings: list[Finding], prefixes: dict[str, str]) -> None:
    """Stable, readable ids per module in report order: ACC-001, ACC-002, …"""
    counters: dict[str, int] = {}
    for f in findings:
        p = prefixes.get(f.module) or f.module[:3].upper()
        counters[p] = counters.get(p, 0) + 1
        f.id = f"{p}-{counters[p]:03d}"


def sort_key(f: Finding):
    return (STATUSES.index(f.status), severity_rank(f.severity), f.module, f.url, f.check)

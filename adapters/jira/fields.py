"""
Field mapping of the Jira back-end (technique-jira-xray.md section 1.1): a model value -> the Jira field's format
(from the field's schema in the create metadata), and a Jira value -> plain text for the model.

- a plain mapping ("summary", "customfield_10051") is a Jira field id, formatted from its schema: text, rich text
  (Atlassian Document Format), option, array of options, component, priority, version, user, number;
- `labels:<prefix>` stores the value as a label with that prefix; labels cannot have spaces, so a value with spaces
  is written with "-" and reported.
"""

from __future__ import annotations

import re

from .adf import from_adf, is_adf, to_adf

LABELS = "labels:"
RICH_TEXT_SYSTEM = {"description", "environment"}
TEXTAREA = "com.atlassian.jira.plugin.system.customfieldtypes:textarea"


def is_label(mapping: str) -> bool:
    return str(mapping).startswith(LABELS)


def label_prefix(mapping: str) -> str:
    return str(mapping)[len(LABELS):]


def label_value(prefix: str, value) -> tuple[str, bool]:
    """(the label, whether spaces were replaced)."""
    text = str(value).strip()
    label = re.sub(r"\s+", "-", text)
    return prefix + label, label != text


def cf_number(field_id: str) -> str | None:
    m = re.fullmatch(r"customfield_(\d+)", str(field_id))
    return m.group(1) if m else None


def jql_field(field_id: str) -> str:
    """The JQL name of a field id: cf[10051] for a custom field, `component` for components, the id otherwise."""
    n = cf_number(field_id)
    if n:
        return f"cf[{n}]"
    return {"components": "component", "fixVersions": "fixVersion", "issuetype": "issuetype"}.get(field_id, field_id)


def is_rich_text(field_id: str, schema: dict | None) -> bool:
    schema = schema or {}
    return field_id in RICH_TEXT_SYSTEM or schema.get("custom") == TEXTAREA


def _values(value) -> list:
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")]
    if isinstance(value, str) and "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return [value]


def _one(kind: str, value):
    if kind in ("option", "option-with-child"):
        return {"value": str(value)}
    if kind in ("component", "priority", "version", "issuetype", "resolution"):
        return {"name": str(value)}
    if kind == "user":
        return {"accountId": str(value)}
    if kind == "number":
        try:
            return float(value) if "." in str(value) else int(value)
        except ValueError:
            return value
    return value if isinstance(value, (dict, int, float, bool)) else str(value)


def format_value(field_id: str, schema: dict | None, value):
    """A model value -> the body value of a Jira field, from the field's schema (createmeta)."""
    schema = schema or {}
    kind = schema.get("type")
    if isinstance(value, dict):
        return value   # already in the Jira format (a default written by hand)
    if kind == "array":
        items = schema.get("items") or "string"
        return [_one(items, v) for v in _values(value)]
    if kind in ("string", None) and is_rich_text(field_id, schema):
        return to_adf(value)
    if kind in ("string", None):
        return "" if value is None else str(value)
    return _one(kind, value)


def read_value(value) -> tuple[str | None, bool]:
    """A Jira value -> (plain text or None, rich text simplified)."""
    if value in (None, "", []):
        return None, False
    if is_adf(value):
        text, simplified = from_adf(value)
        return text, simplified
    if isinstance(value, list):
        parts = [read_value(v) for v in value]
        texts = [p[0] for p in parts if p[0] not in (None, "")]
        return (", ".join(texts) if texts else None), any(p[1] for p in parts)
    if isinstance(value, dict):
        for key in ("value", "name", "displayName", "key"):
            if value.get(key) not in (None, ""):
                return str(value[key]), False
        return None, False
    if isinstance(value, float) and value.is_integer():
        return str(int(value)), False
    return str(value), False


def read_labels(labels, prefix: str) -> str | None:
    found = [lab[len(prefix):] for lab in labels or [] if isinstance(lab, str) and lab.startswith(prefix)]
    return ", ".join(found) if found else None

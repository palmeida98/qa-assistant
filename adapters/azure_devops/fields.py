"""
Field mapping of the Azure DevOps back-end (technique-azure-devops.md section 1.1): a model value -> a work item
field (formatted from the field's type), and a work item value -> plain text for the model.

- a plain mapping ("System.AreaPath", "Custom.Feature") is a field's reference name; its type comes from the project's
  fields (`html` fields get the text as HTML, `integer`/`picklistInteger` an integer, the others text) and a field
  with allowed values (a picklist, e.g. severity "2 - High") refuses any other value;
- `tag:<prefix>` stores the value as a tag (`System.Tags`, "; " separated); a tag cannot have ";" (written as ",").
"""

from __future__ import annotations

from .html import from_html, to_html

TAG = "tag:"
TAGS = "System.Tags"
HTML_FIELDS = {"System.Description", "Microsoft.VSTS.TCM.ReproSteps", "Microsoft.VSTS.TCM.SystemInfo",
               "Microsoft.VSTS.Common.AcceptanceCriteria"}
INTEGER_TYPES = ("integer", "picklistInteger")
# fields the tracker fills itself, never "missing" on a create
AUTOMATIC = {"System.Id", "System.Rev", "System.State", "System.Reason", "System.AreaPath", "System.IterationPath",
             "System.TeamProject", "System.WorkItemType", "System.CreatedBy", "System.CreatedDate",
             "System.ChangedBy", "System.ChangedDate", "System.AuthorizedAs", "System.AuthorizedDate",
             "System.RevisedDate", "System.Watermark", "System.BoardColumn", "System.BoardColumnDone",
             "System.BoardLane", "System.NodeName", "System.AreaId", "System.IterationId", "System.CommentCount"}


def is_tag(mapping: str) -> bool:
    return str(mapping).startswith(TAG)


def tag_prefix(mapping: str) -> str:
    return str(mapping)[len(TAG):]


def tag_value(prefix: str, value) -> tuple[str, bool]:
    """(the tag, whether it was changed: ";" is the tag separator)."""
    text = str(value).strip()
    tag = text.replace(";", ",")
    return prefix + tag, tag != text


def split_tags(value) -> list[str]:
    return [t.strip() for t in str(value or "").split(";") if t.strip()]


def join_tags(tags: list[str]) -> str:
    out: list[str] = []
    for t in tags:
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return "; ".join(out)


def read_tags(tags, prefix: str) -> str | None:
    found = [t[len(prefix):] for t in split_tags(tags) if t.startswith(prefix)]
    return ", ".join(found) if found else None


def is_html(ref: str, field: dict | None) -> bool:
    return ref in HTML_FIELDS or str((field or {}).get("type", "")).lower() == "html"


def format_value(ref: str, field: dict | None, value, allowed: list | None = None) -> tuple[object, str | None]:
    """A model value -> (the work item value, a problem or None)."""
    kind = str((field or {}).get("type") or "string")
    if is_html(ref, field):
        return to_html(value), None
    if kind in INTEGER_TYPES:
        try:
            value = int(str(value).strip())
        except ValueError:
            return value, f"{value!r} is not a number ({ref} is {kind})"
    elif kind in ("double", "picklistDouble"):
        try:
            value = float(str(value).strip())
        except ValueError:
            return value, f"{value!r} is not a number ({ref} is {kind})"
    elif not isinstance(value, (int, float, bool)):
        value = str(value)
    if allowed and str(value) not in [str(a) for a in allowed]:
        return value, f"{value!r} is not an allowed value of {ref}: {allowed}"
    return value, None


def read_value(ref: str, value, field: dict | None = None) -> tuple[str | None, bool]:
    """A work item value -> (plain text or None, rich text simplified); `field` (the project's field, with its
    type) says whether it is HTML."""
    if value in (None, "", []):
        return None, False
    if isinstance(value, dict):
        for key in ("displayName", "name", "uniqueName", "id"):
            if value.get(key) not in (None, ""):
                return str(value[key]), False
        return None, False
    if isinstance(value, float) and value.is_integer():
        return str(int(value)), False
    if isinstance(value, str) and is_html(ref, field):
        text, simplified = from_html(value)
        return (text or None), simplified
    return str(value), False

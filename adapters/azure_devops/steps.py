"""
Test case steps <-> `Microsoft.VSTS.TCM.Steps` (technique-azure-devops.md section 1.1): an XML document with one
<step> per step, whose two <parameterizedString> are the action and the expected result, each as HTML (escaped inside
the XML). The model's `data` of a step goes at the end of the action, after a line "Data: ...", and is read back from
there. Step ids start at 2 (the result's actionPath "00000002" is the first step); `last` is the last id used.

A <compref ref="N"> is a shared steps work item: read as a reference (the back-end expands it to text and flags the
case `shared_steps`); the adapter never writes one.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from .html import from_html, to_html

DATA = "Data: "


def _action_text(step: dict) -> str:
    action = str(step.get("action") or "")
    data = str(step.get("data") or "")
    if data:
        return f"{action}\n{DATA}{data}" if action else f"{DATA}{data}"
    return action


def to_xml(steps: list[dict]) -> str:
    """[{action, data, expected}] -> the Steps field ("" for no steps)."""
    steps = [s for s in steps or [] if any(str(s.get(k) or "").strip() for k in ("action", "data", "expected"))]
    if not steps:
        return ""
    out, n = [], 1
    for s in steps:
        n += 1
        expected = str(s.get("expected") or "")
        kind = "ValidateStep" if expected.strip() else "ActionStep"
        out.append(f"<step id={quoteattr(str(n))} type={quoteattr(kind)}>"
                   f"<parameterizedString isformatted=\"true\">{escape(to_html(_action_text(s)))}"
                   f"</parameterizedString><parameterizedString isformatted=\"true\">{escape(to_html(expected))}"
                   f"</parameterizedString><description/></step>")
    return f"<steps id=\"0\" last=\"{n}\">" + "".join(out) + "</steps>"


def _split_data(action: str) -> tuple[str, str]:
    lines = action.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(DATA):
            return "\n".join(lines[:i]), "\n".join([lines[i][len(DATA):]] + lines[i + 1:])
    return action, ""


def parse(xml_text) -> tuple[list, bool]:
    """The Steps field -> ([{"action", "data", "expected"} or {"shared": <work item id>}], simplified)."""
    if xml_text in (None, ""):
        return [], False
    try:
        root = ET.fromstring(str(xml_text))
    except ET.ParseError:
        text, _ = from_html(xml_text)
        return ([{"action": text, "data": "", "expected": ""}] if text else []), True
    out, simplified = [], False
    for node in root:
        tag = node.tag.lower()
        if tag == "step":
            parts = [p.text or "" for p in node.findall("parameterizedString")]
            action, s1 = from_html(parts[0] if parts else "")
            expected, s2 = from_html(parts[1] if len(parts) > 1 else "")
            simplified = simplified or s1 or s2
            action, data = _split_data(action)
            out.append({"action": action, "data": data, "expected": expected})
        elif tag == "compref":
            out.append({"shared": int(node.get("ref"))} if str(node.get("ref") or "").isdigit() else
                       {"action": "", "data": "", "expected": ""})
            # steps a compref may carry inside are those of the test case itself (after the shared steps)
            for child in node:
                if child.tag.lower() == "step":
                    sub, s = parse(f"<steps>{ET.tostring(child, encoding='unicode')}</steps>")
                    out += sub
                    simplified = simplified or s
    return out, simplified

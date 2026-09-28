"""
Atlassian Document Format (ADF) <-> plain text (technique-jira-xray.md section 1.1).

The adapter writes plain paragraphs, bullet lists ("- " lines) and code blocks (``` fences) and reads ADF back to
plain text. The round trip is exact for what `to_adf` writes; any other node (tables, panels, mentions, headings...)
is read as its text and `from_adf` reports it as simplified (the `rich_text_simplified` flag).
"""

from __future__ import annotations

WRITTEN = {"doc", "paragraph", "text", "bulletList", "listItem", "codeBlock", "hardBreak"}


def _text(value: str) -> list[dict]:
    """The inline content of one paragraph: text nodes separated by hard breaks (a line break inside it)."""
    out: list[dict] = []
    for i, line in enumerate(value.split("\n")):
        if i:
            out.append({"type": "hardBreak"})
        if line:
            out.append({"type": "text", "text": line})
    return out


def to_adf(text) -> dict:
    """Plain text -> ADF: blocks separated by a blank line; a block of "- " lines is a bullet list; a block fenced
    with ``` is a code block; anything else is a paragraph (its single line breaks kept as hard breaks)."""
    text = "" if text is None else str(text).replace("\r\n", "\n").strip("\n")
    content: list[dict] = []
    lines = text.split("\n") if text else []
    i = 0
    block: list[str] = []

    def flush():
        if not block:
            return
        if all(line.startswith("- ") for line in block):
            content.append({"type": "bulletList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": _text(line[2:])}]}
                for line in block]})
        else:
            content.append({"type": "paragraph", "content": _text("\n".join(block))})
        block.clear()

    while i < len(lines):
        line = lines[i]
        if line.strip() == "```":
            flush()
            j = i + 1
            code: list[str] = []
            while j < len(lines) and lines[j].strip() != "```":
                code.append(lines[j])
                j += 1
            node = {"type": "codeBlock"}
            if code:
                node["content"] = [{"type": "text", "text": "\n".join(code)}]
            content.append(node)
            i = j + 1
            continue
        if line.strip() == "":
            flush()
        else:
            block.append(line)
        i += 1
    flush()
    return {"type": "doc", "version": 1, "content": content}


def _inline(nodes, simplified: list) -> str:
    parts = []
    for n in nodes or []:
        t = n.get("type")
        if t not in WRITTEN:
            simplified.append(t)
        if t == "text":
            parts.append(n.get("text", ""))
        elif t == "hardBreak":
            parts.append("\n")
        elif t == "mention":
            parts.append(n.get("attrs", {}).get("text", ""))
        elif t in ("emoji", "inlineCard"):
            attrs = n.get("attrs", {})
            parts.append(attrs.get("text") or attrs.get("shortName") or attrs.get("url") or "")
        elif n.get("content"):
            parts.append(_inline(n["content"], simplified))
    return "".join(parts)


def _block(node, simplified: list) -> str:
    t = node.get("type")
    if t not in WRITTEN:
        simplified.append(t)
    if t == "paragraph":
        return _inline(node.get("content"), simplified)
    if t == "bulletList":
        items = []
        for item in node.get("content") or []:
            items.append("- " + " ".join(_block(c, simplified) for c in item.get("content") or []))
        return "\n".join(items)
    if t == "codeBlock":
        return "```\n" + _inline(node.get("content"), simplified) + "\n```"
    if t == "text":
        return node.get("text", "")
    # anything else (heading, table, panel, orderedList...): its text, one block per child
    children = node.get("content") or []
    if children and all(c.get("type") in ("text", "hardBreak", "mention", "emoji", "inlineCard") for c in children):
        return _inline(children, simplified)
    return "\n".join(x for x in (_block(c, simplified) for c in children) if x != "")


def from_adf(doc) -> tuple[str, bool]:
    """ADF -> (plain text, simplified). A plain string is returned as it is (Jira API v2 texts, or a text field)."""
    if doc is None:
        return "", False
    if isinstance(doc, str):
        return doc, False
    if not isinstance(doc, dict):
        return str(doc), False
    simplified: list = []
    blocks = [_block(n, simplified) for n in doc.get("content") or []]
    return "\n\n".join(blocks), bool(simplified)


def is_adf(value) -> bool:
    return isinstance(value, dict) and value.get("type") == "doc"

"""
Plain text <-> the HTML of Azure DevOps rich text fields (technique-azure-devops.md section 1.1): `System.Description`,
`Microsoft.VSTS.TCM.ReproSteps`, `Microsoft.VSTS.TCM.SystemInfo` and each part of a test step.

The adapter writes paragraphs (a blank line between them), lines (`<br>`) and bullet lists (lines starting with "- "),
the text always escaped; it reads HTML back to that plain text. The round trip is exact for what the adapter writes;
HTML written by people (tables, images, headings, links) is read as plain text and reported as simplified.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

SIMPLE_TAGS = {"p", "div", "br", "ul", "li", "span", "b", "strong", "i", "em", "u"}


def to_html(text) -> str:
    """Plain text -> HTML: paragraphs <p>, lines <br>, "- " lines as <ul><li>."""
    text = "" if text is None else str(text).replace("\r\n", "\n")
    if not text.strip():
        return ""
    out = []
    for block in re.split(r"\n{2,}", text.strip("\n")):
        lines = block.split("\n")
        if all(line.startswith("- ") for line in lines):
            out.append("<ul>" + "".join(f"<li>{html.escape(line[2:], quote=False)}</li>" for line in lines) + "</ul>")
        else:
            out.append("<p>" + "<br>".join(html.escape(line, quote=False) for line in lines) + "</p>")
    return "".join(out)


class _Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[list[str]] = [[""]]    # paragraphs -> lines
        self.simplified = False
        self.in_li = False

    def _new_block(self):
        if any(line for line in self.blocks[-1]):
            self.blocks.append([""])

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in SIMPLE_TAGS:
            self.simplified = True
        if tag in ("p", "div", "ul", "table", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._new_block()
        elif tag == "br":
            self.blocks[-1].append("")
        elif tag == "li":
            if self.blocks[-1][-1]:
                self.blocks[-1].append("")
            self.blocks[-1][-1] = "- "
            self.in_li = True
        elif tag == "tr" and self.blocks[-1][-1]:
            self.blocks[-1].append("")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("p", "div", "ul", "table", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._new_block()
        elif tag == "li":
            self.in_li = False
        elif tag in ("td", "th"):
            self.blocks[-1][-1] += " "

    def handle_data(self, data):
        self.blocks[-1][-1] += re.sub(r"[ \t\r\n]+", " ", data) if "\n" in data else data


def from_html(value) -> tuple[str, bool]:
    """HTML -> (plain text, simplified: HTML the adapter does not write was found)."""
    if value in (None, ""):
        return "", False
    text = str(value)
    if "<" not in text and "&" not in text:
        return text, False
    r = _Reader()
    r.feed(text)
    r.close()
    blocks = []
    for lines in r.blocks:
        while lines and not lines[-1].strip() and len(lines) > 1:
            lines.pop()
        body = "\n".join(line.rstrip() if line.strip() else "" for line in lines).strip("\n")
        if body.strip():
            blocks.append(body)
    return "\n\n".join(blocks), r.simplified


def plain(value) -> str:
    return from_html(value)[0]

"""Accessibility module (docs/qa-audit.md §11; README.md next to this file)."""

from __future__ import annotations

import re

from ...core.findings import Evidence, Finding
from ...core.module import AuditModule
from . import axe, checks

# axe impact -> severity (docs/qa-audit.md §9: automated heuristics never assign "critical")
SEVERITY = {"critical": "high", "serious": "medium", "moderate": "low", "minor": "low"}
WCAG_TAG = re.compile(r"^wcag(\d)(\d)(\d+)$")
WCAG_LEVEL_TAG = re.compile(r"^wcag(\d)(\d?)(a{1,3})$")  # wcag2a -> WCAG 2.0 A, wcag21aa -> WCAG 2.1 AA
WCAG_UNDERSTANDING = "https://www.w3.org/WAI/WCAG21/quickref/"

MANUAL = [
    {"id": "screen-reader", "title": "Screen reader walk-through of the key flows",
     "how": "NVDA or JAWS with Firefox/Chrome, VoiceOver with Safari: headings, landmarks, forms and messages "
            "are announced with the right name, role and state (WCAG 1.3.1, 4.1.2)."},
    {"id": "alt-quality", "title": "Alternative texts describe the purpose of each image",
     "how": "An alt attribute can exist and still be useless ('image', a file name) (WCAG 1.1.1)."},
    {"id": "media", "title": "Captions, transcripts and audio description for media",
     "how": "Video with speech has captions; audio has a transcript (WCAG 1.2.x)."},
    {"id": "resize-text", "title": "Text resized to 200 % keeps content and functions",
     "how": "Browser zoom to 200 %: nothing is cut off or overlaps (WCAG 1.4.4)."},
    {"id": "colour-only", "title": "Information is not conveyed by colour or sound alone",
     "how": "Errors, required fields, charts and links have a non-colour cue (WCAG 1.4.1)."},
    {"id": "errors", "title": "Form errors are identified, described and suggest a fix",
     "how": "Submit forms with invalid data in a test environment (WCAG 3.3.1, 3.3.3)."},
    {"id": "consistency", "title": "Navigation and components are consistent across pages",
     "how": "Same order of menus, same names for the same functions (WCAG 3.2.3, 3.2.4)."},
    {"id": "timing-motion", "title": "Time limits can be extended; motion can be paused",
     "how": "Session timeouts warn; carousels and animations can be stopped (WCAG 2.2.1, 2.2.2)."},
    {"id": "focus-order", "title": "Focus order follows the meaning of the page",
     "how": "The keyboard walk in the report lists the order; a person judges whether it makes sense (WCAG 2.4.3)."},
]


def wcag_refs(tags: list[str]) -> list[str]:
    out = []
    for t in tags or []:
        m = WCAG_TAG.match(t)
        if m:
            out.append(f"WCAG {m.group(1)}.{m.group(2)}.{m.group(3)}")
    return out


def wcag_levels(tags: list[str]) -> list[str]:
    """The WCAG version and level tags of a rule, readable: wcag2aa -> "WCAG 2.0 AA", wcag22aa -> "WCAG 2.2 AA".
    Empty for a rule outside WCAG (e.g. best-practice)."""
    return [f"WCAG {m.group(1)}.{m.group(2) or '0'} {m.group(3).upper()}"
            for t in tags or [] if (m := WCAG_LEVEL_TAG.match(t))]


def selector_of(node: dict) -> str:
    t = node.get("target") or []
    # a list inside the list means shadow DOM: host >>> inner
    return " >>> ".join(" ".join(x) if isinstance(x, list) else str(x) for x in t)


class Accessibility(AuditModule):
    name = "accessibility"
    title = "Accessibility"
    description = "WCAG 2.1 A/AA automated checks (axe-core), keyboard walk and reflow, plus a manual checklist."
    id_prefix = "ACC"
    order = 90         # presses Tab and resizes the viewport: runs last
    manual_checks = MANUAL
    disclaimer = ("An automated audit finds only part of the WCAG issues and is not a conformance certification; "
                  "see the manual checks.")

    def on_page(self, page, info, ctx) -> None:
        cfg = ctx.module_config(self.name)
        url = info.final_url or info.url
        self._axe(page, url, cfg, ctx)
        kb = cfg.get("keyboard") or {}
        if kb.get("enabled", True):
            self._keyboard(page, url, int(kb.get("max_stops", 40)), ctx)
        rf = cfg.get("reflow") or {}
        if rf.get("enabled", True):
            self._reflow(page, url, int(rf.get("width", 320)), ctx)
        if info.iframes:
            ctx.add_finding(Finding(
                module=self.name, check="frames-not-scanned", title="Content inside frames was not scanned",
                status="needs-human", severity="info", confidence="high", url=url,
                description=f"The page has {info.iframes} frame(s); automated checks ran on the page itself only.",
                recommendation="Audit the framed content separately, or check it manually."))

    # --- axe ---------------------------------------------------------------------------------------------------
    def _axe(self, page, url: str, cfg: dict, ctx) -> None:
        max_nodes = int(cfg.get("max_nodes_per_rule", 5))
        max_shots = int(ctx.cfg["evidence"].get("max_element_screenshots", 5))
        try:
            r = axe.run(page, list(cfg.get("tags") or []), max_nodes)
        except axe.AxeUnavailable as e:
            ctx.add_finding(Finding(
                module=self.name, check="axe-not-run", title="Automated WCAG rules could not run on this page",
                status="needs-human", severity="info", confidence="high", url=url, description=str(e),
                recommendation="Usually a strict Content-Security-Policy; audit this page with another engine "
                               "(--browser chromium) or check it manually."))
            return
        ctx.passed(self.name, r["passes"])
        note = ("this browser's crypto.getRandomValues failed; axe-core ran with a Math.random substitute for its "
                "internal ids (results are unaffected)")
        if r.get("cryptoSubstituted") and note not in ctx.notes:
            ctx.notes.append(note)
        for rule in r["violations"]:
            ctx.add_finding(self._rule_finding(page, url, rule, "problem", max_shots, ctx))
        for rule in r["incomplete"]:
            ctx.add_finding(self._rule_finding(page, url, rule, "needs-human", 0, ctx))

    def _rule_finding(self, page, url, rule, status, max_shots, ctx) -> Finding:
        nodes = rule["nodes"]
        first = nodes[0] if nodes else {}
        evidence = [Evidence(kind="html", value=n["html"][:500], label=selector_of(n)) for n in nodes if n.get("html")]
        shots = 0
        for n in nodes:
            if shots >= max_shots:
                break
            sel = selector_of(n)
            if not sel or ">>>" in sel:
                continue
            try:
                loc = page.locator(sel).first
                if not loc.is_visible():
                    continue
            except Exception as e:  # noqa: BLE001 - an axe selector Playwright cannot parse
                ctx.log.info("no element screenshot for %s: %s", sel, e)
                continue
            ev = ctx.screenshot(loc, f"{rule['id']} {sel}")
            if ev:
                evidence.append(ev)
                shots += 1
        help_url = (rule.get("helpUrl") or "").split("?")[0]
        refs = wcag_refs(rule.get("tags")) + wcag_levels(rule.get("tags")) + ([help_url] if help_url else [])
        more = rule["total"] - len(nodes)
        actual = f"{rule['total']} element(s) fail this rule" + (f" ({more} not listed)" if more > 0 else "")
        if first.get("failureSummary"):
            lines = [x.strip() for x in first["failureSummary"].splitlines() if x.strip()]
            actual += ". First: " + (lines[0] + " " + "; ".join(lines[1:]) if len(lines) > 1 else lines[0])
        return Finding(
            module=self.name, check=f"axe/{rule['id']}", title=rule["help"], status=status,
            severity=SEVERITY.get(rule.get("impact") or "", "low"),
            confidence="high" if status == "problem" else "low", url=url,
            description=rule.get("description", ""),
            expected="No element fails the rule." if status == "problem" else "A person confirms the rule is met.",
            actual=actual,
            recommendation=f"{rule['help']}. Details: {help_url}" if help_url else rule["help"],
            element={"selector": selector_of(first), "snippet": (first.get("html") or "")[:500]},
            steps=[f"Open {url}", f"Inspect the element {selector_of(first)}",
                   f"Check it against the rule '{rule['id']}' ({', '.join(wcag_refs(rule.get('tags'))) or 'axe'})"],
            evidence=evidence, references=refs)

    # --- keyboard ----------------------------------------------------------------------------------------------
    def _keyboard(self, page, url: str, max_stops: int, ctx) -> None:
        walk = checks.keyboard_walk(page, max_stops)
        order = [s["selector"] for s in walk["stops"]]
        if walk["trap"]:
            t = walk["trap"]
            ev = [Evidence(kind="text", value=" -> ".join(order[-5:] + [t["selector"]]), label="focus sequence"),
                  Evidence(kind="html", value=t["html"], label=t["selector"])]
            shot = ctx.screenshot(page.locator(t["selector"]).first, f"keyboard trap {t['selector']}")
            if shot:
                ev.append(shot)
            ctx.add_finding(Finding(
                module=self.name, check="keyboard-trap", title="Keyboard focus gets stuck", status="warning",
                severity="high", confidence="medium", url=url, element={"selector": t["selector"],
                                                                       "snippet": t["html"]},
                description="Pressing Tab three times left the focus on the same element.",
                expected="Tab moves the focus to the next element; focus can always leave a component.",
                actual=f"Focus stayed on {t['selector']}.",
                recommendation="Let Tab and Shift+Tab move focus out of the component (WCAG 2.1.2).",
                steps=[f"Open {url}", "Press Tab repeatedly", f"Focus stops moving at {t['selector']}"],
                evidence=ev, references=["WCAG 2.1.2"]))
        else:
            ctx.passed(self.name)
        if walk["no_visible_change"]:
            items = walk["no_visible_change"]
            ctx.add_finding(Finding(
                module=self.name, check="focus-visible", title="Focus may not be visible on some elements",
                status="needs-human", severity="medium", confidence="low", url=url,
                element={"selector": items[0]["selector"], "snippet": items[0]["html"]},
                description=(f"{len(items)} of {len(walk['stops'])} focus stops showed no change in outline, shadow, "
                             "border, background, colour or underline when focused. A focus style drawn another way "
                             "(pseudo-elements, a parent) is not detected, so a person must look."),
                expected="Every focused element has a visible focus indicator.",
                actual="No computed style change on: " + ", ".join(i["selector"] for i in items[:5]),
                recommendation="Tab through the page and look at each listed element (WCAG 2.4.7).",
                steps=[f"Open {url}", "Press Tab until each listed element is focused", "Look for a focus indicator"],
                evidence=[Evidence(kind="html", value=i["html"], label=i["selector"]) for i in items[:5]],
                references=["WCAG 2.4.7"]))
        elif walk["stops"]:
            ctx.passed(self.name)

    # --- reflow ------------------------------------------------------------------------------------------------
    def _reflow(self, page, url: str, width: int, ctx) -> None:
        r = checks.reflow(page, width, capture=lambda: ctx.screenshot(page, f"reflow {width}px {url}"))
        if r is None:
            ctx.passed(self.name)
            return
        ev = [Evidence(kind="metric", value=f"scrollWidth {r['scroll_width']}px > clientWidth {r['client_width']}px "
                                            f"at {width} CSS px", label="horizontal overflow")]
        if r.get("shot"):
            ev.append(r["shot"])
        ctx.add_finding(Finding(
            module=self.name, check="reflow", title=f"Content scrolls horizontally at {width} CSS px",
            status="warning", severity="medium", confidence="medium", url=url,
            description="At this width, content should reflow into one column. Data tables, maps and diagrams may "
                        "legitimately scroll; confirm which element overflows.",
            expected=f"No horizontal scrolling at {width} CSS px wide (equivalent to 400 % zoom at 1280 px).",
            actual=f"The page is {r['scroll_width']} px wide in a {r['client_width']} px viewport.",
            recommendation="Use responsive layout (relative widths, wrapping, media queries) (WCAG 1.4.10).",
            steps=[f"Open {url}", f"Set the window to {width} CSS px wide (or zoom to 400 %)",
                   "Look for a horizontal scrollbar"],
            evidence=ev, references=["WCAG 1.4.10"]))


MODULE = Accessibility()
